import { apiClient } from "@/api/client";

export interface ChatMessage {
  role: "user" | "assistant" | "system" | "tool";
  content: string;
}

export interface ChatResponse {
  answer: string;
  trace_messages: ChatMessage[];
}

export async function chat(
  messages: ChatMessage[],
  maxIterations = 4,
): Promise<ChatResponse> {
  const { data } = await apiClient.post<ChatResponse>("/api/v1/ai/chat", {
    messages,
    max_iterations: maxIterations,
  });
  return data;
}
