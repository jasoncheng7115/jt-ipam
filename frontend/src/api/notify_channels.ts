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
  // Telegram
  telegram_enabled: boolean;
  telegram_chat_id: string | null;
  telegram_token_set: boolean;
  // Slack
  slack_enabled: boolean;
  slack_webhook_set: boolean;
  // Teams
  teams_enabled: boolean;
  teams_webhook_set: boolean;
  // Nextcloud Talk
  nextcloud_enabled: boolean;
  nextcloud_url: string | null;
  nextcloud_token: string | null;
  nextcloud_secret_set: boolean;
  // Zulip
  zulip_enabled: boolean;
  zulip_site: string | null;
  zulip_bot_email: string | null;
  zulip_stream: string | null;
  zulip_topic: string | null;
  zulip_api_key_set: boolean;
  // Generic webhook
  webhook_enabled: boolean;
  webhook_url_set: boolean;
  webhook_token_set: boolean;
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
  telegram_enabled?: boolean;
  telegram_chat_id?: string | null;
  telegram_token?: string | null;
  slack_enabled?: boolean;
  slack_webhook?: string | null;
  teams_enabled?: boolean;
  teams_webhook?: string | null;
  nextcloud_enabled?: boolean;
  nextcloud_url?: string | null;
  nextcloud_token?: string | null;
  nextcloud_secret?: string | null;
  zulip_enabled?: boolean;
  zulip_site?: string | null;
  zulip_bot_email?: string | null;
  zulip_stream?: string | null;
  zulip_topic?: string | null;
  zulip_api_key?: string | null;
  webhook_enabled?: boolean;
  webhook_url?: string | null;
  webhook_token?: string | null;
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

// 對指定 webhook 型管道（telegram/slack/teams/nextcloud/zulip）送測試通知（用已儲存設定）
export async function sendTestChannel(channel: string): Promise<void> {
  await apiClient.post("/api/v1/system/notification-channels/test-channel", { channel });
}

// ── 通知矩陣：哪些事件走哪些管道（站內 / Email）──
export type NotifyMatrix = Record<string, { in_app: boolean; email: boolean }>;
export interface NotifyMatrixResp { matrix: NotifyMatrix; events: string[]; }

export async function getNotificationMatrix(): Promise<NotifyMatrixResp> {
  const { data } = await apiClient.get<NotifyMatrixResp>("/api/v1/system/notification-matrix");
  return data;
}

export async function setNotificationMatrix(matrix: NotifyMatrix): Promise<NotifyMatrixResp> {
  const { data } = await apiClient.put<NotifyMatrixResp>(
    "/api/v1/system/notification-matrix", { matrix },
  );
  return data;
}
