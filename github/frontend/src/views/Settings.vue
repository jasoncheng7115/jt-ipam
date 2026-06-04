<script setup lang="ts">
/**
 * 使用者設定頁。
 *
 * 比 phpIPAM 改進：
 *  - 三個 tab，每個 tab 不超過 ~5-7 個選項，不堆一頁
 *  - TOTP 啟用流程內嵌 SVG QR code(不要逼使用者貼 URI)
 *  - Preferences 即時儲存到 /api/v1/me/preferences，不需手動按 save
 */
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard,
  NTabs,
  NTabPane,
  NSpace,
  NDescriptions,
  NDescriptionsItem,
  NSelect,
  NInputNumber,
  NInput,
  NButton,
  NAlert,
  NCode,
  NPopconfirm,
  useMessage,
} from "naive-ui";
import { NIcon } from "naive-ui";
import { SettingsIcon, UsersIcon, LockIcon } from "@/icons";
import QRCode from "qrcode";
import { storeToRefs } from "pinia";
import { useAuthStore } from "@/stores/auth";
import { useUiStore } from "@/stores/ui";
import {
  getPreferences,
  updatePreferences,
} from "@/api/preferences";
import {
  type UserPreferences,
} from "@/api/preferences";
import * as totpApi from "@/api/totp";

const { t } = useI18n();
const auth = useAuthStore();
const ui = useUiStore();
const { me } = storeToRefs(auth);
const msg = useMessage();

// ── Preferences ──
const prefs = ref<UserPreferences | null>(null);
const prefsLoading = ref(false);

async function loadPrefs() {
  prefsLoading.value = true;
  try {
    prefs.value = await getPreferences();
    // locale 同步到 ui store；theme 不在這裡覆寫——以 ui store(localStorage) 為準，
    // 否則使用者剛在右上切換的佈景會在開設定頁時被後端舊值蓋回去。
    ui.setLocale(prefs.value.locale);
  } catch {
    msg.error(t("errors.network"));
  } finally {
    prefsLoading.value = false;
  }
}

async function patchPref<K extends keyof UserPreferences>(
  key: K,
  value: UserPreferences[K],
) {
  if (!prefs.value) return;
  prefs.value[key] = value;
  try {
    const updated = await updatePreferences({ [key]: value } as Partial<UserPreferences>);
    prefs.value = updated;
    if (key === "locale") ui.setLocale(value as "zh-TW" | "en-US");
    if (key === "theme") ui.setTheme(value as "light" | "dark" | "auto");
  } catch {
    msg.error(t("errors.network"));
  }
}

// ── TOTP enrollment ──
const enrollment = ref<{ secret: string; otpauth_uri: string } | null>(null);
const qrSvg = ref<string>("");
const code = ref("");
const totpBusy = ref(false);

async function startEnroll() {
  totpBusy.value = true;
  try {
    enrollment.value = await totpApi.enroll();
    qrSvg.value = await QRCode.toString(enrollment.value.otpauth_uri, {
      type: "svg",
      margin: 1,
      width: 200,
      errorCorrectionLevel: "M",
    });
  } catch {
    msg.error(t("errors.network"));
  } finally {
    totpBusy.value = false;
  }
}

async function confirmEnroll() {
  if (!enrollment.value || !code.value) return;
  totpBusy.value = true;
  try {
    await totpApi.confirm(enrollment.value.secret, code.value);
    enrollment.value = null;
    qrSvg.value = "";
    code.value = "";
    await auth.fetchMe();
    msg.success(t("settings.security.totp_enabled_msg"));
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("settings.security.totp_invalid"));
  } finally {
    totpBusy.value = false;
  }
}

async function disableTotp() {
  totpBusy.value = true;
  try {
    await totpApi.disable();
    await auth.fetchMe();
    msg.success(t("settings.security.totp_disabled_msg"));
  } catch {
    msg.error(t("errors.network"));
  } finally {
    totpBusy.value = false;
  }
}

function cancelEnroll() {
  enrollment.value = null;
  qrSvg.value = "";
  code.value = "";
}

const localeOptions = [
  { label: "繁體中文", value: "zh-TW" },
  { label: "English", value: "en-US" },
];
const themeOptions = computed(() => [
  { label: t("settings.prefs.theme_light"), value: "light" },
  { label: t("settings.prefs.theme_dark"),  value: "dark"  },
  { label: t("settings.prefs.theme_auto"),  value: "auto"  },
]);
const calendarOptions = computed(() => [
  { label: t("settings.prefs.calendar_gregorian"), value: "gregorian" },
  { label: t("settings.prefs.calendar_minguo"),    value: "minguo"    },
]);

onMounted(() => {
  void loadPrefs();
});
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><SettingsIcon /></n-icon>
        <span>{{ t("settings.title") }}</span>
      </n-space>
    </template>
    <n-tabs type="line" default-value="profile">
      <!-- Profile -->
      <n-tab-pane name="profile">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><UsersIcon /></n-icon>{{ t('settings.profile.tab') }}</span>
        </template>
        <n-descriptions v-if="me" bordered :column="1" label-placement="left"
                        label-style="width: 160px">
          <n-descriptions-item :label="t('settings.profile.username')">{{ me.username }}</n-descriptions-item>
          <n-descriptions-item :label="t('settings.profile.email')">{{ me.email }}</n-descriptions-item>
          <n-descriptions-item :label="t('settings.profile.display_name')">
            {{ me.display_name ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.profile.auth_provider')">{{ me.auth_provider }}</n-descriptions-item>
          <n-descriptions-item :label="t('settings.profile.admin')">
            {{ me.is_admin ? t("common.yes") : t("common.no") }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.profile.last_login')">
            {{ me.last_login_at ?? "—" }}
          </n-descriptions-item>
        </n-descriptions>
      </n-tab-pane>

      <!-- Security: TOTP -->
      <n-tab-pane name="security">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><LockIcon /></n-icon>{{ t('settings.security.tab') }}</span>
        </template>
        <n-space vertical :size="16">
          <n-alert type="info">
            <strong>{{ t("settings.security.totp_title") }}</strong>
            <span v-html="t('settings.security.totp_intro_html')"></span>
          </n-alert>

          <!-- 未在 enrollment 流程中：給「啟用 / 停用」按鈕 -->
          <n-space v-if="!enrollment">
            <n-button type="primary" :loading="totpBusy" @click="startEnroll">
              {{ t("settings.security.enable_totp") }}
            </n-button>
            <n-popconfirm @positive-click="disableTotp">
              <template #trigger>
                <n-button :loading="totpBusy">
                  {{ t("settings.security.disable_totp") }}
                </n-button>
              </template>
              {{ t("settings.security.disable_confirm") }}
            </n-popconfirm>
          </n-space>

          <!-- enrollment 流程中：顯示 QR + 驗證碼輸入 -->
          <div v-else>
            <n-space vertical :size="12">
              <strong>{{ t("settings.security.step1") }}</strong>
              <div v-html="qrSvg" class="qr"></div>
              <details>
                <summary>{{ t("settings.security.cannot_scan") }}</summary>
                <n-code :code="enrollment.otpauth_uri" language="plain" />
                <p style="font-size: 12px; opacity: 0.7">
                  Secret：<code>{{ enrollment.secret }}</code>
                </p>
              </details>

              <strong>{{ t("settings.security.step2") }}</strong>
              <n-space>
                <n-input
                  v-model:value="code"
                  placeholder="123456"
                  maxlength="6"
                  style="width: 160px"
                  @keyup.enter="confirmEnroll"
                />
                <n-button type="primary" :loading="totpBusy" @click="confirmEnroll">
                  {{ t("settings.security.confirm_enable") }}
                </n-button>
                <n-button @click="cancelEnroll">
                  {{ t("common.cancel") }}
                </n-button>
              </n-space>
            </n-space>
          </div>
        </n-space>
      </n-tab-pane>

      <!-- Preferences -->
      <n-tab-pane name="preferences">
        <template #tab>
          <span style="display:inline-flex;align-items:center;gap:6px"><n-icon :size="16"><SettingsIcon /></n-icon>{{ t('settings.prefs.tab') }}</span>
        </template>
        <n-space v-if="prefs" vertical :size="16" style="max-width: 480px">
          <div>
            <label>{{ t("settings.prefs.language") }}</label>
            <n-select
              :value="prefs.locale"
              :options="localeOptions"
              @update:value="(v: any) => patchPref('locale', v)"
            />
          </div>
          <div>
            <label>{{ t("settings.prefs.theme") }}</label>
            <n-select
              :value="ui.theme"
              :options="themeOptions"
              @update:value="(v: any) => patchPref('theme', v)"
            />
          </div>
          <div>
            <label>{{ t("settings.prefs.calendar") }}</label>
            <n-select
              :value="prefs.calendar"
              :options="calendarOptions"
              @update:value="(v: any) => patchPref('calendar', v)"
            />
          </div>
          <div>
            <label>{{ t("settings.prefs.timezone") }}</label>
            <n-input
              :value="prefs.timezone"
              placeholder="Asia/Taipei"
              @update:value="(v: any) => patchPref('timezone', v)"
            />
          </div>
          <div>
            <label>{{ t("settings.prefs.page_size") }}</label>
            <n-input-number
              :value="prefs.page_size"
              :min="10"
              :max="500"
              @update:value="(v: any) => patchPref('page_size', v)"
            />
          </div>
        </n-space>
        <p v-else style="opacity: 0.7">{{ t("common.loading") }}</p>
      </n-tab-pane>

      <!-- LLM (admin only) -->
    </n-tabs>
  </n-card>
</template>

<style scoped>
.qr {
  background: white;
  padding: 8px;
  border-radius: 4px;
  display: inline-block;
}
:deep(.qr svg) {
  display: block;
}
label {
  display: block;
  font-size: 12px;
  margin-bottom: 4px;
  opacity: 0.8;
}
</style>
