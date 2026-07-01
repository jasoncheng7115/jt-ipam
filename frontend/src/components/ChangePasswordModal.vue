<script setup lang="ts">
/** 本機帳號自助變更密碼。外部認證帳號（LDAP/SSO）不會看到入口（MainLayout 以 auth_provider 過濾）。 */
import { computed, ref, watch } from "vue";
import { NModal, NCard, NForm, NFormItem, NInput, NButton, NSpace, NIcon, useMessage } from "naive-ui";
import { useI18n } from "vue-i18n";
import { apiClient } from "@/api/client";
import { LockIcon, SaveIcon, CancelIcon } from "@/icons";

const props = defineProps<{ show: boolean }>();
const emit = defineEmits<{ (e: "update:show", v: boolean): void }>();
const { t } = useI18n();
const msg = useMessage();

const current = ref("");
const next = ref("");
const confirm = ref("");
const busy = ref(false);

const tooShort = computed(() => next.value.length > 0 && next.value.length < 12);
const mismatch = computed(() => confirm.value.length > 0 && next.value !== confirm.value);
const sameAsOld = computed(() => next.value.length > 0 && next.value === current.value);
const canSubmit = computed(() =>
  current.value.length > 0 && next.value.length >= 12 && next.value === confirm.value && !sameAsOld.value,
);

watch(() => props.show, (v) => {
  if (v) { current.value = ""; next.value = ""; confirm.value = ""; busy.value = false; }
});

async function submit() {
  if (!canSubmit.value || busy.value) return;
  busy.value = true;
  try {
    await apiClient.post("/api/v1/auth/change-password", {
      current_password: current.value, new_password: next.value,
    });
    msg.success(t("account.pw_changed"));
    emit("update:show", false);
  } catch (e: any) {
    const code = e?.response?.data?.detail;
    const map: Record<string, string> = {
      current_password_incorrect: t("account.current_pw_wrong"),
      same_password: t("account.same_pw"),
      external_auth: t("account.external_auth"),
    };
    msg.error(map[code] ?? t("account.pw_change_failed"));
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <n-modal :show="show" @update:show="(v) => emit('update:show', v)">
    <n-card style="max-width: 440px" :bordered="false" role="dialog">
      <template #header>
        <span style="display:inline-flex;align-items:center;gap:8px">
          <n-icon :size="18"><LockIcon /></n-icon>{{ t("account.change_password") }}
        </span>
      </template>
      <n-form @submit.prevent="submit">
        <n-form-item :label="t('account.current_pw')">
          <n-input v-model:value="current" type="password" show-password-on="click"
                   :placeholder="t('account.current_pw')" @keydown.enter="submit" />
        </n-form-item>
        <n-form-item :label="t('account.new_pw')"
                     :feedback="tooShort ? t('account.pw_min') : (sameAsOld ? t('account.same_pw') : '')"
                     :validation-status="(tooShort || sameAsOld) ? 'error' : undefined">
          <n-input v-model:value="next" type="password" show-password-on="click"
                   :placeholder="t('account.pw_min')" @keydown.enter="submit" />
        </n-form-item>
        <n-form-item :label="t('account.confirm_pw')"
                     :feedback="mismatch ? t('account.pw_mismatch') : ''"
                     :validation-status="mismatch ? 'error' : undefined">
          <n-input v-model:value="confirm" type="password" show-password-on="click"
                   :placeholder="t('account.confirm_pw')" @keydown.enter="submit" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="emit('update:show', false)">
            <template #icon><n-icon><CancelIcon /></n-icon></template>
            {{ t("common.cancel") }}
          </n-button>
          <n-button type="primary" :disabled="!canSubmit" :loading="busy" @click="submit">
            <template #icon><n-icon><SaveIcon /></n-icon></template>
            {{ t("account.change_password") }}
          </n-button>
        </n-space>
      </template>
    </n-card>
  </n-modal>
</template>
