<script setup lang="ts">
import { computed, h, onMounted, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NTabs, NTabPane, NDataTable, NSpace, NIcon, NButton, NTooltip,
  NModal, NForm, NFormItem, NInput, NInputNumber, NSelect, NPopconfirm,
  NDatePicker,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { apiClient } from "@/api/client";
import { Advanced } from "@/api/phase3";
import { listDevices } from "@/api/basic";
import {
  AdvancedIcon, PlusIcon, DeleteIcon, EditIcon, RefreshIcon, SaveIcon, CancelIcon,
  CustomersIcon, VlansIcon, PhysicalIcon, UsersIcon, ScanAgentsIcon, ListIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";

const { t } = useI18n();
const msg = useMessage();
// 每個頁籤拆成獨立頁面：由路由帶 mode 進來，設定初始頁籤並隱藏頁籤列
const props = defineProps<{ mode?: "tenancy" | "asn" | "circuits" | "contacts" | "wireless" }>();
const tab = ref<"tenancy" | "asn" | "circuits" | "contacts" | "wireless">(props.mode ?? "tenancy");
// 同一元件被不同路由重用時 setup 不會重跑 → watch mode 同步頁籤
watch(() => props.mode, (m) => { if (m) tab.value = m; });
const headerTitle = computed(() => {
  switch (tab.value) {
    case "asn": return "ASN";
    case "circuits": return t("advanced.circuits");
    case "contacts": return t("advanced.contacts");
    case "wireless": return t("advanced.wireless");
    default: return t("advanced.tenancy");
  }
});
// 標題 icon 對應各模組（與左側選單一致）
const headerIcon = computed(() => {
  switch (tab.value) {
    case "asn": return VlansIcon;
    case "circuits": return PhysicalIcon;
    case "contacts": return UsersIcon;
    case "wireless": return ScanAgentsIcon;
    default: return CustomersIcon;
  }
});

const tenants = ref<any[]>([]);
const tenantGroups = ref<any[]>([]);
const asns = ref<any[]>([]);
const providers = ref<any[]>([]);
const circuitTypes = ref<any[]>([]);
const circuits = ref<any[]>([]);
const contactGroups = ref<any[]>([]);
const contacts = ref<any[]>([]);
const ssids = ref<any[]>([]);
const devices = ref<{ id: string; name: string }[]>([]);
const deviceOptions = computed(() => devices.value.map((d) => ({ label: d.name, value: d.id })));
const loading = ref(false);

async function loadAll() {
  loading.value = true;
  try {
    [tenants.value, tenantGroups.value, asns.value, providers.value,
     circuitTypes.value, circuits.value, contactGroups.value,
     contacts.value, ssids.value]
      = await Promise.all([
        Advanced.tenants(), Advanced.tenantGroups(), Advanced.asns(),
        Advanced.providers(), Advanced.circuitTypes(), Advanced.circuits(),
        Advanced.contactGroups(), Advanced.contacts(), Advanced.ssids(),
      ]);
    try { devices.value = (await listDevices({ pageSize: 500 })).items.map((d: any) => ({ id: d.id, name: d.name })); } catch { /* */ }
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}

async function delResource(resource: string, id: string) {
  try {
    await apiClient.delete(`/api/v1/${resource}/${id}`);
    await loadAll();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

// ── 各資源的 modal state ──
type Resource = "tenant" | "tenant_group" | "asn" | "provider" | "circuit" | "circuit_type" | "contact_group" | "contact" | "ssid";
const showCreate = ref(false);
const createKind = ref<Resource>("tenant");
const form = ref<Record<string, any>>({});
const editingId = ref<string | null>(null);   // null = 新增；有值 = 編輯

// 各資源的表單欄位骨架；有 row 就從既有資料帶入（編輯）
function formFor(kind: Resource, row?: any): Record<string, any> {
  const g = (k: string, d: any = null) => (row ? (row[k] ?? d) : d);
  switch (kind) {
    case "tenant":        return { name: g("name", ""), group_id: g("group_id"), description: g("description", "") };
    case "tenant_group":  return { name: g("name", ""), description: g("description", "") };
    case "asn":           return { asn: g("asn", 65000), rir: g("rir", ""), description: g("description", ""), tenant_id: g("tenant_id") };
    case "provider":      return { name: g("name", ""), account_number: g("account_number", ""), description: g("description", "") };
    case "circuit_type":  return { name: g("name", ""), description: g("description", "") };
    case "circuit":       return { cid: g("cid", ""), provider_id: g("provider_id"), type_id: g("type_id"), status: g("status", "active"), up_kbps: g("up_kbps"), down_kbps: g("down_kbps"), commit_rate_kbps: g("commit_rate_kbps"), monthly_fee_cents: g("monthly_fee_cents"), install_date: g("install_date"), contract_end_date: g("contract_end_date"), ip_address: g("ip_address", ""), gateway: g("gateway", ""), netmask: g("netmask", ""), dns_servers: g("dns_servers", ""), device_id: g("device_id"), description: g("description", "") };
    case "contact_group": return { name: g("name", ""), description: g("description", "") };
    case "contact":       return { name: g("name", ""), email: g("email", ""), phone: g("phone", ""), group_id: g("group_id"), description: g("description", "") };
    case "ssid":          return { ssid: g("ssid", ""), description: g("description", "") };
    default:              return {};
  }
}

function openCreate(kind: Resource) {
  createKind.value = kind;
  editingId.value = null;
  form.value = formFor(kind);
  showCreate.value = true;
}

function openEdit(kind: Resource, row: any) {
  createKind.value = kind;
  editingId.value = row.id;
  form.value = formFor(kind, row);
  showCreate.value = true;
}

const URL_MAP: Record<Resource, string> = {
  tenant:        "tenants",
  tenant_group:  "tenant-groups",
  asn:           "asns",
  provider:      "providers",
  circuit:       "circuits",
  circuit_type:  "circuit-types",
  contact_group: "contact-groups",
  contact:       "contacts",
  ssid:          "wireless/ssids",
};

async function submit() {
  // 清掉空字串 → null(避免 backend 嚴格驗證擋住)
  const payload: Record<string, any> = {};
  for (const [k, v] of Object.entries(form.value)) {
    payload[k] = v === "" ? null : v;
  }
  const nameless = ["asn", "circuit", "ssid"];
  if (!payload.name && !nameless.includes(createKind.value)) {
    msg.error(t("advanced.error_name_required"));
    return;
  }
  if (createKind.value === "ssid" && !payload.ssid) {
    msg.error(t("advanced.error_name_required"));
    return;
  }
  if (createKind.value === "asn" && !payload.asn) {
    msg.error(t("advanced.error_asn_number_required"));
    return;
  }
  // 電路類型(type_id)非必填；只需 CID 與供應商
  if (createKind.value === "circuit" && (!payload.cid || !payload.provider_id)) {
    msg.error(t("advanced.error_circuit_required"));
    return;
  }
  try {
    const base = `/api/v1/${URL_MAP[createKind.value]}`;
    if (editingId.value) {
      await apiClient.patch(`${base}/${editingId.value}`, payload);
    } else {
      await apiClient.post(base, payload);
    }
    showCreate.value = false;
    editingId.value = null;
    await loadAll();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

// ── select options ──
const tenantGroupOpts = computed(() => tenantGroups.value.map((g) => ({ label: g.name, value: g.id })));
const tenantOpts = computed(() => tenants.value.map((g) => ({ label: g.name, value: g.id })));
const providerOpts = computed(() => providers.value.map((g) => ({ label: g.name, value: g.id })));
const circuitTypeOpts = computed(() => circuitTypes.value.map((g) => ({ label: g.name, value: g.id })));
const contactGroupOpts = computed(() => contactGroups.value.map((g) => ({ label: g.name, value: g.id })));

// 電路類型支援即時新增：若選到的是使用者打字輸入(非既有 id)，就先建類型再選回新 id
async function onCircuitTypeSelect(val: string | null) {
  if (!val) return;
  if (circuitTypes.value.some((t) => t.id === val)) return;
  try {
    const created = await Advanced.createCircuitType(val);
    circuitTypes.value.push(created);
    form.value.type_id = created.id;
  } catch {
    form.value.type_id = null;
    msg.error(t("errors.server"));
  }
}

const editBtn = (kind: Resource, row: any) => h(NTooltip, null, {
  trigger: () => h(NButton, { size: "small", quaternary: true, type: "primary",
    onClick: (e: MouseEvent) => { e.stopPropagation(); openEdit(kind, row); } },
    { icon: () => h(NIcon, null, () => h(EditIcon)) }),
  default: () => t("common.edit"),
});

const delBtn = (resource: string, id: string) => h(NPopconfirm, {
  onPositiveClick: () => delResource(resource, id),
}, {
  trigger: () => h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type: "error",
      onClick: (e: MouseEvent) => { e.stopPropagation(); } },
      { icon: () => h(NIcon, null, () => h(DeleteIcon)) }),
    default: () => t("common.delete"),
  }),
  default: () => t("common.confirm_delete"),
});

// 編輯 + 刪除 一組（kind 用來開對應編輯表單；resource 是 API 路徑）
const actionsCell = (kind: Resource, resource: string, row: any) =>
  h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [editBtn(kind, row), delBtn(resource, row.id)]);

const tenantCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 180, ellipsis: { tooltip: true } },
  { title: t("advanced.tenant_group"), key: "group_id", width: 160, ellipsis: { tooltip: true },
    render: (r) => tenantGroups.value.find((g) => g.id === r.group_id)?.name ?? "—" },
  { title: t("sections.description"), key: "description", minWidth: 200, ellipsis: { tooltip: true }, render: (r) => r.description ?? "—" },
  { title: t("common.actions"), key: "_", className: "col-actions", width: 92, render: (r) => actionsCell("tenant", "tenants", r) },
]));
const tenantGroupCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 180, ellipsis: { tooltip: true } },
  { title: t("sections.description"), key: "description", minWidth: 220, ellipsis: { tooltip: true }, render: (r) => r.description ?? "—" },
  { title: t("common.actions"), key: "_", className: "col-actions", width: 92, render: (r) => actionsCell("tenant_group", "tenant-groups", r) },
]));
const asnCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: "ASN", key: "asn", width: 140 },
  { title: t("cols.rir"), key: "rir", width: 120, render: (r) => r.rir ?? "—" },
  { title: t("sections.description"), key: "description", minWidth: 220, ellipsis: { tooltip: true }, render: (r) => r.description ?? "—" },
  { title: t("common.actions"), key: "_", className: "col-actions", width: 92, render: (r) => actionsCell("asn", "asns", r) },
]));
const providerCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 180, ellipsis: { tooltip: true } },
  { title: t("circuits.account"), key: "account_number", width: 160, ellipsis: { tooltip: true }, render: (r) => r.account_number ?? "—" },
  { title: t("sections.description"), key: "description", minWidth: 200, ellipsis: { tooltip: true }, render: (r) => r.description ?? "—" },
  { title: t("common.actions"), key: "_", className: "col-actions", width: 92, render: (r) => actionsCell("provider", "providers", r) },
]));
const circuitCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("cols.cid"), key: "cid", minWidth: 160, ellipsis: { tooltip: true } },
  { title: t("circuits.provider"), key: "provider_id", width: 180, ellipsis: { tooltip: true },
    render: (r) => providers.value.find((p) => p.id === r.provider_id)?.name ?? "—" },
  { title: t("circuits.type"), key: "type_id", width: 160, ellipsis: { tooltip: true },
    render: (r) => circuitTypes.value.find((p) => p.id === r.type_id)?.name ?? "—" },
  { title: t("common.status"), key: "status", width: 120,
    render: (r) => { const k = `circuits.status_${r.status}`; const o = t(k); return o === k ? (r.status ?? "—") : o; } },
  { title: t("circuits.device"), key: "device_id", width: 160, ellipsis: { tooltip: true },
    render: (r) => devices.value.find((d) => d.id === r.device_id)?.name ?? "—" },
  { title: t("sections.description"), key: "description", minWidth: 200, ellipsis: { tooltip: true },
    render: (r) => r.description ?? "—" },
  { title: t("common.actions"), key: "_", className: "col-actions", width: 92, render: (r) => actionsCell("circuit", "circuits", r) },
]));
const circuitTypeCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  { title: t("sections.description"), key: "description", minWidth: 220, ellipsis: { tooltip: true }, render: (r) => r.description ?? "—" },
  { title: t("common.actions"), key: "_", className: "col-actions", width: 92, render: (r) => actionsCell("circuit_type", "circuit-types", r) },
]));
const contactCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  { title: t("cols.email"), key: "email", minWidth: 180, ellipsis: { tooltip: true }, render: (r) => r.email ?? "—" },
  { title: t("cols.phone"), key: "phone", width: 140, render: (r) => r.phone ?? "—" },
  { title: t("contacts.group"), key: "group_id", width: 160, ellipsis: { tooltip: true },
    render: (r) => contactGroups.value.find((g) => g.id === r.group_id)?.name ?? "—" },
  { title: t("common.actions"), key: "_", className: "col-actions", width: 92, render: (r) => actionsCell("contact", "contacts", r) },
]));
const contactGroupCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 180, ellipsis: { tooltip: true } },
  { title: t("sections.description"), key: "description", minWidth: 220, ellipsis: { tooltip: true }, render: (r) => r.description ?? "—" },
  { title: t("common.actions"), key: "_", className: "col-actions", width: 92, render: (r) => actionsCell("contact_group", "contact-groups", r) },
]));
const ssidCols = computed<DataTableColumns<any>>(() => autoSort([
  { title: "SSID", key: "ssid", minWidth: 180, ellipsis: { tooltip: true } },
  { title: t("sections.description"), key: "description", minWidth: 220, ellipsis: { tooltip: true }, render: (r) => r.description ?? "—" },
  { title: t("common.actions"), key: "_", className: "col-actions", width: 92, render: (r) => actionsCell("ssid", "wireless/ssids", r) },
]));

// 每張表的欄位顯示偏好（ColumnPicker + useColumnPrefs）+ 即時篩選。actions 欄(key="_")永遠保留。
function useTablePrefs(name: string, cols: typeof tenantCols, rows: typeof tenants) {
  const allKeys = cols.value.filter((c: any) => c.key && c.key !== "_").map((c: any) => String(c.key));
  const { visibleKeys, setVisible, reset } = useColumnPrefs(`advanced_${name}`, allKeys, allKeys);
  const items = computed(() => cols.value
    .filter((c: any) => c.key && c.key !== "_")
    .map((c: any) => ({ key: String(c.key), label: typeof c.title === "string" ? c.title : String(c.key) })));
  const visibleCols = computed<DataTableColumns<any>>(() =>
    cols.value.filter((c: any) => c.key === "_" || visibleKeys.value.includes(String(c.key))));
  const { query, filtered } = useTableQuickFilter(rows);
  return reactive({ visibleKeys, setVisible, reset, items, visibleCols, query, filtered });
}
const tenantP = useTablePrefs("tenants", tenantCols, tenants);
const tenantGroupP = useTablePrefs("tenant_groups", tenantGroupCols, tenantGroups);
const asnP = useTablePrefs("asns", asnCols, asns);
const providerP = useTablePrefs("providers", providerCols, providers);
const circuitP = useTablePrefs("circuits", circuitCols, circuits);
const circuitTypeP = useTablePrefs("circuit_types", circuitTypeCols, circuitTypes);
const contactGroupP = useTablePrefs("contact_groups", contactGroupCols, contactGroups);
const contactP = useTablePrefs("contacts", contactCols, contacts);
const ssidP = useTablePrefs("ssids", ssidCols, ssids);

onMounted(() => { void loadAll(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><component :is="mode ? headerIcon : AdvancedIcon" /></n-icon>
        <span>{{ mode ? headerTitle : t("nav.advanced") }}</span>
      </n-space>
    </template>
    <n-tabs v-model:value="tab" type="line" :class="{ 'single-mode': !!mode }">
      <n-tab-pane name="tenancy">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><CustomersIcon /></n-icon>{{ t('advanced.tenancy') }}</span>
        </template>
        <!-- 同頁多表 → 內層頁籤分開（比照防火牆規則/別名：type=line + icon） -->
        <n-tabs type="line">
          <n-tab-pane name="tenants">
            <template #tab>
              <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><CustomersIcon /></n-icon>{{ t('advanced.tenants') }}</span>
            </template>
            <n-space style="margin: 8px 0" align="center">
              <n-input v-model:value="tenantP.query" clearable style="width:180px" :placeholder="t('common.filter')" />
              <n-button @click="loadAll" :loading="loading">
                <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
              </n-button>
              <n-button type="primary" @click="openCreate('tenant')">
                <template #icon><n-icon><PlusIcon /></n-icon></template>
                {{ t("common.create") }}
              </n-button>
              <ColumnPicker :all="tenantP.items" :visible="tenantP.visibleKeys"
                            @update:visible="tenantP.setVisible" @reset="tenantP.reset" />
              <ExportButton :columns="tenantP.visibleCols" :rows="tenantP.filtered" filename="tenants" :title="t('advanced.tenants')" />
            </n-space>
            <n-data-table :columns="tenantP.visibleCols" :data="tenantP.filtered" :loading="loading" :bordered="false" :scroll-x="596" />
          </n-tab-pane>
          <n-tab-pane name="tenant_groups">
            <template #tab>
              <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><ListIcon /></n-icon>{{ t('advanced.tenant_groups') }}</span>
            </template>
            <n-space style="margin: 8px 0" align="center">
              <n-input v-model:value="tenantGroupP.query" clearable style="width:180px" :placeholder="t('common.filter')" />
              <n-button @click="loadAll" :loading="loading">
                <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
              </n-button>
              <n-button type="primary" @click="openCreate('tenant_group')">
                <template #icon><n-icon><PlusIcon /></n-icon></template>
                {{ t("common.create") }}
              </n-button>
              <ColumnPicker :all="tenantGroupP.items" :visible="tenantGroupP.visibleKeys"
                            @update:visible="tenantGroupP.setVisible" @reset="tenantGroupP.reset" />
              <ExportButton :columns="tenantGroupP.visibleCols" :rows="tenantGroupP.filtered" filename="tenant-groups" :title="t('advanced.tenant_groups')" />
            </n-space>
            <n-data-table :columns="tenantGroupP.visibleCols" :data="tenantGroupP.filtered" :loading="loading" :bordered="false" :scroll-x="456" />
          </n-tab-pane>
        </n-tabs>
      </n-tab-pane>

      <n-tab-pane name="asn">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><VlansIcon /></n-icon>ASN</span>
        </template>
        <n-space style="margin: 8px 0" align="center">
          <n-input v-model:value="asnP.query" clearable style="width:180px" :placeholder="t('common.filter')" />
          <n-button @click="loadAll" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" @click="openCreate('asn')">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("common.create") }}
          </n-button>
          <ColumnPicker :all="asnP.items" :visible="asnP.visibleKeys"
                        @update:visible="asnP.setVisible" @reset="asnP.reset" />
          <ExportButton :columns="asnP.visibleCols" :rows="asnP.filtered" filename="asns" title="ASN" />
        </n-space>
        <n-data-table :columns="asnP.visibleCols" :data="asnP.filtered" :loading="loading" :bordered="false" :scroll-x="536" />
      </n-tab-pane>

      <n-tab-pane name="circuits">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><PhysicalIcon /></n-icon>{{ t('advanced.circuits') }}</span>
        </template>
        <n-tabs type="line">
          <n-tab-pane name="providers">
            <template #tab>
              <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><UsersIcon /></n-icon>{{ t('circuits.providers') }}</span>
            </template>
            <n-space style="margin: 8px 0" align="center">
              <n-input v-model:value="providerP.query" clearable style="width:180px" :placeholder="t('common.filter')" />
              <n-button @click="loadAll" :loading="loading">
                <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
              </n-button>
              <n-button type="primary" @click="openCreate('provider')">
                <template #icon><n-icon><PlusIcon /></n-icon></template>
                {{ t("common.create") }}
              </n-button>
              <ColumnPicker :all="providerP.items" :visible="providerP.visibleKeys"
                            @update:visible="providerP.setVisible" @reset="providerP.reset" />
              <ExportButton :columns="providerP.visibleCols" :rows="providerP.filtered" filename="providers" :title="t('circuits.providers')" />
            </n-space>
            <n-data-table :columns="providerP.visibleCols" :data="providerP.filtered" :loading="loading" :bordered="false" :scroll-x="596" />
          </n-tab-pane>
          <n-tab-pane name="circuits">
            <template #tab>
              <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><PhysicalIcon /></n-icon>{{ t('advanced.circuits') }}</span>
            </template>
            <n-space style="margin: 8px 0" align="center">
              <n-input v-model:value="circuitP.query" clearable style="width:180px" :placeholder="t('common.filter')" />
              <n-button @click="loadAll" :loading="loading">
                <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
              </n-button>
              <n-button type="primary" @click="openCreate('circuit')">
                <template #icon><n-icon><PlusIcon /></n-icon></template>
                {{ t("common.create") }}
              </n-button>
              <ColumnPicker :all="circuitP.items" :visible="circuitP.visibleKeys"
                            @update:visible="circuitP.setVisible" @reset="circuitP.reset" />
              <ExportButton :columns="circuitP.visibleCols" :rows="circuitP.filtered" filename="circuits" :title="t('advanced.circuits')" />
            </n-space>
            <n-data-table :columns="circuitP.visibleCols" :data="circuitP.filtered" :loading="loading" :bordered="false" :scroll-x="1000" />
          </n-tab-pane>
          <n-tab-pane name="circuit_types">
            <template #tab>
              <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><ListIcon /></n-icon>{{ t('circuits.types') }}</span>
            </template>
            <div class="hint" style="margin: 8px 0 4px">{{ t("circuits.types_hint") }}</div>
            <n-space style="margin: 8px 0" align="center">
              <n-input v-model:value="circuitTypeP.query" clearable style="width:180px" :placeholder="t('common.filter')" />
              <n-button @click="loadAll" :loading="loading">
                <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
              </n-button>
              <n-button type="primary" @click="openCreate('circuit_type')">
                <template #icon><n-icon><PlusIcon /></n-icon></template>
                {{ t("common.create") }}
              </n-button>
              <ColumnPicker :all="circuitTypeP.items" :visible="circuitTypeP.visibleKeys"
                            @update:visible="circuitTypeP.setVisible" @reset="circuitTypeP.reset" />
              <ExportButton :columns="circuitTypeP.visibleCols" :rows="circuitTypeP.filtered" filename="circuit-types" :title="t('circuits.types')" />
            </n-space>
            <n-data-table :columns="circuitTypeP.visibleCols" :data="circuitTypeP.filtered" :loading="loading" :bordered="false" :scroll-x="472" />
          </n-tab-pane>
        </n-tabs>
      </n-tab-pane>

      <n-tab-pane name="contacts">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><UsersIcon /></n-icon>{{ t('advanced.contacts') }}</span>
        </template>
        <n-tabs type="line">
          <n-tab-pane name="contacts">
            <template #tab>
              <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><UsersIcon /></n-icon>{{ t('advanced.contacts') }}</span>
            </template>
            <n-space style="margin: 8px 0" align="center">
              <n-input v-model:value="contactP.query" clearable style="width:180px" :placeholder="t('common.filter')" />
              <n-button @click="loadAll" :loading="loading">
                <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
              </n-button>
              <n-button type="primary" @click="openCreate('contact')">
                <template #icon><n-icon><PlusIcon /></n-icon></template>
                {{ t("common.create") }}
              </n-button>
              <ColumnPicker :all="contactP.items" :visible="contactP.visibleKeys"
                            @update:visible="contactP.setVisible" @reset="contactP.reset" />
              <ExportButton :columns="contactP.visibleCols" :rows="contactP.filtered" filename="contacts" :title="t('advanced.contacts')" />
            </n-space>
            <n-data-table :columns="contactP.visibleCols" :data="contactP.filtered" :loading="loading" :bordered="false" :scroll-x="696" />
          </n-tab-pane>
          <n-tab-pane name="contact_groups">
            <template #tab>
              <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><ListIcon /></n-icon>{{ t('advanced.contact_groups') }}</span>
            </template>
            <n-space style="margin: 8px 0" align="center">
              <n-input v-model:value="contactGroupP.query" clearable style="width:180px" :placeholder="t('common.filter')" />
              <n-button @click="loadAll" :loading="loading">
                <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
              </n-button>
              <n-button type="primary" @click="openCreate('contact_group')">
                <template #icon><n-icon><PlusIcon /></n-icon></template>
                {{ t("common.create") }}
              </n-button>
              <ColumnPicker :all="contactGroupP.items" :visible="contactGroupP.visibleKeys"
                            @update:visible="contactGroupP.setVisible" @reset="contactGroupP.reset" />
              <ExportButton :columns="contactGroupP.visibleCols" :rows="contactGroupP.filtered" filename="contact-groups" :title="t('advanced.contact_groups')" />
            </n-space>
            <n-data-table :columns="contactGroupP.visibleCols" :data="contactGroupP.filtered" :loading="loading" :bordered="false" :scroll-x="456" />
          </n-tab-pane>
        </n-tabs>
      </n-tab-pane>

      <n-tab-pane name="wireless">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><ScanAgentsIcon /></n-icon>{{ t('advanced.wireless') }}</span>
        </template>
        <n-space style="margin: 8px 0" align="center">
          <n-input v-model:value="ssidP.query" clearable style="width:180px" :placeholder="t('common.filter')" />
          <n-button @click="loadAll" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" @click="openCreate('ssid')">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("common.create") }}
          </n-button>
          <ColumnPicker :all="ssidP.items" :visible="ssidP.visibleKeys"
                        @update:visible="ssidP.setVisible" @reset="ssidP.reset" />
          <ExportButton :columns="ssidP.visibleCols" :rows="ssidP.filtered" filename="ssids" title="SSID" />
        </n-space>
        <n-data-table :columns="ssidP.visibleCols" :data="ssidP.filtered" :loading="loading" :bordered="false" :scroll-x="456" />
      </n-tab-pane>
    </n-tabs>

    <n-modal v-model:show="showCreate" preset="card" style="width: 520px">
      <template #header>
        <div style="display: flex; align-items: center; gap: 8px; line-height: 1">
          <n-icon :size="20"><component :is="editingId ? EditIcon : PlusIcon" /></n-icon>
          <span>{{ editingId ? t("common.edit") : t(`advanced.create_${createKind}`) }}</span>
        </div>
      </template>

      <n-form label-placement="top">
        <!-- Tenant -->
        <template v-if="createKind === 'tenant'">
          <n-form-item :label="t('common.name')">
            <n-input v-model:value="form.name" placeholder="ACME Corp" />
          </n-form-item>
          <n-form-item :label="t('advanced.tenant_group')">
            <n-select v-model:value="form.group_id" :options="tenantGroupOpts" clearable
                      :placeholder="t('advanced.tenant_group_placeholder')" />
          </n-form-item>
          <n-form-item :label="t('sections.description')">
            <n-input v-model:value="form.description" type="textarea" :rows="2" />
          </n-form-item>
        </template>

        <!-- Tenant Group -->
        <template v-else-if="createKind === 'tenant_group'">
          <n-form-item :label="t('common.name')">
            <n-input v-model:value="form.name" />
          </n-form-item>
          <n-form-item :label="t('sections.description')">
            <n-input v-model:value="form.description" type="textarea" :rows="2" />
          </n-form-item>
        </template>

        <!-- ASN -->
        <template v-else-if="createKind === 'asn'">
          <n-form-item label="AS Number">
            <n-input-number v-model:value="form.asn" :min="1" :max="4294967295" />
          </n-form-item>
          <n-form-item label="RIR">
            <n-select v-model:value="form.rir" clearable
                      :options="['APNIC','ARIN','RIPE','LACNIC','AFRINIC'].map(v => ({label: v, value: v}))" />
          </n-form-item>
          <n-form-item :label="t('advanced.owner_tenant')">
            <n-select v-model:value="form.tenant_id" :options="tenantOpts" clearable filterable />
          </n-form-item>
          <n-form-item :label="t('sections.description')">
            <n-input v-model:value="form.description" type="textarea" :rows="2" />
          </n-form-item>
        </template>

        <!-- Provider -->
        <template v-else-if="createKind === 'provider'">
          <n-form-item :label="t('common.name')">
            <n-input v-model:value="form.name" placeholder="HiNet / TFN / NTT …" />
          </n-form-item>
          <n-form-item :label="t('circuits.account')">
            <n-input v-model:value="form.account_number" :placeholder="t('advanced.account_ph')" />
          </n-form-item>
          <n-form-item :label="t('sections.description')">
            <n-input v-model:value="form.description" type="textarea" :rows="2" />
          </n-form-item>
        </template>

        <!-- Circuit Type -->
        <template v-else-if="createKind === 'circuit_type'">
          <n-form-item :label="t('common.name')">
            <n-input v-model:value="form.name" :placeholder="t('circuits.type_name_ph')" />
          </n-form-item>
          <n-form-item :label="t('sections.description')">
            <n-input v-model:value="form.description" type="textarea" :rows="2" />
          </n-form-item>
        </template>

        <!-- Circuit -->
        <template v-else-if="createKind === 'circuit'">
          <n-form-item :label="t('advanced.cid_label')">
            <n-input v-model:value="form.cid" placeholder="CID-TPE-001" />
          </n-form-item>
          <n-form-item :label="t('circuits.provider')">
            <n-select v-model:value="form.provider_id" :options="providerOpts" filterable
                      :placeholder="t('circuits.provider_placeholder')" />
          </n-form-item>
          <n-form-item :label="t('circuits.type')">
            <n-select v-model:value="form.type_id" :options="circuitTypeOpts" filterable tag clearable
                      :placeholder="t('circuits.type_placeholder')"
                      @update:value="onCircuitTypeSelect" />
          </n-form-item>
          <n-form-item :label="t('common.status')">
            <n-select v-model:value="form.status"
                      :options="['active','planned','provisioning','offline','decommissioned'].map(v => ({label: t('circuits.status_'+v), value: v}))" />
          </n-form-item>
          <div class="circuit-row">
            <n-form-item :label="t('circuits.down_kbps')" :show-feedback="false">
              <n-input-number v-model:value="form.down_kbps" :min="0" :step="1000" style="width: 100%" />
            </n-form-item>
            <n-form-item :label="t('circuits.up_kbps')" :show-feedback="false">
              <n-input-number v-model:value="form.up_kbps" :min="0" :step="1000" style="width: 100%" />
            </n-form-item>
          </div>
          <div class="circuit-row">
            <n-form-item :label="t('circuits.commit_kbps')" :show-feedback="false">
              <n-input-number v-model:value="form.commit_rate_kbps" :min="0" :step="1000" style="width: 100%" />
            </n-form-item>
            <n-form-item :label="t('circuits.monthly_fee')" :show-feedback="false">
              <n-input-number v-model:value="form.monthly_fee_cents" :min="0" style="width: 100%" />
            </n-form-item>
          </div>
          <div class="circuit-row">
            <n-form-item :label="t('circuits.install_date')" :show-feedback="false">
              <n-date-picker v-model:formatted-value="form.install_date" value-format="yyyy-MM-dd" type="date" clearable style="width: 100%" />
            </n-form-item>
            <n-form-item :label="t('circuits.contract_end')" :show-feedback="false">
              <n-date-picker v-model:formatted-value="form.contract_end_date" value-format="yyyy-MM-dd" type="date" clearable style="width: 100%" />
            </n-form-item>
          </div>
          <div class="circuit-row">
            <n-form-item :label="t('circuits.ip_address')" :show-feedback="false">
              <n-input v-model:value="form.ip_address" placeholder="203.0.113.10/30" />
            </n-form-item>
            <n-form-item :label="t('circuits.gateway')" :show-feedback="false">
              <n-input v-model:value="form.gateway" placeholder="203.0.113.9" />
            </n-form-item>
          </div>
          <div class="circuit-row">
            <n-form-item :label="t('circuits.netmask')" :show-feedback="false">
              <n-input v-model:value="form.netmask" placeholder="255.255.255.252" />
            </n-form-item>
            <n-form-item :label="t('circuits.dns_servers')" :show-feedback="false">
              <n-input v-model:value="form.dns_servers" placeholder="8.8.8.8, 1.1.1.1" />
            </n-form-item>
          </div>
          <n-form-item :label="t('circuits.device')">
            <n-select v-model:value="form.device_id" :options="deviceOptions" filterable clearable
                      :placeholder="t('circuits.device_ph')" />
          </n-form-item>
          <n-form-item :label="t('sections.description')">
            <n-input v-model:value="form.description" type="textarea" :rows="2" />
          </n-form-item>
        </template>

        <!-- Contact Group -->
        <template v-else-if="createKind === 'contact_group'">
          <n-form-item :label="t('common.name')">
            <n-input v-model:value="form.name" :placeholder="t('advanced.contact_name_ph')" />
          </n-form-item>
          <n-form-item :label="t('sections.description')">
            <n-input v-model:value="form.description" type="textarea" :rows="2" />
          </n-form-item>
        </template>

        <!-- Contact -->
        <template v-else-if="createKind === 'contact'">
          <n-form-item :label="t('common.name')">
            <n-input v-model:value="form.name" />
          </n-form-item>
          <n-space>
            <n-form-item label="Email" style="min-width: 240px">
              <n-input v-model:value="form.email" placeholder="alice@example.com" />
            </n-form-item>
            <n-form-item label="Phone" style="min-width: 200px">
              <n-input v-model:value="form.phone" placeholder="+886-2-1234-5678" />
            </n-form-item>
          </n-space>
          <n-form-item :label="t('contacts.group')">
            <n-select v-model:value="form.group_id" :options="contactGroupOpts" clearable filterable />
          </n-form-item>
          <n-form-item :label="t('sections.description')">
            <n-input v-model:value="form.description" type="textarea" :rows="2" />
          </n-form-item>
        </template>

        <!-- SSID -->
        <template v-else-if="createKind === 'ssid'">
          <n-form-item label="SSID">
            <n-input v-model:value="form.ssid" placeholder="Corp-WiFi" />
          </n-form-item>
          <n-form-item :label="t('sections.description')">
            <n-input v-model:value="form.description" type="textarea" :rows="2" />
          </n-form-item>
        </template>
      </n-form>

      <n-space justify="end">
        <n-button @click="showCreate = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>

<style scoped>
/* 拆成獨立頁面時隱藏「外層」頁籤列（只顯示該模組內容）。
   用直接子選擇器，避免連內層 segment 頁籤列(供應商/電路/類型…)也一起被藏掉。 */
.single-mode > :deep(.n-tabs-nav) { display: none; }
/* Circuit 表單兩欄：等寬、輸入框填滿欄寬、上緣對齊（取代會錯位的固定寬 n-space） */
.circuit-row { display: flex; gap: 12px; margin-bottom: 14px; }
.circuit-row > * { flex: 1 1 0; min-width: 0; }
</style>
