import { apiClient } from "@/api/client";

export interface EnrollResponse {
  secret: string;
  otpauth_uri: string;
}

export async function enroll(): Promise<EnrollResponse> {
  const { data } = await apiClient.post<EnrollResponse>("/api/v1/auth/totp/enroll");
  return data;
}

export async function confirm(secret: string, code: string): Promise<void> {
  await apiClient.post("/api/v1/auth/totp/confirm", { secret, code });
}

export async function disable(): Promise<void> {
  await apiClient.post("/api/v1/auth/totp/disable");
}
