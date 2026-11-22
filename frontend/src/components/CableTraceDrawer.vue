<script setup lang="ts">
/**
 * 從纜線清單追線（交接包 physical-v1.2 設計圖 05）：左邊是選的那條線，右邊是一路走下去的路徑。
 *
 * - 從 A 端或 B 端出發都可以；第一段就是選的這條線（first_cable_id），不會默默改走另一條
 * - 段別分開寫：實體線（算纜線數）、面板內部對應、邏輯對應（不算線）
 * - 停下來時說明原因（例如「F1 未登錄背面對應」），已知的段落照樣列出
 */
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { NAlert, NButton, NDrawer, NDrawerContent, NIcon, NRadioButton, NRadioGroup, NSpace, NSpin } from "naive-ui";
import { Physical, type PortTrace } from "@/api/phase3";
import { apiErrMsg } from "@/api/client";
import TracePath from "@/components/TracePath.vue";
import { LinkIcon } from "@/icons";

const props = defineProps<{ cable: any | null }>();
const show = defineModel<boolean>("show", { default: false });
const { t } = useI18n();
const side = ref<"A" | "B">("A");
const data = ref<PortTrace | null>(null);
const loading = ref(false);
const err = ref("");

async function load() {
  const c = props.cable;
  data.value = null; err.value = "";
  if (!c) return;
  const port = side.value === "A" ? c.a_port_id : c.b_port_id;
  if (!port) { err.value = t("cable_trace.side_not_port"); return; }
  loading.value = true;
  try { data.value = await Physical.tracePort(port, c.id); }
  catch (e) { err.value = apiErrMsg(e); }
  finally { loading.value = false; }
}
watch(() => [props.cable?.id, show.value], () => {
  if (show.value) { side.value = props.cable?.a_port_id ? "A" : "B"; void load(); }
});
watch(side, () => { if (show.value) void load(); });

</script>

<template>
  <n-drawer v-model:show="show" :width="680" placement="right" style="max-width: 100vw">
    <n-drawer-content closable :title="t('cable_trace.title')">
      <div v-if="cable" class="ct-grid" data-testid="cable-trace">
        <div class="ct-card">
          <div class="hint">{{ t("cable_trace.selected") }}</div>
          <div class="ct-label">{{ cable.label || t("physical_check.no_label") }}</div>
          <div class="hint">{{ cable.type || "—" }} / {{ t(`physical.cable_status_${cable.status}`) }}</div>
          <div class="ct-end"><b>{{ t("cable_trace.a") }}</b> {{ cable.a_end || "—" }}</div>
          <div class="ct-end"><b>{{ t("cable_trace.b") }}</b> {{ cable.b_end || "—" }}</div>
          <n-radio-group v-model:value="side" size="small" style="margin-top: 10px">
            <n-radio-button value="A" :disabled="!cable.a_port_id">{{ t("cable_trace.from_a") }}</n-radio-button>
            <n-radio-button value="B" :disabled="!cable.b_port_id">{{ t("cable_trace.from_b") }}</n-radio-button>
          </n-radio-group>
        </div>
        <div class="ct-card">
          <div class="hint">{{ t("cable_trace.path") }}</div>
          <n-spin :show="loading">
            <n-alert v-if="err" type="warning" :bordered="false">{{ err }}</n-alert>
            <TracePath v-else-if="data" :trace="data" />
          </n-spin>
        </div>
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button size="small" @click="load">
            <template #icon><n-icon><LinkIcon /></n-icon></template>{{ t("cable_trace.retrace") }}
          </n-button>
        </n-space>
      </template>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.hint { font-size: 12px; opacity: .7; }
.ct-grid { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 3fr); gap: 14px; }
@media (max-width: 700px) { .ct-grid { grid-template-columns: 1fr; } }
.ct-card { padding: 12px; border-radius: 6px; background: rgba(127, 127, 127, .06); }
.ct-label { font-size: 22px; font-weight: 700; margin: 4px 0; word-break: break-all; }
.ct-end { margin-top: 6px; word-break: break-all; }
</style>
