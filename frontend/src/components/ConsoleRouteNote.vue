<script setup lang="ts">
/**
 * 主控台連線表單上的「連線路徑」：按下連線之前就看得到會走直連、哪台跳板或哪台掃描代理，
 * 以及這個設定在 IP 還是子網路上（使用者 2026-10-04：連線時要看得出走哪條路；不讓每次連線自己選，
 * 因為在重疊網段選「直連」會連錯主機）。走不通時直接講原因，不必按下去才失敗。
 * 有編輯權限的人旁邊多一個「變更」，就地開視窗改這個 IP 的出口（與 IP 編輯視窗同一個 ConsoleEgressSelect），
 * 要整個子網路一起改就開子網路的編輯視窗。以前是跳到子網路頁，但出口設定在子網路的編輯視窗裡，頁面上根本看不到
 * （使用者 2026-10-04：「按了怎麼是去子網路頁」）。
 */
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { NButton, NForm, NIcon, NModal, NSpace, useMessage } from "naive-ui";
import { apiClient } from "@/api/client";
import { getAddress, updateAddress } from "@/api/addresses";
import { getSubnet } from "@/api/subnets";
import ConsoleEgressSelect from "@/components/ConsoleEgressSelect.vue";
import SubnetEditModal from "@/components/SubnetEditModal.vue";
import { useAuthStore } from "@/stores/auth";
import type { Subnet } from "@/types";
import { wsErrorText } from "@/utils/wsError";
import { LinkIcon, WarnIcon, CancelIcon, SaveIcon } from "@/icons";

const props = defineProps<{ addressId: string }>();
const { t } = useI18n();
const router = useRouter();
const auth = useAuthStore();
const msg = useMessage();

interface RouteInfo {
  kind: "direct" | "jump" | "agent"; name: string | null; source: "ip" | "subnet" | null; ok: boolean;
  code?: string; params?: Record<string, unknown>; message?: string; subnet_id?: string | null;
}
const info = ref<RouteInfo | null>(null);

async function load() {
  try {
    info.value = (await apiClient.get<RouteInfo>(`/api/v1/addresses/${props.addressId}/console-route`)).data;
  } catch { info.value = null; }   // 讀不到就不顯示，連線照常（實際連線時會再判斷一次）
}
onMounted(load);

const pathText = computed(() => {
  const r = info.value;
  if (!r) return "";
  if (r.kind === "jump") return t("console_route.via_jump", { name: r.name ?? "" });
  if (r.kind === "agent") return t("console_route.via_agent", { name: r.name ?? "" });
  return t("console_route.direct");
});
const sourceText = computed(() => {
  const s = info.value?.source;
  return s === "ip" ? t("console_route.from_ip") : s === "subnet" ? t("console_route.from_subnet") : "";
});
const canEdit = computed(() => auth.me?.can_edit !== false);
const isAdmin = computed(() => !!auth.me?.is_admin);

// 就地改這個 IP 的出口（留空＝沿用子網路的設定）
const showEdit = ref(false);
const saving = ref(false);
const egress = ref<{ visible: boolean } | null>(null);
const nothingToPick = computed(() => !!egress.value && !egress.value.visible);
const subnetId = ref<string | null>(null);
const form = ref<{ jump_host_id: string | null; console_agent_id: string | null }>(
  { jump_host_id: null, console_agent_id: null });
async function change() {
  try {
    const a = await getAddress(props.addressId);
    form.value = { jump_host_id: a.jump_host_id ?? null, console_agent_id: a.console_agent_id ?? null };
    subnetId.value = a.subnet_id ?? null;
    showEdit.value = true;
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("common.fail")); }
}
async function save() {
  saving.value = true;
  try {
    await updateAddress(props.addressId, { ...form.value });
    showEdit.value = false;
    msg.success(t("common.saved"));
    await load();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("common.save_failed")); }
  finally { saving.value = false; }
}

// 整個子網路一起改：開子網路的編輯視窗（與子網路頁同一個元件）
const showSubnet = ref(false);
const subnetEditing = ref<Subnet | null>(null);
async function editSubnet() {
  if (!subnetId.value) return;
  try {
    subnetEditing.value = await getSubnet(subnetId.value);
    showEdit.value = false;
    showSubnet.value = true;
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("common.fail")); }
}
function gotoSetup() {
  showEdit.value = false;
  void router.push({ name: "scan_agents", query: { tab: "jump" } });
}
</script>

<template>
  <div v-if="info" class="crn" :class="{ 'crn-bad': !info.ok }" data-testid="console-route-note">
    <div class="crn-line">
      <n-icon :component="info.ok ? LinkIcon : WarnIcon" :size="14" class="crn-icon" />
      <span>{{ t("console_route.label") }}：<b>{{ pathText }}</b></span>
      <span v-if="sourceText" class="crn-src">（{{ sourceText }}）</span>
      <a v-if="canEdit" class="crn-link" data-testid="console-route-change" @click="change">
        {{ t("console_route.change") }}
      </a>
    </div>
    <div v-if="!info.ok" class="crn-err" data-testid="console-route-error">
      {{ wsErrorText(info, info.message ?? "") }}
    </div>

    <n-modal v-model:show="showEdit" preset="card" :title="t('console_route.edit_title')"
             style="width: 520px; max-width: calc(100vw - 32px)" data-testid="console-route-edit">
      <div class="crn-now">
        {{ t("console_route.edit_now") }}：<b>{{ pathText }}</b>
        <span v-if="sourceText" class="crn-src">（{{ sourceText }}）</span>
      </div>
      <n-form label-placement="top">
        <ConsoleEgressSelect ref="egress" v-model:jump-host-id="form.jump_host_id"
                             v-model:console-agent-id="form.console_agent_id"
                             :subnet-id="subnetId" inherit />
      </n-form>
      <div v-if="nothingToPick" class="crn-hint" data-testid="console-route-nothing">
        {{ t("console_route.edit_nothing") }}
      </div>
      <div v-else class="crn-hint">{{ t("console_route.edit_hint") }}</div>
      <div class="crn-hint">
        <a v-if="subnetId" class="crn-link" data-testid="console-route-edit-subnet" @click="editSubnet">
          {{ t("console_route.edit_subnet") }}
        </a>
        <template v-if="isAdmin">
          <span v-if="subnetId" class="crn-sep">·</span>
          <a class="crn-link" @click="gotoSetup">{{ t("console_route.setup_link") }}</a>
        </template>
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showEdit = false"><template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}</n-button>
          <n-button v-if="!nothingToPick" type="primary" :loading="saving" data-testid="console-route-save"
                    @click="save"><template #icon><n-icon><SaveIcon /></n-icon></template>
            {{ t("common.save") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
    <SubnetEditModal v-if="subnetEditing" v-model:show="showSubnet" :editing="subnetEditing" @saved="load" />
  </div>
</template>

<style scoped>
.crn { font-size: 12.5px; margin-bottom: 10px; line-height: 1.6; }
.crn-line { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.crn-icon { flex: none; opacity: .75; }
.crn-src { opacity: .65; }
.crn-link { color: var(--primary-color, #18a058); cursor: pointer; margin-left: 4px; }
.crn-link:hover { text-decoration: underline; }
.crn-bad { padding: 6px 10px; border-radius: 6px; background: rgba(240, 160, 32, .12); }
.crn-err { margin-top: 2px; opacity: .85; }
.crn-now { margin-bottom: 12px; }
.crn-hint { font-size: 12px; opacity: .75; margin-top: 4px; line-height: 1.6; }
.crn-hint .crn-link { margin-left: 0; }
.crn-sep { margin: 0 6px; opacity: .5; }
</style>
