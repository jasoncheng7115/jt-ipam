<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { NSpin, NSpace, useMessage } from "naive-ui";
import { getAddress } from "@/api/addresses";
import IPAddressEditModal from "@/components/IPAddressEditModal.vue";
import type { IPAddress } from "@/types";

const route = useRoute();
const router = useRouter();
const { t } = useI18n();
const msg = useMessage();

const addr = ref<IPAddress | null>(null);
const loading = ref(false);

async function load(id: string) {
  loading.value = true;
  try { addr.value = await getAddress(id); }
  catch { msg.error(t("errors.network")); addr.value = null; }
  finally { loading.value = false; }
}
function onSaved(a: IPAddress) { addr.value = a; }
function onDeleted() { router.push({ name: "addresses" }); }
function back() {
  if (window.history.length > 1) router.back();
  else router.push({ name: "addresses" });
}

onMounted(() => load(String(route.params.id)));
watch(() => route.params.id, (id) => { if (id) load(String(id)); });
</script>

<template>
  <n-spin :show="loading">
    <n-space vertical :size="12">
      <IPAddressEditModal
        v-if="addr"
        inline
        :show="true"
        :address="addr"
        @saved="onSaved"
        @deleted="onDeleted"
        @back="back"
      />
      <n-space v-else-if="!loading" justify="center" style="padding: 40px; opacity: .6">
        {{ t("common.no_data") }}
      </n-space>
    </n-space>
  </n-spin>
</template>
