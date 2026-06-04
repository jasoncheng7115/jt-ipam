import { apiClient } from "@/api/client";

// AI chat 是多輪 tool-calling 迴圈 (max_iterations 輪 × 每輪一次本地 LLM 推論)，
// 在大模型上單次可達數十秒，遠超 apiClient 預設 15s timeout(會誤報 Chat failed)。
// 後端最壞 = system_settings.timeout × max_iterations，這裡給 5 分鐘覆蓋。
const AI_CHAT_TIMEOUT_MS = 300_000;

export interface ChatMessage {
  role: "user" | "assistant" | "system" | "tool";
  content: string;
}

export interface ChatResponse {
  answer: string;
  trace_messages: ChatMessage[];
  model?: string | null;
  elapsed_ms?: number | null;
}

export async function chat(
  messages: ChatMessage[],
  maxIterations = 4,
): Promise<ChatResponse> {
  const { data } = await apiClient.post<ChatResponse>("/api/v1/ai/chat", {
    messages,
    max_iterations: maxIterations,
  }, { timeout: AI_CHAT_TIMEOUT_MS });
  return data;
}

// SSE 串流事件 (對齊後端 ai_service.chat_stream)
export type ChatStreamEvent =
  | { type: "token"; text: string }
  | { type: "tool"; name: string }
  | { type: "tool_round" }
  | { type: "done"; answer: string; trace_messages: ChatMessage[]; model?: string | null; elapsed_ms?: number | null; conversation_id?: string }
  | { type: "error"; detail: string };

/**
 * SSE 串流版 chat：逐 token 收最終答案。用 fetch(EventSource 不支援 POST /
 * Authorization header)。baseURL 與 token 對齊 apiClient。
 */
export interface ChatPageContext {
  subnet_id?: string;
  subnet_cidr?: string;
  device_id?: string;
  section_id?: string;
}

export async function chatStream(
  messages: ChatMessage[],
  maxIterations: number,
  onEvent: (ev: ChatStreamEvent) => void,
  signal?: AbortSignal,
  context?: ChatPageContext,
  conversationId?: string | null,
): Promise<void> {
  const base = import.meta.env.VITE_API_BASE_URL || "";
  const token = localStorage.getItem("access_token");
  const resp = await fetch(`${base}/api/v1/ai/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      messages, max_iterations: maxIterations,
      ...(context && Object.keys(context).length ? { context } : {}),
      ...(conversationId ? { conversation_id: conversationId } : {}),
    }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    let detail = `HTTP ${resp.status}`;
    try {
      const j = await resp.json();
      detail = j?.detail ?? detail;
    } catch { /* 非 JSON body */ }
    throw new Error(detail);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // SSE：以空行分隔事件
    let sep: number;
    while ((sep = buf.indexOf("\n\n")) !== -1) {
      const raw = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      const line = raw.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      const jsonStr = line.slice(5).trim();
      if (!jsonStr) continue;
      try {
        onEvent(JSON.parse(jsonStr) as ChatStreamEvent);
      } catch { /* 跳過壞掉的 chunk */ }
    }
  }
}

// ─────────────────── 對話歷程 ───────────────────
export interface ConversationSummary {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  message_count?: number;
  user_id?: string;
  username?: string;
}
export interface ConversationMessage {
  role: string;
  content: string;
  model?: string | null;
  elapsed_ms?: number | null;
  created_at: string;
}
export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMessage[];
}

export async function listMyConversations(): Promise<ConversationSummary[]> {
  const { data } = await apiClient.get<{ items: ConversationSummary[] }>("/api/v1/ai/chat/conversations");
  return data.items;
}
export async function getConversation(id: string): Promise<ConversationDetail> {
  const { data } = await apiClient.get<ConversationDetail>(`/api/v1/ai/chat/conversations/${id}`);
  return data;
}
export async function deleteConversation(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/ai/chat/conversations/${id}`);
}
export async function listAllConversations(limit = 500): Promise<ConversationSummary[]> {
  const { data } = await apiClient.get<{ items: ConversationSummary[] }>("/api/v1/ai/chat/admin/conversations", { params: { limit } });
  return data.items;
}
export async function getChatRetention(): Promise<number> {
  const { data } = await apiClient.get<{ retention_days: number }>("/api/v1/ai/chat/retention");
  return data.retention_days;
}
export async function setChatRetention(days: number): Promise<number> {
  const { data } = await apiClient.put<{ retention_days: number }>("/api/v1/ai/chat/retention", { retention_days: days });
  return data.retention_days;
}
export async function purgeChatHistory(): Promise<{ removed: number; retention_days: number }> {
  const { data } = await apiClient.post<{ removed: number; retention_days: number }>("/api/v1/ai/chat/purge");
  return data;
}

// 模型參數摘要（chat badge tooltip 用）
export interface ModelInfo {
  model: string;
  family?: string | null;
  parameter_size?: string | null;
  quantization?: string | null;
  context_length?: number | null;
  error?: string;
}
export async function getModelInfo(model?: string): Promise<ModelInfo> {
  const { data } = await apiClient.get<ModelInfo>("/api/v1/ai/model-info", {
    params: model ? { model } : {},
  });
  return data;
}
