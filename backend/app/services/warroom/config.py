"""戰情室設定與電視用的顯示權杖（system_settings 的 `warroom` 鍵）。

顯示權杖（使用者 2026-10-21 決定要做）：牆上的電視不登入也能看。沿用機櫃嵌入 SVG 的系統權杖作法
（加密保存並綁定用途、常數時間比對、會到期、到期前由 credential_expiry 通知），另外：
- 預設關閉；只能讀戰情室的端點（app/api/v1/endpoints/warroom.py 的 warroom_principal）
- 看到的資料＝設定裡指定帳號（principal_user_id）的可見範圍；帳號停用或刪除就失效
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_setting import SystemSetting

KEY = "warroom"
TOKEN_AAD = b"setting:warroom:display_token"

#: 主題（順序就是預設的播放順序）
PAGES = ("overview", "capacity", "topology", "services", "anomalies", "assets", "collection",
         "stats", "relations", "availability", "ai_audit", "ai_intel")
READY_PAGES = PAGES
#: 升級後自動加進播放清單的主題。08–12（統計、物件關係、可用性、AI 巡檢、AI 情報）不自動加入，
#: 由管理員在戰情室設定自己勾（規格：不必讓所有主題預設進入每一份播放清單）
AUTO_JOIN_PAGES = PAGES[:7]
#: 第一批（M1）完成的主題：這之前存的設定沒有 ready_seen，視為當時只看過這三頁
M1_READY = ("overview", "capacity", "collection")
GROUP_BY = ("location", "section", "customer")
AI_MODES = ("off", "template", "llm")
LANGS = ("", "zh-TW", "en-US", "ja-JP")
#: 可用性主題的時間範圍（availability.WINDOWS 的鍵）；歷史不夠長時畫面會自動退到涵蓋得到的最大範圍
AVAILABILITY_WINDOWS = ("24h", "7d", "30d", "90d")
#: 換頁的過場效果（10-24 使用者：多做幾種、設定可選）；「降低動畫」開著時一律直接切換
TRANSITIONS = ("slide", "fade", "zoom", "rise", "wipe", "flip")
#: 大數字的字體（預選幾款，字檔打包在前端，不連外部字型服務）
NUMBER_FONTS = ("inter", "orbitron", "exo2", "rajdhani", "share-tech-mono", "audiowide")
MAX_SCOPE = 500                      # 每一種最多選幾個

# ─────────────── 12 AI 情報（2026-10-26，設計稿 v1.2）：主題自己的設定，跟其他主題的時間互不共用 ───────────────
#: 可選的播報領域（總覽與資料完整性每批一定有，不在這裡）；只在有對應可讀資料時才真的播
INTEL_DOMAINS = ("capacity", "anomalies", "dns", "dhcp", "collection", "topology", "virt", "certs", "ai_audit")
INTEL_LAYOUTS = ("three_panel", "focus")
INTEL_PLAYBACK = ("rotate", "pinned")
INTEL_SPEEDS = ("slow", "normal", "fast")
INTEL_EFFECTS = ("clear", "standard", "cinema")
INTEL_SCANLINES = ("off", "low", "medium")
INTEL_NOISE = ("off", "low")
INTEL_PALETTES = ("phosphor", "amber", "ice", "custom")
INTEL_COLOR_KEYS = ("bg", "text", "secondary", "accent", "warn", "critical")
#: 預設配色＝磷光綠（自訂色不合法時退回這組）
INTEL_DEFAULT_COLORS = {"bg": "#0a1012", "text": "#dce8e4", "secondary": "#a8a3ce", "accent": "#88ebc1",
                        "warn": "#e9b861", "critical": "#f07866"}
INTEL_DEFAULTS: dict[str, Any] = {
    "interval_minutes": 5,           # AI 摘要更新間隔（1–60）
    "dwell_seconds": 90,             # 輪播時停在這個主題多久（10–300）
    "layout": "three_panel",
    "playback": "rotate",            # pinned＝播到這個主題就釘住（跟按 P 一樣）
    "item_limit": 8,                 # 每批幾則（4–30）
    "domains": list(INTEL_DOMAINS),
    # 沒設定過的預設（使用者 10-26 指定）：打字快、電影效果、掃描線低、雜點低
    "speed": "fast", "rhythm": True, "cursor": True,
    "effects": "cinema", "scanlines": "low", "noise": "low",
    "palette": "phosphor", "colors": dict(INTEL_DEFAULT_COLORS),
    "background": False,             # 沒人在看也照排程產生（預設只在有人看這個主題時產生）
    "mask_ips": False,               # 送模型與畫面上的 IP 遮掉最後一段
    # 重大事件警示：只看這裡列的異常類別（AI 不可以自己升級）。預設九類（使用者 10-26 指定）；
    # 不含失聯 IP、MAC 變動、對外曝險、兩個子網段混在同一個二層（量大或屬於常態，放進來警示會一直亮），
    # 也不含 ARP flux（多網卡主機的設定問題，要修但不緊急，使用者 10-26 決定）
    "warning_enabled": True,
    "warning_categories": ["ip_conflicts", "rogue_dhcp", "unauthorized_ips", "suspicious_changes", "fw_rule_rot",
                           "mac_flapping", "identity_changes", "dangling_dns", "duplicate_ip_records"],
    # 有 WARNING 時右上那一塊持續呼吸燈（使用者 10-26：「要有明顯像呼吸燈效果」）；關掉就常亮
    "warning_breathe": True,
}

DEFAULTS: dict[str, Any] = {
    "enabled": True,                 # 登入的帳號都看得到（升級後自動可用）；顯示權杖另外開
    "pages": list(AUTO_JOIN_PAGES),
    "dwell_seconds": 20,
    "max_subpages": 2,               # 每個主題最多輪播幾個資料子頁：大型站台不會長期霸佔輪播
    "group_by": "location",
    "capacity_warn": 80,
    "capacity_crit": 90,
    "language": "",                  # 空＝跟著瀏覽器
    "reduce_motion": False,
    "availability_window": "7d",
    "transition": "slide",
    "number_font": "exo2",           # 預設 Exo 2（10-24 使用者指定）
    "ai_summary": "off",
    "display_enabled": False,
    "principal_user_id": None,
    # 顯示範圍：只顯示這些單位／區段／子網路（聯集；全空＝全部）。見 common.scope_subnet_ids
    "scope": {"customers": [], "sections": [], "subnets": []},
    "intel": dict(INTEL_DEFAULTS),
    # 存設定當下已完成的主題：之後新完成的主題自動加進播放清單（升級後自動可用），
    # 但管理員看過、自己拿掉的不會被加回來
    "ready_seen": list(READY_PAGES),
}


def _hex(v: Any) -> str | None:
    """只收 #RRGGBB（自訂配色不可以注入 CSS 字串）。"""
    import re
    return str(v).lower() if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v) else None


def _clean_intel(v: Any) -> dict[str, Any]:
    from app.services.anomaly import ALL_CATEGORIES

    v = v if isinstance(v, dict) else {}
    out = {**INTEL_DEFAULTS, **{k: v[k] for k in INTEL_DEFAULTS if k in v}}

    def clamp(k: str, lo: int, hi: int) -> int:
        try:
            return min(max(int(out[k]), lo), hi)
        except (TypeError, ValueError):
            return int(INTEL_DEFAULTS[k])
    out["interval_minutes"] = clamp("interval_minutes", 1, 60)
    out["dwell_seconds"] = clamp("dwell_seconds", 10, 300)
    out["item_limit"] = clamp("item_limit", 4, 30)
    for k, allowed in (("layout", INTEL_LAYOUTS), ("playback", INTEL_PLAYBACK), ("speed", INTEL_SPEEDS),
                       ("effects", INTEL_EFFECTS), ("scanlines", INTEL_SCANLINES), ("noise", INTEL_NOISE),
                       ("palette", INTEL_PALETTES)):
        if out.get(k) not in allowed:
            out[k] = INTEL_DEFAULTS[k]
    for k in ("rhythm", "cursor", "background", "mask_ips", "warning_enabled", "warning_breathe"):
        out[k] = bool(out.get(k))
    doms = set(out.get("domains") or []) if isinstance(out.get("domains"), list) else set(INTEL_DOMAINS)
    out["domains"] = [d for d in INTEL_DOMAINS if d in doms]
    colors = out.get("colors") if isinstance(out.get("colors"), dict) else {}
    out["colors"] = {k: _hex(colors.get(k)) or INTEL_DEFAULT_COLORS[k] for k in INTEL_COLOR_KEYS}
    cats = out.get("warning_categories") if isinstance(out.get("warning_categories"), list) else []
    out["warning_categories"] = [c for c in dict.fromkeys(str(x) for x in cats) if c in ALL_CATEGORIES]
    return out


def _clean(v: dict[str, Any]) -> dict[str, Any]:
    """把存的值整理成合法的設定（舊版本少的鍵補預設、亂掉的值退回預設）。"""
    out = {**DEFAULTS, **{k: v[k] for k in DEFAULTS if k in v}}
    seen = v.get("ready_seen") if isinstance(v.get("ready_seen"), list) else list(M1_READY)
    pages = [p for p in (out.get("pages") or []) if p in PAGES]
    if v.get("pages") is not None:
        # 新主題插在預設順序裡它前一個（已在清單中的）主題後面；清單其餘照管理員排的順序
        # （播放清單可拖拉排序，整個重排回預設順序會蓋掉自訂順序，10-22）
        for p in [p for p in AUTO_JOIN_PAGES if p not in seen and p not in pages]:
            before = [q for q in PAGES[:PAGES.index(p)] if q in pages]
            pages.insert(pages.index(before[-1]) + 1 if before else 0, p)
    out["pages"] = list(dict.fromkeys(pages)) or list(AUTO_JOIN_PAGES)
    out["ready_seen"] = list(READY_PAGES)
    sc = out.get("scope") if isinstance(out.get("scope"), dict) else {}
    clean_scope: dict[str, list[str]] = {}
    for kind in ("customers", "sections", "subnets"):
        ids: list[str] = []
        for x in (sc.get(kind) or [])[:MAX_SCOPE]:
            try:
                ids.append(str(uuid.UUID(str(x))))
            except ValueError:
                continue
        clean_scope[kind] = list(dict.fromkeys(ids))
    out["scope"] = clean_scope
    out["dwell_seconds"] = min(max(int(out.get("dwell_seconds") or 20), 5), 600)
    out["max_subpages"] = min(max(int(out.get("max_subpages") or 2), 1), 50)
    out["group_by"] = out["group_by"] if out.get("group_by") in GROUP_BY else "location"
    warn = min(max(int(out.get("capacity_warn") or 80), 1), 99)
    crit = min(max(int(out.get("capacity_crit") or 90), 2), 100)
    out["capacity_warn"], out["capacity_crit"] = (warn, crit) if warn < crit else (80, 90)
    out["language"] = out["language"] if out.get("language") in LANGS else ""
    out["reduce_motion"] = bool(out.get("reduce_motion"))
    if out.get("availability_window") not in AVAILABILITY_WINDOWS:
        out["availability_window"] = "7d"
    if out.get("transition") not in TRANSITIONS:
        out["transition"] = "slide"
    if out.get("number_font") not in NUMBER_FONTS:
        out["number_font"] = "exo2"
    out["ai_summary"] = out["ai_summary"] if out.get("ai_summary") in AI_MODES else "off"
    out["enabled"] = bool(out.get("enabled"))
    out["intel"] = _clean_intel(out.get("intel"))
    out["display_enabled"] = bool(out.get("display_enabled"))
    try:
        out["principal_user_id"] = str(uuid.UUID(str(out["principal_user_id"]))) if out.get("principal_user_id") else None
    except ValueError:
        out["principal_user_id"] = None
    return out


async def _row(session: AsyncSession) -> SystemSetting | None:
    return await session.get(SystemSetting, KEY)


async def get_warroom(session: AsyncSession) -> dict[str, Any]:
    """設定＋顯示權杖狀態（token 是明文，只給程序內用；回給畫面前要拿掉）。"""
    from app.services.system_config import _public_token

    row = await _row(session)
    raw = dict(row.value) if (row and isinstance(row.value, dict)) else {}
    cfg = _clean(raw)
    tok, exp = _public_token(raw, TOKEN_AAD)
    cfg.update({"token": tok, "token_set": bool(tok), "token_expires_at": exp})
    return cfg


def public_view(cfg: dict[str, Any]) -> dict[str, Any]:
    """給設定頁：不含權杖本身。"""
    return {k: v for k, v in cfg.items() if k != "token"}


async def set_warroom(session: AsyncSession, data: dict[str, Any], *,
                      updated_by_user_id: uuid.UUID | None = None) -> dict[str, Any]:
    from sqlalchemy.orm.attributes import flag_modified

    row = await _row(session)
    if row is None:
        row = SystemSetting(key=KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    cur = dict(row.value or {})
    base = _clean(cur)
    patch = {k: v for k, v in data.items() if k in DEFAULTS}
    # AI 情報的設定是一包：只送部分欄位（或沒送）時其餘留原值，不可退回預設
    if isinstance(patch.get("intel"), dict):
        patch["intel"] = {**base["intel"], **patch["intel"]}
    cur.update(_clean({**base, **patch}))
    row.value = cur
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.flush()
    return await get_warroom(session)


async def rotate_display_token(session: AsyncSession, *, days: int,
                               updated_by_user_id: uuid.UUID | None = None) -> dict[str, Any]:
    from sqlalchemy.orm.attributes import flag_modified

    from app.services.system_config import _new_public_token

    row = await _row(session)
    if row is None:
        row = SystemSetting(key=KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    cur = dict(row.value or {})
    _new_public_token(cur, TOKEN_AAD, days, 32)
    row.value = cur
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.flush()
    return await get_warroom(session)


async def revoke_display_token(session: AsyncSession, *, updated_by_user_id: uuid.UUID | None = None) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    row = await _row(session)
    if row is None:
        return
    cur = dict(row.value or {})
    for k in ("token_enc", "token", "token_expires_at"):
        cur.pop(k, None)
    cur["display_enabled"] = False
    row.value = cur
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.flush()


async def get_display_credential(session: AsyncSession) -> dict[str, Any]:
    """給 credential_expiry：與機櫃嵌入同樣的形狀 {enabled, token_set, token_expires_at}。"""
    cfg = await get_warroom(session)
    exp = cfg.get("token_expires_at")
    return {"enabled": cfg["display_enabled"], "token_set": cfg["token_set"],
            "token_expires_at": exp if isinstance(exp, datetime) else None}
