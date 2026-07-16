import { apiClient } from "@/api/client";

// 全系統匯出／匯入（跨機搬移）。路徑帶 /api/v1 前綴（baseURL 為 /）。

export interface TransferSchema {
  scopes: string[];
  default_scope: string[];
  counts: Record<string, number>;
  schema_version: string | null;
  app_version: string;
}

export interface AnalyzeResult {
  token: string;
  metadata: {
    format_version: number | null;
    app_version: string | null;
    schema_version: string | null;
    scope: string[];
    exported_at: string | null;
    encrypted: boolean;
  };
  target_schema_version: string | null;
  counts: Record<string, number>;
  central_secrets: number;
  warnings: string[];
}

export interface TableCount {
  inserted: number;
  updated: number;
  skipped: number;
  errored: number;
  errors?: string[];
}

export interface ImportReport {
  mode: string;
  dry_run: boolean;
  tables: Record<string, TableCount>;
  central_secrets?: TableCount;
}

export async function getTransferSchema(): Promise<TransferSchema> {
  const { data } = await apiClient.get<TransferSchema>("/api/v1/system/transfer/schema");
  return data;
}

export async function startExport(scope: string[], passphrase: string): Promise<{ task_id: string }> {
  const { data } = await apiClient.post("/api/v1/system/transfer/export", { scope, passphrase });
  return data;
}

export async function downloadExport(taskId: string): Promise<Blob> {
  const { data } = await apiClient.get(`/api/v1/system/transfer/export/${taskId}/download`, {
    responseType: "blob",
  });
  return data as Blob;
}

export async function analyzeImport(file: Blob, passphrase: string): Promise<AnalyzeResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("passphrase", passphrase);
  const { data } = await apiClient.post<AnalyzeResult>("/api/v1/system/transfer/import/analyze", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

export async function applyImport(
  token: string,
  passphrase: string,
  mode: "merge" | "replace",
  dryRun: boolean,
): Promise<{ dry_run: boolean; report?: ImportReport; task_id?: string; status?: string }> {
  const { data } = await apiClient.post("/api/v1/system/transfer/import/apply", {
    token,
    passphrase,
    mode,
    dry_run: dryRun,
  });
  return data;
}
