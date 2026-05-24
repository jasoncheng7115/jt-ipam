<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NCard, NDataTable, NSpace, NIcon, NButton, NModal, NForm, NFormItem,
  NInput, NSwitch, NTabs, NTabPane, NSelect, NPopconfirm, NTag,
  useMessage, type DataTableColumns,
} from "naive-ui";
import {
  FirewallIcon, PlusIcon, EditIcon, DeleteIcon, RefreshIcon, SyncIcon, TestIcon, SaveIcon, CancelIcon,
} from "@/icons";
import {
  listFirewalls, createFirewall, updateFirewall, deleteFirewall, testFirewall, syncFirewall,
  listAliasMappings, createAliasMapping, deleteAliasMapping, syncOneMapping,
  type OPNsenseFirewall, type OPNsenseAliasMapping,
} from "@/api/integrations";

const { t } = useI18n();
const msg = useMessage();
const tab = ref<"firewalls" | "mappings">("firewalls");
const fws = ref<OPNsenseFirewall[]>([]);
const mappings = ref<OPNsenseAliasMapping[]>([]);
const loading = ref(false);

const showFw = ref(false);
const editingFw = ref<OPNsenseFirewall | null>(null);
const newFw = ref({
  name: "", api_url: "https://", api_key: "", api_secret: "",
  verify_tls: true, description: "",
});

function openFwCreate() {
  editingFw.value = null;
  newFw.value = { name: "", api_url: "https://", api_key: "", api_secret: "", verify_tls: true, description: "" };
  showFw.value = true;
}
function openFwEdit(r: OPNsenseFirewall) {
  editingFw.value = r;
  newFw.value = {
    name: r.name, api_url: r.api_url, api_key: "", api_secret: "",
    verify_tls: r.verify_tls, description: r.description ?? "",
  };
  showFw.value = true;
}
const showMapCreate = ref(false);
const newMap = ref({
  firewall_id: "", alias_name: "", alias_type: "host",
  selector_type: "section" as "section" | "subnet" | "tag" | "custom_field",
  selector_section_id: "" as string,
  selector_subnet_id: "" as string,
  selector_tag: "",
  selector_field: "",
  selector_value: "",
  direction: "push" as "push" | "pull" | "both",
});

import { listSections } from "@/api/sections";
import { listSubnets } from "@/api/subnets";
const sectionOpts = ref<{ label: string; value: string }[]>([]);
const subnetOpts = ref<{ label: string; value: string }[]>([]);

async function loadAliasSelectorOpts() {
  try {
    const [secs, subs] = await Promise.all([listSections(1, 200), listSubnets({ page: 1, pageSize: 500 })]);
    sectionOpts.value = secs.items.map((s) => ({ label: s.name, value: s.id }));
    subnetOpts.value = subs.items.map((s: any) => ({
      label: `${s.cidr}${s.description ? ' — ' + s.description : ''}`, value: s.id,
    }));
  } catch {}
}

const fwOptions = computed(() => fws.value.map((f) => ({ label: f.name, value: f.id })));

async function refresh() {
  loading.value = true;
  try {
    const [f, m] = await Promise.all([listFirewalls(200, 0), listAliasMappings()]);
    fws.value = f.items;
    mappings.value = m.items;
  } catch { msg.error(t("errors.network")); }
  finally { loading.value = false; }
}
async function submitFw() {
  try {
    if (editingFw.value) {
      const payload: any = {
        name: newFw.value.name,
        api_url: newFw.value.api_url,
        verify_tls: newFw.value.verify_tls,
        description: newFw.value.description || undefined,
      };
      // 只在使用者輸入新憑證時才送 — backend 要 key+secret 同時送
      if (newFw.value.api_key && newFw.value.api_secret) {
        payload.api_key = newFw.value.api_key;
        payload.api_secret = newFw.value.api_secret;
      }
      await updateFirewall(editingFw.value.id, payload);
    } else {
      await createFirewall({
        name: newFw.value.name, api_url: newFw.value.api_url,
        api_key: newFw.value.api_key, api_secret: newFw.value.api_secret,
        verify_tls: newFw.value.verify_tls,
        description: newFw.value.description || undefined,
      });
    }
    showFw.value = false;
    await refresh();
  } catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function submitMap() {
  try {
    let sel: Record<string, unknown> = { type: newMap.value.selector_type };
    if (newMap.value.selector_type === "section") {
      sel.section_id = newMap.value.selector_section_id;
    } else if (newMap.value.selector_type === "subnet") {
      sel.subnet_id = newMap.value.selector_subnet_id;
    } else if (newMap.value.selector_type === "tag") {
      sel.tag = newMap.value.selector_tag;
    } else if (newMap.value.selector_type === "custom_field") {
      sel.field = newMap.value.selector_field;
      sel.value = newMap.value.selector_value;
    }
    await createAliasMapping({
      firewall_id: newMap.value.firewall_id,
      alias_name: newMap.value.alias_name,
      alias_type: newMap.value.alias_type,
      selector: sel,
      direction: newMap.value.direction,
    });
    showMapCreate.value = false;
    newMap.value = { firewall_id: "", alias_name: "", alias_type: "host",
      selector_type: "section", selector_section_id: "", selector_subnet_id: "",
      selector_tag: "", selector_field: "", selector_value: "",
      direction: "push" };
    await refresh();
  } catch (e: any) { msg.error(e?.message ?? e?.response?.data?.detail ?? t("errors.server")); }
}
async function testFw(id: string) {
  try { const r = await testFirewall(id); msg.success(JSON.stringify(r).slice(0, 80)); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function syncFw(id: string) {
  try { const r = await syncFirewall(id); msg.success(JSON.stringify(r).slice(0, 100)); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function delFw(id: string) {
  try { await deleteFirewall(id); await refresh(); } catch { msg.error(t("errors.server")); }
}
async function syncMap(id: string) {
  try { const r = await syncOneMapping(id); msg.success(JSON.stringify(r).slice(0, 100)); await refresh(); }
  catch (e: any) { msg.error(e?.response?.data?.detail ?? t("errors.server")); }
}
async function delMap(id: string) {
  try { await deleteAliasMapping(id); await refresh(); } catch { msg.error(t("errors.server")); }
}

const fwCols = computed<DataTableColumns<OPNsenseFirewall>>(() => [
  { title: t("firewall_admin.name"), key: "name" },
  { title: "API URL", key: "api_url" },
  {
    title: "TLS", key: "verify_tls",
    render: (r) => h(NTag, { size: "small", type: r.verify_tls ? "success" : "warning" },
      () => r.verify_tls ? "verified" : "skip"),
  },
  {
    title: "last sync", key: "last_sync_at",
    render: (r) => r.last_sync_at ? new Date(r.last_sync_at).toLocaleString() : "—",
  },
  { title: "last error", key: "last_error", render: (r) => r.last_error ?? "—" },
  {
    title: t("common.actions"), key: "actions",
    render: (r) => h(NSpace, { size: "small" }, () => [
      h(NButton, { size: "small", onClick: () => openFwEdit(r) }, () => t("common.edit")),
      h(NButton, { size: "small", onClick: () => testFw(r.id) }, () => t("common.test")),
      h(NButton, { size: "small", type: "primary", onClick: () => syncFw(r.id) }, () => t("common.sync")),
      h(NPopconfirm, { onPositiveClick: () => delFw(r.id) },
        { trigger: () => h(NButton, { size: "small", type: "error" }, () => t("common.delete")),
          default: () => t("common.confirm_delete") }),
    ]),
  },
]);
const mapCols = computed<DataTableColumns<OPNsenseAliasMapping>>(() => [
  { title: t("firewall_admin.alias_name"), key: "alias_name" },
  { title: t("firewall_admin.alias_type"), key: "alias_type" },
  {
    title: "fw", key: "firewall_id",
    render: (r) => fws.value.find((f) => f.id === r.firewall_id)?.name ?? r.firewall_id.slice(0, 8),
  },
  { title: t("firewall_admin.direction"), key: "direction" },
  { title: t("firewall_admin.last_synced_count"), key: "last_synced_count",
    render: (r) => r.last_synced_count ?? "—" },
  {
    title: "selector", key: "selector",
    render: (r) => h("code", { style: "font-size: 11px" }, JSON.stringify(r.selector).slice(0, 60)),
  },
  {
    title: t("common.actions"), key: "actions",
    render: (r) => h(NSpace, { size: "small" }, () => [
      h(NButton, { size: "small", type: "primary", onClick: () => syncMap(r.id) }, () => t("common.sync")),
      h(NPopconfirm, { onPositiveClick: () => delMap(r.id) },
        { trigger: () => h(NButton, { size: "small", type: "error" }, () => t("common.delete")),
          default: () => t("common.confirm_delete") }),
    ]),
  },
]);

onMounted(() => { void refresh(); });
</script>

<template>
  <n-card>
    <template #header>
      <n-space align="center" :wrap-item="false">
        <n-icon :size="22"><FirewallIcon /></n-icon>
        <span>{{ t("firewall_admin.title") }}</span>
      </n-space>
    </template>
    <n-tabs v-model:value="tab" type="line">
      <n-tab-pane name="firewalls" :tab="t('firewall_admin.title')">
        <n-space style="margin-bottom: 12px">
          <n-button @click="refresh" :loading="loading">
            <template #icon><n-icon><RefreshIcon /></n-icon></template>
            {{ t("common.refresh") }}
          </n-button>
          <n-button type="primary" @click="openFwCreate">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("firewall_admin.create_firewall") }}
          </n-button>
        </n-space>
        <n-data-table :columns="fwCols" :data="fws" :loading="loading" :bordered="false" />
      </n-tab-pane>
      <n-tab-pane name="mappings" :tab="t('firewall_admin.alias_mappings')">
        <n-space style="margin-bottom: 12px">
          <n-button type="primary"
                    @click="loadAliasSelectorOpts(); showMapCreate = true">
            <template #icon><n-icon><PlusIcon /></n-icon></template>
            {{ t("firewall_admin.create_mapping") }}
          </n-button>
        </n-space>
        <n-data-table :columns="mapCols" :data="mappings" :loading="loading" :bordered="false" />
      </n-tab-pane>
    </n-tabs>

    <n-modal v-model:show="showFw" preset="card"
             :title="editingFw ? t('common.edit') : t('firewall_admin.create_firewall')"
             style="width: 480px">
      <n-form>
        <n-form-item :label="t('firewall_admin.name')"><n-input v-model:value="newFw.name" /></n-form-item>
        <n-form-item :label="t('firewall_admin.api_url')">
          <n-input v-model:value="newFw.api_url" placeholder="https://opnsense.example.com" />
        </n-form-item>
        <n-form-item :label="`API key${editingFw ? ' (' + t('users.password_blank_unchanged') + ')' : ''}`">
          <n-input v-model:value="newFw.api_key" />
        </n-form-item>
        <n-form-item :label="`API secret${editingFw ? ' (' + t('users.password_blank_unchanged') + ')' : ''}`">
          <n-input v-model:value="newFw.api_secret" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item :label="t('firewall_admin.verify_tls')">
          <n-switch v-model:value="newFw.verify_tls" />
        </n-form-item>
        <n-form-item :label="t('sections.description')">
          <n-input v-model:value="newFw.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showFw = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submitFw">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>

    <n-modal v-model:show="showMapCreate" preset="card" :title="t('firewall_admin.create_mapping')"
             style="width: 520px">
      <n-form>
        <n-form-item label="Firewall">
          <n-select v-model:value="newMap.firewall_id" :options="fwOptions" />
        </n-form-item>
        <n-form-item :label="t('firewall_admin.alias_name')">
          <n-input v-model:value="newMap.alias_name" placeholder="jt_section_addrs" />
        </n-form-item>
        <n-form-item :label="t('firewall_admin.alias_type')">
          <n-select v-model:value="newMap.alias_type"
                    :options="['host','network','port','url','urltable','geoip','networkgroup','mac','asn'].map(v => ({label: v, value: v}))" />
        </n-form-item>
        <n-form-item :label="t('firewall_admin.selector_type')">
          <n-select v-model:value="newMap.selector_type"
                    :options="[
                      {label: 'Section', value: 'section'},
                      {label: 'Subnet', value: 'subnet'},
                      {label: 'Tag', value: 'tag'},
                      {label: 'Custom field', value: 'custom_field'},
                    ]" />
        </n-form-item>
        <n-form-item v-if="newMap.selector_type === 'section'" label="Section">
          <n-select v-model:value="newMap.selector_section_id" :options="sectionOpts" filterable />
        </n-form-item>
        <n-form-item v-else-if="newMap.selector_type === 'subnet'" label="Subnet">
          <n-select v-model:value="newMap.selector_subnet_id" :options="subnetOpts" filterable />
        </n-form-item>
        <n-form-item v-else-if="newMap.selector_type === 'tag'" label="Tag">
          <n-input v-model:value="newMap.selector_tag" placeholder="wifi-guest" />
        </n-form-item>
        <template v-else>
          <n-form-item label="Custom field name">
            <n-input v-model:value="newMap.selector_field" placeholder="role" />
          </n-form-item>
          <n-form-item label="Value">
            <n-input v-model:value="newMap.selector_value" placeholder="monitoring" />
          </n-form-item>
        </template>
        <n-form-item :label="t('firewall_admin.direction')">
          <n-select v-model:value="newMap.direction"
                    :options="[{label:'push',value:'push'},{label:'pull',value:'pull'},{label:'both',value:'both'}]" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button @click="showMapCreate = false">{{ t("common.cancel") }}</n-button>
        <n-button type="primary" @click="submitMap">{{ t("common.save") }}</n-button>
      </n-space>
    </n-modal>
  </n-card>
</template>
