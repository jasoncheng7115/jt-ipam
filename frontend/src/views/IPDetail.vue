<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { NCard, NDescriptions, NDescriptionsItem, NSpin, NSpace, NTag, useMessage } from "naive-ui";
import { getAddress } from "@/api/addresses";
import IPAddressEditModal from "@/components/IPAddressEditModal.vue";
import { useScanProbes, probeLabel } from "@/api/scanProbes";
import type { IPAddress } from "@/types";

const route = useRoute();
const router = useRouter();
const { t, locale } = useI18n();
const msg = useMessage();
const { catalog } = useScanProbes();

const addr = ref<IPAddress | null>(null);
const loading = ref(false);

function sshHref(): string {
  return router.resolve({ name: "ssh-console", params: { id: addr.value!.id } }).href;
}
// 主按鈕 → 新分頁；下拉 → 新視窗（彈出）
function openSsh() {
  if (!addr.value) return;
  window.open(sshHref(), "_blank");
}
function openSshPopout() {
  if (!addr.value) return;
  window.open(sshHref(), `ssh-${addr.value.id}`, "width=960,height=640");
}

function rdpHref(): string {
  return router.resolve({ name: "rdp-console", params: { id: addr.value!.id } }).href;
}
function openRdp() {
  if (!addr.value) return;
  window.open(rdpHref(), "_blank");
}
function openRdpPopout() {
  if (!addr.value) return;
  window.open(rdpHref(), `rdp-${addr.value.id}`, "width=1320,height=900");
}

function vncHref(): string {
  return router.resolve({ name: "vnc-console", params: { id: addr.value!.id } }).href;
}
function openVnc() {
  if (!addr.value) return;
  window.open(vncHref(), "_blank");
}
function openVncPopout() {
  if (!addr.value) return;
  window.open(vncHref(), `vnc-${addr.value.id}`, "width=1320,height=900");
}
function novncHref(): string {
  return router.resolve({ name: "novnc-console", params: { id: addr.value!.id } }).href;
}
function openNovnc() {
  if (!addr.value) return;
  window.open(novncHref(), "_blank");
}
function openNovncPopout() {
  if (!addr.value) return;
  window.open(novncHref(), `novnc-${addr.value.id}`, "width=1320,height=900");
}
function bmcHref(): string {
  return router.resolve({ name: "bmc-console", params: { id: addr.value!.id } }).href;
}
function openBmc() {
  if (!addr.value) return;
  window.open(bmcHref(), "_blank");
}
function openBmcPopout() {
  if (!addr.value) return;
  window.open(bmcHref(), `bmc-${addr.value.id}`, "width=1040,height=680");
}

// 把探測 key 轉成顯示 label（比不到目錄就直接顯示 key）
function labelForProbe(key: string): string {
  const p = catalog.value.probes.find((x) => x.key === key);
  return p ? probeLabel(p, locale.value) : key;
}

const showScanSection = computed(() => {
  const a = addr.value;
  return !!a && ((a.effective_probes?.length ?? 0) > 0 || (a.excluded_probes?.length ?? 0) > 0);
});

async function load(id: string) {
  loading.value = true;
  try { addr.value = await getAddress(id); }
  catch { msg.error(t("errors.network")); addr.value = null; }
  finally { loading.value = false; }
}
function onSaved(a: IPAddress) { addr.value = a; }
function onDeleted() { router.push({ name: "addresses" }); }
function back() {
  if (window.history.length > 1) router.back();
  else router.push({ name: "addresses" });
}

onMounted(() => load(String(route.params.id)));
watch(() => route.params.id, (id) => { if (id) load(String(id)); });
</script>

<template>
  <n-spin :show="loading">
    <n-space vertical :size="12">
      <IPAddressEditModal
        v-if="addr"
        inline
        :show="true"
        :address="addr"
        @saved="onSaved"
        @deleted="onDeleted"
        @back="back"
        @ssh-open="openSsh"
        @ssh-popout="openSshPopout"
        @rdp-open="openRdp"
        @rdp-popout="openRdpPopout"
        @vnc-open="openVnc"
        @vnc-popout="openVncPopout"
        @novnc-open="openNovnc"
        @novnc-popout="openNovncPopout"
        @bmc-open="openBmc"
        @bmc-popout="openBmcPopout"
      />

      <!-- 掃描項目（唯讀，由探測結果推導）；OS 已併入上方主要欄位表 -->
      <n-card v-if="addr && showScanSection" size="small" :bordered="true">
        <template v-if="showScanSection">
          <div style="font-weight: 600; margin: 0 0 4px">{{ t("scan_probes.title") }}</div>
          <n-descriptions label-placement="left" :column="1" size="small">
            <n-descriptions-item v-if="addr.effective_probes?.length" :label="t('scan_probes.effective')">
              <n-space :size="4" :wrap="true">
                <n-tag v-for="k in addr.effective_probes" :key="k" size="small" :bordered="false">
                  {{ labelForProbe(k) }}
                </n-tag>
              </n-space>
              <div style="font-size: 12px; opacity: .6; margin-top: 4px">{{ t("scan_probes.effective_hint") }}</div>
            </n-descriptions-item>
            <n-descriptions-item v-if="addr.excluded_probes?.length" :label="t('scan_probes.excluded')">
              <n-space :size="4" :wrap="true">
                <n-tag v-for="k in addr.excluded_probes" :key="k" size="small" type="warning" :bordered="false">
                  {{ labelForProbe(k) }}
                </n-tag>
              </n-space>
            </n-descriptions-item>
          </n-descriptions>
        </template>
      </n-card>

      <n-space v-else-if="!loading" justify="center" style="padding: 40px; opacity: .6">
        {{ t("common.no_data") }}
      </n-space>
    </n-space>
  </n-spin>
</template>
