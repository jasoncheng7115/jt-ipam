/**
 * 全站關聯物件的連結 render helper。
 *
 * 用 h() 生成 <a> 元素，點選以 router push 跳到對應 detail / list 頁。
 * 哪些 entity 有 detail page：section / subnet / device。其餘
 * (vlan / vrf / customer / location / rack) 暫時連到對應 list 頁。
 */
import { h } from "vue";
import type { Router } from "vue-router";

const LINK_STYLE =
  "color: var(--primary-color, #18a058); text-decoration: none; cursor: pointer;";

function _link(label: string, onClick: (e: MouseEvent) => void) {
  return h("a", {
    href: "#",
    style: LINK_STYLE,
    onClick: (e: MouseEvent) => { e.preventDefault(); onClick(e); },
  }, label);
}

export function useEntityLinks(router: Router) {
  return {
    /** section id → section-detail 頁 */
    section(id: string | null | undefined, label: string | null | undefined) {
      if (!id) return label ?? "—";
      return _link(label ?? id.slice(0, 8) + "…",
        () => { void router.push({ name: "section-detail", params: { id } }); });
    },

    /** subnet id → subnet-detail 頁 */
    subnet(id: string | null | undefined, label: string | null | undefined) {
      if (!id) return label ?? "—";
      return _link(label ?? id.slice(0, 8) + "…",
        () => { void router.push({ name: "subnet-detail", params: { id } }); });
    },

    /** device id → device-detail 頁 */
    device(id: string | null | undefined, label: string | null | undefined) {
      if (!id) return label ?? "—";
      return _link(label ?? id.slice(0, 8) + "…",
        () => { void router.push({ name: "device-detail", params: { id } }); });
    },

    /** IP id → address-detail 獨立頁 */
    ipById(id: string | null | undefined, label: string | null | undefined) {
      if (!id) return label ?? "—";
      return _link(label ?? id.slice(0, 8) + "…",
        () => { void router.push({ name: "address-detail", params: { id } }); });
    },

    /** 只有 IP 文字、沒有 id 時：用 /addresses?q=<ip> 搜尋 */
    ipByText(ipText: string | null | undefined, label?: string | null) {
      if (!ipText) return label ?? "—";
      return _link(label ?? ipText,
        () => { void router.push({ name: "addresses", query: { q: ipText } }); });
    },

    /** Customer detail (/admin/customers/:id) */
    customer(id: string | null | undefined, label: string | null | undefined) {
      if (!id) return label ?? "—";
      return _link(label ?? "—",
        () => { void router.push({ name: "customer-detail", params: { id } }); });
    },

    /** Location 暫無 detail；連到 /locations 列表 */
    location(id: string | null | undefined, label: string | null | undefined) {
      if (!id) return label ?? "—";
      return _link(label ?? "—",
        () => { void router.push({ name: "locations" }); });
    },

    /** Rack 暫無 detail；連到 /racks 列表 */
    rack(id: string | null | undefined, label: string | null | undefined) {
      if (!id) return label ?? "—";
      return _link(label ?? "—",
        () => { void router.push({ name: "racks" }); });
    },

    /** VLAN 暫無 detail；連到 /vlans 列表 */
    vlan(id: string | null | undefined, label: string | null | undefined) {
      if (!id) return label ?? "—";
      return _link(label ?? "—",
        () => { void router.push({ name: "vlans" }); });
    },

    /** VRF 暫無 detail；連到 /vrfs 列表 */
    vrf(id: string | null | undefined, label: string | null | undefined) {
      if (!id) return label ?? "—";
      return _link(label ?? "—",
        () => { void router.push({ name: "vrfs" }); });
    },
  };
}
