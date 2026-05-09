<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NDataTable,
  NSpace,
  NInput,
  NSelect,
  NButton,
  NSwitch,
  NTag,
  NModal,
  NForm,
  NFormItem,
  NPopconfirm,
  useMessage,
  type DataTableColumns,
} from "naive-ui";
import {
  listUsers, createUser, updateUser, deleteUser,
  type User, type UserCreate,
} from "@/api/admin";

const { t } = useI18n();
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

const providerOptions = [
  { label: "All", value: "" },
  { label: "local", value: "local" },
  { label: "ldap", value: "ldap" },
  { label: "radius", value: "radius" },
  { label: "oidc", value: "oidc" },
  { label: "saml", value: "saml" },
];

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
  if (!newUser.value.username || !newUser.value.email || newUser.value.password.length < 12) {
    msg.error(t("login.failed"));
    return;
  }
  try {
    await createUser(newUser.value);
    msg.success(t("common.ok"));
    showCreate.value = false;
    newUser.value = {
      username: "", email: "", display_name: "", password: "", is_admin: false,
    };
    await refresh();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  }
}

async function toggleActive(u: User) {
  try {
    await updateUser(u.id, { is_active: !u.is_active });
    await refresh();
  } catch {
    msg.error(t("errors.server"));
  }
}

async function toggleAdmin(u: User) {
  try {
    await updateUser(u.id, { is_admin: !u.is_admin });
    await refresh();
  } catch {
    msg.error(t("errors.server"));
  }
}

async function unlock(u: User) {
  try {
    await updateUser(u.id, { unlock: true });
    msg.success(t("common.ok"));
    await refresh();
  } catch {
    msg.error(t("errors.server"));
  }
}

async function remove(u: User) {
  try {
    await deleteUser(u.id);
    msg.success(t("common.ok"));
    await refresh();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  }
}

const columns = computed<DataTableColumns<User>>(() => [
  { title: t("users.username"), key: "username" },
  { title: t("users.email"), key: "email" },
  { title: t("users.display_name"), key: "display_name", render: (r) => r.display_name ?? "—" },
  {
    title: t("users.auth_provider"), key: "auth_provider",
    render: (r) => h(NTag, { size: "small", type: "info" }, () => r.auth_provider),
  },
  {
    title: t("users.is_active"), key: "is_active",
    render: (r) => h(NSwitch, {
      value: r.is_active,
      "onUpdate:value": () => toggleActive(r),
      size: "small",
    }),
  },
  {
    title: t("users.is_admin"), key: "is_admin",
    render: (r) => h(NSwitch, {
      value: r.is_admin,
      "onUpdate:value": () => toggleAdmin(r),
      size: "small",
    }),
  },
  {
    title: t("users.last_login"), key: "last_login_at",
    render: (r) => r.last_login_at ? new Date(r.last_login_at).toLocaleString() : "—",
  },
  {
    title: t("users.locked_until"), key: "locked_until",
    render: (r) => r.locked_until
      ? h(NTag, { type: "error", size: "small" },
          () => new Date(r.locked_until!).toLocaleString())
      : "—",
  },
  {
    title: t("common.actions"), key: "actions", width: 220,
    render: (r) => h(NSpace, { size: "small" }, () => [
      r.locked_until
        ? h(NButton, { size: "small", onClick: () => unlock(r) }, () => t("users.unlock"))
        : null,
      h(NPopconfirm, {
        onPositiveClick: () => remove(r),
      }, {
        trigger: () => h(NButton, { size: "small", type: "error" }, () => t("common.delete")),
        default: () => t("common.confirm_delete"),
      }),
    ]),
  },
]);

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="t('users.title')">
    <n-space style="margin-bottom: 12px" align="center">
      <n-input v-model:value="q" :placeholder="t('common.search')" style="width: 240px"
               @keyup.enter="refresh" clearable />
      <n-select v-model:value="providerFilter" :options="providerOptions"
                style="width: 140px" />
      <n-button @click="refresh" :loading="loading">{{ t("common.refresh") }}</n-button>
      <n-button type="primary" @click="showCreate = true">{{ t("users.create_user") }}</n-button>
      <span style="opacity: 0.6">total: {{ total }}</span>
    </n-space>

    <n-data-table
      :columns="columns" :data="rows" :loading="loading"
      :pagination="{
        page: Math.floor(offset / limit) + 1,
        pageSize: limit,
        itemCount: total,
        onUpdatePage: (p) => { offset = (p - 1) * limit; void refresh(); },
      }"
      remote :bordered="false"
    >
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>

    <n-modal v-model:show="showCreate" preset="card" :title="t('users.create_user')"
             style="width: 460px">
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
        </n-form-item>
        <n-form-item :label="t('users.is_admin')">
          <n-switch v-model:value="newUser.is_admin" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showCreate = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submitCreate">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
