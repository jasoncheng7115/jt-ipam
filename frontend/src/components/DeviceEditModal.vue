<script setup lang="ts">
/**
 * 共用裝置編輯／新增視窗（清單頁與裝置詳細資料頁都用，詳細資料頁可就地編輯不離頁）。
 * 含機櫃 U 位挑選器（半 U 感知）。自行載入 location / rack / customer / IP 清單。
 */
import { computed, ref, watch } from "vue";
import { RACK_SLOTS, WIDTH_PARTS, spanFor, slotFor, partsFor, posFor, usesLevels,
  rackPickRows, rackRowIsTop, slotBoxPct, slotWhere } from "@/utils/rackSlots";
import { useI18n } from "vue-i18n";
import {
  NModal, NForm, NFormItem, NInput, NInputNumber, NInputGroup, NSelect, NSpace, NIcon,
  NButton, NSpin, useMessage,
} from "naive-ui";
import {
  createDevice, updateDevice, type Device, listLocations, listRacks, type Location, type Rack,
} from "@/api/basic";
import { getRackDiagram, type RackDiagram } from "@/api/racks";
import CustomFieldInputs from "@/components/CustomFieldInputs.vue";
import { resolveRackLocation } from "@/utils/rackLocation";
import { useIpOptions } from "@/composables/useIpOptions";
import { EditIcon, PlusIcon, SaveIcon, CancelIcon, RacksIcon } from "@/icons";
import { useCustomers } from "@/composables/useCustomers";
import { apiErrMsg } from "@/api/client";

const props = defineProps<{ show: boolean; device: Device | null }>();
const emit = defineEmits<{ (e: "update:show", v: boolean): void; (e: "saved"): void }>();
// 自訂欄位：定義沒載到就不送（不清掉既有的值）
const cfRef = ref<InstanceType<typeof CustomFieldInputs> | null>(null);
function cfPart(): { custom_fields?: Record<string, unknown> } {
  const cf = cfRef.value?.payload();
  return cf === undefined ? {} : { custom_fields: cf };
}

const { t } = useI18n();
const msg = useMessage();
const { options: customerOptions, ensureLoaded: ensureCustomersLoaded } = useCustomers();

const locations = ref<Location[]>([]);
const racks = ref<Rack[]>([]);

const form = ref<{
  name: string; fqdn: string; type: string; vendor: string; model: string; serial: string;
  description: string; location_id: string | null; rack_id: string | null;
  u_position: number | null; u_size: number | null;
  rack_face: "front" | "rear" | null; rack_slot: number; rack_slot_span: number;
  customer_id: string | null; primary_ip_id: string | null;
}>({
  name: "", fqdn: "", type: "server", vendor: "", model: "", serial: "", description: "",
  location_id: null, rack_id: null, u_position: null, u_size: null, rack_face: null,
  rack_slot: 0, rack_slot_span: RACK_SLOTS, customer_id: null, primary_ip_id: null,
});

const typeOpts = ["server", "switch", "router", "firewall", "ap", "storage", "ipmi",
  "patch_panel", "pdu", "ups", "workstation", "other"]
  .map((v) => ({ label: t(`devices.type_${v}`), value: v }));
const rackFaceOpts = computed(() => [
  { label: t("devices.rack_face_front"), value: "front" },
  { label: t("devices.rack_face_rear"), value: "rear" },
]);
/** 所選機櫃是不是以「層」計 —— 表單標籤要跟著換，不然層架上會寫「U 位」。 */
const rackUsesLevels = computed(() =>
  usesLevels((racks.value.find((r) => r.id === form.value.rack_id) as any)?.kind));
const rackSideOpts = computed(() => [
  // 層架上「整 U」要寫成「整層」—— 同一組選項在兩種機架上用字不同
  { label: rackUsesLevels.value ? t("devices.rack_height_full") : t("devices.rack_width_full"),
    value: 1 },
  ...WIDTH_PARTS.filter((n) => n > 1).map((n) => ({ label: t("devices.rack_width_nth", { n }), value: n })),
]);
// 寬度／位置是介面用的暫存，送出時換算回 rack_slot / rack_slot_span（issue #31）
const widthParts = ref<number>(1);
const widthPos = ref<number>(1);
const widthPosOpts = computed(() => Array.from({ length: widthParts.value }, (_, i) => ({
  label: t("devices.rack_pos_nth", { n: i + 1 }), value: i + 1,
})));
// 層內的「佔高 + 第幾格」—— 與佔寬同一套換算（只是軸換成垂直）。層架一層放得下疊起來
// 的兩三台，而且不一定放滿；機櫃沒有這個概念，所以只在層架類顯示。
const heightParts = ref<number>(1);
const heightPos = ref<number>(1);
const rackHeightOpts = computed(() => [
  { label: t("devices.rack_height_full"), value: 1 },
  ...WIDTH_PARTS.filter((n) => n > 1).map((n) => ({ label: t("devices.rack_width_nth", { n }), value: n })),
]);
const heightPosOpts = computed(() => Array.from({ length: heightParts.value }, (_, i) => ({
  label: t("devices.rack_vpos_nth", { n: i + 1 }), value: i + 1,
})));
// 主要 IP：搜尋走後端，見 useIpOptions 的說明（GitHub issue #27）
const { options: ipOptions, loading: ipLoading, search: searchIps,
        onSearch: onIpSearch, ensure: ensureIp } = useIpOptions();
const locationOpts = computed(() => locations.value.map((l) => ({ label: l.name, value: l.id })));
const filteredRackOpts = computed(() => {
  const all = racks.value.map((r) => ({
    label: r.location_id
      ? `${locations.value.find((l) => l.id === r.location_id)?.name ?? "?"} / ${r.name}`
      : r.name,
    value: r.id, location_id: r.location_id,
  }));
  if (!form.value.location_id) return all;
  return all.filter((r) => r.location_id === form.value.location_id);
});

async function loadLists() {
  try {
    const [l, rk] = await Promise.all([listLocations(), listRacks()]);
    locations.value = l.items; racks.value = rk.items;
  } catch { /* silent */ }
  if (!ipOptions.value.length) await searchIps();
  await ensureIp(form.value.primary_ip_id);
  void ensureCustomersLoaded();
}

function fillForm(d: Device | null) {
  if (d) {
    form.value = {
      name: d.name, fqdn: d.fqdn ?? "", type: d.type, vendor: d.vendor ?? "", model: d.model ?? "",
      serial: d.serial ?? "", description: d.description ?? "",
      location_id: d.location_id, rack_id: d.rack_id, u_position: d.u_position, u_size: d.u_size,
      rack_face: (d as any).rack_face ?? null,
      rack_slot: (d as any).rack_slot ?? 0,
      rack_slot_span: (d as any).rack_slot_span ?? RACK_SLOTS,
      customer_id: d.customer_id ?? null, primary_ip_id: (d as any).primary_ip_id ?? null,
    };
    // 既有資料還原成介面上的「寬度 + 第幾格」
    widthParts.value = partsFor((d as any).rack_slot_span);
    widthPos.value = posFor((d as any).rack_slot, widthParts.value);
    heightParts.value = partsFor((d as any).rack_vslot_span);
    heightPos.value = posFor((d as any).rack_vslot, heightParts.value);
  } else {
    form.value = {
      name: "", fqdn: "", type: "server", vendor: "", model: "", serial: "", description: "",
      location_id: null, rack_id: null, u_position: null, u_size: null, rack_face: null,
      rack_slot: 0, rack_slot_span: RACK_SLOTS, customer_id: null, primary_ip_id: null,
    };
  }
}

watch(() => props.show, (v) => {
  if (v) { fillForm(props.device); uPickerDiagram.value = null; void loadLists(); }
});

function onLocationChange() {
  const ok = racks.value.find((r) => r.id === form.value.rack_id)?.location_id === form.value.location_id;
  if (!ok) { form.value.rack_id = null; uPickerDiagram.value = null; }
}
function onRackChange(rackId: string | null) {
  uPickerDiagram.value = null;
  // 選了機櫃就把地點帶出來 —— 機櫃本來就屬於某個地點，不該再要求使用者選一次。
  // 反過來要求「先選地點」只是把一個查得到的答案丟回去問人。
  const rack = racks.value.find((r) => r.id === rackId);
  if (rack?.location_id) form.value.location_id = rack.location_id;
}

// ── 機櫃 U 位挑選器（半 U 感知）──
const showUPicker = ref(false);
const uPickerDiagram = ref<RackDiagram | null>(null);
const uPickerLoading = ref(false);
/** 每個 U 的逐格占用：slots[i] = 佔住第 i 格的裝置名稱（null = 空）。 */
/**
 * 每一列上已經有誰，連**佔哪一塊**一起記（橫向 h、層內垂直 v 兩個區間）。
 *
 * 以前只記橫向、而且是逐格塗名字：層架上「只佔下半層」的裝置會被當成整層都滿，
 * 結果同一層想再放一台就選不到那一列。判斷改成二維區間相交，與後端的重疊規則一致。
 */
interface Occupant { name: string; h0: number; h1: number; v0: number; v1: number }
const uHalf = computed<Record<number, Occupant[]>>(() => {
  const m: Record<number, Occupant[]> = {};
  for (const d of uPickerDiagram.value?.devices ?? []) {
    if (props.device && d.device_id === props.device.id) continue;
    const h0 = Number((d as any).rack_slot ?? 0);
    const h1 = h0 + Number((d as any).rack_slot_span ?? RACK_SLOTS);
    const v0 = Number((d as any).rack_vslot ?? 0);
    const v1 = v0 + Number((d as any).rack_vslot_span ?? RACK_SLOTS);
    for (let u = d.u_position; u < d.u_position + d.u_size; u++)
      (m[u] ??= []).push({ name: d.name, h0, h1, v0, v1 });
  }
  return m;
});
/** 這台「將要佔的那一塊」——橫向依佔寬、垂直依佔高（機櫃沒有佔高＝整格）。 */
function wantBox() {
  const h0 = slotFor(widthParts.value, widthPos.value);
  const v0 = rackUsesLevels.value ? slotFor(heightParts.value, heightPos.value) : 0;
  return {
    h0, h1: h0 + spanFor(widthParts.value),
    v0, v1: v0 + (rackUsesLevels.value ? spanFor(heightParts.value) : RACK_SLOTS),
  };
}
function uPickable(u: number): boolean {
  const occ = uHalf.value[u];
  if (!occ || !occ.length) return true;
  const w = wantBox();
  // 兩個方向都相交才算撞到 —— 並排或上下疊都是合法的
  return !occ.some((o) => w.h0 < o.h1 && o.h0 < w.h1 && w.v0 < o.v1 && o.v0 < w.v1);
}
/** 小地圖上的一塊；百分比換算與「垂直由下往上」都交給 slotBoxPct，兩支表單共用同一份。 */
function blkStyle(o: { h0: number; h1: number; v0: number; v1: number }): Record<string, string> {
  const b = slotBoxPct(o);
  return { left: `${b.left}%`, width: `${b.width}%`, bottom: `${b.bottom}%`, height: `${b.height}%` };
}
/** 「nas2（右半）」—— 只列名字的話，同一層放兩台就分不出誰在左誰在右。 */
function occupantText(o: Occupant): string {
  const where = slotWhere(o).map((d) => {
    if (d.parts === 2) {
      return d.axis === "h" ? t(d.pos === 1 ? "devices.pos_left" : "devices.pos_right")
                            : t(d.pos === 1 ? "devices.pos_lower" : "devices.pos_upper");
    }
    return t(d.axis === "h" ? "devices.pos_of_h" : "devices.pos_of_v",
             { n: d.parts, k: d.pos });
  });
  return where.length ? t("devices.occupant_at", { name: o.name, where: where.join("·") }) : o.name;
}
function uCellText(u: number): string {
  const occ = uHalf.value[u];
  if (!occ || !occ.length) return t("devices.u_free");
  // 由左而右、同一格由上而下 —— 照畫面上的順序唸，才對得起來
  const ordered = occ.slice().sort((a, b) => a.h0 - b.h0 || b.v0 - a.v0);
  return Array.from(new Set(ordered.map(occupantText))).join("、");
}
const uRows = computed(() => rackPickRows(uPickerDiagram.value as any));
/** 列首的字：開放頂多出來的那一列標「頂」，其餘標層號／U 號。 */
function uRowLabel(u: number): string {
  return rackRowIsTop(uPickerDiagram.value as any, u) ? t("racks.level_top") : String(u);
}
async function openUPicker() {
  if (!form.value.rack_id) return;
  uPickerLoading.value = true;
  showUPicker.value = true;
  try { uPickerDiagram.value = await getRackDiagram(form.value.rack_id); }
  catch (e) { msg.error(apiErrMsg(e)); }
  finally { uPickerLoading.value = false; }
}
function pickU(u: number) {
  form.value.u_position = u;
  if (!form.value.u_size) form.value.u_size = 1;
  showUPicker.value = false;
}

async function submit() {
  if (!form.value.name.trim()) { msg.error(t("devices.error_name_required")); return; }
  // 機櫃本身就掛在地點上 —— 能推的就別叫使用者再講一次（見 utils/rackLocation）
  const loc = resolveRackLocation(form.value.rack_id, form.value.location_id, racks.value);
  if (!loc.ok) { msg.error(t("devices.error_location_mismatch")); return; }
  form.value.location_id = loc.location_id;
  try {
    const payload = {
      name: form.value.name, fqdn: form.value.fqdn || null, type: form.value.type,
      vendor: form.value.vendor?.trim() || null, model: form.value.model?.trim() || null,
      serial: form.value.serial?.trim() || null, description: form.value.description ?? "",
      location_id: form.value.location_id, rack_id: form.value.rack_id,
      u_position: form.value.u_position, u_size: form.value.u_size,
      rack_face: form.value.rack_id ? form.value.rack_face : null,
      rack_slot: form.value.rack_id ? slotFor(widthParts.value, widthPos.value) : 0,
      rack_slot_span: form.value.rack_id ? spanFor(widthParts.value) : RACK_SLOTS,
      // 層架才有層內位置；機櫃一律整層佔滿，與改版前行為相同
      rack_vslot: (form.value.rack_id && rackUsesLevels.value)
        ? slotFor(heightParts.value, heightPos.value) : 0,
      rack_vslot_span: (form.value.rack_id && rackUsesLevels.value)
        ? spanFor(heightParts.value) : RACK_SLOTS,
      customer_id: form.value.customer_id, primary_ip_id: form.value.primary_ip_id,
      ...cfPart(),
    };
    if (props.device) await updateDevice(props.device.id, payload);
    else await createDevice(payload);
    emit("saved");
    emit("update:show", false);
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
</script>

<template>
  <n-modal :show="show" preset="card" style="width: 540px" @update:show="(v) => emit('update:show', v)">
    <template #header>
      <n-space align="center">
        <n-icon :size="20"><component :is="device ? EditIcon : PlusIcon" /></n-icon>
        <span>{{ device ? t("common.edit") : t("common.create") }}</span>
      </n-space>
    </template>
    <n-form label-placement="top">
      <n-form-item :label="t('common.name')"><n-input v-model:value="form.name" /></n-form-item>
      <n-form-item label="FQDN"><n-input v-model:value="form.fqdn" placeholder="sw1.dc.example.com" /></n-form-item>
      <n-form-item :label="t('devices.type')"
                   :feedback="form.type === 'workstation' ? t('devices.type_workstation_hint') : undefined">
        <n-select v-model:value="form.type" :options="typeOpts" />
      </n-form-item>
      <n-space>
        <n-form-item :label="t('devices.vendor')" style="min-width: 220px">
          <n-input v-model:value="form.vendor" placeholder="Cisco / Juniper / Dell …" />
        </n-form-item>
        <n-form-item :label="t('devices.model')" style="min-width: 220px">
          <n-input v-model:value="form.model" placeholder="Catalyst 9300-48P …" />
        </n-form-item>
      </n-space>
      <n-form-item :label="t('devices.serial')"><n-input v-model:value="form.serial" /></n-form-item>
      <n-form-item :label="t('devices.primary_ip')">
        <n-select v-model:value="form.primary_ip_id" :options="ipOptions" filterable clearable
                  remote :loading="ipLoading" @search="onIpSearch"
                  :placeholder="t('common.not_specified')" />
      </n-form-item>

      <h4 style="margin: 8px 0 4px 0">{{ t("devices.placement_section") }}</h4>
      <div class="dev-row">
        <n-form-item :label="t('devices.location')">
          <n-select v-model:value="form.location_id" :options="locationOpts" filterable clearable
                    :placeholder="t('devices.location_placeholder')" @update:value="onLocationChange" style="width: 100%" />
        </n-form-item>
        <n-form-item :label="t('devices.rack')">
          <n-select v-model:value="form.rack_id" :options="filteredRackOpts" filterable clearable
                    :placeholder="t('devices.rack_placeholder')"
                    style="width: 100%" @update:value="onRackChange" />
        </n-form-item>
      </div>
      <div class="dev-row">
        <n-form-item :label="rackUsesLevels ? t('devices.level_position') : t('devices.u_position')">
          <n-input-group>
            <n-input-number v-model:value="form.u_position" :min="1" :max="99" clearable
                            :disabled="!form.rack_id" style="flex: 1" />
            <n-button :disabled="!form.rack_id" @click="openUPicker"
                      :title="rackUsesLevels ? t('devices.pick_level') : t('devices.pick_u')">
              <template #icon><n-icon><RacksIcon /></n-icon></template>
            </n-button>
          </n-input-group>
        </n-form-item>
        <n-form-item :label="rackUsesLevels ? t('devices.level_size') : t('devices.u_size')">
          <n-input-number v-model:value="form.u_size" :min="1" :max="99" clearable
                          :disabled="!form.rack_id" style="width: 100%" />
        </n-form-item>
      </div>
      <div class="dev-row">
        <n-form-item :label="t('devices.rack_face')">
          <n-select v-model:value="form.rack_face" :options="rackFaceOpts" clearable
                    :disabled="!form.rack_id" :placeholder="t('devices.rack_face_front')" style="width: 100%" />
        </n-form-item>
        <n-form-item class="slot-col" :label="t('devices.rack_width')">
          <!-- 固定 120px 兩個併排會超出欄寬而換行：改成等分並允許縮，一列放得下 -->
          <div class="slot-pair">
            <n-select v-model:value="widthParts" :options="rackSideOpts" :disabled="!form.rack_id"
                      :consistent-menu-width="false" @update:value="widthPos = 1" />
            <n-select v-if="widthParts > 1" v-model:value="widthPos" :options="widthPosOpts"
                      :disabled="!form.rack_id" :consistent-menu-width="false" />
          </div>
        </n-form-item>
        <!-- 層內的上下位置：層架一層放得下疊起來的兩三台，也可以不放滿。
             機櫃沒有這個概念（一台就是佔滿整個 U），所以只在層架類出現。 -->
        <n-form-item v-if="rackUsesLevels" class="slot-col" :label="t('devices.rack_height')">
          <div class="slot-pair">
            <n-select v-model:value="heightParts" :options="rackHeightOpts" :disabled="!form.rack_id"
                      :consistent-menu-width="false" @update:value="heightPos = 1" />
            <n-select v-if="heightParts > 1" v-model:value="heightPos" :options="heightPosOpts"
                      :disabled="!form.rack_id" :consistent-menu-width="false" />
          </div>
        </n-form-item>
      </div>

      <n-form-item :label="t('nav.customers')" style="margin-top: 8px">
        <n-select v-model:value="form.customer_id" :options="customerOptions"
                  :placeholder="t('common.not_specified')" clearable filterable />
      </n-form-item>
      <n-form-item :label="t('sections.description')" style="margin-top: 8px">
        <n-input v-model:value="form.description" type="textarea" :rows="2" />
      </n-form-item>
      <CustomFieldInputs ref="cfRef" object-type="device" :initial="props.device?.custom_fields ?? null" />
    </n-form>
    <n-space justify="end">
      <n-button @click="emit('update:show', false)">
        <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
      </n-button>
      <n-button type="primary" @click="submit">
        <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
      </n-button>
    </n-space>

    <!-- 機櫃 U 位挑選器（半 U 感知）-->
    <n-modal v-model:show="showUPicker" preset="card" style="width: 400px"
             :title="rackUsesLevels ? t('devices.pick_level') : t('devices.pick_u')">
      <n-spin :show="uPickerLoading">
        <p style="font-size:12px; opacity:.65; margin:0 0 8px">{{ rackUsesLevels ? t("devices.pick_level_hint") : t("devices.pick_u_hint") }}</p>
        <div class="upick-rack">
          <div v-for="u in uRows" :key="u" class="upick-row"
               :class="{ occupied: !uPickable(u), cur: form.u_position === u }"
               @click="uPickable(u) && pickU(u)">
            <span class="upick-u">{{ uRowLabel(u) }}</span>
            <span class="upick-map" :title="uCellText(u)">
              <!-- 照實際的佔寬／層內位置畫，才看得出「這一層只被占了一半、旁邊還放得下」 -->
              <span v-for="(o, i) in uHalf[u] ?? []" :key="i" class="upick-blk" :style="blkStyle(o)" />
              <span v-if="uPickable(u)" class="upick-want" :style="blkStyle(wantBox())" />
            </span>
            <span class="upick-body">{{ uCellText(u) }}</span>
          </div>
        </div>
      </n-spin>
    </n-modal>
  </n-modal>
</template>

<style scoped>
/* 佔寬的「幾分之一 + 第幾格」要併在同一列 */
.slot-pair { display: flex; gap: 6px; width: 100%; }
/* 左邊只放「1/2」這種短字串，右邊要放「第 3 格（由下往上）」——
   對半分會把右邊擠成「第 …」，所以左邊給固定窄寬、剩下都給右邊。 */
.slot-pair > *:first-child { flex: 0 0 76px; min-width: 0; }
.slot-pair > *:last-child { flex: 1 1 auto; min-width: 0; }

.dev-row { display: flex; gap: 12px; }
.dev-row > * { flex: 1 1 0; min-width: 0; }
/* 「佔寬／佔高」欄位裡是兩個下拉併排，需要的寬度比單一下拉多；平均分會把右邊那個
   擠成「第 …」。連同下拉選單的 consistent-menu-width=false，兩邊都看得到全文。 */
.dev-row > .slot-col { flex: 1.6 1 0; }
.upick-rack { border: 1px solid var(--n-border-color, rgba(127,127,127,.25)); border-radius: 8px; overflow: hidden; max-height: 60vh; overflow-y: auto; }
.upick-row { display: flex; align-items: center; gap: 8px; height: 26px; padding: 0 8px; font-size: 12px; border-bottom: 1px dashed rgba(127,127,127,.18); cursor: pointer; }
.upick-row:last-child { border-bottom: none; }
.upick-row.occupied { cursor: not-allowed; opacity: .55; }
.upick-row.cur { background: rgba(24,160,88,.14); }
.upick-row:not(.occupied):hover { background: rgba(24,160,88,.08); }
.upick-u { width: 28px; text-align: right; opacity: .7; font-variant-numeric: tabular-nums; }
.upick-body { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 這一層的小地圖：整條＝一整層，塊＝已經占住的那一塊，虛線＝目前這台會放進去的位置。
   只列名字看不出「一層只被占了一半」，而那正是層架跟機櫃最大的差別。 */
.upick-map { position: relative; flex: 0 0 84px; height: 18px; border-radius: 4px;
             background: rgba(127,127,127,.10); overflow: hidden; }
.upick-blk { position: absolute; border-radius: 2px; background: rgba(127,127,127,.45); }
.upick-want { position: absolute; border: 1px dashed rgba(24,160,88,.95);
              background: rgba(24,160,88,.18); border-radius: 2px; }
</style>
