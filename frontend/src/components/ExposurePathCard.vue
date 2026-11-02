<script setup lang="ts">
/**
 * IP 詳細頁「對外路徑」（客戶許願清單 2026-10-28）：網際網路 → WAN IP → 防火牆 → [手動插入] → 交換器埠 → 內部主機。
 * 系統推出來的段落（虛線框以外）跟著資料走；手動插入的 WAF、負載平衡器…可以加、調順序、刪除（要子網路修改權）。
 * 沒有任何轉發到 / 從這個 IP 出去的 NAT 時整張卡片不出現；沒有全域讀取的帳號不打這支 API。
 */
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import {
  NButton, NCard, NForm, NFormItem, NIcon, NInput, NModal, NPopconfirm, NSelect, NSpace, NTooltip, useMessage,
} from "naive-ui";
import {
  ArrowDownIcon, ArrowUpIcon, CancelIcon, DeleteIcon, PathIcon, PlusIcon, SaveIcon,
} from "@/icons";
import { apiClient, apiErrMsg } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { listDevices } from "@/api/basic";

interface Hop {
  kind: string; label: string | null; port?: string | null; vlan?: number | null; auto: boolean;
  id?: string; note?: string | null; position?: number;
  device?: { id: string; name: string } | null; vm?: { name: string; node: string | null; cluster: string | null } | null;
  circuits?: { id: string; cid: string }[]; nat?: { id: string; name: string; protocol: string };
  address_id?: string | null; is_interface?: boolean; hostname?: string | null; vendor?: string | null;
  unresolved?: boolean;
}
interface PathData { paths: { nat_id: string; nat_name: string; protocol: string; hops: Hop[];
                              editable_address_id: string | null }[];
                     manual_hops: Hop[]; can_edit: boolean }

const props = defineProps<{ addressId: string }>();
const { t, te } = useI18n();
const msg = useMessage();
const router = useRouter();
const auth = useAuthStore();

const data = ref<PathData | null>(null);
async function load() {
  if (auth.me?.has_global_read === false) return;
  try {
    const { data: d } = await apiClient.get<PathData>(`/api/v1/addresses/${props.addressId}/exposure-path`);
    data.value = d;
  } catch { data.value = null; }
}

function kindText(k: string): string {
  const key = `exposure.kind_${k}`;
  return te(key) ? t(key) : k;
}
function go(name: string, id: string) { void router.push({ name, params: { id } }); }
// 這個 IP 自己的路徑才能編輯（從 WAN IP 頁看到的手動設備屬於內部主機，去那一頁改）
const editableHere = computed(() => !!data.value?.can_edit
  && data.value.paths.some((p) => p.editable_address_id === props.addressId));

// ── 新增／調整手動插入的設備 ──
const form = reactive({ show: false, busy: false, kind: "waf", device_id: null as string | null, label: "", note: "" });
const devices = ref<{ label: string; value: string }[]>([]);
const kindOpts = computed(() => ["firewall", "waf", "load_balancer", "proxy", "router", "switch", "other"]
  .map((k) => ({ label: kindText(k), value: k })));
async function openAdd() {
  Object.assign(form, { show: true, busy: false, kind: "waf", device_id: null, label: "", note: "" });
  if (!devices.value.length) {
    try {
      devices.value = (await listDevices({ pageSize: 500 })).items.map((d: any) => ({ label: d.name, value: d.id }));
    } catch { /* 選不到裝置時仍可填名稱 */ }
  }
}
async function save() {
  if (!form.device_id && !form.label.trim()) return;
  form.busy = true;
  try {
    await apiClient.post(`/api/v1/addresses/${props.addressId}/path-hops`, {
      kind: form.kind, device_id: form.device_id, label: form.label.trim() || null, note: form.note.trim() || null,
    });
    form.show = false;
    await load();
  } catch (e) { msg.error(apiErrMsg(e)); } finally { form.busy = false; }
}
async function move(h: Hop, dir: -1 | 1) {
  const list = data.value?.manual_hops ?? [];
  const i = list.findIndex((x) => x.id === h.id);
  const j = i + dir;
  if (i < 0 || j < 0 || j >= list.length) return;
  const other = list[j];
  try {   // 兩筆互換位置（位置用陣列索引重新編，舊資料位置相同時也排得開）
    await apiClient.patch(`/api/v1/addresses/${props.addressId}/path-hops/${h.id}`, { position: j });
    await apiClient.patch(`/api/v1/addresses/${props.addressId}/path-hops/${other.id}`, { position: i });
    await load();
  } catch (e) { msg.error(apiErrMsg(e)); }
}
async function remove(h: Hop) {
  try {
    await apiClient.delete(`/api/v1/addresses/${props.addressId}/path-hops/${h.id}`);
    await load();
  } catch (e) { msg.error(apiErrMsg(e)); }
}

watch(() => props.addressId, () => { void load(); });
onMounted(load);
</script>

<template>
  <n-card v-if="data && data.paths.length" size="small" data-testid="exposure-path">
    <template #header>
      <span style="display:inline-flex;align-items:center;gap:8px"><n-icon :size="18"><PathIcon /></n-icon>{{ t("exposure.title") }}</span>
    </template>
    <n-space v-if="editableHere" align="center" justify="end" style="margin-bottom: 8px">
      <n-button size="small" type="primary" data-testid="exposure-add-hop" @click="openAdd">
        <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("exposure.add_hop") }}
      </n-button>
    </n-space>
    <div class="hint" style="margin-bottom: 10px">{{ t("exposure.hint") }}</div>

    <div v-for="p in data.paths" :key="p.nat_id" class="path" data-testid="exposure-path-row">
      <template v-for="(h, i) in p.hops" :key="i">
        <span v-if="i" class="arrow" aria-hidden="true">→</span>
        <div class="hop" :class="[`k-${h.kind}`, { manual: !h.auto }]">
          <div class="kind">{{ kindText(h.kind) }}<span v-if="!h.auto" class="tag-manual">{{ t("exposure.manual") }}</span></div>
          <!-- 網際網路 -->
          <div v-if="h.kind === 'internet'" class="main">Internet</div>
          <!-- WAN：位址或介面＋外部埠＋承載電路 -->
          <template v-else-if="h.kind === 'wan'">
            <div class="main mono">
              <a v-if="h.address_id" href="#" @click.prevent="go('address-detail', h.address_id)">{{ h.label }}</a>
              <span v-else>{{ h.label || "—" }}</span><span v-if="h.port">:{{ h.port }}</span>
            </div>
            <div v-for="c in h.circuits || []" :key="c.id" class="sub">
              {{ t("exposure.circuit") }} <a href="#" @click.prevent="go('adv-circuit-detail', c.id)">{{ c.cid }}</a>
            </div>
          </template>
          <!-- 防火牆＋NAT 規則 -->
          <template v-else-if="h.kind === 'firewall' && h.auto">
            <div class="main">{{ h.label || t("exposure.fw_manual") }}</div>
            <div v-if="h.nat" class="sub">NAT {{ h.nat.name }} · {{ h.nat.protocol }}</div>
          </template>
          <!-- 交換器埠 -->
          <template v-else-if="h.kind === 'switch' && h.auto">
            <div class="main">{{ h.label }}</div>
            <div class="sub mono">{{ h.port }}<span v-if="h.vlan"> · VLAN {{ h.vlan }}</span></div>
          </template>
          <!-- 內部主機 -->
          <template v-else-if="h.kind === 'target'">
            <div class="main mono">
              <a v-if="h.address_id && h.address_id !== addressId" href="#" @click.prevent="go('address-detail', h.address_id)">{{ h.label }}</a>
              <span v-else>{{ h.label || "—" }}</span><span v-if="h.port">:{{ h.port }}</span>
            </div>
            <div v-if="h.device" class="sub"><a href="#" @click.prevent="go('device-detail', h.device.id)">{{ h.device.name }}</a></div>
            <div v-else-if="h.hostname" class="sub">{{ h.hostname }}</div>
            <div v-if="h.vm" class="sub">{{ t("exposure.vm_on", { vm: h.vm.name, node: h.vm.node || h.vm.cluster || "—" }) }}</div>
            <div v-if="h.unresolved" class="sub">{{ t("exposure.unresolved") }}</div>
          </template>
          <!-- 手動插入的設備 -->
          <template v-else>
            <div class="main">
              <a v-if="h.device" href="#" @click.prevent="go('device-detail', h.device.id)">{{ h.device.name }}</a>
              <span v-else>{{ h.label }}</span>
            </div>
            <n-tooltip v-if="h.note">
              <template #trigger><div class="sub ellipsis">{{ h.note }}</div></template>{{ h.note }}
            </n-tooltip>
            <div v-if="editableHere && h.id" class="ops">
              <n-button size="tiny" quaternary :title="t('exposure.move_up')" @click="move(h, -1)">
                <template #icon><n-icon><ArrowUpIcon /></n-icon></template>
              </n-button>
              <n-button size="tiny" quaternary :title="t('exposure.move_down')" @click="move(h, 1)">
                <template #icon><n-icon><ArrowDownIcon /></n-icon></template>
              </n-button>
              <n-popconfirm @positive-click="remove(h)">
                <template #trigger>
                  <n-button size="tiny" quaternary type="error" :title="t('common.delete')">
                    <template #icon><n-icon><DeleteIcon /></n-icon></template>
                  </n-button>
                </template>
                {{ t("exposure.remove_confirm") }}
              </n-popconfirm>
            </div>
          </template>
        </div>
      </template>
    </div>

    <n-modal v-model:show="form.show" preset="card" style="width: 480px; max-width: 95vw" :title="t('exposure.add_hop')">
      <div class="hint" style="margin-bottom: 12px">{{ t("exposure.add_hint") }}</div>
      <n-form label-placement="top">
        <n-form-item :label="t('exposure.hop_kind')">
          <n-select v-model:value="form.kind" :options="kindOpts" />
        </n-form-item>
        <n-form-item :label="t('exposure.hop_device')">
          <n-select v-model:value="form.device_id" :options="devices" filterable clearable data-testid="exposure-hop-device" />
        </n-form-item>
        <n-form-item :label="t('exposure.hop_label')">
          <n-input v-model:value="form.label" maxlength="128" :placeholder="t('exposure.hop_label_ph')"
                   data-testid="exposure-hop-label" />
        </n-form-item>
        <n-form-item :label="t('exposure.hop_note')">
          <n-input v-model:value="form.note" maxlength="1024" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="form.show = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
          </n-button>
          <n-button type="primary" :loading="form.busy" :disabled="form.busy || (!form.device_id && !form.label.trim())"
                    data-testid="exposure-hop-save" @click="save">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </n-card>
</template>

<style scoped>
.hint { font-size: 12.5px; opacity: .7; }
.path { display: flex; flex-wrap: wrap; align-items: stretch; gap: 6px; padding: 8px 0; }
.path + .path { border-top: 1px dashed var(--n-border-color, rgba(128,128,128,.25)); }
.arrow { align-self: center; opacity: .5; font-size: 16px; }
.hop { min-width: 120px; max-width: 220px; padding: 6px 10px; border-radius: 8px;
       border: 1px solid var(--n-border-color, rgba(128,128,128,.25)); background: rgba(128,128,128,.06); }
.hop.manual { border-style: dashed; border-color: rgba(24,160,88,.6); }
.hop.k-internet { background: rgba(59,130,246,.08); }
.hop.k-target { border-color: rgba(24,160,88,.5); }
.kind { font-size: 11.5px; opacity: .65; margin-bottom: 2px; display: flex; align-items: center; gap: 6px; }
.tag-manual { font-size: 10.5px; padding: 0 5px; border-radius: 4px; background: rgba(24,160,88,.15); color: #18a058; }
.main { font-weight: 600; font-size: 13.5px; word-break: break-all; }
.sub { font-size: 12px; opacity: .75; word-break: break-all; }
.ellipsis { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.hop a { color: var(--primary-color, #18a058); text-decoration: none; }
.hop a:hover { text-decoration: underline; }
.ops { margin-top: 4px; display: flex; gap: 2px; }
</style>
