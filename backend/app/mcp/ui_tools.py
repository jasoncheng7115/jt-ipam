"""AI 對話／MCP：「某個功能、頁籤、欄位在網頁的哪裡」（使用者 2026-10-15）。

起因：問「電路的申請日與到期日要去哪裡查」，AI 說電路沒有這兩個欄位；使用者要求「有人問哪個功能或欄位在哪裡，
要能正確回答」。畫面結構從前端原始碼即時解析（services/ui_map），不是手寫清單，選單改了這裡自動跟上。

不碰任何 IPAM 資料，所有登入帳號都能用；管理員才看得到的頁面會標 admin_only，讓模型說明要找管理員。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def find_ui_location(session: AsyncSession, *, user: User, keywords: list[str] | str | None = None,
                           limit: int = 20) -> dict[str, Any]:
    from app.mcp.tools import IPAMToolError
    from app.services import ui_map
    from app.services.ai import user_locale

    if isinstance(keywords, str):
        keywords = [keywords]
    kws = [str(k).strip() for k in (keywords or []) if str(k).strip()][:12]
    if not kws:
        raise IPAMToolError("keywords is required, e.g. [\"電路\", \"到期\"]")
    try:
        out = ui_map.search(kws, await user_locale(session, user) or "zh-TW", limit=limit,
                            is_admin=bool(getattr(user, "is_admin", False)))
    except (FileNotFoundError, ValueError) as exc:
        # 前端原始碼讀不到（理論上安裝與升級都會有）：明講，不讓模型自己編路徑
        raise IPAMToolError(f"UI map unavailable on this server ({type(exc).__name__})") from exc
    out["keywords"] = kws
    if not out["matches"] and not out["pages"]:
        out["note"] = ("No page, tab or field in the jt-ipam UI matches these words. Try synonyms or the object's name "
                       "(e.g. the page name); do not invent a menu path.")
    return out


UI_TOOLS: dict[str, dict[str, Any]] = {
    "find_ui_location": {
        "fn": find_ui_location,
        "description": (
            "Find WHERE something is in the jt-ipam web UI (menu path, tab, exact label of a field / option / "
            "button / column / setting). Use for any 'where is / how do I get to / where "
            "do I set' question and before saying jt-ipam lacks a field. Pass several keywords incl. synonyms and"
            " the object name; a page name also returns all its fields (map 申請日 → 開通日). admin_only = admins only;"
            " open_a_row = open a record from that list; shown_when = appears after that integration is set up."
            " Never write URLs: `links` become buttons."),
        "parameters": {"type": "object", "properties": {
            "keywords": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 12,
                         "description": "Words to look for in menu items, tabs and field labels (any language)"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 60},
        }, "required": ["keywords"]},
    },
}
