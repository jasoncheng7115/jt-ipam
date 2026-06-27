<script setup lang="ts">
import { computed, ref, watch, onMounted, onBeforeUnmount, nextTick, h } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { useFloatingHScroll } from "@/composables/useFloatingHScroll";
import { useVersionCheck } from "@/composables/useVersionCheck";
import {
  NLayout,
  NLayoutHeader,
  NLayoutSider,
  NLayoutContent,
  NMenu,
  NSpace,
  NDropdown,
  NIcon,
  NTooltip,
  type MenuOption,
} from "naive-ui";
import { storeToRefs } from "pinia";
import { useUiStore } from "@/stores/ui";
import { useAuthStore } from "@/stores/auth";
import { listSubnets } from "@/api/subnets";
import { useCustomers } from "@/composables/useCustomers";
import { useSubnetTree } from "@/composables/useSubnetTree";
import type { Subnet } from "@/types";
import NotificationBell from "@/components/NotificationBell.vue";
import GlobalSearch from "@/components/GlobalSearch.vue";
import ChatWidget from "@/components/ChatWidget.vue";
import {
  // 主導覽
  DashboardIcon, SectionsIcon, SubnetsIcon, AddressesIcon, IPChangesIcon, VlansIcon, VrfsIcon,
  NatIcon, DevicesIcon, RacksIcon, LocationsIcon, RequestsIcon, TopologyIcon,
  ToolsIcon, SettingsIcon, TasksIcon,
  // Phase 3 / Admin
  Phase3Icon, VirtualizationIcon, PhysicalIcon, PowerIcon, VpnIcon,
  AdminIcon, AuditIcon, UsersIcon, GroupsIcon, CustomFieldsIcon, CustomersIcon, AnomalyIcon, ChatHistoryIcon,
  DnsIcon, LibreNMSIcon, FirewallIcon, WazuhIcon, ScanAgentsIcon, WebhooksIcon, LockIcon,
  MigrationIcon, ImportIcon, PluginsIcon, ExportIcon, TerminalIcon,
  // topbar / user menu
  LogoutIcon, AccountIcon, LanguageIcon, ThemeDarkIcon, ThemeLightIcon,
  renderIcon,
} from "@/icons";
import { User as UserOutline } from "@iconoir/vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const appVersion = __APP_VERSION__;
const ui = useUiStore();

// 全站懸浮水平捲軸：任何頁面的寬表格只要原生捲軸落在畫面外，視窗底部就會出現一條
useFloatingHScroll();
// 偵測已部署新版 → 提示重新整理（解長壽分頁跑舊 bundle）
useVersionCheck();
const auth = useAuthStore();
const { theme, locale } = storeToRefs(ui);
const { me } = storeToRefs(auth);
// 右上以「帳號@領域」呈現：本機帳號補 @local；外部帳號的 username 已是 jason@ldap 形式
const accountLabel = computed(() => {
  const u = me.value?.username || "";
  return u.includes("@") ? u : `${u}@local`;
});

// ── 子網路導覽 tree（在子網路詳情頁時，左側選單把子網路展開、依客戶分組）──
const { labelFor: customerLabelFor, ensureLoaded: ensureCustomersLoaded } = useCustomers();
const navSubnets = ref<Subnet[]>([]);
let navSubnetsLoaded = false;
async function loadNavSubnets(force = false) {
  if (navSubnetsLoaded && !force) return;
  navSubnetsLoaded = true;
  void ensureCustomersLoaded();
  try {
    const res = await listSubnets({ page: 1, pageSize: 500 });
    navSubnets.value = res.items;
  } catch { navSubnetsLoaded = false; }
}
// 子網路新增/編輯/刪除後 → 強制重載左選單子網路樹
const { version: subnetTreeVersion } = useSubnetTree();
watch(subnetTreeVersion, () => { if (inSubnetContext.value) void loadNavSubnets(true); });

const currentSubnetId = computed(() =>
  route.name === "subnet-detail" ? (route.params.id as string) : null,
);
// 在「子網路」清單頁或某個子網路詳情頁時，左選單就展開子網路樹
const inSubnetContext = computed(() =>
  route.name === "subnets" || route.name === "subnet-detail",
);

// 在同一個單位群組內，依 master_subnet_id 建出「真正的巢狀選單」：
// 有下層的子網段 → n-submenu（可展開、由 n-menu 畫出虛線連接），點標題本身會進入該網段；
// 無下層的 → 一般 leaf。
function buildSubnetMenu(items: Subnet[]): MenuOption[] {
  const ids = new Set(items.map((x) => x.id));
  const childrenBy = new Map<string, Subnet[]>();
  const roots: Subnet[] = [];
  for (const s of items) {
    const pid = (s as any).master_subnet_id as string | null | undefined;
    if (pid && ids.has(pid)) {
      (childrenBy.get(pid) ?? childrenBy.set(pid, []).get(pid)!).push(s);
    } else {
      roots.push(s);
    }
  }
  const cmp = (a: Subnet, b: Subnet) => a.cidr.localeCompare(b.cidr, undefined, { numeric: true });
  const mk = (s: Subnet): MenuOption => {
    const text = s.description ? `${s.cidr} (${s.description})` : s.cidr;
    const kids = (childrenBy.get(s.id) ?? []).slice().sort(cmp);
    if (kids.length) {
      // 有下層 → 可展開節點；標題做成可點連結（點文字進入該網段、點箭頭展開）
      return {
        key: `subnet:${s.id}`,
        label: () => h("a", {
          class: "subnet-node-link",
          onClick: (e: MouseEvent) => {
            e.stopPropagation();
            router.push({ name: "subnet-detail", params: { id: s.id } }).catch(() => {});
          },
        }, text),
        children: kids.map(mk),
      };
    }
    return { key: `subnet:${s.id}`, label: text };
  };
  return roots.slice().sort(cmp).map(mk);
}

const subnetTreeChildren = computed<MenuOption[] | undefined>(() => {
  if (!inSubnetContext.value || !navSubnets.value.length) return undefined;
  const groups = new Map<string, { label: string; items: Subnet[] }>();
  for (const s of navSubnets.value) {
    const cid = s.customer_id || "__none__";
    if (!groups.has(cid)) {
      groups.set(cid, {
        label: s.customer_id
          ? (s.customer_name || customerLabelFor(s.customer_id))
          : t("nav.subnet_no_customer"),
        items: [],
      });
    }
    groups.get(cid)!.items.push(s);
  }
  const groupOpts: MenuOption[] = [...groups.entries()]
    .sort((a, b) => a[1].label.localeCompare(b[1].label))
    .map(([cid, g]) => ({
      key: `subnetgrp:${cid}`,
      label: g.label,
      icon: renderIcon(CustomersIcon),
      // 依 master_subnet_id 建巢狀選單：有下層者成為可展開節點（n-menu 畫虛線連接）
      children: buildSubnetMenu(g.items),
    }));
  return [
    { key: "subnets-all", label: () => t("nav.subnet_all"), icon: renderIcon(SubnetsIcon) },
    ...groupOpts,
  ];
});

const menuValue = computed(() =>
  currentSubnetId.value ? `subnet:${currentSubnetId.value}`
    : route.name === "subnets" ? "subnets-all"
    : (route.name as string),
);

const expandedKeys = ref<string[]>([]);
watch(inSubnetContext, (v) => { if (v) void loadNavSubnets(); }, { immediate: true });
watch([inSubnetContext, currentSubnetId, navSubnets], () => {
  if (!inSubnetContext.value) return;
  const keys = new Set(expandedKeys.value);
  keys.add("subnets");
  const id = currentSubnetId.value;
  const s = id ? navSubnets.value.find((x) => x.id === id) : null;
  if (s) keys.add(`subnetgrp:${s.customer_id || "__none__"}`);
  expandedKeys.value = [...keys];
});

const menuOptions = computed<MenuOption[]>(() => {
  const base: MenuOption[] = [
    { label: () => t("nav.dashboard"),   key: "dashboard",  icon: renderIcon(DashboardIcon) },
    { label: () => t("nav.sections"),    key: "sections",   icon: renderIcon(SectionsIcon) },
    { label: () => t("nav.subnets"),     key: "subnets",    icon: renderIcon(SubnetsIcon), children: subnetTreeChildren.value },
    { label: () => t("nav.addresses"),   key: "addresses",  icon: renderIcon(AddressesIcon) },
    { label: () => t("nav.ip_changes"),  key: "ip_changes", icon: renderIcon(IPChangesIcon) },
    { label: () => t("nav.vlans"),       key: "vlans",      icon: renderIcon(VlansIcon) },
    { label: () => t("nav.vrfs"),        key: "vrfs",       icon: renderIcon(VrfsIcon) },
    { label: () => t("nav.devices"),     key: "devices",    icon: renderIcon(DevicesIcon) },
    { label: () => t("nav.racks"),       key: "racks",      icon: renderIcon(RacksIcon) },
    { label: () => t("nav.locations"),   key: "locations",  icon: renderIcon(LocationsIcon) },
    ...(me.value?.is_admin
      ? [{ label: () => t("nav.customers"), key: "customers", icon: renderIcon(CustomersIcon) }]
      : []),
    { label: () => t("nav.requests"),    key: "requests",   icon: renderIcon(RequestsIcon) },
    { label: () => t("nav.topology"),    key: "topology",   icon: renderIcon(TopologyIcon) },
    {
      label: () => t("nav.phase3_section"),
      key: "phase3",
      icon: renderIcon(Phase3Icon),
      children: [
        { label: () => t("advanced.tenancy"),   key: "adv-tenancy",  icon: renderIcon(CustomersIcon) },
        { label: () => "ASN",                    key: "adv-asn",      icon: renderIcon(VlansIcon) },
        { label: () => t("advanced.circuits"),   key: "adv-circuits", icon: renderIcon(PhysicalIcon) },
        { label: () => t("advanced.contacts"),   key: "adv-contacts", icon: renderIcon(UsersIcon) },
        { label: () => t("advanced.wireless"),   key: "adv-wireless", icon: renderIcon(ScanAgentsIcon) },
        { label: () => t("nav.dns_records"),     key: "adv-dns-records", icon: renderIcon(DnsIcon) },
        { label: () => t("nav.cert_status"),     key: "adv-cert-status", icon: renderIcon(LockIcon) },
        { label: () => t("nav.connections"),     key: "adv-connections", icon: renderIcon(TerminalIcon) },
        { label: () => t("nav.virtualization"), key: "virt",     icon: renderIcon(VirtualizationIcon) },
        { label: () => t("nav.firewall"),       key: "firewall",    icon: renderIcon(FirewallIcon) },
        { label: () => t("nav.pfsense_fw"),     key: "pfsense_fw",  icon: renderIcon(FirewallIcon) },
        { label: () => t("nav.nat"),            key: "nat",         icon: renderIcon(NatIcon) },
        { label: () => t("nav.cabling"),        key: "cabling",     icon: renderIcon(PhysicalIcon) },
        { label: () => t("nav.power"),          key: "power",       icon: renderIcon(PowerIcon) },
        { label: () => t("nav.vpn_tunnels"),    key: "vpn-tunnels", icon: renderIcon(VpnIcon) },
      ],
    },
    { label: () => t("nav.tools"),       key: "tools",      icon: renderIcon(ToolsIcon) },
    { label: () => t("nav.tasks"),       key: "tasks",      icon: renderIcon(TasksIcon) },
  ];
  if (me.value?.is_admin) {
    base.push(
      { type: "divider", key: "d-admin" },
      {
        label: () => t("nav.admin_section"),
        key: "admin",
        icon: renderIcon(AdminIcon),
        children: [
          { label: () => t("nav.audit"),         key: "audit",          icon: renderIcon(AuditIcon) },
          { label: () => t("nav.users"),         key: "users",          icon: renderIcon(UsersIcon) },
          { label: () => t("nav.groups"),        key: "groups",         icon: renderIcon(GroupsIcon) },
          { label: () => t("nav.permissions"),   key: "permissions",    icon: renderIcon(AdminIcon) },
          { label: () => t("nav.custom_fields"), key: "custom_fields",  icon: renderIcon(CustomFieldsIcon) },
          { label: () => t("nav.oui_admin"),     key: "oui_admin",      icon: renderIcon(DevicesIcon) },
          { label: () => t("nav.hostname_precedence"), key: "hostname_precedence", icon: renderIcon(AddressesIcon) },
          { label: () => t("nav.anomaly"),       key: "anomaly",        icon: renderIcon(AnomalyIcon) },
          { label: () => t("nav.dns"),           key: "dns",            icon: renderIcon(DnsIcon) },
          { label: () => t("nav.adguard"),       key: "adguard",        icon: renderIcon(DnsIcon) },
          { label: () => t("nav.librenms"),      key: "librenms",       icon: renderIcon(LibreNMSIcon) },
          { label: () => t("nav.firewall_admin"), key: "firewall_admin", icon: renderIcon(FirewallIcon) },
          { label: () => t("nav.pfsense"),        key: "pfsense",        icon: renderIcon(FirewallIcon) },
          { label: () => t("nav.virt_admin"),    key: "virt_admin",     icon: renderIcon(VirtualizationIcon) },
          { label: () => t("nav.wazuh"),         key: "wazuh",          icon: renderIcon(WazuhIcon) },
          { label: () => t("nav.graylog_dsv"),   key: "graylog_dsv",    icon: renderIcon(ExportIcon) },
          { label: () => t("nav.scan_agents"),   key: "scan_agents",    icon: renderIcon(ScanAgentsIcon) },
          { label: () => t("nav.certificates"),  key: "certificates",   icon: renderIcon(LockIcon) },
          { label: () => t("nav.webhooks"),      key: "webhooks",       icon: renderIcon(WebhooksIcon) },
          { label: () => t("nav.migration"),     key: "migration",      icon: renderIcon(MigrationIcon) },
          { label: () => t("nav.import"),        key: "import",         icon: renderIcon(ImportIcon) },
          { label: () => t("nav.plugins"),       key: "plugins",        icon: renderIcon(PluginsIcon) },
          { label: () => "LLM / AI",             key: "llm_settings",   icon: renderIcon(SettingsIcon) },
          { label: () => t("nav.system_settings"), key: "system_settings", icon: renderIcon(SettingsIcon) },
          { label: () => t("nav.notification_channels"), key: "notification_channels", icon: renderIcon(SettingsIcon) },
          { label: () => t("nav.ip_request_policy"), key: "ip_request_policy", icon: renderIcon(RequestsIcon) },
          { label: () => t("nav.version"),       key: "version",        icon: renderIcon(AdminIcon) },
          { label: () => t("nav.system_logs"),   key: "system_logs",    icon: renderIcon(AuditIcon) },
          { label: () => t("nav.chat_history"),  key: "chat_history",   icon: renderIcon(ChatHistoryIcon) },
        ],
      },
    );
  }
  // 零權限非管理員：後端對 nat/firewall/dns/librenms/virt/實體 等已 403，
  // 其餘資料頁也只會是空的 → 選單只留儀表板/工具/作業，避免點了就出錯。
  if (!me.value?.is_admin && me.value?.has_visibility === false) {
    const allowed = new Set(["dashboard", "tools"]);
    return base.filter((o) => allowed.has(o.key as string));
  }
  // 非管理員且無「全域讀取」（只被指派特定物件）→ 隱藏全域基礎設施選單，
  // 後端對這些端點也會 403（VLAN/VRF/NAT/防火牆/DNS/虛擬化/站對站 VPN…）。
  if (!me.value?.is_admin && me.value?.has_global_read === false) {
    const hide = new Set(["vlans", "vrfs", "nat", "phase3"]);
    return base.filter((o) => !hide.has(o.key as string));
  }
  return base;
});

const localeOptions = [
  { label: "繁體中文", value: "zh-TW" },
  { label: "English",  value: "en-US" },
];

const themeOptions = computed(() => [
  { label: t("topbar.theme.light"), value: "light" },
  { label: t("topbar.theme.dark"),  value: "dark" },
  { label: t("topbar.theme.auto"),  value: "auto" },
]);

// 窄螢幕時語言 / 佈景改用 icon 觸發的下拉（n-dropdown 用 key）
const localeMenuOptions = computed(() => localeOptions.map((o) => ({ label: o.label, key: o.value })));
const themeMenuOptions = computed(() => themeOptions.value.map((o) => ({ label: o.label, key: o.value })));
const currentLocaleLabel = computed(() => localeOptions.find((o) => o.value === locale.value)?.label ?? "");
const currentThemeLabel = computed(() => themeOptions.value.find((o) => o.value === theme.value)?.label ?? "");
const currentThemeIcon = computed(() => (theme.value === "light" ? ThemeLightIcon : ThemeDarkIcon));
// n-dropdown @select 會帶 (key, option)，需包一層只取 key（避免把 option 當成 setLocale 的第二參數）
function pickLocale(k: string | number) { ui.setLocale(String(k) as "zh-TW" | "en-US"); }
function pickTheme(k: string | number) { ui.setTheme(String(k) as "light" | "dark" | "auto"); }

const userMenuOptions = computed(() => [
  { label: t("topbar.user_menu.profile"),     key: "profile",     icon: renderIcon(UserOutline, 16) },
  { label: t("topbar.user_menu.preferences"), key: "preferences", icon: renderIcon(SettingsIcon, 16) },
  { label: t("topbar.user_menu.my_chat_history"), key: "my_chat_history", icon: renderIcon(ChatHistoryIcon, 16) },
  { type: "divider" as const, key: "d" },
  { label: t("topbar.user_menu.logout"),      key: "logout",      icon: renderIcon(LogoutIcon, 16) },
]);

function handleMenu(key: string) {
  if (key === "subnets-all" || key === "subnets") {
    router.push({ name: "subnets" }).catch(() => {});
    return;
  }
  if (key.startsWith("subnet:")) {
    router.push({ name: "subnet-detail", params: { id: key.slice(7) } }).catch(() => {});
    return;
  }
  if (key.startsWith("subnetgrp:")) return; // 群組節點只負責展開/收合
  router.push({ name: key }).catch(() => {});
}

async function handleUserMenu(key: string) {
  if (key === "logout") {
    await auth.logout();
    router.push({ name: "login" });
  } else if (key === "preferences" || key === "profile") {
    router.push({ name: "settings" });
  } else if (key === "my_chat_history") {
    router.push({ name: "my_chat_history" });
  }
}


const siderCollapsed = ref(false);

// 視窗太窄時自動收折左側選單；變寬再自動展開（區間內仍可手動切換）。
const NARROW_PX = 920;
function readWidth() { return typeof window !== "undefined" ? window.innerWidth : 1920; }
const winW = ref(readWidth());
function onResize() { winW.value = readWidth(); }
watch(winW, (w, prev) => {
  if (w < NARROW_PX && prev >= NARROW_PX) siderCollapsed.value = true;
  else if (w >= NARROW_PX && prev < NARROW_PX) siderCollapsed.value = false;
});
// 選單往上捲時，在固定的 logo 欄下方加陰影，與捲動內容分隔
const menuScrolled = ref(false);
let siderScrollEl: HTMLElement | null = null;
function onSiderScroll() {
  if (siderScrollEl) menuScrolled.value = siderScrollEl.scrollTop > 2;
}
onMounted(() => {
  window.addEventListener("resize", onResize);
  if (winW.value < NARROW_PX) siderCollapsed.value = true;
  void nextTick(() => {
    siderScrollEl = document.querySelector(".app-sider .n-layout-sider-scroll-container");
    if (siderScrollEl) {
      siderScrollEl.addEventListener("scroll", onSiderScroll, { passive: true });
      onSiderScroll();
    }
  });
});
onBeforeUnmount(() => {
  window.removeEventListener("resize", onResize);
  if (siderScrollEl) siderScrollEl.removeEventListener("scroll", onSiderScroll);
});

// ── 左側選單可拖動改變寬度 ──
const SIDER_MIN = 180;
const SIDER_MAX = 480;
const siderWidth = ref(
  Math.min(SIDER_MAX, Math.max(SIDER_MIN,
    Number(localStorage.getItem("jt-ipam:sider_width")) || 240)),
);
let dragging = false;
function onDrag(e: MouseEvent) {
  if (!dragging) return;
  siderWidth.value = Math.min(SIDER_MAX, Math.max(SIDER_MIN, e.clientX));
}
function stopDrag() {
  if (!dragging) return;
  dragging = false;
  document.removeEventListener("mousemove", onDrag);
  document.removeEventListener("mouseup", stopDrag);
  document.body.style.userSelect = "";
  document.body.style.cursor = "";
  localStorage.setItem("jt-ipam:sider_width", String(siderWidth.value));
}
function startDrag(e: MouseEvent) {
  if (siderCollapsed.value) return;
  dragging = true;
  e.preventDefault();
  document.addEventListener("mousemove", onDrag);
  document.addEventListener("mouseup", stopDrag);
  document.body.style.userSelect = "none";
  document.body.style.cursor = "col-resize";
}
</script>

<template>
  <n-layout has-sider style="height: 100vh">
    <n-layout-sider
      class="app-sider"
      bordered
      collapse-mode="width"
      :collapsed-width="64"
      :width="siderWidth"
      show-trigger
      :collapsed="siderCollapsed"
      @update:collapsed="(v) => { siderCollapsed = v; }"
    >
      <div v-if="!siderCollapsed" class="sider-resizer" @mousedown="startDrag"></div>
      <div class="brand" :class="{ 'brand-collapsed': siderCollapsed, 'brand-scrolled': menuScrolled }">
        <!-- 收折：只顯示方塊 icon；展開：方塊 + jt-ipam wordmark(currentColor 跟主題色) -->
        <svg v-if="siderCollapsed"
             xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" class="brand-logo" aria-label="jt-ipam">
          <rect width="48" height="48" rx="10" fill="#18a058" />
          <g stroke="#ffffff" stroke-width="2.2" stroke-linecap="round" stroke-opacity="0.9">
            <line x1="13" y1="13" x2="24" y2="24" />
            <line x1="35" y1="13" x2="24" y2="24" />
            <line x1="13" y1="35" x2="24" y2="24" />
            <line x1="35" y1="35" x2="24" y2="24" />
          </g>
          <g fill="#ffffff">
            <circle cx="13" cy="13" r="3.8" />
            <circle cx="35" cy="13" r="3.8" />
            <circle cx="13" cy="35" r="3.8" />
            <circle cx="35" cy="35" r="3.8" />
          </g>
          <circle cx="24" cy="24" r="6" fill="#ffffff" />
          <circle cx="24" cy="24" r="2.6" fill="#18a058" />
        </svg>
        <svg v-else
             xmlns="http://www.w3.org/2000/svg" viewBox="0 0 232 48" class="brand-logo" aria-label="jt-ipam">
          <rect width="48" height="48" rx="10" fill="#18a058" />
          <g stroke="#ffffff" stroke-width="2.2" stroke-linecap="round" stroke-opacity="0.9">
            <line x1="13" y1="13" x2="24" y2="24" />
            <line x1="35" y1="13" x2="24" y2="24" />
            <line x1="13" y1="35" x2="24" y2="24" />
            <line x1="35" y1="35" x2="24" y2="24" />
          </g>
          <g fill="#ffffff">
            <circle cx="13" cy="13" r="3.8" />
            <circle cx="35" cy="13" r="3.8" />
            <circle cx="13" cy="35" r="3.8" />
            <circle cx="35" cy="35" r="3.8" />
          </g>
          <circle cx="24" cy="24" r="6" fill="#ffffff" />
          <circle cx="24" cy="24" r="2.6" fill="#18a058" />
          <text x="60" y="32"
                font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
                font-size="22" font-weight="600" fill="currentColor"
                letter-spacing="-0.3">jt-ipam</text>
          <text x="150" y="33"
                font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
                font-size="16" font-weight="500" fill="currentColor" fill-opacity="0.72"
                letter-spacing="0">v{{ appVersion }}</text>
        </svg>
      </div>
      <n-menu
        :options="menuOptions"
        :value="menuValue"
        :expanded-keys="expandedKeys"
        :collapsed="siderCollapsed"
        :collapsed-width="64"
        :collapsed-icon-size="22"
        :indent="12"
        @update:value="handleMenu"
        @update:expanded-keys="(v: string[]) => expandedKeys = v"
      />
    </n-layout-sider>
    <n-layout>
      <n-layout-header bordered class="topbar">
        <n-space align="center" justify="space-between" :wrap="false" style="width: 100%; min-width: 0">
          <global-search v-if="me" />
          <span v-else />
          <n-space class="topbar-ctls" align="center" :size="4" :wrap="false">
            <!-- 語言：寬螢幕顯示名稱，窄螢幕只剩 icon -->
            <n-dropdown :options="localeMenuOptions" trigger="click" @select="pickLocale">
              <button type="button" class="topbar-ctl">
                <n-icon :size="17" :component="LanguageIcon" />
                <span class="topbar-ctl__label">{{ currentLocaleLabel }}</span>
              </button>
            </n-dropdown>
            <!-- 佈景：寬螢幕顯示名稱，窄螢幕只剩 icon -->
            <n-dropdown :options="themeMenuOptions" trigger="click" @select="pickTheme">
              <button type="button" class="topbar-ctl">
                <n-icon :size="17" :component="currentThemeIcon" />
                <span class="topbar-ctl__label">{{ currentThemeLabel }}</span>
              </button>
            </n-dropdown>
            <span class="topbar-divider" />
            <notification-bell v-if="me" />
            <span class="topbar-divider" />
            <n-dropdown
              v-if="me"
              :options="userMenuOptions"
              trigger="click"
              @select="handleUserMenu"
            >
              <button type="button" class="topbar-ctl">
                <n-icon :size="17" :component="AccountIcon" />
                <span class="topbar-ctl__label">{{ accountLabel }}</span>
                <n-tooltip v-if="me.is_admin" :delay="0">
                  <template #trigger>
                    <n-icon :size="15" :component="AdminIcon" style="color: #18a058" />
                  </template>
                  {{ t("nav.system_admin") }}
                </n-tooltip>
              </button>
            </n-dropdown>
          </n-space>
        </n-space>
      </n-layout-header>
      <n-layout-content content-style="padding: 16px;">
        <router-view />
      </n-layout-content>
    </n-layout>
    <chat-widget v-if="me" />
  </n-layout>
</template>

<style scoped>
.brand {
  padding: 14px 16px;
  display: flex;
  align-items: center;
  /* logo + 系統名 + 版本固定在頂端，選單捲動時仍可見（用 naive 的 sider 底色避免穿透）*/
  position: sticky;
  top: 0;
  z-index: 3;
  background: var(--n-color, #fff);
  transition: box-shadow 0.18s ease;
}
/* 選單往上捲到 logo 欄下方時，加陰影做出層次分隔 */
.brand-scrolled {
  box-shadow: 0 6px 12px -6px rgba(0, 0, 0, 0.28);
}
.brand-collapsed {
  padding: 14px 0;
  justify-content: center;
}
.brand-logo {
  height: 32px;
  width: auto;
  display: block;
}
.topbar {
  padding: 8px 16px;
  /* 頂端列固定，內容捲動時保持可見 */
  position: sticky;
  top: 0;
  z-index: 10;
}
/* 頂列控制鈕：icon + 文字標籤；窄螢幕由全域 media query 隱藏 .topbar-ctl__label */
/* 子網路樹：有下層的網段標題做成可點連結（點文字進入該網段、點箭頭展開） */
.app-sider :deep(.subnet-node-link) { color: inherit; text-decoration: none; display: inline-block; width: 100%; }
.topbar-ctls { flex-shrink: 0; }
.topbar-ctl {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 32px;
  padding: 0 9px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--n-text-color, inherit);
  font: inherit;
  font-size: 13.5px;
  line-height: 1;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.topbar-ctl:hover { background: rgba(127, 127, 127, 0.12); }
.topbar-ctl .n-icon { color: var(--n-text-color-2, #888); }
.topbar-ctl:hover .n-icon { color: var(--primary-color, #18a058); }
.topbar-ctl__label { white-space: nowrap; }
.topbar-caret { font-size: 10px; opacity: 0.5; margin-left: 1px; }
.topbar-divider {
  width: 1px;
  height: 18px;
  background: rgba(127, 127, 127, 0.25);
  margin: 0 4px;
  flex: none;
}
/* 頂列整列與搜尋框垂直置中（避免控制項偏上） */
.topbar :deep(.n-space) { align-items: center; }
.topbar-ctls > * { display: inline-flex; align-items: center; }

/* ── 左側選單拖動把手 ── */
.app-sider { position: relative; }
.sider-resizer {
  position: absolute;
  top: 0;
  right: 0;
  width: 6px;
  height: 100%;
  cursor: col-resize;
  z-index: 3;
}
.sider-resizer:hover {
  background: linear-gradient(to right, transparent, rgba(24, 160, 88, 0.35));
}
/* 收合觸發鈕要蓋在把手之上，才點得到 */
.app-sider :deep(.n-layout-toggle-button) { z-index: 5; }

/* ── 展開的下層：字小一級、行距更密 ── */
.app-sider :deep(.n-submenu-children .n-menu-item),
.app-sider :deep(.n-submenu-children .n-submenu > .n-menu-item) {
  height: 30px !important;
}
.app-sider :deep(.n-submenu-children .n-menu-item-content) {
  font-size: 13px;
  height: 30px !important;
  min-height: 30px !important;
  /* 縮短文字與虛線主幹的距離（每層縮排靠 .n-submenu-children 的 margin-left 提供） */
  padding-left: 18px !important;
}
.app-sider :deep(.n-submenu-children .n-menu-item-content .n-menu-item-content-header) {
  line-height: 1.25;
}

/* ── treeview 虛線：每層一條垂直主幹（container 左邊界）+ 每列一條水平接出 ──
   水平線用每個 item 自身的 ::before（left:0 = 該層主幹位置），所以無論第幾層都自動對齊。 */
.app-sider :deep(.n-submenu-children) {
  position: relative;
  margin-left: 16px;
}
.app-sider :deep(.n-submenu-children > .n-menu-item),
.app-sider :deep(.n-submenu-children > .n-submenu),
.app-sider :deep(.n-submenu-children > .n-submenu > .n-menu-item) { position: relative; }
/* 垂直主幹：
   - 葉節點(.n-menu-item)：畫滿該列高度
   - 群組(.n-submenu)：畫滿「整個群組」高度（含展開的子項）→ 展開時相鄰群組之間
     主幹不會斷掉（修正：原本只畫群組標題列，子項展開後就出現缺口接不下去）。 */
.app-sider :deep(.n-submenu-children > .n-menu-item)::after,
.app-sider :deep(.n-submenu-children > .n-submenu:not(:last-child))::after {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  border-left: 1px dashed rgba(150, 150, 150, 0.5);
  pointer-events: none;
}
/* 最後一個直接子項：垂直線只到中點（連到自己後就轉進來，不再往下畫）。
   群組則畫在它的標題列上（不延伸到自己的子項區）。 */
.app-sider :deep(.n-submenu-children > .n-menu-item:last-child)::after,
.app-sider :deep(.n-submenu-children > .n-submenu:last-child > .n-menu-item)::after {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  height: 50%;
  border-left: 1px dashed rgba(150, 150, 150, 0.5);
  pointer-events: none;
}
/* 水平接出 */
.app-sider :deep(.n-submenu-children > .n-menu-item)::before,
.app-sider :deep(.n-submenu-children > .n-submenu > .n-menu-item)::before {
  content: "";
  position: absolute;
  left: 0;
  width: 12px;
  top: 50%;
  border-top: 1px dashed rgba(150, 150, 150, 0.5);
  pointer-events: none;
  z-index: 1;
}
</style>
