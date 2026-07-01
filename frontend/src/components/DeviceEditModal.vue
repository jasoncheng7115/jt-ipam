<script setup lang="ts">
/**
 * 共用裝置編輯／新增視窗（清單頁與裝置詳情頁都用，詳情頁可就地編輯不離頁）。
 * 含機櫃 U 位挑選器（半 U 感知）。自行載入 location / rack / customer / IP 清單。
 */
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NModal, NForm, NFormItem, NInput, NInputNumber, NInputGroup, NSelect, NSpace, NIcon,
  NButton, NSpin, useMessage,
} from "naive-ui";
import {
  createDevice, updateDevice, type Device, listLocations, listRacks, type Location, type Rack,
} from "@/api/basic";
import { getRackDiagram, type RackDiagram } from "@/api/racks";
import { listAddresses } from "@/api/addresses";
import { EditIcon, PlusIcon, SaveIcon, CancelIcon, RacksIcon } from "@/icons";
import { useCustomers } from "@/composables/useCustomers";

const props = defineProps<{ show: boolean; device: Device | null }>();
const emit = defineEmits<{ (e: "update:show", v: boolean): void; (e: "saved"): void }>();

const { t } = useI18n();
const msg = useMessage();
const { options: customerOptions, ensureLoaded: ensureCustomersLoaded } = useCustomers();

const locations = ref<Location[]>([]);
const racks = ref<Rack[]>([]);
const ipAddrs = ref<{ id: string; ip: string; hostname: string | null }[]>([]);

const form = ref<{
  name: string; fqdn: string; type: string; vendor: string; model: string; serial: string;
  description: string; location_id: string | null; rack_id: string | null;
  u_position: number | null; u_size: number | null;
  rack_face: "front" | "rear" | null; rack_side: "full" | "left" | "right";
  customer_id: string | null; primary_ip_id: string | null;
}>({
  name: "", fqdn: "", type: "server", vendor: "", model: "", serial: "", description: "",
  location_id: null, rack_id: null, u_position: null, u_size: null, rack_face: null,
  rack_side: "full", customer_id: null, primary_ip_id: null,
});

const typeOpts = ["server", "switch", "router", "firewall", "ap", "storage", "ipmi", "other"]
  .map((v) => ({ label: v, value: v }));
const rackFaceOpts = computed(() => [
  { label: t("devices.rack_face_front"), value: "front" },
  { label: t("devices.rack_face_rear"), value: "rear" },
]);
const rackSideOpts = computed(() => [
  { label: t("devices.rack_side_full"), value: "full" },
  { label: t("devices.rack_side_left"), value: "left" },
  { label: t("devices.rack_side_right"), value: "right" },
]);
const ipOptions = computed(() => ipAddrs.value.map((a) => ({
  label: a.hostname ? `${a.ip} — ${a.hostname}` : a.ip, value: a.id,
})));
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
  if (!ipAddrs.value.length) {
    try {
      const r = await listAddresses({ pageSize: 500 });
      ipAddrs.value = r.items.map((a: any) => ({ id: a.id, ip: a.ip, hostname: a.hostname }));
    } catch { /* silent */ }
  }
  void ensureCustomersLoaded();
}

function fillForm(d: Device | null) {
  if (d) {
    form.value = {
      name: d.name, fqdn: d.fqdn ?? "", type: d.type, vendor: d.vendor ?? "", model: d.model ?? "",
      serial: d.serial ?? "", description: d.description ?? "",
      location_id: d.location_id, rack_id: d.rack_id, u_position: d.u_position, u_size: d.u_size,
      rack_face: (d as any).rack_face ?? null, rack_side: (d as any).rack_side ?? "full",
      customer_id: d.customer_id ?? null, primary_ip_id: (d as any).primary_ip_id ?? null,
    };
  } else {
    form.value = {
      name: "", fqdn: "", type: "server", vendor: "", model: "", serial: "", description: "",
      location_id: null, rack_id: null, u_position: null, u_size: null, rack_face: null,
      rack_side: "full", customer_id: null, primary_ip_id: null,
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
function onRackChange() { uPickerDiagram.value = null; }

// ── 機櫃 U 位挑選器（半 U 感知）──
const showUPicker = ref(false);
const uPickerDiagram = ref<RackDiagram | null>(null);
const uPickerLoading = ref(false);
const uHalf = computed<Record<number, { left: string | null; right: string | null }>>(() => {
  const m: Record<number, { left: string | null; right: string | null }> = {};
  for (const d of uPickerDiagram.value?.devices ?? []) {
    if (props.device && d.device_id === props.device.id) continue;
    const side = d.rack_side ?? "full";
    for (let u = d.u_position; u < d.u_position + d.u_size; u++) {
      const cell = (m[u] ??= { left: null, right: null });
      if (side === "left") cell.left = d.name;
      else if (side === "right") cell.right = d.name;
      else { cell.left = d.name; cell.right = d.name; }
    }
  }
  return m;
});
function uPickable(u: number): boolean {
  const cell = uHalf.value[u];
  if (!cell) return true;
  const side = form.value.rack_side;
  if (side === "left") return !cell.left;
  if (side === "right") return !cell.right;
  return !cell.left && !cell.right;
}
function uCellText(u: number): string {
  const cell = uHalf.value[u];
  if (!cell || (!cell.left && !cell.right)) return t("devices.u_free");
  if (cell.left && cell.left === cell.right) return cell.left;
  return `L：${cell.left || t("devices.u_free")}　R：${cell.right || t("devices.u_free")}`;
}
const uRows = computed(() => {
  const n = uPickerDiagram.value?.u_height ?? 0;
  return Array.from({ length: n }, (_, i) => n - i);
});
async function openUPicker() {
  if (!form.value.rack_id) return;
  uPickerLoading.value = true;
  showUPicker.value = true;
  try { uPickerDiagram.value = await getRackDiagram(form.value.rack_id); }
  catch { msg.error(t("errors.network")); }
  finally { uPickerLoading.value = false; }
}
function pickU(u: number) {
  form.value.u_position = u;
  if (!form.value.u_size) form.value.u_size = 1;
  showUPicker.value = false;
}

async function submit() {
  if (!form.value.name.trim()) { msg.error(t("devices.error_name_required")); return; }
  if (form.value.rack_id && !form.value.location_id) { msg.error(t("devices.error_location_for_rack")); return; }
  try {
    const payload = {
      name: form.value.name, fqdn: form.value.fqdn || null, type: form.value.type,
      vendor: form.value.vendor || undefined, model: form.value.model || undefined,
      serial: form.value.serial || undefined, description: form.value.description || undefined,
      location_id: form.value.location_id, rack_id: form.value.rack_id,
      u_position: form.value.u_position, u_size: form.value.u_size,
      rack_face: form.value.rack_id ? form.value.rack_face : null,
      rack_side: form.value.rack_id ? form.value.rack_side : "full",
      customer_id: form.value.customer_id, primary_ip_id: form.value.primary_ip_id,
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
      <n-form-item :label="t('devices.type')"><n-select v-model:value="form.type" :options="typeOpts" /></n-form-item>
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
                    :placeholder="form.location_id ? t('devices.rack_placeholder') : t('devices.rack_pick_location_first')"
                    :disabled="!form.location_id" style="width: 100%" @update:value="onRackChange" />
        </n-form-item>
      </div>
      <div class="dev-row">
        <n-form-item :label="t('devices.u_position')">
          <n-input-group>
            <n-input-number v-model:value="form.u_position" :min="1" :max="99" clearable
                            :disabled="!form.rack_id" style="flex: 1" />
            <n-button :disabled="!form.rack_id" @click="openUPicker" :title="t('devices.pick_u')">
              <template #icon><n-icon><RacksIcon /></n-icon></template>
            </n-button>
          </n-input-group>
        </n-form-item>
        <n-form-item :label="t('devices.u_size')">
          <n-input-number v-model:value="form.u_size" :min="1" :max="99" clearable
                          :disabled="!form.rack_id" style="width: 100%" />
        </n-form-item>
      </div>
      <div class="dev-row">
        <n-form-item :label="t('devices.rack_face')">
          <n-select v-model:value="form.rack_face" :options="rackFaceOpts" clearable
                    :disabled="!form.rack_id" :placeholder="t('devices.rack_face_front')" style="width: 100%" />
        </n-form-item>
        <n-form-item :label="t('devices.rack_side')">
          <n-select v-model:value="form.rack_side" :options="rackSideOpts"
                    :disabled="!form.rack_id" style="width: 100%" />
        </n-form-item>
      </div>

      <n-form-item :label="t('nav.customers')" style="margin-top: 8px">
        <n-select v-model:value="form.customer_id" :options="customerOptions"
                  :placeholder="t('common.not_specified')" clearable filterable />
      </n-form-item>
      <n-form-item :label="t('sections.description')" style="margin-top: 8px">
        <n-input v-model:value="form.description" type="textarea" :rows="2" />
      </n-form-item>
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
    <n-modal v-model:show="showUPicker" preset="card" style="width: 340px" :title="t('devices.pick_u')">
      <n-spin :show="uPickerLoading">
        <p style="font-size:12px; opacity:.65; margin:0 0 8px">{{ t("devices.pick_u_hint") }}</p>
        <div class="upick-rack">
          <div v-for="u in uRows" :key="u" class="upick-row"
               :class="{ occupied: !uPickable(u), cur: form.u_position === u }"
               @click="uPickable(u) && pickU(u)">
            <span class="upick-u">{{ u }}</span>
            <span class="upick-body">{{ uCellText(u) }}</span>
          </div>
        </div>
      </n-spin>
    </n-modal>
  </n-modal>
</template>

<style scoped>
.dev-row { display: flex; gap: 12px; }
.dev-row > * { flex: 1 1 0; min-width: 0; }
.upick-rack { border: 1px solid var(--n-border-color, rgba(127,127,127,.25)); border-radius: 8px; overflow: hidden; max-height: 60vh; overflow-y: auto; }
.upick-row { display: flex; align-items: center; gap: 8px; height: 26px; padding: 0 8px; font-size: 12px; border-bottom: 1px dashed rgba(127,127,127,.18); cursor: pointer; }
.upick-row:last-child { border-bottom: none; }
.upick-row.occupied { cursor: not-allowed; opacity: .55; }
.upick-row.cur { background: rgba(24,160,88,.14); }
.upick-row:not(.occupied):hover { background: rgba(24,160,88,.08); }
.upick-u { width: 28px; text-align: right; opacity: .7; font-variant-numeric: tabular-nums; }
.upick-body { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
