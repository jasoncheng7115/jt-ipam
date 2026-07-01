<script setup lang="ts">
/**
 * Graylog DSV 對照表（僅管理員）。
 * 上半：所有 DSV 端點的表格（可排序 / 選欄 / 篩選 / 重新整理）+ 詳細資料抽屜。
 * 下半：Graylog 串接教學 — 內容跟著表格選取的那個 DSV 來源變動（含切換過場）。
 * 可擴充：未來新 DSV 類型只要往 dsvSources push 一筆，表格與教學自動帶出。
 */
import { computed, h, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NIcon, NInput, NSelect, NSwitch, NButton, NAlert, NTag,
  NDrawer, NDrawerContent, NDataTable, useMessage, type DataTableColumns,
} from "naive-ui";
import { ExportIcon, SaveIcon, RefreshIcon, CopyIcon, InfoIcon } from "@/icons";
import { getGraylogDsv, putGraylogDsv, type GraylogDsv } from "@/api/system";
import { listFirewalls } from "@/api/integrations";
import { Virt } from "@/api/phase3";
import { autoSort } from "@/composables/useTableSort";
import { useColumnPrefs } from "@/composables/useColumnPrefs";
import { useTableQuickFilter } from "@/composables/useTableQuickFilter";
import ColumnPicker from "@/components/ColumnPicker.vue";

const { t } = useI18n();
const msg = useMessage();

const dsv = ref<GraylogDsv>({ enabled: false, fmt: "csv", path: "ip-fqdn", token: "" });
const saving = ref(false);
const loading = ref(false);
const fmtOpts = [{ label: "CSV (,)", value: "csv" }, { label: "TSV (Tab)", value: "tsv" }];
const DSV_HTTP_PORT = 8088;

// 防火牆 DSV：每台啟用「對外提供 DSV」的 OPNsense
const fwDsv = ref<{ id: string; name: string }[]>([]);
const pveClusters = ref<{ id: string; name: string }[]>([]);
function fwLookupUrl(id: string, kind: "rule-aliases" | "aliases", http = false): string {
  if (!dsv.value.token) return "";
  const base = http ? `http://${location.hostname}:${DSV_HTTP_PORT}` : location.origin;
  return `${base}/api/v1/lookup/firewall/${id}/${kind}?token=${dsv.value.token}`;
}
function proxmoxVmsUrl(clusterId: string, http = false): string {
  if (!dsv.value.token) return "";
  const base = http ? `http://${location.hostname}:${DSV_HTTP_PORT}` : location.origin;
  return `${base}/api/v1/lookup/proxmox/${clusterId}/vms?token=${dsv.value.token}`;
}
function slugify(s: string): string {
  return (s || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "fw";
}

const dsvUrl = computed(() =>
  dsv.value.token ? `${location.origin}/api/v1/lookup/${dsv.value.path}?token=${dsv.value.token}` : "");
const dsvUrlHttp = computed(() =>
  dsv.value.token
    ? `http://${location.hostname}:${DSV_HTTP_PORT}/api/v1/lookup/${dsv.value.path}?token=${dsv.value.token}`
    : "");
const gSep = computed(() => (dsv.value.fmt === "tsv" ? "\\t" : ","));

// ── 可擴充的 DSV 來源清單 ──
type DsvKind = "hostname" | "fw_rules" | "fw_aliases" | "pve_vms";
interface DsvSource {
  id: string;
  name: string;
  kind: DsvKind;
  mapping: string;       // "key → value"，表格與教學共用
  base: string;          // Graylog 物件名首碼（adapter/cache/table）
  defaultField: string;  // 教學裡預設的 log 欄位
  enabled: boolean;
  https: string;
  http: string;
  notes: string;
  editable: boolean;     // IP→主機名稱 那筆可在抽屜內改開關 / 路徑
}
const dsvSources = computed<DsvSource[]>(() => {
  const out: DsvSource[] = [{
    id: "hostname",
    name: t("settings.system.graylog_src_hostname"),
    kind: "hostname",
    mapping: "IP → hostname / FQDN",
    base: "jt_ipam",
    defaultField: "src_ip",
    enabled: dsv.value.enabled,
    https: dsvUrl.value,
    http: dsvUrlHttp.value,
    notes: t("settings.system.graylog_hint"),
    editable: true,
  }];
  // 每個 PVE 叢集 / 獨立節點各一筆（vmid 跨叢集會重複，分開才不混淆；比照 OPNsense 多防火牆）
  for (const c of pveClusters.value) {
    const sl = slugify(c.name);
    out.push({
      id: `pve:${c.id}`,
      name: `${c.name} · ${t("settings.system.graylog_src_pve_vms")}`,
      kind: "pve_vms",
      mapping: "vmid → vm name",
      base: `jt_ipam_pve_${sl}`,
      defaultField: "vmid",
      enabled: true,
      https: proxmoxVmsUrl(c.id),
      http: proxmoxVmsUrl(c.id, true),
      notes: t("settings.system.graylog_pve_hint"),
      editable: false,
    });
  }
  for (const fw of fwDsv.value) {
    const sl = slugify(fw.name);
    out.push({
      id: `fw:${fw.id}:rules`,
      name: `${fw.name} · ${t("settings.system.graylog_src_fw_rules")}`,
      kind: "fw_rules",
      mapping: "filterlog rid → alias",
      base: `jt_ipam_${sl}_rules`,
      defaultField: "rid",
      enabled: true,
      https: fwLookupUrl(fw.id, "rule-aliases"),
      http: fwLookupUrl(fw.id, "rule-aliases", true),
      notes: t("settings.system.graylog_fw_hint"),
      editable: false,
    });
    out.push({
      id: `fw:${fw.id}:aliases`,
      name: `${fw.name} · ${t("settings.system.graylog_src_fw_aliases")}`,
      kind: "fw_aliases",
      mapping: "alias → members",
      base: `jt_ipam_${sl}_aliases`,
      defaultField: "alias",
      enabled: true,
      https: fwLookupUrl(fw.id, "aliases"),
      http: fwLookupUrl(fw.id, "aliases", true),
      notes: t("settings.system.graylog_fw_hint"),
      editable: false,
    });
  }
  return out;
});

// ── 表格（排序 / 選欄 / 篩選 / 重新整理）──
const PICKER = computed(() => [
  { key: "name", label: t("settings.system.graylog_tbl_name") },
  { key: "mapping", label: t("settings.system.graylog_tbl_mapping") },
  { key: "enabled", label: t("common.status") },
]);
const { visibleKeys, setVisible, reset } = useColumnPrefs(
  "graylog_dsv_sources", ["name", "mapping", "enabled"], ["name", "mapping", "enabled"]);
const { query, filtered } = useTableQuickFilter(dsvSources);
const allCols = computed<DataTableColumns<DsvSource>>(() => autoSort([
  { title: t("settings.system.graylog_tbl_name"), key: "name", minWidth: 200, ellipsis: { tooltip: true } },
  { title: t("settings.system.graylog_tbl_mapping"), key: "mapping", minWidth: 200,
    render: (r) => h("code", { class: "dsv-map" }, r.mapping) },
  { title: t("common.status"), key: "enabled", width: 96,
    render: (r) => h(NTag, { size: "small", bordered: false, type: r.enabled ? "success" : "default" },
      () => r.enabled ? t("settings.system.graylog_status_on") : t("settings.system.graylog_status_off")) },
  { title: t("common.actions"), key: "_", className: "col-actions", width: 130,
    render: (r) => h(NButton, { size: "tiny", tertiary: true,
      onClick: (e: MouseEvent) => { e.stopPropagation(); openDetail(r.id); } },
      { icon: () => h(NIcon, null, () => h(InfoIcon)), default: () => t("settings.system.graylog_detail") }) },
]));
const cols = computed(() => allCols.value.filter((c: any) => c.key === "_" || visibleKeys.value.includes(String(c.key))));
function rowProps(row: DsvSource) {
  return {
    style: "cursor: pointer",
    class: row.id === selectedId.value ? "dsv-row-selected" : "",
    onClick: () => { selectedId.value = row.id; },
  };
}

// ── 詳細資料抽屜 ──
const detailId = ref<string | null>(null);
const detail = computed<DsvSource | null>(() => dsvSources.value.find((s) => s.id === detailId.value) ?? null);
function openDetail(id: string) { detailId.value = id; }
const detailOpen = computed({
  get: () => detailId.value !== null,
  set: (v: boolean) => { if (!v) detailId.value = null; },
});

// ── 選取（驅動下方教學）──
const selectedId = ref("hostname");
const selected = computed<DsvSource>(() => dsvSources.value.find((s) => s.id === selectedId.value) ?? dsvSources.value[0]);

async function load() {
  loading.value = true;
  try { dsv.value = await getGraylogDsv(); } catch { /* ignore */ }
  try {
    const r = await listFirewalls(200, 0);
    fwDsv.value = r.items.filter((f) => f.expose_dsv).map((f) => ({ id: f.id, name: f.name }));
  } catch { /* ignore */ }
  try {
    pveClusters.value = (await Virt.clusters()).map((c) => ({ id: c.id, name: c.name }));
  } catch { /* ignore */ }
  // 選取的來源若已不存在（防火牆關閉 DSV），退回第一筆
  if (!dsvSources.value.some((s) => s.id === selectedId.value)) selectedId.value = "hostname";
  loading.value = false;
}
async function save(regenerate = false) {
  saving.value = true;
  try {
    dsv.value = await putGraylogDsv({
      enabled: dsv.value.enabled, fmt: dsv.value.fmt, path: dsv.value.path, regenerate_token: regenerate,
    });
    msg.success(t("common.saved"));
  } catch { msg.error(t("errors.network")); } finally { saving.value = false; }
}
function copy(text: string) {
  if (text) { void navigator.clipboard.writeText(text); msg.success(t("common.copied_clipboard")); }
}
// 教學表格內任何 <code>（要貼進 Graylog 的值）點一下就複製
function onCodeCopy(e: MouseEvent) {
  const el = e.target as HTMLElement;
  if (el && el.tagName === "CODE") {
    const txt = (el.innerText || el.textContent || "").trim();
    if (txt) { void navigator.clipboard.writeText(txt); msg.success(t("common.copied_clipboard")); }
  }
}

// ── 教學內容（跟著 selected 變動）──
const gBase = computed(() => selected.value.base);
const gAdapter = computed(() => `${gBase.value}_adapter`);
const gCache = computed(() => `${gBase.value}_cache`);
const gTable = computed(() => `${gBase.value}_table`);
const gUrl = computed(() => selected.value.https || "https://<jt-ipam>/api/v1/lookup/…?token=<token>");
const gUrlHttp = computed(() => selected.value.http
  || `http://<jt-ipam>:${DSV_HTTP_PORT}/api/v1/lookup/…?token=<token>`);
const mapParts = computed(() => selected.value.mapping.split("→").map((s) => s.trim()));
const gKeyDesc = computed(() => mapParts.value[0] || "key");
const gValDesc = computed(() => mapParts.value[1] || "value");
const isHostname = computed(() => selected.value.kind === "hostname");

// log 欄位（切換來源時帶回該來源預設）
const gField = ref("src_ip");
watch(selected, (s) => { if (s) gField.value = s.defaultField; }, { immediate: true });
const gFieldError = computed(() => {
  const v = (gField.value || "").trim();
  if (!v) return t("settings.system.graylog_g_field_err_empty");
  if (/^[0-9]/.test(v)) return t("settings.system.graylog_g_field_err_digit");
  if (!/^[A-Za-z0-9_]+$/.test(v)) return t("settings.system.graylog_g_field_err_chars");
  return "";
});
const gFieldClean = computed(() =>
  (gField.value || "").trim().replace(/[^A-Za-z0-9_]/g, "") || selected.value.defaultField);
const gOutField = computed(() => selected.value.kind === "fw_rules" ? "rule_alias"
  : selected.value.kind === "fw_aliases" ? "alias_members"
  : selected.value.kind === "pve_vms" ? "vm_name"
  : `${gFieldClean.value}_hostname`);
const pipelineRule = computed(() => {
  const f = gFieldClean.value, tbl = gTable.value, out = gOutField.value;
  if (isHostname.value) {
    return `rule "jt-ipam enrich ${f} -> ${out} (LAN only)"
when
    has_field("${f}") &&
    (
        cidr_match("10.0.0.0/8",     to_ip($message.${f})) ||
        cidr_match("172.16.0.0/12",  to_ip($message.${f})) ||
        cidr_match("192.168.0.0/16", to_ip($message.${f}))
    )
then
    let v = lookup_value("${tbl}", to_string($message.${f}));
    set_field("${out}", v);
end`;
  }
  return `rule "jt-ipam enrich ${f} -> ${out}"
when
    has_field("${f}")
then
    let v = lookup_value("${tbl}", to_string($message.${f}));
    set_field("${out}", v);
end`;
});

onMounted(() => { void load(); });
</script>

<template>
  <div class="gd-wrap">
    <!-- ── 端點設定 + 表格 ── -->
    <n-card>
      <template #header>
        <n-space align="center" :wrap-item="false">
          <n-icon :size="20"><ExportIcon /></n-icon>
          <span>{{ t("settings.system.graylog_title") }}</span>
        </n-space>
      </template>
      <p class="gd-intro">{{ t("settings.system.graylog_page_intro") }}</p>

      <!-- 格式（輸出格式設定）與 權杖（存取金鑰，與格式無關）是兩回事 → 左右兩張卡片，明顯分開 -->
      <div class="gd-config-row">
        <div class="gd-panel">
          <label class="gd-panel-label">{{ t("settings.system.graylog_format") }}</label>
          <n-select v-model:value="dsv.fmt" :options="fmtOpts" style="max-width:240px" @update:value="() => save()" />
        </div>
        <div class="gd-panel">
          <label class="gd-panel-label">{{ t("settings.system.graylog_token_label") }}</label>
          <n-button size="small" :loading="saving" @click="() => save(true)">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("settings.system.graylog_regen") }}
          </n-button>
        </div>
      </div>

      <n-alert v-if="!dsv.token" type="warning" :show-icon="true" style="margin-top:14px">
        {{ t("settings.system.graylog_need_token") }}
        <n-button size="tiny" type="primary" style="margin-left:8px" :loading="saving" @click="() => save(true)">
          {{ t("settings.system.graylog_gen_token") }}
        </n-button>
      </n-alert>

      <template v-else>
        <!-- 工具列：篩選 / 重新整理 / 選欄（與上方「格式 / 權杖」區隔開，加分隔線）-->
        <n-space align="center" class="gd-toolbar"
                 style="margin:28px 0 8px; padding-top:22px; border-top:1px solid rgba(128,128,128,.18)">
          <n-input v-model:value="query" clearable style="width:220px" :placeholder="t('common.filter')" />
          <n-button :loading="loading" @click="load">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
          </n-button>
          <ColumnPicker :all="PICKER" :visible="visibleKeys" @update:visible="setVisible" @reset="reset" />
        </n-space>
        <n-data-table
          :columns="cols" :data="filtered" :loading="loading" :bordered="false"
          :row-props="rowProps" :scroll-x="640" size="small"
        />
        <div class="hint" style="line-height:1.6; margin-top:8px">{{ t("settings.system.graylog_tbl_hint") }}</div>
      </template>
    </n-card>

    <!-- ── DSV 詳細資料抽屜 ── -->
    <n-drawer v-model:show="detailOpen" :width="540" placement="right">
      <n-drawer-content :title="detail?.name ?? ''" closable>
        <template v-if="detail">
          <div class="fld">
            <label>{{ t("settings.system.graylog_tbl_mapping") }}</label>
            <code>{{ detail.mapping }}</code>
          </div>
          <template v-if="detail.editable">
            <div class="gd-row" style="margin-top:14px">
              <n-switch v-model:value="dsv.enabled" @update:value="() => save()" />
              <span class="gd-switch-label">{{ t("settings.system.graylog_enable") }}</span>
            </div>
            <div class="fld" style="margin-top:12px">
              <label>{{ t("settings.system.graylog_path") }}</label>
              <div class="gd-url">
                <n-input v-model:value="dsv.path" placeholder="ip-fqdn" style="flex:1" />
                <n-button size="small" type="primary" :loading="saving" @click="() => save()">
                  <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
                </n-button>
              </div>
            </div>
          </template>
          <div class="fld" style="margin-top:14px">
            <label>{{ t("settings.system.graylog_url") }}</label>
            <div class="gd-url">
              <n-input :value="detail.https" readonly style="flex:1" />
              <n-button size="small" type="primary" ghost :disabled="!detail.https" @click="copy(detail.https)">
                <template #icon><n-icon><CopyIcon /></n-icon></template>{{ t("settings.system.graylog_copy") }}
              </n-button>
            </div>
          </div>
          <div class="fld" style="margin-top:10px">
            <label>{{ t("settings.system.graylog_url_http") }}</label>
            <div class="gd-url">
              <n-input :value="detail.http" readonly style="flex:1" />
              <n-button size="small" type="primary" ghost :disabled="!detail.http" @click="copy(detail.http)">
                <template #icon><n-icon><CopyIcon /></n-icon></template>{{ t("settings.system.graylog_copy") }}
              </n-button>
            </div>
            <div class="hint" style="margin-top:4px">{{ t("settings.system.graylog_url_http_hint") }}</div>
          </div>
          <div class="hint" style="line-height:1.7; margin-top:14px">{{ detail.notes }}</div>
        </template>
      </n-drawer-content>
    </n-drawer>

    <!-- ── Graylog 串接教學（跟著表格選取的來源）── -->
    <n-card style="margin-top:16px">
      <template #header>
        <n-space align="center" :wrap-item="false">
          <n-icon :size="20"><InfoIcon /></n-icon>
          <span>{{ t("settings.system.graylog_guide_card") }} — {{ selected.name }}</span>
        </n-space>
      </template>

      <Transition name="guide-swap" mode="out-in">
        <div :key="selectedId" class="guide-body" @click="onCodeCopy">
          <n-alert type="info" :show-icon="true" style="margin-bottom:10px">
            {{ t("settings.system.graylog_g_how") }}
          </n-alert>
          <p class="gd-note" style="margin-bottom:14px">{{ t("settings.system.graylog_g_click_copy") }}</p>

          <div class="gd-step"><span class="gd-step-num">1</span>
            <span class="gd-step-title">{{ t("settings.system.graylog_g_lt_title") }}</span></div>
          <p class="gd-p">{{ t("settings.system.graylog_g_lt_intro") }}</p>

          <div class="gd-sub">① Data Adapter — <code>DSV File from HTTP</code></div>
          <table class="gd-tbl">
            <tr><td>Title / Name</td><td><code>{{ gAdapter }}</code></td></tr>
            <tr><td>Description</td><td>{{ selected.name }}（<code>{{ selected.mapping }}</code>）</td></tr>
            <tr><td>File / Download URL</td><td>
              <div>HTTPS：<code>{{ gUrl }}</code></div>
              <div style="margin-top:4px">{{ t("settings.system.graylog_g_url_or_http") }}<code>{{ gUrlHttp }}</code></div>
            </td></tr>
            <tr><td>Separator</td><td><code>{{ gSep }}</code> （CSV=逗號、TSV=Tab）</td></tr>
            <tr><td>Line Separator</td><td><code>\n</code></td></tr>
            <tr><td>Quote character</td><td><code>"</code>（TSV 可留空）</td></tr>
            <tr><td>Ignore characters</td><td><code>#</code></td></tr>
            <tr><td>Key column</td><td><code>0</code>（{{ gKeyDesc }}）</td></tr>
            <tr><td>Value column</td><td><code>1</code>（{{ gValDesc }}）</td></tr>
            <tr><td>Refresh interval</td><td><code>300</code> 秒（多久重抓一次）</td></tr>
          </table>

          <div class="gd-sub">② Cache — <code>Node-local, in-memory cache</code></div>
          <table class="gd-tbl">
            <tr><td>Title / Name</td><td><code>{{ gCache }}</code></td></tr>
            <tr><td>Description</td><td>{{ t("settings.system.graylog_g_cache_desc") }}</td></tr>
            <tr><td>Maximum entries</td><td><code>100000</code></td></tr>
            <tr><td>Expire after access</td><td><code>300</code> 秒</td></tr>
            <tr><td>Expire after write</td><td>{{ t("settings.system.graylog_g_leave_empty") }}</td></tr>
          </table>

          <div class="gd-sub">③ Lookup Table</div>
          <table class="gd-tbl">
            <tr><td>Title / Name</td><td><code>{{ gTable }}</code></td></tr>
            <tr><td>Description</td><td>{{ t("settings.system.graylog_g_lt_desc") }}</td></tr>
            <tr><td>Data Adapter</td><td>{{ t("settings.system.graylog_g_pick_adapter") }}<code>{{ gAdapter }}</code></td></tr>
            <tr><td>Cache</td><td>{{ t("settings.system.graylog_g_pick_cache") }}<code>{{ gCache }}</code></td></tr>
            <tr><td>Default single value</td><td>{{ t("settings.system.graylog_g_leave_empty") }}</td></tr>
            <tr><td>Default multi value</td><td>{{ t("settings.system.graylog_g_leave_empty") }}</td></tr>
          </table>

          <!-- 步驟 2：套用到記錄（Extractor 或 Pipeline 擇一，不是兩步驟）-->
          <div class="gd-step"><span class="gd-step-num">2</span>
            <span class="gd-step-title">{{ t("settings.system.graylog_g_apply_title") }}</span></div>
          <p class="gd-p">{{ t("settings.system.graylog_g_apply_intro") }}</p>
          <!-- 要查的 log 欄位（兩種做法共用）-->
          <div class="gd-ipfield">
            <span>{{ t("settings.system.graylog_g_field_label") }}</span>
            <n-input v-model:value="gField" size="small" :placeholder="selected.defaultField" style="max-width: 200px"
                     :status="gFieldError ? 'error' : undefined" />
            <span v-if="gFieldError" class="gd-field-err">{{ gFieldError }}</span>
            <span v-else class="gd-note" style="margin:0">→ <code>$message.{{ gFieldClean }}</code> → <code>{{ gOutField }}</code></span>
          </div>

          <!-- 做法 A：Extractor -->
          <div class="gd-alt">{{ t("settings.system.graylog_g_alt_ex") }}</div>
          <p class="gd-p">{{ t("settings.system.graylog_g_ex") }}</p>
          <table class="gd-tbl">
            <tr><td>Source field</td><td><code>{{ gFieldClean }}</code></td></tr>
            <tr><td>Lookup Table</td><td><code>{{ gTable }}</code></td></tr>
            <tr><td>Store as</td><td><code>{{ gOutField }}</code></td></tr>
          </table>

          <!-- 做法 B：Pipeline -->
          <div class="gd-alt">{{ t("settings.system.graylog_g_alt_pl") }}</div>
          <p class="gd-p">{{ isHostname ? t("settings.system.graylog_g_pl") : t("settings.system.graylog_g_pl_fw") }}</p>
          <div class="gd-code-head">
            <span>Pipeline rule</span>
            <n-button size="tiny" quaternary @click="copy(pipelineRule)">
              <template #icon><n-icon><CopyIcon /></n-icon></template>{{ t("settings.system.graylog_copy") }}
            </n-button>
          </div>
          <pre class="gd-code">{{ pipelineRule }}</pre>
          <p v-if="isHostname" class="gd-note">{{ t("settings.system.graylog_g_note") }}</p>
        </div>
      </Transition>
    </n-card>
  </div>
</template>

<style scoped>
.gd-wrap { width: 100%; }
.gd-intro { font-size: 13px; opacity: .75; margin: 0 0 14px; line-height: 1.6; }
.gd-row { display: flex; align-items: center; gap: 8px; }
.gd-switch-label { font-size: 13px; }
.fld label { display: block; font-size: 12px; opacity: .8; margin-bottom: 4px; }
/* 格式（輸出設定）與權杖（金鑰）是兩回事 → 左右兩張卡片，明顯分開、窄螢幕自動換行 */
.gd-config-row { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 14px; }
.gd-panel {
  flex: 1 1 240px; min-width: 230px;
  border: 1px solid var(--n-border-color, rgba(128,128,128,.18));
  border-radius: 10px; padding: 13px 16px 15px;
  background: rgba(128,128,128,.035);
}
.gd-panel-label { display: block; font-size: 12px; font-weight: 600; opacity: .75; margin-bottom: 9px; }
/* Extractor / Pipeline 是擇一的兩種做法（非連續步驟）→ 用左邊條的小標，不用數字圓圈 */
.gd-alt { font-size: 13px; font-weight: 700; margin: 18px 0 6px; padding-left: 10px;
  border-left: 3px solid var(--primary-color, #18a058); }
.gd-url { display: flex; gap: 8px; align-items: center; }
.dsv-map { font-size: 12px; }
:deep(.dsv-row-selected td) { background: rgba(64,128,255,0.10) !important; }
.hint { font-size: 11px; opacity: .65; }
.gd-p { font-size: 13px; line-height: 1.7; opacity: .85; margin: 0 0 8px; }
.gd-sub { font-size: 13px; font-weight: 600; margin: 12px 0 6px; }
/* 數字步驟（仿憑證安裝說明的綠色圓圈）*/
.gd-step { display: flex; align-items: center; gap: 10px; margin: 24px 0 8px; padding-top: 16px;
  border-top: 1px solid var(--n-border-color, rgba(128,128,128,.15)); }
.gd-step:first-of-type { border-top: none; padding-top: 4px; margin-top: 4px; }
.gd-step-num { flex: 0 0 auto; width: 24px; height: 24px; border-radius: 50%;
  background: var(--primary-color, #18a058); color: #fff; display: flex; align-items: center;
  justify-content: center; font-size: 13px; font-weight: 700; line-height: 1; }
.gd-step-title { font-size: 14px; font-weight: 600; }
.gd-tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; margin-bottom: 6px; }
.gd-tbl td { padding: 5px 8px; border: 1px solid rgba(128,128,128,.18); vertical-align: top; }
/* 左欄（欄位名）淡底，跟右欄（值）做區分 */
.gd-tbl td:first-child { width: 200px; opacity: .85; white-space: nowrap; background: rgba(128,128,128,.07); font-weight: 500; }
code { background: rgba(128,128,128,.14); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
/* 教學區的值點一下即複製 */
.guide-body code { cursor: pointer; transition: background .12s ease; }
.guide-body code:hover { background: rgba(64,128,255,.22); outline: 1px solid rgba(64,128,255,.4); }
.gd-ipfield { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 6px 0; font-size: 13px; }
.gd-field-err { color: #d03050; font-size: 12px; }
.gd-code-head { display: flex; align-items: center; justify-content: space-between; margin-top: 6px; }
.gd-code { background: rgba(128,128,128,.1); border: 1px solid rgba(128,128,128,.18); border-radius: 6px;
  padding: 12px; font-size: 12px; line-height: 1.5; overflow-x: auto; white-space: pre; margin: 4px 0 8px; }
.gd-note { font-size: 12px; opacity: .65; line-height: 1.6; }
/* 教學切換過場：舊內容淡出上移、新內容淡入下方（out-in）*/
.guide-swap-enter-active, .guide-swap-leave-active { transition: opacity .22s ease, transform .22s ease; }
.guide-swap-enter-from { opacity: 0; transform: translateY(10px); }
.guide-swap-leave-to { opacity: 0; transform: translateY(-10px); }
</style>
