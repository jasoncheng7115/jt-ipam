<script setup lang="ts">
/**
 * 使用者設定頁。
 *
 * 比 phpIPAM 改進：
 *  - 三個 tab，每個 tab 不超過 ~5-7 個選項，不堆一頁
 *  - TOTP 啟用流程內嵌 SVG QR code（不要逼使用者貼 URI）
 *  - Preferences 即時儲存到 /api/v1/me/preferences，不需手動按 save
 */
import { onMounted, ref, watch } from "vue";
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
import { SettingsIcon } from "@/icons";
import QRCode from "qrcode";
import { storeToRefs } from "pinia";
import { useAuthStore } from "@/stores/auth";
import { useUiStore } from "@/stores/ui";
import {
  getPreferences,
  updatePreferences,
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
    // 同步到 ui store（讓主題 / 語言馬上反映）
    ui.setLocale(prefs.value.locale);
    ui.setTheme(prefs.value.theme);
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
const themeOptions = [
  { label: "Light", value: "light" },
  { label: "Dark", value: "dark" },
  { label: "Auto", value: "auto" },
];
const calendarOptions = [
  { label: "西元 (Gregorian)", value: "gregorian" },
  { label: "民國 (Minguo)", value: "minguo" },
];

// 是否啟用 TOTP — me 物件目前未含此欄位；若沒有 enrollment 就視為「未啟用 / 已啟用」皆可，
// 但我們以「server 接受 disable」為依據；先用 confirm 失敗作 fallback。
// （Phase 1.5 在 /me 增加 totp_enabled 欄位）
const totpStateUnknown = ref(true);

onMounted(loadPrefs);
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><SettingsIcon /></n-icon>
        <span>Settings</span>
      </n-space>
    </template>
    <n-tabs type="line" default-value="profile">
      <!-- Profile -->
      <n-tab-pane name="profile" tab="Profile">
        <n-descriptions v-if="me" bordered :column="1" label-style="width: 180px">
          <n-descriptions-item label="Username">{{ me.username }}</n-descriptions-item>
          <n-descriptions-item label="Email">{{ me.email }}</n-descriptions-item>
          <n-descriptions-item label="Display name">
            {{ me.display_name ?? "—" }}
          </n-descriptions-item>
          <n-descriptions-item label="Auth provider">{{ me.auth_provider }}</n-descriptions-item>
          <n-descriptions-item label="Admin">{{ me.is_admin ? "Yes" : "No" }}</n-descriptions-item>
          <n-descriptions-item label="Last login">
            {{ me.last_login_at ?? "—" }}
          </n-descriptions-item>
        </n-descriptions>
      </n-tab-pane>

      <!-- Security: TOTP -->
      <n-tab-pane name="security" tab="Security">
        <n-space vertical :size="16">
          <n-alert type="info">
            <strong>Two-factor authentication (TOTP)</strong> 啟用後，登入除了密碼還需要
            6 位數驗證碼，大幅提升帳號安全。建議使用 Google Authenticator / 1Password / Authy。
          </n-alert>

          <!-- 未在 enrollment 流程中：給「啟用 / 停用」按鈕 -->
          <n-space v-if="!enrollment">
            <n-button type="primary" :loading="totpBusy" @click="startEnroll">
              啟用 TOTP
            </n-button>
            <n-popconfirm @positive-click="disableTotp">
              <template #trigger>
                <n-button :loading="totpBusy">停用 TOTP（如已啟用）</n-button>
              </template>
              確定要停用 TOTP？停用後登入將不再要求 6 位數驗證碼。
            </n-popconfirm>
          </n-space>

          <!-- enrollment 流程中：顯示 QR + 驗證碼輸入 -->
          <div v-else>
            <n-space vertical :size="12">
              <strong>步驟 1：用 authenticator app 掃描 QR code</strong>
              <div v-html="qrSvg" class="qr"></div>
              <details>
                <summary>無法掃描？手動輸入</summary>
                <n-code :code="enrollment.otpauth_uri" language="plain" />
                <p style="font-size: 12px; opacity: 0.7">
                  Secret：<code>{{ enrollment.secret }}</code>
                </p>
              </details>

              <strong>步驟 2：輸入 app 顯示的 6 位數驗證碼</strong>
              <n-space>
                <n-input
                  v-model:value="code"
                  placeholder="123456"
                  maxlength="6"
                  style="width: 160px"
                  @keyup.enter="confirmEnroll"
                />
                <n-button type="primary" :loading="totpBusy" @click="confirmEnroll">
                  確認啟用
                </n-button>
                <n-button @click="cancelEnroll">取消</n-button>
              </n-space>
            </n-space>
          </div>
        </n-space>
      </n-tab-pane>

      <!-- Preferences -->
      <n-tab-pane name="preferences" tab="Preferences">
        <n-space v-if="prefs" vertical :size="16" style="max-width: 480px">
          <div>
            <label>Language</label>
            <n-select
              :value="prefs.locale"
              :options="localeOptions"
              @update:value="(v: any) => patchPref('locale', v)"
            />
          </div>
          <div>
            <label>Theme</label>
            <n-select
              :value="prefs.theme"
              :options="themeOptions"
              @update:value="(v: any) => patchPref('theme', v)"
            />
          </div>
          <div>
            <label>Calendar</label>
            <n-select
              :value="prefs.calendar"
              :options="calendarOptions"
              @update:value="(v: any) => patchPref('calendar', v)"
            />
          </div>
          <div>
            <label>Timezone</label>
            <n-input
              :value="prefs.timezone"
              placeholder="Asia/Taipei"
              @update:value="(v: any) => patchPref('timezone', v)"
            />
          </div>
          <div>
            <label>Page size</label>
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
