<script setup lang="ts">
/**
 * 網域詳細資料：註冊資訊（RDAP 與手填）、NS 與 DNS 託管、綁定的服務，以及相關的 DNS 區域與憑證。
 * 編輯用跟清單頁同一個對話框。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { storeToRefs } from "pinia";
import {
  NButton, NCard, NDescriptions, NDescriptionsItem, NEmpty, NIcon, NPopconfirm, NSpace, NSpin, NTag, useMessage,
} from "naive-ui";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { DeleteIcon, DnsIcon, DomainIcon, EditIcon, LockIcon, SyncIcon } from "@/icons";
import { apiErrMsg } from "@/api/client";
import { Domains, type DomainDetail } from "@/api/domains";
import { useAuthStore } from "@/stores/auth";
import DomainEditModal from "@/components/DomainEditModal.vue";
import { fmtDate, fmtDateTime } from "@/utils/datetime";
import { expiryTag, rdapErrorText } from "@/utils/domains";

const route = useRoute();
const router = useRouter();
const { t, te } = useI18n();
const msg = useMessage();
const { me } = storeToRefs(useAuthStore());
const isAdmin = computed(() => !!me.value?.is_admin);

const data = ref<DomainDetail | null>(null);
const loading = ref(false);
const refreshing = ref(false);
const editShow = ref(false);

async function load() {
  loading.value = true;
  try { data.value = await Domains.get(String(route.params.id)); }
  catch (e) { msg.error(apiErrMsg(e)); data.value = null; }
  finally { loading.value = false; }
}
async function refresh() {
  refreshing.value = true;
  try {
    const n = await Domains.refresh(String(route.params.id));
    if (n.rdap_error) msg.warning(rdapErrorText(n.rdap_error, t, te));
    await load();
  } catch (e) { msg.error(apiErrMsg(e)); } finally { refreshing.value = false; }
}
async function remove() {
  try { await Domains.remove(String(route.params.id)); router.push({ name: "adv-domains" }); }
  catch (e) { msg.error(apiErrMsg(e)); }
}
function back() {
  if (window.history.length > 1) router.back();
  else router.push({ name: "adv-domains" });
}
const tag = computed(() => (data.value ? expiryTag(data.value, t) : null));

watch(() => route.params.id, (id) => { if (id) void load(); });
onMounted(load);
</script>

<template>
  <n-spin :show="loading">
    <n-space vertical :size="16">
      <n-card v-if="data">
        <template #header>
          <n-space align="center" :wrap-item="false">
            <n-icon :size="22"><DomainIcon /></n-icon>
            <span data-testid="domain-title">{{ data.name }}</span>
            <n-tag v-if="tag" size="small" :type="tag.type" :bordered="false">{{ tag.text }}</n-tag>
          </n-space>
        </template>
        <n-space align="center" justify="end" style="margin-bottom: 10px">
          <n-button size="small" @click="back">
            <template #icon><n-icon><ArrowLeftIcon /></n-icon></template>{{ t("common.back") }}
          </n-button>
          <template v-if="isAdmin">
            <n-button size="small" :loading="refreshing" :disabled="refreshing" data-testid="domain-refresh" @click="refresh">
              <template #icon><n-icon><SyncIcon /></n-icon></template>{{ t("domains.refresh") }}
            </n-button>
            <n-button size="small" type="success" data-testid="domain-edit" @click="editShow = true">
              <template #icon><n-icon><EditIcon /></n-icon></template>{{ t("common.edit") }}
            </n-button>
            <n-popconfirm @positive-click="remove">
              <template #trigger>
                <n-button size="small" type="error" ghost>
                  <template #icon><n-icon><DeleteIcon /></n-icon></template>{{ t("common.delete") }}
                </n-button>
              </template>
              {{ t("domains.delete_confirm", { name: data.name }) }}
            </n-popconfirm>
          </template>
        </n-space>
        <n-descriptions label-placement="left" :column="2" size="small" bordered>
          <n-descriptions-item :label="t('domains.registrar')">
            {{ data.registrar_effective || "—" }}
            <n-tag v-if="!data.registrar && data.registrar_effective" size="tiny" :bordered="false" style="margin-left:6px">RDAP</n-tag>
          </n-descriptions-item>
          <n-descriptions-item :label="t('domains.registrar_account')">{{ data.registrar_account || "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('domains.registrar_url')" :span="2">
            <a v-if="data.registrar_url" :href="data.registrar_url" target="_blank" rel="noopener noreferrer">{{ data.registrar_url }}</a>
            <span v-else>—</span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('domains.expires')">
            {{ data.expires ? fmtDate(data.expires) : "—" }}
            <n-tag v-if="data.expires_source === 'manual'" size="tiny" :bordered="false" style="margin-left:6px">{{ t("domains.manual") }}</n-tag>
          </n-descriptions-item>
          <n-descriptions-item :label="t('domains.registered_at')">{{ data.registered_at ? fmtDate(data.registered_at) : "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('domains.auto_renew')">
            {{ data.auto_renew == null ? "—" : data.auto_renew ? t("common.yes") : t("common.no") }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('domains.dns_provider')">
            {{ data.dns_provider_effective || "—" }}
            <n-tag v-if="data.dns_provider_inferred" size="tiny" :bordered="false" style="margin-left:6px">{{ t("domains.inferred") }}</n-tag>
          </n-descriptions-item>
          <n-descriptions-item :label="t('domains.nameservers')" :span="2">
            <span class="mono">{{ data.nameservers.join(", ") || "—" }}</span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('domains.status')" :span="2">
            <n-space v-if="data.status.length" :size="4">
              <n-tag v-for="s in data.status" :key="s" size="tiny" :bordered="false">{{ s }}</n-tag>
            </n-space>
            <span v-else>—</span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('domains.services')" :span="2"><span class="pre">{{ data.services || "—" }}</span></n-descriptions-item>
          <n-descriptions-item :label="t('sections.description')" :span="2"><span class="pre">{{ data.description || "—" }}</span></n-descriptions-item>
          <n-descriptions-item :label="t('domains.rdap_checked')" :span="2">
            {{ data.rdap_checked_at ? fmtDateTime(data.rdap_checked_at) : "—" }}
            <n-tag v-if="data.rdap_error" size="small" :bordered="false" type="warning" style="margin-left:6px">
              {{ rdapErrorText(data.rdap_error, t, te) }}
            </n-tag>
          </n-descriptions-item>
        </n-descriptions>
      </n-card>

      <n-card v-if="data" data-testid="domain-zones">
        <template #header>
          <span style="display:inline-flex;align-items:center;gap:8px"><n-icon :size="18"><DnsIcon /></n-icon>{{ t("domains.zones") }}</span>
        </template>
        <n-empty v-if="!data.zones.length" :description="t('domains.zones_empty')" />
        <table v-else class="plain">
          <thead><tr><th>{{ t("domains.zone") }}</th><th>{{ t("domains.zone_server") }}</th><th>{{ t("cols.type") }}</th></tr></thead>
          <tbody><tr v-for="z in data.zones" :key="z.id"><td class="mono">{{ z.name }}</td><td>{{ z.server }}</td><td>{{ z.type }}</td></tr></tbody>
        </table>
      </n-card>

      <n-card v-if="data && isAdmin" data-testid="domain-certs">
        <template #header>
          <span style="display:inline-flex;align-items:center;gap:8px"><n-icon :size="18"><LockIcon /></n-icon>{{ t("domains.certs") }}</span>
        </template>
        <n-empty v-if="!data.certificates.length" :description="t('domains.certs_empty')" />
        <table v-else class="plain">
          <thead><tr><th>{{ t("cols.name") }}</th><th>{{ t("domains.cert_names") }}</th></tr></thead>
          <tbody><tr v-for="c in data.certificates" :key="c.id"><td>{{ c.name }}</td><td class="mono">{{ c.domains.join(", ") }}</td></tr></tbody>
        </table>
      </n-card>

      <n-space v-if="!data && !loading" justify="center" style="padding: 40px; opacity: .6">{{ t("common.no_data") }}</n-space>
    </n-space>
    <DomainEditModal v-model:show="editShow" :domain="data" @saved="load" />
  </n-spin>
</template>

<style scoped>
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.pre { white-space: pre-wrap; }
.plain { width: 100%; border-collapse: collapse; font-size: 13.5px; }
.plain th, .plain td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--n-border-color, rgba(128,128,128,.2)); }
.plain th { font-weight: 600; opacity: .8; }
</style>
