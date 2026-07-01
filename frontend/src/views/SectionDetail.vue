<script setup lang="ts">
import { h, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NIcon, NButton, NDescriptions, NDescriptionsItem,
  NProgress, NDataTable, NSpin, NModal, NForm, NFormItem, NInput, NSelect,
  NCheckbox, NInputNumber,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { SectionsIcon, SubnetsIcon, RefreshIcon, EditIcon, SaveIcon, CancelIcon } from "@/icons";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { apiClient } from "@/api/client";
import { listSubnets, getSubnetUsage } from "@/api/subnets";
import { updateSection } from "@/api/sections";
import type { Section, Subnet, SubnetUsage } from "@/types";
import { autoSort } from "@/composables/useTableSort";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useCustomers } from "@/composables/useCustomers";
import ColumnPicker from "@/components/ColumnPicker.vue";
import { computed } from "vue";
import { useTablePagination } from "@/composables/useTablePagination";
const pg = useTablePagination();
const { t } = useI18n();

const { visibleKeys: snVis, setVisible: snSet, reset: snReset } = useColumnPrefs(
  "section_detail_subnets",
  ["cidr", "description", "usage"],
  ["cidr", "description", "usage"],
);
const snPicker = computed(() => [
  { key: "cidr", label: "CIDR" },
  { key: "description", label: t("cols.description") },
  { key: "usage", label: t("cols.usage") },
]);

const route = useRoute();
const router = useRouter();
const links = useEntityLinks(router);
const msg = useMessage();

const section = ref<Section | null>(null);
const subnets = ref<Subnet[]>([]);
const usageMap = ref<Record<string, SubnetUsage>>({});
const loading = ref(false);

const { options: customerOptions, ensureLoaded: ensureCustomerOptsLoaded } = useCustomers();
const showEdit = ref(false);
const saving = ref(false);
const form = ref({
  name: "",
  description: "" as string | null,
  strict_mode: false,
  display_order: 0 as number | null,
  customer_id: null as string | null,
});

function openEdit() {
  if (!section.value) return;
  void ensureCustomerOptsLoaded();
  form.value = {
    name: section.value.name,
    description: section.value.description ?? "",
    strict_mode: section.value.strict_mode,
    display_order: section.value.display_order,
    customer_id: section.value.customer_id ?? null,
  };
  showEdit.value = true;
}

async function saveEdit() {
  if (!section.value) return;
  saving.value = true;
  try {
    await updateSection(section.value.id, {
      name: form.value.name,
      description: form.value.description || null,
      strict_mode: form.value.strict_mode,
      display_order: form.value.display_order ?? 0,
      customer_id: form.value.customer_id ?? null,
    });
    showEdit.value = false;
    msg.success(t("common.saved"));
    await load(section.value.id);
  } catch {
    msg.error(t("errors.network"));
  } finally {
    saving.value = false;
  }
}

async function load(id: string) {
  loading.value = true;
  try {
    const [sec, subs] = await Promise.all([
      apiClient.get<Section>(`/api/v1/sections/${id}`).then((r) => r.data),
      listSubnets({ sectionId: id, page: 1, pageSize: 500 }),
    ]);
    section.value = sec;
    subnets.value = subs.items;
    const usages = await Promise.all(
      subs.items.map(async (s) => {
        try { return await getSubnetUsage(s.id); } catch { return null; }
      }),
    );
    const map: Record<string, SubnetUsage> = {};
    usages.forEach((u) => { if (u) map[u.subnet_id] = u; });
    usageMap.value = map;
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

const allColumns: DataTableColumns<Subnet> = autoSort([
  { title: () => t("subnets.cidr"), key: "cidr", render: (r) => links.subnet(r.id, r.cidr) },
  {
    title: () => t("common.description"),
    key: "description",
    render: (r) => r.description ?? "",
  },
  {
    title: () => t("subnets.usage"),
    key: "usage",
    render: (r) => {
      const u = usageMap.value[r.id];
      if (!u) return "—";
      const status = u.used_pct >= 90 ? "error" : u.used_pct >= 75 ? "warning" : "success";
      return h(NProgress, {
        type: "line",
        percentage: u.used_pct,
        status,
        showIndicator: true,
      });
    },
  },
]);

const columns = computed<DataTableColumns<Subnet>>(() =>
  allColumns.filter((c: any) => snVis.value.includes(c.key)),
);

watch(() => route.params.id, (id) => {
  if (typeof id === "string") void load(id);
});

onMounted(() => {
  const id = route.params.id;
  if (typeof id === "string") void load(id);
});
</script>

<template>
  <n-spin :show="loading">
    <n-space vertical :size="16">
      <n-card v-if="section">
        <template #header>
          <n-space align="center" :wrap-item="false">
            <n-icon :size="22"><SectionsIcon /></n-icon>
            <span>{{ section.name }}</span>
          </n-space>
        </template>
        <template #header-extra>
          <n-space :size="8">
            <n-button type="primary" size="small" @click="openEdit">
              <template #icon><n-icon><EditIcon /></n-icon></template>
              {{ t("common.edit") }}
            </n-button>
            <n-button @click="router.push({ name: 'sections' })" size="small">
              <template #icon><n-icon><ArrowLeftIcon /></n-icon></template>
              {{ t("common.back") }}
            </n-button>
          </n-space>
        </template>
        <n-descriptions bordered :column="2" size="small">
          <n-descriptions-item :label="t('common.name')">{{ section.name }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.subnet_count')">{{ section.subnet_count ?? 0 }}</n-descriptions-item>
          <n-descriptions-item :label="t('sections.strict_mode')">{{ section.strict_mode ? "✓" : "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('custom_fields.display_order')">{{ section.display_order }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.description')" :span="2">
            {{ section.description ?? "—" }}
          </n-descriptions-item>
        </n-descriptions>
      </n-card>

      <n-card>
        <template #header>
          <n-space align="center" :wrap-item="false">
            <n-icon :size="20"><SubnetsIcon /></n-icon>
            <span>{{ t("nav.subnets") }} ({{ subnets.length }})</span>
          </n-space>
        </template>
        <template #header-extra>
          <n-space>
            <ColumnPicker :all="snPicker" :visible="snVis"
                          @update:visible="snSet" @reset="snReset" />
            <n-button
              v-if="section"
              @click="load(section.id)"
              :loading="loading"
              size="small"
            >
              <template #icon><n-icon><RefreshIcon /></n-icon></template>
              {{ t("common.refresh") }}
            </n-button>
          </n-space>
        </template>
        <n-data-table
          :columns="columns"
          :data="subnets"
          :loading="loading"
          :pagination="pg"
          :bordered="false"
          :row-props="(row: Subnet) => ({
            style: 'cursor: pointer',
            onClick: () => router.push({ name: 'subnet-detail', params: { id: row.id } }),
          })"
        >
          <template #empty>
            <n-space justify="center">{{ t("common.no_data") }}</n-space>
          </template>
        </n-data-table>
      </n-card>
    </n-space>

    <n-modal v-model:show="showEdit" preset="card" style="width: 520px" :title="t('common.edit')">
      <n-form label-placement="top">
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="form.name" />
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :autosize="{ minRows: 2 }" />
        </n-form-item>
        <n-form-item :label="t('nav.customers')">
          <n-select v-model:value="form.customer_id" :options="customerOptions" clearable filterable />
        </n-form-item>
        <n-space :size="24">
          <n-form-item :label="t('custom_fields.display_order')">
            <n-input-number v-model:value="form.display_order" :min="0" style="width: 140px" />
          </n-form-item>
          <n-form-item :label="t('sections.strict_mode')">
            <n-checkbox v-model:checked="form.strict_mode" />
          </n-form-item>
        </n-space>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button size="small" @click="showEdit = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>
            {{ t("common.cancel") }}
          </n-button>
          <n-button type="primary" size="small" :loading="saving" @click="saveEdit">
            <template #icon><n-icon><SaveIcon /></n-icon></template>
            {{ t("common.save") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </n-spin>
</template>
