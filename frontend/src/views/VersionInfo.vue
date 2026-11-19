<script setup lang="ts">
/** 版本資訊（管理）：現行版本 + Python / 套件版本，並可檢查 GitHub 最新版。 */
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { NAlert, NCard, NSpace, NIcon, NButton, NSpin, NTag, useMessage } from "naive-ui";
import { SettingsIcon, RefreshIcon, DownloadIcon } from "@/icons";
import { saveBlob } from "@/utils/saveFile";
import {
  getVersionInfo, checkLatestVersion, downloadSbom, type VersionInfo, type LatestVersion,
} from "@/api/system";
import { apiErrMsg } from "@/api/client";

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

// 選用相依：缺了不會壞，但對應的功能會靜默不可用 —— 讓管理員在這裡就看得到，
// 而不是等使用者回報「按了沒反應」。
const optionalTools = computed(() => {
  const ot = info.value?.host?.optional_tools;
  return ot ? Object.entries(ot).map(([name, v]) => ({ name, ...v })) : [];
});
// 備用引擎（aardwolf）沒裝是正常的：不列進警告（有的 Python 版本根本裝不起來）；
// GeoIP 要管理員自己的 MaxMind 帳號（opt_in），沒設定也是正常的
// FreeRDP 引擎沒選用（engine_off）：那組套件本來就不裝，不算缺（客戶 2026-10-08）
const missingTools = computed(() => optionalTools.value.filter((x) => !x.present && !x.fallback && !x.opt_in
                                                                      && !x.engine_off));
// 下載來的資料庫各有自己的頁面（版本、更新、設定），這裡只列狀態並連過去
const toolPages: Record<string, string> = { recog: "recog_admin", oui: "oui_admin", geoip: "system_settings" };
// 必要相依（guacd：RDP／VNC 的預設引擎，必裝）：沒裝或沒在跑都要用紅色講清楚
const requiredTools = computed(() => {
  const rt = info.value?.host?.required_tools;
  return rt ? Object.entries(rt).map(([name, v]) => ({ name, ...v })) : [];
});
const brokenRequired = computed(() => requiredTools.value.filter((x) => !x.running));
/** 「1.6.1-pre.d9ec474 (build 3) for Ubuntu 24.04.4 LTS (amd64)」→ 去掉「for …」：OS 上面已經有了，
 *  整串放在卡片右邊會把名稱欄擠成一行一個字（0.6.49 實機回報） */
function shortVersion(v: string | null | undefined): string {
  return (v || "").replace(/\s+for\s+.*$/, "").trim();
}

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

// SBOM：這個站台實際安裝的套件（給 Dependency-Track 等弱點管理工具匯入）
const sbomBusy = ref(false);
async function getSbom() {
  sbomBusy.value = true;
  try {
    const { filename, blob } = await downloadSbom();
    saveBlob(filename, blob, "application/vnd.cyclonedx+json", { source: "sbom", audited: true });   // 後端下載時已記稽核
  } catch (e) { msg.error(apiErrMsg(e)); }
  finally { sbomBusy.value = false; }
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
        <!-- 授權條款：這是 AGPL 專案，散佈與修改的義務跟著它走 ——
             使用者不該為了知道自己在用什麼授權而跑去翻原始碼。 -->
        <div class="ver-tile">
          <div class="ver-tile__label">{{ t("version.license") }}</div>
          <div class="ver-tile__value ver-tile__value--sm">{{ info?.license ?? "—" }}</div>
          <a class="ver-link" href="https://github.com/jasoncheng7115/jt-ipam/blob/main/LICENSE"
             target="_blank" rel="noopener">{{ t("version.license_link") }}</a>
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

      <!-- 軟體物料清單：實際安裝的 Python 套件＋打包進畫面的 npm 套件 -->
      <div class="ver-sbom" data-testid="ver-sbom">
        <div>
          <div class="ver-pkg-title">{{ t("version.sbom_title") }}</div>
          <div class="ver-pkg-hint">{{ t("version.sbom_hint") }}</div>
        </div>
        <n-button size="small" :loading="sbomBusy" :disabled="sbomBusy" data-testid="ver-sbom-download" @click="getSbom">
          <template #icon><n-icon><DownloadIcon /></n-icon></template>
          {{ t("version.sbom_download") }}
        </n-button>
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

      <!-- 必要相依：缺了或沒在跑，對應功能就不能正常運作 -->
      <template v-if="requiredTools.length">
        <div class="ver-pkg-head">
          <span class="ver-pkg-title">{{ t("version.section_required") }}</span>
          <span class="ver-pkg-hint">{{ t("version.section_required_hint") }}</span>
        </div>
        <n-alert v-if="brokenRequired.length" type="error" :bordered="false" style="margin-bottom:10px">
          {{ t("version.required_missing", { pkgs: brokenRequired.map(x => x.name).join(", ") }) }}
        </n-alert>
        <div class="ver-pkg-grid">
          <!-- 右邊只放短的狀態字；版本放名稱下面自己一行（版本字串很長，放右邊會把名稱擠扁） -->
          <div v-for="p in requiredTools" :key="p.name" class="ver-pkg">
            <span class="ver-pkg__name">
              {{ p.name }}
              <span v-if="p.version" class="ver-req-version">{{ shortVersion(p.version) }}</span>
              <span class="ver-opt-use">{{ p.used_by }}</span>
            </span>
            <span class="ver-pkg__ver" :style="p.running ? 'color:#18a058' : 'color:#d03050'">
              {{ p.running ? t("version.required_running")
                 : p.present ? t("version.required_not_running") : t("version.optional_absent") }}
            </span>
          </div>
        </div>
      </template>

      <!-- 選用相依（缺了只會讓對應功能不可用，不影響服務） -->
      <template v-if="optionalTools.length">
        <div class="ver-pkg-head">
          <span class="ver-pkg-title">{{ t("version.section_optional") }}</span>
          <span class="ver-pkg-hint">{{ t("version.section_optional_hint") }}</span>
        </div>
        <n-alert v-if="missingTools.length" type="warning" :bordered="false" style="margin-bottom:10px">
          {{ t("version.optional_missing", { pkgs: missingTools.map(x => x.package).join(", ") }) }}
        </n-alert>
        <div class="ver-pkg-grid">
          <div v-for="p in optionalTools" :key="p.name" class="ver-pkg">
            <!-- Recog／OUI／GeoIP 的詳細資訊與更新在各自的頁面，這裡只列版本並連過去 -->
            <span class="ver-pkg__name">
              <router-link v-if="toolPages[p.name]" :to="{ name: toolPages[p.name] }" class="ver-link"
                           :data-testid="`version-${p.name}-link`">{{ p.name }}</router-link>
              <template v-else>{{ p.name }}</template>
              <span class="ver-opt-use">{{ p.used_by }}</span>
            </span>
            <span class="ver-pkg__ver" :style="p.present || p.fallback || p.opt_in || p.engine_off ? '' : 'color:#d03050'">
              {{ p.present ? (p.version ? p.version : t("version.optional_present"))
                : (p.engine_off ? t("version.optional_engine_off")
                  : (p.opt_in ? t("version.optional_not_configured") : t("version.optional_absent"))) }}
            </span>
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
.ver-opt-use { display: block; font-size: 11.5px; opacity: .6; margin-top: 2px; }

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
/* 授權識別字比版本號長，而且中間有連字號 —— 不縮字級又不禁止斷行的話會被折成
   「AGPL-3.0-or-」＋「later」兩行，看起來像壞掉。 */
.ver-tile__value--sm { font-size: 17px; letter-spacing: 0; white-space: nowrap; }
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

.ver-sbom {
  display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;
  margin: 20px 0 4px;
}
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
/* min-width:0 + 自己吃掉剩下的寬度：右邊的值再長，名稱欄也不會被擠成一行一個字 */
.ver-pkg__name { font-size: 13.5px; opacity: .85; flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }
.ver-req-version {
  display: block; margin-top: 2px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px;
}
.ver-pkg__ver {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px; font-weight: 600;
  white-space: nowrap; flex: none;   /* 「已安裝」這類狀態字不可折行（使用者回饋） */
}
</style>
