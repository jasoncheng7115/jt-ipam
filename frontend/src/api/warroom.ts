/**
 * 戰情室（大螢幕看板）的 API。
 *
 * 兩種身分：
 * - 一般登入：走共用的 apiClient（帶存取權杖、401 會嘗試換新）
 * - 電視用的顯示權杖：獨立的 axios（只帶 X-Warroom-Token，不碰登入的權杖；401 不跳登入頁，畫面自己說明）
 */
import axios, { type AxiosInstance } from "axios";
import { apiClient } from "./client";

const DISPLAY_KEY = "jtipam.warroom.display";

export type CellState = string;

export interface WarroomConfig {
  pages: string[];
  all_pages: string[];
  ready_pages: string[];
  dwell_seconds: number;
  max_subpages: number;
  group_by: "location" | "section" | "customer";
  language: string;
  reduce_motion: boolean;
  /** 資訊展示模式（11-03）：只呈現現況與數量，不顯示告警、異常、離線與問題 */
  info_mode?: boolean;
  /** 換頁過場：slide／fade／zoom／rise／wipe／flip */
  transition?: string;
  /** 大數字字體（warroomFonts.ts 的鍵） */
  number_font?: string;
  thresholds: { warn: number; crit: number };
  /** 12 AI 情報的顯示設定（停留秒數、版面、配色、打字速度…） */
  intel?: IntelDisplay;
  via: "session" | "display";
  is_admin: boolean;
  /** 顯示範圍（null＝全部）：頁首顯示前幾個名稱 */
  scope: { names: string[]; more: number } | null;
}

// ── 12 AI 情報（2026-10-26） ──
export interface IntelColors { bg: string; text: string; secondary: string; accent: string; warn: string; critical: string }
export interface IntelDisplay {
  interval_minutes: number; dwell_seconds: number; layout: "three_panel" | "focus"; playback: "rotate" | "pinned";
  item_limit: number; domains: string[]; speed: "slow" | "normal" | "fast"; rhythm: boolean; cursor: boolean;
  effects: "clear" | "standard" | "cinema"; scanlines: "off" | "low" | "medium"; noise: "off" | "low";
  palette: "phosphor" | "amber" | "ice" | "custom"; colors: IntelColors; mask_ips: boolean;
  warning_enabled: boolean; warning_breathe: boolean;
}
export interface IntelSettings extends IntelDisplay { background: boolean; warning_categories: string[] }
/** 一則播報：模型寫的有 text；系統摘要只有 code＋params（文字依語系組） */
export interface IntelItem {
  id: string; fact_ids: string[]; category: string; state: string; subjects?: string[];
  text?: string; code?: string; params?: Record<string, any>;
}
export interface IntelSource { id: string; name: string; kind: string; label: string; last_success_at: string | null;
  state: "fresh" | "stale" | "failed" | "partial" | "not_configured" | "unknown"; count?: number; stale?: number }
export interface IntelPinned { kind: "anomaly" | "source"; category?: string; label: string; ip?: string | null;
  state: string; since: string | null; id: string }
export interface IntelBatch {
  id: string; status: "ready" | "empty"; origin: "llm" | "template"; model: string | null; error_code: string | null;
  baseline: boolean; snapshot_at: string; generated_at: string; checked_at: string; count: number;
  pinned: IntelPinned[]; sources: IntelSource[]; stats: Record<string, number | null>; items?: IntelItem[];
}
export interface IntelState {
  ai_enabled: boolean; generating: boolean; next_due_at: string | null; last_error_code: string | null;
  last_attempt_at: string | null; batch: IntelBatch | null; stale: boolean; outdated: boolean;
}
export interface IntelEvent { id: string; category: string; label: string; ip: string | null; first_seen_at: string;
  baseline: boolean; resolved_at: string | null; state: "open" | "resolved" | "unknown";
  /** 在哪裡（10-26）：VM 的節點，或實體裝置接在哪台交換器的哪個埠；查不到就沒有 */
  where?: { kind: "vm"; vm: string; node: string | null; cluster: string | null; platform: string | null }
    | { kind: "port"; switch: string; port: string; vlan: number | null };
  mac?: string; vendor?: string }
export interface IntelEvents { state: "ok" | "off" | "forbidden"; items: IntelEvent[]; checked_at?: string | null;
  detection_stale?: boolean }

/** rev：手上這一批的 id（同一批就不帶正文）；meta：只要資訊不要正文（播別的主題時的心跳） */
export async function getIntel(rev?: string | null, meta = false): Promise<IntelState> {
  const { data } = await client().get<IntelState>("/api/v1/warroom/intel",
                                                  { params: { ...(rev ? { rev } : {}), ...(meta ? { meta: true } : {}) } });
  return data;
}
export async function getIntelEvents(): Promise<IntelEvents> {
  const { data } = await client().get<IntelEvents>("/api/v1/warroom/intel/events");
  return data;
}
export async function generateIntelNow(): Promise<{ started: boolean }> {
  const { data } = await apiClient.post("/api/v1/system/warroom/intel/generate");
  return data;
}

export interface Section<T = Record<string, unknown>> { state: "ok" | "empty" | "forbidden" | "disabled"; [k: string]: unknown; items?: T[] }

export interface PageData {
  key: string;
  data_at: string;
  page: number;
  pages: number;
  total?: number;
  /** 數字；null＝無權限；字串「—」＝沒有可用的結果（例如 AI 巡檢未執行或失敗），不是 0 */
  metrics: Record<string, number | string | null> | null;
  [k: string]: unknown;
}

/** 這台電視的顯示權杖（網址帶 ?display= 時存起來，網址列隨即清掉）。
 *  存 localStorage 而不是 sessionStorage：電視每天關機，隔天瀏覽器還原的是已經清掉權杖的網址，
 *  sessionStorage 又跟著瀏覽器關閉被清空，就會掉到登入頁（10-23 使用者問到）。權杖只能讀戰情室、
 *  隨時可撤銷。失效時不清掉：電視會一直顯示「權杖無效」而不是掉到登入頁，用新的網址開一次就覆蓋。
 *  舊版存在 sessionStorage 的也照樣讀得到。 */
export function displayToken(): string | null {
  try {
    const t = localStorage.getItem(DISPLAY_KEY);
    if (t) return t;
    const old = sessionStorage.getItem(DISPLAY_KEY);   // 舊版存在這裡：搬過去，之後關機重開也記得
    if (old) { localStorage.setItem(DISPLAY_KEY, old); sessionStorage.removeItem(DISPLAY_KEY); }
    return old;
  } catch { return null; }
}
export function setDisplayToken(t: string | null): void {
  try {
    if (t) localStorage.setItem(DISPLAY_KEY, t);
    else localStorage.removeItem(DISPLAY_KEY);
    sessionStorage.removeItem(DISPLAY_KEY);
  } catch { /* 無痕或封鎖儲存：只能把完整的權杖網址設成電視的首頁 */ }
}

function client(): AxiosInstance {
  const t = displayToken();
  if (!t) return apiClient;
  return axios.create({ baseURL: "/", headers: { "X-Warroom-Token": t }, timeout: 30000 });
}

export async function getWarroomConfig(): Promise<WarroomConfig> {
  const { data } = await client().get<WarroomConfig>("/api/v1/warroom/config");
  return data;
}

export async function getWarroomPage(key: string, page = 1): Promise<PageData> {
  const { data } = await client().get<PageData>(`/api/v1/warroom/pages/${encodeURIComponent(key)}`, { params: { page } });
  return data;
}

// ── 管理：設定與顯示權杖 ──
export interface WarroomSettings {
  enabled: boolean;
  pages: string[];
  all_pages: string[];
  ready_pages: string[];
  dwell_seconds: number;
  max_subpages: number;
  group_by: string;
  capacity_warn: number;
  capacity_crit: number;
  language: string;
  reduce_motion: boolean;
  /** 資訊展示模式（11-03） */
  info_mode: boolean;
  /** 換頁過場：slide／fade／zoom／rise／wipe／flip */
  transition?: string;
  /** 大數字字體（warroomFonts.ts 的鍵） */
  number_font?: string;
  availability_window: string;
  ai_summary: string;
  intel: IntelSettings;
  display_enabled: boolean;
  principal_user_id: string | null;
  token_set: boolean;
  token_expires_at: string | null;
  scope: Record<ScopeKind, string[]>;
  scope_labels?: Record<ScopeKind, ScopeOption[]>;
}

export type ScopeKind = "customers" | "sections" | "subnets";
export interface ScopeOption { value: string; label: string }

/** 顯示範圍的選單：後端依名稱搜尋、最多 50 筆（大站台不整份載入） */
export async function searchWarroomScope(kind: ScopeKind, q: string): Promise<ScopeOption[]> {
  const { data } = await apiClient.get<ScopeOption[]>("/api/v1/system/warroom/scope-options", { params: { kind, q } });
  return data;
}

export async function getWarroomSettings(): Promise<WarroomSettings> {
  const { data } = await apiClient.get<WarroomSettings>("/api/v1/system/warroom");
  return data;
}
export async function putWarroomSettings(body: Partial<WarroomSettings>): Promise<WarroomSettings> {
  const { data } = await apiClient.put<WarroomSettings>("/api/v1/system/warroom", body);
  return data;
}
export async function rotateWarroomToken(days: number): Promise<{ token: string; expires_at: string | null }> {
  const { data } = await apiClient.post("/api/v1/system/warroom/token", { days });
  return data;
}
export async function revealWarroomToken(): Promise<{ token: string; expires_at: string | null }> {
  const { data } = await apiClient.get("/api/v1/system/warroom/token");
  return data;
}
export async function revokeWarroomToken(): Promise<void> {
  await apiClient.delete("/api/v1/system/warroom/token");
}
