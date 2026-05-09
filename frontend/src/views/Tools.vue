<script setup lang="ts">
import { ref } from "vue";
import {
  NCard,
  NTabs,
  NTabPane,
  NSpace,
  NInput,
  NInputNumber,
  NButton,
  NDescriptions,
  NDescriptionsItem,
  NCode,
  useMessage,
} from "naive-ui";
import { apiClient } from "@/api/client";

const msg = useMessage();

// ── IP Info ──
const ipInput = ref("8.8.8.8");
const ipResult = ref<Record<string, unknown> | null>(null);
async function runIpInfo() {
  try {
    const { data } = await apiClient.get("/api/v1/tools/ip-info", { params: { ip: ipInput.value } });
    ipResult.value = data;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "Error");
  }
}

// ── CIDR Info ──
const cidrInput = ref("192.168.0.0/24");
const cidrResult = ref<Record<string, unknown> | null>(null);
async function runCidrInfo() {
  try {
    const { data } = await apiClient.get("/api/v1/tools/cidr-info", { params: { cidr: cidrInput.value } });
    cidrResult.value = data;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "Error");
  }
}

// ── CIDR Split ──
const splitCidr = ref("192.168.0.0/24");
const splitNew = ref(28);
const splitResult = ref<{ subnets: string[]; count: number } | null>(null);
async function runSplit() {
  try {
    const { data } = await apiClient.get("/api/v1/tools/cidr-split", {
      params: { cidr: splitCidr.value, new_prefix: splitNew.value },
    });
    splitResult.value = data;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "Error");
  }
}

// ── EUI-64 ──
const macInput = ref("00:11:22:33:44:55");
const prefixInput = ref("2001:db8::/64");
const eui64Result = ref<Record<string, unknown> | null>(null);
async function runEui64() {
  try {
    const { data } = await apiClient.get("/api/v1/tools/eui64", {
      params: { mac: macInput.value, prefix: prefixInput.value },
    });
    eui64Result.value = data;
  } catch (e: any) {
    msg.error(e?.response?.data?.detail ?? "Error");
  }
}
</script>

<template>
  <n-card title="Tools">
    <n-tabs type="line" default-value="ip">
      <n-tab-pane name="ip" tab="IP info">
        <n-space vertical :size="12">
          <n-space>
            <n-input v-model:value="ipInput" placeholder="8.8.8.8" style="width: 280px" @keyup.enter="runIpInfo" />
            <n-button type="primary" @click="runIpInfo">Lookup</n-button>
          </n-space>
          <n-descriptions v-if="ipResult" bordered :column="2">
            <n-descriptions-item v-for="(v, k) in ipResult" :key="String(k)" :label="String(k)">
              <code>{{ v ?? "—" }}</code>
            </n-descriptions-item>
          </n-descriptions>
        </n-space>
      </n-tab-pane>

      <n-tab-pane name="cidr" tab="CIDR info">
        <n-space vertical :size="12">
          <n-space>
            <n-input v-model:value="cidrInput" placeholder="192.168.0.0/24" style="width: 280px" @keyup.enter="runCidrInfo" />
            <n-button type="primary" @click="runCidrInfo">Lookup</n-button>
          </n-space>
          <n-descriptions v-if="cidrResult" bordered :column="2">
            <n-descriptions-item v-for="(v, k) in cidrResult" :key="String(k)" :label="String(k)">
              <code>{{ v ?? "—" }}</code>
            </n-descriptions-item>
          </n-descriptions>
        </n-space>
      </n-tab-pane>

      <n-tab-pane name="split" tab="CIDR split">
        <n-space vertical :size="12">
          <n-space>
            <n-input v-model:value="splitCidr" placeholder="192.168.0.0/24" style="width: 220px" />
            <n-input-number v-model:value="splitNew" :min="0" :max="128" placeholder="new prefix" style="width: 140px" />
            <n-button type="primary" @click="runSplit">Split</n-button>
          </n-space>
          <n-card v-if="splitResult" :title="`${splitResult.count} subnets`">
            <n-code :code="splitResult.subnets.join('\n')" language="plain" />
          </n-card>
        </n-space>
      </n-tab-pane>

      <n-tab-pane name="eui64" tab="EUI-64">
        <n-space vertical :size="12">
          <n-space>
            <n-input v-model:value="macInput" placeholder="00:11:22:33:44:55" style="width: 220px" />
            <n-input v-model:value="prefixInput" placeholder="2001:db8::/64" style="width: 220px" />
            <n-button type="primary" @click="runEui64">Generate</n-button>
          </n-space>
          <n-descriptions v-if="eui64Result" bordered :column="1">
            <n-descriptions-item v-for="(v, k) in eui64Result" :key="String(k)" :label="String(k)">
              <code>{{ v ?? "—" }}</code>
            </n-descriptions-item>
          </n-descriptions>
        </n-space>
      </n-tab-pane>
    </n-tabs>
  </n-card>
</template>
