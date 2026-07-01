<script setup lang="ts">
/** 版本資訊（管理）：現行版本 + Python / 套件版本，並可檢查 GitHub 最新版。 */
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { NCard, NSpace, NIcon, NButton, NSpin, NTag, useMessage } from "naive-ui";
import { SettingsIcon, RefreshIcon } from "@/icons";
import {
  getVersionInfo, checkLatestVersion, type VersionInfo, type LatestVersion,
} from "@/api/system";

const { t } = useI18n();
const msg = useMessage();

const info = ref<VersionInfo | null>(null);
const loading = ref(false);
const latest = ref<LatestVersion | null>(null);
const checking = ref(false);

const pkgRows = computed(() =>
  Object.entries(info.value?.packages ?? {})
    .map(([name, version]) => ({ name, version: version ?? "—" })));

const frontendRows = computed(() =>
  Object.entries(info.value?.frontend ?? {})
    .map(([name, version]) => ({ name, version: version ?? "—" })));

const hostRows = computed(() => {
  const h = info.value?.host;
  if (!h) return [];
  return [
    { name: t("version.host_os"), version: h.os ?? "—" },
    { name: t("version.host_kernel"), version: h.kernel ?? "—" },
    { name: "nginx", version: h.nginx ?? "—" },
    { name: "Node.js", version: h.node ?? "—" },
    { name: "PostgreSQL", version: h.postgres ?? "—" },
  ];
});

async function load() {
  loading.value = true;
  try {
    info.value = await getVersionInfo();
  } catch {
    msg.error(t("errors.network"));
  } finally {
    loading.value = false;
  }
}

async function check() {
  checking.value = true;
  latest.value = null;
  try {
    latest.value = await checkLatestVersion();
    if (latest.value.error) msg.warning(t("version.check_failed"));
  } catch {
    msg.error(t("errors.network"));
  } finally {
    checking.value = false;
  }
}

onMounted(load);
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><SettingsIcon /></n-icon>
        <span>{{ t("version.title") }}</span>
      </n-space>
    </template>

    <n-spin :show="loading">
      <!-- 版本概覽 tiles -->
      <div class="ver-tiles">
        <div class="ver-tile ver-tile--accent">
          <div class="ver-tile__label">{{ t("version.current") }}</div>
          <div class="ver-tile__value">v{{ info?.current ?? "—" }}</div>
        </div>
        <div class="ver-tile">
          <div class="ver-tile__label">Python</div>
          <div class="ver-tile__value">{{ info?.python ?? "—" }}</div>
        </div>
        <div class="ver-tile ver-tile--action">
          <div class="ver-tile__label">{{ t("version.check_latest") }}</div>
          <n-space align="center" :size="10" style="margin-top: 6px">
            <n-button size="small" type="primary" :loading="checking" @click="check">
              <template #icon><n-icon><RefreshIcon /></n-icon></template>
              {{ t("version.check_latest") }}
            </n-button>
            <template v-if="latest && !latest.error">
              <n-tag v-if="latest.update_available" type="warning" size="small" round>
                {{ t("version.update_available", { v: latest.latest }) }}
              </n-tag>
              <n-tag v-else type="success" size="small" round>{{ t("version.up_to_date") }}</n-tag>
              <a :href="latest.release_url" target="_blank" rel="noopener" class="ver-link">{{ t("version.releases") }}</a>
            </template>
            <n-tag v-else-if="latest && latest.error" type="error" size="small" round>
              {{ t("version.check_failed") }}
            </n-tag>
          </n-space>
        </div>
      </div>

      <!-- 本機環境（OS / kernel / nginx / Node.js / PostgreSQL）-->
      <template v-if="hostRows.length">
        <div class="ver-pkg-head">
          <span class="ver-pkg-title">{{ t("version.section_host") }}</span>
          <span class="ver-pkg-hint">{{ t("version.section_host_hint") }}</span>
        </div>
        <div class="ver-pkg-grid">
          <div v-for="p in hostRows" :key="p.name" class="ver-pkg">
            <span class="ver-pkg__name">{{ p.name }}</span>
            <span class="ver-pkg__ver">{{ p.version }}</span>
          </div>
        </div>
      </template>

      <!-- 後端套件 -->
      <div class="ver-pkg-head">
        <span class="ver-pkg-title">{{ t("version.section_backend") }}</span>
        <span class="ver-pkg-hint">{{ t("version.packages_hint") }}</span>
      </div>
      <div class="ver-pkg-grid">
        <div v-for="p in pkgRows" :key="p.name" class="ver-pkg">
          <span class="ver-pkg__name">{{ p.name }}</span>
          <span class="ver-pkg__ver">{{ p.version }}</span>
        </div>
      </div>

      <!-- 前端框架 -->
      <template v-if="frontendRows.length">
        <div class="ver-pkg-head">
          <span class="ver-pkg-title">{{ t("version.section_frontend") }}</span>
        </div>
        <div class="ver-pkg-grid">
          <div v-for="p in frontendRows" :key="p.name" class="ver-pkg">
            <span class="ver-pkg__name">{{ p.name }}</span>
            <span class="ver-pkg__ver">{{ p.version }}</span>
          </div>
        </div>
      </template>
    </n-spin>
  </n-card>
</template>

<style scoped>
.ver-tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 14px;
  margin-bottom: 22px;
}
.ver-tile {
  border: 1px solid var(--n-border-color, rgba(128,128,128,.2));
  border-radius: 12px;
  padding: 16px 18px;
  background: rgba(128, 128, 128, 0.04);
}
.ver-tile--accent {
  background: linear-gradient(135deg, rgba(24,160,88,.14), rgba(20,184,166,.10));
  border-color: rgba(24,160,88,.35);
}
/* 「檢查 GitHub 最新版」與「現行版本 / Python」同一排（第三格），不再獨佔整列 */
.ver-tile__label {
  font-size: 12.5px; opacity: .7; letter-spacing: .3px; margin-bottom: 4px;
}
.ver-tile__value {
  font-size: 24px; font-weight: 700; font-variant-numeric: tabular-nums;
}
.ver-link { font-size: 13px; }

.ver-pkg-head {
  display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
  margin: 20px 0 12px;
}
.ver-pkg-title { font-weight: 600; font-size: 15px; }
.ver-pkg-hint { font-size: 12.5px; opacity: .6; }
.ver-pkg-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 8px 16px;
}
.ver-pkg {
  display: flex; align-items: center; justify-content: space-between;
  gap: 10px; padding: 8px 12px;
  border: 1px solid var(--n-border-color, rgba(128,128,128,.16));
  border-radius: 9px;
}
.ver-pkg__name { font-size: 13.5px; opacity: .85; }
.ver-pkg__ver {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px; font-weight: 600;
}
</style>
