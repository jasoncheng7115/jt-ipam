<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NSwitch, NInput, NInputNumber, NSelect, NButton, NIcon,
  NFormItem, NAlert, NTag, NGrid, NGridItem, useMessage,
} from "naive-ui";
import { SettingsIcon, SaveIcon } from "@/icons";
import {
  getNotificationChannels, setNotificationChannels, sendTestEmail,
  type NotificationChannels,
} from "@/api/notify_channels";

const { t } = useI18n();
const msg = useMessage();

const loading = ref(false);
const saving = ref(false);
const testing = ref(false);
const cfg = ref<NotificationChannels | null>(null);
const pw = ref("");          // 留空＝不變更
const testTo = ref("");

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
    };
    if (pw.value) patch.smtp_password = pw.value;
    cfg.value = await setNotificationChannels(patch);
    pw.value = "";
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

function channelLabel(key: string): string {
  const m: Record<string, string> = {
    telegram: "Telegram", slack: "Slack", teams: "Microsoft Teams",
    nextcloud: "Nextcloud Talk", zulip: "Zulip",
  };
  return m[key] ?? key;
}

onMounted(load);
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

    <!-- 其他管道（開發中，反灰）-->
    <n-card :title="t('notify_ch.other_title')">
      <n-grid :cols="3" :x-gap="12" :y-gap="12" responsive="screen">
        <n-grid-item v-for="ch in otherChannels" :key="ch.key">
          <div class="ch-card">
            <div class="ch-name">{{ channelLabel(ch.key) }}</div>
            <n-tag size="small" :bordered="false">{{ t("notify_ch.coming_soon") }}</n-tag>
          </div>
        </n-grid-item>
      </n-grid>
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
</style>
