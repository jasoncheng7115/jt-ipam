"""IPAM 這一側的證據：新位址的衝突、活動證據、jt-ipam 自己設定裡的引用、文字線索、裝置的實體關係。

都是 jt-ipam 自己的資料，逐物件權限（IP／子網路／裝置的可見性）；ARP 屬 LibreNMS 等整合 → 全域讀取。
"""

from __future__ import annotations

import ipaddress
import json
import re
from datetime import timedelta
from typing import Any

from sqlalchemy import select, text

from app.services.change_impact.context import Ctx
from app.services.change_impact.matching import norm_ip, text_mentions
from app.services.change_impact.model import Evidence, Finding, TargetAddr

ACTIVITY_WINDOW = timedelta(days=7)
_SEEN_FIELDS = ("last_seen_scanner", "last_seen_librenms", "last_seen_arp", "last_seen_wazuh",
                "last_seen_zabbix", "last_seen_ocs")


async def compute_overlap(ctx: Ctx) -> None:
    """根位址是否也落在別的（未封存）子網路：整合沒設範圍時，比中的證據分不出是哪一邊的。"""
    for r in ctx.roots:
        n = (await ctx.session.execute(text("""
            SELECT count(*) FROM subnets WHERE archived_at IS NULL AND cidr >>= CAST(:ip AS inet) AND id <> :sid
        """), {"ip": r.ip_text, "sid": r.subnet_id})).scalar() or 0
        ctx.overlap[r.ip_id] = n > 0


# ─────────────────── 新位址（改址目標） ───────────────────

async def new_ip(ctx: Ctx) -> None:
    sc = ctx.scenario
    if not ctx.is_renumber or sc.new_aip is None:
        return
    root = ctx.roots[0]
    rows = (await ctx.session.execute(text("""
        SELECT a.id, host(a.ip) AS ip, a.subnet_id, s.cidr::text AS cidr, s.vrf_id, a.state, a.hostname,
               a.device_id, a.updated_at
          FROM ip_addresses a JOIN subnets s ON s.id = a.subnet_id
         WHERE a.ip = CAST(:ip AS inet) AND s.archived_at IS NULL
    """), {"ip": sc.new_ip})).all()
    hidden = False
    for r in rows:
        if not ctx.can_see("ip", r.id):
            hidden = True
            continue
        key = ctx.add(Evidence(
            key=f"ip_address:{r.id}", source_type="ipam", object_type="ip_address", object_id=r.id,
            label=f"{r.ip} ({r.cidr})", payload={"ip": r.ip, "subnet": r.cidr, "state": r.state,
                                                 "hostname": r.hostname},
            observed_at=r.updated_at, freshness="current", visibility=("ip", r.id)))
        if r.subnet_id == sc.target_subnet_id:
            rule = "ipam.new_ip_reserved" if r.state == "reserved" else "ipam.new_ip_assigned"
            ctx.find(Finding(rule, "ip_address", f"{r.ip} {r.hostname}" if r.hostname else r.ip, [key],
                             subject_id=r.id, match_kind="exact",
                             params={"address": r.ip, "state": r.state, "hostname": r.hostname or ""},
                             visibility=("ip", r.id)))
        elif r.vrf_id == sc.target_vrf_id:
            # 同一個 VRF 的重疊子網路有同一個位址：可能是同一台，也可能不是 —— 要人確認
            ctx.find(Finding("ipam.new_ip_overlap_record", "ip_address", f"{r.ip} ({r.cidr})", [key],
                             subject_id=r.id, match_kind="exact",
                             params={"address": r.ip, "subnet": r.cidr}, visibility=("ip", r.id)))
    if hidden:
        ctx.gap("ipam", "permission_limited", affected="ipam")

    cd = (await ctx.session.execute(text("""
        SELECT id, host(ip) AS ip, until, previous_hostname FROM ip_cooldowns
         WHERE subnet_id = :sid AND ip = CAST(:ip AS inet) AND cleared_at IS NULL AND until > :now
    """), {"sid": sc.target_subnet_id, "ip": sc.new_ip, "now": ctx.now})).first()
    if cd is not None and ctx.can_see("subnet", sc.target_subnet_id):
        key = ctx.add(Evidence(key=f"ip_cooldown:{cd.id}", source_type="ipam", object_type="ip_cooldown",
                               object_id=cd.id, label=cd.ip, payload={"until": cd.until.isoformat(),
                                                                      "previous_hostname": cd.previous_hostname},
                               freshness="current", visibility=("subnet", sc.target_subnet_id)))
        ctx.find(Finding("ipam.new_ip_cooldown", "ip_address", cd.ip, [key], subject_key=cd.ip, match_kind="exact",
                         params={"address": cd.ip, "until": cd.until.isoformat()},
                         visibility=("subnet", sc.target_subnet_id)))

    if ctx.can_see("subnet", sc.target_subnet_id):
        vis = ("subnet", sc.target_subnet_id)
        # 有人申請了這個位址、還沒配發（待審核或已核准）：改過去就會跟申請撞在一起
        for rq in (await ctx.session.execute(text("""
            SELECT id, host(requested_ip) AS ip, hostname, status, created_at FROM ip_requests
             WHERE subnet_id = :sid AND requested_ip = CAST(:ip AS inet) AND status IN ('pending', 'approved')
        """), {"sid": sc.target_subnet_id, "ip": sc.new_ip})).all():
            key = ctx.add(Evidence(key=f"ip_request:{rq.id}", source_type="ipam", object_type="ip_request",
                                   object_id=rq.id, label=rq.ip, payload={"hostname": rq.hostname, "status": rq.status},
                                   observed_at=rq.created_at, freshness="current", visibility=vis))
            ctx.find(Finding("ipam.new_ip_requested", "ip_request", rq.ip, [key], subject_id=rq.id, match_kind="exact",
                             params={"address": rq.ip, "hostname": rq.hostname or ""}, visibility=vis))
        # IP 範圍（DHCP／保留／其他）：落在範圍裡要確認用途允許
        for rg in (await ctx.session.execute(text("""
            SELECT id, host(start_ip) AS s, host(end_ip) AS e, purpose, name FROM ip_ranges
             WHERE subnet_id = :sid AND CAST(:ip AS inet) BETWEEN start_ip AND end_ip
        """), {"sid": sc.target_subnet_id, "ip": sc.new_ip})).all():
            label = rg.name or f"{rg.s}-{rg.e}"
            key = ctx.add(Evidence(key=f"ip_range:{rg.id}", source_type="ipam", object_type="ip_range",
                                   object_id=rg.id, label=label, payload={"start": rg.s, "end": rg.e,
                                                                          "purpose": rg.purpose},
                                   freshness="current", visibility=vis))
            ctx.find(Finding("ipam.new_ip_in_range", "ip_range", label, [key], subject_id=rg.id,
                             match_kind="range_contains",
                             params={"address": sc.new_ip, "range": label, "purpose": rg.purpose}, visibility=vis))

    if sc.cross_subnet:
        gw = (await ctx.session.execute(text("SELECT host(gateway) FROM subnets WHERE id = :id"),
                                        {"id": sc.target_subnet_id})).scalar()
        key = ctx.add(Evidence(key=f"subnet:{sc.target_subnet_id}", source_type="ipam", object_type="subnet",
                               object_id=sc.target_subnet_id, label=sc.target_subnet_cidr or "",
                               payload={"cidr": sc.target_subnet_cidr, "gateway": gw}, freshness="current",
                               visibility=("subnet", sc.target_subnet_id)))
        # 跨子網路：VLAN、閘道、路由、ACL、DHCP、實體接入位置都要確認；沒有閘道資料就不猜
        for item in ("vlan", "gateway", "route", "acl", "dhcp", "physical_port"):
            ctx.find(Finding("network.cross_subnet", "subnet", sc.target_subnet_cidr or "", [key],
                             subject_id=sc.target_subnet_id, subject_key=item, match_kind=None,
                             params={"item": item, "from": root.subnet_cidr, "to": sc.target_subnet_cidr or "",
                                     "gateway": gw or ""},
                             visibility=("subnet", sc.target_subnet_id)))
        if not gw:
            ctx.gap("ipam", "gateway_unknown", subnet=sc.target_subnet_cidr or "", affected="cross_subnet")


async def new_ip_activity(ctx: Ctx) -> None:
    """新位址最近有沒有人在用：ARP（整合）、未納管目擊（掃描代理）。沒看到 ≠ 可以用（規格 T07）。"""
    sc = ctx.scenario
    if not ctx.is_renumber or sc.new_aip is None:
        return
    since = ctx.now - ACTIVITY_WINDOW
    um = (await ctx.session.execute(text("""
        SELECT id, host(ip) AS ip, mac, hostname, source, last_seen_at FROM unmanaged_sightings
         WHERE subnet_id = :sid AND ip = CAST(:ip AS inet) AND last_seen_at >= :since
    """), {"sid": sc.target_subnet_id, "ip": sc.new_ip, "since": since})).all()
    for r in um:
        if not ctx.can_see("subnet", sc.target_subnet_id):
            break
        key = ctx.add(Evidence(key=f"unmanaged_sighting:{r.id}", source_type="activity",
                               object_type="unmanaged_sighting", object_id=r.id, label=r.ip,
                               payload={"mac": r.mac, "hostname": r.hostname, "source": r.source},
                               observed_at=r.last_seen_at, freshness="current",
                               visibility=("subnet", sc.target_subnet_id)))
        ctx.find(Finding("activity.new_ip_seen", "address", r.ip, [key], subject_key=r.ip, match_kind="exact",
                         params={"address": r.ip, "source": r.source, "mac": r.mac or "",
                                 "last_seen": r.last_seen_at.isoformat()},
                         visibility=("subnet", sc.target_subnet_id)))
    if ctx.allowed("librenms"):
        # ARP 列沒有命名空間：LibreNMS 的列只有 VRF 文字；其他來源的列有 subnet_id 界定範圍
        rows = (await ctx.session.execute(text("""
            SELECT id, host(ip) AS ip, mac::text AS mac, source, subnet_id, instance_id, last_seen_at
              FROM arp_entries
             WHERE ip = CAST(:ip AS inet) AND last_seen_at >= :since
               AND (subnet_id IS NULL OR subnet_id = :sid)
             ORDER BY last_seen_at DESC LIMIT 20
        """), {"ip": sc.new_ip, "since": since, "sid": sc.target_subnet_id})).all()
        for r in rows:
            key = ctx.add(Evidence(key=f"arp_entry:{r.id}", source_type="librenms", object_type="arp_entry",
                                   object_id=r.id, label=f"{r.ip} → {r.mac}",
                                   payload={"mac": r.mac, "source": r.source}, observed_at=None,
                                   collected_at=r.last_seen_at, freshness="collected_only",
                                   visibility=("global", None)))
            ctx.find(Finding("activity.new_ip_seen", "address", r.ip, [key], subject_key=r.ip, match_kind="exact",
                             params={"address": r.ip, "source": r.source, "mac": r.mac or "",
                                     "last_seen": r.last_seen_at.isoformat()},
                             strength="corroborated" if r.subnet_id else "inferred"))
        if rows:
            ctx.gap("librenms", "arp_observed_time_unknown", affected="activity")


# ─────────────────── 舊位址／裝置位址的活動 ───────────────────

async def root_activity(ctx: Ctx) -> None:
    from app.services.arp_seen import _parse
    from app.services.evidence import is_aging
    since = ctx.now - ACTIVITY_WINDOW
    for root in ctx.roots:
        row = (await ctx.session.execute(text(f"""
            SELECT {", ".join(_SEEN_FIELDS)}, arp_seen, effective_status, in_dhcp_lease FROM ip_addresses WHERE id = :id
        """), {"id": root.ip_id})).mappings().first()  # noqa: S608 -- 欄位名稱是本檔常數
        if row is None:
            continue
        seen = {f.removeprefix("last_seen_"): row[f] for f in _SEEN_FIELDS if row[f] and row[f] >= since}
        # 防火牆同步的逐來源時間（使用者 2026-10-08：只有防火牆看得到的機器改址時也要提醒）。
        # 採信範圍跟上線判定一致：會過期的（ARP 表、目前連著的 VPN）才算；DHCP 租約可能是幾天前拿的，不算
        for src, raw in (row["arp_seen"] or {}).items():
            ts = _parse(raw)
            if ts is not None and ts >= since and is_aging(src):
                seen[src] = ts
        if not seen:
            continue
        newest = max(seen.values())
        key = ctx.add(Evidence(key=f"ip_activity:{root.ip_id}", source_type="activity", object_type="ip_address",
                               object_id=root.ip_id, label=root.ip_text,
                               payload={"seen_by": sorted(seen), "effective_status": row["effective_status"]},
                               observed_at=newest, freshness="current", visibility=("ip", root.ip_id)))
        rule = "activity.old_ip_active" if ctx.is_renumber else "device.recent_activity"
        ctx.find(Finding(rule, "ip_address", root.ip_text, [key], subject_id=root.ip_id, match_kind="exact",
                         params={"address": root.ip_text, "sources": ", ".join(sorted(seen)),
                                 "last_seen": newest.isoformat()},
                         visibility=("ip", root.ip_id)))


# ─────────────────── jt-ipam 自己設定裡的引用 ───────────────────

def _host_of(url: str | None) -> str | None:
    from urllib.parse import urlsplit
    if not url:
        return None
    try:
        h = urlsplit(url if "://" in url else f"//{url}").hostname
    except ValueError:
        return None
    return h


_INTEGRATION_URLS: tuple[tuple[str, str, str, str], ...] = (
    ("app.models.librenms", "LibreNMSInstance", "api_url", "librenms"),
    ("app.models.firewall", "OPNsenseFirewall", "api_url", "opnsense"),
    ("app.models.pfsense", "PfSenseFirewall", "api_url", "pfsense"),
    ("app.models.fortigate", "FortiGateFirewall", "api_url", "fortigate"),
    ("app.models.paloalto", "PaloAltoFirewall", "api_url", "paloalto"),
    ("app.models.mikrotik", "MikroTikRouter", "api_url", "mikrotik"),
    ("app.models.virt", "ProxmoxInstance", "api_url", "proxmox"),
    ("app.models.esxi", "ESXiInstance", "api_url", "esxi"),
    ("app.models.hyperv", "HyperVInstance", "host", "hyperv"),
    # OpenStack：Keystone 端點（Nova／Neutron 的位址來自它的服務目錄）；CubeCOS API（沒填時就是 Keystone 主機）
    ("app.models.openstack", "OpenStackInstance", "auth_url", "openstack"),
    ("app.models.openstack", "OpenStackInstance", "cubecos_url", "openstack"),
    ("app.models.zabbix", "ZabbixInstance", "api_url", "zabbix"),
    ("app.models.wazuh", "WazuhInstance", "api_url", "wazuh"),
    ("app.models.dns", "DNSServer", "api_url", "dns_server"),
    ("app.models.dns", "DNSServer", "server_address", "dns_server"),
    ("app.models.dhcp_standalone", "KeaDhcpServer", "api_url", "kea_dhcp"),
    ("app.models.technitium", "TechnitiumDhcpServer", "api_url", "technitium"),
    # Check Point：管理伺服器的網址；它管的閘道位址（管理伺服器靠這個位址跟閘道溝通、安裝政策）
    ("app.models.checkpoint", "CheckPointServer", "api_url", "checkpoint"),
    ("app.models.checkpoint", "CheckPointGateway", "ipv4_address", "checkpoint"),
    # 第二階段：閘道的 Gaia API 網址（jt-ipam 連這個位址讀 DHCP／ARP／租約）
    ("app.models.checkpoint_gaia", "CheckPointGaiaTarget", "gaia_url", "checkpoint"),
    ("app.models.windows_dhcp", "WindowsDhcpServer", "host", "windows_dhcp"),
    ("app.models.ocs", "OcsServer", "base_url", "ocs"),
    ("app.models.ocs", "OcsServer", "db_host", "ocs"),
    ("app.models.adguard", "AdGuardInstance", "api_url", "adguard"),
    ("app.models.isoinsight", "IsoInsightSource", "base_url", "isoinsight"),
    # 憑證推送目標（0227）：jt-ipam 連這個位址把憑證推到防火牆；確認位址另外列
    ("app.models.cert_push_target", "CertPushTarget", "api_url", "cert_push"),
    ("app.models.cert_push_target", "CertPushTarget", "verify_endpoint", "cert_push"),
    # 叢集的其他節點：換行或逗號分隔
    ("app.models.virt", "ProxmoxInstance", "extra_api_urls", "proxmox"),
    ("app.models.esxi", "ESXiInstance", "extra_api_urls", "esxi"),
    ("app.models.hyperv", "HyperVInstance", "extra_hosts", "hyperv"),
    # RustDesk：網頁連線連 hbbs／hbbr 的位址、客戶端連線用的位址（都可以帶 :埠）
    ("app.models.rustdesk", "RustDeskServer", "hbbs_host", "rustdesk"),
    ("app.models.rustdesk", "RustDeskServer", "relay_host", "rustdesk"),
    ("app.models.rustdesk", "RustDeskServer", "client_address", "rustdesk"),
    # 舊 pull 模式的掃描代理：伺服器主動連 agent_url
    ("app.models.scan_agent", "ScanAgent", "agent_url", "scan_agent"),
    # SNMP 收集目標的管理位址覆寫（0217）：掃描代理連這個位址讀設備。沒填覆寫時用裝置主要 IP，那是 IP 記錄本身
    ("app.models.snmp", "SnmpTarget", "ip_override", "snmp"),
    ("app.models.notification", "WebhookSubscription", "target_url", "webhook"),
)


def _hosts_in(value: str | None) -> list[Any]:
    """一個欄位裡的所有主機位址：可以是單一位址、網址、主機:埠，或以換行／逗號分隔的多個。"""
    out: list[Any] = []
    for tok in re.split(r"[,\s]+", value or ""):
        a = norm_ip(tok) or norm_ip(_host_of(tok))
        if a is not None and a not in out:
            out.append(a)
    return out


async def _system_endpoints(ctx: Ctx) -> list[tuple[str, str]]:
    """jt-ipam 自己會主動連的主機（系統設定優先、沒設才用環境變數，與各 get_* 相同）。"""
    from app.services import system_config as scfg
    ldap = await scfg.get_ldap_config(ctx.session)
    llm = await scfg.get_llm_config(ctx.session)
    fwd = await scfg.get_audit_forward(ctx.session)
    ch = await scfg.get_notification_channels(ctx.session)
    oidc = await scfg.get_oidc_config(ctx.session)
    saml = await scfg.get_saml_config(ctx.session)
    pairs = (("ldap", ldap.server), ("llm", llm.url), ("audit_forward", fwd.host), ("smtp", ch.get("smtp_host")),
             ("oidc", oidc.issuer), ("saml", saml.idp_metadata_url))
    return [(k, str(v)) for k, v in pairs if v]


async def config_refs(ctx: Ctx) -> None:
    import importlib
    targets = {r.aip: r for r in ctx.roots}
    # 子網路的閘道與 DNS 伺服器（逐物件：子網路可見才列）
    for root in ctx.roots:
        for s in (await ctx.session.execute(text("""
            SELECT id, cidr::text AS cidr, host(gateway) AS gw, dns_servers FROM subnets
             WHERE archived_at IS NULL AND (gateway = CAST(:ip AS inet) OR dns_servers ILIKE :like)
        """), {"ip": root.ip_text, "like": f"%{root.ip_text}%"})).all():
            if not ctx.can_see("subnet", s.id):
                continue
            key = ctx.add(Evidence(key=f"subnet:{s.id}", source_type="config", object_type="subnet", object_id=s.id,
                                   label=s.cidr, payload={"gateway": s.gw, "dns_servers": s.dns_servers},
                                   freshness="current", visibility=("subnet", s.id)))
            if s.gw and norm_ip(s.gw) == root.aip:
                ctx.find(Finding("config.subnet_gateway", "subnet", s.cidr, [key], subject_id=s.id,
                                 match_kind="exact", params={"address": root.ip_text, "subnet": s.cidr},
                                 visibility=("subnet", s.id)))
            if root.aip in text_mentions(s.dns_servers or "", [root.aip]):
                ctx.find(Finding("config.subnet_dns_server", "subnet", s.cidr, [key], subject_id=s.id,
                                 match_kind="exact", params={"address": root.ip_text, "subnet": s.cidr},
                                 visibility=("subnet", s.id)))
        flag = (await ctx.session.execute(text("SELECT is_dhcp_server FROM ip_addresses WHERE id = :id"),
                                          {"id": root.ip_id})).scalar()
        if flag:
            key = ctx.add(Evidence(key=f"ip_dhcp_server:{root.ip_id}", source_type="config", object_type="ip_address",
                                   object_id=root.ip_id, label=root.ip_text, payload={"is_dhcp_server": True},
                                   freshness="current", visibility=("ip", root.ip_id)))
            ctx.find(Finding("config.dhcp_server_ip", "ip_address", root.ip_text, [key], subject_id=root.ip_id,
                             match_kind="exact", params={"address": root.ip_text}, visibility=("ip", root.ip_id)))

    # VPN 通道端點（全域）
    if ctx.global_read:
        from app.models.physical import VPNTunnel
        for t in (await ctx.session.execute(select(VPNTunnel))).scalars().all():
            for side, ep in (("a", t.a_endpoint), ("b", t.b_endpoint)):
                a = norm_ip(ep)
                if a in targets:
                    key = ctx.add(Evidence(key=f"vpn_tunnel:{t.id}", source_type="config", object_type="vpn_tunnel",
                                           object_id=t.id, label=t.name, payload={"side": side, "endpoint": ep,
                                                                                  "type": t.type, "status": t.status},
                                           freshness="current"))
                    ctx.find(Finding("config.vpn_endpoint", "vpn_tunnel", t.name, [key], subject_id=t.id,
                                     match_kind="exact", params={"address": str(a), "side": side}))
    else:
        ctx.gap("config", "permission_limited", affected="vpn_endpoint")

    # 線路（ISP）的 IP、閘道、DNS：進階資源是全域資料，同 VPN
    if ctx.global_read:
        linked: set[tuple[Any, Any]] = set()   # 已經用 IPAM 關聯找到的電路，舊文字欄就不重複列
        for root in ctx.roots:
            for c in (await ctx.session.execute(text("""
                SELECT ca.id AS link_id, ca.role, ca.subnet_id, ca.address_id, c.id, c.cid, c.status,
                       p.name AS provider, s.cidr
                  FROM circuit_addresses ca
                  JOIN circuits c ON c.id = ca.circuit_id
                  LEFT JOIN providers p ON p.id = c.provider_id
                  LEFT JOIN subnets s ON s.id = ca.subnet_id
                 WHERE ca.address_id = :ip OR ca.subnet_id = :sub
            """), {"ip": root.ip_id, "sub": root.subnet_id})).all():
                linked.add((c.id, root.ip_id))
                key = ctx.add(Evidence(key=f"circuit_link:{c.link_id}", source_type="config", object_type="circuit",
                                       object_id=c.id, label=c.cid,
                                       payload={"role": c.role, "provider": c.provider, "status": c.status,
                                                "cidr": str(c.cidr) if c.cidr else None},
                                       freshness="current"))
                if c.address_id is not None:
                    ctx.find(Finding("config.circuit_wan_address", "circuit", c.cid, [key], subject_id=c.id,
                                     subject_key=f"addr:{c.link_id}", match_kind="exact",
                                     params={"address": root.ip_text, "circuit": c.cid, "circuit_role": c.role,
                                             "provider": c.provider or ""}))
                else:
                    ctx.find(Finding("config.circuit_wan_block", "circuit", c.cid, [key], subject_id=c.id,
                                     subject_key=f"block:{c.link_id}", match_kind="range_contains",
                                     params={"address": root.ip_text, "circuit": c.cid, "cidr": str(c.cidr),
                                             "provider": c.provider or ""}))
        for root in ctx.roots:
            like = f"%{root.ip_text}%"
            for c in (await ctx.session.execute(text("""
                SELECT id, cid, status, ip_address, gateway, dns_servers FROM circuits
                 WHERE ip_address ILIKE :l OR gateway ILIKE :l OR dns_servers ILIKE :l
            """), {"l": like})).all():
                for fld in ("ip_address", "gateway", "dns_servers"):
                    if fld != "dns_servers" and (c.id, root.ip_id) in linked:
                        continue
                    if root.aip not in text_mentions(getattr(c, fld) or "", [root.aip]):
                        continue
                    key = ctx.add(Evidence(key=f"circuit_cfg:{c.id}", source_type="config", object_type="circuit",
                                           object_id=c.id, label=c.cid,
                                           payload={"ip_address": c.ip_address, "gateway": c.gateway,
                                                    "dns_servers": c.dns_servers, "status": c.status},
                                           freshness="current"))
                    ctx.find(Finding("config.circuit_address", "circuit", c.cid, [key], subject_id=c.id,
                                     subject_key=fld, match_kind="exact",
                                     params={"address": root.ip_text, "field": fld, "circuit": c.cid}))
    else:
        ctx.gap("config", "permission_limited", affected="circuit")

    # 跳板主機與各整合的連線端點：管理資料，只給管理員
    if not ctx.is_admin:
        ctx.gap("config", "permission_limited", affected="integration_endpoint")
        return
    from app.models.jump_host import JumpHost
    for j in (await ctx.session.execute(select(JumpHost))).scalars().all():
        a = norm_ip(j.host)
        if a in targets:
            key = ctx.add(Evidence(key=f"jump_host:{j.id}", source_type="config", object_type="jump_host",
                                   object_id=j.id, label=j.name, payload={"host": j.host, "port": j.port},
                                   freshness="current", visibility=("admin", None)))
            ctx.find(Finding("config.jump_host", "jump_host", j.name, [key], subject_id=j.id, match_kind="exact",
                             params={"address": str(a)}, visibility=("admin", None)))
    from app.models.virt import VirtCluster
    from app.services.change_impact.labels import integration_label
    # Proxmox 連線沒有名稱欄位：用它所屬的叢集名稱
    clusters = {c.id: c.name for c in (await ctx.session.execute(select(VirtCluster))).scalars().all()}
    # SNMP 收集目標也沒有名稱欄位：用它的裝置名稱（以收集目標的 id 查）
    from app.models.device import Device
    from app.models.snmp import SnmpTarget
    clusters.update({tid: dname for tid, dname in (await ctx.session.execute(
        select(SnmpTarget.id, Device.name).join(Device, Device.id == SnmpTarget.device_id)
        .where(SnmpTarget.ip_override.is_not(None)))).all()})
    for mod, cls, attr, kind in _INTEGRATION_URLS:
        model = getattr(importlib.import_module(mod), cls, None)
        if model is None or not hasattr(model, attr):
            continue
        for row in (await ctx.session.execute(select(model))).scalars().all():
            raw = getattr(row, attr, None)
            # INET 欄位（例如 Check Point 閘道位址）讀出來是 IPv4Address，不是字串
            for a in _hosts_in(str(raw) if raw is not None and not isinstance(raw, str) else raw):
                if a not in targets:
                    continue
                host = _host_of(getattr(row, "api_url", None) or getattr(row, "base_url", None)
                                or getattr(row, "server_address", None) or getattr(row, "host", None))
                name = integration_label(kind, getattr(row, "name", None)
                                         or clusters.get(getattr(row, "cluster_id", None))
                                         or clusters.get(row.id), host)
                key = ctx.add(Evidence(key=f"integration:{kind}:{row.id}:{attr}", source_type="config",
                                       object_type="integration", object_id=row.id, label=name,
                                       payload={"kind": kind, "field": attr}, freshness="current",
                                       visibility=("admin", None)))
                ctx.find(Finding("config.integration_endpoint", "integration", name, [key],
                                 subject_id=row.id, subject_key=f"{kind}:{attr}:{a}", match_kind="exact",
                                 params={"address": str(a), "kind": kind, "field": attr},
                                 visibility=("admin", None)))
    for setting, value in await _system_endpoints(ctx):
        for a in _hosts_in(value):
            if a not in targets:
                continue
            key = ctx.add(Evidence(key=f"system_setting:{setting}", source_type="config", object_type="system_setting",
                                   label=setting, payload={"setting": setting}, freshness="current",
                                   visibility=("admin", None)))
            ctx.find(Finding("config.system_endpoint", "system_setting", "jt-ipam", [key], subject_key=f"{setting}:{a}",
                             match_kind="exact", params={"address": str(a), "setting": setting},
                             visibility=("admin", None)))


# ─────────────────── 文字線索 ───────────────────

async def text_hints(ctx: Ctx) -> None:
    """其他記錄的說明、備註、擁有者、自訂欄位裡出現這個位址：只是線索，需人工確認（規格 §5.3、T02）。

    以 token 邊界比對：198.51.100.2 不會比中寫著 198.51.100.20 的備註。
    """
    root_ids = {r.ip_id for r in ctx.roots}
    targets = [r.aip for r in ctx.roots]
    for root in ctx.roots:
        like = f"%{root.ip_text}%"
        for r in (await ctx.session.execute(text("""
            SELECT id, host(ip) AS ip, description, note, owner, custom_fields::text AS cf FROM ip_addresses
             WHERE description ILIKE :l OR note ILIKE :l OR owner ILIKE :l OR custom_fields::text ILIKE :l
             LIMIT 200
        """), {"l": like})).all():
            if r.id in root_ids or not ctx.can_see("ip", r.id):
                continue
            blob = " ".join(x for x in (r.description, r.note, r.owner, r.cf) if x)
            for a in text_mentions(blob, targets):
                _hint(ctx, "ip_address", r.id, r.ip, str(a), ("ip", r.id))
        for r in (await ctx.session.execute(text("""
            SELECT id, cidr::text AS cidr, description, custom_fields::text AS cf FROM subnets
             WHERE archived_at IS NULL AND (description ILIKE :l OR custom_fields::text ILIKE :l) LIMIT 200
        """), {"l": like})).all():
            if not ctx.can_see("subnet", r.id):
                continue
            for a in text_mentions(" ".join(x for x in (r.description, r.cf) if x), targets):
                _hint(ctx, "subnet", r.id, r.cidr, str(a), ("subnet", r.id))
        for r in (await ctx.session.execute(text("""
            SELECT id, name, description, custom_fields::text AS cf FROM devices
             WHERE description ILIKE :l OR custom_fields::text ILIKE :l LIMIT 200
        """), {"l": like})).all():
            if not ctx.can_see("device", r.id) or r.id == ctx.scenario.device_id:
                continue
            for a in text_mentions(" ".join(x for x in (r.description, r.cf) if x), targets):
                _hint(ctx, "device", r.id, r.name, str(a), ("device", r.id))


def _hint(ctx: Ctx, otype: str, oid: Any, label: str, addr: str, vis: tuple[str, Any]) -> None:
    key = ctx.add(Evidence(key=f"text:{otype}:{oid}", source_type="text", object_type=otype, object_id=oid,
                           label=label, payload={"mentions": addr}, freshness="current", visibility=vis))
    ctx.find(Finding("text.hint", otype, label, [key], subject_id=oid, match_kind="text_hint",
                     params={"address": addr}, visibility=vis))


# ─────────────────── 裝置除役：實體與關聯 ───────────────────

async def device_relations(ctx: Ctx) -> None:
    sc = ctx.scenario
    if sc.scenario_type != "device_decommission" or sc.device_id is None:
        return
    dev_id = sc.device_id
    vis = ("device", dev_id)
    if not ctx.roots:
        key = ctx.add(Evidence(key=f"device:{dev_id}", source_type="ipam", object_type="device", object_id=dev_id,
                               label=sc.device_name or "", payload={"ips": 0}, freshness="current", visibility=vis))
        ctx.find(Finding("device.no_ips", "device", sc.device_name or "", [key], subject_id=dev_id, visibility=vis))
    d = (await ctx.session.execute(text("""
        SELECT d.rack_id, r.name AS rack, d.u_position, d.u_size FROM devices d
          LEFT JOIN racks r ON r.id = d.rack_id WHERE d.id = :id
    """), {"id": dev_id})).first()
    if d is not None and d.rack_id:
        key = ctx.add(Evidence(key=f"device_rack:{dev_id}", source_type="physical", object_type="rack",
                               object_id=d.rack_id, label=d.rack or "", payload={"u_position": d.u_position,
                                                                                 "u_size": d.u_size},
                               freshness="current", visibility=vis))
        ctx.find(Finding("device.rack", "rack", d.rack or "", [key], subject_id=d.rack_id,
                         params={"u": d.u_position or "", "size": d.u_size or ""}, visibility=vis))
    if not ctx.global_read:
        # 佈線、電力、電路、VPN 都是全域資料
        ctx.gap("physical", "permission_limited", affected="physical")
        return
    cables = (await ctx.session.execute(text("""
        SELECT DISTINCT c.id, COALESCE(c.label, '') AS label, c.status FROM cables c
          JOIN cable_terminations t ON t.cable_id = c.id
         WHERE c.status <> 'decommissioned' AND (
               (t.object_type = 'device' AND t.object_id = :d)
            OR (t.object_type = 'device_port' AND t.object_id IN (SELECT id FROM device_ports WHERE device_id = :d)))
    """), {"d": dev_id})).all()
    for c in cables:
        key = ctx.add(Evidence(key=f"cable:{c.id}", source_type="physical", object_type="cable", object_id=c.id,
                               label=c.label or str(c.id)[:8], payload={"status": c.status}, freshness="current"))
        ctx.find(Finding("device.cables", "cable", c.label or str(c.id)[:8], [key], subject_id=c.id,
                         params={"status": c.status}))
    for p in (await ctx.session.execute(text("""
        SELECT pp.id, pp.name, o.label AS outlet FROM device_power_ports pp
          LEFT JOIN power_outlets o ON o.id = pp.outlet_id WHERE pp.device_id = :d
    """), {"d": dev_id})).all():
        key = ctx.add(Evidence(key=f"power_port:{p.id}", source_type="physical", object_type="device_power_port",
                               object_id=p.id, label=p.name, payload={"outlet": p.outlet}, freshness="current"))
        ctx.find(Finding("device.power", "device_power_port", p.name, [key], subject_id=p.id,
                         params={"outlet": p.outlet or ""}))
    for c in (await ctx.session.execute(text(
            "SELECT id, cid, status FROM circuits WHERE device_id = :d"), {"d": dev_id})).all():
        key = ctx.add(Evidence(key=f"circuit:{c.id}", source_type="physical", object_type="circuit", object_id=c.id,
                               label=c.cid, payload={"status": c.status}, freshness="current"))
        ctx.find(Finding("device.circuit", "circuit", c.cid, [key], subject_id=c.id, params={"status": c.status}))
    # 向供應商租用的設備：除役後要歸還（客戶許願清單 2026-10-28）
    lease = (await ctx.session.execute(text("""
        SELECT l.id, l.return_required, l.return_due, p.name AS provider, c.cid
          FROM device_leases l LEFT JOIN providers p ON p.id = l.provider_id
          LEFT JOIN circuits c ON c.id = l.circuit_id WHERE l.device_id = :d
    """), {"d": dev_id})).first()
    for h in (await ctx.session.execute(text("""
        SELECT h.id, h.kind, h.address_id, host(a.ip) AS ip FROM ip_path_hops h
          JOIN ip_addresses a ON a.id = h.address_id WHERE h.device_id = :d ORDER BY a.ip LIMIT 200
    """), {"d": dev_id})).all():
        key = ctx.add(Evidence(key=f"path_hop:{h.id}", source_type="config", object_type="ip_address",
                               object_id=h.address_id, label=h.ip, payload={"kind": h.kind},
                               freshness="current", visibility=("ip", h.address_id)))
        ctx.find(Finding("device.path_hop", "ip_address", h.ip, [key], subject_id=h.address_id,
                         subject_key=f"path_hop:{h.id}", params={"address": h.ip, "hop_kind": h.kind},
                         visibility=("ip", h.address_id)))
    if lease is not None and lease.return_required:
        key = ctx.add(Evidence(key=f"device_lease:{lease.id}", source_type="physical", object_type="device",
                               object_id=dev_id, label=sc.device_name or "",
                               payload={"provider": lease.provider, "circuit": lease.cid,
                                        "return_due": lease.return_due.isoformat() if lease.return_due else None},
                               freshness="current"))
        ctx.find(Finding("device.lease_return", "device", sc.device_name or "", [key], subject_id=dev_id,
                         subject_key="lease",
                         params={"provider": lease.provider or "", "circuit": lease.cid or "",
                                 "due": lease.return_due.isoformat() if lease.return_due else ""}))
    for t in (await ctx.session.execute(text("""
        SELECT id, name, status, a_device_id, b_device_id FROM vpn_tunnels WHERE a_device_id = :d OR b_device_id = :d
    """), {"d": dev_id})).all():
        key = ctx.add(Evidence(key=f"vpn_tunnel:{t.id}", source_type="config", object_type="vpn_tunnel",
                               object_id=t.id, label=t.name, payload={"status": t.status}, freshness="current"))
        ctx.find(Finding("device.vpn_tunnel", "vpn_tunnel", t.name, [key], subject_id=t.id,
                         params={"status": t.status}))


def json_dumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False, default=str)


def subnet_of(root: TargetAddr) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    return ipaddress.ip_network(root.subnet_cidr, strict=False)
