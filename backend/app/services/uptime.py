"""每日存活狀態（status page 式長條圖用）。

資料有兩層，優先序不同：
1. **逐日觀測**（`ip_liveness_days`）—— 每輪同步實際記下當天看到什麼。有記錄的日子
   一律以它為準，不做任何推估。
2. **狀態轉換推估**（`ip_change_log`）—— 只用在開始逐日記錄之前的舊日子。推估必然
   有極限：沒有轉換就代表「什麼都沒發生」，但那既可能是一直正常，也可能是沒人在看。

回傳的 `observed_from` 是逐日記錄的起點，讓前端可以標出「這天之後是實際觀測」。

以下為原本的重建規則（仍適用於第 2 層）：

我們沒有逐時取樣，只有 `ip_change_log` 裡的**狀態轉換**。重建方式：某段期間的狀態
＝上一筆轉換的 `new_value`，一直持續到下一筆轉換；第一筆轉換之前＝未知。

三個不可妥協的規則（弄錯的話圖會說謊）：
1. **沒有資料的日子是 `unknown`，不是 `up`。** 沒有存活來源（掃描代理／LibreNMS）的
   IP 永遠不會產生轉換 → 整條灰。那是有意義的訊號（「這個 IP 沒在被監測」）。
2. **`uptime_pct` 的分母只算有資料的天數。** 只監測 3 天且全綠的 IP 應該是 100%，
   不是被 87 天灰稀釋後的數字，也不是把灰當中斷算出來的低分。
3. **判斷上線要用 `startswith("online")`** —— `effective_status` 是小寫且帶來源後綴
   （`online (scanner)` / `online (librenms)`）。拿固定字串比對正是 v0.4.196 修過的
   儀表板誤判（上線數從 153 被誤算成 63）。
4. **`online (arp)` 不算「觀測到上線」。** ARP 證據沒有時間概念（LibreNMS 的 ARP API
   不回時間欄位，我們只能因為「這筆還在清單裡」就蓋上同步當下的時間），來源設備的
   ARP 快取不老化的話，機器關掉幾十天也會一直是這個狀態 —— 拿它畫綠色等於說謊。
   這種日子畫成灰色（未知），也不進 `uptime_pct` 的分母。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.address import IPAddress
from app.models.ip_change_log import IPChangeLog
from app.models.ip_liveness import IPLivenessDay
from app.services import evidence

#: 只有 ARP 撐著的上線 —— 不足以宣稱「那天是通的」（見檔頭規則 4）
ARP_ONLY_STATUS = "online (arp)"


def _aging_keys(arp_seen: dict | None) -> set[str]:
    """`arp_seen` 裡**會過期**的來源有哪些（防火牆的 ARP 表／VPN 連線）。

    只有這些能拿來說「這個來源現在還在看著它」；`lease:*` 不算（租約撐好幾天）。
    """
    return {k for k in (arp_seen or {}) if evidence.is_aging(k)}


def carry_forward_ok(
    value: str | None, *, has_scanner: bool, has_librenms: bool,
    present: set[str] | frozenset[str] | None = None,
) -> bool:
    """這筆狀態值可不可以拿來往後延續到沒有觀測的日子。

    規則：**它宣稱的來源現在必須還在**。舊轉換寫著「上線（LibreNMS）」，但這個 IP
    現在連一筆 LibreNMS 證據都沒有，就代表那個來源早已不在看它 —— 拿它宣稱之後
    幾十天都正常，是沒有根據的。實機上正是這樣把一台關著的 VM 畫成 52 天全綠：
    當初撐著它的其實是 ARP（沒有時間概念），而 ARP 從來就不該延續。

    「哪些來源會過期」不在這裡判斷 —— 問 `services/evidence` 的登記表。
    先前這裡是字串比對（`"scanner" in v`），新增來源（例如 zabbix）時會安靜地
    落到最後那個寬鬆分支，等於預設信任一個沒人檢查過的來源。
    """
    if not value:
        return False
    v = value.strip().lower()
    src = evidence.source_from_status(v)
    have = {n for n, ok in (("scanner", has_scanner), ("librenms", has_librenms)) if ok}
    have |= set(present or ())
    if src is not None:
        if not evidence.is_aging(src):
            return False                      # 不會過期的證據不得往後延續
        return src in have
    # 沒有來源後綴的 online／offline：任一會老化的來源還在就可以延續
    return bool(have)


def status_is_up(value: str | None) -> bool | None:
    """`online*` → True、`offline*` → False、其餘（unknown / 空 / 不會過期的來源）→ None。"""
    if not value:
        return None
    v = value.strip().lower()
    if v.startswith("online"):
        src = evidence.source_from_status(v)
        # 來源不會過期（ARP 這種）→ 它說「曾經學到這個對應」，不是「那天活著」
        if src is not None and not evidence.is_aging(src):
            return None
        return True
    if v.startswith("offline"):
        return False
    return None


async def uptime_for_ips(
    session: AsyncSession, ip_ids: list[uuid.UUID], *, days: int = 90,
) -> dict[str, Any]:
    """重建這些 IP 合起來的每日狀態。

    多個 IP（裝置有多個位址）時：**當天任一 IP 曾中斷就標中斷**。與單一 IP 的
    每日規則一致（一天內只要出現過 offline 就算中斷），且傾向浮現問題而非掩蓋。

    ⚠️ **「沒有轉換記錄」不等於「沒在監測」。** 一個從加入以來都沒斷過的 IP
    根本不會產生任何轉換 —— 只看 `ip_change_log` 會把它誤判成整條灰的「未監測」，
    但它其實一直是上線的。所以還要看 IP 目前的 `effective_status` 與 `last_seen_*`：
    有存活來源、且該段期間內沒有任何轉換 → 用目前狀態回填（沒斷過才會沒有轉換）。
    回填起點取 `max(視窗起點, IP 建立時間)`，加入 IPAM 之前仍然是未知。
    """
    today = datetime.now(UTC).date()
    start_day = today - timedelta(days=days - 1)
    start_dt = datetime.combine(start_day, datetime.min.time(), tzinfo=UTC)

    rows: list[tuple[uuid.UUID | None, datetime, str | None]] = []
    if ip_ids:
        rows = list((await session.execute(
            select(IPChangeLog.ip_id, IPChangeLog.created_at, IPChangeLog.new_value)
            .where(
                in_values(IPChangeLog.ip_id, ip_ids),
                IPChangeLog.field == "effective_status",
            )
            .order_by(IPChangeLog.created_at)
        )).all())

    # 目前狀態 / 存活來源 / 建立時間 —— 用來處理「一直沒斷過所以沒有轉換」的 IP
    cur_rows: dict[
        uuid.UUID, tuple[str | None, bool, datetime, bool, bool, set[str]]
    ] = {}
    if ip_ids:
        for i, st, seen_s, seen_l, created, aseen, seen_w, seen_z, seen_c in (await session.execute(
            select(
                IPAddress.id, IPAddress.effective_status,
                IPAddress.last_seen_scanner, IPAddress.last_seen_librenms,
                IPAddress.created_at, IPAddress.arp_seen,
                IPAddress.last_seen_wazuh, IPAddress.last_seen_zabbix, IPAddress.last_seen_checkmk,
            ).where(in_values(IPAddress.id, ip_ids))
        )).all():
            # 「有存活來源」刻意不含 LibreNMS 的 ARP：它沒有時間概念，不能回填整段綠色。
            # 防火牆自己的 ARP／VPN 表會逾時淘汰，算數。Wazuh／Zabbix／Checkmk 也會過期：以前沒列進來，
            # 「上線（zabbix）」的日子永遠不往後延續（2026-11-14 加 Checkmk 時發現）
            extra = _aging_keys(aseen) | {n for n, t in (("wazuh", seen_w), ("zabbix", seen_z),
                                                          ("checkmk", seen_c)) if t}
            cur_rows[i] = (st, bool(seen_s or seen_l or extra), created,
                           bool(seen_s), bool(seen_l), extra)

    # 逐日觀測（有記錄的日子一律以它為準）
    obs: dict[uuid.UUID, dict[date, dict[str, bool]]] = {}
    observed_from: date | None = None
    if ip_ids:
        for oid, oday, oup, odown, oarp in (await session.execute(
            select(IPLivenessDay.ip_id, IPLivenessDay.day, IPLivenessDay.up,
                   IPLivenessDay.down, IPLivenessDay.arp_only)
            .where(in_values(IPLivenessDay.ip_id, ip_ids), IPLivenessDay.day >= start_day)
        )).all():
            obs.setdefault(oid, {})[oday] = {"up": oup, "down": odown, "arp_only": oarp}
            if observed_from is None or oday < observed_from:
                observed_from = oday

    # 每個 IP 各自跑一條時間線，最後再逐日合併
    per_ip_days: list[dict[date, dict[str, bool]]] = []
    monitored = False
    for ip_id in ip_ids:
        evs = [(ts, nv) for (i, ts, nv) in rows if i == ip_id]
        state: bool | None = None
        state_val: str | None = None
        for ts, nv in evs:
            if ts < start_dt:
                state = status_is_up(nv)
                state_val = nv
            else:
                break
        by_day: dict[date, list[str | None]] = {}
        for ts, nv in evs:
            if ts >= start_dt:
                by_day.setdefault(ts.date(), []).append(nv)

        (cur_status, has_live_source, created_at,
         has_scanner, has_lnms, has_extra) = cur_rows.get(
            ip_id, (None, False, start_dt, False, False, set()))
        if has_live_source:
            monitored = True
        # 整段視窗都沒有轉換、但確實有存活來源 → 用目前狀態回填。
        # 沒斷過才會沒有轉換，所以「現在是什麼狀態」就是這整段的狀態。
        backfill_from: date | None = None
        if not evs and has_live_source and status_is_up(cur_status) is not None:
            state = status_is_up(cur_status)
            state_val = cur_status
            backfill_from = max(start_day, created_at.date())

        flags: dict[date, dict[str, bool]] = {}
        cur = state
        cur_val = state_val
        for i in range(days):
            d = start_day + timedelta(days=i)
            # 回填情形下，加入 IPAM 之前仍然是未知
            if backfill_from is not None and d < backfill_from:
                flags[d] = {"up": False, "down": False}
                continue
            # 沒有任何**會老化**的證據來源（掃描代理／LibreNMS 裝置狀態）時，不准把
            # 上一筆轉換的狀態一路往後填 —— 那等於拿一筆幾十天前的舊記錄宣稱「這段期間
            # 都是通的」。只有 ARP 撐著的 IP 正是這種情況（ARP 沒有時間概念）。
            rec = obs.get(ip_id, {}).get(d)
            if rec is not None:
                # 有實際觀測 → 直接用，不推估（arp_only 不算可用）
                flags[d] = {"up": rec["up"], "down": rec["down"]}
                if rec["up"]:
                    cur = True
                elif rec["down"]:
                    cur = False
                continue
            carry = carry_forward_ok(cur_val, has_scanner=has_scanner,
                                     has_librenms=has_lnms, present=has_extra)
            up = (cur is True) if carry else False
            down = (cur is False) if carry else False
            for nv in by_day.get(d, []):
                cur = status_is_up(nv)
                cur_val = nv
                if cur is True:
                    up = True
                elif cur is False:
                    down = True
            flags[d] = {"up": up, "down": down}
        per_ip_days.append(flags)

    items: list[dict[str, str]] = []
    known = down_days = 0
    for i in range(days):
        d = start_day + timedelta(days=i)
        any_down = any(f[d]["down"] for f in per_ip_days)
        any_up = any(f[d]["up"] for f in per_ip_days)
        # 「整天全掛」與「當天有斷有通」要分開：連續一個月的離線若全部畫成
        # 「曾中斷」，看起來會像 30 次短暫中斷，而不是一次持續的離線。
        if any_down and any_up:
            st = "partial"
            known += 1
            down_days += 1
        elif any_down:
            st = "down"
            known += 1
            down_days += 1
        elif any_up:
            st = "up"
            known += 1
        else:
            st = "unknown"
        items.append({"date": d.isoformat(), "status": st})

    return {
        "days": days,
        "items": items,
        # 分母只算有資料的天數；完全沒資料回 None，前端顯示「尚無資料」而不是 0%／100%
        "uptime_pct": (round((known - down_days) / known * 100, 3) if known else None),
        "known_days": known,
        "down_days": down_days,
        # 這天（含）之後是實際逐日觀測；之前只能依狀態轉換推估
        "observed_from": observed_from.isoformat() if observed_from else None,
        # 有轉換記錄、或目前就有存活來源（掃描代理／LibreNMS），都算「有在監測」
        "has_source": bool(rows) or monitored,
    }


async def uptime_batch(
    session: AsyncSession, ip_ids: list[uuid.UUID], *, days: int = 90,
) -> list[dict[str, Any]]:
    """一次算多個 IP，**每個 IP 各一條**（儀表板區塊用）。

    與 `uptime_for_ips` 的差別：那支是把多個 IP 合併成一條（裝置的多個位址算同一台
    機器）；這支是每個 IP 獨立一條。

    刻意只打兩次 DB（轉換記錄一次、目前狀態一次）再在記憶體裡分組 —— 30 個 IP
    各自呼叫 `uptime_for_ips` 會變成 60 次查詢。
    """
    if not ip_ids:
        return []

    today = datetime.now(UTC).date()
    start_day = today - timedelta(days=days - 1)
    start_dt = datetime.combine(start_day, datetime.min.time(), tzinfo=UTC)

    ev_rows = list((await session.execute(
        select(IPChangeLog.ip_id, IPChangeLog.created_at, IPChangeLog.new_value)
        .where(in_values(IPChangeLog.ip_id, ip_ids), IPChangeLog.field == "effective_status")
        .order_by(IPChangeLog.created_at)
    )).all())
    by_ip: dict[uuid.UUID, list[tuple[datetime, str | None]]] = {}
    for i, ts, nv in ev_rows:
        if i is not None:
            by_ip.setdefault(i, []).append((ts, nv))

    # 逐日觀測（與 uptime_for_ips 同一份資料，避免儀表板與詳細資料頁講不同的話）
    obs: dict[uuid.UUID, dict[date, dict[str, bool]]] = {}
    observed_from: date | None = None
    if ip_ids:
        for oid, oday, oup, odown, oarp in (await session.execute(
            select(IPLivenessDay.ip_id, IPLivenessDay.day, IPLivenessDay.up,
                   IPLivenessDay.down, IPLivenessDay.arp_only)
            .where(in_values(IPLivenessDay.ip_id, ip_ids), IPLivenessDay.day >= start_day)
        )).all():
            obs.setdefault(oid, {})[oday] = {"up": oup, "down": odown, "arp_only": oarp}
            if observed_from is None or oday < observed_from:
                observed_from = oday

    meta: dict[
        uuid.UUID,
        tuple[str, str | None, str | None, bool, datetime, bool, bool, set[str]]
    ] = {}
    for i, ipv, host, st, seen_s, seen_l, created, aseen in (await session.execute(
        select(
            IPAddress.id, IPAddress.ip, IPAddress.hostname, IPAddress.effective_status,
            IPAddress.last_seen_scanner, IPAddress.last_seen_librenms,
            IPAddress.created_at, IPAddress.arp_seen,
        ).where(in_values(IPAddress.id, ip_ids))
    )).all():
        extra = _aging_keys(aseen)
        meta[i] = (str(ipv).split("/")[0], host, st, bool(seen_s or seen_l or extra),
                   created, bool(seen_s), bool(seen_l), extra)

    out: list[dict[str, Any]] = []
    for ip_id in ip_ids:                      # 依使用者排的順序回，不用 DB 順序
        if ip_id not in meta:
            continue
        (ip_text, hostname, cur_status, has_live_source, created_at,
         has_scanner, has_lnms, has_extra) = meta[ip_id]
        evs = by_ip.get(ip_id, [])

        state: bool | None = None
        state_val: str | None = None
        for ts, nv in evs:
            if ts < start_dt:
                state = status_is_up(nv)
                state_val = nv
            else:
                break
        day_events: dict[date, list[str | None]] = {}
        for ts, nv in evs:
            if ts >= start_dt:
                day_events.setdefault(ts.date(), []).append(nv)

        # 與 uptime_for_ips 相同：沒有轉換不代表沒在監測（從沒斷過就不會有轉換）
        backfill_from: date | None = None
        if not evs and has_live_source and status_is_up(cur_status) is not None:
            state = status_is_up(cur_status)
            state_val = cur_status
            backfill_from = max(start_day, created_at.date())

        items: list[dict[str, str]] = []
        known = down_days = 0
        cur = state
        cur_val = state_val
        for n in range(days):
            d = start_day + timedelta(days=n)
            if backfill_from is not None and d < backfill_from:
                items.append({"date": d.isoformat(), "status": "unknown"})
                continue
            rec = obs.get(ip_id, {}).get(d)
            if rec is not None:
                # 有實際觀測 → 直接用，不推估
                if rec["up"] and rec["down"]:
                    st2, known, down_days = "partial", known + 1, down_days + 1
                elif rec["down"]:
                    st2, known, down_days = "down", known + 1, down_days + 1
                elif rec["up"]:
                    st2, known = "up", known + 1
                else:
                    st2 = "unknown"
                if rec["up"]:
                    cur = True
                elif rec["down"]:
                    cur = False
                items.append({"date": d.isoformat(), "status": st2})
                continue
            # 與 uptime_for_ips 同一條規則：狀態值宣稱的來源現在還在，才可以往後延續
            carry = carry_forward_ok(cur_val, has_scanner=has_scanner,
                                     has_librenms=has_lnms, present=has_extra)
            up = (cur is True) if carry else False
            down = (cur is False) if carry else False
            for nv in day_events.get(d, []):
                cur = status_is_up(nv)
                cur_val = nv
                if cur is True:
                    up = True
                elif cur is False:
                    down = True
            if up and down:
                st2 = "partial"
                known += 1
                down_days += 1
            elif down:
                st2 = "down"
                known += 1
                down_days += 1
            elif up:
                st2 = "up"
                known += 1
            else:
                st2 = "unknown"
            items.append({"date": d.isoformat(), "status": st2})

        out.append({
            "ip_id": str(ip_id),
            "ip": ip_text,
            "hostname": hostname,
            "days": days,
            "items": items,
            "uptime_pct": (round((known - down_days) / known * 100, 3) if known else None),
            "known_days": known,
            "down_days": down_days,
            "observed_from": observed_from.isoformat() if observed_from else None,
            "has_source": bool(evs) or has_live_source,
        })
    return out
