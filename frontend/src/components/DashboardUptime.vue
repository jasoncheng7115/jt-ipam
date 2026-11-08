<template>
  <n-card size="small" class="dash-uptime">
    <template #header>
      <CardTitle :icon="IPChangesIcon" :text="t('uptime.dash_title')">
        <n-tag v-if="ids.length" size="small" round :bordered="false">
          {{ ids.length }} / {{ MAX }}
        </n-tag>
      </CardTitle>
    </template>
    <!-- 控制列：自標題列搬到內文最上方 -->
    <n-space align="center" justify="end" style="margin-bottom: 10px">
      <n-button size="small" @click="openPicker">
        <template #icon><n-icon :component="SettingsIcon" /></template>
        {{ t("uptime.dash_edit") }}
      </n-button>
    </n-space>

    <n-empty v-if="!ids.length" :description="t('uptime.dash_empty')" size="small">
      <template #extra>
        <n-button size="small" @click="openPicker"><template #icon><n-icon><EditIcon /></n-icon></template>{{ t("uptime.dash_pick") }}</n-button>
      </template>
    </n-empty>

    <n-spin v-else :show="loading">
      <div class="rows">
        <div v-for="r in sortedRows" :key="r.ip_id" class="row">
          <a class="row-label" :title="r.hostname ? `${r.ip} — ${r.hostname}` : r.ip"
             @click="goIp(r.ip_id)">
            <span class="row-ip">{{ r.ip }}</span>
            <span v-if="r.hostname" class="row-host">{{ r.hostname }}</span>
          </a>
          <div class="row-bar">
            <n-tooltip v-for="d in r.items" :key="d.date" trigger="hover" :delay="80">
              <template #trigger>
                <span class="bar" :class="`bar-${d.status}`" />
              </template>
              <div>
                <div style="font-weight:600">{{ d.date }}</div>
                <div>{{ t(`uptime.st_${d.status}`) }}</div>
              </div>
            </n-tooltip>
          </div>
          <n-tooltip v-if="r.uptime_pct !== null" trigger="hover" :delay="80">
            <template #trigger>
              <span class="row-pct">{{ Math.round(r.uptime_pct) }}%</span>
            </template>
            <!-- 整數看趨勢、小數看差別：99% 跟 99.4% 在畫面上要能分得出來，
                 但預設就秀三位小數只會讓一整欄變成雜訊 -->
            <div style="font-variant-numeric:tabular-nums">{{ pct3(r.uptime_pct) }}%</div>
          </n-tooltip>
          <span v-else class="row-pct">—</span>
        </div>
      </div>
      <div class="rows-foot">
        <span>{{ t("uptime.days_ago", { n: DAYS }) }}</span>
        <span>{{ t("uptime.today") }}</span>
      </div>
    </n-spin>

    <!-- 選 IP -->
    <!-- 視窗矮（筆電＋字型放大）時內容在裡面捲動，儲存 / 取消一定看得到（客戶 11-03：按鈕被擠出畫面） -->
    <n-modal
      v-model:show="pickerShow"
      preset="card"
      style="width: 640px; max-width: calc(100vw - 32px)"
      :content-style="{ maxHeight: 'calc(100vh - 220px)', overflowY: 'auto' }"
      :title="t('uptime.dash_edit')"
    >
      <n-alert type="info" :show-icon="true" style="margin-bottom: 12px">
        {{ t("uptime.dash_hint", { n: MAX }) }}
      </n-alert>
      <n-select
        v-model:value="draft"
        multiple
        filterable
        clearable
        :options="selectOptions"
        :loading="optLoading"
        max-tag-count="responsive"
        :render-tag="renderTag"
        :theme-overrides="MENU_FIT"
        data-testid="uptime-picker"
        :placeholder="t('uptime.dash_ph')"
        @search="onSearch"
      />
      <div v-if="draft.length > MAX" class="over">
        {{ t("uptime.dash_over", { n: MAX }) }}
      </div>

      <!-- 排序：追蹤十幾個位址時，順序決定了「一眼看到什麼」——
           想先看最差的就用可用率遞增，想照網段掃就用 IP -->
      <div class="sort-row">
        <span class="sort-label">{{ t("uptime.sort_by") }}</span>
        <n-radio-group v-model:value="draftSortKey" size="small">
          <n-radio-button value="ip">
            <span class="btn-ic"><n-icon :component="AddressesIcon" />IP</span>
          </n-radio-button>
          <n-radio-button value="hostname">
            <span class="btn-ic"><n-icon :component="HostnameIcon" />{{ t("addresses.hostname") }}</span>
          </n-radio-button>
          <n-radio-button value="sla">
            <span class="btn-ic"><n-icon :component="SlaIcon" />{{ t("uptime.sort_sla") }}</span>
          </n-radio-button>
        </n-radio-group>
        <n-radio-group v-model:value="draftSortDir" size="small">
          <n-radio-button value="asc">
            <span class="btn-ic"><n-icon :component="SortAscIcon" />{{ t("uptime.sort_asc") }}</span>
          </n-radio-button>
          <n-radio-button value="desc">
            <span class="btn-ic"><n-icon :component="SortDescIcon" />{{ t("uptime.sort_desc") }}</span>
          </n-radio-button>
        </n-radio-group>
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button data-testid="uptime-cancel" @click="pickerShow = false">
            <template #icon><n-icon :component="CancelIcon" /></template>{{ t("common.cancel") }}
          </n-button>
          <n-button type="primary" :disabled="draft.length > MAX" data-testid="uptime-save" @click="save">
            <template #icon><n-icon :component="SaveIcon" /></template>{{ t("common.save") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </n-card>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import {
  NAlert, NButton, NCard, NEmpty, NIcon, NModal, NRadioButton, NRadioGroup,
  NSelect, NSpace, NSpin, NTag, NTooltip,
} from "naive-ui";
import CardTitle from "@/components/CardTitle.vue";
import {
  AddressesIcon, CancelIcon, HostnameIcon, IPChangesIcon, SaveIcon, SettingsIcon,
  SlaIcon, SortAscIcon, SortDescIcon, EditIcon,
} from "@/icons";
import { apiClient } from "@/api/client";
import { listAddresses } from "@/api/addresses";
import { usePinned } from "@/composables/usePinned";

const MAX = 30;          // 儀表板區塊上限
const DAYS = 90;

const { t } = useI18n();
const router = useRouter();
// 存在 user_preferences.pinned["uptime_ips"]（既有的通用釘選機制，免加欄位）
const { ids, toggle } = usePinned("uptime_ips");

interface Day { date: string; status: "up" | "partial" | "down" | "unknown" }
interface Row {
  ip_id: string; ip: string; hostname: string | null;
  items: Day[]; uptime_pct: number | null;
}

const rows = ref<Row[]>([]);
const loading = ref(false);
const pickerShow = ref(false);
const draft = ref<string[]>([]);
const ipOptions = ref<{ label: string; value: string }[]>([]);
const optLoading = ref(false);
// id → 顯示文字。搜尋會把 ipOptions 整個換掉，已選那筆的 option 就不見了；
// n-select 找不到 option 時會把 value（UUID）當標籤畫出來 —— 所以要自己留一份。
const labelCache = ref<Record<string, string>>({});

/** 百分比的小數三位（滑過去才看，預設只顯示整數）。 */
function pct3(v: number): string {
  return v.toFixed(3);
}

function ipLabel(ip: string, hostname: string | null): string {
  return hostname ? `${ip} — ${hostname}` : ip;
}

/** 下拉清單最高佔視窗的 38%：放大字型時清單曾超出畫面，下面的位址選不到（客戶 11-03） */
const MENU_FIT = { peers: { InternalSelectMenu: { height: "min(260px, 38vh)" } } };

/** 搜尋的排序：完全符合的位址排第一（打 198.51.100.1 時它不會埋在 .10、.100… 裡），其次是開頭符合，再照數字 */
function ipKey(ip: string): number[] {
  return ip.split(/[.:]/).map((x) => parseInt(x, ip.includes(":") ? 16 : 10) || 0);
}
function rankFor(q: string) {
  const s = q.trim().toLowerCase();
  return (label: string): number => {
    const ip = label.split(" — ")[0].toLowerCase();
    if (!s) return 2;
    if (ip === s) return 0;
    return ip.startsWith(s) ? 1 : 2;
  };
}
const lastQuery = ref("");

/** 下拉清單只列還沒選的（已選的在上面是標籤，可以按 × 拿掉）。已選的也列出來時，清單一打開就捲到
 * 第一個已選的位置，完全符合的那一筆被捲到看不到（客戶 11-03：「打 198.51.100.1 選不到」）。
 * 標籤文字由 renderTag 從 labelCache 取，不靠選項清單。 */
const selectOptions = computed(() => {
  const picked = new Set(draft.value);
  return ipOptions.value.filter((o) => !picked.has(o.value));
});
function renderTag({ option, handleClose }: { option: { value?: string | number }; handleClose: () => void }) {
  const id = String(option.value ?? "");
  return h(NTag, { size: "small", closable: true, onClose: (e: MouseEvent) => { e.stopPropagation(); handleClose(); } },
           { default: () => labelCache.value[id] ?? t("uptime.dash_ip_gone") });
}

async function load() {
  if (!ids.value.length) { rows.value = []; return; }
  loading.value = true;
  try {
    const { data } = await apiClient.post("/api/v1/addresses/uptime/batch", {
      ip_ids: ids.value.slice(0, MAX), days: DAYS,
    });
    rows.value = data.items ?? [];
    // 批次結果本身就帶 ip/hostname → 已追蹤的那些不必再打一次 API 才有標籤
    for (const r of rows.value) labelCache.value[r.ip_id] = ipLabel(r.ip, r.hostname);
  } catch {
    rows.value = [];
  } finally {
    loading.value = false;
  }
}

async function loadOptions(q?: string) {
  optLoading.value = true;
  try {
    const res = await listAddresses({ page: 1, pageSize: 200, q: q || undefined });
    lastQuery.value = q ?? "";
    const rank = rankFor(lastQuery.value);
    ipOptions.value = res.items.map((a: any) => {
      const label = ipLabel(a.ip, a.hostname);
      labelCache.value[a.id] = label;
      return { label, value: a.id, ip: String(a.ip) };
    }).sort((x: { label: string; ip: string }, y: { label: string; ip: string }) => {
      const r = rank(x.label) - rank(y.label);
      if (r) return r;
      const a = ipKey(x.ip), b = ipKey(y.ip);
      for (let i = 0; i < Math.max(a.length, b.length); i++) if ((a[i] ?? 0) !== (b[i] ?? 0)) return (a[i] ?? 0) - (b[i] ?? 0);
      return 0;
    }).map(({ label, value }: { label: string; value: string }) => ({ label, value }));
  } catch {
    ipOptions.value = [];
  } finally {
    optLoading.value = false;
  }
}

let searchTimer: ReturnType<typeof setTimeout> | undefined;
function onSearch(q: string) {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => void loadOptions(q), 250);
}

function openPicker() {
  draftSortKey.value = sortKey.value;
  draftSortDir.value = sortDir.value;
  draft.value = [...ids.value];
  pickerShow.value = true;
  void loadOptions();
}

// 排序偏好存在 user_preferences.table_columns（既有的表格顯示偏好袋，免加欄位）
type SortKey = "ip" | "hostname" | "sla";
type SortDir = "asc" | "desc";
const sortKey = ref<SortKey>("ip");
const sortDir = ref<SortDir>("asc");
const draftSortKey = ref<SortKey>("ip");
const draftSortDir = ref<SortDir>("asc");

const SORT_PREF_KEY = "uptime_sort";

async function loadSort() {
  try {
    const { data } = await apiClient.get<{ table_columns: Record<string, unknown> | null }>(
      "/api/v1/me/preferences",
    );
    const v = data?.table_columns?.[SORT_PREF_KEY] as { key?: SortKey; dir?: SortDir } | undefined;
    if (v?.key) sortKey.value = v.key;
    if (v?.dir) sortDir.value = v.dir;
  } catch { /* 沒登入 / 失敗 → 用預設 */ }
}

async function saveSort() {
  try {
    const { data } = await apiClient.get<{ table_columns: Record<string, unknown> | null }>(
      "/api/v1/me/preferences",
    );
    // 讀回整包再寫：只送自己這一格會把別的表格的欄位偏好整個蓋掉
    const bag = { ...(data?.table_columns ?? {}) };
    bag[SORT_PREF_KEY] = { key: sortKey.value, dir: sortDir.value };
    await apiClient.patch("/api/v1/me/preferences", { table_columns: bag });
  } catch { /* 後端失敗不回滾 UI */ }
}

/** IPv4 轉可比較的數值；非 IPv4 退回字串比較。 */
function ipOrder(ip: string): number {
  const p = ip.split(".");
  if (p.length !== 4) return Number.NaN;
  return p.reduce((acc, x) => acc * 256 + (Number(x) || 0), 0);
}

const sortedRows = computed(() => {
  const dir = sortDir.value === "asc" ? 1 : -1;
  return [...rows.value].sort((a, b) => {
    if (sortKey.value === "sla") {
      // 沒有資料的排最後（不管遞增遞減）—— 它不是「可用率 0」，是「不知道」
      const av = a.uptime_pct, bv = b.uptime_pct;
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      return (av - bv) * dir;
    }
    if (sortKey.value === "hostname") {
      return (a.hostname || "").localeCompare(b.hostname || "", undefined, { numeric: true }) * dir;
    }
    const an = ipOrder(a.ip), bn = ipOrder(b.ip);
    if (Number.isNaN(an) || Number.isNaN(bn)) {
      return a.ip.localeCompare(b.ip, undefined, { numeric: true }) * dir;
    }
    return (an - bn) * dir;
  });
});

function save() {
  // usePinned 只提供 toggle → 用差集把它推到 draft 的內容
  const before = new Set(ids.value);
  const after = new Set(draft.value.slice(0, MAX));
  for (const id of before) if (!after.has(id)) toggle(id);
  for (const id of after) if (!before.has(id)) toggle(id);
  sortKey.value = draftSortKey.value;
  sortDir.value = draftSortDir.value;
  void saveSort();
  pickerShow.value = false;
  void load();
}

function goIp(id: string) {
  router.push({ name: "address-detail", params: { id } }).catch(() => {});
}

onMounted(() => { void loadSort(); void load(); });
watch(ids, load, { deep: true });
</script>

<style scoped>
.dash-uptime { width: 100%; }
.rows { display: flex; flex-direction: column; gap: 6px; }
/* 標籤固定寬、長條吃掉剩下全部 → 每一列的長條左右對齊 */
.row { display: grid; grid-template-columns: 210px minmax(0, 1fr) 46px; align-items: center; gap: 10px; }
/* IP 與主機名稱同一行、基線對齊：兩行式在每列高度不一致時（有些沒有主機名稱）
   會讓整欄看起來參差不齊 */
.row-label {
  display: flex; align-items: baseline; gap: 6px; min-width: 0;
  cursor: pointer; text-decoration: none; line-height: 1.4;
}
.row-label:hover .row-ip { color: #18a058; }
.row-ip {
  /* 固定寬度讓主機名稱在每一列都從同一個位置開始 —— 位址長度不一（192.168.1.3 vs
     192.168.1.111），只靠 gap 的話主機名稱那一欄會參差不齊。放不下的（IPv6）才撐開。 */
  flex: 0 0 auto; min-width: 112px;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 12.5px; font-weight: 600; color: var(--n-text-color);
  font-variant-numeric: tabular-nums;
}
.row-host {
  flex: 1 1 auto; min-width: 0; font-size: 11.5px; color: var(--n-text-color-disabled);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.row-bar { display: flex; gap: 1px; height: 22px; }
.bar { flex: 1 1 0; min-width: 0; border-radius: 0; cursor: pointer; transition: opacity .12s ease; }
.bar:hover { opacity: .72; }
.bar-up { background: #18a058; }
.bar-partial { background: #f0a020; }
.bar-down { background: #d03050; }
.bar-unknown { background: var(--n-border-color); }
.row-pct {
  font-size: 12px; font-weight: 600; text-align: right; color: var(--n-text-color);
  font-variant-numeric: tabular-nums; cursor: default; display: block;
}
.rows-foot {
  display: flex; justify-content: space-between; margin-top: 8px;
  font-size: 12px; color: var(--n-text-color-disabled);
  /* 對齊長條區（跳過左側標籤欄與右側百分比欄） */
  padding-left: 220px; padding-right: 56px;
}
.over { margin-top: 8px; font-size: 12px; color: #d03050; }
.sort-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 14px; }
.sort-label { font-size: 13px; color: var(--n-text-color-3); }
/* icon 與文字在按鈕內同一基線，間距才不會看起來一邊寬一邊窄 */
.btn-ic { display: inline-flex; align-items: center; gap: 5px; }
</style>
