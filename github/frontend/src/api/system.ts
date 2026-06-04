import { apiClient } from "@/api/client";

export interface GraylogDsv { enabled: boolean; fmt: string; path: string; token: string; }
export async function getGraylogDsv(): Promise<GraylogDsv> {
  const { data } = await apiClient.get<GraylogDsv>("/api/v1/system/graylog-dsv");
  return data;
}
export async function putGraylogDsv(p: {
  enabled: boolean; fmt: string; path: string; regenerate_token?: boolean;
}): Promise<GraylogDsv> {
  const { data } = await apiClient.put<GraylogDsv>("/api/v1/system/graylog-dsv", p);
  return data;
}

export interface LLMConfig {
  enabled: boolean;
  url: string;
  embedding_model: string;
  chat_model: string;
  timeout: number;
}

export interface LLMConfigPatch {
  enabled?: boolean;
  url?: string;
  embedding_model?: string;
  chat_model?: string;
  timeout?: number;
}

export async function getLLMConfig(): Promise<LLMConfig> {
  const { data } = await apiClient.get<LLMConfig>("/api/v1/system/llm");
  return data;
}

export async function patchLLMConfig(payload: LLMConfigPatch): Promise<LLMConfig> {
  const { data } = await apiClient.patch<LLMConfig>("/api/v1/system/llm", payload);
  return data;
}

export interface OllamaModel {
  name: string;
  size: number | null;
  modified_at: string | null;
  family: string | null;
  parameter_size: string | null;
}

export async function listOllamaModels(): Promise<{ models: OllamaModel[]; error?: string }> {
  const { data } = await apiClient.get<{ models: OllamaModel[]; error?: string }>(
    "/api/v1/system/llm/models",
  );
  return data;
}

export interface VersionInfo {
  current: string;
  python: string;
  packages: Record<string, string | null>;
}

export interface LatestVersion {
  current: string;
  latest: string | null;
  update_available: boolean;
  release_url: string;
  error: string | null;
}

export async function getVersionInfo(): Promise<VersionInfo> {
  const { data } = await apiClient.get<VersionInfo>("/api/v1/system/version");
  return data;
}

export async function checkLatestVersion(): Promise<LatestVersion> {
  const { data } = await apiClient.get<LatestVersion>("/api/v1/system/version/check-latest");
  return data;
}
