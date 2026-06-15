import { apiClient } from "@/api/client";

export interface NotifyChannelInfo { key: string; available: boolean; }

export interface NotificationChannels {
  email_enabled: boolean;
  smtp_host: string | null;
  smtp_port: number;
  smtp_tls: "none" | "starttls" | "tls";
  smtp_username: string | null;
  smtp_from: string | null;
  smtp_password_set: boolean;
  channels: NotifyChannelInfo[];
}

export interface NotificationChannelsUpdate {
  email_enabled?: boolean;
  smtp_host?: string | null;
  smtp_port?: number;
  smtp_tls?: string;
  smtp_username?: string | null;
  smtp_from?: string | null;
  smtp_password?: string | null;   // 給非空才更新；"" 清除；不給保留
}

export async function getNotificationChannels(): Promise<NotificationChannels> {
  const { data } = await apiClient.get<NotificationChannels>("/api/v1/system/notification-channels");
  return data;
}

export async function setNotificationChannels(
  patch: NotificationChannelsUpdate,
): Promise<NotificationChannels> {
  const { data } = await apiClient.put<NotificationChannels>(
    "/api/v1/system/notification-channels", patch,
  );
  return data;
}

export async function sendTestEmail(to: string): Promise<void> {
  await apiClient.post("/api/v1/system/notification-channels/test-email", { to });
}
