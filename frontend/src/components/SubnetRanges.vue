<script setup lang="ts">
/**
 * 子網路內的位址範圍（集區）—— GitHub issue #40。
 *
 * 子網路維持 CIDR（它是一個 L3 網路）；DHCP 集區、保留給某些設備的一段位址是它裡面的一段，
 * 常常不是一個 CIDR 表示得了的（例如 .181～.250）。以前只能拆成一堆 /32、/31 的子網路。
 * 用途為「DHCP 集區」的範圍也會算進清單上的「在 DHCP 範圍內」與 DHCP 集區使用率。
 */
import { computed, h, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NButton, NDataTable, NForm, NFormItem, NIcon, NInput, NModal, NPopconfirm, NProgress,
  NSelect, NSpace, NTag, useMessage, type DataTableColumns,
} from "naive-ui";
import { PlusIcon, EditIcon, DeleteIcon, CancelIcon, SaveIcon } from "@/icons";
import { apiErrMsg } from "@/api/client";
import {
  createIPRange, deleteIPRange, updateIPRange, RANGE_COLORS,
  type IPRange, type IPRangePurpose,
} from "@/api/ipRanges";

const props = defineProps<{ subnetId: string; cidr: string; ranges: IPRange[]; canEdit: boolean }>();
const emit = defineEmits<{
  (e: "changed"): void;
  (e: "create-ip", ip: string): void;
}>();
const { t } = useI18n();
const msg = useMessage();

const purposeOpts = computed(() => (["dhcp", "reserved", "other"] as IPRangePurpose[]).map((v) => ({
  label: t(`ranges.purpose_${v}`), value: v,
})));

const columns = computed<DataTableColumns<IPRange>>(() => [
  {
    // 要給寬度：沒有寬度時等寬、不換行的文字會蓋到隔壁「用途」的標籤上。
    // 兩端各自不換行、長的（IPv6）在「–」處換行
    title: t("ranges.col_range"), key: "start_ip", width: 260,
    render: (r) => h("span", { style: "font-family: monospace; display:inline-flex; flex-wrap:wrap; column-gap:6px" }, [
      // 空格留在文字裡（複製出來、讀屏都是「起 – 迄」）；換行時行尾的空白不會畫出來
      h("span", { style: "white-space: nowrap" }, `${r.start_ip} `),
      h("span", { style: "white-space: nowrap" }, `– ${r.end_ip}`),
    ]),
  },
  {
    title: t("ranges.col_purpose"), key: "purpose", width: 120,
    render: (r) => h(NTag, { size: "small", bordered: false,
      color: { color: RANGE_COLORS[r.purpose] + "26", textColor: RANGE_COLORS[r.purpose] } },
    () => t(`ranges.purpose_${r.purpose}`)),
  },
  {
    title: t("common.name"), key: "name", width: 200,
    // 自動建立的：名稱就是來源（firewall-a · KEA），前面標「自動」，滑過去講它跟著誰同步
    render: (r) => r.auto
      ? h("span", { style: "display:inline-flex; align-items:center; gap:6px; white-space:nowrap",
                    title: t("ranges.auto_hint", { source: r.source_label || r.name || "" }) }, [
          h(NTag, { size: "small", bordered: false, type: "info" }, () => t("ranges.auto")),
          r.name || "—",
        ])
      : r.name || "—",
  },
  {
    title: t("ranges.col_used"), key: "used", width: 190,
    render: (r) => h("div", { style: "display:flex; align-items:center; gap:8px" }, [
      h(NProgress, { type: "line", percentage: r.size ? Math.round((r.used / r.size) * 100) : 0,
        showIndicator: false, height: 6, style: "flex:1; min-width:60px",
        status: r.size && r.used / r.size >= 0.9 ? "error" : r.size && r.used / r.size >= 0.75 ? "warning" : "success" }),
      h("span", { style: "font-size:12px; white-space:nowrap" }, `${r.used} / ${r.size}`),
      // DHCP 伺服器排除、不發放的位址不算在總數裡 —— 講出來，免得跟範圍的起訖對不上
      r.excluded ? h("span", { class: "range-excluded", title: t("ranges.excluded_hint", { n: r.excluded }),
                               "data-testid": "range-excluded" }, t("ranges.excluded_n", { n: r.excluded })) : null,
      // DHCP 範圍裡離線又沒租約的舊記錄不算已用 —— 講出來，免得跟 IP 清單的筆數對不上
      r.stale ? h("span", { class: "range-excluded", title: t("ranges.stale_hint", { n: r.stale }),
                            "data-testid": "range-stale" }, t("ranges.stale_n", { n: r.stale })) : null,
    ]),
  },
  {
    title: t("ranges.col_first_free"), key: "first_free", width: 150,
    render: (r) => r.first_free
      ? h(NButton, { text: true, type: "primary", size: "small", disabled: !props.canEdit,
          title: t("ranges.assign_hint"), onClick: () => emit("create-ip", r.first_free as string) },
        () => r.first_free)
      : r.stale && (r.free ?? 0) > 0
        // 沒有空白位址，但有過期記錄：不是「已滿」，DHCP 還發得出去
        ? h("span", { style: "opacity:.6", title: t("ranges.no_blank_hint", { n: r.stale }),
                      "data-testid": "range-no-blank" }, t("ranges.no_blank"))
        : h("span", { style: "opacity:.6" }, t("ranges.full")),
  },
  { title: t("common.description"), key: "description", ellipsis: { tooltip: true },
    render: (r) => r.description || (r.auto ? t("ranges.auto_desc") : "—") },
  ...(props.canEdit ? [{
    title: "", key: "actions", width: 80,
    // 自動建立的由同步管理：不給改、不給刪（後端也會擋），滑過去講要改上游
    render: (r: IPRange) => r.auto
      ? h("span", { class: "auto-lock", title: t("ranges.auto_hint", { source: r.source_label || r.name || "" }) }, "—")
      : h(NSpace, { size: 4, wrap: false }, () => [
      h(NButton, { size: "tiny", quaternary: true, title: t("common.edit"), onClick: () => openEdit(r) },
        { icon: () => h(NIcon, null, () => h(EditIcon)) }),
      h(NPopconfirm, { onPositiveClick: () => remove(r) }, {
        trigger: () => h(NButton, { size: "tiny", quaternary: true, type: "error", title: t("common.delete") },
          { icon: () => h(NIcon, null, () => h(DeleteIcon)) }),
        default: () => t("ranges.delete_confirm", { range: `${r.start_ip} – ${r.end_ip}` }),
      }),
    ]),
  }] : []),
]);

const show = ref(false);
const editing = ref<IPRange | null>(null);
const busy = ref(false);
const form = ref({ start_ip: "", end_ip: "", purpose: "dhcp" as IPRangePurpose, name: "", description: "" });

function openCreate() {
  editing.value = null;
  form.value = { start_ip: "", end_ip: "", purpose: "dhcp", name: "", description: "" };
  show.value = true;
}
function openEdit(r: IPRange) {
  editing.value = r;
  form.value = { start_ip: r.start_ip, end_ip: r.end_ip, purpose: r.purpose,
    name: r.name ?? "", description: r.description ?? "" };
  show.value = true;
}
async function submit() {
  busy.value = true;
  const body = { start_ip: form.value.start_ip.trim(), end_ip: form.value.end_ip.trim(),
    purpose: form.value.purpose, name: form.value.name.trim() || null,
    description: form.value.description.trim() || null };
  try {
    if (editing.value) await updateIPRange(props.subnetId, editing.value.id, body);
    else await createIPRange(props.subnetId, body);
    show.value = false;
    msg.success(t("common.ok"));
    emit("changed");
  } catch (e) { msg.error(apiErrMsg(e)); } finally { busy.value = false; }
}
async function remove(r: IPRange) {
  try {
    await deleteIPRange(props.subnetId, r.id);
    msg.success(t("common.ok"));
    emit("changed");
  } catch (e) { msg.error(apiErrMsg(e)); }
}
</script>

<template>
  <div class="subnet-ranges">
    <n-space align="center" justify="space-between" style="margin-bottom: 10px">
      <span class="field-hint">{{ t("ranges.hint", { cidr }) }}</span>
      <n-button v-if="canEdit" type="primary" size="small" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("ranges.add") }}
      </n-button>
    </n-space>
    <!-- scroll-x：窄的時候整張表橫向捲動，不要把欄位擠成一行一個字 -->
    <n-data-table v-if="ranges.length" :columns="columns" :data="ranges" size="small" :scroll-x="1150"
                  :row-key="(r: IPRange) => r.id" :bordered="false" />
    <div v-else class="empty">{{ t("ranges.empty") }}</div>

    <n-modal v-model:show="show" preset="card" style="width: 460px"
             :title="editing ? t('ranges.edit') : t('ranges.add')">
      <n-form label-placement="left" label-width="90">
        <n-form-item :label="t('ranges.start')" required>
          <n-input v-model:value="form.start_ip" placeholder="198.51.100.181" />
        </n-form-item>
        <n-form-item :label="t('ranges.end')" required>
          <n-input v-model:value="form.end_ip" placeholder="198.51.100.250" />
        </n-form-item>
        <n-form-item :label="t('ranges.col_purpose')">
          <n-select v-model:value="form.purpose" :options="purposeOpts" />
        </n-form-item>
        <n-form-item :label="t('common.name')">
          <n-input v-model:value="form.name" :maxlength="64" />
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :autosize="{ minRows: 2 }" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button size="small" @click="show = false"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
          <n-button size="small" type="primary" :loading="busy" @click="submit"><template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.field-hint { font-size: 12px; opacity: 0.7; }
:global(.range-excluded) { font-size: 11px; opacity: 0.65; white-space: nowrap; }
.empty { font-size: 13px; opacity: 0.6; padding: 6px 0; }
</style>
