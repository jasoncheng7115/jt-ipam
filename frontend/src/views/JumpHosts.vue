<script setup lang="ts">
/**
 * 跳板主機（issue #24 階段一）：主控台可以改成「後端 → 跳板 → 目標」。
 *
 * 這一頁的重點是**主機金鑰指紋**：跳板是整條路徑的中間人，沒有釘選指紋就不允許連線。
 * 所以流程刻意是兩步 —— 先「測試連線」取回指紋、人核對過，再按「信任並儲存」。
 */
import { computed, h, onMounted, ref } from "vue";
import { fmtDateTime } from "@/utils/datetime";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NButton, NTag, NIcon, NTooltip, NAlert, NModal, NForm,
  NFormItem, NInput, NInputNumber, NSwitch, NRadioGroup, NRadio, NPopconfirm, NCode,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  listJumpHosts, createJumpHost, updateJumpHost, deleteJumpHost, testJumpHost, jumpHostUsage,
  type JumpHost, type JumpHostProbe,
} from "@/api/jumpHosts";
import {
  TerminalIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, TestIcon, SaveIcon, CancelIcon,
  InfoIcon, CloneIcon, CheckIcon,
} from "@/icons";
import { autoSort } from "@/composables/useTableSort";
import { apiErrMsg } from "@/api/client";

const { t } = useI18n();
// embedded：放在掃描代理頁的頁籤裡時不要自己的卡片標題與外框
const props = withDefaults(defineProps<{ embedded?: boolean }>(), { embedded: false });
void props;
const msg = useMessage();
// 需求與設定說明（使用者 2026-10-02：「跳板主機要是什麼系統、有什麼條件，在這邊沒看到說明」）
const showHelp = ref(false);
// 只給轉發用的專用帳號：jt-ipam 只登入並做本機轉發（direct-tcpip），不執行任何指令，所以不需要 shell。
// 這份設定實測過（OpenSSH 9.2：nologin 帳號＋Match 區塊，轉發可用、互動登入被拒）
const SETUP_SNIPPET = [
  "# 1. 建立只供轉發的帳號（不給 shell）",
  "useradd -m -s /usr/sbin/nologin jtipam-jump",
  "",
  "# 2. 產生金鑰：公鑰放進跳板；私鑰（jtipam-jump）內容貼到 jt-ipam 後刪掉",
  "ssh-keygen -t ed25519 -N '' -C jt-ipam -f ./jtipam-jump",
  "install -d -m 700 -o jtipam-jump /home/jtipam-jump/.ssh",
  "install -m 600 -o jtipam-jump ./jtipam-jump.pub /home/jtipam-jump/.ssh/authorized_keys",
  "",
  "# 3. 加在 /etc/ssh/sshd_config 最後面，再 systemctl reload ssh",
  "Match User jtipam-jump",
  "    AllowTcpForwarding local",
  "    PermitTTY no",
  "    X11Forwarding no",
  "    AllowAgentForwarding no",
].join("\n");
async function copySnippet() {
  try { await navigator.clipboard.writeText(SETUP_SNIPPET); msg.success(t("common.copied")); }
  catch { /* 瀏覽器不給用剪貼簿時，使用者照樣可以直接選取上面的文字 */ }
}

const rows = ref<JumpHost[]>([]);
const loading = ref(false);
const show = ref(false);
const editing = ref<JumpHost | null>(null);

const probe = ref<JumpHostProbe | null>(null);
const probeFor = ref<JumpHost | null>(null);
const probeOpen = ref(false);
const usage = ref<{ subnets: number; ips: number } | null>(null);

function blankForm() {
  return {
    name: "", host: "", port: 22, username: "root",
    auth_kind: "key" as "key" | "password",
    private_key: "", password: "",
    enabled: true, max_sessions: 10, description: "",
  };
}
const form = ref(blankForm());

async function refresh() {
  loading.value = true;
  try { rows.value = (await listJumpHosts()).items; }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}

function openCreate() {
  editing.value = null;
  form.value = blankForm();
  show.value = true;
}

function openEdit(r: JumpHost) {
  editing.value = r;
  form.value = {
    name: r.name, host: r.host, port: r.port, username: r.username,
    auth_kind: r.auth_kind, private_key: "", password: "",
    enabled: r.enabled, max_sessions: r.max_sessions, description: r.description ?? "",
  };
  show.value = true;
}

async function submit() {
  const payload: Record<string, unknown> = {
    name: form.value.name, host: form.value.host, port: form.value.port,
    username: form.value.username, auth_kind: form.value.auth_kind,
    enabled: form.value.enabled, max_sessions: form.value.max_sessions,
    description: form.value.description ?? "",
  };
  // 空白＝不修改（編輯既有跳板時不必重打金鑰／密碼）
  if (form.value.private_key) payload.private_key = form.value.private_key;
  if (form.value.password) payload.password = form.value.password;
  try {
    if (editing.value) await updateJumpHost(editing.value.id, payload);
    else await createJumpHost(payload as never);
    show.value = false;
    msg.success(t("common.ok"));
    await refresh();
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function test(r: JumpHost) {
  probeFor.value = r;
  probe.value = null;
  probeOpen.value = true;
  try { probe.value = await testJumpHost(r.id); }
  catch (e) { msg.error(apiErrMsg(e)); probeOpen.value = false; }
}

/** 核對過指紋之後才釘選 —— 這一步就是「我確認對面是我以為的那台機器」。 */
async function trustFingerprint() {
  if (!probeFor.value || !probe.value) return;
  try {
    await updateJumpHost(probeFor.value.id, { host_key_fingerprint: probe.value.fingerprint });
    msg.success(t("jump_hosts.trusted"));
    probeOpen.value = false;
    await refresh();
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function confirmDelete(r: JumpHost) {
  // 刪掉會讓指向它的子網路／IP 回到直連 —— 在網段重疊的站台，那等於連到別人。
  // 所以刪除前先把影響範圍數出來給人看。
  try {
    const u = await jumpHostUsage(r.id);
    usage.value = { subnets: u.subnets.length, ips: u.ips.length };
  } catch { usage.value = null; }
}

async function del(id: string) {
  try { await deleteJumpHost(id); usage.value = null; await refresh(); }
  catch (e) { msg.error(apiErrMsg(e)); }
}

function iconAction(icon: unknown, label: string, onClick: () => void, type?: string) {
  return h(NTooltip, null, {
    trigger: () => h(NButton, { size: "small", quaternary: true, type: type as never,
      onClick: (e: MouseEvent) => { e.stopPropagation(); onClick(); } },
      { icon: () => h(NIcon, null, () => h(icon as never)) }),
    default: () => label,
  });
}

const cols = computed<DataTableColumns<JumpHost>>(() => autoSort([
  { title: t("common.name"), key: "name", minWidth: 140, ellipsis: { tooltip: true } },
  {
    title: t("jump_hosts.endpoint"), key: "host", minWidth: 170, ellipsis: { tooltip: true },
    render: (r) => `${r.username}@${r.host}:${r.port}`,
  },
  {
    title: t("jump_hosts.auth"), key: "auth_kind", width: 110,
    render: (r) => h(NTag, { size: "small", bordered: false,
      type: r.has_secret ? "default" : "warning" },
      () => r.auth_kind === "key" ? t("jump_hosts.auth_key") : t("jump_hosts.auth_password")),
  },
  {
    // 沒釘選指紋的跳板一定連不了 —— 這一欄要一眼看得出來，不要等到連線失敗才知道
    title: t("jump_hosts.host_key"), key: "host_key_fingerprint", minWidth: 150,
    ellipsis: { tooltip: true },
    render: (r) => r.host_key_fingerprint
      ? h(NTag, { size: "small", type: "success", bordered: false },
        () => t("jump_hosts.pinned"))
      : h(NTag, { size: "small", type: "warning", bordered: false },
        () => t("jump_hosts.not_pinned")),
  },
  {
    title: t("common.status"), key: "enabled", width: 100,
    render: (r) => h(NTag, { type: r.enabled ? "success" : "default", size: "small" },
      () => r.enabled ? t("common.enabled") : t("common.disabled")),
  },
  { title: t("jump_hosts.max_sessions"), key: "max_sessions", width: 110 },
  {
    title: t("jump_hosts.last_ok"), key: "last_ok_at", width: 165,
    render: (r) => fmtDateTime(r.last_ok_at),
  },
  {
    title: t("cols.last_error"), key: "last_error", minWidth: 140,
    ellipsis: { tooltip: true }, render: (r) => r.last_error ?? "—",
  },
  {
    title: t("common.actions"), key: "actions", className: "col-actions", width: 150,
    render: (r) => h(NSpace, { size: 2, wrapItem: false, wrap: false }, () => [
      iconAction(EditIcon, t("common.edit"), () => openEdit(r)),
      iconAction(TestIcon, t("jump_hosts.test"), () => test(r)),
      h(NPopconfirm, { onPositiveClick: () => del(r.id), onShow: () => confirmDelete(r) }, {
        trigger: () => iconAction(DeleteIcon, t("common.delete"), () => {}, "error"),
        default: () => usage.value && (usage.value.subnets || usage.value.ips)
          ? t("jump_hosts.delete_in_use", usage.value)
          : t("common.confirm_delete"),
      }),
    ]),
  },
]));

onMounted(() => { void refresh(); });
</script>

<template>
  <!-- 放在掃描代理頁的頁籤裡時不要再套一層卡片的外框與底色（用 inline style：Naive UI 自己的樣式會蓋過 scoped CSS） -->
  <n-card :bordered="!embedded"
          :style="embedded ? 'border: none; background: transparent; box-shadow: none' : undefined"
          :content-style="embedded ? 'padding: 0' : undefined">
    <template v-if="!embedded" #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><TerminalIcon /></n-icon>
        <span>{{ t("jump_hosts.title") }}</span>
      </n-space>
    </template>

    <n-alert type="info" :bordered="false" style="margin-bottom: 12px">
      {{ t("jump_hosts.intro") }}
    </n-alert>

    <n-space style="margin-bottom: 12px">
      <n-button @click="refresh" :loading="loading">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>
        {{ t("common.refresh") }}
      </n-button>
      <n-button type="primary" @click="openCreate">
        <template #icon><n-icon><PlusIcon /></n-icon></template>
        {{ t("common.create") }}
      </n-button>
      <n-button quaternary data-testid="jump-help-btn" @click="showHelp = true">
        <template #icon><n-icon><InfoIcon /></n-icon></template>
        {{ t("jump_hosts.help_button") }}
      </n-button>
    </n-space>

    <n-data-table :columns="cols" :data="rows" :loading="loading" :bordered="false"
                  :scroll-x="1150" />

    <!-- 新增 / 編輯 -->
    <n-modal v-model:show="show" preset="card"
             :title="editing ? t('common.edit') : `${t('common.create')} — ${t('jump_hosts.title')}`"
             style="width: 600px; max-width: calc(100vw - 32px)">
      <n-form>
        <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" /></n-form-item>
        <n-form-item :label="t('jump_hosts.host')">
          <n-input v-model:value="form.host" placeholder="198.51.100.9" />
        </n-form-item>
        <n-form-item :label="t('jump_hosts.port')">
          <n-input-number v-model:value="form.port" :min="1" :max="65535" style="width: 140px" />
        </n-form-item>
        <n-form-item :label="t('common.username')">
          <n-input v-model:value="form.username" placeholder="jump" />
        </n-form-item>
        <n-form-item :label="t('jump_hosts.auth')">
          <n-radio-group v-model:value="form.auth_kind">
            <n-radio value="key">{{ t("jump_hosts.auth_key") }}</n-radio>
            <n-radio value="password">{{ t("jump_hosts.auth_password") }}</n-radio>
          </n-radio-group>
        </n-form-item>
        <n-form-item v-if="form.auth_kind === 'key'"
                     :label="editing ? t('jump_hosts.key_keep') : t('jump_hosts.key')">
          <n-input v-model:value="form.private_key" type="textarea" :rows="4"
                   :placeholder="t('jump_hosts.key_ph')" />
        </n-form-item>
        <n-form-item v-else :label="editing ? t('jump_hosts.pw_keep') : t('common.password')">
          <n-input v-model:value="form.password" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item :label="t('jump_hosts.max_sessions')">
          <div style="width: 100%">
            <n-input-number v-model:value="form.max_sessions" :min="1" :max="200"
                            style="width: 140px" />
            <div class="jh-hint">{{ t("jump_hosts.max_sessions_hint") }}</div>
          </div>
        </n-form-item>
        <n-form-item :label="t('common.enable')">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>
        <n-form-item :label="t('common.description')">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-alert type="warning" :bordered="false" style="margin-bottom: 12px">
        {{ t("jump_hosts.pin_required") }}
      </n-alert>
      <!-- 新增時最需要知道跳板要符合什麼條件：從這裡也打得開說明 -->
      <n-button text type="primary" size="small" style="margin-bottom: 12px" @click="showHelp = true">
        <template #icon><n-icon><InfoIcon /></n-icon></template>
        {{ t("jump_hosts.help_link") }}
      </n-button>
      <n-space justify="end">
        <n-button @click="show = false">
          <template #icon><n-icon><CancelIcon /></n-icon></template>
          {{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" @click="submit">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </n-space>
    </n-modal>

    <!-- 測試連線 / 信任主機金鑰 -->
    <n-modal v-model:show="probeOpen" preset="card"
             :title="`${t('jump_hosts.test')} — ${probeFor?.name ?? ''}`"
             style="width: 600px; max-width: calc(100vw - 32px)">
      <template v-if="probe">
        <n-space vertical :size="10">
          <div><strong>{{ t("jump_hosts.endpoint") }}：</strong>{{ probe.host }}:{{ probe.port }}</div>
          <div>
            <strong>{{ t("jump_hosts.fingerprint") }}：</strong>
            <n-code :code="probe.fingerprint" word-wrap />
          </div>
          <n-alert v-if="probe.matches === true" type="success" :bordered="false">
            {{ t("jump_hosts.fp_match") }}
            <template v-if="probe.authenticated"> · {{ t("jump_hosts.auth_ok") }}</template>
          </n-alert>
          <n-alert v-else-if="probe.matches === false" type="error" :bordered="false">
            {{ t("jump_hosts.fp_mismatch") }}
          </n-alert>
          <n-alert v-else type="warning" :bordered="false">
            {{ probe.note || t("jump_hosts.fp_unpinned") }}
          </n-alert>
          <div v-if="probe.server_version" class="jh-hint">{{ probe.server_version }}</div>
        </n-space>
      </template>
      <n-space justify="end" style="margin-top: 12px">
        <n-button @click="probeOpen = false"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.close") }}</n-button>
        <n-button v-if="probe && probe.matches !== true" type="primary" @click="trustFingerprint"><template #icon><n-icon><CheckIcon /></n-icon></template>
          {{ t("jump_hosts.trust") }}
        </n-button>
      </n-space>
    </n-modal>
    <!-- 需求與設定說明 -->
    <n-modal v-model:show="showHelp" preset="card" :title="t('jump_hosts.help_title')"
             style="width: 800px; max-width: 92vw" data-testid="jump-help">
      <div class="jh-help">
        <n-alert type="info" :bordered="false" style="margin-bottom: 14px">
          {{ t("jump_hosts.help_when") }}
        </n-alert>
        <h4>{{ t("jump_hosts.help_req_title") }}</h4>
        <ol class="jh-steps">
          <li v-for="k in ['os', 'net', 'fwd', 'account', 'auth', 'hostkey']" :key="k">
            <span class="sn">{{ ['os', 'net', 'fwd', 'account', 'auth', 'hostkey'].indexOf(k) + 1 }}</span>
            <span><b>{{ t(`jump_hosts.req_${k}_t`) }}</b>：{{ t(`jump_hosts.req_${k}`) }}</span>
          </li>
        </ol>
        <h4>{{ t("jump_hosts.help_setup_title") }}</h4>
        <div class="jh-code">
          <pre>{{ SETUP_SNIPPET }}</pre>
          <n-button size="small" secondary @click="copySnippet">
            <template #icon><n-icon><CloneIcon /></n-icon></template>
          </n-button>
        </div>
        <div class="jh-muted">{{ t("jump_hosts.help_setup_note") }}</div>
        <h4>{{ t("jump_hosts.help_limits_title") }}</h4>
        <div class="jh-muted" style="margin-top:0">{{ t("jump_hosts.help_limits") }}</div>
      </div>
    </n-modal>
  </n-card>
</template>

<style scoped>
.jh-hint { font-size: 11px; opacity: .7; margin-top: 4px; }
/* 說明視窗：照掃描代理頁「安裝說明」的樣式 */
.jh-help h4 { margin: 16px 0 6px; font-size: 14px; }
.jh-steps { list-style: none; padding: 0; margin: 0; }
.jh-steps li { display: flex; align-items: flex-start; gap: 10px; margin: 8px 0; line-height: 1.6; font-size: 14px; }
.jh-steps .sn {
  flex: 0 0 auto; width: 22px; height: 22px; margin-top: 1px;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 50%; background: var(--primary-color, #18a058); color: #fff; font-size: 12px; font-weight: 600;
}
.jh-code { display: flex; align-items: flex-start; gap: 8px; }
.jh-code pre {
  flex: 1 1 auto; margin: 0; background: rgba(127,127,127,0.12); padding: 10px 12px; border-radius: 6px;
  overflow-x: auto; font-size: 12px; line-height: 1.5; white-space: pre;
}
.jh-muted { opacity: .7; font-size: 12px; margin-top: 8px; line-height: 1.6; }
</style>
