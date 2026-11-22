<script setup lang="ts">
/**
 * 站對站 VPN 清單：「進階 → 站對站 VPN」與電路頁的「站對站 VPN」頁籤共用（客戶許願清單：VPN 跟電路當成同一種管）。
 * 名稱連到 VPN 詳細資料（聯絡人、跑在哪條電路上）。
 *
 * 新增 / 編輯 / 刪除也做在這裡，兩個入口才會一樣（2026-11-21：以前只有沒有畫面的新增 API，
 * 手動記的通道建了改不了、刪不掉）。防火牆同步來的通道唯讀：下次同步會照設備的設定蓋回去。
 */
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import {
  NButton, NDataTable, NForm, NFormItem, NGrid, NGridItem, NIcon, NInput, NModal, NPopconfirm, NSelect,
  NSpace, NTag, NTooltip, useMessage, type DataTableColumns,
} from "naive-ui";
import { Physical, type VPNTunnel, type VPNTunnelWrite } from "@/api/phase3";
import { apiErrMsg } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { autoSort } from "@/composables/useTableSort";
import { useDeviceOptions } from "@/composables/useDeviceOptions";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { useTablePagination } from "@/composables/useTablePagination";
import { CancelIcon, DeleteIcon, EditIcon, PlusIcon, RefreshIcon, SaveIcon } from "@/icons";

const { t } = useI18n();
const msg = useMessage();
const auth = useAuthStore();
const links = useEntityLinks(useRouter());
const pg = useTablePagination();
const vpns = ref<VPNTunnel[]>([]);
const loading = ref(false);
const isAdmin = computed(() => !!auth.me?.is_admin);

const TYPES = ["ipsec_ikev2", "ipsec_ikev1", "wireguard", "openvpn", "l2tp", "vxlan", "vpls", "evpn", "other"] as const;
const TYPE_LABEL: Record<string, string> = {
  ipsec_ikev1: "IPsec IKEv1", ipsec_ikev2: "IPsec IKEv2", wireguard: "WireGuard", openvpn: "OpenVPN",
  l2tp: "L2TP", vxlan: "VXLAN", vpls: "VPLS", evpn: "EVPN",
};
const STATUSES = ["active", "planned", "offline", "decommissioned"] as const;
const typeLabel = (v: string) => (v === "other" ? t("physical.vpn_type_other") : TYPE_LABEL[v] ?? v);
const statusLabel = (v: string) => t(`circuits.status_${v}`);
const typeOpts = computed(() => TYPES.map((v) => ({ label: typeLabel(v), value: v })));
const statusOpts = computed(() => STATUSES.map((v) => ({ label: statusLabel(v), value: v })));

function iconBtn(icon: any, tip: string, type: "primary" | "error", disabled: boolean, onClick?: () => void) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type, disabled,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick?.(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => tip,
  });
}

const cols = computed<DataTableColumns<VPNTunnel>>(() => autoSort([
  // 欄寬給下限：沒設時手機寬度下名稱欄分到最少，「hq-branch-xxxx」被擠成一行一兩個字
  { title: t("common.name"), key: "name", minWidth: 160, render: (r) => links.vpn(r.id, r.name) },
  { title: t("cols.type"), key: "type", width: 120, render: (r) => typeLabel(r.type) },
  { title: t("common.status"), key: "status", width: 100, render: (r) => statusLabel(r.status) },
  {
    title: t("physical.vpn_peer"), key: "peer", minWidth: 260,
    render: (r) => {
      if (r.peered && r.a_device_name && r.b_device_name) {
        const reliable = r.pairing_method === "wireguard_pubkey";
        const badge = h(NTag, {
          size: "tiny", type: reliable ? "success" : "warning", bordered: false,
          style: "margin-left:8px",
        }, () => reliable ? t("physical.pair_reliable") : t("physical.pair_besteffort"));
        return h("span", { class: "vpn-peered" }, [
          `${r.a_device_name} ⇄ ${r.b_device_name}`, badge,
        ]);
      }
      if (r.b_endpoint) return h("span", { style: "opacity:.7" }, `→ ${r.b_endpoint}`);
      return h("span", { style: "opacity:.4" }, "—");
    },
  },
  {
    title: t("common.actions"), key: "_", className: "col-actions", width: 92,
    render: (r) => {
      // 同步來的通道：按鈕反灰，提示說明為什麼（改了也會被下次同步蓋掉）
      const synced = !!r.source_origin;
      const off = synced || !isAdmin.value;
      const tip = (base: string) => (synced ? t("physical.vpn_synced_tip") : !isAdmin.value ? t("errors.admin_required") : base);
      return h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
        iconBtn(EditIcon, tip(t("common.edit")), "primary", off, () => openEdit(r)),
        off
          ? iconBtn(DeleteIcon, tip(t("common.delete")), "error", true)
          : h(NPopconfirm, { onPositiveClick: () => remove(r) }, {
            trigger: () => iconBtn(DeleteIcon, t("common.delete"), "error", false),
            default: () => t("common.confirm_delete"),
          }),
      ]);
    },
  },
]));

async function refresh() {
  loading.value = true;
  try { vpns.value = await Physical.vpns(); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}
defineExpose({ refresh });
onMounted(refresh);

// ── 新增 / 編輯 ──
const devA = useDeviceOptions();
const devB = useDeviceOptions();
const show = ref(false);
const saving = ref(false);
const editingId = ref<string | null>(null);
const blank = (): VPNTunnelWrite => ({
  name: "", type: "ipsec_ikev2", status: "active", a_device_id: null, b_device_id: null,
  a_endpoint: "", b_endpoint: "", encryption_algo: "", auth_algo: "", description: "",
});
const form = ref<VPNTunnelWrite>(blank());

async function openForm(row: VPNTunnel | null) {
  editingId.value = row?.id ?? null;
  form.value = row ? {
    name: row.name, type: row.type, status: row.status,
    a_device_id: row.a_device_id ?? null, b_device_id: row.b_device_id ?? null,
    a_endpoint: row.a_endpoint ?? "", b_endpoint: row.b_endpoint ?? "",
    encryption_algo: row.encryption_algo ?? "", auth_algo: row.auth_algo ?? "", description: row.description ?? "",
  } : blank();
  show.value = true;
  await Promise.all([devA.search(), devB.search()]);
  await Promise.all([devA.ensure(row?.a_device_id), devB.ensure(row?.b_device_id)]);
}
const openCreate = () => openForm(null);
const openEdit = (r: VPNTunnel) => openForm(r);

async function save() {
  const f = form.value;
  if (!f.name.trim()) { msg.error(t("common.name_required")); return; }
  if (f.a_device_id && f.a_device_id === f.b_device_id) { msg.error(t("physical.vpn_same_device")); return; }
  // 清空的欄位送 null（不是不送），後端才會真的清掉
  const body: VPNTunnelWrite = {
    name: f.name.trim(), type: f.type, status: f.status,
    a_device_id: f.a_device_id ?? null, b_device_id: f.b_device_id ?? null,
    a_endpoint: f.a_endpoint?.trim() || null, b_endpoint: f.b_endpoint?.trim() || null,
    encryption_algo: f.encryption_algo?.trim() || null, auth_algo: f.auth_algo?.trim() || null,
    description: f.description?.trim() || null,
  };
  saving.value = true;
  try {
    if (editingId.value) await Physical.updateVpn(editingId.value, body);
    else await Physical.createVpn(body);
    show.value = false;
    msg.success(t("common.saved"));
    await refresh();
  } catch (e) { msg.error(apiErrMsg(e) || t("common.save_failed")); }
  finally { saving.value = false; }
}

async function remove(r: VPNTunnel) {
  try { await Physical.deleteVpn(r.id); msg.success(t("common.deleted")); await refresh(); }
  catch (e) { msg.error(apiErrMsg(e) || t("common.delete_failed")); }
}
</script>

<template>
  <n-space align="center" style="margin: 8px 0">
    <n-button @click="refresh" :loading="loading">
      <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
    </n-button>
    <n-button type="primary" :disabled="!isAdmin" :title="isAdmin ? undefined : t('errors.admin_required')"
              data-testid="vpn-create" @click="openCreate">
      <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("common.add") }}
    </n-button>
  </n-space>
  <n-data-table :columns="cols" :data="vpns" :loading="loading" :bordered="false" :pagination="pg"
                :scroll-x="722" data-testid="vpn-tunnel-table" />

  <n-modal v-model:show="show" preset="card" style="max-width: 640px" data-testid="vpn-form">
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="20"><component :is="editingId ? EditIcon : PlusIcon" /></n-icon>
        <span>{{ (editingId ? t("common.edit") : t("common.add")) + " " + t("nav.vpn_tunnels") }}</span>
      </n-space>
    </template>
    <n-form label-placement="top">
      <n-form-item :label="t('common.name')" required>
        <n-input v-model:value="form.name" maxlength="128" data-testid="vpn-name" />
      </n-form-item>
      <n-grid :cols="2" :x-gap="12" responsive="screen" item-responsive>
        <n-grid-item span="2 s:1">
          <n-form-item :label="t('cols.type')"><n-select v-model:value="form.type" :options="typeOpts" data-testid="vpn-type" /></n-form-item>
        </n-grid-item>
        <n-grid-item span="2 s:1">
          <n-form-item :label="t('common.status')"><n-select v-model:value="form.status" :options="statusOpts" data-testid="vpn-status" /></n-form-item>
        </n-grid-item>
        <n-grid-item span="2 s:1">
          <n-form-item :label="t('physical.vpn_a_device')">
            <n-select v-model:value="form.a_device_id" :options="devA.options.value" filterable clearable remote
                      :loading="devA.loading.value" @search="devA.onSearch" data-testid="vpn-a" />
          </n-form-item>
        </n-grid-item>
        <n-grid-item span="2 s:1">
          <n-form-item :label="t('physical.vpn_b_device')">
            <n-select v-model:value="form.b_device_id" :options="devB.options.value" filterable clearable remote
                      :loading="devB.loading.value" @search="devB.onSearch" data-testid="vpn-b" />
          </n-form-item>
        </n-grid-item>
        <n-grid-item span="2 s:1">
          <n-form-item :label="t('physical.vpn_a_endpoint')">
            <n-input v-model:value="form.a_endpoint" clearable maxlength="255" :placeholder="t('physical.vpn_endpoint_ph')" data-testid="vpn-a-ep" />
          </n-form-item>
        </n-grid-item>
        <n-grid-item span="2 s:1">
          <n-form-item :label="t('physical.vpn_b_endpoint')">
            <n-input v-model:value="form.b_endpoint" clearable maxlength="255" :placeholder="t('physical.vpn_endpoint_ph')" data-testid="vpn-b-ep" />
          </n-form-item>
        </n-grid-item>
        <n-grid-item span="2 s:1">
          <n-form-item :label="t('physical.vpn_encryption')">
            <n-input v-model:value="form.encryption_algo" clearable maxlength="32" placeholder="aes256gcm" data-testid="vpn-enc" />
          </n-form-item>
        </n-grid-item>
        <n-grid-item span="2 s:1">
          <n-form-item :label="t('physical.vpn_auth')">
            <n-input v-model:value="form.auth_algo" clearable maxlength="32" placeholder="sha256" data-testid="vpn-auth" />
          </n-form-item>
        </n-grid-item>
      </n-grid>
      <n-form-item :label="t('common.description')">
        <n-input v-model:value="form.description" type="textarea" clearable :autosize="{ minRows: 2, maxRows: 5 }"
                 maxlength="1024" data-testid="vpn-desc" />
      </n-form-item>
    </n-form>
    <template #footer>
      <n-space justify="end">
        <n-button @click="show = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" :loading="saving" :disabled="saving" data-testid="vpn-save" @click="save">
          <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
        </n-button>
      </n-space>
    </template>
  </n-modal>
</template>

<style scoped>
:deep(.vpn-peered) {
  color: #18a058;
  font-weight: 600;
}
</style>
