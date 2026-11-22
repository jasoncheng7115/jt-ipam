<template>
  <div class="nd">
    <!-- 已經是獨立頁籤，不必再放一條寫著同樣字的分隔線；說明留著（它講的是
         「封包從伺服器送出、不是從你的電腦」，那件事不會因為換了位置就不用講） -->
    <div class="nd-note">{{ t("netdiag.section_note") }}</div>

    <!-- 執行來源：預設從伺服器送出；選代理則由該網段的代理在當地執行 -->
    <n-space align="center" :size="10" style="margin-bottom: 10px">
      <span style="font-size: 13px; opacity: .75">{{ t("netdiag.source") }}</span>
      <n-select v-model:value="source" :options="sourceOptions" size="small"
                style="width: 260px" />
      <n-spin v-if="agentBusy" :size="14" />
      <span v-if="agentBusy" style="font-size: 12.5px; opacity: .7">
        {{ t("netdiag.agent_running") }}
      </span>
    </n-space>
    <n-alert v-if="source !== 'server'" type="info" :bordered="false" size="small"
             style="margin-bottom: 10px">
      {{ t("netdiag.agent_note") }}
    </n-alert>
    <n-card v-if="agentOut" size="small" style="margin-bottom: 12px">
      <template #header>
        <span style="font-size: 13.5px">{{ agentOut.agent }} · {{ agentOut.kind }}</span>
      </template>
      <pre class="nd-agent-out">{{ agentOut.text }}</pre>
    </n-card>

    <div class="nd-grid">
      <!-- Ping -->
      <div class="nd-wide">
        <n-card size="small">
          <template #header>
            <CardTitle :icon="LiveIcon" :text="t('netdiag.ping')">
              <n-tag v-if="caps && !caps.ping" size="small" type="warning" :bordered="false">
                {{ t("netdiag.unavailable") }}
              </n-tag>
            </CardTitle>
          </template>
          <n-input v-model:value="ping.targets" type="textarea" :rows="3"
                   :placeholder="t('netdiag.targets_ph')" />
          <div class="nd-hint">{{ t("netdiag.targets_hint", { n: MAX_TARGETS }) }}</div>
          <n-space align="center" :size="14" style="margin-top:10px" :wrap="true">
            <span class="nd-lbl">{{ t("netdiag.count") }}
              <n-input-number v-model:value="ping.count" :min="1" :max="10" size="small" style="width:92px" />
            </span>
            <span class="nd-lbl">{{ t("netdiag.timeout") }}
              <n-input-number v-model:value="ping.timeout" :min="1" :max="10" size="small" style="width:92px" />
            </span>
            <span class="nd-lbl">{{ t("netdiag.concurrency") }}
              <n-input-number v-model:value="ping.concurrency" :min="1" :max="32" size="small" style="width:92px" />
            </span>
            <n-button type="primary" :loading="ping.busy" :disabled="caps ? !caps.ping : false"
                      @click="runPing">
              <template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("netdiag.run") }}
            </n-button>
          </n-space>
          <div v-if="ping.rows.length" class="nd-sum">{{ pingSummary }}</div>
          <n-data-table v-if="ping.rows.length" size="small" :columns="pingCols" :data="ping.rows"
                        :bordered="true" style="margin-top:8px" />
        </n-card>
      </div>

      <!-- 路徑追蹤 -->
      <div class="nd-wide">
        <n-card size="small">
          <template #header>
            <CardTitle :icon="TopologyIcon" :text="t('netdiag.trace')">
              <n-tag v-if="caps && !caps.tracepath && !caps.traceroute" size="small" type="warning" :bordered="false">
                {{ t("netdiag.unavailable") }}
              </n-tag>
            </CardTitle>
          </template>
          <n-space :size="10" align="center" :wrap="true">
            <n-input v-model:value="trace.target" style="width:280px"
                     placeholder="8.8.8.8" @keyup.enter="runTrace" />
            <span class="nd-lbl">{{ t("netdiag.max_hops") }}
              <n-input-number v-model:value="trace.maxHops" :min="1" :max="30" size="small" style="width:92px" />
            </span>
            <!-- 執行中變成「取消」：長作業不能只有轉圈，要給使用者反悔的路。
                 loading 轉圈拿掉——轉圈中的按鈕看起來不可按，跟「可以按取消」矛盾。 -->
            <n-button :type="trace.busy ? 'warning' : 'primary'"
                      :disabled="caps ? !(caps.tracepath || caps.traceroute) : false" @click="runTrace">
              <template #icon>
                <n-icon><CancelIcon v-if="trace.busy" /><SearchIcon v-else /></n-icon>
              </template>
              {{ trace.busy ? t("common.cancel") : t("netdiag.run") }}
            </n-button>
            <!-- 路徑追蹤要跑數十秒（沒有回應的躍點必須等滿逾時才知道它不會回）。
                 不顯示已等待時間的話，畫面只有轉圈，看起來像當掉。 -->
            <span v-if="trace.busy" class="nd-elapsed">
              {{ t("netdiag.trace_running", { s: trace.elapsed }) }}
            </span>
          </n-space>
          <div class="nd-hint">{{ t("netdiag.trace_hint") }}</div>
          <div class="nd-hint">{{ t("netdiag.trace_slow_hint") }}</div>
          <div v-if="trace.res?.tool" class="nd-sum">
            {{ t("netdiag.trace_tool", { tool: trace.res.tool }) }}
            <template v-if="trace.res.path_mtu"> · {{ t("netdiag.path_mtu", { n: trace.res.path_mtu }) }}</template>
            <n-tag v-if="trace.res.reached === false && !trace.busy" size="small" type="warning" :bordered="false"
                   style="margin-left:6px" :title="t('netdiag.not_reached_tip')">{{ t("netdiag.not_reached") }}</n-tag>
            <n-tag v-if="trace.res.truncated" size="small" type="warning" :bordered="false"
                   style="margin-left:8px">{{ t("netdiag.truncated") }}</n-tag>
          </div>
          <n-data-table v-if="trace.res?.hops.length" size="small" :columns="hopCols"
                        :data="trace.res.hops" :bordered="true" style="margin-top:8px" />
        </n-card>
      </div>

      <!-- TCP 埠 -->
      <div class="nd-wide">
        <n-card size="small">
          <template #header><CardTitle :icon="LinkIcon" :text="t('netdiag.tcp')" /></template>
          <n-input v-model:value="tcp.targets" type="textarea" :rows="2"
                   :placeholder="t('netdiag.targets_ph')" />
          <n-space align="center" :size="14" style="margin-top:10px" :wrap="true">
            <span class="nd-lbl">{{ t("netdiag.ports") }}
              <n-input v-model:value="tcp.ports" size="small" style="width:180px"
                       :placeholder="t('netdiag.ports_ph')" />
              <n-tooltip :delay="150" trigger="hover">
                <template #trigger>
                  <n-icon :size="15" class="nd-hint-ic"><InfoIcon /></n-icon>
                </template>
                <div style="max-width:280px;line-height:1.6">{{ t("netdiag.ports_hint") }}</div>
              </n-tooltip>
            </span>
            <span class="nd-lbl">{{ t("netdiag.timeout") }}
              <n-input-number v-model:value="tcp.timeout" :min="1" :max="10" size="small" style="width:92px" />
            </span>
            <n-button type="primary" :loading="tcp.busy" @click="runTcp">
              <template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("netdiag.run") }}
            </n-button>
          </n-space>
          <div class="nd-hint">{{ t("netdiag.tcp_hint") }}</div>
          <n-data-table v-if="tcp.rows.length" size="small" :columns="tcpCols" :data="tcp.rows"
                        :bordered="true" style="margin-top:8px" />
        </n-card>
      </div>
      <!-- UDP 埠 -->
      <div class="nd-wide">
        <n-card size="small">
          <template #header><CardTitle :icon="LinkIcon" :text="t('netdiag.udp')" /></template>
          <n-input v-model:value="udp.targets" type="textarea" :rows="2"
                   :placeholder="t('netdiag.targets_ph')" />
          <n-space align="center" :size="14" style="margin-top:10px" :wrap="true">
            <span class="nd-lbl">{{ t("netdiag.ports") }}
              <n-input v-model:value="udp.ports" size="small" style="width:180px"
                       :placeholder="t('netdiag.ports_ph_udp')" />
              <n-tooltip :delay="150" trigger="hover">
                <template #trigger>
                  <n-icon :size="15" class="nd-hint-ic"><InfoIcon /></n-icon>
                </template>
                <div style="max-width:280px;line-height:1.6">{{ t("netdiag.ports_hint") }}</div>
              </n-tooltip>
            </span>
            <span class="nd-lbl">{{ t("netdiag.timeout") }}
              <n-input-number v-model:value="udp.timeout" :min="1" :max="10" size="small" style="width:92px" />
            </span>
            <n-button type="primary" :loading="udp.busy" @click="runUdp">
              <template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("netdiag.run") }}
            </n-button>
          </n-space>
          <n-alert type="warning" :bordered="false" :show-icon="false" class="nd-udp-note">
            {{ t("netdiag.udp_note") }}
          </n-alert>
          <n-data-table v-if="udp.rows.length" size="small" :columns="udpCols" :data="udp.rows"
                        :bordered="true" style="margin-top:8px" />
        </n-card>
      </div>
      <!-- TLS 憑證檢查 -->
      <div class="nd-wide">
        <n-card size="small">
          <template #header><CardTitle :icon="LockIcon" :text="t('netdiag.tls')" /></template>
          <n-input v-model:value="tls.targets" type="textarea" :rows="2"
                   :placeholder="t('netdiag.targets_ph')" />
          <n-space align="center" :size="14" style="margin-top:10px" :wrap="true">
            <span class="nd-lbl">{{ t("netdiag.port") }}
              <n-input-number v-model:value="tls.port" :min="1" :max="65535" size="small" style="width:110px" />
            </span>
            <span class="nd-lbl">SNI
              <n-input v-model:value="tls.sni" size="small" style="width:200px"
                       :placeholder="t('netdiag.sni_ph')" />
            </span>
            <n-button type="primary" :loading="tls.busy" @click="runTls">
              <template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("netdiag.run") }}
            </n-button>
          </n-space>
          <div class="nd-hint">{{ t("netdiag.tls_hint") }}</div>
          <n-data-table v-if="tls.rows.length" size="small" :columns="tlsCols" :data="tls.rows"
                        :bordered="true" :scroll-x="1050" style="margin-top:8px" />
        </n-card>
      </div>

      <!-- HTTP 檢查 -->
      <div class="nd-wide">
        <n-card size="small">
          <template #header><CardTitle :icon="DnsIcon" :text="t('netdiag.http')" /></template>
          <n-space align="center" :size="14" :wrap="true">
            <n-input v-model:value="http.url" style="width:420px"
                     placeholder="https://example.com" @keyup.enter="runHttp" />
            <span class="nd-lbl">{{ t("netdiag.timeout") }}
              <n-input-number v-model:value="http.timeout" :min="1" :max="30" size="small" style="width:92px" />
            </span>
            <n-button type="primary" :loading="http.busy" @click="runHttp">
              <template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("netdiag.run") }}
            </n-button>
          </n-space>
          <div class="nd-hint">{{ t("netdiag.http_hint") }}</div>
          <template v-if="http.res">
            <div class="nd-sum">
              <span v-if="http.res.status">{{ http.res.status }} · {{ http.res.final_url }}</span>
              <span v-else style="color:#d03050">{{ http.res.error }}</span>
              <span v-if="http.res.elapsed_ms" style="opacity:.6;margin-left:8px">{{ http.res.elapsed_ms }} ms</span>
            </div>
            <n-descriptions v-if="http.res.status" bordered size="small" :column="2"
                            label-placement="left" style="margin-top:8px">
              <n-descriptions-item label="Server">{{ http.res.server || "—" }}</n-descriptions-item>
              <n-descriptions-item label="Content-Type">{{ http.res.content_type || "—" }}</n-descriptions-item>
              <n-descriptions-item label="HSTS" :span="2">{{ http.res.hsts || "—" }}</n-descriptions-item>
            </n-descriptions>
            <template v-if="http.res.redirects.length">
              <div class="nd-hint" style="margin-top:8px">{{ t("netdiag.redirect_chain") }}</div>
              <div v-for="(hop, i) in http.res.redirects" :key="i" class="nd-hop">
                <b>{{ hop.status }}</b> {{ hop.url }} → {{ hop.location }}
              </div>
            </template>
          </template>
        </n-card>
      </div>

      <!-- 批次反向 DNS -->
      <div class="nd-wide">
        <n-card size="small">
          <template #header><CardTitle :icon="DnsIcon" :text="t('netdiag.rdns')" /></template>
          <n-input v-model:value="rdns.targets" type="textarea" :rows="2"
                   :placeholder="t('netdiag.targets_ph')" />
          <n-space align="center" :size="14" style="margin-top:10px" :wrap="true">
            <n-button type="primary" :loading="rdns.busy" @click="runRdns">
              <template #icon><n-icon><SearchIcon /></n-icon></template>{{ t("netdiag.run") }}
            </n-button>
          </n-space>
          <div class="nd-hint">{{ t("netdiag.rdns_hint") }}</div>
          <div v-if="rdns.rows.length" class="nd-sum">
            {{ t("netdiag.rdns_summary", { n: rdns.withPtr, total: rdns.rows.length }) }}
          </div>
          <n-data-table v-if="rdns.rows.length" size="small" :columns="rdnsCols" :data="rdns.rows"
                        :bordered="true" style="margin-top:8px" />
        </n-card>
      </div>
    </div>
  </div>
    <!-- ICMP 被服務沙箱擋住時的排除說明。給的是可以照做的指令，不是「請洽管理員」 -->
    <n-modal v-model:show="icmpHelpShow" preset="card" style="max-width: 720px"
             :title="t('netdiag.icmp_howto_title')">
      <n-alert type="warning" :bordered="false" style="margin-bottom:12px">
        {{ t("netdiag.icmp_howto_why") }}
      </n-alert>
      <p class="howto-p">{{ t("netdiag.icmp_howto_a") }}</p>
      <pre class="howto-pre">sudo sysctl -w net.ipv4.ping_group_range="0 2147483647"
# 開機後保留：
echo 'net.ipv4.ping_group_range = 0 2147483647' | sudo tee /etc/sysctl.d/99-jt-ipam-ping.conf</pre>
      <p class="howto-p">{{ t("netdiag.icmp_howto_b") }}</p>
      <pre class="howto-pre">sudo systemctl edit jt-ipam-backend
# 貼上：
[Service]
AmbientCapabilities=CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_RAW

sudo systemctl daemon-reload &amp;&amp; sudo systemctl restart jt-ipam-backend</pre>
      <p class="howto-note">{{ t("netdiag.icmp_howto_note") }}</p>
    </n-modal>
</template>

<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NCard, NDataTable, NDescriptions, NDescriptionsItem, NModal,
  NIcon, NTooltip, NInput, NInputNumber, NSelect, NSpace, NSpin, NTag, useMessage,
} from "naive-ui";
import { DnsIcon, InfoIcon, LinkIcon, LockIcon, NetDiagIcon as LiveIcon, SearchIcon, TopologyIcon, CancelIcon } from "@/icons";
import { MAX_PORTS, parsePorts } from "@/utils/ports";
import { autoSort } from "@/composables/useTableSort";
import { cmpNatural } from "@/utils/sort";
import CardTitle from "@/components/CardTitle.vue";
import { apiClient, apiErrMsg } from "@/api/client";
import { traceStream } from "@/api/tools";

const MAX_TARGETS = 64;
const { t } = useI18n();
const msg = useMessage();

interface Caps { ping: boolean; tracepath: boolean; traceroute: boolean; tcp: boolean }
interface PingRow {
  target: string; alive: boolean; sent: number; received: number;
  loss_pct: number | null; rtt_avg_ms: number | null; error: string | null;
  error_code?: string | null;
}
interface Hop { hop: number; host: string | null; fqdn?: string | null; rtt_ms: number | null;
                note: string | null; note_code?: string | null }
interface TraceRes { target: string; tool: string; path_mtu: number | null; truncated: boolean; reached?: boolean; hops: Hop[] }
interface TcpRow { target: string; port: number; open: boolean; latency_ms: number | null; error: string | null }
interface TlsRow {
  target: string; port: number; ok: boolean; subject: string | null; issuer: string | null;
  not_after: string | null; days_remaining: number | null; sans: string[];
  self_signed: boolean; trusted: boolean | null; hostname_match: boolean | null;
  tls_version: string | null; error: string | null;
}
interface HttpHopRow { url: string; status: number; location: string | null }
interface HttpRes {
  status: number | null; final_url: string | null; elapsed_ms: number | null;
  server: string | null; content_type: string | null; hsts: string | null;
  redirects: HttpHopRow[]; error: string | null;
}
interface RdnsRow { ip: string; ptr: string | null; error: string | null }
interface UdpRow {
  target: string; port: number; state: "open" | "closed" | "no_reply";
  probe: string; latency_ms: number | null; reply_bytes: number | null; detail: string | null;
}

const caps = ref<Caps | null>(null);
const ping = reactive({ targets: "", count: 3, timeout: 2, concurrency: 8, busy: false, rows: [] as PingRow[] });
// 預設 15：內網目標會提早結束；對不回應的網際網路目標，躍點數直接決定等待時間
const trace = reactive({ target: "", maxHops: 15, busy: false, elapsed: 0, res: null as TraceRes | null });
const tcp = reactive({ targets: "", ports: "443, 22", timeout: 2, busy: false, rows: [] as TcpRow[] });
const udp = reactive({ targets: "", ports: "53, 123", timeout: 3, busy: false, rows: [] as UdpRow[] });
const tls = reactive({ targets: "", port: 443, sni: "", busy: false, rows: [] as TlsRow[] });
const http = reactive({ url: "", timeout: 10, busy: false, res: null as HttpRes | null });
const rdns = reactive({ targets: "", busy: false, withPtr: 0, rows: [] as RdnsRow[] });

const pingSummary = computed(() => {
  const up = ping.rows.filter((r) => r.alive).length;
  return t("netdiag.ping_summary", { up, total: ping.rows.length });
});

// 「通/不通」用顏色點表示，掃一眼就看得出來，不必逐格讀字
function dot(ok: boolean) {
  return h("span", { style: `display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:${ok ? "#18a058" : "#d03050"}` });
}

// ICMP 被沙箱擋住時的「怎麼修」說明
const icmpHelpShow = ref(false);

// 排序比的是畫面上看到的值；空值排最後
const by = <T,>(f: (r: T) => unknown) => (a: T, b: T) => {
  const x = f(a), y = f(b), ex = x == null || x === "", ey = y == null || y === "";
  return ex || ey ? Number(ex) - Number(ey) : cmpNatural(x, y);
};
const upFirst = <T,>(f: (r: T) => boolean | null) => (a: T, b: T) => Number(!f(a)) - Number(!f(b));
const pingCols = computed(() => autoSort<any>([
  { title: t("netdiag.target"), key: "target", width: 190 },
  {
    title: t("netdiag.state"), key: "alive", width: 110, sorter: upFirst((r: PingRow) => r.alive),
    render: (r: PingRow) => h("span", null, [dot(r.alive), r.alive ? t("netdiag.up") : t("netdiag.down")]),
  },
  { title: t("netdiag.loss"), key: "loss_pct", width: 100,
    render: (r: PingRow) => (r.loss_pct === null ? "—" : `${r.loss_pct}%`) },
  { title: t("netdiag.rtt_avg"), key: "rtt_avg_ms", width: 120,
    render: (r: PingRow) => (r.rtt_avg_ms === null ? "—" : `${r.rtt_avg_ms} ms`) },
  {
    title: t("netdiag.note"), key: "error", sorter: by((r: PingRow) => r.error),
    render: (r: PingRow) => {
      if (!r.error) return "";
      // 送不出封包時只講「壞了」沒有用 —— 旁邊放一個點得下去的「怎麼修」
      if (r.error_code !== "icmp_blocked") return r.error;
      return h("span", {}, [
        r.error,
        " ",
        h(NButton, {
          text: true, type: "primary", size: "tiny",
          onClick: () => { icmpHelpShow.value = true; },
        }, { default: () => t("netdiag.icmp_howto_link") }),
      ]);
    },
  },
]));

const hopCols = computed(() => autoSort<any>([
  { title: "#", key: "hop", width: 60 },
  { title: t("netdiag.hop_host"), key: "host", sorter: by((h2: Hop) => h2.fqdn || h2.host),
    // 有反查到名稱就一起顯示（fqdn ＋ 淡色 IP）—— 跟終端機 traceroute 的閱讀體驗一致
    render: (h2: Hop) => h2.host
      ? (h2.fqdn
          ? h("span", null, [h2.fqdn, h("span", { style: "opacity:.55;margin-left:6px" }, `(${h2.host})`)])
          : h2.host)
      : h("span", { style: "opacity:.5" }, t("netdiag.no_reply")) },
  { title: t("netdiag.rtt"), key: "rtt_ms", width: 120,
    render: (h2: Hop) => (h2.rtt_ms === null ? "—" : `${h2.rtt_ms} ms`) },
  // note 可能是路由器回的旗標原文（`!H`…），也可能是我們自己造的句子；後者帶代碼，
  // 要翻譯後再顯示，否則英文與日文介面上會冒出一句中文
  { title: t("netdiag.note"), key: "note", sorter: by((h2: Hop) => (h2.note_code ? t(`errors.${h2.note_code}`) : h2.note)),
    render: (h2: Hop) => (h2.note_code ? t(`errors.${h2.note_code}`) : h2.note) || "" },
]));

const tcpCols = computed(() => autoSort<any>([
  { title: t("netdiag.target"), key: "target", width: 190 },
  { title: t("netdiag.port"), key: "port", width: 90 },
  {
    title: t("netdiag.state"), key: "open", width: 110, sorter: upFirst((r: TcpRow) => r.open),
    render: (r: TcpRow) => h("span", null, [dot(r.open), r.open ? t("netdiag.open") : t("netdiag.closed")]),
  },
  { title: t("netdiag.latency"), key: "latency_ms", width: 120,
    render: (r: TcpRow) => (r.latency_ms === null ? "—" : `${r.latency_ms} ms`) },
  { title: t("netdiag.note"), key: "error", render: (r: TcpRow) => r.error || "" },
]));

const UDP_COLOR: Record<string, string> = { open: "#18a058", closed: "#d03050", no_reply: "#9aa0a6" };
const udpCols = computed(() => autoSort<any>([
  { title: t("netdiag.target"), key: "target", width: 170 },
  { title: t("netdiag.port"), key: "port", width: 80 },
  {
    title: t("netdiag.state"), key: "state", width: 150, sorter: by((r: UdpRow) => t(`netdiag.udp_${r.state}`)),
    render: (r: UdpRow) => h("span", null, [
      h("span", { style: `display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:${UDP_COLOR[r.state]}` }),
      t(`netdiag.udp_${r.state}`),
    ]),
  },
  { title: t("netdiag.probe"), key: "probe", width: 90 },
  { title: t("netdiag.latency"), key: "latency_ms", width: 110,
    render: (r: UdpRow) => (r.latency_ms === null ? "—" : `${r.latency_ms} ms`) },
  { title: t("netdiag.note"), key: "detail", render: (r: UdpRow) => r.detail || "" },
]));

async function runUdp() {
  if (!udp.targets.trim()) { msg.error(t("netdiag.need_target")); return; }
  const parsed = parsePorts(udp.ports);
  if (parsed.invalid.length) { msg.warning(t("netdiag.port_invalid", { s: parsed.invalid.join(", ") })); }
  if (parsed.overflow) { msg.warning(t("netdiag.port_overflow", { n: MAX_PORTS })); }
  const ports = parsed.ports;
  if (!ports.length) { msg.error(t("netdiag.need_port")); return; }
  udp.busy = true;
  try {
    const { data } = await apiClient.post("/api/v1/tools/net/udp", {
      targets: udp.targets, ports, timeout: udp.timeout,
    });
    udp.rows = data.results;
  } catch (e) { msg.error(apiErrMsg(e)); } finally { udp.busy = false; }
}

// 憑證到期天數用顏色分級：30 天內紅、90 天內橘 —— 一眼看出哪張該處理
function daysTag(d: number | null) {
  if (d === null) return "—";
  const color = d < 0 ? "#d03050" : d <= 30 ? "#d03050" : d <= 90 ? "#f0a020" : "#18a058";
  return h("span", { style: `color:${color};font-weight:600` },
    d < 0 ? t("netdiag.expired") : t("netdiag.days_left", { n: d }));
}
function yesNo(v: boolean | null, goodIsTrue = true) {
  if (v === null) return "—";
  const good = goodIsTrue ? v : !v;
  return h("span", { style: `color:${good ? "#18a058" : "#f0a020"}` },
    v ? t("common.yes") : t("common.no"));
}

const tlsCols = computed(() => autoSort<any>([
  { title: t("netdiag.target"), key: "target", width: 160 },
  { title: t("netdiag.tls_subject"), key: "subject", width: 200,
    render: (r: TlsRow) => r.subject || h("span", { style: "color:#d03050" }, r.error || "—"),
    sorter: by((r: TlsRow) => r.subject || r.error) },
  { title: t("netdiag.tls_issuer"), key: "issuer", width: 200 },
  { title: t("netdiag.tls_expiry"), key: "days_remaining", width: 130,
    render: (r: TlsRow) => daysTag(r.days_remaining) },
  { title: t("netdiag.tls_trusted"), key: "trusted", width: 90, render: (r: TlsRow) => yesNo(r.trusted) },
  { title: t("netdiag.tls_name_match"), key: "hostname_match", width: 100,
    render: (r: TlsRow) => yesNo(r.hostname_match) },
  { title: t("netdiag.tls_self_signed"), key: "self_signed", width: 90,
    render: (r: TlsRow) => yesNo(r.self_signed, false) },
  { title: "TLS", key: "tls_version", width: 100 },
]));

const rdnsCols = computed(() => autoSort<any>([
  { title: "IP", key: "ip", width: 200 },
  { title: "PTR", key: "ptr", sorter: by((r: RdnsRow) => r.ptr),
    render: (r: RdnsRow) => r.ptr || h("span", { style: "opacity:.5" }, t("netdiag.no_ptr")) },
  { title: t("netdiag.note"), key: "error", render: (r: RdnsRow) => r.error || "" },
]));

async function runTls() {
  if (!tls.targets.trim()) { msg.error(t("netdiag.need_target")); return; }
  tls.busy = true;
  try {
    const { data } = await apiClient.post("/api/v1/tools/net/tls", {
      targets: tls.targets, port: tls.port, server_name: tls.sni || null,
    });
    tls.rows = data.results;
  } catch (e) { msg.error(apiErrMsg(e)); } finally { tls.busy = false; }
}

async function runHttp() {
  if (!http.url.trim()) { msg.error(t("netdiag.need_target")); return; }
  http.busy = true;
  try {
    const { data } = await apiClient.post("/api/v1/tools/net/http", {
      url: http.url, timeout: http.timeout,
    });
    http.res = data;
  } catch (e) { msg.error(apiErrMsg(e)); } finally { http.busy = false; }
}

async function runRdns() {
  if (source.value !== "server") {
    await runViaAgent("rdns", { targets: rdns.targets });
    return;
  }
  if (!rdns.targets.trim()) { msg.error(t("netdiag.need_target")); return; }
  rdns.busy = true;
  try {
    const { data } = await apiClient.post("/api/v1/tools/net/rdns", { targets: rdns.targets });
    rdns.rows = data.results;
    rdns.withPtr = data.with_ptr;
  } catch (e) { msg.error(apiErrMsg(e)); } finally { rdns.busy = false; }
}

// ── 執行來源：伺服器本身，或某個掃描代理 ──
// 伺服器只看得到自己那一段網路。要確認「客戶站台內部通不通」就得從那個網段裡面打，
// 而掃描代理本來就裝在各網段。代理只由內往外連，所以請求走工作佇列：
// 建立工作 → 代理長輪詢領取 → 當地執行 → 回報 → 這裡輪詢取回。
const source = ref<string>("server");
const agents = ref<{ label: string; value: string }[]>([]);
const agentBusy = ref(false);
const agentOut = ref<{ agent: string; kind: string; text: string } | null>(null);

const sourceOptions = computed(() => [
  { label: t("netdiag.source_server"), value: "server" },
  ...agents.value,
]);

async function loadAgents() {
  try {
    const { data } = await apiClient.get("/api/v1/scan-agents", { params: { page_size: 100 } });
    agents.value = (data.items ?? [])
      .filter((a: any) => a.enabled)
      .map((a: any) => ({ label: `${t("netdiag.source_agent")}：${a.name}`, value: a.id }));
  } catch {
    agents.value = [];      // 非管理員看不到代理清單 → 只留「伺服器」，不是錯誤
  }
}
onMounted(loadAgents);

/** 指派給代理執行並等結果。回傳格式化後的文字（不同探測形狀不同，統一以純文字呈現）。 */
async function runViaAgent(kind: string, params: Record<string, unknown>): Promise<void> {
  agentBusy.value = true;
  agentOut.value = null;
  try {
    const { data } = await apiClient.post("/api/v1/tools/net/agent-probe",
                                          { agent_id: source.value, kind, ...params });
    const jobId = data.job_id;
    const deadline = Date.now() + 90_000;
    for (;;) {
      await new Promise((r) => setTimeout(r, 800));
      const { data: j } = await apiClient.get(`/api/v1/tools/net/agent-probe/${jobId}`);
      if (j.status === "done" || j.status === "failed" || j.status === "expired") {
        const label = sourceOptions.value.find((o) => o.value === source.value)?.label ?? "";
        agentOut.value = {
          agent: label, kind,
          text: j.error ? `${t("netdiag.agent_failed")}：${j.error}`
                        : JSON.stringify(j.result, null, 2),
        };
        return;
      }
      if (Date.now() > deadline) {
        agentOut.value = { agent: "", kind, text: t("netdiag.agent_timeout") };
        return;
      }
    }
  } catch (e) { msg.error(apiErrMsg(e)); } finally { agentBusy.value = false; }
}

async function runPing() {
  if (!ping.targets.trim()) { msg.error(t("netdiag.need_target")); return; }
  if (source.value !== "server") {
    await runViaAgent("ping", { targets: ping.targets, count: ping.count,
                                timeout: ping.timeout });
    return;
  }
  ping.busy = true;
  try {
    const { data } = await apiClient.post("/api/v1/tools/net/ping", {
      targets: ping.targets, count: ping.count,
      timeout: ping.timeout, concurrency: ping.concurrency,
    });
    ping.rows = data.results;
  } catch (e) { msg.error(apiErrMsg(e)); } finally { ping.busy = false; }
}

let traceTimer: ReturnType<typeof setInterval> | null = null;
// 取消用：長作業（15 跳可能 30～60 秒）跑著的時候，按鈕要能取消而不是只能等。
// abort 會讓 fetch 中斷 → 後端 StreamingResponse 收到斷線，netdiag 的 finally 會 kill 子行程。
let traceAbort: AbortController | null = null;

function cancelTrace() {
  traceAbort?.abort();
}

async function runTrace() {
  if (trace.busy) { cancelTrace(); return; }
  if (!trace.target.trim()) { msg.error(t("netdiag.need_target")); return; }
  if (source.value !== "server") {
    await runViaAgent("traceroute", { targets: trace.target, max_hops: trace.maxHops });
    return;
  }
  trace.busy = true;
  trace.elapsed = 0;
  // 邊跑邊長：先給一個空殼，每收到一跳就 push 一列進去
  trace.res = { target: trace.target, tool: "", path_mtu: null, truncated: false, hops: [] };
  traceTimer = setInterval(() => { trace.elapsed += 1; }, 1000);
  traceAbort = new AbortController();
  try {
    await traceStream(trace.target, trace.maxHops, (ev) => {
      if (ev.type === "hop") {
        trace.res!.hops.push({
          hop: ev.hop!, host: ev.host ?? null, fqdn: ev.fqdn ?? null,
          rtt_ms: ev.rtt_ms ?? null, note: ev.note ?? null, note_code: ev.note_code ?? null,
        });
      } else if (ev.type === "done") {
        trace.res!.tool = ev.tool ?? "";
        trace.res!.path_mtu = ev.path_mtu ?? null;
        trace.res!.truncated = !!ev.truncated;
        trace.res!.reached = ev.reached;
      } else if (ev.type === "error") {
        // 後端給代碼，照語系翻譯（以前直接送中文句子）；舊後端沒有代碼才退回 detail
        msg.error(ev.code ? t(`errors.${ev.code}`, ev.params ?? {}) : (ev.detail ?? t("errors.server")));
      }
    }, traceAbort.signal);
  } catch (e: any) {
    // 使用者自己按的取消不是錯誤，跳紅色訊息只會讓人以為壞了
    if (e?.name === "AbortError") msg.info(t("netdiag.trace_cancelled"));
    else msg.error(apiErrMsg(e));
  }
  finally {
    trace.busy = false;
    traceAbort = null;
    if (traceTimer) { clearInterval(traceTimer); traceTimer = null; }
  }
}

async function runTcp() {
  if (!tcp.targets.trim()) { msg.error(t("netdiag.need_target")); return; }
  const parsed = parsePorts(tcp.ports);
  if (parsed.invalid.length) { msg.warning(t("netdiag.port_invalid", { s: parsed.invalid.join(", ") })); }
  if (parsed.overflow) { msg.warning(t("netdiag.port_overflow", { n: MAX_PORTS })); }
  const ports = parsed.ports;
  if (!ports.length) { msg.error(t("netdiag.need_port")); return; }
  if (source.value !== "server") {
    await runViaAgent("tcp", { targets: tcp.targets, ports: ports.join(","),
                               timeout: tcp.timeout });
    return;
  }
  tcp.busy = true;
  try {
    const { data } = await apiClient.post("/api/v1/tools/net/tcp", {
      targets: tcp.targets, ports, timeout: tcp.timeout,
    });
    tcp.rows = data.results;
  } catch (e) { msg.error(apiErrMsg(e)); } finally { tcp.busy = false; }
}

onMounted(async () => {
  // 伺服器上沒裝 tracepath/traceroute 時要先反灰，而不是讓人按了才看到錯誤
  try { caps.value = (await apiClient.get("/api/v1/tools/net/capabilities")).data; } catch { /* 取不到就不反灰 */ }
});
</script>

<style scoped>
.nd-agent-out {
  margin: 0; max-height: 320px; overflow: auto; font-size: 12.5px; line-height: 1.55;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  background: rgba(127, 127, 127, .07); padding: 10px 12px; border-radius: 8px;
  white-space: pre-wrap; word-break: break-word;
}
.howto-p { margin: 10px 0 6px; font-size: 13px; line-height: 1.8; }
.howto-pre {
  margin: 0; padding: 10px 12px; font-size: 12px; line-height: 1.7;
  background: var(--n-color-embedded, rgba(128, 128, 128, .08));
  border-radius: 4px; overflow-x: auto; white-space: pre-wrap; word-break: break-all;
}
.howto-note { margin-top: 12px; font-size: 12px; color: var(--n-text-color-disabled); line-height: 1.8; }
.nd-div { font-size: 13px; font-weight: 600; }
/* 與上方計算工具用同一組格線參數（2 欄、12px、820px 收合），整頁看起來才一致 */
.nd-grid { display: grid; grid-template-columns: 1fr; gap: 12px; align-items: start; }
.nd-wide { grid-column: 1 / -1; }
/* 表格儲存格不要從字中間斷行 —— "Connection refused" 曾被折成 "Connectio n refused" */
.nd :deep(.n-data-table td) { word-break: keep-all; overflow-wrap: anywhere; }
.nd-note { font-size: 12.5px; line-height: 1.7; color: var(--n-text-color-disabled); margin-bottom: 14px; }
.nd-hint { margin-top: 6px; font-size: 12px; line-height: 1.6; color: var(--n-text-color-disabled); }
.nd-elapsed { font-size:12px; opacity:.65; font-variant-numeric: tabular-nums }
.nd-hint-ic { opacity:.55; cursor:help; margin-left:4px; vertical-align:middle }
.nd-lbl { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; }
.nd-udp-note { margin-top: 10px; font-size: 12px; line-height: 1.7; }
.nd-hop { font-size: 12.5px; line-height: 1.7; opacity: .85; }
.nd-sum { margin-top: 10px; font-size: 13px; font-weight: 600; }
</style>
