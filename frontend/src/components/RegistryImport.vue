<template>
  <div>
    <n-alert type="info" :bordered="false" style="margin-bottom: 14px">
      <template #icon><n-icon><InfoIcon /></n-icon></template>
      {{ t(`import.${source}_help`) }}
    </n-alert>

    <n-radio-group v-model:value="mode" size="small" style="margin-bottom: 14px">
      <n-radio-button value="lookup">{{ t("import.mode_lookup") }}</n-radio-button>
      <n-radio-button value="paste">{{ t("import.mode_paste") }}</n-radio-button>
    </n-radio-group>

    <n-form class="import-form">
      <template v-if="mode === 'lookup'">
        <n-form-item :label="t('import.query')" :show-feedback="false">
          <div style="width:100%">
            <n-input v-model:value="query" placeholder="163.28.0.0  /  1.34.0.0/16"
                     @keyup.enter="preview" />
            <div class="hint">{{ t("import.query_hint") }}</div>
          </div>
        </n-form-item>
      </template>
      <template v-else>
        <n-form-item :label="t('import.whois_text')" :show-feedback="false">
          <div style="width:100%">
            <n-input v-model:value="text" type="textarea" :rows="9"
                     :placeholder="whoisPlaceholder" />
            <div class="hint">{{ t("import.whois_hint") }}</div>
          </div>
        </n-form-item>
      </template>

      <n-form-item :label="t('import.target_section')" :show-feedback="false">
        <div style="width:100%">
          <n-select v-model:value="sectionId" :options="sectionOpts" filterable clearable
                    :placeholder="t('import.target_section_placeholder')" />
          <div class="hint">{{ t("import.section_hint") }}</div>
        </div>
      </n-form-item>
    </n-form>

    <n-space style="margin-top: 14px">
      <n-button :loading="previewing" :disabled="!hasInput" @click="preview">
        <template #icon><n-icon><EyeIcon /></n-icon></template>
        {{ t("import.preview") }}
      </n-button>
      <n-button type="primary" :loading="committing" :disabled="!hasInput || !sectionId" @click="commit">
        <template #icon><n-icon><SaveIcon /></n-icon></template>
        {{ t("import.commit") }}
      </n-button>
    </n-space>

    <!-- 查到的登記資料（只有線上查詢有） -->
    <template v-if="network">
      <n-divider style="margin: 18px 0 12px" />
      <div class="sec-title">{{ t("import.registry_info") }}</div>
      <n-descriptions bordered size="small" :column="2" label-placement="left">
        <n-descriptions-item :label="t('import.netname')">{{ network.name || "—" }}</n-descriptions-item>
        <n-descriptions-item :label="t('import.country')">{{ network.country || "—" }}</n-descriptions-item>
        <n-descriptions-item :label="t('import.range')">{{ network.handle || "—" }}</n-descriptions-item>
        <n-descriptions-item :label="t('import.alloc_type')">{{ network.type || "—" }}</n-descriptions-item>
        <n-descriptions-item v-if="network.entities.length" :label="t('import.contacts')" :span="2">
          <span v-for="(e, i) in network.entities" :key="i" style="margin-right: 12px">
            {{ e.name || e.handle }}<span v-if="e.roles.length" class="hint-inline">（{{ e.roles.join(", ") }}）</span>
          </span>
        </n-descriptions-item>
        <n-descriptions-item v-if="network.remarks.length" :label="t('import.remarks')" :span="2">
          <div v-for="(r, i) in network.remarks" :key="i">{{ r }}</div>
        </n-descriptions-item>
        <n-descriptions-item v-if="network.source_url" :label="t('import.source')" :span="2">
          <code class="src">{{ network.source_url }}</code>
        </n-descriptions-item>
      </n-descriptions>
    </template>

    <!-- 即將建立的網段 -->
    <template v-if="plans.length">
      <n-divider style="margin: 18px 0 12px" />
      <div class="sec-title">{{ t("import.will_create", { n: plans.length }) }}</div>
      <n-data-table size="small" :columns="planCols" :data="plans" :bordered="true" />
    </template>
    <n-empty v-else-if="previewed" :description="t('import.no_result')" size="small"
             style="margin-top: 18px" />

    <!-- 匯入結果 -->
    <n-alert v-if="result" :type="result.errored.length ? 'warning' : 'success'"
             style="margin-top: 16px">
      {{ t("import.result", { ins: result.inserted, skip: result.skipped, total: result.total_plans }) }}
      <div v-if="result.skipped" class="hint" style="margin-top:4px">{{ t("import.skip_hint") }}</div>
      <div v-for="(e, i) in result.errored" :key="i" class="hint" style="margin-top:4px">
        {{ e.cidr }}: {{ e.error }}
      </div>
    </n-alert>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NDataTable, NDescriptions, NDescriptionsItem, NDivider, NEmpty,
  NForm, NFormItem, NIcon, NInput, NRadioButton, NRadioGroup, NSelect, NSpace, useMessage,
} from "naive-ui";
import { EyeIcon, InfoIcon, SaveIcon } from "@/icons";
import { apiErrMsg } from "@/api/client";
import {
  rdapCommit, rdapPreview, whoisCommit, whoisPreview,
  type ImportPlan, type RdapNetworkInfo, type ImportResult, type RdapSource,
} from "@/api/phase3";
import { listSections } from "@/api/sections";
import { autoSort } from "@/composables/useTableSort";

const props = defineProps<{ source: RdapSource }>();
const { t } = useI18n();
const msg = useMessage();

// 線上查詢（RDAP）只查得到 IP/CIDR；handle 之類的查詢 RDAP 給不出來，走貼上 whois 文字。
const mode = ref<"lookup" | "paste">("lookup");
const query = ref("");
const text = ref("");
const sectionId = ref<string | null>(null);
const sectionOpts = ref<{ label: string; value: string }[]>([]);
const previewing = ref(false);
const committing = ref(false);
const previewed = ref(false);
const plans = ref<ImportPlan[]>([]);
const network = ref<RdapNetworkInfo | null>(null);
const result = ref<ImportResult | null>(null);

const hasInput = computed(() =>
  mode.value === "lookup" ? query.value.trim().length > 0 : text.value.trim().length > 0);

const whoisPlaceholder =
  "inetnum:  163.28.0.0 - 163.28.255.255\nnetname:  T-EDU.TW-NET\ncountry:  TW\ndescr:    …";

const planCols = computed(() => autoSort<ImportPlan>([
  { title: "CIDR", key: "cidr", width: 190 },
  { title: t("import.netname"), key: "netname" },
  { title: t("import.description"), key: "description" },
  { title: t("import.country"), key: "country", width: 90 },
]));

async function loadSections() {
  try {
    const res = await listSections(1, 200);
    sectionOpts.value = res.items.map((s) => ({ label: s.name, value: s.id }));
  } catch { /* 區段載不到不擋查詢，只是不能匯入 */ }
}

function reset() { plans.value = []; network.value = null; result.value = null; }

async function preview() {
  previewing.value = true;
  reset();
  try {
    const r = mode.value === "lookup"
      ? await rdapPreview({ source: props.source, query: query.value })
      : await whoisPreview({ text: text.value });
    plans.value = r.plans;
    network.value = r.network ?? null;
    previewed.value = true;
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { previewing.value = false; }
}

async function commit() {
  if (!sectionId.value) { msg.error(t("import.section_required")); return; }
  committing.value = true;
  try {
    result.value = mode.value === "lookup"
      ? await rdapCommit({ source: props.source, query: query.value, section_id: sectionId.value })
      : await whoisCommit({ text: text.value, section_id: sectionId.value });
    msg.success(t("import.result", {
      ins: result.value.inserted, skip: result.value.skipped, total: result.value.total_plans,
    }));
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { committing.value = false; }
}

onMounted(() => { void loadSections(); });
</script>

<style scoped>
.import-form :deep(.n-form-item) { margin-bottom: 14px; }
.import-form :deep(.n-form-item:last-child) { margin-bottom: 0; }
.hint { margin-top: 4px; font-size: 12px; line-height: 1.5; color: var(--n-text-color-disabled); }
.hint-inline { font-size: 12px; color: var(--n-text-color-disabled); }
.sec-title { font-weight: 600; margin-bottom: 8px; }
.src { font-size: 12px; word-break: break-all; }
</style>
