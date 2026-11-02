<script setup lang="ts">
/**
 * 網域的新增 / 編輯對話框：清單頁與詳細資料頁共用（同一個物件只有一套編輯表單）。
 * 註冊商、到期日、NS 會由 RDAP 自動查；這裡填的是查不到或要補充的。不存註冊商密碼。
 */
import JtDatePicker from "@/components/JtDatePicker.vue";
import { computed, reactive, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NButton, NForm, NFormItem, NIcon, NInput, NModal, NSelect, NSpace, NSwitch, useMessage,
} from "naive-ui";
import { CancelIcon, SaveIcon } from "@/icons";
import { apiErrMsg } from "@/api/client";
import { Domains, type Domain } from "@/api/domains";

const props = defineProps<{ show: boolean; domain?: Domain | null }>();
const emit = defineEmits<{ "update:show": [boolean]; saved: [Domain] }>();
const { t } = useI18n();
const msg = useMessage();

const form = reactive({
  busy: false, name: "", registrar: "", registrar_url: "", registrar_account: "", dns_provider: "",
  services: "", description: "", expires_at: null as number | null, auto_renew: null as boolean | null,
  lookup: true,
});
const isEdit = computed(() => !!props.domain);

function toTs(d: string | null): number | null {
  if (!d) return null;
  const [y, m, dd] = d.split("-").map(Number);
  return new Date(y, m - 1, dd).getTime();
}
function toDate(ts: number | null): string | null {
  if (ts == null) return null;
  const d = new Date(ts);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
watch(() => props.show, (v) => {
  if (!v) return;
  const d = props.domain;
  Object.assign(form, {
    busy: false, name: d?.name ?? "", registrar: d?.registrar ?? "", registrar_url: d?.registrar_url ?? "",
    registrar_account: d?.registrar_account ?? "", dns_provider: d?.dns_provider ?? "", services: d?.services ?? "",
    description: d?.description ?? "", expires_at: toTs(d?.expires_at ?? null), auto_renew: d?.auto_renew ?? null,
    lookup: true,
  });
});
const renewOpts = computed(() => [
  { label: t("domains.auto_renew_unknown"), value: "unknown" },
  { label: t("domains.auto_renew_yes"), value: "yes" },
  { label: t("domains.auto_renew_no"), value: "no" },
]);
const renew = computed({
  get: () => (form.auto_renew == null ? "unknown" : form.auto_renew ? "yes" : "no"),
  set: (v: string) => { form.auto_renew = v === "unknown" ? null : v === "yes"; },
});
const urlBad = computed(() => !!form.registrar_url.trim() && !/^https?:\/\//i.test(form.registrar_url.trim()));

async function save() {
  if ((!isEdit.value && !form.name.trim()) || urlBad.value) return;
  form.busy = true;
  const body = {
    registrar: form.registrar.trim() || null, registrar_url: form.registrar_url.trim() || null,
    registrar_account: form.registrar_account.trim() || null, dns_provider: form.dns_provider.trim() || null,
    services: form.services.trim() || null, description: form.description.trim() || null,
    expires_at: toDate(form.expires_at), auto_renew: form.auto_renew,
  };
  try {
    const d = isEdit.value
      ? await Domains.update(props.domain!.id, body)
      : await Domains.create({ ...body, name: form.name.trim(), lookup: form.lookup });
    emit("saved", d);
    emit("update:show", false);
    msg.success(t("common.saved"));
  } catch (e) { msg.error(apiErrMsg(e)); } finally { form.busy = false; }
}
</script>

<template>
  <n-modal :show="show" preset="card" style="width: 560px; max-width: 95vw"
           :title="isEdit ? t('domains.edit_title', { name: domain?.name }) : t('domains.add')"
           @update:show="(v: boolean) => emit('update:show', v)">
    <n-form label-placement="top">
      <n-form-item v-if="!isEdit" :label="t('domains.name')">
        <n-input v-model:value="form.name" placeholder="example.com" maxlength="300" data-testid="domain-name" />
      </n-form-item>
      <div v-if="!isEdit" class="hint" style="margin: -12px 0 12px">{{ t("domains.name_hint") }}</div>
      <n-form-item :label="t('domains.registrar')">
        <n-input v-model:value="form.registrar" maxlength="128" :placeholder="t('domains.from_rdap_ph')" />
      </n-form-item>
      <n-form-item :label="t('domains.registrar_url')" :feedback="urlBad ? t('domains.url_bad') : ''"
                   :validation-status="urlBad ? 'error' : undefined">
        <n-input v-model:value="form.registrar_url" maxlength="2048" placeholder="https://" />
      </n-form-item>
      <n-form-item :label="t('domains.registrar_account')">
        <n-input v-model:value="form.registrar_account" maxlength="128" />
      </n-form-item>
      <div class="hint" style="margin: -12px 0 12px">{{ t("domains.no_password_hint") }}</div>
      <n-form-item :label="t('domains.dns_provider')">
        <n-input v-model:value="form.dns_provider" maxlength="128" :placeholder="t('domains.dns_provider_ph')" />
      </n-form-item>
      <n-form-item :label="t('domains.expires_manual')">
        <jt-date-picker v-model:value="form.expires_at" type="date" clearable style="width: 100%" />
      </n-form-item>
      <div class="hint" style="margin: -12px 0 12px">{{ t("domains.expires_manual_hint") }}</div>
      <n-form-item :label="t('domains.auto_renew')">
        <n-select v-model:value="renew" :options="renewOpts" />
      </n-form-item>
      <n-form-item :label="t('domains.services')">
        <n-input v-model:value="form.services" type="textarea" :autosize="{ minRows: 2 }" maxlength="4096"
                 :placeholder="t('domains.services_ph')" />
      </n-form-item>
      <n-form-item :label="t('sections.description')">
        <n-input v-model:value="form.description" type="textarea" :autosize="{ minRows: 2 }" maxlength="4096" />
      </n-form-item>
      <n-form-item v-if="!isEdit" :label="t('domains.lookup_now')">
        <n-switch v-model:value="form.lookup" />
      </n-form-item>
    </n-form>
    <template #footer>
      <n-space justify="end">
        <n-button @click="emit('update:show', false)">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" :loading="form.busy" :disabled="form.busy || urlBad || (!isEdit && !form.name.trim())"
                  data-testid="domain-save" @click="save">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </template>
  </n-modal>
</template>

<style scoped>
.hint { font-size: 12.5px; opacity: .7; }
</style>
