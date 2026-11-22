<script setup lang="ts">
/**
 * 一條追線結果的顯示（纜線清單的追線側欄、IP 詳情的實體線路卡片共用）。
 * 段別分開寫：實體線（算纜線數）、面板內部對應、邏輯對應（不算線）；停下來時說明原因。
 */
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { NAlert, NTag } from "naive-ui";
import type { PortTrace, TraceNode } from "@/api/phase3";
import SwitchPortLabel from "@/components/SwitchPortLabel.vue";

const props = defineProps<{ trace: PortTrace }>();
const { t } = useI18n();

// 走到第 i 個節點的那一段是 hops[i - 1]
const steps = computed(() => props.trace.nodes.map((n, i) => ({ node: n, hop: i > 0 ? props.trace.hops[i - 1] ?? null : null })));
const passthroughs = computed(() => props.trace.hops.filter((h) => h.kind === "panel_passthrough").length);
const nodeText = (n: TraceNode) => (n.device_name ? `${n.device_name}@${n.port_name}` : (n.port_name || n.object_type || "?"));
const stopText = computed(() => {
  const d = props.trace;
  if (!d.stop_reason || d.stop_reason === "end") return "";
  return t(`cable_trace.stop_${d.stop_reason}`, { port: d.stop_port ? nodeText(d.stop_port) : "",
                                                 list: (d.candidates ?? []).map((x) => x || t("physical_check.no_label")).join("、") });
});
</script>

<template>
  <div>
    <div v-for="(s, i) in steps" :key="i">
      <div v-if="i > 0 && s.hop" class="ct-seg" :data-testid="`trace-seg-${s.hop.kind}`">
        <template v-if="s.hop.kind === 'cable'">{{ s.hop.cable_label || t("physical_check.no_label") }} · {{ s.hop.cable_type || t("cable_trace.cable") }}</template>
        <template v-else-if="s.hop.kind === 'panel_passthrough'">{{ t("cable_trace.passthrough") }}</template>
        <template v-else>{{ t("cable_trace.bridge") }}</template>
      </div>
      <div class="ct-node" data-testid="trace-node">
        <SwitchPortLabel v-if="s.node.device_id" :value="`${s.node.device_name} / ${s.node.port_name}`"
                         :to="{ name: 'device-detail', params: { id: s.node.device_id } }" />
        <span v-else>{{ nodeText(s.node) }}</span>
        <n-tag v-if="s.node.port_type === 'front' || s.node.port_type === 'rear'" size="tiny" :bordered="false" style="margin-left: 6px">
          {{ t(`ports.type_${s.node.port_type}`) }}
        </n-tag>
      </div>
    </div>
    <div v-if="trace.hops.length && !trace.hops[trace.hops.length - 1].to" class="ct-seg">{{ t("cable_trace.unknown_end") }}</div>
    <div class="ct-sum" data-testid="trace-summary">{{ t("cable_trace.summary", { n: trace.cable_count ?? 0, m: passthroughs }) }}</div>
    <n-alert v-if="stopText" type="warning" :bordered="false" style="margin-top: 8px" data-testid="trace-stop">{{ stopText }}</n-alert>
  </div>
</template>

<style scoped>
.ct-node { padding: 7px 10px; border-radius: 6px; background: rgba(24, 160, 88, .12); font-weight: 600; word-break: break-all; }
.ct-seg { margin: 4px 0 4px 12px; padding-left: 10px; border-left: 2px dashed rgba(127, 127, 127, .5); font-size: 12.5px; opacity: .8; }
.ct-sum { margin-top: 10px; font-weight: 600; }
</style>
