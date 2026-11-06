<script setup lang="ts">
// 裝置詳細資料的「裝置群組」卡片（2026-11-05）：同組的其他成員、角色與序號、共用 IP。
// 本機不在群組裡就不顯示。卡片標題只放標題＋數量，控制放內文最上方。
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { NButton, NCard, NIcon, NSpace, NTag } from "naive-ui";
import { getDeviceGroup, type DeviceGroup } from "@/api/deviceGroups";
import type { DeviceGroupSummary } from "@/api/basic";
import CardTitle from "@/components/CardTitle.vue";
import { GroupIcon } from "@/icons";

const props = defineProps<{ deviceId: string; group: DeviceGroupSummary }>();
const { t } = useI18n();
const router = useRouter();
const detail = ref<DeviceGroup | null>(null);

watch(() => props.group.id, async (id) => {
  detail.value = null;
  try { detail.value = await getDeviceGroup(id); } catch { /* 卡片退回只顯示摘要 */ }
}, { immediate: true });

const roleMeta = (role: string | null, order: number | null) =>
  [role ? t(`device_groups.role_${role}`) : "", order ? `#${order}` : ""].filter(Boolean).join(" ");
function goGroup() { router.push({ name: "devices", query: { tab: "groups", group: props.group.id } }); }
function goDevice(id: string) { router.push({ name: "device-detail", params: { id } }); }
</script>

<template>
  <n-card size="small" data-testid="device-group-card">
    <template #header>
      <CardTitle :icon="GroupIcon" :text="`${t('device_groups.title')} (${group.member_count})`" />
    </template>
    <n-space align="center" justify="space-between" style="margin-bottom: 8px">
      <span>
        <b>{{ group.name }}</b>
        <n-tag size="small" type="info" :bordered="false" style="margin-left: 8px">
          {{ t(`device_groups.kind_${group.kind}`) }}
        </n-tag>
      </span>
      <n-button size="small" @click="goGroup">
        <template #icon><n-icon><GroupIcon /></n-icon></template>
        {{ t("device_groups.manage") }}
      </n-button>
    </n-space>
    <div v-if="detail" class="dgc-list">
      <div v-for="m in [...detail.members].sort((a, b) => (a.order ?? 999) - (b.order ?? 999))" :key="m.device_id"
           class="dgc-row" :class="{ 'is-self': m.device_id === deviceId }">
        <a v-if="m.device_id !== deviceId" href="#" class="dgc-link" @click.prevent="goDevice(m.device_id)">{{ m.name }}</a>
        <b v-else>{{ m.name }}</b>
        <n-tag v-if="m.device_id === deviceId" size="tiny" :bordered="false">{{ t("device_groups.this_device") }}</n-tag>
        <span v-if="roleMeta(m.role, m.order)" class="dgc-meta">{{ roleMeta(m.role, m.order) }}</span>
        <n-tag v-if="m.device_id === detail.representative_id" size="tiny" type="success" :bordered="false">
          {{ t("device_groups.representative") }}
        </n-tag>
        <span class="dgc-meta">{{ m.rack_name ? (m.u_position ? `${m.rack_name} U${m.u_position}` : m.rack_name) : t("device_groups.not_in_rack") }}</span>
      </div>
      <div v-if="detail.shared_ips.length" class="dgc-ips">
        <span class="dgc-meta">{{ t("device_groups.shared_ips") }}</span>
        <a v-for="ip in detail.shared_ips" :key="ip.id" href="#" class="dgc-link"
           @click.prevent="router.push({ name: 'address-detail', params: { id: ip.id } })">{{ ip.ip }}</a>
      </div>
    </div>
  </n-card>
</template>

<style scoped>
.dgc-list { display: grid; gap: 4px; }
.dgc-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 2px 0; }
.dgc-meta { font-size: 12px; opacity: 0.65; }
/* 跟全站的關聯連結同一個樣子（useEntityLinks）：主色、不加底線 */
.dgc-link { color: var(--primary-color, #18a058); text-decoration: none; cursor: pointer; }
.dgc-ips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; align-items: center; }
</style>
