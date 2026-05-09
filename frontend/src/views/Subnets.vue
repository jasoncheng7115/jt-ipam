<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { NCard, NEmpty } from "naive-ui";

const { t } = useI18n();
</script>

<template>
  <n-card :title="t('nav.subnets')">
    <n-empty :description="t('common.no_data')" />
  </n-card>
</template>
