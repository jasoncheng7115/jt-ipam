<script setup lang="ts">
// 裝置詳細資料的「裝置群組」卡片（2026-11-05）：同組的其他成員、角色與序號、共用 IP。
// 本機不在群組裡就不顯示。卡片標題只放標題＋數量，控制放內文最上方。
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { NButton, NCard, NIcon, NTable, NTag } from "naive-ui";
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
const members = computed(() => [...(detail.value?.members ?? [])].sort((a, b) => (a.order ?? 999) - (b.order ?? 999)));
function goGroup() { router.push({ name: "devices", query: { tab: "groups", group: props.group.id } }); }
function goDevice(id: string) { router.push({ name: "device-detail", params: { id } }); }
</script>

<template>
  <n-card size="small" data-testid="device-group-card">
    <template #header>
      <CardTitle :icon="GroupIcon" :text="`${t('device_groups.title')} (${group.member_count})`" />
    </template>
    <div class="dgc-head">
      <span class="dgc-name">{{ group.name }}</span>
      <n-tag size="small" type="info" :bordered="false">{{ t(`device_groups.kind_${group.kind}`) }}</n-tag>
      <span class="dgc-spacer" />
      <n-button size="small" @click="goGroup">
        <template #icon><n-icon><GroupIcon /></n-icon></template>
        {{ t("device_groups.manage") }}
      </n-button>
    </div>
    <!-- 對齊的三欄（使用者 2026-11-07：「裝置群組卡片內排列的不好看」——以前每列的欄位跟著名稱長短飄） -->
    <n-table v-if="detail" size="small" :bordered="false" :single-line="false" class="dgc-table" data-testid="device-group-members">
      <thead>
        <tr>
          <th>{{ t("device_groups.members") }}</th>
          <th class="dgc-col-role">{{ t("device_groups.role") }}</th>
          <th>{{ t("device_groups.col_rack") }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="m in members" :key="m.device_id" :class="{ 'is-self': m.device_id === deviceId }">
          <td>
            <span class="dgc-member">
              <a v-if="m.device_id !== deviceId" href="#" class="dgc-link" @click.prevent="goDevice(m.device_id)">{{ m.name }}</a>
              <b v-else>{{ m.name }}</b>
              <n-tag v-if="m.device_id === deviceId" size="tiny" :bordered="false">{{ t("device_groups.this_device") }}</n-tag>
              <n-tag v-if="m.device_id === detail.representative_id" size="tiny" type="success" :bordered="false">
                {{ t("device_groups.representative") }}
              </n-tag>
            </span>
          </td>
          <td class="dgc-col-role">{{ roleMeta(m.role, m.order) || "—" }}</td>
          <td :class="{ 'dgc-dim': !m.rack_name }">
            {{ m.rack_name ? (m.u_position ? `${m.rack_name} U${m.u_position}` : m.rack_name) : t("device_groups.not_in_rack") }}
          </td>
        </tr>
      </tbody>
    </n-table>
    <div v-if="detail && detail.shared_ips.length" class="dgc-ips" data-testid="device-group-shared-ips">
      <span class="dgc-label">{{ t("device_groups.shared_ips") }}</span>
      <n-tag v-for="ip in detail.shared_ips" :key="ip.id" size="small" :bordered="false" class="dgc-ip"
             @click="router.push({ name: 'address-detail', params: { id: ip.id } })">{{ ip.ip }}</n-tag>
    </div>
  </n-card>
</template>

<style scoped>
.dgc-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.dgc-name { font-weight: 600; font-size: 15px; }
.dgc-spacer { flex: 1; }
.dgc-table th { font-size: 12px; font-weight: 500; opacity: 0.7; }
.dgc-col-role { width: 120px; white-space: nowrap; }
.dgc-member { display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.dgc-table tr.is-self td { background: var(--jt-tabs-active, rgba(24, 160, 88, 0.08)); }
.dgc-dim { opacity: 0.55; }
/* 跟全站的關聯連結同一個樣子（useEntityLinks）：主色、不加底線 */
.dgc-link { color: var(--primary-color, #18a058); text-decoration: none; cursor: pointer; }
.dgc-ips { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 10px; }
.dgc-label { font-size: 12px; opacity: 0.7; margin-right: 4px; }
.dgc-ip { cursor: pointer; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--primary-color, #18a058); }
</style>
