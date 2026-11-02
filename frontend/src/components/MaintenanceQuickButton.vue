<script setup lang="ts">
/**
 * 物件頁的「維護」按鈕（IP、裝置、子網路…）：最常用的「臨時停一台」按一下就好。
 * 沒在維護：下拉選 30 分到 8 小時，或「自訂…」開完整對話框。
 * 正在維護：按鈕變成「維護中，到 14:30」，下拉可以延長、提前結束、查看。
 * 涵蓋這個物件的維護可能是別的範圍（例如整個機櫃），這時只能查看，調整要去那個維護。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { h, type Component } from "vue";
import { NButton, NDropdown, NIcon, useMessage } from "naive-ui";
import { EditIcon, ElapsedIcon, EndNowIcon, ExtendIcon, EyeIcon, MaintenanceIcon } from "@/icons";
import { apiErrMsg } from "@/api/client";
import { MAINT_CHANGED, Maintenance, type ActiveFor, type ScopeType } from "@/api/maintenance";
import MaintenanceEditModal from "@/components/MaintenanceEditModal.vue";
import { durationLabel, remaining } from "@/utils/maintenance";

const props = defineProps<{ objectType: ScopeType; objectId: string; label?: string | null; integrationKind?: string }>();
const { t } = useI18n();
const msg = useMessage();
const router = useRouter();
const active = ref<ActiveFor[]>([]);
const busy = ref(false);
const editShow = ref(false);

async function load() {
  try { active.value = await Maintenance.activeFor(props.objectType, props.objectId, props.integrationKind); }
  catch { active.value = []; }
}
const cur = computed(() => active.value[0] ?? null);
function hhmm(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
const ico = (c: Component) => () => h(NIcon, null, () => h(c));
const startOpts = computed(() => [
  ...[30, 60, 120, 240, 480].map((m) => ({ key: String(m), label: durationLabel(m, t), icon: ico(ElapsedIcon) })),
  { type: "divider", key: "d" }, { key: "custom", label: t("maint.custom_full"), icon: ico(EditIcon) }]);
const viewOpt = { key: "view", label: t("maint.view"), icon: ico(EyeIcon) };
const activeOpts = computed(() => cur.value?.can_manage && cur.value.source === "manual"
  ? [{ key: "x60", label: t("maint.extend_n", { n: 1 }), icon: ico(ExtendIcon) },
     { key: "x120", label: t("maint.extend_n", { n: 2 }), icon: ico(ExtendIcon) },
     { key: "end", label: t("maint.end_now"), icon: ico(EndNowIcon) }, { type: "divider", key: "d" }, viewOpt]
  : [viewOpt]);

async function onStart(k: string) {
  if (k === "custom") { editShow.value = true; return; }
  busy.value = true;
  try {
    await Maintenance.quick({ scope_type: props.objectType, object_id: props.objectId,
                              integration_kind: props.integrationKind ?? null }, Number(k));
    msg.success(t("maint.started_msg"));
    await load();
  } catch (e) { msg.error(apiErrMsg(e)); } finally { busy.value = false; }
}
async function onActive(k: string) {
  const w = cur.value;
  if (!w) return;
  if (k === "view") { void router.push({ name: "maintenance-detail", params: { id: w.id } }); return; }
  busy.value = true;
  try {
    if (k === "end") await Maintenance.end(w.id);
    else await Maintenance.extend(w.id, Number(k.slice(1)));
    await load();
  } catch (e) { msg.error(apiErrMsg(e)); } finally { busy.value = false; }
}
const preset = computed(() => [{ scope_type: props.objectType, object_id: props.objectId,
                                 integration_kind: props.integrationKind ?? null, label: props.label ?? undefined }]);
watch(() => props.objectId, () => { void load(); });
onMounted(() => { void load(); window.addEventListener(MAINT_CHANGED, load); });
onBeforeUnmount(() => window.removeEventListener(MAINT_CHANGED, load));
</script>

<template>
  <n-dropdown v-if="cur" trigger="click" :options="activeOpts" @select="onActive">
    <n-button size="small" type="warning" :loading="busy" data-testid="maint-quick-active"
              :title="cur.title + (cur.until ? ` · ${remaining(cur.until, t)}` : '')">
      <template #icon><n-icon><MaintenanceIcon /></n-icon></template>
      {{ cur.until ? t("maint.in_maint_until", { time: hhmm(cur.until) }) : t("maint.in_maint") }}
    </n-button>
  </n-dropdown>
  <n-dropdown v-else trigger="click" :options="startOpts" @select="onStart">
    <n-button size="small" :loading="busy" data-testid="maint-quick">
      <template #icon><n-icon><MaintenanceIcon /></n-icon></template>{{ t("maint.quick") }}
    </n-button>
  </n-dropdown>
  <MaintenanceEditModal v-model:show="editShow" :preset-scopes="preset" @saved="load" />
</template>
