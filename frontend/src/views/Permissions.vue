<script setup lang="ts">
/**
 * 權限指派（admin）— 以「使用者」為中心，簡化流程：
 *   1. 列出使用者 → 點一位看細節 / 編輯
 *   2. 指派「角色」（= 群組，內建幾個預設角色，每個角色由細部物件授權組成）
 *   3. 進階：仍可對單一使用者直接做物件層級授權（保留舊的細部編輯，收進折疊區）
 * 角色管理（成員、新增角色）在「群組」頁；階層繼承由後端處理。
 */
import { computed, h, onMounted, ref } from "vue";
import {
  NButton, NCard, NSpace, NSelect, NIcon, NTag, NInput, NSwitch, NDrawer, NDrawerContent,
  NTooltip,
  NDataTable, NEmpty, NCollapse, NCollapseItem, NList, NListItem, NThing, useMessage,
  type DataTableColumns,
} from "naive-ui";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import { apiClient, apiErrMsg } from "@/api/client";
import {
  listUsers, listGroups, getUserGroups, addGroupMember, removeGroupMember,
  type User, type Group,
} from "@/api/admin";
import {
  listPermissions, upsertPermission, deletePermission, listRoles, getPermissionSummary,
  type PermissionGrant, type PermObjectType, type PermLevel,
} from "@/api/permissions";
import { UsersIcon, DeleteIcon, PlusIcon, AdminIcon, EditIcon } from "@/icons";
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
import { useTablePagination } from "@/composables/useTablePagination";
const pg = useTablePagination();
const grantPg = useTablePagination();

const { t } = useI18n();
const msg = useMessage();
const route = useRoute();

const users = ref<User[]>([]);
/** 帳號 id → 角色名稱與直接物件授權數（清單的「權限」欄） */
const summary = ref<Record<string, { roles: string[]; grants: number }>>({});
async function loadSummary() {
  try { summary.value = await getPermissionSummary(); } catch { /* 清單照常顯示，只是少了這一欄 */ }
}
const roles = ref<Group[]>([]);
const loading = ref(false);
const { query: filterQ, filtered: filteredUsers } = useTableQuickFilter(users);

const objectTypes = ref<PermObjectType[]>([]);
const levels = ref<PermLevel[]>(["read", "write", "admin"]);

const TYPE_CFG: Record<string, { ep: string; label: string }> = {
  customer: { ep: "/api/v1/customers", label: "name" },
  section:  { ep: "/api/v1/sections", label: "name" },
  subnet:   { ep: "/api/v1/subnets", label: "cidr" },
  device:   { ep: "/api/v1/devices", label: "name" },
  // 機櫃／地點的端點是 /api/v1/racks 與 /api/v1/locations —— 雖然兩者實作在後端
  // endpoints/locations.py 同一個檔案，但那個 router 沒有 /locations 前綴。
  // 原本誤寫成 /api/v1/locations/racks 與 /api/v1/locations/locations，會被
  // /locations/{location_id} 接走、UUID 解析失敗回 400 → 清單載不出來，
  // 結果是「無法對機櫃／地點授權」。
  rack:     { ep: "/api/v1/racks", label: "name" },
  location: { ep: "/api/v1/locations", label: "name" },
};
const labelMap = ref<Record<string, string>>({});
const typeOptions = ref<Record<string, { label: string; value: string }[]>>({});

// 抽屜：選定使用者
const drawer = ref(false);
const sel = ref<User | null>(null);
const userRoleIds = ref<Set<string>>(new Set());
const grants = ref<PermissionGrant[]>([]);
const busyRole = ref<string | null>(null);

// 進階授權表單
const fObjType = ref<PermObjectType>("subnet");
const fAll = ref(true);
const fObjs = ref<string[]>([]);
const fLevel = ref<PermLevel>("read");
const saving = ref(false);

const typeSelOptions = computed(() => objectTypes.value.map((tt) => ({ label: t(`perm.type_${tt}`), value: tt })));
const levelSelOptions = computed(() => levels.value.map((l) => ({ label: t(`perm.level_${l}`), value: l })));
const fObjOptions = computed(() => typeOptions.value[fObjType.value] ?? []);
const fSpecificDisabled = computed(() => fObjType.value === "ip");

async function loadLists() {
  loading.value = true;
  try {
    const [us, gs, r] = await Promise.all([
      listUsers("", "", 500, 0),
      listGroups(500, 0),
      listRoles(),
    ]);
    users.value = us.items;
    void loadSummary();
    roles.value = gs.items;
    objectTypes.value = r.object_types;
    levels.value = r.levels;
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
  for (const [tt, cfg] of Object.entries(TYPE_CFG)) {
    try {
      const { data } = await apiClient.get(cfg.ep, { params: { page: 1, page_size: 500 } });
      const items = data.items ?? data ?? [];
      typeOptions.value[tt] = items.map((it: any) => ({ label: String(it[cfg.label] ?? it.id), value: it.id }));
      for (const it of items) labelMap.value[it.id] = String(it[cfg.label] ?? it.id);
    } catch { typeOptions.value[tt] = []; }
  }
}

async function openUser(u: User) {
  sel.value = u;
  drawer.value = true;
  userRoleIds.value = new Set();
  grants.value = [];
  try {
    const [gs, gr] = await Promise.all([
      getUserGroups(u.id),
      listPermissions("user", u.id),
    ]);
    userRoleIds.value = new Set(gs.map((g) => g.id));
    grants.value = gr;
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function toggleRole(role: Group, on: boolean) {
  if (!sel.value) return;
  busyRole.value = role.id;
  try {
    if (on) await addGroupMember(role.id, sel.value.id);
    else await removeGroupMember(role.id, sel.value.id);
    const next = new Set(userRoleIds.value);
    if (on) next.add(role.id); else next.delete(role.id);
    userRoleIds.value = next;
    void loadSummary();
    msg.success(t("common.ok"));
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { busyRole.value = null; }
}

async function addGrant() {
  if (!sel.value) return;
  saving.value = true;
  try {
    const targets: (string | null)[] = fAll.value ? [null] : fObjs.value;
    if (!fAll.value && targets.length === 0) { msg.warning(t("perm.pick_objects")); return; }
    for (const oid of targets) {
      await upsertPermission({
        object_type: fObjType.value, object_id: oid,
        principal_type: "user", principal_id: sel.value.id, level: fLevel.value,
      });
    }
    msg.success(t("common.saved"));
    fObjs.value = [];
    grants.value = await listPermissions("user", sel.value.id);
    void loadSummary();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("perm.save_failed")); }
  finally { saving.value = false; }
}
async function removeGrant(id: string) {
  if (!sel.value) return;
  try { await deletePermission(id); grants.value = await listPermissions("user", sel.value.id); void loadSummary(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}
function targetLabel(g: PermissionGrant): string {
  if (g.object_id === null) return t("perm.all");
  return labelMap.value[g.object_id] ?? g.object_id.slice(0, 8);
}

const userCols = computed<DataTableColumns<User>>(() => [
  { title: t("cols.username"), key: "username", minWidth: 140, ellipsis: { tooltip: true },
    render: (u) => u.display_name ? `${u.username} (${u.display_name})` : u.username },
  { title: "Email", key: "email", minWidth: 160, ellipsis: { tooltip: true } },
  { title: t("cols.admin"), key: "is_admin", width: 90,
    render: (u) => u.is_admin ? h(NTag, { size: "small", type: "error" }, () => t("perm.superuser")) : "—" },
  // 權限：掛了哪些角色＋直接的物件授權幾筆（使用者 10-22：不用一個一個點開才知道）
  { title: t("perm.col_summary"), key: "summary", minWidth: 220,
    render: (u) => {
      const s = summary.value[u.id];
      if (!s || (!s.roles.length && !s.grants)) return h("span", { style: "opacity:.45" }, t("perm.none"));
      return h(NSpace, { size: 4, wrap: true }, () => [
        ...s.roles.map((r) => h(NTag, { size: "small", type: "info", bordered: false }, () => r)),
        ...(s.grants ? [h(NTag, { size: "small", bordered: false }, () => t("perm.object_grants_n", { n: s.grants }))] : []),
      ]);
    } },
  // 與使用者頁一致：icon 按鈕 + tooltip（文字按鈕在窄視窗會被欄寬切掉，且全站只有這裡是文字）
  { title: t("common.actions"), key: "actions", width: 72, align: "center",
    className: "col-actions",
    render: (u) => h(NTooltip, null, {
      trigger: () => h(NButton, { size: "small", quaternary: true,
        onClick: (e: MouseEvent) => { e.stopPropagation(); openUser(u); } },
        { icon: () => h(NIcon, null, () => h(EditIcon)) }),
      default: () => t("common.edit"),
    }) },
]);

const grantCols = computed<DataTableColumns<PermissionGrant>>(() => [
  { title: t("perm.col_type"), key: "object_type", width: 100, render: (r) => t(`perm.type_${r.object_type}`) },
  { title: t("perm.col_target"), key: "object_id", minWidth: 140,
    render: (r) => r.object_id === null ? h(NTag, { size: "small", type: "info" }, () => t("perm.all")) : targetLabel(r) },
  { title: t("perm.col_level"), key: "level", width: 90,
    render: (r) => h(NTag, { size: "small", type: r.level === "admin" ? "error" : r.level === "write" ? "warning" : "success" }, () => t(`perm.level_${r.level}`)) },
  { title: "", key: "actions", width: 50, align: "center", className: "col-actions",
    render: (r) => h(NButton, { size: "small", quaternary: true, type: "error", onClick: () => removeGrant(r.id) },
      { icon: () => h(NIcon, null, () => h(DeleteIcon)) }) },
]);

onMounted(async () => {
  await loadLists();
  const pid = route.query.pid as string | undefined;
  const pt = route.query.ptype as string | undefined;
  if (pid && (!pt || pt === "user")) {
    const u = users.value.find((x) => x.id === pid);
    if (u) await openUser(u);
  }
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false"><n-icon :size="22"><AdminIcon /></n-icon><span>{{ t("perm.title") }}</span></n-space>
    </template>

    <n-space align="center" style="margin-bottom: 12px">
      <n-input v-model:value="filterQ" :placeholder="t('common.search')" clearable style="width: 220px">
        <template #prefix><n-icon><UsersIcon /></n-icon></template>
      </n-input>
      <span style="opacity:.6; font-size:12px">{{ t("perm.users_hint") }}</span>
    </n-space>

    <!-- scroll-x：欄寬總和放不下時要水平捲動，而不是把最後一欄（操作）擠掉看不見 -->
    <n-data-table :columns="userCols" :data="filteredUsers" :loading="loading" :bordered="false"
                  :scroll-x="480"
                  :row-props="(u: User) => ({ style: 'cursor:pointer', onClick: () => openUser(u) })"
                  :pagination="pg" />

    <n-drawer v-model:show="drawer" :width="480" placement="right">
      <n-drawer-content :title="sel ? (sel.display_name || sel.username) : ''" closable>
        <template v-if="sel">
          <n-tag v-if="sel.is_admin" type="error" size="small" style="margin-bottom: 10px">
            {{ t("perm.superuser_note") }}
          </n-tag>

          <div style="font-weight: 600; margin: 4px 0 8px">{{ t("perm.assign_roles") }}</div>
          <n-list bordered>
            <n-list-item v-for="r in roles" :key="r.id">
              <n-thing :description="r.description ?? ''">
                <template #header>
                  <n-space :size="6" align="center" :wrap-item="false">
                    <span>{{ r.name }}</span>
                    <n-tag v-if="r.is_builtin" size="tiny" :bordered="false" type="info">
                      {{ t("perm.builtin") }}
                    </n-tag>
                  </n-space>
                </template>
              </n-thing>
              <template #suffix>
                <n-switch :value="userRoleIds.has(r.id)" :loading="busyRole === r.id"
                          @update:value="(v: boolean) => toggleRole(r, v)" />
              </template>
            </n-list-item>
          </n-list>

          <n-collapse style="margin-top: 16px">
            <n-collapse-item name="adv">
              <!-- 收合時也看得出裡面有設定（使用者 10-22）：標題旁顯示筆數 -->
              <template #header>
                <span>{{ t("perm.advanced_grants") }}</span>
                <n-tag v-if="grants.length" size="small" type="warning" round :bordered="false" style="margin-left: 8px"
                       data-testid="perm-adv-count">{{ grants.length }}</n-tag>
              </template>
              <p style="opacity:.6;font-size:12px;margin:0 0 10px">{{ t("perm.advanced_hint") }}</p>
              <n-space align="center" :wrap="true" style="margin-bottom: 10px">
                <n-select v-model:value="fObjType" :options="typeSelOptions" style="width: 130px" size="small" />
                <n-switch v-model:value="fAll" :disabled="fSpecificDisabled" size="small">
                  <template #checked>{{ t("perm.all") }}</template>
                  <template #unchecked>{{ t("perm.specific") }}</template>
                </n-switch>
                <n-select v-if="!fAll" v-model:value="fObjs" :options="fObjOptions" multiple filterable size="small"
                          :placeholder="t('perm.pick_objects')" style="min-width: 200px" max-tag-count="responsive" />
                <n-select v-model:value="fLevel" :options="levelSelOptions" style="width: 110px" size="small" />
                <n-button type="primary" size="small" :loading="saving" @click="addGrant">
                  <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("common.create") }}
                </n-button>
              </n-space>
              <n-data-table :columns="grantCols" :data="grants" :bordered="false" :scroll-x="380" size="small" :pagination="grantPg" />
              <p style="opacity:.55;font-size:11px;margin:8px 0 0">{{ t("perm.cascade_hint") }}</p>
            </n-collapse-item>
          </n-collapse>
        </template>
        <n-empty v-else :description="t('common.no_data')" />
      </n-drawer-content>
    </n-drawer>
  </n-card>
</template>
