"""調查模式：把一個位址散落在各處的線索收成一份檔案。

查一台機器現在要在六個頁面之間跳。這幾天追「macOS 掛到 Linux VM」與「ping 得到卻顯示
離線」兩個問題，都是這樣一頁一頁翻出來的 —— IP 詳細資料、Wazuh、DNS、NAT、ARP、
異動記錄。人做得到，但慢，而且很容易漏掉關鍵的那一項。

**這裡只收事實**，不做推論。要不要請模型解讀是呼叫端的事，兩者刻意分開：檔案本身可以
單獨使用，而模型的敘述永遠標示為推測（延續異常偵測與 AI 巡檢那條線）。

權限依專案的三類資料分層：
- 位址本身是逐物件資料 → 看不到那個子網路，就連「這個位址存在」都不揭露
- NAT／防火牆／DNS 是全域基礎設施 → 只有具全域讀取權限者才附上那幾段
  （**降級而不是整個擋掉** —— 部門帳號對自己的機器仍該查得到基本狀況）
- 稽核、異常偵測、AI 巡檢、探測結果是管理資料 → 只有管理員（REST 與 MCP 本來就是 require_admin）

## 2026-10-05 加強：每個整合知道什麼都收進來

使用者看著一台 Windows 主機的調查視窗：「我們現在整合不少東西，例如 OCS，調查功能應該也要配合加強」。

| 段落 | 讀什麼 | 誰看得到 |
|---|---|---|
| identity | ip_addresses 的 device_kind／model／os_*；類型的依據（device_identity.ipam_facts＋resolve_kind）；oui_vendors | 逐物件 |
| agents.ocs | ip_addresses 的 os_ocs／ocs_*（OCS 盤點寫在 IP 記錄上，沒有另外的電腦表） | 逐物件 |
| agents.rustdesk | rustdesk_peers（address_id／candidate_address_id）＋ rustdesk.key_problem | 逐物件 |
| monitoring | wazuh_agents、librenms_devices（裝置關聯或 primary_ip）、zabbix_hosts | 逐物件 |
| virtualization | vm_interfaces／virtual_machines（fw_lookup.vm_match_for：兩邊 MAC 都知道而不同就不算） | 逐物件 |
| dhcp | dhcp_reservations、dhcp_lease_sightings、dhcp_pool_ranges＋手動集區（範圍的設定本身只給全域讀取） | 逐物件 |
| switch_ports | fdb_entries（LibreNMS 與 MikroTik 兩種列，mac_history.switch_ports_for_mac） | 依交換器的裝置可見性 |
| fw_evidence／last_seen | ip_addresses.arp_seen 與各 last_seen_*；上線判定規則 system_config.get_liveness_config | 逐物件 |
| dns／nat／firewall_rules／firewall_refs／dhcp_rogue | dns_records、nat_translations、各家防火牆（fw_lookup.rules_touching_ip）、dhcp_sightings | 全域讀取 |
| probe | agent_probe_jobs（identify，最近一次完成的）＋ ip_identify.summarize | 管理員 |
| anomalies／ai_findings | 異常偵測最近一次的結果（system_settings）、ai_findings（未處理的） | 管理員 |
| console_sessions | audit_logs（SSH／SFTP／RDP／VNC／noVNC／BMC／RustDesk 網頁連線的開啟紀錄） | 管理員 |

規則：
- **每一段包在自己的 SAVEPOINT＋try 裡**：某個整合沒設定、資料壞了或 SQL 出錯，那一段就空著；
  只用 try 不夠 —— SQL 錯誤會讓整個交易進入 aborted 狀態，後面每一段都跟著失敗。
- **沒有資料的段落不放進檔案**（畫面不留空標題，給模型的版本也短一些）。原本就有的幾段
  （hostname_sources、arp、dns…）維持原樣，空的時候是空清單。
- 每段有上限（MAX_ROWS），查詢都走索引或天然很小的表（防火牆設定、DHCP 範圍）。
- `conflicts`（彼此矛盾的線索）在這裡算一次：畫面、四種匯出與 AI 判讀拿到的是同一份。
"""

from __future__ import annotations

import importlib
import ipaddress
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.os_fingerprint import wazuh_os_display
from app.core.sqlin import in_values
from app.models.address import IPAddress
from app.models.subnet import Subnet
from app.models.user import User
from app.services.permission import visible_ids

logger = logging.getLogger(__name__)

MAX_ROWS = 20          # 每一段最多列幾筆（檔案是給人讀的，不是傾印資料庫）
CHANGE_DAYS = 90
CONSOLE_ROWS = 10      # 主控台連線列最近幾筆
#: 給模型的版本：每個清單最多幾筆、每段文字最多幾個字（超過 num_ctx 會被靜靜截斷，見 prompt_view）
PROMPT_ROWS = 6
PROMPT_TEXT = 300

#: 主控台「開啟連線」寫的稽核動作 → 種類（見 api/v1/endpoints/*_console.py）
_CONSOLE_ACTIONS = ("ssh.session_open", "sftp_open", "rdp.session_open", "vnc.session_open",
                    "novnc.session_open", "bmc.session_open", "rustdesk.web_session_open",
                    # 用本機的 RustDesk 客戶端開啟：連線不經 jt-ipam，只知道誰在什麼時候按了
                    "rustdesk.local_client_open")
_CONSOLE_KIND = {a: a.split(".")[0].split("_")[0] for a in _CONSOLE_ACTIONS}
_CONSOLE_KIND["rustdesk.local_client_open"] = "rustdesk_local"
#: 結束連線的稽核動作 → 種類（帶 duration_seconds；本機 RustDesk 客戶端不經 jt-ipam，沒有結束記錄）
_CONSOLE_CLOSE_KIND = {"ssh.session_close": "ssh", "sftp_close": "sftp", "rdp.session_close": "rdp",
                       "vnc.session_close": "vnc", "novnc.session_close": "novnc", "bmc.session_close": "bmc",
                       "rustdesk.web_session_close": "rustdesk"}
_CONSOLE_CLOSE_ACTIONS = tuple(_CONSOLE_CLOSE_KIND)

#: DHCP 租約／固定分配的 source_type → 整合的資料表（只用來把 id 換成名稱）
_DHCP_SOURCE_MODELS: dict[str, tuple[str, str]] = {
    "opnsense": ("app.models.firewall", "OPNsenseFirewall"),
    "pfsense": ("app.models.pfsense", "PfSenseFirewall"),
    "fortigate": ("app.models.fortigate", "FortiGateFirewall"),
    "paloalto": ("app.models.paloalto", "PaloAltoFirewall"),
    "mikrotik": ("app.models.mikrotik", "MikroTikRouter"),
    "windows_dhcp": ("app.models.windows_dhcp", "WindowsDhcpServer"),
    "kea_dhcp": ("app.models.dhcp_standalone", "KeaDhcpServer"),
    "isc_dhcp": ("app.models.dhcp_standalone", "IscDhcpServer"),
    "isoinsight": ("app.models.isoinsight", "IsoInsightSource"),
    "technitium": ("app.models.technitium", "TechnitiumDhcpServer"),
    # Check Point：租約與發放範圍來自閘道的 Gaia API（第二階段），source_id 是那台閘道的連線設定
    "checkpoint": ("app.models.checkpoint_gaia", "CheckPointGaiaTarget"),
}

#: 異常偵測的一筆發現裡，調查要帶出來的欄位（其餘是畫面專用或太長）
_ANOMALY_FIELDS = ("kind", "mac", "macs", "hostname", "port", "field", "old", "new", "count",
                   "server_ip", "device_name", "name", "detail_key", "first_at", "last_at", "last_seen_at")


def _dt(v: Any) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else None


def _parse_dt(v: Any) -> datetime | None:
    """arp_seen 裡存的是 ISO 字串。"""
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    try:
        d = datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _text(v: Any, n: int = 200) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s[:n] or None


def _compact(d: dict[str, Any]) -> dict[str, Any]:
    """拿掉沒有值的欄位（None、空字串、空清單）。False 與 0 是事實，保留。"""
    return {k: v for k, v in d.items() if v is not None and v != "" and v != [] and v != {}}


async def _global_read(session: AsyncSession, user: User) -> bool:
    if getattr(user, "is_admin", False):
        return True
    for ot in ("subnet", "device", "customer", "section", "rack", "location"):
        if await visible_ids(session, user=user, object_type=ot) is None:
            return True
    return False


@dataclass
class _Ctx:
    session: AsyncSession
    user: User
    ip: str
    ipa: IPAddress
    subnet: Subnet
    gread: bool
    admin: bool
    #: 只被授權部分子網路（visible_ids 不是 None）：沒有子網路歸屬的資料要看重疊網段
    restricted: bool = False

    @property
    def mac(self) -> str | None:
        return str(self.ipa.mac) if self.ipa.mac else None


Section = Callable[[_Ctx], Awaitable[Any]]


async def _safe(c: _Ctx, name: str, fn: Section) -> Any:
    """跑一段；壞了就回 None（記日誌），不影響其他段落。SAVEPOINT 讓 SQL 錯誤不會毒到後面的查詢。"""
    try:
        async with c.session.begin_nested():
            return await fn(c)
    except Exception:
        logger.warning("investigate: section %s failed for %s", name, c.ip, exc_info=True)
        return None


# ═══════════════════════════ 逐物件：原本就有的幾段 ═══════════════════════════

async def _sec_hostname_sources(c: _Ctx) -> list[dict[str, Any]]:
    """主機名稱各來源分別回報了什麼（名稱對不上多半是張冠李戴的第一個徵兆）。"""
    from app.models.ip_hostname import IPHostnameObservation
    from app.services.hostname_reports import reports_by_source
    reports = await reports_by_source(c.session, c.ipa.id)
    return [
        {"source": o.source, "hostname": o.hostname, "seen_at": _dt(o.observed_at),
         # 哪一台回報的、最後一次什麼時候（observed_at 只在名稱改變時才動）
         "reports": reports.get(o.source, [])[:MAX_ROWS]}
        for o in (await c.session.execute(
            select(IPHostnameObservation).where(IPHostnameObservation.ip_id == c.ipa.id)
            .limit(MAX_ROWS)
        )).scalars().all()
    ]


async def _sec_os_candidates(c: _Ctx) -> dict[str, str]:
    from app.services.os_precedence import _candidates
    return await _candidates(c.session, c.ipa)


async def _sec_wazuh(c: _Ctx) -> dict[str, Any] | None:
    from app.models.wazuh import WazuhAgent
    from app.services.wazuh import agent_represents_ip
    wa = (await c.session.execute(
        select(WazuhAgent).where(WazuhAgent.jt_ipam_address_id == c.ipa.id)
        .order_by(WazuhAgent.last_keep_alive.desc().nulls_last()).limit(1)
    )).scalars().first()
    if wa is None:
        return None
    return {
        "agent_id": wa.agent_id, "name": wa.name, "status": wa.status,
        "os": wazuh_os_display(wa.os_name, wa.os_platform, wa.os_version),
        "last_keep_alive": _dt(wa.last_keep_alive),
        "sca_score": wa.sca_score, "sca_policy": wa.sca_policy,
        # 失聯 agent 的登記可能是舊的（DHCP 位址被回收給別台）
        "still_represents_this_ip": agent_represents_ip(wa, c.ipa),
    }


async def _sec_librenms(c: _Ctx) -> dict[str, Any] | None:
    """以前只認裝置關聯：IP 沒掛裝置時，LibreNMS 明明在監控它（primary_ip 就是它），調查卻說沒有。"""
    from app.models.librenms import LibreNMSDevice
    conds = [LibreNMSDevice.primary_ip == c.ip]
    if c.ipa.device_id:
        conds.append(LibreNMSDevice.jt_ipam_device_id == c.ipa.device_id)
    rows = (await c.session.execute(select(LibreNMSDevice).where(or_(*conds)).limit(5))).scalars().all()
    if not rows:
        return None
    # 裝置關聯（人工或匯入確認過的）優先，其次才是位址
    linked = [r for r in rows if c.ipa.device_id and r.jt_ipam_device_id == c.ipa.device_id]
    ln = linked[0] if linked else rows[0]
    return _compact({
        "hostname": ln.hostname, "sysname": ln.sysname, "os": ln.os, "status": ln.status,
        "hardware": ln.hardware, "type": ln.type, "last_seen_at": _dt(ln.last_seen_at),
        "matched_by": "device" if linked else "primary_ip",
    })


async def _sec_zabbix(c: _Ctx) -> list[dict[str, Any]] | None:
    from app.models.zabbix import ZabbixHost, ZabbixInstance
    rows = (await c.session.execute(
        select(ZabbixHost, ZabbixInstance.name)
        .join(ZabbixInstance, ZabbixInstance.id == ZabbixHost.instance_id)
        # 依位址比對時，已經對到別筆記錄（重疊網段的另一個）的不算
        .where(or_(ZabbixHost.jt_ipam_address_id == c.ipa.id,
                   and_(ZabbixHost.ip == c.ip, ZabbixHost.jt_ipam_address_id.is_(None))))
        .order_by(ZabbixHost.last_seen_at.desc().nulls_last()).limit(5)
    )).all()
    return [_compact({
        "name": h.name or h.host, "host": h.host, "status": h.status, "available": h.available,
        "maintenance": bool(h.maintenance), "last_seen_at": _dt(h.last_seen_at), "instance": iname,
    }) for h, iname in rows] or None


async def _sec_checkmk(c: _Ctx) -> list[dict[str, Any]] | None:
    from app.models.checkmk import CheckmkHost, CheckmkInstance
    rows = (await c.session.execute(
        select(CheckmkHost, CheckmkInstance.name)
        .join(CheckmkInstance, CheckmkInstance.id == CheckmkHost.instance_id)
        .where(or_(CheckmkHost.jt_ipam_address_id == c.ipa.id,
                   and_(CheckmkHost.ip == c.ip, CheckmkHost.jt_ipam_address_id.is_(None))))
        .order_by(CheckmkHost.last_seen_at.desc().nulls_last()).limit(5)
    )).all()
    return [_compact({
        "name": h.alias or h.host_name, "host": h.host_name, "state": h.state, "downtime": bool(h.downtime),
        "folder": h.folder, "last_check": _dt(h.last_check), "last_seen_at": _dt(h.last_seen_at), "instance": iname,
    }) for h, iname in rows] or None


async def _sec_arp(c: _Ctx) -> list[dict[str, Any]]:
    """ARP：這個位址在網路上被哪些 MAC 用過（換過 MAC 常代表換了機器）。

    只被授權部分子網路的帳號：只看這個子網路的列；沒有子網路歸屬的（LibreNMS）要這個子網路是唯一最細的容器 ——
    以前依位址撈全部，重疊網段裡另一個單位同一個位址的 MAC 也給（2026-11-03 稽核；規則同 get_ip_history）。"""
    from app.models.librenms import ARPEntry
    stmt = select(ARPEntry.mac, ARPEntry.last_seen_at).where(ARPEntry.ip == c.ip)
    if c.restricted:
        from app.services.unmanaged import sole_container
        sole = await sole_container(c.session, c.ipa.subnet_id, c.ip)
        stmt = stmt.where(or_(ARPEntry.subnet_id == c.ipa.subnet_id,
                              and_(ARPEntry.subnet_id.is_(None), literal(sole))))
    return [
        {"mac": str(m), "last_seen_at": _dt(t)}
        for m, t in (await c.session.execute(
            stmt.order_by(ARPEntry.last_seen_at.desc()).limit(MAX_ROWS))).all()
    ]


async def _sec_changes(c: _Ctx) -> list[dict[str, Any]]:
    """異動記錄：這個位址最近被改過什麼。"""
    from app.models.ip_change_log import IPChangeLog
    return [
        {"event": x.event_type, "field": x.field, "old": x.old_value, "new": x.new_value,
         "at": _dt(x.created_at), "source": x.source}
        for x in (await c.session.execute(
            select(IPChangeLog)
            .where(IPChangeLog.ip_id == c.ipa.id,
                   IPChangeLog.created_at >= datetime.now(UTC) - timedelta(days=CHANGE_DAYS))
            .order_by(IPChangeLog.created_at.desc()).limit(MAX_ROWS)
        )).scalars().all()
    ]


# ═══════════════════════════ 逐物件：新加的段落 ═══════════════════════════

async def _sec_identity(c: _Ctx) -> dict[str, Any] | None:
    """這是什麼設備：類型、型號、OS，以及類型是依什麼決定的。

    `kind_reason` 是 device_identity.resolve_kind 的依據（`librenms:…`、`wazuh:linux`、`virt:pve-node`、
    `hostname:…`）；沒有＝沿用探測／定期偵測的結論。依 IPAM 現在的事實判讀出的類型跟記錄上的不同時
    另外帶 `kind_by_facts`（下一次偵測就會改成那個）。
    """
    from app.services.device_identity import ipam_facts, resolve_kind
    from app.services.mac_history import is_random_mac

    ipa = c.ipa
    facts = await ipam_facts(c.session, ipa)
    kind, reason = resolve_kind(ipa.device_kind, facts, os_family=ipa.os_family)
    random = is_random_mac(c.mac) if c.mac else None
    out = _compact({
        "device_kind": ipa.device_kind, "device_model": ipa.device_model,
        "os_family": ipa.os_family, "os_guess": ipa.os_guess,
        "identified_at": _dt(ipa.device_identified_at),
        "kind_reason": reason,
        "kind_by_facts": kind if reason and kind != ipa.device_kind else None,
        "nic_vendor": facts.nic_vendor,
        "mac_random": random,
        "virtual_guest": facts.guest_kind,
        "agent_os": f"{facts.agent_source}:{facts.agent_family}" if facts.agent_family else None,
    })
    meaningful = {"device_kind", "device_model", "os_family", "os_guess", "kind_reason", "nic_vendor",
                  "virtual_guest", "agent_os"}
    return out if (meaningful & out.keys() or random) else None


async def _sec_ocs(c: _Ctx) -> dict[str, Any] | None:
    """OCS Inventory 對這個 IP 的盤點（寫在 IP 記錄上：os_ocs、ocs_hw、ocs_notes…）。"""
    ipa = c.ipa
    if not (ipa.os_ocs or ipa.ocs_id or ipa.last_seen_ocs or ipa.ocs_hw):
        return None
    hw = ipa.ocs_hw if isinstance(ipa.ocs_hw, dict) else {}
    system = hw.get("system") or {}
    cpus = [x for x in (hw.get("cpus") or []) if isinstance(x, dict) and x.get("model")]
    mem = hw.get("memory") or {}
    disks = [x for x in (hw.get("disks") or []) if isinstance(x, dict)]
    notes = [n for n in (ipa.ocs_notes or []) if isinstance(n, dict)]
    return _compact({
        "ocs_id": ipa.ocs_id, "tag": ipa.ocs_tag, "agent": ipa.ocs_agent, "os": ipa.os_ocs,
        "last_inventory": _dt(ipa.last_seen_ocs),
        "system": " ".join(str(x) for x in (system.get("vendor"), system.get("model")) if x) or None,
        "serial": system.get("serial") or (hw.get("board") or {}).get("serial"),
        "cpu": ", ".join(str(x["model"]) + (f" x{x['count']}" if (x.get("count") or 1) > 1 else "")
                         for x in cpus[:4]) or None,
        "memory_mb": mem.get("total_mb") if isinstance(mem, dict) else None,
        "disks": [_compact({"model": _text(x.get("model"), 80), "size_mb": x.get("size_mb")})
                  for x in disks[:6]],
        "notes": [_compact({"date": _text(n.get("date"), 32), "user": _text(n.get("user"), 64),
                            "comment": _text(n.get("comment"), 200)}) for n in notes[:3]],
    })


async def _sec_rustdesk(c: _Ctx) -> list[dict[str, Any]] | None:
    """RustDesk：對應到這個 IP 的 ID（含只有主機名稱相符、只當建議的那些）。"""
    from app.models.rustdesk import RustDeskPeer, RustDeskServer
    from app.services.rustdesk import key_problem

    rows = (await c.session.execute(
        select(RustDeskPeer, RustDeskServer.name)
        .join(RustDeskServer, RustDeskServer.id == RustDeskPeer.server_id)
        .where(or_(RustDeskPeer.address_id == c.ipa.id, RustDeskPeer.candidate_address_id == c.ipa.id))
        .order_by(RustDeskPeer.online.desc(), RustDeskPeer.last_online_at.desc().nulls_last())
        .limit(MAX_ROWS)
    )).all()
    out = []
    for p, sname in rows:
        kp = key_problem(p)
        out.append(_compact({
            "id": p.rustdesk_id, "server": sname, "online": bool(p.online),
            "last_online_at": _dt(p.last_online_at), "last_heartbeat_at": _dt(p.last_heartbeat_at),
            "os": p.os_name, "hostname": p.hostname, "username": p.username, "version": p.client_version,
            "match_status": p.match_status, "candidate": p.address_id != c.ipa.id,
            "evidence": list(p.match_evidence or [])[:5],
            # Key 設錯：區網直連正常，網頁連線與外網連線都會失敗（rustdesk.key_problem）
            "key_problem": kp is not None,
            "key_problem_at": _dt(kp["at"]) if kp else None,
            "key_problem_count": kp["count"] if kp else None,
        }))
    return out or None


async def _sec_virtualization(c: _Ctx) -> dict[str, Any] | None:
    """虛擬化平台回報的哪一台 VM／容器就是它。

    比對沿用 fw_lookup.vm_match_for（VM 網卡的 IP 或 MAC；只有 IP 對到而兩邊 MAC 都知道卻不同 ＝
    DHCP 位址換了主人，不算）。對不到時再看同步寫下的 primary_ip_id，同一條 MAC 規則照樣適用。
    """
    from app.models.virt import VirtCluster, VirtualMachine, VMInterface
    from app.services.fw_lookup import vm_match_for

    m = await vm_match_for(c.session, ip=c.ip, macs=[c.mac] if c.mac else None)
    if m is not None:
        return _compact(m)
    vm = (await c.session.execute(
        select(VirtualMachine).where(VirtualMachine.primary_ip_id == c.ipa.id).limit(1)
    )).scalars().first()
    if vm is None:
        return None
    if c.mac:
        nic_macs = {str(x).lower() for x in (await c.session.execute(
            select(VMInterface.mac).where(VMInterface.vm_id == vm.id, VMInterface.mac.is_not(None)).limit(16)
        )).scalars().all()}
        if nic_macs and c.mac.lower() not in nic_macs:
            return None
    cluster = await c.session.get(VirtCluster, vm.cluster_id)
    return _compact({"vm": vm.name, "cluster": cluster.name if cluster else None,
                     "platform": cluster.type if cluster else None, "kind": vm.kind,
                     "node": vm.node, "status": vm.status, "vmid": vm.legacy_vmid,
                     "matched_by": "primary_ip_id"})


async def _source_names(session: AsyncSession, pairs: Iterable[tuple[str, Any]]) -> dict[tuple[str, str], str]:
    """(source_type, source_id) → 整合名稱（DHCP 租約只記 id）。"""
    by_type: dict[str, set[Any]] = defaultdict(set)
    for st, sid in pairs:
        if st in _DHCP_SOURCE_MODELS and sid is not None:
            by_type[st].add(sid)
    out: dict[tuple[str, str], str] = {}
    for st, ids in by_type.items():
        mod, cls = _DHCP_SOURCE_MODELS[st]
        model = getattr(importlib.import_module(mod), cls)
        for sid, name in (await session.execute(
                select(model.id, model.name).where(in_values(model.id, list(ids))))).all():
            out[(st, str(sid))] = name
    return out


async def _sec_dhcp(c: _Ctx) -> dict[str, Any] | None:
    """DHCP：固定分配、哪些 DHCP 來源現在發著它的租約、在不在發放範圍內、是否被排除在發放範圍外。"""
    from app.models.dhcp import DHCPExclusion, DHCPLeaseSighting, DHCPPoolRange, DHCPReservation
    from app.services.ip_ranges import manual_dhcp_pools

    s, ipa = c.session, c.ipa
    # 固定分配沒有子網路欄位：受限帳號只看對到這筆記錄的，或沒對到記錄而這個子網路是唯一容器的
    # （以前依位址撈全部，重疊網段另一邊的固定分配也給；2026-11-03 稽核）
    res_stmt = select(DHCPReservation).where(DHCPReservation.ip == c.ip)
    if c.restricted:
        from app.services.unmanaged import sole_container
        sole = await sole_container(s, ipa.subnet_id, c.ip)
        res_stmt = res_stmt.where(or_(DHCPReservation.ip_address_id == ipa.id,
                                      and_(DHCPReservation.ip_address_id.is_(None), literal(sole))))
    reservations = [_compact({
        "mac": r.mac, "hostname": r.hostname, "description": _text(r.description),
        "source_type": r.source_type, "source_name": r.source_name, "engine": r.source,
    }) for r in (await s.execute(res_stmt.limit(MAX_ROWS))).scalars().all()]

    sightings = (await s.execute(
        select(DHCPLeaseSighting).where(DHCPLeaseSighting.ip_address_id == ipa.id)
        .order_by(DHCPLeaseSighting.last_seen_at.desc()).limit(MAX_ROWS))).scalars().all()
    names = await _source_names(s, [(x.source_type, x.source_id) for x in sightings])
    leases = [_compact({
        "source_type": x.source_type, "source_name": names.get((x.source_type, str(x.source_id))),
        "first_seen": _dt(x.first_seen_at), "last_seen": _dt(x.last_seen_at),
    }) for x in sightings]

    pools: list[dict[str, Any]] = []
    try:
        aip = ipaddress.ip_address(c.ip)
    except ValueError:
        aip = None
    if aip is not None:
        # 範圍是 DHCP 的設定，筆數跟著 DHCP 設定走（不是跟著 IP 數量），整張讀進來比對
        cand = [(st, sn, a, b) for st, sn, a, b in (await s.execute(
            select(DHCPPoolRange.source_type, DHCPPoolRange.source_name, DHCPPoolRange.start_ip,
                   DHCPPoolRange.end_ip).limit(5000))).all()]
        cand += [("manual", p.source_name, p.start_ip, p.end_ip)
                 for p in await manual_dhcp_pools(s, [ipa.subnet_id])]
        for st, sn, a, b in cand:
            try:
                lo, hi = ipaddress.ip_address(str(a)), ipaddress.ip_address(str(b))
            except ValueError:
                continue
            if lo.version == aip.version and int(lo) <= int(aip) <= int(hi):
                pools.append(_compact({"source_type": st, "source_name": sn, "start": str(a), "end": str(b)}))

    # 排除區間（0207）：在發放範圍裡、但伺服器不發給任何人（通常是設固定 IP 的設備）
    exclusions: list[dict[str, Any]] = []
    if aip is not None:
        for st, sn, a, b in (await s.execute(
            select(DHCPExclusion.source_type, DHCPExclusion.source_name, DHCPExclusion.start_ip,
                   DHCPExclusion.end_ip).limit(5000))).all():
            try:
                lo, hi = ipaddress.ip_address(str(a)), ipaddress.ip_address(str(b))
            except ValueError:
                continue
            if lo.version == aip.version and int(lo) <= int(aip) <= int(hi):
                exclusions.append(_compact({"source_type": st, "source_name": sn, "start": str(a), "end": str(b)}))

    out: dict[str, Any] = {"in_lease": bool(ipa.in_dhcp_lease), "reserved": bool(ipa.dhcp_reserved),
                           "in_pool": bool(pools), "excluded": bool(exclusions)}
    if reservations:
        out["reservations"] = reservations
    if leases:
        out["leases"] = leases
    if pools and c.gread:
        out["pools"] = pools[:MAX_ROWS]        # 範圍的設定是全域資料；旗標本身 IP 詳細資料就有
    if exclusions and c.gread:
        out["exclusions"] = exclusions[:MAX_ROWS]
    if not (out["in_lease"] or out["reserved"] or out["in_pool"] or out["excluded"] or reservations or leases):
        return None
    return out


async def _sec_switch_ports(c: _Ctx) -> list[dict[str, Any]] | None:
    """連接位置：交換器 MAC 表上這張網卡出現在哪台交換器的哪個埠（依交換器的裝置可見性）。"""
    from app.services.mac_history import normalize_mac, switch_ports_for_mac
    m = normalize_mac(c.mac)
    if not m:
        return None
    vis_dev = await visible_ids(c.session, user=c.user, object_type="device")

    def dev_ok(did: Any) -> bool:
        return vis_dev is None or (did is not None and did in vis_dev)

    rows = await switch_ports_for_mac(c.session, m, dev_ok=dev_ok, limit=MAX_ROWS)
    return [_compact({k: v for k, v in r.items() if k != "switch_device_id"}) for r in rows] or None


async def _sec_fw_evidence(c: _Ctx) -> list[dict[str, Any]] | None:
    """防火牆給的逐來源證據（ip_addresses.arp_seen：arp:／vpn:／lease:<廠牌>）。

    `aging`＝這個來源的證據會不會過期（services/evidence 的登記表）：ARP 表、VPN 連線會；租約不會。
    """
    from app.services.evidence import is_aging
    rows = []
    for key, v in (c.ipa.arp_seen or {}).items():
        kind, _, vendor = str(key).partition(":")
        at = _parse_dt(v)
        rows.append({"key": key, "kind": kind, "vendor": vendor, "at": _dt(at), "aging": is_aging(key)})
    rows.sort(key=lambda r: r["at"] or "", reverse=True)
    return rows[:MAX_ROWS] or None


async def _sec_last_seen(c: _Ctx) -> dict[str, Any] | None:
    """各來源最後出現：與 IP 詳細資料「各來源最後出現」同一份資料、同一套判定。

    欄位就是 IP 記錄上的 last_seen_* 與 arp_seen，上線判定的規則（採信哪些來源、幾分鐘內）來自
    system_config.get_liveness_config —— 與 recompute_effective_status、IP 詳細資料的 liveness_rule 同一份。
    verdict：fresh（採信且在時限內）／weak（只有 ARP）／stale（採信但過期）／ignored（系統設定不採信）。
    """
    from app.services.rustdesk import matched_peer
    from app.services.system_config import get_liveness_config

    ipa = c.ipa
    rule = await get_liveness_config(c.session)
    rows: list[tuple[str, datetime | None, tuple[str, ...]]] = [
        ("scanner", ipa.last_seen_scanner, ("scanner",)),
        ("librenms", ipa.last_seen_librenms, ("librenms",)),
        ("arp", ipa.last_seen_arp, ("arp", "arp:librenms")),
        ("wazuh", ipa.last_seen_wazuh, ("wazuh",)),
        ("zabbix", ipa.last_seen_zabbix, ("zabbix",)),
        ("checkmk", ipa.last_seen_checkmk, ("checkmk",)),
        ("ocs", ipa.last_seen_ocs, ("ocs",)),
    ]
    peer = await matched_peer(c.session, ipa.id)
    if peer is not None:
        rows.append(("rustdesk", peer[0].last_heartbeat_at or peer[0].last_online_at, ("rustdesk",)))
    for key, v in (ipa.arp_seen or {}).items():
        rows.append((str(key), _parse_dt(v), (str(key),)))
    rows.append(("dns", ipa.last_seen_dns, ("dns",)))

    now = datetime.now(UTC)
    grace = timedelta(minutes=int(rule.get("minutes") or 30))
    sources = set(rule.get("sources") or [])
    out = []
    for key, at, live in rows:
        if at is None:
            continue
        if not sources.intersection(live):
            verdict = "ignored"
        elif now - at > grace:
            verdict = "stale"
        else:
            verdict = "weak" if key == "arp" else "fresh"
        out.append({"source": key, "at": _dt(at), "verdict": verdict})
    if not out:
        return None
    out.sort(key=lambda r: r["at"] or "", reverse=True)
    return {"minutes": int(rule.get("minutes") or 30), "rows": out}


# ═══════════════════════════ 管理員：探測、異常、巡檢、主控台 ═══════════════════════════

async def _sec_probe(c: _Ctx) -> dict[str, Any] | None:
    """最近一次完成的 IP 探測（只有管理員：探測頁本來就是 require_admin）。

    摘要跟探測頁同一套（ip_identify.summarize，OUI 廠商、虛擬機判斷、Recog 指紋庫都一樣，見 identify_tasks）。
    """
    from app.models.agent_probe_job import STATUS_DONE, AgentProbeJob
    from app.services import ip_identify
    from app.services.identify_tasks import job_is_virtual_guest
    from app.services.oui import vendor_for_mac
    from app.services.recog import get_matcher

    q = select(AgentProbeJob).where(
        AgentProbeJob.kind == "identify", AgentProbeJob.status == STATUS_DONE,
        AgentProbeJob.params["targets"][0].astext == c.ip)
    if c.ipa.device_since is not None:
        # 位址換手之前的探測屬於上一台設備，不拿來說明現在這台（2026-10-12）
        q = q.where(AgentProbeJob.created_at >= c.ipa.device_since)
    job = (await c.session.execute(q.order_by(AgentProbeJob.created_at.desc()).limit(1))).scalars().first()
    if job is None or not isinstance(job.result, dict):
        return None
    raw_nmap = job.result.get("nmap")
    nmap: dict[str, Any] = raw_nmap if isinstance(raw_nmap, dict) else {}
    vendor_mac = c.mac or nmap.get("mac")
    summ = ip_identify.summarize(job.result, mac_vendor=await vendor_for_mac(c.session, vendor_mac),
                                 recog=await get_matcher(c.session),
                                 virtual_guest=await job_is_virtual_guest(c.session, job), mac=vendor_mac)
    services = list(summ.get("services") or [])
    return _compact({
        "at": _dt(job.finished_at or job.created_at),
        "device_type": summ.get("device_type"), "os": summ.get("os"),
        "vendor": summ.get("vendor"), "model": summ.get("model"),
        "no_response": bool(summ.get("no_response")),
        "services": services[:MAX_ROWS],
        "services_total": len(services) if len(services) > MAX_ROWS else None,
        "applications": list(summ.get("applications") or [])[:10],
        "names": list(summ.get("names") or [])[:10],
        "evidence": list(summ.get("evidence") or [])[:10],
    })


def _concerns(item: dict[str, Any], ip: str, ip_id: str) -> bool:
    """異常偵測的這筆發現講的是不是這個位址（有記錄 id 就以 id 為準：重疊網段的另一筆不算）。"""
    for k in ("ip_id", "ip_address_id"):
        if item.get(k):
            return str(item[k]) == ip_id
    return any(item.get(k) and str(item[k]).split("/")[0] == ip for k in ("ip", "server_ip"))


async def _sec_anomalies(c: _Ctx) -> list[dict[str, Any]] | None:
    """異常偵測最近一次的結果裡講到這個位址的（偵測結果是「目前的狀態」，存在 system_settings）。"""
    from app.services.system_config import get_anomaly_report
    rep = await get_anomaly_report(c.session)
    if not rep:
        return None
    ip_id = str(c.ipa.id)
    out: list[dict[str, Any]] = []
    for cat, items in (rep.get("report") or {}).items():
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict) or not _concerns(it, c.ip, ip_id):
                continue
            brief: dict[str, Any] = {"category": cat, "detected_at": rep.get("at")}
            for k in _ANOMALY_FIELDS:
                v = it.get(k)
                if isinstance(v, list):
                    v = [str(x)[:64] for x in v[:5]]
                elif isinstance(v, str):
                    v = v[:200]
                elif not isinstance(v, (int, float, bool)) and v is not None:
                    v = str(v)[:200]
                brief[k] = v
            out.append(_compact(brief))
            if len(out) >= MAX_ROWS:
                return out
    return out or None


async def _sec_ai_findings(c: _Ctx) -> list[dict[str, Any]] | None:
    """AI 巡檢未處理的發現裡講到這個位址的（依據資料的 ips，或直接關聯到這筆記錄）。"""
    from app.models.ai_finding import AIFinding
    rows = (await c.session.execute(
        select(AIFinding).where(
            AIFinding.status == "open",
            or_(AIFinding.object_id == c.ipa.id, AIFinding.evidence["ips"].contains([c.ip])))
        .order_by(AIFinding.created_at.desc()).limit(MAX_ROWS)
    )).scalars().all()
    return [_compact({
        "severity": f.severity, "category": f.category, "title": _text(f.title),
        "detail": _text(f.detail, 300), "at": _dt(f.created_at), "model": f.model,
    }) for f in rows] or None


async def _sec_console_sessions(c: _Ctx) -> list[dict[str, Any]] | None:
    """最近誰從 jt-ipam 開過這個位址的主控台（稽核記錄：誰、何時、從哪裡、用哪個帳號登入）。

    稽核資料敏感，只給管理員。只列開啟那一筆（關閉那筆是同一次連線）。
    """
    from app.models.audit import AuditLog
    rows = (await c.session.execute(
        select(AuditLog.id, AuditLog.ts, AuditLog.action, AuditLog.actor_user_id, AuditLog.actor_ip, AuditLog.diff)
        .where(AuditLog.object_type == "ip", AuditLog.object_id == c.ipa.id,
               AuditLog.action.in_(_CONSOLE_ACTIONS))
        .order_by(AuditLog.ts.desc()).limit(CONSOLE_ROWS)
    )).all()
    if not rows:
        return None
    durations = await _console_durations(c, rows)
    ids = {r.actor_user_id for r in rows if r.actor_user_id}
    users: dict[Any, str] = {}
    if ids:
        users = {uid: name for uid, name in (await c.session.execute(
            select(User.id, User.username).where(in_values(User.id, list(ids))))).all()}
    return [_compact({
        "at": _dt(r.ts), "kind": _CONSOLE_KIND.get(r.action, r.action),
        "user": users.get(r.actor_user_id), "actor_ip": str(r.actor_ip) if r.actor_ip else None,
        "remote_user": _text((r.diff or {}).get("username"), 64) if isinstance(r.diff, dict) else None,
        "duration_seconds": durations.get(r.id),
    }) for r in rows]


async def _console_durations(c: _Ctx, opens: list[Any]) -> dict[Any, float]:
    """每一筆「開啟連線」配上它的「結束連線」記錄裡的 duration_seconds（開啟的稽核 id → 秒數）。

    結束記錄沒有連線編號，所以照順序配：同一個人、同一種主控台、在開啟之後（稽核 id 較大）最早的那筆還沒配過的
    結束記錄。同一個人同時開兩條時先開的配先結束的，個別秒數可能對調，總和不變；還沒有結束記錄的不配。
    """
    from app.models.audit import AuditLog
    first = min(r.id for r in opens)
    closes = (await c.session.execute(
        select(AuditLog.id, AuditLog.action, AuditLog.actor_user_id, AuditLog.diff)
        .where(AuditLog.object_type == "ip", AuditLog.object_id == c.ipa.id,
               AuditLog.action.in_(_CONSOLE_CLOSE_ACTIONS), AuditLog.id > first)
        .order_by(AuditLog.id).limit(CONSOLE_ROWS * 4)
    )).all()
    used: set[Any] = set()
    out: dict[Any, float] = {}
    for o in sorted(opens, key=lambda r: r.id):
        kind = _CONSOLE_KIND.get(o.action)
        for cl in closes:
            if (cl.id in used or cl.id <= o.id or cl.actor_user_id != o.actor_user_id
                    or _CONSOLE_CLOSE_KIND.get(cl.action) != kind):
                continue
            used.add(cl.id)
            dur = (cl.diff or {}).get("duration_seconds") if isinstance(cl.diff, dict) else None
            if isinstance(dur, (int, float)) and not isinstance(dur, bool) and dur >= 0:
                out[o.id] = float(dur)
            break
    return out


# ═══════════════════════════ 全域讀取：基礎設施 ═══════════════════════════

async def _sec_dns(c: _Ctx) -> list[dict[str, Any]]:
    """DNS：哪些名字指到這裡。"""
    from app.models.dns import DNSRecord
    return [
        {"name": n, "type": t, "value": v}
        for n, t, v in (await c.session.execute(
            select(DNSRecord.name, DNSRecord.type, DNSRecord.value)
            .where(DNSRecord.value == c.ip).limit(MAX_ROWS)
        )).all()
    ]


async def _sec_nat(c: _Ctx) -> list[dict[str, Any]]:
    """NAT：對外開了什麼（轉發目標是這個位址的規則）。

    來源限制（src_ip／src_port）、埠範圍、說明與來自哪台防火牆也要給：「只有合作夥伴的位址能連」與
    「全世界都能連」是兩回事（2026-10-15 AI 工具盤點）。"""
    from app.models.nat import NATTranslation
    rows = list((await c.session.execute(
        select(NATTranslation).where(NATTranslation.dst_ip_id == c.ipa.id).limit(MAX_ROWS)
    )).scalars().all())
    src_ids = {n.src_ip_id for n in rows if n.src_ip_id}
    src_ip = {i: str(v).split("/")[0] for i, v in (await c.session.execute(
        select(IPAddress.id, IPAddress.ip).where(in_values(IPAddress.id, src_ids)))).all()} if src_ids else {}
    fw_names: dict[str, str] = {}
    refs = {(n.source_origin or "").partition(":")[2] for n in rows} - {""}
    if refs:
        from app.mcp.tools import _firewall_name_map
        fw_names = await _firewall_name_map(c.session)
    out = []
    for n in rows:
        kind, _, ref = (n.source_origin or "manual").partition(":")
        out.append({
            "name": n.name, "type": n.type, "protocol": n.protocol,
            "port": n.dst_port, "interface": n.src_interface, "disabled": n.disabled,
            **_compact({"port_to": n.dst_port_to, "src_ip": src_ip.get(n.src_ip_id), "src_alias": n.src_alias,
                        "src_not": n.src_not or None, "src_port": n.src_port, "src_port_to": n.src_port_to,
                        "description": n.description, "source": kind, "firewall": fw_names.get(ref)}),
        })
    return out


async def _sec_firewall_rules(c: _Ctx) -> list[dict[str, Any]]:
    """OPNsense 規則：目的地就是這個位址的。"""
    from app.models.firewall_rule import OPNsenseRule
    return [
        {"action": r.action, "interface": r.interface, "direction": r.direction,
         "protocol": r.protocol, "source": r.source_net, "port": r.destination_port,
         "description": r.description, "enabled": r.enabled}
        for r in (await c.session.execute(
            select(OPNsenseRule).where(OPNsenseRule.destination_net == c.ip).limit(MAX_ROWS)
        )).scalars().all()
    ]


async def _sec_firewall_refs(c: _Ctx) -> dict[str, Any] | None:
    """各家防火牆裡涵蓋這個位址的別名／位址物件，以及以它們（或位址本身）為來源或目的的規則。

    與 IP 詳細資料的防火牆區塊同一份反查（fw_lookup.rules_touching_ip）。OPNsense「目的地就是這個位址」
    的規則上面 firewall_rules 已經列了，這裡不重複。
    """
    from app.services.fw_lookup import rules_touching_ip
    r = await rules_touching_ip(c.session, c.ip, ip_id=c.ipa.id)

    def dup(x: dict[str, Any]) -> bool:
        m = x.get("match") or {}
        return x.get("source_type") == "opnsense" and m.get("side") == "dst" and m.get("code") == "exact"

    aliases = [_compact({"source_type": a.get("source_type"), "firewall": a.get("firewall"),
                         "name": a.get("name"), "descr": a.get("descr")}) for a in r.get("aliases") or []]
    rules = [_compact({k: x.get(k) for k in ("source_type", "firewall", "action", "interface", "protocol",
                                              "src", "dst", "dst_port", "descr", "match")})
             for x in r.get("rules") or [] if not dup(x)]
    if not aliases and not rules:
        return None
    return _compact({
        "aliases": aliases[:MAX_ROWS], "rules": rules[:MAX_ROWS],
        "aliases_total": len(aliases) if len(aliases) > MAX_ROWS else None,
        "rules_total": len(rules) if len(rules) > MAX_ROWS else None,
    })


async def _sec_dhcp_rogue(c: _Ctx) -> list[dict[str, Any]] | None:
    """掃描代理在網段上觀測到的 DHCP 回應：發了這個位址的、或這個位址本身在發的。

    `server_marked`＝那台伺服器在 IPAM 有沒有被標記為 DHCP 伺服器；`server_integration`＝它是哪個整合中的
    DHCP 來源（與異常偵測、IP 清單同一份判斷：services/dhcp_known）。合法與否由人判斷，這裡只給事實。
    """
    from app.models.dhcp_sighting import DHCPSighting
    rows = (await c.session.execute(
        select(DHCPSighting, Subnet.cidr).join(Subnet, Subnet.id == DHCPSighting.subnet_id)
        .where(or_(DHCPSighting.offered_ip == c.ip, DHCPSighting.server_ip == c.ip))
        .order_by(DHCPSighting.last_seen_at.desc()).limit(MAX_ROWS)
    )).all()
    if not rows:
        return None
    servers = {str(x.server_ip).split("/")[0] for x, _ in rows}
    marked: set[tuple[Any, str]] = set()
    hostnames: dict[str, list[str]] = {}
    for sid, ip, is_srv, hn in (await c.session.execute(
        select(IPAddress.subnet_id, IPAddress.ip, IPAddress.is_dhcp_server, IPAddress.hostname).where(
            in_values(IPAddress.ip, sorted(servers))))).all():
        key = str(ip).split("/")[0]
        if is_srv:
            marked.add((sid, key))
        if hn:
            hostnames.setdefault(key, []).append(hn)
    from app.services.dhcp_known import load_known_dhcp_servers
    known = await load_known_dhcp_servers(c.session)

    def _integration(srv: str) -> str | None:
        m = known.match(srv) or [x for hn in hostnames.get(srv, []) for x in known.match(None, hn)]
        return m[0].display if m else None
    out = []
    for x, cidr in rows:
        srv = str(x.server_ip).split("/")[0]
        out.append(_compact({
            "role": "server" if srv == c.ip else "offered",
            "server_ip": srv, "server_mac": str(x.server_mac) if x.server_mac else None,
            "offered_ip": str(x.offered_ip).split("/")[0] if x.offered_ip else None,
            "router": str(x.router).split("/")[0] if x.router else None,
            "via_relay": bool(x.via_relay), "subnet": str(cidr),
            "server_marked": (x.subnet_id, srv) in marked,
            "server_integration": _integration(srv),
            "first_seen": _dt(x.first_seen_at), "last_seen": _dt(x.last_seen_at),
        }))
    return out


# ═══════════════════════════ 組起來 ═══════════════════════════

async def collect_dossier(
    session: AsyncSession, *, user: User, ip: str,
) -> dict[str, Any]:
    """收集某個位址的完整線索。只回事實。"""
    try:
        ip = str(ipaddress.ip_address(str(ip).strip()))     # 各整合存的是正規寫法（IPv6 尤其）
    except ValueError:
        ip = str(ip).strip()
    vis = await visible_ids(session, user=user, object_type="subnet")
    try:
        ipaddress.ip_address(ip)
        # `>>=`（包含或等於）走 ip 欄位的 GiST 索引先縮小範圍，host() 再精確比對 —— 結果與只用 host() 相同
        # （host(x) == ip 必然 x >>= ip），但十萬筆 IP 時不必整張表逐列算 host()
        where = [IPAddress.ip.op(">>=")(ip), func.host(IPAddress.ip) == ip]
    except ValueError:
        where = [func.host(IPAddress.ip) == ip]
    if vis is not None:
        # 可見範圍推進 SQL：先 LIMIT 再過濾的話，重疊網段很多時看得到的那筆可能被截掉
        where.append(in_values(IPAddress.subnet_id, list(vis)))
    rows = (await session.execute(
        select(IPAddress, Subnet)
        .join(Subnet, IPAddress.subnet_id == Subnet.id)
        .where(*where).limit(MAX_ROWS)
    )).all()
    if not rows:
        # 看不到就當作不存在 —— 回「無權限」等於確認了這個位址存在
        return {"found": False, "ip": ip}

    ipa, subnet = rows[0]
    gread = await _global_read(session, user)
    admin = bool(getattr(user, "is_admin", False))
    c = _Ctx(session=session, user=user, ip=ip, ipa=ipa, subnet=subnet, gread=gread, admin=admin,
             restricted=vis is not None)
    # 掛的裝置：看得到那台裝置才給名稱（與 REST 位址清單同一規則）；單位名稱跟著位址走
    from app.models.customer import Customer
    from app.models.device import Device
    dev_name = None
    if ipa.device_id is not None:
        vis_dev = await visible_ids(session, user=user, object_type="device")
        if vis_dev is None or ipa.device_id in vis_dev:
            dev_name = await session.scalar(select(Device.name).where(Device.id == ipa.device_id))
    cust_name = (await session.scalar(select(Customer.name).where(Customer.id == ipa.customer_id))
                 if ipa.customer_id else None)
    out: dict[str, Any] = {
        "found": True, "ip": ip, "global_read": gread,
        "address": {
            "ip_address_id": str(ipa.id), "hostname": ipa.hostname,
            "state": ipa.state, "effective_status": ipa.effective_status,
            "mac": str(ipa.mac) if ipa.mac else None,
            "owner": ipa.owner, "description": ipa.description, "note": ipa.note,
            "device": dev_name, "customer": cust_name,
            "subnet": str(subnet.cidr), "subnet_description": subnet.description,
            "discovery_source": ipa.discovery_source,
            "in_dhcp_lease": bool(ipa.in_dhcp_lease),
            "dhcp_reserved": bool(getattr(ipa, "dhcp_reserved", False)),
            "is_dhcp_server": bool(ipa.is_dhcp_server),
            "last_seen_scanner": _dt(ipa.last_seen_scanner),
            "last_seen_librenms": _dt(ipa.last_seen_librenms),
            "switch_port": ipa.switch_port,
            "switch_port_confident": ipa.switch_port_confident,
            # 人工設定的旗標：不 ping、上線跟著所屬裝置（多 LAN 閘道）、忽略 PTR 不一致
            "exclude_from_ping": bool(ipa.exclude_from_ping),
            "liveness_from_device": bool(ipa.liveness_from_device),
            "ptr_ignore": bool(ipa.ptr_ignore),
        },
        # 重疊網段下同一位址的其他紀錄。挑一筆正是這幾天連續修掉的那類 bug，
        # 調查用的檔案更不該重蹈覆轍 —— 全部列出來，讓人看見有幾筆。
        "other_records": [
            {"ip_address_id": str(a.id), "subnet": str(s.cidr),
             "hostname": a.hostname, "effective_status": a.effective_status}
            for a, s in rows[1:]
        ],
        "hostname_sources": [], "os_candidates": {}, "monitoring": {},
        "arp": [], "dns": [], "nat": [], "firewall_rules": [], "changes": [],
    }

    # ── 逐物件（看得到這個子網路就看得到）
    out["hostname_sources"] = await _safe(c, "hostname_sources", _sec_hostname_sources) or []
    out["os_candidates"] = await _safe(c, "os_candidates", _sec_os_candidates) or {}
    ident = await _safe(c, "identity", _sec_identity)
    if ident:
        out["identity"] = ident
    # 監控涵蓋：誰在看這台
    for key, fn in (("wazuh", _sec_wazuh), ("librenms", _sec_librenms), ("zabbix", _sec_zabbix),
                    ("checkmk", _sec_checkmk)):
        v = await _safe(c, key, fn)
        if v:
            out["monitoring"][key] = v
    # 裝在這台電腦上的代理（Wazuh 在監控那一段）
    agents: dict[str, Any] = {}
    for key, fn in (("ocs", _sec_ocs), ("rustdesk", _sec_rustdesk)):
        v = await _safe(c, key, fn)
        if v:
            agents[key] = v
    if agents:
        out["agents"] = agents
    for key, fn in (("virtualization", _sec_virtualization), ("dhcp", _sec_dhcp),
                    ("switch_ports", _sec_switch_ports), ("fw_evidence", _sec_fw_evidence),
                    ("last_seen", _sec_last_seen)):
        v = await _safe(c, key, fn)
        if v:
            out[key] = v
    out["arp"] = await _safe(c, "arp", _sec_arp) or []
    out["changes"] = await _safe(c, "changes", _sec_changes) or []

    # ── 管理資料（稽核、異常偵測、AI 巡檢、探測）：只有管理員
    if admin:
        for key, fn in (("probe", _sec_probe), ("anomalies", _sec_anomalies),
                        ("ai_findings", _sec_ai_findings), ("console_sessions", _sec_console_sessions)):
            v = await _safe(c, key, fn)
            if v:
                out[key] = v

    # ── 全域基礎設施：只有全域讀取（降級，不是整個擋掉）
    if gread:
        out["dns"] = await _safe(c, "dns", _sec_dns) or []
        out["nat"] = await _safe(c, "nat", _sec_nat) or []
        out["firewall_rules"] = await _safe(c, "firewall_rules", _sec_firewall_rules) or []
        for key, fn in (("firewall_refs", _sec_firewall_refs), ("dhcp_rogue", _sec_dhcp_rogue)):
            v = await _safe(c, key, fn)
            if v:
                out[key] = v

    out["conflicts"] = compute_conflicts(out)
    return out


# ═══════════════════════════ 矛盾（後端算一次，大家共用） ═══════════════════════════

def _norm_hostname(h: Any) -> str:
    return str(h or "").strip().lower().rstrip(".")


def distinct_hostnames(names: Iterable[Any]) -> list[str]:
    """把只是寫法不同的主機名稱合併後，還剩哪幾個不同的名字。

    實機（2026-10-05）：掃描代理回 `win11-desk-01`、OPNsense 回 `win11-desk-01.`（結尾有點）、NetBIOS 回
    `WIN11-DESK-01`，畫面卻說「各來源回報的主機名稱不一致（2 種）」。同一個名字的寫法差異：
    大小寫、結尾的點、短名稱對上某個 FQDN 的第一段。兩個網域不同的 FQDN 仍算兩個名字。
    """
    normed = {_norm_hostname(n) for n in names} - {""}
    fqdns = {n for n in normed if "." in n}
    labels = {n.split(".", 1)[0] for n in fqdns}
    shorts = {n for n in normed if "." not in n and n not in labels}
    return sorted(fqdns | shorts)


def compute_conflicts(d: dict[str, Any]) -> list[dict[str, Any]]:
    """彼此對不上的線索：`[{code, params}]`。畫面照 `investigate.conflict_<code>` 翻譯。

    這個功能存在的理由就是「線索散在各處、彼此對不上」—— 以前在前端算，AI 判讀與匯出各自再猜一次；
    現在只在這裡算，三邊拿到的是同一份。只放確定的矛盾，正常的樣態（反向代理多個域名、DHCP 位址換人）
    不在這裡（那些是 infer_role_hints 的角色訊號）。
    """
    from app.services.mac_history import is_random_mac, normalize_mac

    out: list[dict[str, Any]] = []
    names = distinct_hostnames(h.get("hostname") for h in d.get("hostname_sources") or [])
    if len(names) > 1:
        out.append({"code": "names", "params": {"n": len(names), "names": names[:10]}})
    w = (d.get("monitoring") or {}).get("wazuh") or {}
    if w and w.get("still_represents_this_ip") is False:
        out.append({"code": "stale_agent", "params": {"name": w.get("name") or ""}})
    if d.get("other_records"):
        out.append({"code": "duplicate", "params": {"n": len(d["other_records"]) + 1}})
    # 隨機（私人 Wi‑Fi）MAC 不算「兩台在搶」：手機、筆電換了位址（使用者回報：3 個 MAC 裡兩個是隨機的，
    # 卻被說成「單一主機不會這樣」）
    macs = list(dict.fromkeys(m for m in (normalize_mac(a.get("mac")) for a in d.get("arp") or []) if m))
    random = [m for m in macs if is_random_mac(m)]
    burned = len(macs) - len(random)
    if burned > 2:
        out.append({"code": "macs", "params": {"n": burned}})
    elif len(macs) > 2 and random:
        out.append({"code": "macs_random", "params": {"n": len(macs), "r": len(random)}})
    # DHCP 固定分配綁的網卡跟現在用這個位址的不同
    cur = normalize_mac((d.get("address") or {}).get("mac"))
    for r in (d.get("dhcp") or {}).get("reservations") or []:
        bound = normalize_mac(r.get("mac"))
        if cur and bound and bound != cur:
            out.append({"code": "reservation_mac",
                        "params": {"reserved": bound, "current": cur, "source": r.get("source_name") or ""}})
            break
    return out


# ═══════════════════════════ 給模型的精簡版 ═══════════════════════════

def _looks_like_uuid(v: Any) -> bool:
    if not isinstance(v, str) or len(v) != 36:
        return False
    try:
        import uuid as _uuid
        _uuid.UUID(v)
        return True
    except ValueError:
        return False


def prompt_view(d: dict[str, Any]) -> dict[str, Any]:
    """送給模型的檔案：拿掉空值與內部識別碼，每個清單最多 PROMPT_ROWS 筆、每段文字最多 PROMPT_TEXT 字。

    為什麼要另外一份：檔案加強之後大了好幾倍，而超過模型的 num_ctx 時**不會報錯**，只是前面的內容被
    靜靜截掉、判讀照樣產出（AI 巡檢踩過）。被截掉的清單附 `<鍵>_total`，模型才知道還有多少沒列。
    """
    def walk(v: Any) -> Any:
        if isinstance(v, dict):
            res: dict[str, Any] = {}
            for k, x in v.items():
                if k == "found" or (k.endswith("_id") and _looks_like_uuid(x)):
                    continue
                w = walk(x)
                if w is None or w == "" or w == [] or w == {}:
                    continue
                res[k] = w
                if isinstance(x, list) and len(x) > PROMPT_ROWS and f"{k}_total" not in v:
                    res[f"{k}_total"] = len(x)
            return res
        if isinstance(v, list):
            items = [walk(x) for x in v[:PROMPT_ROWS]]
            return [x for x in items if not (x is None or x == "" or x == [] or x == {})]
        if isinstance(v, str) and len(v) > PROMPT_TEXT:
            return v[:PROMPT_TEXT] + "…"
        return v

    out: dict[str, Any] = walk(d)
    return out


def infer_role_hints(dossier: dict[str, Any]) -> list[str]:
    """從事實裡算出「這台在扮演什麼角色」的訊號。

    由來（實機）：一台反向代理有 20 筆 A 記錄指向它，AI 判讀把這件事說成「DNS 記錄與
    主機名稱來源顯著矛盾」。那不是模型的錯 —— 送過去的只是一串域名，沒有任何訊號說明
    多個域名指向同一個位址對反向代理是常態，而提示詞又特別要求它「指出矛盾」。

    這裡只描述**觀察到的樣態**，不下「安全 / 不安全」「設定錯誤」這類判斷 ——
    那取決於這台本來該做什麼，而那件事只有人知道。
    """
    hints: list[str] = []

    names = {str(r.get("name") or "").strip().lower()
             for r in (dossier.get("dns") or []) if r.get("name")}
    if len(names) >= 5:
        hints.append(
            f"{len(names)} distinct DNS names resolve to this address. Many names on one"
            " address is the normal shape of a reverse proxy, load balancer or shared"
            " web host — do not report it as a contradiction on its own."
        )

    nat_ports = sorted({str(n.get("port")) for n in (dossier.get("nat") or []) if n.get("port")})
    web = [p for p in nat_ports if p in ("80", "443", "8080", "8443")]
    if web:
        hints.append(
            f"Ports {', '.join(web)} are forwarded to this address from outside, which is"
            " what a public web entry point looks like."
        )
    other = [p for p in nat_ports if p not in web]
    if other:
        hints.append(f"Also forwarded from outside: ports {', '.join(other)}.")

    macs = {str(a.get("mac")) for a in (dossier.get("arp") or []) if a.get("mac")}
    if len(macs) > 2:
        hints.append(
            f"{len(macs)} different MAC addresses have used this address over time. That is"
            " expected in a DHCP range and unexpected for a statically assigned server —"
            " which of the two this is, the record should say."
        )

    # DHCP 的事實（2026-10-05）：位址會換人、隨機 MAC 會換，是 DHCP 與手機筆電的常態
    dh = dossier.get("dhcp") or {}
    leased = bool(dh.get("in_lease") or dh.get("leases"))
    pinned = bool(dh.get("reserved") or dh.get("reservations"))
    if leased and (dossier.get("identity") or {}).get("mac_random"):
        hints.append(
            "This address is handed out by DHCP (it has a lease) and its current MAC is a randomized"
            " (private) address. Phones and laptops rotate such MACs and DHCP gives the address to"
            " whoever asks next, so a different MAC or device on this address over time is expected"
            " — not a contradiction on its own."
        )
    elif (leased or dh.get("in_pool")) and not pinned:
        hints.append(
            "This address is in a DHCP pool with no reservation, so different devices using it over"
            " time is the normal behaviour of a DHCP range."
        )
    return hints
