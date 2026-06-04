<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NDataTable,
  NSpace,
  NIcon,
  NInput,
  NSelect,
  NButton,
  NSwitch,
  NTag,
  NModal,
  NForm,
  NFormItem,
  NPopconfirm,
  NTooltip,
  useMessage,
  type DataTableColumns,
} from "naive-ui";
import {
  listUsers, createUser, updateUser, deleteUser,
  type User, type UserCreate,
} from "@/api/admin";
import {
  UsersIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SaveIcon, CancelIcon, TokenIcon, AdminIcon,
} from "@/icons";
import { useRouter } from "vue-router";
import { autoSort } from "@/composables/useTableSort";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
const { t } = useI18n();
const router = useRouter();
function goPerms(r: User) { router.push({ name: "permissions", query: { ptype: "user", pid: r.id } }); }

const { visibleKeys: usrVis, setVisible: usrSet, reset: usrReset } = useColumnPrefs(
  "users",
  ["username", "email", "display_name", "auth_provider", "is_active", "is_admin", "last_login_at", "locked_until", "actions"],
  ["username", "email", "display_name", "auth_provider", "is_active", "is_admin", "last_login_at", "locked_until", "actions"],
);
const usrPicker = [
  { key: "username", label: t("cols.username") },
  { key: "email", label: "Email" },
  { key: "display_name", label: t("cols.display_name") },
  { key: "auth_provider", label: t("cols.auth_method") },
  { key: "is_active", label: t("cols.enabled") },
  { key: "is_admin", label: t("cols.admin") },
  { key: "last_login_at", label: t("cols.last_login") },
  { key: "locked_until", label: t("cols.locked_until") },
  { key: "actions", label: t("cols.actions") },
];

const msg = useMessage();

const rows = ref<User[]>([]);
const total = ref(0);
const loading = ref(false);
const q = ref("");
const providerFilter = ref<string | null>(null);
const limit = ref(50);
const offset = ref(0);

const showCreate = ref(false);
const newUser = ref<UserCreate>({
  username: "", email: "", display_name: "", password: "", is_admin: false,
});
const newUserPasswordConfirm = ref("");

function passwordScore(p: string): number {
  let s = 0;
  if (p.length >= 12) s++;
  if (p.length >= 16) s++;
  if (/[a-z]/.test(p) && /[A-Z]/.test(p)) s++;
  if (/\d/.test(p)) s++;
  if (/[^A-Za-z0-9]/.test(p)) s++;
  return s;
}
const newPasswordStrength = computed(() => {
  const p = newUser.value.password;
  if (!p) return { score: 0, label: "", color: "default" as const };
  const s = passwordScore(p);
  if (s <= 2) return { score: s, label: t("users.pw_weak"), color: "error" as const };
  if (s === 3) return { score: s, label: t("users.pw_medium"), color: "warning" as const };
  return { score: s, label: t("users.pw_strong"), color: "success" as const };
});

const showEdit = ref(false);
const editing = ref<User | null>(null);
const editForm = ref({
  email: "", display_name: "", password: "",
});

const providerOptions = [
  { label: "All", value: "" },
  { label: "local", value: "local" },
  { label: "ldap", value: "ldap" },
  { label: "radius", value: "radius" },
  { label: "oidc", value: "oidc" },
  { label: "saml", value: "saml" },
];

// 匯出全部：用相同篩選分頁抓完整資料集
async function fetchAllForExport(): Promise<User[]> {
  const all: User[] = [];
  const big = 500;   // 後端 limit 上限
  let off = 0;
  for (;;) {
    const res = await listUsers(q.value, providerFilter.value || "", big, off);
    all.push(...res.items);
    if (res.items.length === 0 || all.length >= res.total) break;
    off += big;
  }
  return all;
}

async function refresh() {
  loading.value = true;
  try {
    const res = await listUsers(
      q.value, providerFilter.value || "", limit.value, offset.value,
    );
    rows.value = res.items;
    total.value = res.total;
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

async function submitCreate() {
  if (!newUser.value.username.trim()) {
    msg.error(t("users.error_username_required"));
    return;
  }
  if (!newUser.value.email.trim()) {
    msg.error(t("users.error_email_required"));
    return;
  }
  if (newUser.value.password.length < 12) {
    msg.error(t("users.error_password_too_short"));
    return;
  }
  if (newUser.value.password !== newUserPasswordConfirm.value) {
    msg.error(t("users.error_password_mismatch"));
    return;
  }
  if (passwordScore(newUser.value.password) <= 2) {
    msg.error(t("users.error_password_weak"));
    return;
  }
  try {
    await createUser(newUser.value);
    msg.success(t("common.ok"));
    showCreate.value = false;
    newUser.value = {
      username: "", email: "", display_name: "", password: "", is_admin: false,
    };
    newUserPasswordConfirm.value = "";
    await refresh();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  }
}

function openEdit(u: User) {
  editing.value = u;
  editForm.value = {
    email: u.email,
    display_name: u.display_name ?? "",
    password: "",
  };
  showEdit.value = true;
}
async function submitEdit() {
  if (!editing.value) return;
  try {
    const payload: any = {
      email: editForm.value.email,
      display_name: editForm.value.display_name || undefined,
    };
    if (editForm.value.password) payload.password = editForm.value.password;
    await updateUser(editing.value.id, payload);
    showEdit.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  }
}

async function toggleActive(u: User) {
  try { await updateUser(u.id, { is_active: !u.is_active }); await refresh(); }
  catch { msg.error(t("errors.server")); }
}
async function toggleAdmin(u: User) {
  try { await updateUser(u.id, { is_admin: !u.is_admin }); await refresh(); }
  catch { msg.error(t("errors.server")); }
}
async function unlock(u: User) {
  try { await updateUser(u.id, { unlock: true }); msg.success(t("common.ok")); await refresh(); }
  catch { msg.error(t("errors.server")); }
}
async function remove(u: User) {
  try { await deleteUser(u.id); msg.success(t("common.ok")); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}

function iconAction(icon: any, label: string, onClick: () => void, type?: any) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon)) }),
    default: () => label,
  });
}
const allColumns = computed<DataTableColumns<User>>(() => autoSort([
  { title: t("users.username"), key: "username", minWidth: 160, ellipsis: { tooltip: true } },
  { title: t("users.email"), key: "email", minWidth: 150, ellipsis: { tooltip: true } },
  { title: t("users.display_name"), key: "display_name", minWidth: 120, ellipsis: { tooltip: true }, render: (r) => r.display_name ?? "—" },
  {
    title: t("users.auth_provider"), key: "auth_provider", width: 96,
    render: (r) => h(NTag, { size: "small", type: "info" }, () => r.auth_provider),
  },
  {
    title: t("users.is_active"), key: "is_active", width: 90,
    render: (r) => h(NSwitch, {
      value: r.is_active,
      "onUpdate:value": () => toggleActive(r),
      size: "small",
    }),
  },
  {
    title: t("users.is_admin"), key: "is_admin", width: 90,
    render: (r) => h(NSwitch, {
      value: r.is_admin,
      "onUpdate:value": () => toggleAdmin(r),
      size: "small",
    }),
  },
  {
    title: t("users.last_login"), key: "last_login_at", width: 158,
    render: (r) => fmtDateTime(r.last_login_at),
  },
  {
    title: t("users.locked_until"), key: "locked_until", width: 120,
    render: (r) => r.locked_until
      ? h(NTag, { type: "error", size: "small" },
          () => fmtDateTime(r.locked_until!))
      : "—",
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 150,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      iconAction(AdminIcon, t("users.assign_perms"), () => goPerms(r)),
      r.locked_until
        ? iconAction(TokenIcon, t("users.unlock"), () => unlock(r))
        : null,
      h(NPopconfirm, { onPositiveClick: () => remove(r) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]));

const columns = computed<DataTableColumns<User>>(() =>
  allColumns.value.filter((c: any) => usrVis.value.includes(c.key)),
);

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><UsersIcon /></n-icon>
        <span>{{ t("users.title") }}</span>
      </n-space>
    </template>
    <n-space style="margin-bottom: 12px" align="center">
      <n-input v-model:value="q" :placeholder="t('common.search')" style="width: 240px"
               @keyup.enter="refresh" clearable />
      <n-select v-model:value="providerFilter" :options="providerOptions"
                style="width: 140px" />
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" @click="showCreate = true">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("users.create_user") }}
      </n-button>
      <ColumnPicker :all="usrPicker" :visible="usrVis"
                    @update:visible="usrSet" @reset="usrReset" />
      <ExportButton :columns="columns" :rows="rows" :fetch-all="fetchAllForExport"
                    filename="users" :title="t('users.title')" />
      <span style="opacity: 0.6">{{ t("common.total_n", { n: total }) }}</span>
    </n-space>

    <n-data-table
      :columns="columns" :data="rows" :loading="loading"
      :pagination="{
        page: Math.floor(offset / limit) + 1,
        pageSize: limit,
        itemCount: total,
        onUpdatePage: (p) => { offset = (p - 1) * limit; void refresh(); },
      }"
      remote :bordered="false" :scroll-x="1084"
    >
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>

    <n-modal v-model:show="showCreate" preset="card" style="width: 460px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><PlusIcon /></n-icon>
          <span>{{ t("users.create_user") }}</span>
        </n-space>
      </template>
      <n-form>
        <n-form-item :label="t('users.username')">
          <n-input v-model:value="newUser.username" />
        </n-form-item>
        <n-form-item :label="t('users.email')">
          <n-input v-model:value="newUser.email" />
        </n-form-item>
        <n-form-item :label="t('users.display_name')">
          <n-input v-model:value="newUser.display_name" />
        </n-form-item>
        <n-form-item :label="t('users.password')">
          <n-input v-model:value="newUser.password" type="password" show-password-on="click" />
          <template #feedback>
            <n-space :size="6" align="center">
              <span style="opacity: 0.7">{{ t("users.password_hint") }}</span>
              <n-tag v-if="newPasswordStrength.label" :type="newPasswordStrength.color" size="small">
                {{ newPasswordStrength.label }}
              </n-tag>
            </n-space>
          </template>
        </n-form-item>
        <n-form-item :label="t('users.password_confirm')">
          <n-input v-model:value="newUserPasswordConfirm" type="password" show-password-on="click" />
          <template #feedback v-if="newUserPasswordConfirm && newUser.password !== newUserPasswordConfirm">
            <span style="color: var(--n-error-color, #d03050)">
              {{ t("users.error_password_mismatch") }}
            </span>
          </template>
        </n-form-item>
        <n-form-item :label="t('users.is_admin')">
          <n-switch v-model:value="newUser.is_admin" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showCreate = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="submitCreate">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>

    <n-modal v-model:show="showEdit" preset="card" style="width: 460px">
      <template #header>
        <n-space align="center">
          <n-icon :size="20"><EditIcon /></n-icon>
          <span>{{ editing ? `${t("common.edit")} ${editing.username}` : "" }}</span>
        </n-space>
      </template>
      <n-form>
        <n-form-item :label="t('users.email')">
          <n-input v-model:value="editForm.email" />
        </n-form-item>
        <n-form-item :label="t('users.display_name')">
          <n-input v-model:value="editForm.display_name" />
        </n-form-item>
        <n-form-item v-if="editing?.auth_provider === 'local'"
                     :label="`${t('users.password')} (${t('users.password_optional')})`">
          <n-input v-model:value="editForm.password" type="password"
                   show-password-on="click" :placeholder="t('users.password_blank_unchanged')" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showEdit = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="submitEdit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
