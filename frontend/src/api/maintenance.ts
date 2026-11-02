/** 維護排程（2026-11-01）：維護期間範圍內不告警。 */
import { apiClient } from "@/api/client";

export type ScopeType = "ip" | "ip_range" | "subnet" | "section" | "device" | "rack" | "location" | "customer"
  | "integration" | "scan_agent";
export interface ScopeIn { scope_type: ScopeType; object_id?: string | null; integration_kind?: string | null;
                           range?: string | null }
export interface ScopeOut extends ScopeIn { label: string | null }
export interface Window {
  id: string; title: string; note: string | null; ticket: string | null; behavior: "mute" | "mark";
  schedule_type: "once" | "weekly"; starts_at: string | null; ends_at: string | null;
  weekdays: number[] | null; start_time: string | null; end_time: string | null;
  valid_from: string | null; valid_until: string | null; source: string; source_ref: string | null;
  created_by: string | null; status: "active" | "upcoming" | "past";
  current_start: string | null; current_end: string | null; next_start: string | null; next_end: string | null;
  suppressed: number; scopes: ScopeOut[];
  counts?: Counts; can_manage?: boolean;
}
export interface Counts { ips: number; subnets: number; devices: number; integrations: number; agents: number;
                          range_addresses: number; large_threshold?: number }
export interface ScheduleIn {
  type: "once" | "weekly"; starts_at?: string | null; ends_at?: string | null; weekdays?: number[];
  start_time?: string | null; end_time?: string | null; valid_from?: string | null; valid_until?: string | null;
}
export interface WindowIn { title: string; note?: string | null; ticket?: string | null; behavior: "mute" | "mark";
                            schedule: ScheduleIn; scopes: ScopeIn[]; confirm_large?: boolean }
export interface ActiveFor { id: string; title: string; behavior: string; source: string; until: string | null;
                             schedule_type: string; can_manage: boolean }

/** 維護有變動（建立、延長、結束、刪除）：頂端列的「維護中 N」與物件頁按鈕馬上更新，不用等下一次輪詢 */
export const MAINT_CHANGED = "jtipam:maintenance-changed";
function changed<T>(p: Promise<T>): Promise<T> {
  return p.then((v) => { window.dispatchEvent(new CustomEvent(MAINT_CHANGED)); return v; });
}

export const Maintenance = {
  list: (status: "active" | "upcoming" | "past" | "all" = "all") =>
    apiClient.get<{ items: Window[] }>("/api/v1/maintenance", { params: { status } }).then((r) => r.data.items),
  get: (id: string) => apiClient.get<Window>(`/api/v1/maintenance/${id}`).then((r) => r.data),
  create: (body: WindowIn) => changed(apiClient.post<Window>("/api/v1/maintenance", body).then((r) => r.data)),
  update: (id: string, body: WindowIn) => changed(apiClient.patch<Window>(`/api/v1/maintenance/${id}`, body).then((r) => r.data)),
  remove: (id: string) => changed(apiClient.delete(`/api/v1/maintenance/${id}`)),
  quick: (scope: ScopeIn, minutes: number, extra: { title?: string; note?: string; ticket?: string } = {}) =>
    changed(apiClient.post<Window>("/api/v1/maintenance/quick", { scope, minutes, ...extra }).then((r) => r.data)),
  end: (id: string) => changed(apiClient.post<Window>(`/api/v1/maintenance/${id}/end`).then((r) => r.data)),
  extend: (id: string, minutes: number) =>
    changed(apiClient.post<Window>(`/api/v1/maintenance/${id}/extend`, { minutes }).then((r) => r.data)),
  preview: (scopes: ScopeIn[]) => apiClient.post<Counts>("/api/v1/maintenance/preview", scopes).then((r) => r.data),
  activeFor: (objectType: ScopeType, objectId: string, integrationKind?: string) =>
    apiClient.get<ActiveFor[]>("/api/v1/maintenance/active", {
      params: { object_type: objectType, object_id: objectId, integration_kind: integrationKind } }).then((r) => r.data),
  summary: () => apiClient.get<{ active: number; items: { id: string; title: string; until: string | null }[] }>(
    "/api/v1/maintenance/summary").then((r) => r.data),
  suppressed: (id: string) =>
    apiClient.get<{ event: string; label: string | null; first_at: string; count: number; occurrence: string }[]>(
      `/api/v1/maintenance/${id}/suppressed`).then((r) => r.data),
  search: (type: ScopeType, q: string) =>
    apiClient.get<{ items: { id: string | null; label: string; integration_kind?: string }[] }>(
      "/api/v1/maintenance/scope-search", { params: { type, q } }).then((r) => r.data.items),
  settings: () => apiClient.get<{ max_hours: number; large_threshold: number }>("/api/v1/maintenance/settings")
    .then((r) => r.data),
  saveSettings: (body: { max_hours: number; large_threshold: number }) =>
    apiClient.put("/api/v1/maintenance/settings", body).then((r) => r.data),
};
