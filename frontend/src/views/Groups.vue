<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NIcon, NInput, NButton, NTag, NTooltip,
  NModal, NDrawer, NDrawerContent, NForm, NFormItem,
  NPopconfirm, NSelect, NList, NListItem, NThing,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  listGroups, createGroup, updateGroup, deleteGroup,
  listGroupMembers, addGroupMember, removeGroupMember,
  listUsers,
  type Group, type User,
} from "@/api/admin";
import {
  GroupsIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
const { t } = useI18n();

const { visibleKeys: grpVis, setVisible: grpSet, reset: grpReset } = useColumnPrefs(
  "groups",
  ["name", "description", "member_count", "is_builtin", "actions"],
  ["name", "description", "member_count", "is_builtin", "actions"],
);
const grpPicker = [
  { key: "name", label: t("cols.name") },
  { key: "description", label: t("cols.description") },
  { key: "member_count", label: t("cols.member_count") },
  { key: "is_builtin", label: t("cols.builtin") },
  { key: "actions", label: t("cols.actions") },
];

const msg = useMessage();
const rows = ref<Group[]>([]);
const total = ref(0);
const loading = ref(false);

const showEdit = ref(false);
const editing = ref<Group | null>(null);
const form = ref({ name: "", description: "" });

const showMembers = ref(false);
const membersGroup = ref<Group | null>(null);
const members = ref<User[]>([]);
const membersLoading = ref(false);
const allUsers = ref<User[]>([]);
const memberToAdd = ref<string | null>(null);

async function refresh() {
  loading.value = true;
  try {
    const res = await listGroups(200, 0);
    rows.value = res.items;
    total.value = res.total;
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}

function openCreate() {
  editing.value = null;
  form.value = { name: "", description: "" };
  showEdit.value = true;
}
function openEdit(r: Group) {
  editing.value = r;
  form.value = { name: r.name, description: r.description ?? "" };
  showEdit.value = true;
}
async function submit() {
  if (!form.value.name.trim() && !editing.value) return;
  try {
    if (editing.value) {
      await updateGroup(editing.value.id, form.value.description);
    } else {
      await createGroup(form.value.name.trim(), form.value.description || undefined);
    }
    showEdit.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function del(g: Group) {
  try { await deleteGroup(g.id); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

async function openMembers(g: Group) {
  membersGroup.value = g;
  showMembers.value = true;
  await loadMembers();
  if (allUsers.value.length === 0) {
    try { allUsers.value = (await listUsers("", "", 500, 0)).items; }
    catch { msg.error(t("errors.network")); }
  }
}
async function loadMembers() {
  if (!membersGroup.value) return;
  membersLoading.value = true;
  try { members.value = await listGroupMembers(membersGroup.value.id); }
  catch { msg.error(t("errors.network")); }
  finally { membersLoading.value = false; }
}
async function add() {
  if (!membersGroup.value || !memberToAdd.value) return;
  try {
    await addGroupMember(membersGroup.value.id, memberToAdd.value);
    memberToAdd.value = null;
    await loadMembers();
    await refresh();   // member_count 會變
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function removeMember(u: User) {
  if (!membersGroup.value) return;
  try {
    await removeGroupMember(membersGroup.value.id, u.id);
    await loadMembers();
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

const userOptions = computed(() => {
  const memberIds = new Set(members.value.map((m) => m.id));
  return allUsers.value
    .filter((u) => !memberIds.has(u.id))
    .map((u) => ({ label: `${u.username} (${u.email})`, value: u.id }));
});

function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}
const allColumns = computed<DataTableColumns<Group>>(() => autoSort([
  { title: t("groups.name"), key: "name", minWidth: 180, ellipsis: { tooltip: true } },
  { title: t("groups.description"), key: "description", minWidth: 220, ellipsis: { tooltip: true }, render: (r) => r.description ?? "—" },
  {
    title: t("groups.members"), key: "member_count", width: 110,
    render: (r) => h(NButton, { size: "small", text: true, onClick: () => openMembers(r) },
      () => `${r.member_count} →`),
  },
  {
    title: t("groups.is_builtin"), key: "is_builtin", width: 110,
    render: (r) => r.is_builtin ? h(NTag, { size: "small", type: "info" }, () => "built-in") : "—",
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 96,
    render: (r) => r.is_builtin
      ? h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
          iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
        ])
      : h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
          iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
          h(NPopconfirm, { onPositiveClick: () => del(r) }, {
            trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
            default: () => t("common.confirm_delete"),
          }),
        ]),
  },
]));

const columns = computed<DataTableColumns<Group>>(() =>
  allColumns.value.filter((c: any) => grpVis.value.includes(c.key)),
);

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><GroupsIcon /></n-icon>
        <span>{{ t("groups.title") }}</span>
      </n-space>
    </template>
    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("common.create") }}
      </n-button>
      <ColumnPicker :all="grpPicker" :visible="grpVis"
                    @update:visible="grpSet" @reset="grpReset" />
      <span style="opacity: 0.6">{{ t("common.total_n", { n: total }) }}</span>
    </n-space>
    <n-data-table :columns="columns" :data="rows" :loading="loading" :bordered="false" :scroll-x="716">
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>

    <n-modal v-model:show="showEdit" preset="card" style="width: 460px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><component :is="editing ? EditIcon : PlusIcon" /></n-icon>
          <span>{{ editing ? t("common.edit") : t("common.create") }}</span>
        </n-space>
      </template>
      <n-form>
        <n-form-item :label="t('groups.name')">
          <n-input v-model:value="form.name" :disabled="!!editing" />
        </n-form-item>
        <n-form-item :label="t('groups.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="3" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showEdit = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>

    <n-drawer v-model:show="showMembers" :width="480">
      <n-drawer-content :title="`${t('groups.members')} — ${membersGroup?.name ?? ''}`">
        <n-space style="margin-bottom: 16px">
          <n-select
            v-model:value="memberToAdd" :options="userOptions" filterable
            :placeholder="t('groups.add_member_placeholder')" style="width: 280px"
          />
          <n-button type="primary" :disabled="!memberToAdd" @click="add">
            {{ t("common.create") }}
          </n-button>
        </n-space>
        <n-list bordered>
          <template v-if="!membersLoading && members.length === 0">
            <n-list-item>
              <n-thing>
                <template #header>{{ t("common.no_data") }}</template>
              </n-thing>
            </n-list-item>
          </template>
          <n-list-item v-for="u in members" :key="u.id">
            <n-thing>
              <template #header>{{ u.username }}</template>
              <template #description>{{ u.email }} · {{ u.auth_provider }}</template>
            </n-thing>
            <template #suffix>
              <n-popconfirm @positive-click="removeMember(u)">
                <template #trigger>
                  <n-button size="small" type="error">{{ t("common.delete") }}</n-button>
                </template>
                {{ t("common.confirm_delete") }}
              </n-popconfirm>
            </template>
          </n-list-item>
        </n-list>
      </n-drawer-content>
    </n-drawer>
  </n-card>
</template>
