"""「東西壞了卻沒人知道」的三類告警。

資料早就在資料庫裡：整合的 `last_error`、代理的 `last_seen_at`、系統診斷的檢查結果。
問題是**要有人主動去點才看得到**，而實務上沒有人每天去點 —— LibreNMS 的 token 過期、
pfSense 換了 API 金鑰、Proxmox 憑證更新，同步停掉好幾天，IPAM 顯示的還是舊資料，
畫面上一切正常。

三類都走同一套狀態轉換（`services/state_alert`）：只在開始與恢復時發，
中間持續壞著不再吵。逐類別可在「通知發送設定」關掉。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services.notification import push_notification
from app.services.state_alert import observe, prune_missing
from app.services.system_config import get_notification_matrix

EVENT_INTEGRATION = "integration.sync_failed"

# 整合類型 → 設定頁的路由。**不要用 f"/{kind}" 湊**：proxmox / opnsense / windows_dhcp
# 這三個的路由名稱與類型代碼不一樣，湊出來的連結會 404 ——
# 通知點了打不開，比沒有連結更糟（使用者會以為功能壞了）。
_INTEGRATION_ROUTE = {
    "librenms": "/librenms", "wazuh": "/wazuh", "zabbix": "/zabbix", "checkmk": "/checkmk",
    "adguard": "/adguard", "proxmox": "/virt-admin", "esxi": "/esxi", "hyperv": "/hyperv", "openstack": "/openstack",
    "opnsense": "/firewall", "pfsense": "/pfsense", "fortigate": "/fortigate",
    "paloalto": "/paloalto", "checkpoint": "/checkpoint", "checkpoint_gaia": "/checkpoint",
    "mikrotik": "/mikrotik",
    "windows_dhcp": "/windows-dhcp", "kea_dhcp": "/kea-dhcp", "isc_dhcp": "/isc-dhcp",
    "technitium": "/technitium-dhcp",
    "isoinsight": "/isoinsight",
    "rustdesk": "/rustdesk",
    "dns": "/dns", "ocs": "/ocs",
}
EVENT_AGENT = "agent.offline"
EVENT_SYSTEM = "system.health"


async def _admins(session: AsyncSession) -> list[User]:
    return list((await session.execute(
        select(User).where(User.is_admin.is_(True), User.is_active.is_(True))
    )).scalars().all())


async def _notify(
    session: AsyncSession, *, event: str, title: str, body: str, link: str,
    severity: str = "warning",
    title_key: str | None = None, body_key: str | None = None,
    params: dict[str, Any] | None = None,
) -> None:
    """站內通知 + 郵件 + 外部管道。

    `title`／`body` 是**退路**，不是顯示內容：站內通知由前端用 `title_key`／`body_key`
    加 `params` 依當前語言渲染。少給 key 的話，日文與英文的使用者會在通知中心看到中文
    （實際被回報過）。郵件與外部管道沒有「當前語言」可言，仍用寫好的字串。"""
    ch = (await get_notification_matrix(session)).get(
        event, {"in_app": True, "email": False})
    from app.services.system_config import notify_wanted
    if not notify_wanted(ch):
        return
    admins = await _admins(session)
    if ch.get("in_app"):
        for a in admins:
            await push_notification(session, user_id=a.id, severity=severity,
                                    title=title, body=body, link=link,
                                    object_type="system",
                                    title_key=title_key, body_key=body_key, params=params)
    if ch.get("email"):
        from app.services.notification import email_users
        await email_users(session, [a.email for a in admins], f"[jt-ipam] {title}", body, link=link)
    from app.services.notify_channels import broadcast_channels
    await broadcast_channels(session, subject=title, text=body, link=link, severity=severity, event=event)


async def check_integration_health(
    session: AsyncSession, instances: list[tuple[str, Any]], *, threshold: int = 2,
) -> int:
    """整合同步失敗／恢復。`instances` 是 (類型, 實例) —— 實例要有 name 與 last_error。

    門檻預設 2：整合偶爾逾時一次是常態，第一次就吵人的話這個功能會在一週內被關掉。
    """
    sent = 0
    alive: set[str] = set()
    for kind, inst in instances:
        key = f"integration:{kind}:{inst.id}"
        alive.add(key)
        change = await observe(session, key=key, failing=bool(inst.last_error),
                               threshold=threshold)
        if change is None:
            continue
        sent += 1
        if change == "down":
            await _notify(
                session, event=EVENT_INTEGRATION,
                title=f"整合同步失敗：{inst.name}（{kind}）",
                title_key="notif.sync_failed", body_key="notif.sync_failed_body",
                params={"name": inst.name, "kind": kind,
                        "reason": str(inst.last_error)[:300]},
                # 錯誤原文要帶上 —— 「同步失敗」四個字沒有人知道要修什麼
                body=f"{str(inst.last_error)[:300]}",
                link=_INTEGRATION_ROUTE.get(kind, "/dashboard"), severity="error")
        else:
            await _notify(
                session, event=EVENT_INTEGRATION,
                title=f"整合同步已恢復：{inst.name}（{kind}）",
                title_key="notif.sync_ok", body_key="notif.sync_ok_body",
                params={"name": inst.name, "kind": kind},
                body="先前的同步錯誤已排除，最近一次同步成功。",
                link=_INTEGRATION_ROUTE.get(kind, "/dashboard"), severity="info")
    await prune_missing(session, prefix="integration:", alive=alive)
    return sent


# 逐類型的失聯門檻。**兩種代理的回報頻率差了兩個數量級**：
#   * 掃描代理是常駐長輪詢，預設 300 秒一輪 → 30 分鐘沒回報確實不對勁。
#   * 憑證代理是 systemd timer，安裝腳本的預設是 **daily** → 用 30 分鐘判，
#     等於每天把每一台都報一次。實機上就是這樣：17 台憑證代理全部被誤報。
# 給憑證代理兩天：漏掉一次每日執行可能只是重開機，連續兩次沒來才是真的。
_AGENT_MAX_AGE_MINUTES = {
    "scan": 30,
    "cert": 2 * 24 * 60,
}
_AGENT_MAX_AGE_DEFAULT = 24 * 60


async def check_agent_health(
    session: AsyncSession, agents: list[tuple[str, Any]], *,
    max_age_minutes: int | None = None, threshold: int = 1,
) -> int:
    """代理失聯／恢復。看的是**代理自己回報的時間**，不是我們檢查的時間。

    兩個刻意的例外：
    - **從沒回報過**的代理不算失聯 —— 那是剛建好還沒裝上去，每個新代理都先發一則
      假警報只會讓人不信任這個通知。
    - **已停用**的代理不算失聯 —— 停用就是明確表示「現在不該有回報」。
    """
    now = datetime.now(UTC)
    sent = 0
    alive: set[str] = set()
    for kind, ag in agents:
        key = f"agent:{kind}:{ag.id}"
        alive.add(key)
        last = getattr(ag, "last_seen_at", None)
        if not getattr(ag, "enabled", True) or last is None:
            await observe(session, key=key, failing=False, threshold=threshold)
            continue
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        age_min = (now - last).total_seconds() / 60
        limit = (max_age_minutes if max_age_minutes is not None
                 else _AGENT_MAX_AGE_MINUTES.get(kind, _AGENT_MAX_AGE_DEFAULT))
        change = await observe(session, key=key, failing=age_min > limit,
                               threshold=threshold)
        if change is None:
            continue
        sent += 1
        if change == "down":
            await _notify(
                session, event=EVENT_AGENT,
                title=f"代理失聯：{ag.name}（{kind}）",
                title_key="notif.agent_lost", body_key="notif.agent_lost_body",
                params={"name": ag.name, "kind": kind, "minutes": int(age_min)},
                body=(f"最後一次回報是 {int(age_min)} 分鐘前"
                      f"（這類代理的容忍上限是 {limit} 分鐘）。"),
                link="/scan-agents" if kind == "scan" else "/certificates",
                severity="error")
        else:
            await _notify(
                session, event=EVENT_AGENT,
                title=f"代理已恢復回報：{ag.name}（{kind}）",
                title_key="notif.agent_back", body_key="notif.agent_back_body",
                params={"name": ag.name, "kind": kind},
                body="代理已恢復回報。",
                link="/scan-agents" if kind == "scan" else "/certificates",
                severity="info")
    await prune_missing(session, prefix="agent:", alive=alive)
    return sent


async def check_system_health(
    session: AsyncSession, checks: list[Any], *, threshold: int = 1,
) -> int:
    """系統診斷裡的 **bad** 項目。

    `warn` 刻意不吵人：診斷頁上長期有一半是 warn（沒裝 traceroute 之類），
    那些不是「出事了」；把它們也拿來發通知，等於把真正的 bad 埋掉。
    """
    sent = 0
    alive: set[str] = set()
    bad_keys = {c.key for c in checks if getattr(c, "status", "") == "bad"}
    for c in checks:
        key = f"system:{c.key}"
        alive.add(key)
        change = await observe(session, key=key, failing=c.key in bad_keys,
                               threshold=threshold)
        if change is None:
            continue
        sent += 1
        if change == "down":
            body = str(getattr(c, "detail", "") or "")
            fix = str(getattr(c, "fix", "") or "")
            await _notify(
                session, event=EVENT_SYSTEM,
                title=f"系統檢查未通過：{getattr(c, 'title', c.key)}",
                title_key="notif.selfcheck_failed", body_key="notif.selfcheck_failed_body",
                params={"check": str(getattr(c, "title", c.key)), "detail": body,
                        "fix": fix or ""},
                # 「該怎麼修」要一起帶 —— 只說壞了等於沒說（系統診斷頁的原則）
                body=(f"{body}\n處理方式：{fix}" if fix else body),
                link="/doctor", severity="error")
        else:
            await _notify(
                session, event=EVENT_SYSTEM,
                title=f"系統檢查已恢復：{getattr(c, 'title', c.key)}",
                title_key="notif.selfcheck_ok", body_key="notif.selfcheck_ok_body",
                params={"check": str(getattr(c, "title", c.key))},
                body="此檢查項目已恢復通過。", link="/doctor", severity="info")
    await prune_missing(session, prefix="system:", alive=alive)
    return sent
