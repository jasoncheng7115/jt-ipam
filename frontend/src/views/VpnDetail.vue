<script setup lang="ts">
/**
 * 站對站 VPN 詳細資料（客戶許願清單：VPN 跟電路當成同一種管）：基本資料、聯絡人（例如對端公司的窗口）、
 * 跑在哪條電路上（VPN 端點落在電路關聯的 WAN 位址或位址段裡，自動推出；端點是名稱時不推）。
 * VPN 多半是從防火牆同步來的，欄位在同步時更新，這頁不另外編輯。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { storeToRefs } from "pinia";
import {
  NButton, NCard, NDescriptions, NDescriptionsItem, NEmpty, NIcon, NSpace, NSpin, NTag, useMessage,
} from "naive-ui";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { PhysicalIcon, UsersIcon, VpnIcon } from "@/icons";
import { apiErrMsg } from "@/api/client";
import { Advanced, type VpnDetail } from "@/api/phase3";
import { useAuthStore } from "@/stores/auth";
import { useEntityLinks } from "@/composables/useEntityLinks";
import ContactAssignments from "@/components/ContactAssignments.vue";

const route = useRoute();
const router = useRouter();
const { t, te } = useI18n();
const msg = useMessage();
const links = useEntityLinks(router);
const { me } = storeToRefs(useAuthStore());
const isAdmin = computed(() => !!me.value?.is_admin);

const data = ref<VpnDetail | null>(null);
const loading = ref(false);

async function load() {
  loading.value = true;
  try { data.value = await Advanced.vpnDetail(String(route.params.id)); }
  catch (e) { msg.error(apiErrMsg(e)); data.value = null; }
  finally { loading.value = false; }
}
function back() {
  if (window.history.length > 1) router.back();
  else router.push({ name: "vpn-tunnels" });
}
function sideText(side: "a" | "b"): string {
  return side === "a" ? t("vpn_detail.side_a") : t("vpn_detail.side_b");
}
function statusText(s: string): string {
  const k = `circuits.status_${s}`;
  return te(k) ? t(k) : s;
}

watch(() => route.params.id, (id) => { if (id) void load(); });
onMounted(load);
</script>

<template>
  <n-spin :show="loading">
    <n-space vertical :size="16">
      <n-card v-if="data">
        <template #header>
          <n-space align="center" :wrap-item="false">
            <n-icon :size="22"><VpnIcon /></n-icon>
            <span data-testid="vpn-name">{{ data.tunnel.name }}</span>
            <n-tag size="small" :type="data.tunnel.status === 'active' ? 'success' : 'default'">
              {{ statusText(data.tunnel.status) }}
            </n-tag>
          </n-space>
        </template>
        <n-space align="center" justify="end" style="margin-bottom: 10px">
          <n-button size="small" @click="back">
            <template #icon><n-icon><ArrowLeftIcon /></n-icon></template>
            {{ t("common.back") }}
          </n-button>
        </n-space>
        <n-descriptions label-placement="left" :column="2" size="small" bordered>
          <n-descriptions-item :label="t('cols.type')">{{ data.tunnel.type }}</n-descriptions-item>
          <n-descriptions-item :label="t('vpn_detail.pairing')">{{ data.tunnel.pairing_method || "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('vpn_detail.side_a')">
            <component :is="() => (data!.a_device ? links.device(data!.a_device.id, data!.a_device.name) : '—')" />
            <span class="mono dim"> {{ data.tunnel.a_endpoint || "" }}</span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('vpn_detail.side_b')">
            <component :is="() => (data!.b_device ? links.device(data!.b_device.id, data!.b_device.name) : '—')" />
            <span class="mono dim"> {{ data.tunnel.b_endpoint || "" }}</span>
          </n-descriptions-item>
          <n-descriptions-item :label="t('vpn_detail.encryption')">{{ data.tunnel.encryption_algo || "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('vpn_detail.auth')">{{ data.tunnel.auth_algo || "—" }}</n-descriptions-item>
          <n-descriptions-item :label="t('sections.description')" :span="2">{{ data.tunnel.description || "—" }}</n-descriptions-item>
        </n-descriptions>
      </n-card>

      <n-card v-if="data" data-testid="vpn-contacts">
        <template #header>
          <span style="display:inline-flex;align-items:center;gap:8px">
            <n-icon :size="18"><UsersIcon /></n-icon>{{ t("circuits.contacts") }}
          </span>
        </template>
        <ContactAssignments object-type="vpn_tunnel" :object-id="data.tunnel.id" :contacts="data.contacts"
                            :title="t('vpn_detail.contacts_hint')" :admin="isAdmin" @changed="load" />
      </n-card>

      <n-card v-if="data" data-testid="vpn-carriers">
        <template #header>
          <span style="display:inline-flex;align-items:center;gap:8px">
            <n-icon :size="18"><PhysicalIcon /></n-icon>{{ t("vpn_detail.carriers") }}
          </span>
        </template>
        <div class="hint" style="margin-bottom: 8px">{{ t("vpn_detail.carriers_hint") }}</div>
        <n-empty v-if="!data.carriers.length" :description="t('vpn_detail.carriers_empty')" />
        <table v-else class="plain">
          <thead><tr>
            <th>{{ t("vpn_detail.side") }}</th><th>{{ t("vpn_detail.endpoint") }}</th>
            <th>{{ t("vpn_detail.circuit") }}</th><th>{{ t("circuits.provider") }}</th>
          </tr></thead>
          <tbody>
            <tr v-for="c in data.carriers" :key="c.side + c.circuit.id">
              <td>{{ sideText(c.side) }}</td>
              <td class="mono">{{ c.endpoint }}</td>
              <td><component :is="() => links.circuit(c.circuit.id, c.circuit.cid)" /></td>
              <td>{{ c.provider || "—" }}</td>
            </tr>
          </tbody>
        </table>
      </n-card>

      <n-space v-if="!data && !loading" justify="center" style="padding: 40px; opacity: .6">
        {{ t("common.no_data") }}
      </n-space>
    </n-space>
  </n-spin>
</template>

<style scoped>
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.dim { opacity: .65; font-size: 13px; }
.hint { font-size: 12.5px; opacity: .7; }
.plain { width: 100%; border-collapse: collapse; font-size: 13.5px; }
.plain th, .plain td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--n-border-color, rgba(128,128,128,.2)); }
.plain th { font-weight: 600; opacity: .8; }
</style>
