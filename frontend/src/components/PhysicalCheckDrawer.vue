<script setup lang="ts">
/**
 * 佈線與供電的「資料檢查」：既有的重複占用、不對稱配對、半條線、舊式插座直連（2026-11-21）。
 *
 * 只讀。新的寫入規則只擋之後的錯；升級前就存在的問題不自動刪除、合併或改接（交接包 physical-v1.2），
 * 列在這裡讓管理員到對應的裝置自己修。佈線頁與電力頁共用。
 */
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { NBadge, NButton, NDrawer, NDrawerContent, NEmpty, NIcon, NSpin, useMessage } from "naive-ui";
import { Physical, type PhysicalCheck } from "@/api/phase3";
import { apiErrMsg } from "@/api/client";
import { useEntityLinks } from "@/composables/useEntityLinks";
import { RefreshIcon, WarnIcon } from "@/icons";

const { t } = useI18n();
const msg = useMessage();
const links = useEntityLinks(useRouter());
const show = ref(false);
const loading = ref(false);
const data = ref<PhysicalCheck | null>(null);

async function load() {
  loading.value = true;
  try { data.value = await Physical.physicalCheck(); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { loading.value = false; }
}
async function open() { show.value = true; await load(); }

const sections = computed(() => {
  const d = data.value;
  if (!d) return [];
  return [
    { key: "ports_multi_cable", total: d.ports_multi_cable.total, rows: d.ports_multi_cable.items.map((x) => ({
      id: x.port_id, device: x.device_id, deviceName: x.device, at: true, text: `${x.port}：${x.cables.map((c) => c || t("physical_check.no_label")).join("、")}` })) },
    { key: "outlets_multi_psu", total: d.outlets_multi_psu.total, rows: d.outlets_multi_psu.items.map((x) => ({
      id: x.outlet_id, device: null, deviceName: "", at: false, text: `${x.outlet}：${x.ports.join("、")}` })) },
    { key: "asymmetric_peers", total: d.asymmetric_peers.total, rows: d.asymmetric_peers.items.map((x) => ({
      id: x.port_id, device: x.device_id, deviceName: x.device, at: true,
      text: t("physical_check.asym_row", { port: x.port, peer: x.peer, back: x.peer_points_to ?? t("physical_check.nothing") }) })) },
    { key: "incomplete_cables", total: d.incomplete_cables.total, rows: d.incomplete_cables.items.map((x) => ({
      id: x.cable_id, device: null, deviceName: "", at: false,
      text: `${x.cable || t("physical_check.no_label")}：${t(`physical_check.reason_${x.reason}`)}` })) },
    { key: "legacy_outlet_links", total: d.legacy_outlet_links.total, rows: d.legacy_outlet_links.items.map((x) => ({
      id: x.outlet_id, device: x.device_id, deviceName: x.device, at: false,
      text: `${x.outlet}：${t(`physical_check.reason_${x.reason}`)}` })) },
  ].filter((s) => s.total > 0);
});
</script>

<template>
  <n-button data-testid="physical-check-open" @click="open">
    <template #icon><n-icon><WarnIcon /></n-icon></template>{{ t("physical_check.title") }}
  </n-button>
  <n-drawer v-model:show="show" :width="560" placement="right" style="max-width: 100vw">
    <n-drawer-content closable>
      <template #header>
        <span style="display:inline-flex;align-items:center;gap:8px">
          <n-icon><WarnIcon /></n-icon>{{ t("physical_check.title") }}
          <n-badge v-if="data" :value="data.total" :max="9999" :type="data.total ? 'warning' : 'success'" show-zero />
        </span>
      </template>
      <div class="hint">{{ t("physical_check.hint") }}</div>
      <n-button size="small" :loading="loading" style="margin: 8px 0 12px" @click="load">
        <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("common.refresh") }}
      </n-button>
      <n-spin :show="loading">
        <n-empty v-if="data && !sections.length" :description="t('physical_check.clean')" data-testid="physical-check-clean" />
        <section v-for="s in sections" :key="s.key" class="pc-sec" :data-testid="`pc-${s.key}`">
          <h4>{{ t(`physical_check.${s.key}`) }}（{{ s.total }}）</h4>
          <div class="hint">{{ t(`physical_check.${s.key}_hint`) }}</div>
          <ul>
            <li v-for="r in s.rows" :key="r.id">
              <component :is="r.device ? links.device(r.device, r.deviceName) : 'span'" v-if="r.device" />
              <!-- 裝置與埠之間照全站寫法用綠色 @（SwitchPortLabel）；插座那幾類不是埠，維持 · -->
              <span v-if="r.device && r.at" class="pc-at">@</span><span v-else-if="r.device"> · </span>{{ r.text }}
            </li>
          </ul>
          <div v-if="data && s.total > s.rows.length" class="hint">{{ t("physical_check.truncated", { n: s.rows.length, total: s.total }) }}</div>
        </section>
      </n-spin>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.hint { font-size: 12px; opacity: .7; line-height: 1.5; }
.pc-sec { margin-bottom: 16px; }
.pc-sec h4 { margin: 0 0 4px; font-size: 14px; }
.pc-sec ul { margin: 6px 0 0; padding-left: 18px; }
.pc-sec li { margin: 2px 0; word-break: break-word; }
.pc-at { color: #18a058; font-weight: 700; margin: 0 1px; }
</style>
