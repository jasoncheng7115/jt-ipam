<script setup lang="ts">
// 裝置群組（堆疊／HA／叢集，客戶 2026-11-05：兩台 N2048P 堆疊標不出來、IP 也不能各分）。
// 裝置頁的「群組」頁籤。讀：登入即可；新增／修改／刪除：管理員（後端也擋）。
import { computed, h, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import {
  NAlert, NButton, NDataTable, NEmpty, NForm, NFormItem, NIcon, NInput, NInputNumber, NModal,
  NPopconfirm, NSelect, NSpace, NTag, NTooltip, useMessage, type DataTableColumns,
} from "naive-ui";
import {
  listDeviceGroups, createDeviceGroup, updateDeviceGroup, deleteDeviceGroup,
  GROUP_KINDS, ROLES_BY_KIND, type DeviceGroup, type GroupKind,
} from "@/api/deviceGroups";
import { listDevices } from "@/api/basic";
import { listAddresses } from "@/api/addresses";
import { apiErrMsg } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { useTablePagination } from "@/composables/useTablePagination";
import { cmpNatural } from "@/utils/sort";
import {
  CancelIcon, DeleteIcon, EditIcon, GroupIcon, InfoIcon, PlusIcon, RefreshIcon, SaveIcon,
} from "@/icons";

const { t } = useI18n();
const msg = useMessage();
const route = useRoute();
const router = useRouter();
const links = useEntityLinks(router);
const auth = useAuthStore();
const isAdmin = computed(() => !!auth.me?.is_admin);
const pg = useTablePagination();

const rows = ref<DeviceGroup[]>([]);
const loading = ref(false);
const filterQ = ref("");
async function refresh() {
  loading.value = true;
  try { rows.value = await listDeviceGroups(); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}
const shown = computed(() => {
  const q = filterQ.value.trim().toLowerCase();
  if (!q) return rows.value;
  return rows.value.filter((g) => g.name.toLowerCase().includes(q)
    || g.members.some((m) => m.name.toLowerCase().includes(q))
    || g.shared_ips.some((ip) => ip.ip.includes(q)));
});

const kindOptions = computed(() => GROUP_KINDS.map((k) => ({ label: t(`device_groups.kind_${k}`), value: k })));
const roleLabel = (r: string | null) => (r ? t(`device_groups.role_${r}`) : "");

function iconAction(icon: any, label: string, onClick: () => void, type?: any, testid?: string) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type, "data-testid": testid,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}

const cols = computed<DataTableColumns<DeviceGroup>>(() => [
  { title: t("common.name"), key: "name", width: 180, sorter: (a, b) => cmpNatural(a.name, b.name),
    render: (g) => h("b", null, g.name) },
  { title: t("device_groups.kind"), key: "kind", width: 150,
    render: (g) => h(NTag, { size: "small", type: "info", bordered: false }, () => t(`device_groups.kind_${g.kind}`)) },
  { title: t("device_groups.members"), key: "members",
    render: (g) => h("div", { class: "dg-members" }, [...g.members]
      .sort((a, b) => (a.order ?? 999) - (b.order ?? 999) || cmpNatural(a.name, b.name))
      .map((m) => h("span", { class: "dg-member" }, [
        links.device(m.device_id, m.name),
        m.role || m.order ? h("span", { class: "dg-meta" },
          [roleLabel(m.role), m.order ? `#${m.order}` : ""].filter(Boolean).join(" ")) : null,
        m.device_id === g.representative_id
          ? h(NTag, { size: "tiny", bordered: false, type: "success" }, () => t("device_groups.representative")) : null,
        m.rack_name ? h("span", { class: "dg-meta" }, m.u_position ? `${m.rack_name} U${m.u_position}` : m.rack_name) : null,
      ]))) },
  { title: t("device_groups.shared_ips"), key: "shared_ips", width: 200,
    render: (g) => (g.shared_ips.length
      ? h("div", { class: "dg-members" }, g.shared_ips.map((ip) => links.ipById(ip.id, ip.ip)))
      : "—") },
  { title: t("common.actions"), key: "actions", width: 96, fixed: "right", className: "col-actions",
    render: (g) => (isAdmin.value ? h(NSpace, { size: 2, wrap: false, wrapItem: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(g), "success", "dg-edit"),
      h(NPopconfirm, { onPositiveClick: () => del(g) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error", "dg-delete"),
        default: () => t("device_groups.confirm_delete"),
      }),
    ]) : "—") },
]);

// ── 新增／編輯 ──────────────────────────────────────────────
interface MemberRow { device_id: string | null; role: string; order: number | null }
const show = ref(false);
const showHelp = ref(false);
const editing = ref<DeviceGroup | null>(null);
const saving = ref(false);
const form = ref({ name: "", kind: "stack" as GroupKind, description: "" as string | null,
  members: [] as MemberRow[], representative_id: null as string | null, shared_ip_ids: [] as string[] });
/** 選項的顯示文字：已選的裝置／IP 不在這次搜尋結果裡時也要顯示名稱，不能退回一串 UUID */
const deviceNames = ref<Record<string, string>>({});
const ipNames = ref<Record<string, string>>({});
const deviceHits = ref<{ id: string; name: string }[]>([]);
const ipHits = ref<{ id: string; ip: string }[]>([]);
const devSearching = ref(false);
const ipSearching = ref(false);

const roleOptions = computed(() => (ROLES_BY_KIND[form.value.kind] ?? ["member"])
  .map((r) => ({ label: t(`device_groups.role_${r}`), value: r })));
watch(() => form.value.kind, (k) => {
  // 換類型時角色要跟著換：第一台給主導角色（堆疊主控、HA 主要），其餘給預設角色。
  // 以前一律給預設角色，HA 主備兩台都變成「備援」（使用者 11-06 截圖）
  const ok = ROLES_BY_KIND[k] ?? ["member"];
  const lead = ok.length > 1 ? ok[1] : ok[0];
  form.value.members.forEach((m, i) => { if (!ok.includes(m.role)) m.role = i === 0 ? lead : ok[0]; });
});
/** 第 i 列的裝置選項：別列已經選了的不列（同一台不能加兩次），這一列自己選的要留著 */
function deviceOptionsFor(i: number) {
  const mine = form.value.members[i]?.device_id ?? null;
  const taken = new Set(form.value.members.map((m, j) => (j === i ? null : m.device_id)).filter(Boolean));
  const opts = deviceHits.value.filter((d) => !taken.has(d.id)).map((d) => ({ label: d.name, value: d.id }));
  if (mine && !opts.some((o) => o.value === mine)) opts.unshift({ label: deviceNames.value[mine] ?? mine, value: mine });
  return opts;
}
const repOptions = computed(() => form.value.members.filter((m) => m.device_id)
  .map((m) => ({ label: deviceNames.value[m.device_id!] ?? m.device_id!, value: m.device_id! })));
const ipOptions = computed(() => {
  const ids = new Set(ipHits.value.map((x) => x.id));
  return [...form.value.shared_ip_ids.filter((id) => !ids.has(id)).map((id) => ({ label: ipNames.value[id] ?? id, value: id })),
          ...ipHits.value.map((x) => ({ label: x.ip, value: x.id }))];
});

let devTimer: ReturnType<typeof setTimeout> | null = null;
function searchDevices(q: string) {
  if (devTimer) clearTimeout(devTimer);
  devTimer = setTimeout(async () => {
    devSearching.value = true;
    try {
      const r = await listDevices({ q, pageSize: 50 });
      deviceHits.value = r.items.map((d) => ({ id: d.id, name: d.name }));
      for (const d of r.items) deviceNames.value[d.id] = d.name;
    } catch { /* 搜尋失敗就維持原本的選項 */ } finally { devSearching.value = false; }
  }, 250);
}
let ipTimer: ReturnType<typeof setTimeout> | null = null;
function searchIps(q: string) {
  if (ipTimer) clearTimeout(ipTimer);
  if (!q.trim()) { ipHits.value = []; return; }
  ipTimer = setTimeout(async () => {
    ipSearching.value = true;
    try {
      const r = await listAddresses({ q, pageSize: 50 });
      ipHits.value = r.items.map((x) => ({ id: x.id, ip: String(x.ip).split("/")[0] }));
      for (const x of ipHits.value) ipNames.value[x.id] = x.ip;
    } catch { /* 同上 */ } finally { ipSearching.value = false; }
  }, 250);
}

function openCreate() {
  editing.value = null;
  form.value = { name: "", kind: "stack", description: "", members: [
    { device_id: null, role: "master", order: 1 }, { device_id: null, role: "member", order: 2 }],
    representative_id: null, shared_ip_ids: [] };
  searchDevices("");
  ipHits.value = [];
  show.value = true;
}
function openEdit(g: DeviceGroup) {
  editing.value = g;
  for (const m of g.members) deviceNames.value[m.device_id] = m.name;
  for (const ip of g.shared_ips) ipNames.value[ip.id] = ip.ip;
  form.value = { name: g.name, kind: g.kind, description: g.description ?? "",
    members: [...g.members].sort((a, b) => (a.order ?? 999) - (b.order ?? 999))
      .map((m) => ({ device_id: m.device_id, role: m.role ?? (ROLES_BY_KIND[g.kind]?.[0] ?? "member"), order: m.order })),
    representative_id: g.representative_id, shared_ip_ids: g.shared_ips.map((x) => x.id) };
  searchDevices("");
  ipHits.value = [];
  show.value = true;
}
function addMember() {
  const next = Math.max(0, ...form.value.members.map((m) => m.order ?? 0)) + 1;
  form.value.members.push({ device_id: null, role: ROLES_BY_KIND[form.value.kind]?.[0] ?? "member", order: next });
}
function removeMember(i: number) {
  const [gone] = form.value.members.splice(i, 1);
  if (gone?.device_id && gone.device_id === form.value.representative_id) form.value.representative_id = null;
}
const canSave = computed(() => !!form.value.name.trim() && form.value.members.some((m) => m.device_id));
async function save() {
  const members = form.value.members.filter((m) => m.device_id)
    .map((m) => ({ device_id: m.device_id!, role: m.role, order: m.order ?? null }));
  const rep = form.value.representative_id && members.some((m) => m.device_id === form.value.representative_id)
    ? form.value.representative_id : null;
  const body = { name: form.value.name.trim(), kind: form.value.kind, description: form.value.description || null,
    members, representative_id: rep, shared_ip_ids: form.value.shared_ip_ids };
  saving.value = true;
  try {
    if (editing.value) await updateDeviceGroup(editing.value.id, body);
    else await createDeviceGroup(body);
    msg.success(t("common.ok"));
    show.value = false;
    await refresh();
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { saving.value = false; }
}
async function del(g: DeviceGroup) {
  try { await deleteDeviceGroup(g.id); msg.success(t("common.ok")); await refresh(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

onMounted(async () => {
  await refresh();
  // 從機櫃圖的「管理群組」帶 ?group=<id> 進來：直接開那個群組（管理員開編輯，其他人只篩出那一列）
  const want = typeof route.query.group === "string" ? route.query.group : "";
  const g = want ? rows.value.find((x) => x.id === want) : undefined;
  if (g) { if (isAdmin.value) openEdit(g); else filterQ.value = g.name; }
});
</script>

<template>
  <div data-testid="device-groups-panel">
    <n-space style="margin-bottom: 12px" align="center">
      <n-input v-model:value="filterQ" :placeholder="t('common.filter')" clearable style="width: 220px" />
      <n-button :loading="loading" @click="refresh">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button v-if="isAdmin" type="primary" data-testid="dg-create" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("common.create") }}
      </n-button>
      <!-- 說明（使用者 2026-11-06）：這個功能是什麼、什麼時候用、欄位與規則 -->
      <n-button data-testid="dg-help" @click="showHelp = true">
        <template #icon><n-icon><InfoIcon /></n-icon></template>
        {{ t("device_groups.help_button") }}
      </n-button>
    </n-space>
    <n-alert v-if="!isAdmin" type="info" :bordered="false" style="margin-bottom: 12px">
      {{ t("device_groups.admin_only") }}
    </n-alert>
    <n-data-table v-if="rows.length || loading" :columns="cols" :data="shown" :loading="loading"
                  :bordered="false" :scroll-x="900" :pagination="pg" :row-key="(g: DeviceGroup) => g.id" />
    <n-empty v-else :description="t('device_groups.empty')" style="padding: 32px 0" />

    <n-modal v-model:show="showHelp" preset="card" style="width: 640px; max-width: calc(100vw - 32px)"
             data-testid="dg-help-modal">
      <template #header>
        <n-space align="center" :wrap-item="false">
          <n-icon :size="20"><InfoIcon /></n-icon>
          <span>{{ t("device_groups.help_title") }}</span>
        </n-space>
      </template>
      <div class="dg-help">
        <p>{{ t("device_groups.help_intro") }}</p>
        <h4>{{ t("device_groups.help_when") }}</h4>
        <ul>
          <li v-for="k in ['stack', 'ha_ap', 'ha_aa', 'cluster']" :key="k">{{ t(`device_groups.help_when_${k}`) }}</li>
        </ul>
        <h4>{{ t("device_groups.help_fields") }}</h4>
        <ul>
          <li v-for="k in ['kind', 'rep', 'ips']" :key="k">{{ t(`device_groups.help_field_${k}`) }}</li>
        </ul>
        <h4>{{ t("device_groups.help_where") }}</h4>
        <ul>
          <li v-for="k in ['rack', 'pages', 'logic']" :key="k">{{ t(`device_groups.help_where_${k}`) }}</li>
        </ul>
        <h4>{{ t("device_groups.help_notes") }}</h4>
        <ul>
          <li v-for="k in ['device', 'one', 'admin', 'delete']" :key="k">{{ t(`device_groups.help_note_${k}`) }}</li>
        </ul>
      </div>
      <n-space justify="end" style="margin-top: 12px">
        <n-button @click="showHelp = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.close") }}
        </n-button>
      </n-space>
    </n-modal>

    <n-modal v-model:show="show" preset="card" style="width: 640px; max-width: calc(100vw - 32px)">
      <template #header>
        <n-space align="center" :wrap-item="false">
          <n-icon :size="20"><GroupIcon /></n-icon>
          <span>{{ editing ? t("device_groups.edit_title") : t("device_groups.create_title") }}</span>
        </n-space>
      </template>
      <n-form label-placement="top">
        <div class="dg-grid">
          <n-form-item :label="t('common.name')" required>
            <n-input v-model:value="form.name" maxlength="128" data-testid="dg-name" />
          </n-form-item>
          <n-form-item :label="t('device_groups.kind')">
            <n-select v-model:value="form.kind" :options="kindOptions" data-testid="dg-kind" />
          </n-form-item>
        </div>
        <n-form-item :label="t('device_groups.members')" required>
          <div class="dg-rows">
            <div v-for="(m, i) in form.members" :key="i" class="dg-row" data-testid="dg-member-row">
              <n-select v-model:value="m.device_id" :options="deviceOptionsFor(i)" filterable remote clearable
                        :loading="devSearching" :placeholder="t('device_groups.pick_device')"
                        @search="searchDevices" @update:value="(v: string | null) => v && searchDevices('')">
                <template #empty><span class="dg-empty">{{ t("device_groups.no_device_hint") }}</span></template>
              </n-select>
              <n-select v-model:value="m.role" :options="roleOptions" style="width: 120px" />
              <n-input-number v-model:value="m.order" :min="1" :max="999" :placeholder="t('device_groups.order')"
                              :show-button="false" style="width: 72px" />
              <n-tooltip>
                <template #trigger>
                  <n-button quaternary type="error" :disabled="form.members.length <= 1" @click="removeMember(i)">
                    <template #icon><n-icon><DeleteIcon /></n-icon></template>
                  </n-button>
                </template>
                {{ t("device_groups.remove_member") }}
              </n-tooltip>
            </div>
            <n-button dashed size="small" data-testid="dg-add-member" @click="addMember">
              <template #icon><n-icon><PlusIcon /></n-icon></template>
              {{ t("device_groups.add_member") }}
            </n-button>
          </div>
        </n-form-item>
        <n-form-item :label="t('device_groups.representative')">
          <div style="width: 100%">
            <n-select v-model:value="form.representative_id" :options="repOptions" clearable
                      data-testid="dg-representative" />
            <p class="dg-hint">{{ t("device_groups.representative_hint") }}</p>
          </div>
        </n-form-item>
        <n-form-item :label="t('device_groups.shared_ips')">
          <div style="width: 100%">
            <n-select v-model:value="form.shared_ip_ids" :options="ipOptions" multiple filterable remote clearable
                      :loading="ipSearching" :placeholder="t('device_groups.pick_ip')" data-testid="dg-shared-ips"
                      @search="searchIps" />
            <p class="dg-hint">{{ t("device_groups.shared_ips_hint") }}</p>
          </div>
        </n-form-item>
        <n-form-item :label="t('device_groups.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" maxlength="2000" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="show = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" :loading="saving" :disabled="!canSave || saving" data-testid="dg-save" @click="save">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>
  </div>
</template>

<style scoped>
.dg-help { font-size: 14px; line-height: 1.7; }
.dg-help p { margin: 0 0 8px; }
.dg-help h4 { margin: 14px 0 4px; font-size: 14px; }
.dg-help ul { margin: 0; padding-left: 20px; }
.dg-help li { margin: 2px 0; }
.dg-grid { display: grid; grid-template-columns: minmax(0, 1fr) 200px; gap: 12px; }
@media (max-width: 560px) { .dg-grid { grid-template-columns: 1fr; } }
.dg-rows { display: grid; gap: 8px; width: 100%; }
.dg-row { display: flex; gap: 8px; align-items: center; }
.dg-row > :first-child { flex: 1 1 auto; min-width: 0; }
.dg-hint { margin: 4px 0 0; font-size: 12px; opacity: 0.6; line-height: 1.5; }
.dg-empty { display: block; max-width: 320px; font-size: 12px; opacity: 0.7; line-height: 1.5; white-space: normal; }
/* 表格儲存格是 h() 畫的，吃不到 scoped 樣式 → 用 :deep */
:deep(.dg-members) { display: flex; flex-direction: column; gap: 2px; }
:deep(.dg-member) { display: inline-flex; flex-wrap: wrap; gap: 6px; align-items: center; }
:deep(.dg-meta) { font-size: 12px; opacity: 0.65; }
</style>
