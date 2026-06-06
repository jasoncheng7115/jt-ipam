<script setup lang="ts">
/**
 * Graylog DSV 對照表（僅管理員）— 從「系統設定」拆出的獨立頁。
 * 上半：端點設定（啟用 / 路徑 / 格式 / token / 複製網址）。
 * 下半：Graylog 串接教學（Lookup Table 三層、Extractor、Pipeline、只對內網 IP 查）。
 */
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { NCard, NSpace, NIcon, NInput, NSelect, NSwitch, NButton, NAlert, useMessage } from "naive-ui";
import { ExportIcon, SaveIcon, RefreshIcon, CopyIcon } from "@/icons";
import { getGraylogDsv, putGraylogDsv, type GraylogDsv } from "@/api/system";

const { t } = useI18n();
const msg = useMessage();

const dsv = ref<GraylogDsv>({ enabled: false, fmt: "csv", path: "ip-fqdn", token: "" });
const saving = ref(false);
const fmtOpts = [{ label: "CSV (,)", value: "csv" }, { label: "TSV (Tab)", value: "tsv" }];

const dsvUrl = computed(() =>
  dsv.value.token ? `${location.origin}/api/v1/lookup/${dsv.value.path}?token=${dsv.value.token}` : "");
const DSV_HTTP_PORT = 8088;
const dsvUrlHttp = computed(() =>
  dsv.value.token
    ? `http://${location.hostname}:${DSV_HTTP_PORT}/api/v1/lookup/${dsv.value.path}?token=${dsv.value.token}`
    : "");

// 教學裡示範用的網址：優先顯示實際端點，未啟用時用佔位
const sampleUrl = computed(() => dsvUrl.value || "https://<jt-ipam>/api/v1/lookup/ip-fqdn?token=<token>");
const sep = computed(() => (dsv.value.fmt === "tsv" ? "\\t" : ","));

async function load() { try { dsv.value = await getGraylogDsv(); } catch { /* ignore */ } }
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
  if (text) { void navigator.clipboard.writeText(text); msg.success(t("common.ok")); }
}

// Pipeline rule 範例（程式碼不翻譯）；路徑名稱跟著設定走
const pipelineRule = computed(() => `rule "enrich src_ip -> hostname (LAN only)"
when
    has_field("src_ip") &&
    (
        cidr_match("10.0.0.0/8",     to_ip($message.src_ip)) ||
        cidr_match("172.16.0.0/12",  to_ip($message.src_ip)) ||
        cidr_match("192.168.0.0/16", to_ip($message.src_ip))
    )
then
    let h = lookup_value("${dsv.value.path || "ip-fqdn"}", to_string($message.src_ip));
    set_field("src_hostname", h);
end`);

onMounted(() => { void load(); });
</script>

<template>
  <div class="gd-wrap">
    <!-- ── 端點設定 ── -->
    <n-card>
      <template #header>
        <n-space align="center" :wrap-item="false">
          <n-icon :size="20"><ExportIcon /></n-icon>
          <span>{{ t("settings.system.graylog_title") }}</span>
        </n-space>
      </template>
      <p class="gd-intro">{{ t("settings.system.graylog_page_intro") }}</p>

      <div class="gd-row">
        <n-switch v-model:value="dsv.enabled" @update:value="() => save()" />
        <span class="gd-switch-label">{{ t("settings.system.graylog_enable") }}</span>
      </div>

      <div class="gd-grid">
        <div class="fld">
          <label>{{ t("settings.system.graylog_path") }}</label>
          <n-input v-model:value="dsv.path" placeholder="ip-fqdn" />
        </div>
        <div class="fld">
          <label>{{ t("settings.system.graylog_format") }}</label>
          <n-select v-model:value="dsv.fmt" :options="fmtOpts" />
        </div>
      </div>

      <n-space style="margin-top:12px">
        <n-button type="primary" size="small" :loading="saving" @click="() => save()">
          <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
        </n-button>
        <n-button size="small" :loading="saving" @click="() => save(true)">
          <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("settings.system.graylog_regen") }}
        </n-button>
      </n-space>

      <div v-if="dsvUrl" class="fld" style="margin-top:14px">
        <label>{{ t("settings.system.graylog_url") }}</label>
        <div class="gd-url">
          <n-input :value="dsvUrl" readonly style="flex:1" />
          <n-button size="small" type="primary" ghost @click="copy(dsvUrl)">
            <template #icon><n-icon><CopyIcon /></n-icon></template>{{ t("settings.system.graylog_copy") }}
          </n-button>
        </div>
      </div>
      <div v-if="dsvUrlHttp" class="fld" style="margin-top:10px">
        <label>{{ t("settings.system.graylog_url_http") }}</label>
        <div class="gd-url">
          <n-input :value="dsvUrlHttp" readonly style="flex:1" />
          <n-button size="small" type="primary" ghost @click="copy(dsvUrlHttp)">
            <template #icon><n-icon><CopyIcon /></n-icon></template>{{ t("settings.system.graylog_copy") }}
          </n-button>
        </div>
        <div class="hint" style="margin-top:4px">{{ t("settings.system.graylog_url_http_hint") }}</div>
      </div>
      <div class="hint" style="line-height:1.6; margin-top:10px">{{ t("settings.system.graylog_hint") }}</div>
    </n-card>

    <!-- ── Graylog 串接教學 ── -->
    <n-card style="margin-top:16px">
      <template #header>
        <span>{{ t("settings.system.graylog_guide_card") }}</span>
      </template>

      <n-alert type="info" :show-icon="true" style="margin-bottom:16px">
        {{ t("settings.system.graylog_g_how") }}
      </n-alert>

      <!-- 步驟 1：Lookup Table 三層 -->
      <h4 class="gd-h">{{ t("settings.system.graylog_g_lt_title") }}</h4>
      <p class="gd-p">{{ t("settings.system.graylog_g_lt_intro") }}</p>

      <div class="gd-sub">① Data Adapter — <code>DSV File from HTTP</code></div>
      <table class="gd-tbl">
        <tr><td>File / Download URL</td><td><code>{{ sampleUrl }}</code></td></tr>
        <tr><td>Separator</td><td><code>{{ sep }}</code> （CSV=逗號、TSV=Tab）</td></tr>
        <tr><td>Quote character</td><td><code>"</code>（TSV 可留空）</td></tr>
        <tr><td>Key column</td><td><code>1</code>（IP）</td></tr>
        <tr><td>Value column</td><td><code>2</code>（hostname / FQDN）</td></tr>
        <tr><td>Check interval</td><td><code>60</code> 秒（多久重抓一次）</td></tr>
      </table>

      <div class="gd-sub">② Cache — <code>Node-local, in-memory cache</code></div>
      <table class="gd-tbl">
        <tr><td>Maximum entries</td><td><code>100000</code></td></tr>
        <tr><td>Expire after write</td><td><code>60s</code></td></tr>
      </table>

      <div class="gd-sub">③ Lookup Table</div>
      <table class="gd-tbl">
        <tr><td>Data Adapter</td><td>{{ t("settings.system.graylog_g_pick_adapter") }}</td></tr>
        <tr><td>Cache</td><td>{{ t("settings.system.graylog_g_pick_cache") }}</td></tr>
        <tr><td>Default single value</td><td>{{ t("settings.system.graylog_g_leave_empty") }}</td></tr>
      </table>

      <!-- 步驟 2A：Extractor -->
      <h4 class="gd-h">{{ t("settings.system.graylog_g_ex_title") }}</h4>
      <p class="gd-p">{{ t("settings.system.graylog_g_ex") }}</p>

      <!-- 步驟 2B：Pipeline -->
      <h4 class="gd-h">{{ t("settings.system.graylog_g_pl_title") }}</h4>
      <p class="gd-p">{{ t("settings.system.graylog_g_pl") }}</p>

      <!-- 步驟 3：只對內網 IP -->
      <h4 class="gd-h">{{ t("settings.system.graylog_g_lan_title") }}</h4>
      <p class="gd-p">{{ t("settings.system.graylog_g_lan") }}</p>
      <div class="gd-code-head">
        <span>Pipeline rule</span>
        <n-button size="tiny" quaternary @click="copy(pipelineRule)">
          <template #icon><n-icon><CopyIcon /></n-icon></template>{{ t("settings.system.graylog_copy") }}
        </n-button>
      </div>
      <pre class="gd-code">{{ pipelineRule }}</pre>
      <p class="gd-note">{{ t("settings.system.graylog_g_note") }}</p>
    </n-card>
  </div>
</template>

<style scoped>
.gd-wrap { max-width: 820px; }
.gd-intro { font-size: 13px; opacity: .75; margin: 0 0 14px; line-height: 1.6; }
.gd-row { display: flex; align-items: center; gap: 8px; }
.gd-switch-label { font-size: 13px; }
.gd-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
.fld label { display: block; font-size: 12px; opacity: .8; margin-bottom: 4px; }
.gd-url { display: flex; gap: 8px; align-items: center; }
.hint { font-size: 11px; opacity: .65; }
.gd-h { font-size: 14px; margin: 22px 0 6px; padding-top: 14px; border-top: 1px solid var(--n-border-color, rgba(128,128,128,.15)); }
.gd-h:first-of-type { border-top: none; padding-top: 0; }
.gd-p { font-size: 13px; line-height: 1.7; opacity: .85; margin: 0 0 8px; }
.gd-sub { font-size: 13px; font-weight: 600; margin: 12px 0 6px; }
.gd-tbl, .gd-tbl tr, .gd-tbl td,
.gd-tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; margin-bottom: 6px; }
.gd-tbl td { padding: 5px 8px; border: 1px solid rgba(128,128,128,.18); vertical-align: top; }
.gd-tbl td:first-child { width: 200px; opacity: .8; white-space: nowrap; }
code { background: rgba(128,128,128,.14); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
.gd-code-head { display: flex; align-items: center; justify-content: space-between; margin-top: 6px; }
.gd-code { background: rgba(128,128,128,.1); border: 1px solid rgba(128,128,128,.18); border-radius: 6px;
  padding: 12px; font-size: 12px; line-height: 1.5; overflow-x: auto; white-space: pre; margin: 4px 0 8px; }
.gd-note { font-size: 12px; opacity: .65; line-height: 1.6; }
</style>
