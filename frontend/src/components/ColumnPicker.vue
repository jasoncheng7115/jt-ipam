<script setup lang="ts">
/**
 * 表格欄位挑選器：勾選要顯示的欄位，並可拖拉調整欄位順序。
 *
 * 用法（搭配 useColumnPrefs）：
 *   <ColumnPicker
 *     :all="[{key: 'ip', label: 'IP'}, ...]"
 *     :visible="visibleKeys" @update:visible="setVisible"
 *     :order="order" @update:order="setOrder"
 *     @reset="reset"
 *   />
 *
 * 拖拉用 pointer events 做（滑鼠、觸控、觸控筆都通，不靠 HTML5 drag，手機上也拖得動），
 * 抓每一列左邊的把手；把手也能用鍵盤聚焦後按上下方向鍵移動一格。
 * 拖拉中只改畫面上的暫定順序，放開才送出 update:order。
 * 沒有傳 order（undefined）的用法不顯示把手，避免拖了卻存不起來。
 */
import { computed, nextTick, ref } from "vue";
import { useI18n } from "vue-i18n";
import { NButton, NPopover, NCheckbox, NIcon } from "naive-ui";
import { Settings as SettingsIcon } from "@iconoir/vue";
import { DragHandleIcon, RestoreIcon } from "@/icons";
import { applyOrder, moveItem } from "@/utils/columnOrder";

const { t } = useI18n();

type Item = { key: string; label: string };

const props = defineProps<{
  all: Item[];
  visible: string[];
  order?: string[] | null;   // 使用者自訂的完整欄位順序；null = 照 all 的順序
  size?: "tiny" | "small" | "medium" | "large";   // 對齊鄰近按鈕高度（明細頁多為 small）
}>();
const emit = defineEmits<{
  (e: "update:visible", v: string[]): void;
  (e: "update:order", v: string[]): void;
  (e: "reset"): void;
}>();

const reorderable = computed(() => props.order !== undefined);
const items = computed<Item[]>(() => applyOrder(props.all, props.order, (c) => c.key));
const draft = ref<Item[] | null>(null);          // 拖拉中的暫定順序
const shown = computed(() => draft.value ?? items.value);
const dragKey = ref<string | null>(null);
const listEl = ref<HTMLElement | null>(null);
const scrollEl = ref<HTMLElement | null>(null);

function toggle(key: string, checked: boolean) {
  const next = checked
    ? [...new Set([...props.visible, key])]
    : props.visible.filter((k) => k !== key);
  emit("update:visible", next);
}

function sameOrder(a: Item[], b: Item[]): boolean {
  return a.length === b.length && a.every((c, i) => c.key === b[i].key);
}

function onPointerDown(e: PointerEvent, key: string) {
  if (e.pointerType === "mouse" && e.button !== 0) return;
  e.preventDefault();
  try { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); } catch { /* 不支援就靠事件冒泡 */ }
  dragKey.value = key;
  draft.value = items.value.slice();
}

// 拖到清單上下緣時自動捲動（欄位多時清單有最大高度）
function autoScroll(clientY: number) {
  const el = scrollEl.value;
  if (!el) return;
  const r = el.getBoundingClientRect();
  if (clientY < r.top + 24) el.scrollTop -= 12;
  else if (clientY > r.bottom - 24) el.scrollTop += 12;
}

function onPointerMove(e: PointerEvent) {
  if (dragKey.value === null || !draft.value || !listEl.value) return;
  autoScroll(e.clientY);
  const rows = Array.from(listEl.value.querySelectorAll<HTMLElement>("[data-picker-key]"));
  if (!rows.length) return;
  // 指標所在的那一列（在第一列上方＝最前面、最後一列下方＝最後面）
  let over = rows.length - 1;
  for (let i = 0; i < rows.length; i++) {
    if (e.clientY < rows[i].getBoundingClientRect().bottom) { over = i; break; }
  }
  const from = draft.value.findIndex((c) => c.key === dragKey.value);
  if (from >= 0 && over !== from) draft.value = moveItem(draft.value, from, over);
}

function onPointerUp() {
  const d = draft.value;
  dragKey.value = null;
  draft.value = null;
  if (d && !sameOrder(d, items.value)) emit("update:order", d.map((c) => c.key));
}

function onPointerCancel() {
  dragKey.value = null;
  draft.value = null;
}

function focusHandle(key: string) {
  const row = Array.from(listEl.value?.querySelectorAll<HTMLElement>("[data-picker-key]") ?? [])
    .find((el) => el.dataset.pickerKey === key);
  row?.querySelector<HTMLElement>(".col-drag")?.focus();
}

// 鍵盤：把手聚焦後按上／下方向鍵移動一格
function onHandleKey(e: KeyboardEvent, key: string) {
  const delta = e.key === "ArrowUp" ? -1 : e.key === "ArrowDown" ? 1 : 0;
  if (!delta) return;
  e.preventDefault();
  const list = items.value;
  const from = list.findIndex((c) => c.key === key);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= list.length) return;
  emit("update:order", moveItem(list, from, to).map((c) => c.key));
  void nextTick(() => focusHandle(key));
}
</script>

<template>
  <n-popover trigger="click" placement="bottom-end">
    <template #trigger>
      <n-button :size="props.size" :title="t('column_picker.columns')">
        <template #icon><n-icon><SettingsIcon /></n-icon></template>
        {{ t("column_picker.columns") }}
      </n-button>
    </template>
    <div ref="scrollEl" class="col-picker">
      <div ref="listEl" class="col-picker-list" :class="{ 'is-dragging': dragKey !== null }">
        <div
          v-for="c in shown" :key="c.key"
          class="col-picker-row" :class="{ 'is-drag-source': c.key === dragKey }"
          :data-picker-key="c.key"
        >
          <button
            v-if="reorderable"
            type="button" class="col-drag"
            :title="t('column_picker.drag_hint')"
            :aria-label="t('column_picker.drag_aria', { label: c.label || c.key })"
            @pointerdown="onPointerDown($event, c.key)"
            @pointermove="onPointerMove"
            @pointerup="onPointerUp"
            @pointercancel="onPointerCancel"
            @keydown="onHandleKey($event, c.key)"
          >
            <n-icon :size="14"><DragHandleIcon /></n-icon>
          </button>
          <n-checkbox
            :checked="props.visible.includes(c.key)"
            @update:checked="(v: boolean) => toggle(c.key, v)"
          >
            {{ c.label || c.key }}
          </n-checkbox>
        </div>
      </div>
      <div class="col-picker-foot">
        <n-button size="tiny" quaternary @click="emit('reset')"><template #icon><n-icon><RestoreIcon /></n-icon></template>{{ t("column_picker.reset") }}</n-button>
      </div>
    </div>
  </n-popover>
</template>

<style scoped>
.col-picker { min-width: 180px; max-height: 360px; overflow-y: auto; }
.col-picker-list { display: flex; flex-direction: column; gap: 6px; }
.col-picker-list.is-dragging { user-select: none; cursor: grabbing; }
.col-picker-row { display: flex; align-items: center; gap: 4px; border-radius: 4px; }
.col-picker-row.is-drag-source { background: rgba(127, 127, 127, 0.16); }
.col-drag {
  flex: none; display: inline-flex; align-items: center; justify-content: center;
  width: 20px; height: 20px; padding: 0; border: 0; border-radius: 4px;
  background: transparent; color: inherit; opacity: 0.5;
  cursor: grab; touch-action: none; user-select: none;
}
.col-drag:hover, .col-drag:focus-visible { opacity: 1; background: rgba(127, 127, 127, 0.14); }
.col-drag:focus-visible { outline: 2px solid rgba(24, 160, 88, 0.6); outline-offset: 1px; }
.col-picker-list.is-dragging .col-drag { cursor: grabbing; }
.col-picker-foot { border-top: 1px solid rgba(127, 127, 127, 0.2); margin-top: 6px; padding-top: 6px; }
</style>
