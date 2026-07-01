<script setup lang="ts">
/** 進階 → 連線管理：列出所有已啟用 SSH 且本人可連線的目標，可排序/篩選/選欄位/匯出。 */
import { computed, h, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NInput, NButton, NIcon, NDataTable, NButtonGroup, NDropdown,
  NSelect, NTooltip, useMessage, type DataTableColumns,
} from "naive-ui";
import { listConnectionTargets } from "@/api/rdp";
import { TerminalIcon, DisplayIcon, VncIcon, NoVncIcon, ChevronDownIcon, OpenNewWindowIcon, RefreshIcon, SearchIcon } from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useTablePagination } from "@/composables/useTablePagination";
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { useCustomers } from "@/composables/useCustomers";
import ColumnPicker from "@/components/ColumnPicker.vue";
import ExportButton from "@/components/ExportButton.vue";
import LiveStatusDot from "@/components/LiveStatusDot.vue";
import OsIcon from "@/components/OsIcon.vue";
import { renderIcon } from "@/icons";
import type { IPAddress } from "@/types";

const { t } = useI18n();
const router = useRouter();
const msg = useMessage();
const links = useEntityLinks(router);
const { labelFor, ensureLoaded: ensureCustomers } = useCustomers();
const pg = useTablePagination();

const rows = ref<IPAddress[]>([]);
const loading = ref(false);
const { query, filtered } = useTableQuickFilter(rows);

// 工具列篩選：連線類型（SSH / RDP）＋ OS
const typeFilter = ref<string | null>(null);
const osFilter = ref<string | null>(null);
const typeOptions = [{ label: "SSH", value: "ssh" }, { label: "RDP (Beta)", value: "rdp" }, { label: "VNC (Beta)", value: "vnc" }, { label: "noVNC/xterm (PVE)", value: "novnc" }, { label: "BMC SOL (Beta)", value: "bmc" }];
const osOptions = computed(() => {
  const seen = new Map<string, string>();
  for (const r of rows.value) {
    const v = r.os_guess || r.os_family;
    if (v && !seen.has(v)) seen.set(v, v);
  }
  return [...seen.keys()].sort().map((v) => ({ label: v, value: v }));
});
const displayRows = computed(() =>
  filtered.value.filter((r) => {
    if (osFilter.value && (r.os_guess || r.os_family) !== osFilter.value) return false;
    if (typeFilter.value === "ssh" && !r.ssh_available) return false;
    if (typeFilter.value === "rdp" && !r.rdp_available) return false;
    if (typeFilter.value === "vnc" && !r.vnc_available) return false;
    if (typeFilter.value === "novnc" && !r.novnc_available) return false;
    if (typeFilter.value === "bmc" && !r.bmc_available) return false;
    return true;
  }));

// 寬度不夠時操作欄按鈕只顯示 icon（量測卡片容器寬度）
const rootRef = ref<any>(null);
const elWidth = ref(99999);
// 門檻隨「列中最多連線種類」放大：一列有越多種連線（SSH/RDP/VNC），帶文字按鈕越寬，需要更多容器寬度
const compact = computed(() => {
  const mp = Math.max(1, ...rows.value.map((r) =>
    (r.ssh_available ? 1 : 0) + (r.rdp_available ? 1 : 0) + (r.vnc_available ? 1 : 0) + (r.novnc_available ? 1 : 0) + (r.bmc_available ? 1 : 0)));
  return elWidth.value < 740 + mp * 115;
});
let ro: ResizeObserver | null = null;

function sshHref(row: IPAddress) {
  return router.resolve({ name: "ssh-console", params: { id: row.id } }).href;
}
function openTab(row: IPAddress) { window.open(sshHref(row), "_blank"); }
function openWin(row: IPAddress) { window.open(sshHref(row), `ssh-${row.id}`, "width=960,height=640"); }
const sshRowMenu = [{ label: t("ssh.open_popout"), key: "popout", icon: renderIcon(OpenNewWindowIcon) }];
function onRowMenu(key: string, row: IPAddress) { if (key === "popout") openWin(row); }

function rdpHref(row: IPAddress) {
  return router.resolve({ name: "rdp-console", params: { id: row.id } }).href;
}
function openRdpTab(row: IPAddress) { window.open(rdpHref(row), "_blank"); }
function openRdpWin(row: IPAddress) { window.open(rdpHref(row), `rdp-${row.id}`, "width=1320,height=900"); }
const rdpRowMenu = [{ label: t("rdp.open_popout"), key: "popout", icon: renderIcon(OpenNewWindowIcon) }];
function onRdpRowMenu(key: string, row: IPAddress) { if (key === "popout") openRdpWin(row); }

function vncHref(row: IPAddress) {
  return router.resolve({ name: "vnc-console", params: { id: row.id } }).href;
}
function openVncTab(row: IPAddress) { window.open(vncHref(row), "_blank"); }
function openVncWin(row: IPAddress) { window.open(vncHref(row), `vnc-${row.id}`, "width=1320,height=900"); }
const vncRowMenu = [{ label: t("vnc.open_popout"), key: "popout", icon: renderIcon(OpenNewWindowIcon) }];
function onVncRowMenu(key: string, row: IPAddress) { if (key === "popout") openVncWin(row); }

function novncHref(row: IPAddress) {
  return router.resolve({ name: "novnc-console", params: { id: row.id } }).href;
}
function openNovncTab(row: IPAddress) { window.open(novncHref(row), "_blank"); }
function openNovncWin(row: IPAddress) { window.open(novncHref(row), `novnc-${row.id}`, "width=1320,height=900"); }
const novncRowMenu = [{ label: t("vnc.open_popout"), key: "popout", icon: renderIcon(OpenNewWindowIcon) }];
function onNovncRowMenu(key: string, row: IPAddress) { if (key === "popout") openNovncWin(row); }

function bmcHref(row: IPAddress) {
  return router.resolve({ name: "bmc-console", params: { id: row.id } }).href;
}
function openBmcTab(row: IPAddress) { window.open(bmcHref(row), "_blank"); }
function openBmcWin(row: IPAddress) { window.open(bmcHref(row), `bmc-${row.id}`, "width=1040,height=680"); }
const bmcRowMenu = [{ label: t("vnc.open_popout"), key: "popout", icon: renderIcon(OpenNewWindowIcon) }];
function onBmcRowMenu(key: string, row: IPAddress) { if (key === "popout") openBmcWin(row); }

async function refresh() {
  loading.value = true;
  try { rows.value = await listConnectionTargets(); }
  catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
onMounted(() => {
  void refresh();
  void ensureCustomers();   // 確保「單位」名稱可解析（否則 labelFor 退回顯示 UUID 片段）
  const el = rootRef.value?.$el as HTMLElement | undefined;
  if (el) {
    ro = new ResizeObserver(() => { elWidth.value = el.clientWidth; });
    ro.observe(el);
  }
});
onBeforeUnmount(() => { ro?.disconnect(); ro = null; });

const { visibleKeys, setVisible, reset, isVisible } = useColumnPrefs(
  "connections",
  ["ip", "hostname", "unit", "device", "os", "status", "actions"],
  ["ip", "hostname", "unit", "device", "os", "status", "actions"],
);
const pickerCols = [
  { key: "ip", label: t("connections.col_ip") },
  { key: "hostname", label: t("connections.col_hostname") },
  { key: "unit", label: t("connections.col_unit") },
  { key: "device", label: t("connections.col_device") },
  { key: "os", label: t("connections.col_os") },
  { key: "status", label: t("connections.col_status") },
];

const allColumns = computed<DataTableColumns<IPAddress>>(() => {
  const cz = compact.value;   // 讓此 computed 隨 compact 變動重算 → 表格重繪
  return [
    { title: "", key: "status", width: 44, align: "center", sorter: false,
      render: (r) => h(LiveStatusDot, { address: r }) },
    {
      title: t("connections.col_ip"), key: "ip", sorter: "default", width: 160,
      render: (r) => h("a", {
        style: "color:var(--n-color-target,#2080f0);cursor:pointer",
        onClick: () => router.push({ name: "address-detail", params: { id: r.id } }),
      }, r.ip),
    },
    { title: t("connections.col_hostname"), key: "hostname", sorter: "default",
      render: (r) => r.hostname || "—" },
    { title: t("connections.col_unit"), key: "unit", sorter: "default",
      render: (r) => labelFor(r.customer_id) || "—" },
    { title: t("connections.col_device"), key: "device", sorter: "default",
      render: (r) => (r.device_id ? links.device(r.device_id, r.device_name) : "—") },
    { title: t("connections.col_os"), key: "os", sorter: "default", minWidth: 150,
      render: (r) => h("span",
        { style: "display:inline-flex;align-items:center;gap:5px;white-space:nowrap" },
        [h(OsIcon, { family: r.os_family }), r.os_guess || "—"]) },
    {
      title: t("connections.col_actions"), key: "actions", width: cz ? 190 : 300,
      render: (r) => {
        // 只有頁面（卡片）真的窄時才收成 icon；寬度夠就顯示文字（欄寬已留可容 3 組帶文字按鈕）
        const ic = cz;
        const grp = (key: string, icon: any, label: string, title: string, onMain: () => void,
                     menu: any, onMenu: (k: string) => void, btnType: "info" | "warning" = "info") =>
          h(NTooltip, { key, delay: 200 }, {
            // 用系統自己的即時彈窗，不要瀏覽器原生 title
            trigger: () => h(NButtonGroup, null, () => [
              h(NButton, { type: btnType, size: "small", onClick: onMain },
                ic ? { icon: () => h(NIcon, null, () => h(icon)) }
                   : { icon: () => h(NIcon, null, () => h(icon)), default: () => label }),
              h(NDropdown, { trigger: "click", options: menu, onSelect: onMenu },
                () => h(NButton, { type: btnType, size: "small", style: "padding:0 2px;border-left:1px solid rgba(255,255,255,.45)" },
                  { icon: () => h(NIcon, null, () => h(ChevronDownIcon)) })),
            ]),
            default: () => title,
          });
        const groups = [];
        if (r.ssh_available)
          groups.push(grp("ssh", TerminalIcon, "SSH", t("ssh.connect"), () => openTab(r), sshRowMenu, (k) => onRowMenu(k, r)));
        if (r.rdp_available)
          groups.push(grp("rdp", DisplayIcon, "RDP", t("rdp.connect"), () => openRdpTab(r), rdpRowMenu, (k) => onRdpRowMenu(k, r)));
        if (r.vnc_available)
          groups.push(grp("vnc", VncIcon, "VNC", t("vnc.connect"), () => openVncTab(r), vncRowMenu, (k) => onVncRowMenu(k, r)));
        if (r.novnc_available) {
          const isCt = r.pve?.kind === "ct";
          const proto = isCt ? "xterm" : "noVNC";
          // 比照 IP 詳情頁：橘色按鈕 + 右上角「PVE」小標（label 只放 noVNC/xterm）
          const btn = grp("novnc", isCt ? TerminalIcon : NoVncIcon,
            proto, `${proto} ${t("novnc.connect")}`,
            () => openNovncTab(r), novncRowMenu, (k) => onNovncRowMenu(k, r), "warning");
          groups.push(h("span", { key: "novnc-wrap", style: "position:relative;display:inline-flex" }, [
            btn,
            h("span", {
              style: "position:absolute;top:-7px;right:-6px;z-index:2;pointer-events:none;"
                + "font-size:9px;font-weight:700;line-height:1;letter-spacing:.2px;padding:1px 4px;"
                + "border-radius:999px;color:#fff;background:#d99812;box-shadow:0 0 0 1.5px var(--n-color,#fff)",
            }, "PVE"),
          ]));
        }
        if (r.bmc_available) {
          const bbtn = grp("bmc", TerminalIcon, "BMC", t("bmc.connect"), () => openBmcTab(r), bmcRowMenu, (k) => onBmcRowMenu(k, r), "warning");
          groups.push(h("span", { key: "bmc-wrap", style: "position:relative;display:inline-flex" }, [
            bbtn,
            h("span", {
              style: "position:absolute;top:-7px;right:-6px;z-index:2;pointer-events:none;"
                + "font-size:9px;font-weight:700;line-height:1;letter-spacing:.2px;padding:1px 4px;"
                + "border-radius:999px;color:#fff;background:#9aa3af;box-shadow:0 0 0 1.5px var(--n-color,#fff)",
            }, "SOL"),
          ]));
        }
        return h("div", { style: "display:flex;gap:6px;flex-wrap:nowrap" }, groups);
      },
    },
  ];
});
const columns = computed(() =>
  autoSort(allColumns.value.filter((c) => isVisible((c as any).key))));
</script>

<template>
  <n-card ref="rootRef" :bordered="false">
    <template #header>
      <span style="display:flex;align-items:center;gap:8px">
        <n-icon :component="TerminalIcon" :size="20" />
        <span>{{ t("nav.connections") }}</span>
      </span>
    </template>

    <p style="margin:0 0 12px;opacity:.7;font-size:13px">{{ t("connections.hint") }}</p>

    <!-- 工具列：放在表格上方（搜尋 / 選欄位 / 匯出 / 重新整理） -->
    <n-space align="center" :size="8" style="margin-bottom: 12px" :wrap="true">
      <n-input v-model:value="query" clearable :placeholder="t('common.search')" style="width: 220px">
        <template #prefix><n-icon :component="SearchIcon" /></template>
      </n-input>
      <n-select v-model:value="typeFilter" :options="typeOptions" clearable :consistent-menu-width="false"
                :placeholder="t('connections.filter_type')" style="width: 150px" />
      <n-select v-model:value="osFilter" :options="osOptions" clearable
                :consistent-menu-width="false"
                :placeholder="t('connections.filter_os')" style="width: 170px" />
      <ColumnPicker :all="pickerCols" :visible="visibleKeys"
                    @update:visible="setVisible" @reset="reset" />
      <ExportButton :columns="allColumns" :rows="displayRows" filename="ssh-connections"
                    :title="t('nav.connections')" />
      <n-button size="small" @click="refresh">
        <template #icon><n-icon :component="RefreshIcon" /></template>{{ t("common.refresh") }}
      </n-button>
    </n-space>

    <n-data-table
      :columns="columns" :data="displayRows" :loading="loading"
      :pagination="pg" :row-key="(r: IPAddress) => r.id"
      size="small" :bordered="false" />
  </n-card>
</template>

<style scoped>
/* 卡片標題 icon+文字垂直置中（覆蓋主題預設，避免內容偏上） */
:deep(.n-card > .n-card-header) { display: flex; align-items: center; padding-top: 12px; padding-bottom: 12px; }
</style>
