"""畫面地圖：某個功能、頁籤、欄位在網頁的哪裡（AI 回答「X 在哪裡設定」用）。

起因（使用者 2026-10-15）：問「電路的申請日與到期日要去哪裡查」，AI 說電路沒有這兩個欄位 —— 編輯畫面明明有
「開通日」「合約到期」。模型只知道工具給它的資料，不知道畫面長什麼樣子；它對選單的說法若不是從畫面來的，
就只能用猜的。使用者要求「有人問哪個功能或欄位在哪裡，要能正確回答」。

**直接從前端原始碼解析，不維護一份手寫清單**：手寫清單一定會跟畫面脫節（選單路徑說明就出過十幾處錯，
見 tests/test_nav_paths.py）。這裡讀：
- `components/layout/MainLayout.vue`：側欄選單（含「進階」「管理」「外部系統整合」子選單、哪些要設定了整合才出現）、
  右上角帳號選單
- `router/index.ts`：選單項目 → 頁面元件（同一個元件帶不同 mode 的也分得出來）
- 每個頁面元件與它引用的子元件：頁籤（n-tab-pane）、表單欄位（n-form-item 的 label）、勾選項、卡片／區塊標題、
  表格欄位（script 裡的 `title: t(...)`）
- 三種語言的 i18n 檔：回答用使用者介面的語言

安裝與升級都是從原始碼建置前端，`frontend/src` 一定在；讀不到時工具會明講，不會亂答。
結果依程序快取（部署會重啟服務，原始碼改了自然重建）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

FE = Path(__file__).resolve().parents[3] / "frontend" / "src"
LOCALES = ("zh-TW", "en-US", "ja-JP")

#: 沒有出現在選單上、要從清單點進去的詳細頁 → 從哪個選單項目進去
DETAIL_PARENTS: dict[str, str] = {
    "section-detail": "sections",
    "subnet-detail": "subnets",
    "address-detail": "addresses",
    "address-unmanaged": "subnets",
    "address-identify": "addresses",
    "device-detail": "devices",
    "customer-detail": "customers",
    "request-detail": "requests",
    "change_impact_plan": "change_impact",
    "change_impact_services": "change_impact",
    "adv-circuit-detail": "adv-circuits",
    "vpn-detail": "vpn-tunnels",
    "adv-domain-detail": "adv-domains",
    "maintenance-detail": "maintenance",
}

#: 有頁面元件、但不在選單上也不是詳細頁的路由（理由）。新增頁面沒歸類時，測試會失敗。
UNLISTED: dict[str, str] = {
    "advanced": "舊網址：進階頁不帶 mode（選單上已拆成租戶／ASN／電路／聯絡人／無線各一頁）",
    "firewall_admin": "與 firewall 共用 FirewallAdmin.vue（管理 → 外部系統整合 → OPNsense 的另一個網址）",
    "ip-identify": "從 IP 詳細頁或未登記位址點「探測」進去，跟著 address-identify",
    "ai_audit": "從異常偵測／系統設定進去的 AI 巡檢結果頁",
    "hostname_precedence": "從系統設定的「主機名稱來源優先序」進去",
    "oui_admin": "從系統設定進去的 OUI 資料庫頁",
    "recog_admin": "從系統設定進去的 Recog 指紋庫頁",
    "notifications": "右上角通知鈴鐺的「全部通知」",
    "not_found": "網址打錯或物件已刪除時顯示的「找不到頁面」，不是可以走到的頁面",
}

#: 右上角帳號選單的名稱（選單本身是使用者名稱，沒有 i18n 字串）
_ACCOUNT_MENU = {"zh-TW": "右上角帳號選單", "en-US": "Account menu (top right)", "ja-JP": "右上のアカウントメニュー"}
_ACCOUNT_ROUTES = {"profile": "settings", "preferences": "settings", "my_chat_history": "my_chat_history",
                   "api_tokens": "api_tokens"}

_TAG_RE = re.compile(r"<(/?)([A-Za-z][\w.-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>")
_T_RE = re.compile(r"""\bt\(\s*["'`]([A-Za-z_][\w]*(?:\.[\w]+)+)["'`]""")
_ATTR_RE = re.compile(r"""(?:^|\s)(:|v-bind:)?([\w#:@.-]+)(?:=("([^"]*)"|'([^']*)'))?""")
_CALL_LIT_RE = re.compile(r"""\w+\(\s*['"](\w+)['"]""")
_KIND_RE = re.compile(r"""===?\s*['"](\w+)['"]""")
_VOID = {"br", "hr", "img", "input", "meta", "link", "col", "source", "wbr"}
_IMPORT_RE = re.compile(r"""import\s+(\w+)\s+from\s+["'](@/components/[\w/]+\.vue|\.{1,2}/[\w/.]+\.vue)["']""")
_SELECT_OPT_RE = re.compile(r"""\blabel:\s*t\(\s*["']([\w]+(?:\.[\w]+)+)["']\s*\)\s*,\s*value:""")
_COLUMN_RE = re.compile(r"""\btitle:\s*(?:\(\)\s*=>\s*)?t\(\s*["']([\w]+(?:\.[\w]+)+)["']""")


@dataclass(frozen=True)
class Label:
    key: str | None = None
    literal: str | None = None

    def text(self, strings: dict[str, Any]) -> str | None:
        if self.literal is not None:
            return self.literal
        o: Any = strings
        for part in (self.key or "").split("."):
            o = o.get(part) if isinstance(o, dict) else None
        return o if isinstance(o, str) else None


@dataclass(frozen=True)
class Tab:
    name: str | None
    label: Label | None


@dataclass
class Element:
    kind: str                     # field / option / choice / button / tab / section / column
    label: Label
    tabs: tuple[Tab, ...] = ()
    kind_ctx: str | None = None   # 表單依「哪一種資料」切換時（例如 createKind === 'circuit'）
    name: str | None = None       # 頁籤本身的 name（kind == "tab"）：頁面支援 ?tab= 時連結帶上它


@dataclass
class Page:
    route: str | None
    menu: tuple[Label, ...]
    admin: bool = False
    condition: str | None = None  # integration:<名稱> / setting:<名稱>
    via_row: bool = False         # 詳細頁：從清單點進去
    component: str | None = None
    path: str | None = None       # 網址（詳細頁是它的清單頁）；含參數的路由沒有
    tab_query: bool = False       # 頁面讀 ?tab= 切到指定頁籤
    elements: list[Element] = field(default_factory=list)


def _label_from_attr(attrs: str, name: str) -> Label | None:
    for m in _ATTR_RE.finditer(attrs):
        bound, attr, val = m.group(1), m.group(2), (m.group(4) if m.group(4) is not None else m.group(5))
        if attr != name or val is None:
            continue
        if bound:
            k = _T_RE.search(val)
            return Label(key=k.group(1)) if k else None
        return Label(literal=val) if val.strip() else None
    return None


def _first_t(text: str) -> Label | None:
    k = _T_RE.search(text)
    return Label(key=k.group(1)) if k else None


def _sfc_parts(src: str) -> tuple[str, str]:
    tpl = re.search(r"^<template>[^\n]*\n(.*?)^</template>", src, re.S | re.M)
    scr = re.search(r"^<script[^>]*>(.*?)^</script>", src, re.S | re.M)
    return (tpl.group(1) if tpl else ""), (scr.group(1) if scr else "")


def _kebab(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


@dataclass
class _Parsed:
    elements: list[Element]
    children: list[tuple[Path, tuple[Tab, ...], str | None]]   # 子元件、用在哪個頁籤、哪種資料


def _parse_component(path: Path) -> _Parsed:
    src = path.read_text(encoding="utf-8")
    tpl, script = _sfc_parts(src)
    imports: dict[str, Path] = {}
    for name, ref in _IMPORT_RE.findall(script):
        p = (FE / ref[2:]) if ref.startswith("@/") else (path.parent / ref).resolve()
        if p.exists():
            imports[name] = p
            imports[_kebab(name)] = p

    elements: list[Element] = []
    children: list[tuple[Path, tuple[Tab, ...], str | None]] = []
    stack: list[dict[str, Any]] = []
    tab_literals: list[tuple[tuple[Tab, ...], set[str]]] = []

    def open_tabs() -> tuple[Tab, ...]:
        return tuple(f["tab"] for f in stack if f.get("tab") is not None)

    def kind_ctx() -> str | None:
        for f in reversed(stack):
            if f.get("kind"):
                return f["kind"]
        return None

    for m in _TAG_RE.finditer(tpl):
        closing, tag, attrs = m.group(1) == "/", m.group(2), m.group(3) or ""
        low = tag.lower()
        if closing:
            # 找到同名的那一層才收（容錯：中間沒收乾淨的一起丟掉）
            for i in range(len(stack) - 1, -1, -1):
                if stack[i]["tag"] == low:
                    frame = stack[i]
                    del stack[i:]
                    content = tpl[frame["start"]:m.start()]
                    cap = frame.get("capture")
                    if cap == "tab-slot":
                        owner = frame["owner"]
                        lab = _first_t(content)
                        if owner.get("tab") is not None and owner["tab"].label is None and lab:
                            owner["tab"] = Tab(owner["tab"].name, lab)
                            parent_tabs = tuple(f["tab"] for f in stack if f.get("tab") is not None
                                                and f is not owner)
                            elements.append(Element("tab", lab, parent_tabs, kind_ctx(), owner["tab"].name))
                    elif cap in ("option", "section", "button"):
                        lab = _first_t(content)
                        if lab:
                            elements.append(Element(cap, lab, open_tabs(), kind_ctx()))
                    elif cap == "label-slot" or cap == "label-text":
                        lab = _first_t(content)
                        if lab:
                            elements.append(Element("field", lab, open_tabs(), kind_ctx()))
                    if frame.get("tab") is not None:
                        tab_literals.append(((*open_tabs(), frame["tab"]), frame["literals"]))
                    break
            continue
        self_closing = attrs.rstrip().endswith("/") or low in _VOID
        # 事件屬性裡帶字串的呼叫（openCreate('provider')）→ 記在目前所有頁籤上，用來把表單對回頁籤
        lits = set(_CALL_LIT_RE.findall(" ".join(v for v in re.findall(r'@[\w.:-]+="([^"]*)"', attrs))))
        for f in stack:
            if f.get("tab") is not None:
                f["literals"] |= lits
        frame: dict[str, Any] = {"tag": low, "start": m.end()}
        if low == "n-tab-pane":
            name_m = re.search(r'(?:^|\s)name="([^"]*)"', attrs)
            frame["tab"] = Tab(name_m.group(1) if name_m else None,
                               _label_from_attr(attrs, "tab"))
            frame["literals"] = set()
            if frame["tab"].label is not None:
                elements.append(Element("tab", frame["tab"].label, open_tabs(), kind_ctx(), frame["tab"].name))
        elif low == "template":
            if re.search(r"(?:^|\s)(?:#tab|v-slot:tab)\b", attrs):
                owner = next((f for f in reversed(stack) if f.get("tab") is not None), None)
                if owner is not None and owner["tab"].label is None:
                    frame["capture"], frame["owner"] = "tab-slot", owner
            elif re.search(r"(?:^|\s)(?:#label|v-slot:label)\b", attrs) and stack and \
                    stack[-1]["tag"].startswith("n-form-item") and not stack[-1].get("labelled"):
                frame["capture"] = "label-slot"
            cond = re.search(r'v-(?:else-)?if="([^"]*)"', attrs)
            if cond:
                k = _KIND_RE.search(cond.group(1))
                if k:
                    frame["kind"] = k.group(1)
        elif low in ("n-form-item", "n-form-item-gi"):
            lab = _label_from_attr(attrs, "label")
            if lab:
                frame["labelled"] = True
                elements.append(Element("field", lab, open_tabs(), kind_ctx()))
        elif low in ("n-checkbox", "n-radio"):
            lab = _label_from_attr(attrs, "label")
            if lab:
                elements.append(Element("option", lab, open_tabs(), kind_ctx()))
            else:
                frame["capture"] = "option"
        elif low in ("n-card", "n-collapse-item"):
            lab = _label_from_attr(attrs, "title")
            if lab:
                elements.append(Element("section", lab, open_tabs(), kind_ctx()))
        elif low in ("n-divider", "h2", "h3", "h4"):
            frame["capture"] = "section"
        elif low == "n-button":
            frame["capture"] = "button"
        elif low == "label":
            # 自訂排版的設定列（系統設定頁：<label>{{ t(...) }}</label> ＋右邊控制項）
            frame["capture"] = "label-text"
        elif tag in imports or low in imports:
            children.append((imports.get(tag) or imports[low], open_tabs(), kind_ctx()))
        if not self_closing:
            stack.append(frame)

    # 依「哪種資料」切換的共用表單（例如進階頁的新增對話框）：用開它的按鈕在哪個頁籤，把欄位對回那個頁籤
    for el in elements:
        if el.tabs or not el.kind_ctx:
            continue
        hits = [path for path, lit in tab_literals if el.kind_ctx in lit]
        if hits:
            deepest = max(len(h) for h in hits)
            cands = {h for h in hits if len(h) == deepest}
            if len(cands) == 1:
                el.tabs = next(iter(cands))
    for key in dict.fromkeys(_COLUMN_RE.findall(script)):
        elements.append(Element("column", Label(key=key)))
    # 下拉選單的選項（script 裡的 { label: t("…"), value: … }）
    for key in dict.fromkeys(_SELECT_OPT_RE.findall(script)):
        elements.append(Element("choice", Label(key=key)))
    return _Parsed(elements, children)


def _collect(path: Path, depth: int = 0, seen: frozenset[Path] = frozenset()) -> list[Element]:
    if depth > 3 or path in seen or not path.exists():
        return []
    parsed = _parse_component(path)
    out = list(parsed.elements)
    for child, tabs, kctx in parsed.children:
        for el in _collect(child, depth + 1, seen | {path}):
            out.append(Element(el.kind, el.label, tabs + el.tabs, el.kind_ctx or kctx))
    return out


def _routes() -> dict[str, dict[str, Any]]:
    src = (FE / "router" / "index.ts").read_text(encoding="utf-8")
    out: dict[str, dict[str, Any]] = {}
    for line in src.splitlines():
        m = re.search(r'name:\s*"([\w-]+)",\s*component:\s*\(\)\s*=>\s*import\("@/views/([\w]+\.vue)"\)', line)
        if not m:
            continue
        mode = re.search(r'props:\s*\{\s*mode:\s*"(\w+)"', line)
        pm = re.search(r'path:\s*"([^"]*)"', line)
        path = None
        if pm and ":" not in pm.group(1):
            path = pm.group(1) if pm.group(1).startswith("/") else "/" + pm.group(1)
        out[m.group(1)] = {"view": m.group(2), "mode": mode.group(1) if mode else None,
                           "admin": "admin: true" in line, "path": path}
    return out


def _bracket_end(src: str, start: int) -> int:
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "[":
            depth += 1
        elif src[i] == "]":
            depth -= 1
            if depth == 0:
                return i
    return len(src)


_ITEM_RE = re.compile(r'\{\s*label:\s*\(\)\s*=>\s*(?:t\("([\w.]+)"\)|"([^"]+)"),\s*key:\s*"([\w-]+)"')


def _guard(src: str, at: int) -> str:
    """這個選單項目外面包著的 `...(條件 ? [ … ] : [])` 的條件文字；不在條件裡回空字串。
    只看**還沒收尾**的那一個：往前找最近的 `...(`，中間若已經出現 `: [])` 就表示那個條件早就結束了。"""
    start = src.rfind("...(", 0, at)
    if start < 0 or at - start > 400:
        return ""
    between = src[start:at]
    if re.search(r":\s*\[\]\s*\)", between):
        return ""
    return between


def _condition(src: str, at: int) -> str | None:
    ctx = _guard(src, at)
    m = re.search(r"intgPresence\.value\.(\w+)", ctx)
    if m:
        return f"integration:{m.group(1)}"
    if "impact.settings" in ctx:
        return "setting:change_impact"
    return None


def _menu() -> list[tuple[tuple[Label, ...], str, bool, str | None]]:
    """[(選單路徑, 路由名稱, 管理員限定, 出現條件)]"""
    src = (FE / "components" / "layout" / "MainLayout.vue").read_text(encoding="utf-8")
    m0 = src.index("const menuOptions")
    m1 = src.index("const userMenuOptions") if "const userMenuOptions" in src else len(src)
    block = src[m0:m1]
    p_at = block.index('key: "phase3"')
    p0 = block.index("children: [", p_at) + len("children: ")
    p1 = _bracket_end(block, p0)
    a_at = block.index('key: "admin"')
    a0 = block.index("children: [", a_at) + len("children: ")
    a1 = _bracket_end(block, a0)
    adv, admin = Label(key="nav.phase3_section"), Label(key="nav.admin_section")
    out: list[tuple[tuple[Label, ...], str, bool, str | None]] = []
    for m in _ITEM_RE.finditer(block):
        key, route = m.group(1), m.group(3)
        if route in ("phase3", "admin"):
            continue
        lab = Label(key=key) if key else Label(literal=m.group(2))
        cond = _condition(block, m.start())
        is_admin_cond = "is_admin" in _guard(block, m.start())
        if p0 <= m.start() <= p1:
            out.append(((adv, lab), route, is_admin_cond, cond))
        elif a0 <= m.start() <= a1:
            out.append(((admin, lab), route, True, cond))
        else:
            out.append(((lab,), route, is_admin_cond, cond))
    intg = Label(key="nav.integrations")
    for route, key in re.findall(r'\["([\w]+)", "(nav\.[\w]+)", \w+Icon\]', src):
        out.append(((admin, intg, Label(key=key)), route, True, None))
    acct = Label(literal="__account__")
    um = src[m1:] if m1 < len(src) else ""
    for key, item in re.findall(r'\{\s*label:\s*t\("([\w.]+)"\),\s*key:\s*"(\w+)"', um[:2500]):
        if item in _ACCOUNT_ROUTES:
            out.append(((acct, Label(key=key)), _ACCOUNT_ROUTES[item], False, None))
    return out


def _elements_for(view: str, mode: str | None) -> list[Element]:
    els = _collect(FE / "views" / view)
    if not mode:
        return els
    # 同一個元件依 mode 拆成好幾頁（進階 → 租戶／ASN／電路…）：只留這一頁的頁籤；最外層頁籤列是藏起來的，不列進路徑
    out = []
    for el in els:
        if el.tabs and el.tabs[0].name == mode:
            out.append(Element(el.kind, el.label, el.tabs[1:], el.kind_ctx))
    return out


def _reads_tab_query(view: str) -> bool:
    try:
        return "route.query.tab" in (FE / "views" / view).read_text(encoding="utf-8")
    except OSError:
        return False


@lru_cache(maxsize=1)
def build() -> tuple[list[Page], dict[str, dict[str, Any]]]:
    """(所有頁面, 三種語言的字串)。讀不到前端原始碼時丟 FileNotFoundError。"""
    strings = {loc: json.loads((FE / "i18n" / f"{loc}.json").read_text(encoding="utf-8")) for loc in LOCALES}
    routes = _routes()
    pages: list[Page] = []
    menu_of: dict[str, Page] = {}
    for path, route, admin, cond in _menu():
        info = routes.get(route)
        page = Page(route=route, menu=path, admin=admin or bool(info and info["admin"]), condition=cond,
                    component=info["view"] if info else None,
                    elements=_elements_for(info["view"], info["mode"]) if info else [],
                    path=info["path"] if info else None,
                    tab_query=bool(info) and not info["mode"] and _reads_tab_query(info["view"]))
        pages.append(page)
        menu_of.setdefault(route, page)
    for route, parent in DETAIL_PARENTS.items():
        info, par = routes.get(route), menu_of.get(parent)
        if info and par:
            # 詳細頁要先選一筆，連結給它的清單頁
            pages.append(Page(route=route, menu=par.menu, admin=par.admin or info["admin"], condition=par.condition,
                              via_row=True, component=info["view"], elements=_elements_for(info["view"], None),
                              path=par.path))
    return pages, strings


def _text(label: Label | None, strings: dict[str, Any], loc: str) -> str | None:
    if label is None:
        return None
    if label.literal == "__account__":
        return _ACCOUNT_MENU.get(loc, _ACCOUNT_MENU["en-US"])
    return label.text(strings)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).casefold()


def search(keywords: list[str], locale: str = "zh-TW", limit: int = 20, *, is_admin: bool = False) -> dict[str, Any]:
    """用關鍵字找功能／頁籤／欄位在哪裡。關鍵字可以混用三種語言；回答用 `locale` 的文字。

    命中頁面名稱（選單項目）時另外列出那一頁所有的頁籤與欄位：使用者用的詞常常跟畫面不一樣
    （問「申請日」，畫面叫「開通日」），給整頁欄位讓模型自己對。

    每筆另有 `link`（站內網址，頁面支援時帶 ?tab=）讓對話視窗顯示「前往」按鈕；管理員限定的頁面
    一般帳號不給連結（點了也只會被擋）。`links` 是整理好、去重的前幾個（放在最前面，結果被截斷也還在）。"""
    loc = locale if locale in LOCALES else "zh-TW"
    pages, strings = build()
    kws = [_norm(k) for k in keywords if k and _norm(k)]
    if not kws:
        return {"locale": loc, "matches": [], "pages": []}

    def score(label: Label | None) -> int:
        best_total = 0
        for kw in kws:
            best = 0
            for lc in LOCALES:
                t = _text(label, strings[lc], lc)
                if not t:
                    continue
                nt = _norm(t)
                if nt == kw:
                    best = max(best, 5)
                elif kw in nt:
                    best = max(best, 3)
                elif len(nt) >= 2 and nt in kw:
                    best = max(best, 2)
            best_total += best
        return best_total

    def path_of(p: Page) -> list[str]:
        return [x for x in (_text(lab, strings[loc], loc) for lab in p.menu) if x]

    def tabs_of(el: Element) -> list[str]:
        return [x for x in (_text(t.label, strings[loc], loc) for t in el.tabs) if x]

    def link_of(p: Page, tab_name: str | None) -> str | None:
        if not p.path or (p.admin and not is_admin):
            return None
        if tab_name and p.tab_query and not p.via_row and re.fullmatch(r"[\w-]+", tab_name):
            return f"{p.path}?tab={tab_name}"
        return p.path

    def top_tab(el: Element) -> str | None:
        if el.tabs:
            return el.tabs[0].name
        return el.name if el.kind == "tab" else None

    def where(p: Page, tabs: list[str], tab_name: str | None = None) -> dict[str, Any]:
        d: dict[str, Any] = {"menu": path_of(p), "tabs": tabs, "admin_only": p.admin}
        link = link_of(p, tab_name)
        if link:
            d["link"] = link
        if p.via_row:
            d["open_a_row"] = True          # 詳細頁：在清單點某一筆進去
        if p.condition:
            d["shown_when"] = p.condition   # 例如 integration:opnsense＝設定了該整合才出現在選單
        return d

    hits: list[tuple[int, dict[str, Any]]] = []
    page_hits: list[tuple[int, Page]] = []
    seen: set[tuple[Any, ...]] = set()
    for p in pages:
        ps = score(p.menu[-1]) if p.menu else 0
        if ps and not p.via_row:
            page_hits.append((ps, p))
        for el in p.elements:
            s = score(el.label)
            if not s:
                continue
            label = _text(el.label, strings[loc], loc)
            tabs = tabs_of(el)
            sig = (tuple(path_of(p)), tuple(tabs), label, el.kind, p.via_row)
            if sig in seen or not label:
                continue
            seen.add(sig)
            hits.append((s + (1 if el.kind in ("field", "option", "tab") else 0),
                         {"label": label, "type": el.kind, **where(p, tabs, top_tab(el))}))
    hits.sort(key=lambda x: -x[0])
    page_hits.sort(key=lambda x: -x[0])

    page_out = []
    for _s, p in page_hits[:4]:
        fields: list[str] = []
        tabs_all: list[str] = []
        for el in p.elements:
            t = _text(el.label, strings[loc], loc)
            if not t:
                continue
            if el.kind == "tab":
                if t not in tabs_all:
                    tabs_all.append(t)
            elif el.kind in ("field", "option") and t not in fields:
                fields.append(t)
        page_out.append({**where(p, []), "page_tabs": tabs_all[:30], "fields": fields[:80]})
    matches = [h for _s, h in hits[: max(1, min(int(limit), 60))]]
    links: list[dict[str, str]] = []
    for item in [*page_out, *matches]:
        href = item.get("link")
        if not href or any(x["href"] == href for x in links):
            continue
        name = item["menu"][-1] if item["menu"] else href
        if "?tab=" in href:
            tab_label = item["tabs"][0] if item["tabs"] else item.get("label")
            if tab_label:
                name = f"{name} › {tab_label}"
        links.append({"label": name, "href": href})
    return {
        "links": links[:6],
        "locale": loc,
        "matches": matches,
        "pages": page_out,
        "types": {"field": "form field (in the create/edit dialog or settings form)",
                  "option": "checkbox / radio option", "choice": "choice in a drop-down",
                  "button": "button", "tab": "tab on the page",
                  "section": "card or section heading", "column": "table column"},
    }
