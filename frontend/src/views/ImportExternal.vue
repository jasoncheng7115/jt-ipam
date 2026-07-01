<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NIcon, NButton, NForm, NFormItem, NInput, NSelect, NCode,
  NAlert, NTabs, NTabPane,
  useMessage,
} from "naive-ui";
import { ImportIcon, EyeIcon, SaveIcon, InfoIcon } from "@/icons";
import { ripePreview, ripeCommit } from "@/api/phase3";
import { listSections } from "@/api/sections";

const { t } = useI18n();
const msg = useMessage();
const tab = ref<"ripe" | "twnic">("ripe");

const handle = ref("");
const cidr = ref("");
const sectionId = ref<string | null>(null);
const sectionOpts = ref<{ label: string; value: string }[]>([]);
const previewing = ref(false);
const committing = ref(false);
const previewResult = ref<string | null>(null);
const commitResult = ref<string | null>(null);

async function loadSections() {
  try {
    const res = await listSections(1, 200);
    sectionOpts.value = res.items.map((s) => ({ label: s.name, value: s.id }));
  } catch {}
}

function validateInput(): boolean {
  if (!handle.value && !cidr.value) {
    msg.error(t("import.error_need_one"));
    return false;
  }
  if (handle.value && cidr.value) {
    msg.error(t("import.error_pick_one"));
    return false;
  }
  return true;
}

async function preview() {
  if (!validateInput()) return;
  previewing.value = true;
  try {
    const r = await ripePreview({
      handle: handle.value || undefined,
      cidr: cidr.value || undefined,
    });
    previewResult.value = JSON.stringify(r, null, 2);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
  finally { previewing.value = false; }
}
async function commit() {
  if (!validateInput()) return;
  if (!sectionId.value) { msg.error(t("import.section_required")); return; }
  committing.value = true;
  try {
    const r = await ripeCommit({
      handle: handle.value || undefined,
      cidr: cidr.value || undefined,
      section_id: sectionId.value,
    });
    commitResult.value = JSON.stringify(r, null, 2);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
  finally { committing.value = false; }
}

onMounted(() => { void loadSections(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><ImportIcon /></n-icon>
        <span>{{ t("import.title") }}</span>
      </n-space>
    </template>
    <n-tabs v-model:value="tab" type="line">
      <n-tab-pane name="ripe">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><ImportIcon /></n-icon>RIPE</span>
        </template>
        <n-alert type="info" style="margin-bottom: 16px">
          <template #icon><n-icon><InfoIcon /></n-icon></template>
          {{ t("import.ripe_help") }}
        </n-alert>
        <n-form class="import-form">
          <n-alert type="info" style="margin-bottom: 16px">
            <template #icon><n-icon><InfoIcon /></n-icon></template>
            {{ t("import.either_or_hint") }}
          </n-alert>
          <n-form-item :label="t('import.handle')">
            <n-input v-model:value="handle" placeholder="HOLDER-NIC"
                     :disabled="!!cidr" />
            <template #feedback>{{ t("import.handle_hint") }}</template>
          </n-form-item>
          <n-form-item label="CIDR">
            <n-input v-model:value="cidr" placeholder="203.0.113.0/24"
                    :disabled="!!handle" />
            <template #feedback>{{ t("import.cidr_hint") }}</template>
          </n-form-item>
          <n-form-item :label="t('import.target_section')">
            <n-select v-model:value="sectionId" :options="sectionOpts" filterable
                      :placeholder="t('import.target_section_placeholder')" />
          </n-form-item>
        </n-form>
        <n-space style="margin-top: 12px">
          <n-button :loading="previewing" @click="preview">
            <template #icon><n-icon><EyeIcon /></n-icon></template>
            {{ t("import.preview") }}
          </n-button>
          <n-button type="primary" :loading="committing" @click="commit">
            <template #icon><n-icon><SaveIcon /></n-icon></template>
            {{ t("import.commit") }}
          </n-button>
        </n-space>
        <template v-if="previewResult">
          <h3 style="margin-top: 24px">Preview</h3>
          <n-code :code="previewResult" language="json" />
        </template>
        <template v-if="commitResult">
          <h3 style="margin-top: 16px">Commit</h3>
          <n-code :code="commitResult" language="json" />
        </template>
      </n-tab-pane>
      <n-tab-pane name="twnic">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><ImportIcon /></n-icon>TWNIC</span>
        </template>
        <n-alert type="info">
          <template #icon><n-icon><InfoIcon /></n-icon></template>
          {{ t("import.twnic_help") }}
        </n-alert>
      </n-tab-pane>
    </n-tabs>
  </n-card>
</template>

<style scoped>
/* 讓 Handle / CIDR / 目標 section 各欄之間有呼吸空間，提示文字不再緊貼下一個標籤 */
.import-form :deep(.n-form-item) { margin-bottom: 14px; }
.import-form :deep(.n-form-item:last-child) { margin-bottom: 0; }
.import-form :deep(.n-form-item-feedback__line) { line-height: 1.5; opacity: .72; }
</style>
