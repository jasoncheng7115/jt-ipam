<script setup lang="ts">
/**
 * 全站的日期選擇器：跟 n-date-picker 一樣用（屬性、v-model 都直接傳下去），只是面板右下的
 * 「清除 / 現在 / 確定」按鈕補上圖示 —— 按鈕都要有圖示（2026-11-02 使用者指出日期選擇器沒有）。
 * 新的日期欄位一律用這個；tests/test_date_picker_icons.py 守著不可以再直接寫 <n-date-picker>。
 */
import { NButton, NDatePicker, NIcon } from "naive-ui";
import { CancelIcon, CheckIcon, ElapsedIcon } from "@/icons";
</script>

<template>
  <!-- 月曆從星期日開始（跟維護排程的星期選項、台灣的習慣一致）；個別欄位可以自己傳 first-day-of-week 蓋掉 -->
  <n-date-picker :first-day-of-week="6">
    <template #clear="{ onClear, text }">
      <n-button size="tiny" data-testid="date-clear" @click="onClear">
        <template #icon><n-icon><CancelIcon /></n-icon></template>{{ text }}
      </n-button>
    </template>
    <template #now="{ onNow, text }">
      <n-button size="tiny" data-testid="date-now" @click="onNow">
        <template #icon><n-icon><ElapsedIcon /></n-icon></template>{{ text }}
      </n-button>
    </template>
    <template #confirm="{ onConfirm, disabled, text }">
      <n-button size="tiny" type="primary" :disabled="disabled" data-testid="date-confirm" @click="onConfirm">
        <template #icon><n-icon><CheckIcon /></n-icon></template>{{ text }}
      </n-button>
    </template>
  </n-date-picker>
</template>
