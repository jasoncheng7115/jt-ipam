import { apiClient } from "@/api/client";
import type { RackGroupInfo } from "@/utils/rackGroups";

/** 機架型態。以 U 計：rack / industrial / lackrack；其餘以「層」計。 */
export type RackKind = "rack" | "industrial" | "shelf" | "wire_shelf" | "wood_shelf"
  | "angle_shelf" | "kallax" | "lackrack";
/** 表面顏色（各型態可用的見 utils/rackFinish.ts） */
export type RackFinish = "black" | "white" | "galvanized" | "black_brown" | "oak" | "brown";

export interface RackDeviceSlot {
  rack_slot?: number;
  rack_slot_span?: number;
  device_id: string;
  name: string;
  type: string;
  vendor: string | null;
  model: string | null;
  u_position: number;
  u_size: number;
  primary_ip: string | null;
  rack_face?: "front" | "rear" | null;
  rack_side?: "full" | "left" | "right";
  /** 裝置群組（堆疊／HA／叢集）：機櫃圖用同色括線把同組成員框起來 */
  group?: RackGroupInfo | null;
}

export interface RackDiagram {
  rack_id: string;
  name: string;
  u_height: number;
  location_id: string | null;
  numbering?: "top-down" | "bottom-up";
  face?: "front" | "rear";
  /** 對外公開這個機櫃的示意圖（給別的系統用 <img> 嵌入）。預設關 */
  expose_svg?: boolean;
  /** issue #30：rack＝標準機櫃（列＝U）、shelf＝層架（列＝層） */
  kind?: RackKind;
  /** 表面顏色（沒有顏色選項的型態是 null） */
  finish?: RackFinish | null;
  /** 後端依機櫃實體尺寸算好的繪製大小（層架非標準寬/層高才畫得對） */
  render_width_px?: number;
  render_row_px?: number;
  /** 每一層的高度 px，由第 1 層起算 */
  render_row_px_list?: number[];
  /** 每一列**底下**那片板的厚度 px，由上往下（畫面順序，含開放頂端那一列）。
   *  KALLAX 的外框比內隔板厚、LackRack 疊幾張就有幾片桌面；其他型態是均一值。 */
  render_board_px_list?: number[];
  /** 最上面那一列之上的厚度 px（LackRack 的桌面） */
  render_top_px?: number;
  /** KALLAX 有幾欄、直的內隔板多寬 px */
  render_cols?: number;
  render_divider_px?: number;
  devices: RackDeviceSlot[];
  conflicts: Record<string, unknown>[];
}

export async function getRackDiagram(id: string): Promise<RackDiagram> {
  const { data } = await apiClient.get<RackDiagram>(`/api/v1/racks/${id}/diagram`);
  return data;
}

// ─────────────────── 機房平面圖 ───────────────────
export interface Rack {
  id: string;
  name: string;
  u_height: number;
  kind?: RackKind;
  finish?: RackFinish | null;
  width_mm: number | null;
  row_height_mm?: number | null;
  /** 逐層高度（mm），由第 1 層起算；null＝整台用 row_height_mm */
  level_heights?: number[] | null;
  depth_mm: number | null;
  location_id: string | null;
  location_name?: string | null;
  description: string | null;
  seq?: number | null;
  device_count?: number;
  numbering?: "top-down" | "bottom-up";
  face?: "front" | "rear";
  /** 對外公開這個機櫃的示意圖（給別的系統用 <img> 嵌入）。預設關 */
  expose_svg?: boolean;
  pos_x: number | null;
  pos_y: number | null;
  pos_rot: number;
  pos_w: number | null;
  pos_h: number | null;
}

export async function listRacksByLocation(locationId: string): Promise<Rack[]> {
  const { data } = await apiClient.get<{ items: Rack[] }>("/api/v1/racks", {
    params: { location_id: locationId, page: 1, page_size: 500 },
  });
  return data.items;
}

/** 取機房平面圖底圖（需授權）→ 回 object URL，呼叫端用完要 revokeObjectURL。 */
export async function getFloorplanObjectURL(locationId: string): Promise<string> {
  const { data } = await apiClient.get<Blob>(
    `/api/v1/locations/${locationId}/floorplan`, { responseType: "blob" },
  );
  return URL.createObjectURL(data);
}

export async function uploadFloorplan(locationId: string, file: File): Promise<void> {
  const form = new FormData();
  form.append("file", file);
  await apiClient.post(`/api/v1/locations/${locationId}/floorplan`, form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

export async function deleteFloorplan(locationId: string): Promise<void> {
  await apiClient.delete(`/api/v1/locations/${locationId}/floorplan`);
}

export async function setRackPositions(
  locationId: string,
  positions: { id: string; pos_x: number; pos_y: number; pos_rot: number; pos_w?: number | null; pos_h?: number | null }[],
): Promise<{ updated: number }> {
  const { data } = await apiClient.put<{ updated: number }>(
    `/api/v1/locations/${locationId}/rack-positions`, { positions },
  );
  return data;
}


// ─────────────────── 對外嵌入設定（admin）───────────────────
export interface RackEmbedConfig {
  enabled: boolean;
  /** 權杖不再隨設定回傳：按「顯示」才另外取（後端留稽核） */
  token_set: boolean;
  token_expires_at: string | null;
}

export async function getRackEmbedConfig(): Promise<RackEmbedConfig> {
  const { data } = await apiClient.get<RackEmbedConfig>("/api/v1/system/rack-embed");
  return data;
}

/** 取嵌入權杖明文（組 <img> 網址用）；每次呼叫後端都留稽核。 */
export async function revealRackEmbedToken(): Promise<string> {
  const { data } = await apiClient.get<{ token: string }>("/api/v1/system/rack-embed/token");
  return data.token;
}

export async function setRackEmbedConfig(
  enabled: boolean, regenerate_token = false, token_days = 365,
): Promise<RackEmbedConfig> {
  const { data } = await apiClient.put<RackEmbedConfig>(
    "/api/v1/system/rack-embed", { enabled, regenerate_token, token_days },
  );
  return data;
}

/** 給外部系統嵌入的完整網址（含 token）。注意：這串等同一把鑰匙，別貼到公開的地方。 */
export function rackEmbedUrl(rackId: string, token: string): string {
  const base = window.location.origin;
  return `${base}/api/v1/racks/${rackId}/embed.svg?token=${encodeURIComponent(token)}`;
}

// ─────────────────── 層數調整（插入／刪除一層，裝置整批移位） ───────────────────
export interface RackLevelMove { device_id: string; name: string; u_size: number; from: number; to: number }
export interface RackLevelBlocker { device_id: string; name: string; u_position: number; u_size: number; reason: string }
export interface RackLevelPlan {
  op: "insert" | "remove";
  at: number;
  rack_id: string;
  old_height: number;
  new_height: number;
  moves: RackLevelMove[];
  blockers: RackLevelBlocker[];
  /** 照著送回來就能還原（插入與刪除互為反操作） */
  undo: { op: "insert" | "remove"; at: number; height_mm: number | null } | null;
}

/** `dryRun` 只算不做：預覽與實際執行走後端同一段程式，所以預覽看到什麼就會發生什麼。 */
export async function rackLevelOp(
  rackId: string,
  body: { op: "insert" | "remove"; at: number; height_mm?: number | null; dry_run?: boolean },
): Promise<RackLevelPlan> {
  const { data } = await apiClient.post<RackLevelPlan>(`/api/v1/racks/${rackId}/levels`, body);
  return data;
}
