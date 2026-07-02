<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NSwitch, NInput, NInputNumber, NSelect, NButton, NIcon,
  NFormItem, NAlert, NTag, NGrid, NGridItem, NCheckbox, useMessage,
} from "naive-ui";
import { SettingsIcon, SaveIcon } from "@/icons";
import {
  getNotificationChannels, setNotificationChannels, sendTestEmail, sendTestChannel,
  getNotificationMatrix, setNotificationMatrix,
  type NotificationChannels, type NotifyMatrix,
} from "@/api/notify_channels";

const { t } = useI18n();
const msg = useMessage();

const loading = ref(false);
const saving = ref(false);
const testing = ref(false);
const cfg = ref<NotificationChannels | null>(null);
const pw = ref("");          // 留空＝不變更
const testTo = ref("");
// 各管道密鑰輸入（留空＝不變更）
const tgToken = ref("");
const slackHook = ref("");
const teamsHook = ref("");
const ncSecret = ref("");
const zulipKey = ref("");
const webhookUrl = ref("");
const webhookToken = ref("");
const testingCh = ref("");   // 正在測試的管道 key

const tlsOptions = [
  { label: "STARTTLS", value: "starttls" },
  { label: "SSL/TLS", value: "tls" },
  { label: t("notify_ch.tls_none"), value: "none" },
];

const otherChannels = computed(() =>
  (cfg.value?.channels ?? []).filter((c) => c.key !== "email"),
);

async function load() {
  loading.value = true;
  try {
    cfg.value = await getNotificationChannels();
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

async function save() {
  if (!cfg.value) return;
  saving.value = true;
  try {
    const c = cfg.value;
    const patch: any = {
      email_enabled: c.email_enabled,
      smtp_host: c.smtp_host,
      smtp_port: c.smtp_port,
      smtp_tls: c.smtp_tls,
      smtp_username: c.smtp_username,
      smtp_from: c.smtp_from,
      telegram_enabled: c.telegram_enabled,
      telegram_chat_id: c.telegram_chat_id,
      slack_enabled: c.slack_enabled,
      teams_enabled: c.teams_enabled,
      nextcloud_enabled: c.nextcloud_enabled,
      nextcloud_url: c.nextcloud_url,
      nextcloud_token: c.nextcloud_token,
      zulip_enabled: c.zulip_enabled,
      zulip_site: c.zulip_site,
      zulip_bot_email: c.zulip_bot_email,
      zulip_stream: c.zulip_stream,
      zulip_topic: c.zulip_topic,
      webhook_enabled: c.webhook_enabled,
    };
    if (pw.value) patch.smtp_password = pw.value;
    if (tgToken.value) patch.telegram_token = tgToken.value;
    if (slackHook.value) patch.slack_webhook = slackHook.value;
    if (teamsHook.value) patch.teams_webhook = teamsHook.value;
    if (ncSecret.value) patch.nextcloud_secret = ncSecret.value;
    if (zulipKey.value) patch.zulip_api_key = zulipKey.value;
    if (webhookUrl.value) patch.webhook_url = webhookUrl.value;
    if (webhookToken.value) patch.webhook_token = webhookToken.value;
    cfg.value = await setNotificationChannels(patch);
    pw.value = tgToken.value = slackHook.value = teamsHook.value = ncSecret.value = zulipKey.value = "";
    webhookUrl.value = webhookToken.value = "";
    msg.success(t("common.saved"));
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  } finally {
    saving.value = false;
  }
}

async function test() {
  if (!testTo.value.trim()) { msg.warning(t("notify_ch.test_to_required")); return; }
  if (!cfg.value?.smtp_host) { msg.warning(t("notify_ch.no_host")); return; }
  testing.value = true;
  try {
    await sendTestEmail(testTo.value.trim());
    msg.success(t("notify_ch.test_sent"));
  } catch (e: any) {
    const detail = e?.response?.data?.detail ?? "";
    if (detail === "missing_smtp_host") {
      msg.error(t("notify_ch.no_host"));
    } else if (typeof detail === "string" && detail.startsWith("SMTP send failed")) {
      msg.error(t("notify_ch.send_failed", { msg: detail.replace("SMTP send failed: ", "") }));
    } else {
      msg.error(detail || t("errors.network"));
    }
  } finally {
    testing.value = false;
  }
}

async function testChannel(ch: string) {
  if (!cfg.value) return;
  testingCh.value = ch;
  try {
    // 測試用「已儲存」設定；先存再測，避免剛輸入未儲存的密鑰測不到
    await save();
    await sendTestChannel(ch);
    msg.success(t("notify_ch.test_sent"));
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  } finally {
    testingCh.value = "";
  }
}

function channelLabel(key: string): string {
  const m: Record<string, string> = {
    telegram: "Telegram", slack: "Slack", teams: "Microsoft Teams",
    nextcloud: "Nextcloud Talk", zulip: "Zulip",
  };
  return m[key] ?? key;
}

// ── 通知矩陣 ──
const matrix = ref<NotifyMatrix>({});
const matrixEvents = ref<string[]>([]);
const matrixSaving = ref(false);
function eventLabel(ev: string): string {
  return t(`notify_ch.ev.${ev.replace(/\./g, "_")}`);
}
async function loadMatrix() {
  try {
    const r = await getNotificationMatrix();
    matrix.value = r.matrix;
    matrixEvents.value = r.events;
  } catch { /* ignore */ }
}
async function saveMatrix() {
  matrixSaving.value = true;
  try {
    const r = await setNotificationMatrix(matrix.value);
    matrix.value = r.matrix;
    matrixEvents.value = r.events;
    msg.success(t("common.saved"));
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  } finally {
    matrixSaving.value = false;
  }
}

onMounted(() => { void load(); void loadMatrix(); });
</script>

<template>
  <n-space vertical :size="16">
    <n-card>
      <template #header>
        <n-space align="center" :wrap-item="false">
          <n-icon :size="22"><SettingsIcon /></n-icon>
          <span>{{ t("notify_ch.title") }}</span>
        </n-space>
      </template>
      <n-alert type="info" :show-icon="true">{{ t("notify_ch.intro") }}</n-alert>
    </n-card>

    <!-- 通知矩陣：哪些事件、走哪些管道（總覽，放所有管道設定之上）-->
    <n-card :title="t('notify_ch.matrix_title')">
      <p class="nmx-hint">{{ t("notify_ch.matrix_hint") }}</p>
      <table class="nmx">
        <thead>
          <tr>
            <th>{{ t("notify_ch.matrix_event") }}</th>
            <th class="nmx-c">{{ t("notify_ch.matrix_in_app") }}</th>
            <th class="nmx-c">{{ t("notify_ch.matrix_email") }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="ev in matrixEvents" :key="ev">
            <td>
              <div class="nmx-ev">{{ eventLabel(ev) }}</div>
              <code class="nmx-key">{{ ev }}</code>
            </td>
            <td class="nmx-c">
              <n-checkbox v-if="matrix[ev]" v-model:checked="matrix[ev].in_app" />
            </td>
            <td class="nmx-c">
              <n-checkbox v-if="matrix[ev]" v-model:checked="matrix[ev].email" />
            </td>
          </tr>
        </tbody>
      </table>
      <p class="nmx-hint">{{ t("notify_ch.matrix_email_note") }}</p>
      <n-space justify="end" style="margin-top: 12px">
        <n-button type="success" :loading="matrixSaving" @click="saveMatrix">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-card>

    <!-- Email（已實作）-->
    <n-card v-if="cfg" :title="'Email (SMTP)'">
      <n-space vertical :size="14" style="max-width: 640px">
        <n-form-item :label="t('notify_ch.email_enabled')" label-placement="left">
          <n-switch v-model:value="cfg.email_enabled" />
        </n-form-item>
        <n-form-item label="SMTP Host" label-placement="top">
          <n-input v-model:value="cfg.smtp_host" placeholder="smtp.example.com" />
        </n-form-item>
        <n-space :size="14">
          <n-form-item label="Port" label-placement="top">
            <n-input-number v-model:value="cfg.smtp_port" :min="1" :max="65535" style="width: 120px" />
          </n-form-item>
          <n-form-item label="TLS" label-placement="top">
            <n-select v-model:value="cfg.smtp_tls" :options="tlsOptions" style="width: 160px" />
          </n-form-item>
        </n-space>
        <n-form-item :label="t('notify_ch.username')" label-placement="top">
          <n-input v-model:value="cfg.smtp_username" placeholder="user@example.com" />
        </n-form-item>
        <n-form-item :label="t('notify_ch.password')" label-placement="top">
          <n-input v-model:value="pw" type="password" show-password-on="click"
                   :placeholder="cfg.smtp_password_set ? t('notify_ch.password_keep') : t('notify_ch.password_ph')" />
        </n-form-item>
        <n-form-item :label="t('notify_ch.from')" label-placement="top">
          <n-input v-model:value="cfg.smtp_from" placeholder="jt-ipam@example.com" />
        </n-form-item>

        <n-space align="center">
          <n-button type="success" :loading="saving" @click="save">
            <template #icon><n-icon><SaveIcon /></n-icon></template>
            {{ t("common.save") }}
          </n-button>
        </n-space>

        <n-form-item :label="t('notify_ch.test')" label-placement="top">
          <n-space align="center">
            <n-input v-model:value="testTo" :placeholder="t('notify_ch.test_to_ph')" style="width: 280px" />
            <n-button :loading="testing" @click="test">{{ t("notify_ch.test_send") }}</n-button>
          </n-space>
        </n-form-item>
      </n-space>
    </n-card>

    <!-- Telegram -->
    <n-card v-if="cfg" title="Telegram">
      <n-space vertical :size="12" style="max-width: 640px">
        <n-form-item :label="t('notify_ch.enable')" label-placement="left">
          <n-switch v-model:value="cfg.telegram_enabled" />
        </n-form-item>
        <n-alert type="info" :show-icon="false" :bordered="false" size="small">{{ t("notify_ch.tg_hint") }}</n-alert>
        <n-form-item label="Bot Token" label-placement="top">
          <n-input v-model:value="tgToken" type="password" show-password-on="click"
                   :placeholder="cfg.telegram_token_set ? t('notify_ch.secret_keep') : '123456:ABC-DEF...'" />
        </n-form-item>
        <n-form-item label="Chat ID" label-placement="top">
          <n-input v-model:value="cfg.telegram_chat_id" placeholder="-1001234567890 / @channel" />
        </n-form-item>
        <n-space align="center">
          <n-button type="success" :loading="saving" @click="save">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
          <n-button :loading="testingCh === 'telegram'" @click="testChannel('telegram')">{{ t("notify_ch.test_send") }}</n-button>
        </n-space>
      </n-space>
    </n-card>

    <!-- Slack -->
    <n-card v-if="cfg" title="Slack">
      <n-space vertical :size="12" style="max-width: 640px">
        <n-form-item :label="t('notify_ch.enable')" label-placement="left">
          <n-switch v-model:value="cfg.slack_enabled" />
        </n-form-item>
        <n-alert type="info" :show-icon="false" :bordered="false" size="small">{{ t("notify_ch.slack_hint") }}</n-alert>
        <n-form-item label="Incoming Webhook URL" label-placement="top">
          <n-input v-model:value="slackHook" type="password" show-password-on="click"
                   :placeholder="cfg.slack_webhook_set ? t('notify_ch.secret_keep') : 'https://hooks.slack.com/services/...'" />
        </n-form-item>
        <n-space align="center">
          <n-button type="success" :loading="saving" @click="save">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
          <n-button :loading="testingCh === 'slack'" @click="testChannel('slack')">{{ t("notify_ch.test_send") }}</n-button>
        </n-space>
      </n-space>
    </n-card>

    <!-- Microsoft Teams -->
    <n-card v-if="cfg" title="Microsoft Teams">
      <n-space vertical :size="12" style="max-width: 640px">
        <n-form-item :label="t('notify_ch.enable')" label-placement="left">
          <n-switch v-model:value="cfg.teams_enabled" />
        </n-form-item>
        <n-alert type="info" :show-icon="false" :bordered="false" size="small">{{ t("notify_ch.teams_hint") }}</n-alert>
        <n-form-item label="Incoming Webhook / Workflow URL" label-placement="top">
          <n-input v-model:value="teamsHook" type="password" show-password-on="click"
                   :placeholder="cfg.teams_webhook_set ? t('notify_ch.secret_keep') : 'https://...webhook.office.com/...'" />
        </n-form-item>
        <n-space align="center">
          <n-button type="success" :loading="saving" @click="save">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
          <n-button :loading="testingCh === 'teams'" @click="testChannel('teams')">{{ t("notify_ch.test_send") }}</n-button>
        </n-space>
      </n-space>
    </n-card>

    <!-- Nextcloud Talk -->
    <n-card v-if="cfg" title="Nextcloud Talk">
      <n-space vertical :size="12" style="max-width: 640px">
        <n-form-item :label="t('notify_ch.enable')" label-placement="left">
          <n-switch v-model:value="cfg.nextcloud_enabled" />
        </n-form-item>
        <n-alert type="info" :show-icon="false" :bordered="false" size="small">{{ t("notify_ch.nc_hint") }}</n-alert>
        <n-form-item :label="t('notify_ch.nc_url')" label-placement="top">
          <n-input v-model:value="cfg.nextcloud_url" placeholder="https://cloud.example.com" />
        </n-form-item>
        <n-form-item :label="t('notify_ch.nc_token')" label-placement="top">
          <n-input v-model:value="cfg.nextcloud_token" placeholder="conversation token" />
        </n-form-item>
        <n-form-item :label="t('notify_ch.nc_secret')" label-placement="top">
          <n-input v-model:value="ncSecret" type="password" show-password-on="click"
                   :placeholder="cfg.nextcloud_secret_set ? t('notify_ch.secret_keep') : t('notify_ch.nc_secret_ph')" />
        </n-form-item>
        <n-space align="center">
          <n-button type="success" :loading="saving" @click="save">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
          <n-button :loading="testingCh === 'nextcloud'" @click="testChannel('nextcloud')">{{ t("notify_ch.test_send") }}</n-button>
        </n-space>
      </n-space>
    </n-card>

    <!-- Zulip -->
    <n-card v-if="cfg" title="Zulip">
      <n-space vertical :size="12" style="max-width: 640px">
        <n-form-item :label="t('notify_ch.enable')" label-placement="left">
          <n-switch v-model:value="cfg.zulip_enabled" />
        </n-form-item>
        <n-alert type="info" :show-icon="false" :bordered="false" size="small">{{ t("notify_ch.zulip_hint") }}</n-alert>
        <n-form-item :label="t('notify_ch.zulip_site')" label-placement="top">
          <n-input v-model:value="cfg.zulip_site" placeholder="https://your.zulipchat.com" />
        </n-form-item>
        <n-form-item :label="t('notify_ch.zulip_bot_email')" label-placement="top">
          <n-input v-model:value="cfg.zulip_bot_email" placeholder="bot@your.zulipchat.com" />
        </n-form-item>
        <n-form-item label="API Key" label-placement="top">
          <n-input v-model:value="zulipKey" type="password" show-password-on="click"
                   :placeholder="cfg.zulip_api_key_set ? t('notify_ch.secret_keep') : t('notify_ch.zulip_key_ph')" />
        </n-form-item>
        <n-space :size="14">
          <n-form-item :label="t('notify_ch.zulip_stream')" label-placement="top">
            <n-input v-model:value="cfg.zulip_stream" placeholder="alerts" style="width: 200px" />
          </n-form-item>
          <n-form-item :label="t('notify_ch.zulip_topic')" label-placement="top">
            <n-input v-model:value="cfg.zulip_topic" placeholder="jt-ipam" style="width: 200px" />
          </n-form-item>
        </n-space>
        <n-space align="center">
          <n-button type="success" :loading="saving" @click="save">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
          <n-button :loading="testingCh === 'zulip'" @click="testChannel('zulip')">{{ t("notify_ch.test_send") }}</n-button>
        </n-space>
      </n-space>
    </n-card>

    <!-- 通用 Webhook -->
    <n-card v-if="cfg" :title="t('notify_ch.webhook_title')">
      <n-space vertical :size="12" style="max-width: 640px">
        <n-form-item :label="t('notify_ch.enable')" label-placement="left">
          <n-switch v-model:value="cfg.webhook_enabled" />
        </n-form-item>
        <n-alert type="info" :show-icon="false" :bordered="false" size="small">{{ t("notify_ch.webhook_hint") }}</n-alert>
        <n-form-item label="URL" label-placement="top">
          <n-input v-model:value="webhookUrl" type="password" show-password-on="click"
                   :placeholder="cfg.webhook_url_set ? t('notify_ch.secret_keep') : 'https://example.com/hook'" />
        </n-form-item>
        <n-form-item :label="t('notify_ch.webhook_token')" label-placement="top">
          <n-input v-model:value="webhookToken" type="password" show-password-on="click"
                   :placeholder="cfg.webhook_token_set ? t('notify_ch.secret_keep') : t('notify_ch.webhook_token_ph')" />
        </n-form-item>
        <n-space align="center">
          <n-button type="success" :loading="saving" @click="save">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
          <n-button :loading="testingCh === 'webhook'" @click="testChannel('webhook')">{{ t("notify_ch.test_send") }}</n-button>
        </n-space>
      </n-space>
    </n-card>
  </n-space>
</template>

<style scoped>
.ch-card {
  border: 1px dashed var(--n-border-color, #d9d9d9);
  border-radius: 8px;
  padding: 14px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  opacity: 0.6;
}
.ch-name { font-weight: 600; }
.nmx { width: 100%; border-collapse: collapse; font-size: 13.5px; }
.nmx th, .nmx td { padding: 8px 12px; border-bottom: 1px solid var(--n-border-color, rgba(128,128,128,.18)); text-align: left; }
.nmx th { font-weight: 600; opacity: .7; font-size: 12.5px; }
.nmx-c { text-align: center; width: 90px; }
.nmx-ev { font-weight: 500; }
.nmx-key { font-size: 11px; opacity: .5; }
.nmx-hint { font-size: 12px; opacity: .65; line-height: 1.5; margin: 4px 0 10px; }
</style>
