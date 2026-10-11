<template>
  <div>
    <n-card>
      <template #header>
        <n-space align="center" :size="8">
          <n-icon :component="KeyIcon" :size="20" />
          <span>{{ t("api_tokens.title") }}</span>
        </n-space>
      </template>
      <!-- 控制列：自標題列搬到內文最上方 -->
      <n-space align="center" justify="end" style="margin-bottom: 10px">
        <n-button size="small" @click="refresh">
          <template #icon><n-icon :component="RefreshIcon" /></template>
          {{ t("common.refresh") }}
        </n-button>
        <n-button size="small" type="primary" @click="openCreate">
          <template #icon><n-icon :component="PlusIcon" /></template>
          {{ t("common.add") }}
        </n-button>
      </n-space>

      <n-alert type="info" :show-icon="true" style="margin-bottom: 16px">
        {{ t("api_tokens.intro") }}
        <template v-if="docsUrl">
          <br />
          <a :href="docsUrl" target="_blank" rel="noopener">{{ t("api_tokens.manual_link") }}</a>
        </template>
      </n-alert>

      <n-data-table
        :columns="columns"
        :data="rows"
        :loading="loading"
        :pagination="pagination"
        :row-key="(r: ApiToken) => r.id"
        size="small"
        :scroll-x="1040"
      />
    </n-card>

    <!-- 建立 -->
    <n-modal v-model:show="createShow" preset="card" style="max-width: 560px" :title="t('api_tokens.create_title')">
      <n-form label-placement="top">
        <n-form-item :label="t('api_tokens.name')">
          <n-input v-model:value="form.name" :placeholder="t('api_tokens.name_ph')" />
        </n-form-item>
        <n-form-item :label="t('api_tokens.expires_in_days')">
          <n-input-number v-model:value="form.expires_in_days" :min="1" :max="365" style="width: 100%" />
        </n-form-item>
        <n-form-item :label="t('api_tokens.scope')">
          <div style="width: 100%">
            <n-radio-group v-model:value="form.readonly">
              <n-space vertical :size="6">
                <n-radio :value="false">{{ t("api_tokens.scope_full") }}</n-radio>
                <n-radio :value="true">{{ t("api_tokens.scope_read") }}</n-radio>
              </n-space>
            </n-radio-group>
            <div class="hint">{{ t("api_tokens.scope_hint") }}</div>
          </div>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="createShow = false">{{ t("common.cancel") }}</n-button>
          <n-button type="primary" :loading="saving" @click="doCreate">{{ t("common.save") }}</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 建立成功：一次性顯示明文 -->
    <n-modal
      v-model:show="createdShow"
      preset="card"
      style="max-width: 620px"
      :title="t('api_tokens.created_title')"
      :mask-closable="false"
      :closable="false"
    >
      <n-alert type="warning" :show-icon="true" style="margin-bottom: 12px">
        {{ t("api_tokens.created_warn") }}
      </n-alert>
      <n-input :value="created?.token ?? ''" readonly type="textarea" :autosize="{ minRows: 2 }" />
      <n-space style="margin-top: 12px" :size="8">
        <n-button size="small" @click="copyToken">
          <template #icon><n-icon :component="CopyIcon" /></template>
          {{ t("common.copy") }}
        </n-button>
      </n-space>
      <div class="hint" style="margin-top: 12px">{{ t("api_tokens.created_usage") }}</div>
      <n-code :code="curlSample" language="bash" style="margin-top: 6px" />
      <template #footer>
        <n-space justify="end">
          <n-button type="primary" @click="closeCreated">{{ t("api_tokens.created_done") }}</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import type { DataTableColumns } from "naive-ui";
import {
  NAlert, NButton, NCard, NCode, NDataTable, NForm, NFormItem, NIcon, NInput,
  NInputNumber, NModal, NPopconfirm, NRadio, NRadioGroup, NSpace, NTag, useMessage,
} from "naive-ui";
import { CopyIcon, DeleteIcon, KeyIcon, PlusIcon, RefreshIcon } from "@/icons";
import {
  createApiToken, listApiTokens, revokeApiToken,
  type ApiToken, type ApiTokenCreated,
} from "@/api/apiTokens";
import { fmtDateTime, fmtRelative } from "@/utils/datetime";
import { useTablePagination } from "@/composables/useTablePagination";

const { t } = useI18n();
const message = useMessage();
const pagination = useTablePagination();

const rows = ref<ApiToken[]>([]);
const loading = ref(false);
const saving = ref(false);
const createShow = ref(false);
const createdShow = ref(false);
const created = ref<ApiTokenCreated | null>(null);
const form = ref({ name: "", expires_in_days: 90, readonly: false });

// 手冊在 GitHub Pages（正式環境不開 /docs Swagger）
const docsUrl = "https://jasoncheng7115.github.io/jt-ipam/api.html";

const curlSample = computed(
  () =>
    `curl -H "Authorization: Bearer ${created.value?.token ?? "jt_prod_..."}" \\\n` +
    `  ${window.location.origin}/api/v1/subnets`,
);

async function refresh() {
  loading.value = true;
  try {
    rows.value = (await listApiTokens()).items;
  } catch {
    message.error(t("common.fail"));
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  form.value = { name: "", expires_in_days: 90, readonly: false };
  createShow.value = true;
}

async function doCreate() {
  if (!form.value.name.trim()) {
    message.warning(t("api_tokens.name_required"));
    return;
  }
  saving.value = true;
  try {
    created.value = await createApiToken({
      name: form.value.name.trim(),
      expires_in_days: form.value.expires_in_days,
      scopes: form.value.readonly ? ["read"] : [],
    });
    createShow.value = false;
    createdShow.value = true;
    await refresh();
  } catch {
    message.error(t("common.save_failed"));
  } finally {
    saving.value = false;
  }
}

async function copyToken() {
  try {
    await navigator.clipboard.writeText(created.value?.token ?? "");
    message.success(t("common.copied_clipboard"));
  } catch {
    message.error(t("common.fail"));
  }
}

function closeCreated() {
  createdShow.value = false;
  created.value = null;
}

async function doRevoke(id: string) {
  try {
    await revokeApiToken(id);
    message.success(t("api_tokens.revoked"));
    await refresh();
  } catch {
    message.error(t("common.delete_failed"));
  }
}

function statusTag(r: ApiToken) {
  if (r.revoked_at) return h(NTag, { size: "small", type: "error" }, () => t("api_tokens.st_revoked"));
  if (new Date(r.expires_at).getTime() <= Date.now())
    return h(NTag, { size: "small", type: "warning" }, () => t("api_tokens.st_expired"));
  return h(NTag, { size: "small", type: "success" }, () => t("api_tokens.st_active"));
}

const columns = computed<DataTableColumns<ApiToken>>(() => [
  { title: t("api_tokens.name"), key: "name", minWidth: 160, ellipsis: { tooltip: true } },
  {
    title: t("api_tokens.prefix"), key: "token_prefix", width: 120,
    render: (r) => h("code", null, `${r.token_prefix}…`),
  },
  {
    title: t("api_tokens.scope"), key: "scopes", width: 110,
    render: (r) =>
      r.scopes?.includes("read")
        ? h(NTag, { size: "small", type: "info" }, () => t("api_tokens.tag_read"))
        : h(NTag, { size: "small" }, () => t("api_tokens.tag_full")),
  },
  { title: t("api_tokens.status"), key: "st", width: 100, render: statusTag },
  {
    title: t("api_tokens.expires_at"), key: "expires_at", width: 170,
    render: (r) => fmtDateTime(r.expires_at),
  },
  {
    title: t("api_tokens.last_used"), key: "last_used_at", width: 150,
    render: (r) => (r.last_used_at ? fmtRelative(r.last_used_at) : "—"),
  },
  { title: t("api_tokens.last_used_ip"), key: "last_used_ip", width: 140, render: (r) => r.last_used_ip || "—" },
  {
    title: t("common.actions"), key: "actions", width: 90, align: "center",
    render: (r) =>
      r.revoked_at
        ? "—"
        : h(
            NPopconfirm,
            { onPositiveClick: () => doRevoke(r.id) },
            {
              trigger: () =>
                h(
                  NButton,
                  { size: "tiny", type: "error", quaternary: true, title: t("api_tokens.revoke") },
                  { icon: () => h(NIcon, { component: DeleteIcon }) },
                ),
              default: () => t("api_tokens.revoke_confirm"),
            },
          ),
  },
]);

onMounted(refresh);
</script>

<style scoped>
.hint {
  font-size: 12px;
  opacity: 0.7;
  margin-top: 6px;
  line-height: 1.6;
}
</style>
