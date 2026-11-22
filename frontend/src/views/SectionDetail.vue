<script setup lang="ts">
import { h, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NIcon, NButton, NDescriptions, NDescriptionsItem,
  NProgress, NDataTable, NSpin, NModal, NForm,
  useMessage, type DataTableColumns,
} from "naive-ui";
import { SectionsIcon, SubnetsIcon, RefreshIcon, EditIcon, SaveIcon, CancelIcon } from "@/icons";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { apiClient, apiErrMsg } from "@/api/client";
import { listSubnets, getSubnetUsage } from "@/api/subnets";
import { fetchAllSections, updateSection } from "@/api/sections";
import type { Section, Subnet, SubnetUsage } from "@/types";
import { autoSort } from "@/composables/useTableSort";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useCustomers } from "@/composables/useCustomers";
import ColumnPicker from "@/components/ColumnPicker.vue";
import SectionFormFields, { type SectionFormModel } from "@/components/SectionFormFields.vue";
import { computed } from "vue";
import { useTablePagination } from "@/composables/useTablePagination";
const pg = useTablePagination();
const { t } = useI18n();

const { visibleKeys: snVis, setVisible: snSet, reset: snReset,
  order: snOrder, setOrder: snSetOrder, orderColumns: snOrderCols } = useColumnPrefs(
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
const form = ref<SectionFormModel>({
  name: "", description: "", parent_id: null, strict_mode: false, display_order: 0, customer_id: null,
});
// 上層區段選單要全部區段（跟列表頁同一份表單：SectionFormFields）
const allSections = ref<Section[]>([]);

async function openEdit() {
  if (!section.value) return;
  void ensureCustomerOptsLoaded();
  form.value = {
    name: section.value.name,
    description: section.value.description ?? "",
    parent_id: section.value.parent_id ?? null,
    strict_mode: section.value.strict_mode,
    display_order: section.value.display_order,
    customer_id: section.value.customer_id ?? null,
  };
  showEdit.value = true;
  try { allSections.value = await fetchAllSections(); } catch { /* 讀不到就只少了上層選項 */ }
}

async function saveEdit() {
  if (!section.value) return;
  saving.value = true;
  try {
    if (!form.value.name.trim()) { msg.error(t("common.name_required")); return; }
    await updateSection(section.value.id, {
      name: form.value.name.trim(),
      description: form.value.description.trim() || null,
      parent_id: form.value.parent_id ?? null,
      strict_mode: form.value.strict_mode,
      display_order: form.value.display_order ?? 0,
      customer_id: form.value.customer_id ?? null,
    });
    showEdit.value = false;
    msg.success(t("common.saved"));
    await load(section.value.id);
  } catch (e) {
    msg.error(apiErrMsg(e) || t("errors.network"));
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
  snOrderCols(allColumns.filter((c: any) => snVis.value.includes(c.key))),
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
<!-- 控制元件移到卡片內文最上方（標題列不放控制元件） -->
        <n-space align="center" justify="end" style="margin-bottom: 10px">
          <n-button type="primary" size="small" @click="openEdit">
            <template #icon><n-icon><EditIcon /></n-icon></template>
            {{ t("common.edit") }}
          </n-button>
          <n-button @click="router.push({ name: 'sections' })" size="small">
            <template #icon><n-icon><ArrowLeftIcon /></n-icon></template>
            {{ t("common.back") }}
          </n-button>
        </n-space>
        <n-descriptions bordered :column="2" size="small">
          <n-descriptions-item :label="t('common.name')">{{ section.name }}</n-descriptions-item>
          <n-descriptions-item :label="t('common.subnet_count')">{{ section.subnet_count ?? 0 }}</n-descriptions-item>
          <n-descriptions-item :label="t('sections.strict_mode')">{{ section.strict_mode ? "✓" : "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('custom_fields.display_order')">{{ section.display_order }}</n-descriptions-item>
          <!-- phpIPAM 相容 ID：Proxmox VE SDN 的 phpIPAM 外掛「區段」欄位只收整數，填這個（10-24） -->
          <n-descriptions-item :label="t('sections.compat_id')">
            <span data-testid="section-compat-id">{{ section.compat_id ?? "—" }}</span>
            <span style="margin-left: 10px; font-size: 12px; opacity: .65">{{ t("sections.compat_id_hint") }}</span>
          </n-descriptions-item>
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
<!-- 控制元件移到卡片內文最上方（標題列不放控制元件） -->
        <n-space align="center" justify="end" style="margin-bottom: 10px">
          <ColumnPicker :all="snPicker" :visible="snVis"
                        @update:visible="snSet" @reset="snReset"
                        :order="snOrder" @update:order="snSetOrder" />
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
        <SectionFormFields v-model="form" :sections="allSections" :editing-id="section?.id ?? null"
                           :customer-options="customerOptions" />
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
