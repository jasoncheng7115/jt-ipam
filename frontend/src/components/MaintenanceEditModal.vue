<script setup lang="ts">
/**
 * 維護排程的新增 / 編輯對話框（清單頁、詳細資料頁、物件頁「自訂…」共用）。
 * 時間三種：立即開始（選時數）、排定一次、每週例行（一定要到期日）。範圍可多筆混搭，改了就自動預覽影響；
 * 超過大範圍門檻要勾「我確認」才送得出去。
 */
import JtDatePicker from "@/components/JtDatePicker.vue";
import { computed, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NCheckbox, NCheckboxGroup, NForm, NFormItem, NIcon, NInput, NInputGroup, NInputNumber, NModal, NRadioButton,
  NRadioGroup, NSelect, NSpace, NTimePicker, useMessage,
} from "naive-ui";
import { CancelIcon, DeleteIcon, PlusIcon, SaveIcon } from "@/icons";
import { apiErrMsg } from "@/api/client";
import { Maintenance, type Counts, type ScopeIn, type ScopeType, type Window } from "@/api/maintenance";
import { UNIT_MINUTES, WEEKDAY_ORDER, durationLabel, type DurationUnit } from "@/utils/maintenance";

const props = defineProps<{ show: boolean; window?: Window | null; presetScopes?: (ScopeIn & { label?: string })[] }>();
const emit = defineEmits<{ "update:show": [boolean]; saved: [Window] }>();
const { t } = useI18n();
const msg = useMessage();

interface Row { type: ScopeType; value: string | null; kind: string | null; range: string;
                options: { label: string; value: string }[]; loading: boolean }
const form = reactive({
  busy: false, title: "", ticket: "", note: "", behavior: "mute" as "mute" | "mark",
  mode: "now" as "now" | "once" | "weekly", minutes: 120, customAmount: 90 as number | null, customUnit: "minute" as DurationUnit,
  range: null as [number, number] | null,
  weekdays: [] as number[], start_time: "02:00" as string | null, end_time: "04:00" as string | null,
  valid: null as [string, string] | null, confirm: false,
});
const rows = ref<Row[]>([]);
const counts = ref<Counts | null>(null);
const isEdit = computed(() => !!props.window);

const TYPES: ScopeType[] = ["ip", "subnet", "section", "device", "rack", "location", "customer", "ip_range",
                            "integration", "scan_agent"];
const typeOpts = computed(() => TYPES.map((x) => ({ label: t(`maint.scope_${x}`), value: x })));
const durationOpts = computed(() => [30, 60, 120, 240, 480].map((m) => ({ label: durationLabel(m, t), value: m }))
  .concat([{ label: t("maint.custom"), value: -1 }]));
const weekdayOpts = computed(() => WEEKDAY_ORDER.map((d) => ({ label: t(`maint.wd_${d}`), value: d })));
const unitOpts = computed(() => (["minute", "hour", "day"] as DurationUnit[]).map((u) => ({ label: t(`maint.unit_${u}`), value: u })));

function encode(type: ScopeType, id: string | null, kind: string | null): string {
  return type === "integration" ? `${kind}|${id ?? ""}` : (id ?? "");
}
function newRow(type: ScopeType = "ip"): Row {
  return { type, value: null, kind: null, range: "", options: [], loading: false };
}
watch(() => props.show, (v) => {
  if (!v) return;
  const w = props.window;
  Object.assign(form, {
    busy: false, title: w?.title ?? "", ticket: w?.ticket ?? "", note: w?.note ?? "", behavior: w?.behavior ?? "mute",
    mode: w ? (w.schedule_type === "weekly" ? "weekly" : "once") : "now", minutes: 120, customAmount: 90 as number | null, customUnit: "minute" as DurationUnit,
    range: w?.starts_at && w?.ends_at ? [new Date(w.starts_at).getTime(), new Date(w.ends_at).getTime()] : null,
    weekdays: w?.weekdays ?? [], start_time: w?.start_time ?? "02:00", end_time: w?.end_time ?? "04:00",
    valid: w?.valid_until ? [w.valid_from ?? new Date().toISOString().slice(0, 10), w.valid_until] : null,
    confirm: false,
  });
  const src: (ScopeIn & { label?: string | null })[] = w?.scopes ?? props.presetScopes ?? [];
  rows.value = src.length ? src.map((s) => {
    const r = newRow(s.scope_type);
    r.kind = s.integration_kind ?? null;
    r.range = s.range ?? "";
    r.value = s.scope_type === "ip_range" ? null : encode(s.scope_type, s.object_id ?? null, s.integration_kind ?? null);
    if (r.value !== null) r.options = [{ label: s.label ?? r.value, value: r.value }];
    return r;
  }) : [newRow()];
  counts.value = null;
  void refreshPreview();
});

async function search(r: Row, q: string) {
  r.loading = true;
  try {
    const items = await Maintenance.search(r.type, q);
    r.options = items.map((x) => ({ label: x.label, value: encode(r.type, x.id, x.integration_kind ?? null) }));
  } catch { /* 搜不到就空著 */ } finally { r.loading = false; }
}
function onType(r: Row) { r.value = null; r.options = []; r.range = ""; void search(r, ""); }

function scopesOut(): ScopeIn[] {
  const out: ScopeIn[] = [];
  for (const r of rows.value) {
    if (r.type === "ip_range") {
      if (r.range.trim()) out.push({ scope_type: "ip_range", range: r.range.trim() });
    } else if (r.value !== null && r.value !== "") {
      if (r.type === "integration") {
        const [kind, id] = r.value.split("|");
        out.push({ scope_type: "integration", integration_kind: kind, object_id: id || null });
      } else {
        out.push({ scope_type: r.type, object_id: r.value });
      }
    }
  }
  return out;
}
let timer: ReturnType<typeof setTimeout> | null = null;
async function refreshPreview() {
  const sc = scopesOut();
  if (!sc.length) { counts.value = null; return; }
  try { counts.value = await Maintenance.preview(sc); } catch { counts.value = null; }
}
watch(rows, () => { if (timer) clearTimeout(timer); timer = setTimeout(refreshPreview, 500); }, { deep: true });
const large = computed(() => !!counts.value && counts.value.ips > (counts.value.large_threshold ?? 500));

function iso(ts: number): string { return new Date(ts).toISOString(); }
async function save() {
  const scopes = scopesOut();
  if (!form.title.trim() || !scopes.length) return;
  let schedule;
  if (form.mode === "now") {
    const minutes = form.minutes === -1 ? (form.customAmount ?? 0) * UNIT_MINUTES[form.customUnit] : form.minutes;
    schedule = { type: "once" as const, starts_at: iso(Date.now() - 1000), ends_at: iso(Date.now() + minutes * 60000) };
  } else if (form.mode === "once") {
    if (!form.range) { msg.warning(t("maint.need_time")); return; }
    schedule = { type: "once" as const, starts_at: iso(form.range[0]), ends_at: iso(form.range[1]) };
  } else {
    if (!form.weekdays.length || !form.valid) { msg.warning(t("maint.need_weekly")); return; }
    schedule = { type: "weekly" as const, weekdays: form.weekdays, start_time: form.start_time, end_time: form.end_time,
                 valid_from: form.valid[0], valid_until: form.valid[1] };
  }
  form.busy = true;
  const body = { title: form.title.trim(), ticket: form.ticket.trim() || null, note: form.note.trim() || null,
                 behavior: form.behavior, schedule, scopes, confirm_large: form.confirm };
  try {
    const w = isEdit.value ? await Maintenance.update(props.window!.id, body) : await Maintenance.create(body);
    emit("saved", w);
    emit("update:show", false);
    msg.success(t("common.saved"));
  } catch (e) { msg.error(apiErrMsg(e)); } finally { form.busy = false; }
}
const canSave = computed(() => !!form.title.trim() && scopesOut().length > 0 && (!large.value || form.confirm));
</script>

<template>
  <n-modal :show="show" preset="card" style="width: 680px; max-width: 96vw"
           :title="isEdit ? t('maint.edit') : t('maint.add')" @update:show="(v: boolean) => emit('update:show', v)">
    <n-form label-placement="top">
      <n-form-item :label="t('maint.title')">
        <n-input v-model:value="form.title" maxlength="200" :placeholder="t('maint.title_ph')" data-testid="maint-title" />
      </n-form-item>
      <n-space :size="12" :wrap="false" style="width: 100%">
        <n-form-item :label="t('maint.ticket')" style="flex: 1">
          <n-input v-model:value="form.ticket" maxlength="100" :placeholder="t('maint.ticket_ph')" />
        </n-form-item>
        <n-form-item :label="t('maint.behavior')" style="flex: 1">
          <n-radio-group v-model:value="form.behavior" size="small">
            <n-radio-button value="mute">{{ t("maint.behavior_mute") }}</n-radio-button>
            <n-radio-button value="mark">{{ t("maint.behavior_mark") }}</n-radio-button>
          </n-radio-group>
        </n-form-item>
      </n-space>
      <div class="hint" style="margin: -12px 0 14px">{{ form.behavior === "mute" ? t("maint.behavior_mute_hint") : t("maint.behavior_mark_hint") }}</div>

      <n-form-item :label="t('maint.when')">
        <n-radio-group v-model:value="form.mode" size="small" data-testid="maint-mode">
          <n-radio-button v-if="!isEdit" value="now">{{ t("maint.mode_now") }}</n-radio-button>
          <n-radio-button value="once">{{ t("maint.mode_once") }}</n-radio-button>
          <n-radio-button value="weekly">{{ t("maint.mode_weekly") }}</n-radio-button>
        </n-radio-group>
      </n-form-item>
      <template v-if="form.mode === 'now'">
        <n-space align="center" style="margin: -8px 0 16px">
          <n-select v-model:value="form.minutes" :options="durationOpts" style="width: 160px" data-testid="maint-duration" />
          <n-input-group v-if="form.minutes === -1" style="width: 220px">
            <n-input-number v-model:value="form.customAmount" :min="1" :precision="0" :show-button="false"
                            style="width: 110px" data-testid="maint-custom-amount" />
            <n-select v-model:value="form.customUnit" :options="unitOpts" style="width: 110px"
                      data-testid="maint-custom-unit" />
          </n-input-group>
        </n-space>
      </template>
      <n-form-item v-else-if="form.mode === 'once'" :show-label="false">
        <jt-date-picker v-model:value="form.range" type="datetimerange" clearable style="width: 100%" />
      </n-form-item>
      <template v-else>
        <n-form-item :label="t('maint.weekdays')">
          <n-checkbox-group v-model:value="form.weekdays">
            <n-space :size="10"><n-checkbox v-for="o in weekdayOpts" :key="o.value" :value="o.value" :label="o.label" /></n-space>
          </n-checkbox-group>
        </n-form-item>
        <n-space :size="12" align="center" style="margin-top: -8px">
          <n-time-picker v-model:formatted-value="form.start_time" value-format="HH:mm" format="HH:mm" style="width: 110px" />
          <span>-</span>
          <n-time-picker v-model:formatted-value="form.end_time" value-format="HH:mm" format="HH:mm" style="width: 110px" />
          <span class="hint">{{ t("maint.cross_midnight") }}</span>
        </n-space>
        <n-form-item :label="t('maint.valid')" style="margin-top: 12px">
          <jt-date-picker v-model:formatted-value="form.valid" value-format="yyyy-MM-dd" type="daterange" style="width: 100%" />
        </n-form-item>
      </template>

      <n-form-item :label="t('maint.scopes')">
        <div style="width: 100%">
          <div v-for="(r, i) in rows" :key="i" class="scope-row" data-testid="maint-scope-row">
            <n-select v-model:value="r.type" :options="typeOpts" style="width: 150px; flex: none" @update:value="onType(r)" />
            <n-input v-if="r.type === 'ip_range'" v-model:value="r.range" placeholder="192.0.2.10-192.0.2.50 / 192.0.2.0/24" />
            <n-select v-else v-model:value="r.value" :options="r.options" filterable remote clearable :loading="r.loading"
                      :placeholder="t('maint.search_ph')" @search="(q: string) => search(r, q)" @focus="() => !r.options.length && search(r, '')"
                      data-testid="maint-scope-pick" />
            <n-button quaternary :disabled="rows.length === 1" :title="t('common.delete')" @click="rows.splice(i, 1)">
              <template #icon><n-icon><DeleteIcon /></n-icon></template>
            </n-button>
          </div>
          <n-button size="small" dashed @click="rows.push(newRow())">
            <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("maint.add_scope") }}
          </n-button>
          <div v-if="counts" class="preview" data-testid="maint-preview">
            {{ t("maint.preview", { ips: counts.ips, subnets: counts.subnets, devices: counts.devices,
                                    integrations: counts.integrations, agents: counts.agents }) }}
            <span v-if="counts.range_addresses">{{ t("maint.preview_range", { n: counts.range_addresses }) }}</span>
          </div>
          <n-alert v-if="large" type="warning" :bordered="false" style="margin-top: 8px">
            {{ t("maint.large_warn", { n: counts!.ips }) }}
            <div style="margin-top: 6px"><n-checkbox v-model:checked="form.confirm">{{ t("maint.large_confirm") }}</n-checkbox></div>
          </n-alert>
        </div>
      </n-form-item>
      <n-form-item :label="t('maint.note')">
        <n-input v-model:value="form.note" type="textarea" :autosize="{ minRows: 2 }" maxlength="4000" />
      </n-form-item>
    </n-form>
    <template #footer>
      <n-space justify="end">
        <n-button @click="emit('update:show', false)">
          <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
        </n-button>
        <n-button type="primary" :loading="form.busy" :disabled="form.busy || !canSave" data-testid="maint-save" @click="save">
          <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
        </n-button>
      </n-space>
    </template>
  </n-modal>
</template>

<style scoped>
.hint { font-size: 12.5px; opacity: .7; }
.scope-row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
.preview { margin-top: 10px; font-size: 13px; padding: 6px 10px; border-radius: 6px; background: rgba(128,128,128,.08); }
</style>
