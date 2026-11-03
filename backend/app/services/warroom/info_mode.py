"""戰情室「資訊展示模式」（2026-11-03 使用者：有的單位不允許看到紅字或問題）。

開啟後戰情室只呈現現況與數量，不出現警告、異常、離線、失敗、到期這類內容。整頁都在講問題的主題
（異常與曝露、採集健康、可用性、AI 巡檢）直接不提供；其餘主題的資料在**伺服器端**拿掉問題欄位，
電視收到的封包裡就沒有，不是只靠畫面藏起來（AI 情報的播報文字也沒辦法在前端過濾）。

只影響戰情室：告警、通知、異常偵測與其他頁面照常運作。
"""

from __future__ import annotations

from typing import Any

#: 資訊展示模式下不提供的主題（整頁都是問題；使用者 11-03 決定連看都看不到）
HIDDEN_PAGES = ("anomalies", "collection", "availability", "ai_audit")

#: 標題列的問題類大數字
PROBLEM_METRICS = frozenset({
    "open_anomalies", "capacity_alerts", "high_util_subnets", "pools_alert", "dns_mismatch", "certs_30d",
    "gaps", "agents_stale", "agents_overloaded", "integrations_failing", "down_now", "flapping", "watch",
    "stale_sources", "new_6h", "resolved_6h", "high", "open", "insufficient",
})


def enabled(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("info_mode"))


def shown_pages(cfg: dict[str, Any], pages: list[str]) -> list[str]:
    return [p for p in pages if p not in HIDDEN_PAGES] if enabled(cfg) else list(pages)


def _hidden() -> dict[str, Any]:
    """整段不顯示（畫面不畫這一塊，也不寫「無權限」）。"""
    return {"state": "hidden"}


def _metrics(data: dict[str, Any], extra: dict[str, Any] | None = None) -> None:
    m = {k: v for k, v in (data.get("metrics") or {}).items() if k not in PROBLEM_METRICS}
    data["metrics"] = {**m, **(extra or {})}


def _overview(d: dict[str, Any]) -> None:
    subnets = 0
    for g in d.get("groups") or []:
        g["cells"] = ["ok" for _ in g.get("cells") or []]          # 警告／異常／過期都畫成一般的子網路
        g["counts"] = {"ok": g.get("subnets", 0)}
        subnets += int(g.get("subnets") or 0)
    d["priority"] = _hidden()
    _metrics(d)
    d["metrics"].setdefault("subnets", subnets)


def _capacity(d: dict[str, Any]) -> None:
    for it in d.get("items") or []:
        it["level"] = None
        if it.get("scan") in ("stale", "unscanned"):
            it["scan"] = "ok"
    d.pop("thresholds", None)
    _metrics(d)


def _topology(d: dict[str, Any]) -> None:
    focus = d.get("focus") or {}
    if isinstance(focus.get("device"), dict):
        focus["device"]["anomalies"] = None
    for k in ("up", "down", "links"):
        for n in focus.get(k) or []:
            n["anomalies"] = None
    _metrics(d)


def _services(d: dict[str, Any]) -> None:
    dns = d.get("dns") or {}
    groups = dns.get("total", 0) if dns.get("state") == "ok" else 0
    if dns.get("state") == "ok":
        g = dns.get("group") or {}
        g["status"] = None
        for k in ("diffs", "confirmed"):
            g.pop(k, None)
        for s in g.get("sources") or []:
            total = int(s.get("total") or 0)
            s["counts"] = {"ok": total}                        # 不一致、缺少、待確認都不分
            s["cells"] = ["ok" for _ in s.get("cells") or []]
    pool = d.get("pool") or {}
    pools = pool.get("total", 0) if pool.get("state") == "ok" else 0
    if isinstance(pool.get("item"), dict):
        pool["item"]["level"] = None
    certs = d.get("certs") or {}
    n_certs = None
    if certs.get("state") == "ok":
        n_certs = int(certs.get("all") or 0)
        d["certs"] = {"state": "count", "total": n_certs}      # 只給總數（不給到期日與剩餘天數）
    elif certs.get("state") == "forbidden":
        d["certs"] = certs
    else:
        d["certs"] = {"state": "count", "total": 0}
        n_certs = 0
    _metrics(d, {"dns_groups": groups if dns.get("state") != "forbidden" else None,
                 "pools": pools, "certs": n_certs})
    # 子頁只跟著 DNS 群組與集區走（憑證只剩總數，不再一張一頁）
    d["pages"] = d["total"] = max(1, int(groups or 0), int(pools or 0))
    d["page"] = min(int(d.get("page") or 1), d["pages"])


def _assets(d: dict[str, Any]) -> None:
    # 監控涵蓋整段在講「哪些 VM 沒被監控」、變更評估在講受阻與待覆核 —— 都是問題，整段不給
    d["coverage"] = _hidden()
    d["change"] = _hidden()
    _metrics(d)


def _stats(d: dict[str, Any]) -> None:
    st = d.get("status") or {}
    rows = [r for r in st.get("rows") or [] if r.get("status") == "online"]   # 不顯示離線、未回應
    st["rows"] = rows
    _metrics(d)


def _relations(d: dict[str, Any]) -> None:
    d["pending"] = _hidden()
    focus = d.get("focus") or {}
    focus["conflicts"] = []
    for e in focus.get("edges") or []:
        if e.get("state") == "pending":
            e["state"], e["reason"] = "ok", None
    ip = focus.get("ip") or {}
    if ip.get("status") not in (None, "online"):
        ip["status"] = None
    _metrics(d)


def _ai_intel(d: dict[str, Any]) -> None:
    _metrics(d)


_BY_KEY = {"overview": _overview, "capacity": _capacity, "topology": _topology, "services": _services,
           "assets": _assets, "stats": _stats, "relations": _relations, "ai_intel": _ai_intel}


def neutralize(key: str, data: dict[str, Any]) -> dict[str, Any]:
    """主題資料 → 資訊展示模式的版本（就地修改後回傳；呼叫端給的是剛建好、還沒進快取的資料）。"""
    fn = _BY_KEY.get(key)
    if fn is not None:
        fn(data)
    data["info_mode"] = True
    return data


# ─────────────────────────── AI 情報 ───────────────────────────

#: 播報文字裡不可以出現的字（模型照規則寫還是可能用到；出現就整批重寫，再不行改用系統摘要）
BANNED_WORDS = (
    "異常", "告警", "警告", "警示", "警報", "離線", "失聯", "失敗", "錯誤", "過期", "到期", "逾期", "問題", "風險",
    "衝突", "危險", "嚴重", "緊急", "故障", "中斷", "未授權", "不一致", "缺口", "待處理", "需處理", "需要處理",
    "建議確認", "注意", "WARNING", "CRITICAL",
    "anomal", "alert", "warning", "critical", "offline", "down", "fail", "error", "expire", "risk", "conflict",
    "problem", "issue", "unauthori", "mismatch", "outage",
    "異常", "障害", "警告", "失敗", "期限切れ", "問題", "リスク", "競合", "オフライン",
)


def banned_in(text: str) -> str | None:
    low = text.lower()
    for w in BANNED_WORDS:
        if w.lower() in low:
            return w
    return None
