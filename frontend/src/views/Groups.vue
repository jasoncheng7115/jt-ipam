<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NDataTable,
  NSpace,
  NInput,
  NButton,
  NTag,
  NModal,
  NForm,
  NFormItem,
  NPopconfirm,
  useMessage,
  type DataTableColumns,
} from "naive-ui";
import {
  listGroups, createGroup, deleteGroup,
  type Group,
} from "@/api/admin";

const { t } = useI18n();
const msg = useMessage();
const rows = ref<Group[]>([]);
const total = ref(0);
const loading = ref(false);
const showCreate = ref(false);
const newName = ref("");
const newDesc = ref("");

async function refresh() {
  loading.value = true;
  try {
    const res = await listGroups(200, 0);
    rows.value = res.items;
    total.value = res.total;
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

async function submit() {
  if (!newName.value.trim()) return;
  try {
    await createGroup(newName.value.trim(), newDesc.value || undefined);
    showCreate.value = false;
    newName.value = "";
    newDesc.value = "";
    msg.success(t("common.ok"));
    await refresh();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  }
}

async function remove(g: Group) {
  try {
    await deleteGroup(g.id);
    msg.success(t("common.ok"));
    await refresh();
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.server"));
  }
}

const columns = computed<DataTableColumns<Group>>(() => [
  { title: t("groups.name"), key: "name" },
  { title: t("groups.description"), key: "description", render: (r) => r.description ?? "—" },
  { title: t("groups.members"), key: "member_count" },
  {
    title: t("groups.is_builtin"), key: "is_builtin",
    render: (r) => r.is_builtin ? h(NTag, { size: "small", type: "info" }, () => "built-in") : "—",
  },
  {
    title: t("common.actions"), key: "actions", width: 120,
    render: (r) => r.is_builtin ? "—" : h(NPopconfirm, {
      onPositiveClick: () => remove(r),
    }, {
      trigger: () => h(NButton, { size: "small", type: "error" }, () => t("common.delete")),
      default: () => t("common.confirm_delete"),
    }),
  },
]);

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card :title="t('groups.title')">
    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">{{ t("common.refresh") }}</n-button>
      <n-button type="primary" @click="showCreate = true">{{ t("common.create") }}</n-button>
      <span style="opacity: 0.6">total: {{ total }}</span>
    </n-space>
    <n-data-table :columns="columns" :data="rows" :loading="loading" :bordered="false">
      <template #empty>
        <n-space justify="center">{{ t("common.no_data") }}</n-space>
      </template>
    </n-data-table>
    <n-modal v-model:show="showCreate" preset="card" :title="t('groups.title')"
             style="width: 420px">
      <n-form>
        <n-form-item :label="t('groups.name')">
          <n-input v-model:value="newName" />
        </n-form-item>
        <n-form-item :label="t('groups.description')">
          <n-input v-model:value="newDesc" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showCreate = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submit">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
