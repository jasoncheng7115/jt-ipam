/**
 * 遠端主控（RDP/VNC）「送出特殊按鍵」共用邏輯。
 * 瀏覽器/本機 OS 會攔截部分按鍵（Ctrl+Alt+Del、F5/F11/F12、⊞/⌘…），
 * 改由選單送出組合鍵序列到遠端：每個 token 依序按下，再反序放開。
 * token：長度 1 = 字元（ch=該字元）；其餘 = 特殊鍵名（後端 _SPECIAL_KEYS / _VNC_KEYSYMS 對應）。
 * 選單項目以「鍵帽」樣式呈現（每個鍵一個小框，以 + 串接），並依平台帶 icon。
 */
import { h } from "vue";
import { NIcon, type DropdownOption } from "naive-ui";
import { Key, Windows, Apple } from "@iconoir/vue";

export const KEY_COMBOS: Record<string, string[]> = {
  esc: ["Escape"],
  tab: ["Tab"],
  cad: ["Control", "Alt", "Delete"],
  win: ["Meta"],
  alttab: ["Alt", "Tab"],
  ctrlesc: ["Control", "Escape"],
  cmdtab: ["Meta", "Tab"],
  cmdspace: ["Meta", " "],
  cmdq: ["Meta", "q"],
  cmdw: ["Meta", "w"],
};
for (let i = 1; i <= 12; i++) KEY_COMBOS[`f${i}`] = [`F${i}`];

const CAP = "display:inline-block;min-width:16px;text-align:center;padding:0 5px;"
  + "border:1px solid rgba(128,128,128,.55);border-radius:4px;font-size:11px;font-weight:600;"
  + "line-height:1.55;background:rgba(128,128,128,.10);font-family:inherit";

/** 把鍵名陣列渲染成鍵帽（key1 + key2 + …）。 */
function keycaps(caps: string[]) {
  return () => h(
    "span",
    { style: "display:inline-flex;align-items:center;gap:4px" },
    caps.flatMap((c, i) => (i === 0
      ? [h("kbd", { style: CAP }, c)]
      : [h("span", { style: "opacity:.5;font-size:11px" }, "+"), h("kbd", { style: CAP }, c)])),
  );
}
function ic(Icon: any) { return () => h(NIcon, null, () => h(Icon)); }
// 「F1 – F12」也用鍵帽樣式呈現
function fkeyRangeLabel() {
  return h("span", { style: "display:inline-flex;align-items:center;gap:4px" }, [
    h("kbd", { style: CAP }, "F1"),
    h("span", { style: "opacity:.5;font-size:11px" }, "–"),
    h("kbd", { style: CAP }, "F12"),
  ]);
}

/** 組合鍵選單；mac=true 時多一組 macOS（⌘）組合（VNC 目標可能是 Mac，RDP 一律 Windows）。 */
export function buildSendKeysMenu(mac: boolean): DropdownOption[] {
  const fchildren: DropdownOption[] = Array.from({ length: 12 }, (_, i) => ({
    key: `f${i + 1}`, label: keycaps([`F${i + 1}`]), icon: ic(Key),
  }));
  const menu: DropdownOption[] = [
    { key: "esc", label: keycaps(["Esc"]), icon: ic(Key) },
    { key: "tab", label: keycaps(["Tab"]), icon: ic(Key) },
    { key: "fgrp", label: fkeyRangeLabel, icon: ic(Key), children: fchildren },
    { type: "divider", key: "d1" },
    { type: "group", label: "Windows", key: "g_win", children: [
      { key: "cad", label: keycaps(["Ctrl", "Alt", "Del"]), icon: ic(Windows) },
      { key: "win", label: keycaps(["⊞ Win"]), icon: ic(Windows) },
      { key: "alttab", label: keycaps(["Alt", "Tab"]), icon: ic(Windows) },
      { key: "ctrlesc", label: keycaps(["Ctrl", "Esc"]), icon: ic(Windows) },
    ] },
  ];
  if (mac) {
    menu.push({ type: "group", label: "macOS", key: "g_mac", children: [
      { key: "cmdtab", label: keycaps(["⌘", "Tab"]), icon: ic(Apple) },
      { key: "cmdspace", label: keycaps(["⌘", "Space"]), icon: ic(Apple) },
      { key: "cmdq", label: keycaps(["⌘", "Q"]), icon: ic(Apple) },
      { key: "cmdw", label: keycaps(["⌘", "W"]), icon: ic(Apple) },
    ] });
  }
  return menu;
}

/** 回傳一個 onSelect(comboKey) — 透過傳入的 wsSend 送出該組合鍵的按下/放開序列。 */
export function makeSendCombo(wsSend: (o: Record<string, unknown>) => void) {
  return (comboKey: string) => {
    const tokens = KEY_COMBOS[comboKey];
    if (!tokens) return;
    const sk = (tok: string, p: boolean) =>
      wsSend({ type: "k", key: tok, ch: tok.length === 1 ? tok : "", p });
    tokens.forEach((t) => sk(t, true));
    [...tokens].reverse().forEach((t) => sk(t, false));
  };
}
