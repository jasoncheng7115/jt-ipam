<script setup lang="ts">
/**
 * 裝置頁「租用資訊」：向供應商租用、合約結束或除役時要歸還的設備（客戶許願清單 2026-10-28）。
 * 選了電路就帶出它的供應商；電路詳細資料頁會列出掛在那條電路的租用設備。
 * 設定只限管理員（內容是供應商與電路，屬全域資料）。
 */
import JtDatePicker from "@/components/JtDatePicker.vue";
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import {
  NButton, NCard, NDescriptions, NDescriptionsItem, NForm, NFormItem, NIcon, NInput, NModal,
  NPopconfirm, NSelect, NSpace, NSwitch, useMessage,
} from "naive-ui";
import { CancelIcon, DeleteIcon, EditIcon, LeaseIcon, PlusIcon, SaveIcon } from "@/icons";
import { apiErrMsg } from "@/api/client";
import { Advanced, type Circuit, type DeviceLease, type Provider } from "@/api/phase3";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { fmtDate } from "@/utils/datetime";

const props = defineProps<{ deviceId: string; admin: boolean }>();
const { t } = useI18n();
const msg = useMessage();
const links = useEntityLinks(useRouter());

const lease = ref<DeviceLease | null>(null);
const loaded = ref(false);
const providers = ref<Provider[]>([]);
const circuits = ref<Circuit[]>([]);

async function load() {
  try { lease.value = await Advanced.deviceLease(props.deviceId); }
  catch { lease.value = null; }
  finally { loaded.value = true; }
}

const form = reactive({
  show: false, busy: false, provider_id: null as string | null, circuit_id: null as string | null,
  return_required: true, return_due: null as number | null, note: "",
});
function toTs(d: string | null): number | null {
  if (!d) return null;
  const [y, m, dd] = d.split("-").map(Number);
  return new Date(y, m - 1, dd).getTime();
}
function toDate(ts: number | null): string | null {
  if (ts == null) return null;
  const d = new Date(ts);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
async function openEdit() {
  const l = lease.value;
  Object.assign(form, {
    show: true, busy: false, provider_id: l?.provider?.id ?? null, circuit_id: l?.circuit?.id ?? null,
    return_required: l?.return_required ?? true, return_due: toTs(l?.return_due ?? null), note: l?.note ?? "",
  });
  try {
    [providers.value, circuits.value] = await Promise.all([Advanced.providers(), Advanced.circuits()]);
  } catch (e) { msg.error(apiErrMsg(e)); }
}
const providerOpts = computed(() => providers.value.map((p) => ({ label: p.name, value: p.id })));
const circuitOpts = computed(() => circuits.value
  .filter((c) => !form.provider_id || c.provider_id === form.provider_id)
  .map((c) => ({ label: c.cid, value: c.id })));
// 選了電路：供應商就是電路的供應商（不用再選一次）
watch(() => form.circuit_id, (id) => {
  const c = circuits.value.find((x) => x.id === id);
  if (c) form.provider_id = c.provider_id;
});
async function save() {
  form.busy = true;
  try {
    lease.value = await Advanced.putDeviceLease(props.deviceId, {
      provider_id: form.provider_id, circuit_id: form.circuit_id, return_required: form.return_required,
      return_due: toDate(form.return_due), note: form.note.trim() || null,
    });
    form.show = false;
    msg.success(t("common.saved"));
  } catch (e) { msg.error(apiErrMsg(e)); } finally { form.busy = false; }
}
async function remove() {
  try {
    await Advanced.deleteDeviceLease(props.deviceId);
    lease.value = null;
  } catch (e) { msg.error(apiErrMsg(e)); }
}

watch(() => props.deviceId, () => { void load(); });
onMounted(load);
</script>

<template>
  <!-- 不是租用設備、又不能設定的人看不到這張卡片 -->
  <n-card v-if="loaded && (lease || admin)" size="small" data-testid="device-lease-card">
    <template #header>
      <span style="display:inline-flex;align-items:center;gap:8px">
        <n-icon :size="18"><LeaseIcon /></n-icon>
        {{ t("device_lease.title") }}
      </span>
    </template>
    <n-space v-if="admin" align="center" justify="end" style="margin-bottom: 10px">
      <n-popconfirm v-if="lease" @positive-click="remove">
        <template #trigger>
          <n-button size="small" type="error" ghost>
            <template #icon><n-icon><DeleteIcon /></n-icon></template>
            {{ t("device_lease.remove") }}
          </n-button>
        </template>
        {{ t("device_lease.remove_confirm") }}
      </n-popconfirm>
      <n-button size="small" :type="lease ? 'success' : 'primary'" data-testid="device-lease-edit" @click="openEdit">
        <template #icon><n-icon><component :is="lease ? EditIcon : PlusIcon" /></n-icon></template>
        {{ lease ? t("common.edit") : t("device_lease.set") }}
      </n-button>
    </n-space>
    <div v-if="!lease" class="hint">{{ t("device_lease.none") }}</div>
    <n-descriptions v-else label-placement="left" :column="2" size="small">
      <n-descriptions-item :label="t('device_lease.provider')">{{ lease.provider?.name ?? "—" }}</n-descriptions-item>
      <n-descriptions-item :label="t('device_lease.circuit')">
        <component :is="() => (lease!.circuit ? links.circuit(lease!.circuit.id, lease!.circuit.cid) : '—')" />
      </n-descriptions-item>
      <n-descriptions-item :label="t('device_lease.return_required')">
        {{ lease.return_required ? t("common.yes") : t("common.no") }}
      </n-descriptions-item>
      <n-descriptions-item :label="t('device_lease.return_due')">{{ lease.return_due ? fmtDate(lease.return_due) : "—" }}</n-descriptions-item>
      <n-descriptions-item :label="t('device_lease.note')" :span="2">{{ lease.note || "—" }}</n-descriptions-item>
    </n-descriptions>

    <n-modal v-model:show="form.show" preset="card" style="width: 480px; max-width: 95vw" :title="t('device_lease.title')">
      <div class="hint" style="margin-bottom: 12px">{{ t("device_lease.hint") }}</div>
      <n-form label-placement="top">
        <n-form-item :label="t('device_lease.circuit')">
          <n-select v-model:value="form.circuit_id" clearable filterable :options="circuitOpts"
                    data-testid="device-lease-circuit" />
        </n-form-item>
        <n-form-item :label="t('device_lease.provider')">
          <n-select v-model:value="form.provider_id" clearable filterable :options="providerOpts"
                    data-testid="device-lease-provider" />
        </n-form-item>
        <n-form-item :label="t('device_lease.return_required')">
          <n-switch v-model:value="form.return_required" />
        </n-form-item>
        <n-form-item :label="t('device_lease.return_due')">
          <jt-date-picker v-model:value="form.return_due" type="date" clearable style="width: 100%" />
        </n-form-item>
        <n-form-item :label="t('device_lease.note')">
          <n-input v-model:value="form.note" type="textarea" :autosize="{ minRows: 2 }" maxlength="2048" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="form.show = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>
            {{ t("common.cancel") }}
          </n-button>
          <n-button type="primary" :loading="form.busy" :disabled="form.busy" data-testid="device-lease-save" @click="save">
            <template #icon><n-icon><SaveIcon /></n-icon></template>
            {{ t("common.save") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </n-card>
</template>

<style scoped>
.hint { font-size: 12.5px; opacity: .7; }
</style>
