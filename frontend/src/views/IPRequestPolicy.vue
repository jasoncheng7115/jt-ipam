<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NSpace, NRadioGroup, NRadio, NSelect, NSwitch, NButton, NIcon,
  NFormItem, NAlert, NInput, NDivider, useMessage,
} from "naive-ui";
import { RequestsIcon, SaveIcon, PlusIcon, DeleteIcon } from "@/icons";
import { getRequestPolicy, setRequestPolicy, type IPRequestPolicy, type IPRequestStep } from "@/api/ip_requests";
import { listUsers, listGroups } from "@/api/admin";

const { t } = useI18n();
const msg = useMessage();

const loading = ref(false);
const saving = ref(false);
const policy = ref<IPRequestPolicy>({
  approver_mode: "admin",
  designated_user_ids: [],
  designated_group_ids: [],
  allow_self_approve: false,
  stages: [],
});
const userOpts = ref<{ label: string; value: string }[]>([]);
const groupOpts = ref<{ label: string; value: string }[]>([]);

const isMultiStep = computed(() =>
  policy.value.approver_mode === "parallel" || policy.value.approver_mode === "stages");

async function load() {
  loading.value = true;
  try {
    const [pol, users, groups] = await Promise.all([
      getRequestPolicy(), listUsers("", "", 500, 0), listGroups(500, 0),
    ]);
    policy.value = { ...pol, stages: pol.stages ?? [] };
    userOpts.value = users.items.map((u) => ({
      label: `${u.display_name || u.username} (${u.username})`, value: u.id,
    }));
    groupOpts.value = groups.items.map((g) => ({ label: g.name, value: g.id }));
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

function addStage() {
  policy.value.stages.push({ name: "", user_ids: [], group_ids: [] } as IPRequestStep);
}
function removeStage(i: number) { policy.value.stages.splice(i, 1); }
function moveStage(i: number, d: number) {
  const j = i + d;
  if (j < 0 || j >= policy.value.stages.length) return;
  const s = policy.value.stages;
  [s[i], s[j]] = [s[j], s[i]];
}

async function save() {
  // 多關卡模式：至少要有一個關卡、且每關卡都要有審核人
  if (isMultiStep.value) {
    if (!policy.value.stages.length) { msg.warning(t("req_policy.need_stage")); return; }
    for (const s of policy.value.stages) {
      if (!s.user_ids.length && !s.group_ids.length) { msg.warning(t("req_policy.stage_need_approver")); return; }
    }
  }
  saving.value = true;
  try {
    policy.value = { ...(await setRequestPolicy(policy.value)) };
    if (!policy.value.stages) policy.value.stages = [];
    msg.success(t("common.saved"));
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? t("errors.network"));
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><RequestsIcon /></n-icon>
        <span>{{ t("req_policy.title") }}</span>
      </n-space>
    </template>

    <n-space vertical :size="18" style="max-width: 760px">
      <n-alert type="info" :show-icon="true">{{ t("req_policy.intro") }}</n-alert>

      <n-form-item :label="t('req_policy.mode_label')" label-placement="top">
        <n-radio-group v-model:value="policy.approver_mode">
          <n-space vertical>
            <n-radio value="admin">{{ t("req_policy.mode_admin") }}</n-radio>
            <n-radio value="designated">{{ t("req_policy.mode_designated") }}</n-radio>
            <n-radio value="parallel">{{ t("req_policy.mode_parallel") }}</n-radio>
            <n-radio value="stages">{{ t("req_policy.mode_stages") }}</n-radio>
          </n-space>
        </n-radio-group>
      </n-form-item>

      <!-- designated：單一審核人集合 -->
      <template v-if="policy.approver_mode === 'designated'">
        <n-form-item :label="t('req_policy.designated_users')" label-placement="top">
          <n-select v-model:value="policy.designated_user_ids" multiple filterable
                    :options="userOpts" :placeholder="t('req_policy.pick_users')" />
        </n-form-item>
        <n-form-item :label="t('req_policy.designated_groups')" label-placement="top">
          <n-select v-model:value="policy.designated_group_ids" multiple filterable
                    :options="groupOpts" :placeholder="t('req_policy.pick_groups')" />
        </n-form-item>
      </template>

      <!-- parallel / stages：多關卡 -->
      <template v-if="isMultiStep">
        <n-alert type="default" :bordered="true" :show-icon="false" style="font-size: 12.5px">
          {{ policy.approver_mode === 'stages' ? t("req_policy.stages_note") : t("req_policy.parallel_note") }}
        </n-alert>
        <div v-for="(s, i) in policy.stages" :key="i" class="stage-box">
          <div class="stage-head">
            <span class="stage-no">{{ i + 1 }}</span>
            <n-input v-model:value="s.name" size="small" style="flex: 1"
                     :placeholder="t('req_policy.stage_name_ph', { n: i + 1 })" />
            <n-button v-if="policy.approver_mode==='stages'" size="tiny" quaternary :disabled="i===0" @click="moveStage(i,-1)">↑</n-button>
            <n-button v-if="policy.approver_mode==='stages'" size="tiny" quaternary :disabled="i===policy.stages.length-1" @click="moveStage(i,1)">↓</n-button>
            <n-button size="tiny" quaternary type="error" @click="removeStage(i)">
              <template #icon><n-icon><DeleteIcon /></n-icon></template>
            </n-button>
          </div>
          <n-space vertical :size="8" style="margin-top: 8px">
            <n-select v-model:value="s.user_ids" multiple filterable size="small"
                      :options="userOpts" :placeholder="t('req_policy.pick_users')" />
            <n-select v-model:value="s.group_ids" multiple filterable size="small"
                      :options="groupOpts" :placeholder="t('req_policy.pick_groups')" />
          </n-space>
        </div>
        <n-button dashed @click="addStage">
          <template #icon><n-icon><PlusIcon /></n-icon></template>
          {{ t("req_policy.add_stage") }}
        </n-button>
      </template>

      <n-divider style="margin: 4px 0" />

      <n-form-item :label="t('req_policy.self_approve')" label-placement="left">
        <n-switch v-model:value="policy.allow_self_approve" />
        <span style="margin-left: 10px; font-size: 12.5px; opacity: .7">{{ t("req_policy.self_approve_hint") }}</span>
      </n-form-item>

      <div>
        <n-button type="success" :loading="saving" @click="save">
          <template #icon><n-icon><SaveIcon /></n-icon></template>
          {{ t("common.save") }}
        </n-button>
      </div>
    </n-space>
  </n-card>
</template>

<style scoped>
.stage-box { border: 1px solid var(--n-border-color, #e0e0e6); border-radius: 8px; padding: 12px 14px; }
.stage-head { display: flex; align-items: center; gap: 8px; }
.stage-no {
  display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; border-radius: 50%;
  background: var(--n-color-target, #36ad6a); color: #fff; font-size: 12px; flex: none;
}
</style>
