<script setup lang="ts">
/**
 * 掛在某個物件上的聯絡人（電路、供應商、站對站 VPN…）：表格＋新增（選既有或直接建一位）＋移除。
 * 電路與 VPN 詳細資料共用；後端 `/contact-assignments`（寫入只限管理員）。
 */
import { computed, h, reactive, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NButton, NDataTable, NForm, NFormItem, NIcon, NInput, NModal, NPopconfirm, NRadioButton, NRadioGroup, NSelect,
  NSpace, NTag, useMessage, type DataTableColumns,
} from "naive-ui";
import { CancelIcon, DeleteIcon, PlusIcon, SaveIcon } from "@/icons";
import { apiClient, apiErrMsg } from "@/api/client";
import { Advanced, type AssignedContact, type Contact, type ContactObjectType, type ContactRole } from "@/api/phase3";

const props = defineProps<{
  objectType: ContactObjectType; objectId: string; contacts: AssignedContact[]; title: string;
  admin: boolean; addTitle?: string;
}>();
const emit = defineEmits<{ changed: [] }>();
const { t, te } = useI18n();
const msg = useMessage();

const roles = ref<ContactRole[]>([]);
const all = ref<Contact[]>([]);

function roleName(r: AssignedContact["role"] | ContactRole | null): string {
  if (!r) return "—";
  const k = r.builtin_key ? `circuits.role_${r.builtin_key}` : "";
  return k && te(k) ? t(k) : r.name;
}

const cols = computed<DataTableColumns<AssignedContact>>(() => [
  { title: t("cols.name"), key: "name", minWidth: 120, render: (r) => r.contact.name },
  { title: t("circuits.contact_role"), key: "role", width: 120,
    render: (r) => h(NTag, { size: "small", bordered: false }, () => roleName(r.role)) },
  { title: t("circuits.contact_title"), key: "title", width: 120, render: (r) => r.contact.title || "—" },
  { title: t("circuits.contact_phone"), key: "phone", width: 150,
    render: (r) => (r.contact.phone ? h("a", { href: `tel:${r.contact.phone}` }, r.contact.phone) : "—") },
  { title: t("circuits.contact_email"), key: "email", minWidth: 180,
    render: (r) => (r.contact.email ? h("a", { href: `mailto:${r.contact.email}` }, r.contact.email) : "—") },
  ...(props.admin ? [{
    title: t("common.actions"), key: "_", width: 70, className: "col-actions",
    render: (r: AssignedContact) => h(NPopconfirm, { onPositiveClick: () => remove(r) }, {
      trigger: () => h(NButton, { size: "tiny", type: "error", ghost: true, title: t("common.delete") },
                       { icon: () => h(NIcon, null, () => h(DeleteIcon)) }),
      default: () => t("circuits.contact_remove_confirm", { name: r.contact.name }),
    }),
  }] : []),
]);

const form = reactive({
  show: false, busy: false, mode: "pick" as "pick" | "new", contact_id: null as string | null,
  role_id: null as string | null, name: "", phone: "", email: "", title: "",
});
async function open() {
  Object.assign(form, { show: true, busy: false, mode: "pick", contact_id: null, role_id: null,
                        name: "", phone: "", email: "", title: "" });
  try {
    [roles.value, all.value] = await Promise.all([Advanced.contactRoles(), Advanced.contacts()]);
    if (!all.value.length) form.mode = "new";
  } catch (e) { msg.error(apiErrMsg(e)); }
}
const roleOpts = computed(() => roles.value.map((r) => ({ label: roleName(r), value: r.id })));
const contactOpts = computed(() => all.value.map((c) => ({
  label: [c.name, c.phone, c.email].filter(Boolean).join(" · "), value: c.id })));
const canSave = computed(() => (form.mode === "pick" ? !!form.contact_id : form.name.trim().length > 0));
async function save() {
  if (!canSave.value) return;
  form.busy = true;
  try {
    let cid = form.contact_id;
    if (form.mode === "new") {
      const { data: c } = await apiClient.post<Contact>("/api/v1/contacts", {
        name: form.name.trim(), phone: form.phone.trim() || null, email: form.email.trim() || null,
        title: form.title.trim() || null,
      });
      cid = c.id;
    }
    await Advanced.assignContact({ contact_id: cid!, role_id: form.role_id, object_type: props.objectType,
                                   object_id: props.objectId });
    form.show = false;
    msg.success(t("common.saved"));
    emit("changed");
  } catch (e) { msg.error(apiErrMsg(e)); } finally { form.busy = false; }
}
async function remove(r: AssignedContact) {
  try {
    await Advanced.deleteAssignment(r.id);
    emit("changed");
  } catch (e) { msg.error(apiErrMsg(e)); }
}
</script>

<template>
  <div class="sec">
    <n-space align="center" justify="space-between" style="margin-bottom: 6px">
      <span class="sec-title">{{ title }}</span>
      <n-button v-if="admin" size="small" type="primary" :data-testid="`contact-add-${objectType}`" @click="open">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("circuits.contact_add") }}
      </n-button>
    </n-space>
    <n-data-table size="small" :columns="cols" :data="contacts" :row-key="(r: AssignedContact) => r.id" :bordered="false" />

    <n-modal v-model:show="form.show" preset="card" style="width: 520px; max-width: 95vw" :title="addTitle || t('circuits.contact_add')">
      <n-form label-placement="top">
        <n-form-item>
          <n-radio-group v-model:value="form.mode" size="small">
            <n-radio-button value="pick" :disabled="!all.length">{{ t("circuits.contact_pick") }}</n-radio-button>
            <n-radio-button value="new">{{ t("circuits.contact_new") }}</n-radio-button>
          </n-radio-group>
        </n-form-item>
        <n-form-item v-if="form.mode === 'pick'" :label="t('circuits.contact')">
          <n-select v-model:value="form.contact_id" filterable :options="contactOpts" data-testid="contact-pick" />
        </n-form-item>
        <template v-else>
          <n-form-item :label="t('cols.name')">
            <n-input v-model:value="form.name" maxlength="128" data-testid="contact-name" />
          </n-form-item>
          <n-form-item :label="t('circuits.contact_phone')">
            <n-input v-model:value="form.phone" maxlength="64" />
          </n-form-item>
          <n-form-item :label="t('circuits.contact_email')">
            <n-input v-model:value="form.email" maxlength="255" />
          </n-form-item>
          <n-form-item :label="t('circuits.contact_title')">
            <n-input v-model:value="form.title" maxlength="128" />
          </n-form-item>
        </template>
        <n-form-item :label="t('circuits.contact_role')">
          <n-select v-model:value="form.role_id" clearable :options="roleOpts" data-testid="contact-role" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="form.show = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>
            {{ t("common.cancel") }}
          </n-button>
          <n-button type="primary" :loading="form.busy" :disabled="!canSave || form.busy" data-testid="contact-save"
                    @click="save">
            <template #icon><n-icon><SaveIcon /></n-icon></template>
            {{ t("common.save") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.sec + .sec { margin-top: 18px; }
.sec-title { font-weight: 600; }
</style>
