import { apiClient } from "./client";

/**
 * 變更影響預演（docs/SPEC_CHANGE_IMPACT_zh-TW.md；後端 endpoints/change_impact.py）。
 * 預演只讀：不會改任何來源設備。結果每次讀取都依目前權限重新過濾。
 */
export type ScenarioType = "ip_renumber" | "device_decommission" | "switch_maintenance" | "node_downtime";
/** M2：維護／停機情境（看誰跟著停、服務還剩不剩所需的依賴） */
export const M2_SCENARIOS: ScenarioType[] = ["switch_maintenance", "node_downtime"];
export const ALL_SCENARIOS: ScenarioType[] = ["ip_renumber", "device_decommission", "switch_maintenance", "node_downtime"];
export type Lifecycle = "draft" | "in_review" | "approved" | "in_progress" | "verified" | "closed" | "cancelled";
export type JobStatus = "queued" | "snapshotting" | "extracting" | "analyzing" | "persisting"
  | "completed" | "partial" | "failed" | "cancelled";

export interface ImpactSettings {
  enabled: boolean;
  ai_available: boolean;
  config?: Record<string, any>;
}
export interface ChangePlan {
  id: string; title: string; scenario_type: ScenarioType; target_type: "ip_address" | "device";
  target_id: string; target_label: string;
  parameters: { new_ip?: string; target_subnet_id?: string; mode?: string | null; also_down?: string[] };
  also_down_labels?: string[];
  planned_start: string | null; planned_end: string | null; created_by: string | null;
  owner_user_id: string | null; reviewer_user_id: string | null; revision: number; lifecycle: Lifecycle;
  latest_run_id: string | null; archived_at: string | null; created_at: string; updated_at: string;
  latest_run?: { job_status: JobStatus; decision_status: string | null; completeness: string | null;
                 counts: Record<string, number> | null; completed_at: string | null } | null;
  current_run_id?: string | null; current_run_expired?: boolean | null; can_edit?: boolean;
  latest_review?: ImpactReview | null;
  /** 這個人現在能不能覆核（送審中、在審核人名單或對目標有修改權、不是自己建的） */
  can_review?: boolean;
  /** 審核人（有名單時是名單裡看得到目標的人，沒有名單時是管理員），最多 20 位 */
  reviewers?: { id: string; name: string }[]; reviewers_total?: number; reviewers_designated?: boolean;
  /** 審核關卡的模式與多關卡的進度（「申請審核設定」） */
  review_mode?: "editors" | "admin" | "designated" | "parallel" | "stages";
  review_steps?: { index: number; name: string; approved: boolean; is_current: boolean; approvers: string[] }[];
}
export interface ImpactRun {
  id: string; plan_id: string; plan_revision: number; job_status: JobStatus; stage: string | null;
  decision_status: "blocked" | "needs_review" | "no_known_blocker" | null;
  completeness: "complete" | "partial" | "failed" | null;
  scope_manifest: { sources?: { kind: string; id?: string; name: string; freshness: string; last_sync_at: string | null }[];
                    permission_limited?: string[]; roots?: string[]; new_ip?: string | null;
                    target_subnet?: string | null; cross_subnet?: boolean;
                    observed_from?: string | null; observed_to?: string | null };
  /** 讀者的權限範圍跟分析當時不同、而且這個端點沒有重算時是 null */
  counts: Record<string, number> | null; snapshot_hash: string | null; engine_version: string | null;
  rules_version: string | null; attempt: number; started_at: string | null; completed_at: string | null;
  expires_at: string | null; truncated: boolean; truncation: { what: string; limit: number; stage: string } | null;
  error_code: string | null; created_at: string; cancel_requested: boolean; permission_scope_changed?: boolean;
  /** 這次分析有沒有我看不到的需覆核項目（有的話我不能核准，只說有、不說幾項） */
  review_items_hidden?: boolean;
}
export interface ImpactFinding {
  id: string; rule_id: string; rule_version: string; category: string; subject_type: string;
  subject_id: string | null; subject_key: string | null; subject_label: string; match_kind: string | null;
  impact: string; severity: "critical" | "high" | "medium" | "low" | "info";
  disposition: "blocker" | "review" | "informational"; evidence_strength: string; reason_code: string;
  params: Record<string, any>; evidence_ids: string[]; relationship_path: { ref: string; label: string }[];
  suggested_action: Record<string, any> | null; fingerprint: string;
}
export interface ImpactEvidence {
  id: string; key: string; source_type: string; integration_ref: string | null; object_type: string;
  object_id?: string | null; label: string; observed_at: string | null; collected_at: string | null;
  freshness: string; payload?: Record<string, any>;
}
export interface ImpactGap {
  id: string; category: string; reason_code: string; params: Record<string, any>;
  source_scope: string | null; affected_analysis: string | null;
}
export interface ChangeTask {
  id: string; phase: "precheck" | "change" | "rollback" | "verify"; position: number; title: string;
  instruction: string; template_code: string | null; template_params: Record<string, any> | null;
  origin: "rule_template" | "ai_draft" | "manual"; finding_ids: string[]; depends_on: string[];
  assignee_user_id: string | null; state: "pending" | "in_progress" | "done" | "blocked" | "skipped";
  version: number; completed_by: string | null; completed_at: string | null; completion_note: string | null;
}
export interface ImpactReview {
  id: string; revision: number; run_id: string | null; snapshot_hash: string | null; reviewer_id: string | null;
  decision: "approve" | "reject" | "accept_risk" | "request_changes"; rationale: string;
  /** 審核人顯示名稱（2026-10-25 起） */
  reviewer_name?: string | null;
  dispositions: Record<string, { action: string; note: string }>; created_at: string;
}
export interface AIItem { text?: string; code?: string; params?: Record<string, any>; finding_ids?: string[];
                          evidence_ids?: string[]; gap_ids?: string[]; phase?: string; requires_confirmation?: boolean }
export interface ImpactAIArtifact {
  id: string; artifact_type: "summary" | "checklist" | "explanation" | "answer";
  status: "pending" | "running" | "completed" | "failed" | "fallback"; question: string | null;
  /** 執行中做到哪一步（collecting／asking／checking／retrying） */
  stage?: string | null;
  model: string | null; validation_state: string | null; truncated: boolean; error_code: string | null;
  generated_at: string | null; created_at: string; hidden_scope_changed: boolean;
  output: { template?: boolean; summary?: AIItem[]; uncertainties?: AIItem[]; suggested_tasks?: AIItem[] } | null;
}

export async function getImpactSettings(): Promise<ImpactSettings> {
  return (await apiClient.get("/api/v1/change-impact/settings")).data;
}
export async function putImpactSettings(body: Record<string, any>): Promise<ImpactSettings> {
  return (await apiClient.put("/api/v1/change-impact/settings", body)).data;
}
export async function listPlans(params: Record<string, any>): Promise<{ items: ChangePlan[]; total: number }> {
  return (await apiClient.get("/api/v1/change-plans", { params })).data;
}
export async function createPlan(body: Record<string, any>, idemKey?: string): Promise<ChangePlan> {
  return (await apiClient.post("/api/v1/change-plans", body,
                               idemKey ? { headers: { "Idempotency-Key": idemKey } } : undefined)).data;
}
export async function getPlan(id: string): Promise<ChangePlan> {
  return (await apiClient.get(`/api/v1/change-plans/${id}`)).data;
}
export async function patchPlan(id: string, revision: number, body: Record<string, any>): Promise<ChangePlan> {
  return (await apiClient.patch(`/api/v1/change-plans/${id}`, body, { headers: { "If-Match": String(revision) } })).data;
}
export async function archivePlan(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/change-plans/${id}`);
}
export async function transitionPlan(id: string, action: string): Promise<ChangePlan> {
  return (await apiClient.post(`/api/v1/change-plans/${id}/transitions`, { action })).data;
}
export async function startRun(planId: string): Promise<ImpactRun> {
  return (await apiClient.post(`/api/v1/change-plans/${planId}/runs`, null,
                               { headers: { "Idempotency-Key": crypto.randomUUID?.() ?? String(Date.now()) } })).data;
}
export async function listRuns(planId: string): Promise<ImpactRun[]> {
  return (await apiClient.get(`/api/v1/change-plans/${planId}/runs`)).data.items;
}
export async function listRevisions(planId: string): Promise<{ revision: number; payload: any; created_at: string }[]> {
  return (await apiClient.get(`/api/v1/change-plans/${planId}/revisions`)).data.items;
}
export async function getRun(id: string): Promise<ImpactRun> {
  return (await apiClient.get(`/api/v1/impact-runs/${id}`)).data;
}
export async function cancelRun(id: string): Promise<ImpactRun> {
  return (await apiClient.post(`/api/v1/impact-runs/${id}/cancel`)).data;
}
export async function listFindings(runId: string): Promise<{ items: ImpactFinding[]; total: number;
                                                             permission_scope_changed: boolean }> {
  return (await apiClient.get(`/api/v1/impact-runs/${runId}/findings`, { params: { page_size: 200 } })).data;
}
export async function listEvidence(runId: string): Promise<ImpactEvidence[]> {
  return (await apiClient.get(`/api/v1/impact-runs/${runId}/evidence`)).data.items;
}
export async function getEvidence(runId: string, evId: string): Promise<ImpactEvidence> {
  return (await apiClient.get(`/api/v1/impact-runs/${runId}/evidence/${evId}`)).data;
}
export async function listGaps(runId: string): Promise<ImpactGap[]> {
  return (await apiClient.get(`/api/v1/impact-runs/${runId}/gaps`)).data.items;
}
export async function listTasks(planId: string): Promise<ChangeTask[]> {
  return (await apiClient.get(`/api/v1/change-plans/${planId}/tasks`)).data.items;
}
export async function createTask(planId: string, body: Record<string, any>): Promise<ChangeTask> {
  return (await apiClient.post(`/api/v1/change-plans/${planId}/tasks`, body)).data;
}
export async function acceptAiTasks(planId: string, artifactId: string, indices: number[]): Promise<ChangeTask[]> {
  return (await apiClient.post(`/api/v1/change-plans/${planId}/tasks/from-ai`,
                               { artifact_id: artifactId, indices })).data.items;
}
export async function patchTask(id: string, version: number, body: Record<string, any>): Promise<ChangeTask> {
  return (await apiClient.patch(`/api/v1/change-tasks/${id}`, { ...body, expected_version: version })).data;
}
export async function createReview(planId: string, body: Record<string, any>): Promise<ImpactReview> {
  return (await apiClient.post(`/api/v1/change-plans/${planId}/reviews`, body)).data;
}
export async function listReviews(planId: string): Promise<ImpactReview[]> {
  return (await apiClient.get(`/api/v1/change-plans/${planId}/reviews`)).data.items;
}
export async function requestAi(runId: string, artifactType: string): Promise<ImpactAIArtifact> {
  return (await apiClient.post(`/api/v1/impact-runs/${runId}/ai-artifacts`, { artifact_type: artifactType })).data;
}
export async function askAi(runId: string, question: string): Promise<ImpactAIArtifact> {
  return (await apiClient.post(`/api/v1/impact-runs/${runId}/questions`, { question })).data;
}
export async function listAi(runId: string): Promise<ImpactAIArtifact[]> {
  return (await apiClient.get(`/api/v1/impact-runs/${runId}/ai-artifacts`)).data.items;
}
export async function candidates(
  ip: string, subnetId?: string | null,
): Promise<{ id: string; ip: string; subnet: string; hostname: string | null }[]> {
  return (await apiClient.get("/api/v1/change-impact/candidates",
    { params: { ip, subnet_id: subnetId || undefined } })).data.items;
}

export interface TargetSubnet {
  id: string; cidr: string; description: string | null;
  section_id: string; section_name: string; customer_id: string | null; customer_name: string | null;
  vrf_name: string | null;
}
export interface TargetSubnets {
  subnets: TargetSubnet[];
  sections: { id: string; name: string }[];
  customers: { id: string; name: string }[];
  truncated: boolean;
}
/** 建立視窗的子網路下拉：只列可以修改的子網路；單位／區段選項也只從這些子網路推出來 */
export async function targetSubnets(
  params: { q?: string; sectionId?: string | null; customerId?: string | null } = {},
): Promise<TargetSubnets> {
  return (await apiClient.get("/api/v1/change-impact/target-subnets", { params: {
    q: params.q || undefined, section_id: params.sectionId || undefined, customer_id: params.customerId || undefined,
  } })).data;
}
/** 匯出是即時產生的檔案：用 blob 下載（帶登入權杖），不產生永久網址 */
export async function exportRun(runId: string, format: "md" | "json"): Promise<Blob> {
  return (await apiClient.get(`/api/v1/impact-runs/${runId}/export`, { params: { format }, responseType: "blob" })).data;
}

// ── M2：服務與依賴（服務是共享基礎設施：看要全域讀取、改只有管理員） ──
export type MemberType = "device" | "vm" | "ip" | "subnet" | "service";
export type RelationType = "hosted_on" | "requires_network" | "requires_storage" | "requires_power"
  | "requires_service" | "references" | "observed_on";
export interface ServiceMember { object_type: MemberType; object_id: string; relation_type: RelationType;
  note?: string | null; label?: string | null; missing?: boolean }
export interface ServiceGroup { id?: string; name: string; required_count: number; purpose?: string | null;
  confirmed: boolean; members: ServiceMember[] }
export interface ServiceEndpoint { object_type: "ip" | "device" | "vm" | null; object_id: string | null;
  label?: string | null; hostname: string | null; port: number | null; protocol: string | null }
export interface ImpactService {
  id: string; name: string; customer_id: string | null; description: string | null;
  owner_user_id: string | null; owner_group_id: string | null;
  criticality: "critical" | "high" | "normal" | "low"; status: "active" | "retired";
  maintenance_notes: string | null; version: number; updated_at: string | null; group_count: number;
  endpoints?: ServiceEndpoint[]; groups?: ServiceGroup[];
}
export async function listServices(params: { q?: string; page?: number; pageSize?: number; sort?: string;
                                            order?: "asc" | "desc" } = {}):
    Promise<{ items: ImpactService[]; total: number; can_edit: boolean }> {
  return (await apiClient.get("/api/v1/change-impact/services", { params: {
    q: params.q || undefined, page: params.page ?? 1, page_size: params.pageSize ?? 50,
    sort: params.sort || undefined, order: params.sort ? params.order : undefined } })).data;
}
export async function getService(id: string): Promise<ImpactService> {
  return (await apiClient.get(`/api/v1/change-impact/services/${id}`)).data;
}
export async function createService(body: Record<string, unknown>): Promise<ImpactService> {
  return (await apiClient.post("/api/v1/change-impact/services", body)).data;
}
export async function updateService(id: string, body: Record<string, unknown>): Promise<ImpactService> {
  return (await apiClient.put(`/api/v1/change-impact/services/${id}`, body)).data;
}
export async function deleteService(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/change-impact/services/${id}`);
}
export async function searchServiceObjects(type: MemberType, q: string): Promise<{ id: string; label: string }[]> {
  return (await apiClient.get("/api/v1/change-impact/services/object-search", { params: { type, q } })).data.items;
}

// 關係圖（後端依讀者過濾、有上限）
export interface RelationEvidence { source_type: string; object_type: string; label: string; freshness: string;
  observed_at: string | null }
export interface RelationNode { id: string; type: string; label: string; impact: string | null; root: boolean;
  category?: string | null;
  /** 明細面板：原始物件 id、整合類型、為什麼列出來（規則＋原因參數）、證據來源與時間 */
  subject_id?: string | null; kind?: string | null;
  rules?: { rule_id: string; reason: string; params: Record<string, unknown>; impact: string }[];
  evidence?: RelationEvidence[];
  /** 這個物件有阻擋項目（圖上紅框；2026-10-25） */
  blocker?: boolean }
export interface RelationGraph { nodes: RelationNode[]; edges: { from: string; to: string; relation: string; strength: string }[];
  truncated: boolean; total_nodes: number; limit: number }
export async function getRunRelations(runId: string, limit = 100): Promise<RelationGraph> {
  return (await apiClient.get(`/api/v1/impact-runs/${runId}/relations`, { params: { limit } })).data;
}

/** IP 變更評估的審核關卡（「申請審核設定」的「IP 變更評估審核」；管理員） */
export interface ReviewPolicy {
  approver_mode: "editors" | "admin" | "designated" | "parallel" | "stages";
  designated_user_ids: string[]; designated_group_ids: string[]; allow_self_approve: boolean;
  stages: { name: string; user_ids: string[]; group_ids: string[] }[];
}
export async function getReviewPolicy(): Promise<ReviewPolicy> {
  return (await apiClient.get("/api/v1/change-impact/review-policy")).data;
}
export async function setReviewPolicy(p: ReviewPolicy): Promise<ReviewPolicy> {
  return (await apiClient.put("/api/v1/change-impact/review-policy", p)).data;
}
