"""瀏覽器 e2e 用的固定樣本資料。

為什麼要有這一支：e2e 的斷言寫的是**具體的名字**（`10.20.0.10`／`web-01`／`nas-01`／
`vcenter-lab`…），而這批資料原本只存在某一台開發機手動建的資料庫裡。換一台機器
就有一半的 spec 跑不動 —— 而且失敗訊息長得跟「功能壞掉」一模一樣（元素找不到、
文字對不上），每次都要重查一輪才能確定只是沒有資料。

只能對名字以 `_e2e` 結尾的資料庫跑。位址一律用文件保留範圍（RFC 5737）與私網範例，
不從實機抄任何名稱或位址。

用法：
    cd backend
    set -a; source /opt/jt-ipam/.dev.env; set +a
    POSTGRES_DB=jt_ipam_e2e .venv/bin/python -m tests.seed_e2e
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any


def _guard() -> None:
    name = os.environ.get("POSTGRES_DB", "")
    if not name.endswith("_e2e"):
        raise SystemExit(
            f"POSTGRES_DB={name!r} —— 這支只能對 *_e2e 的拋棄式資料庫跑（會重建樣本資料）。"
        )


async def seed() -> None:
    from app.core.db import SessionLocal
    from app.models.address import IPAddress
    from app.models.ai_finding import AIFinding
    from app.models.device import Device
    from app.models.dns import DNSRecord, DNSServer, DNSZone
    from app.models.fw_snapshot import FwRuleSnapshot
    from app.models.location import Location, Rack
    from app.models.nat import NATTranslation
    from app.models.oui import OUIVendor
    from app.models.physical import DevicePort
    from app.models.section import Section
    from app.models.subnet import Subnet
    from app.models.virt import VirtCluster, VirtualMachine, VMInterface
    from sqlalchemy import delete, select

    async with SessionLocal() as s:
        async def one(cls, **kw):
            """已存在就沿用，不存在才建 —— 重跑不會爆唯一鍵，也不會長出第二份。

            第一個參數叫 `cls` 不叫 `model`：有些資料表自己就有 `model` 欄位
            （裝置的型號），叫 model 會與關鍵字參數撞名。
            """
            key, val = next(iter(kw.items()))
            row = (await s.execute(select(cls).where(getattr(cls, key) == val))).scalars().first()
            if row:
                for k, v in kw.items():
                    setattr(row, k, v)
                return row
            row = cls(**kw)
            s.add(row)
            await s.flush()
            return row

        # ── 地點 / 機櫃 ──────────────────────────────────────────────
        loc = await one(Location, name="測試機房 A", description="e2e fixture")
        rack = await one(Rack, name="RACK-01", location_id=loc.id, u_height=42)
        # 800mm 的網路機櫃：兩側走線空間比 600mm（RACK-01 沒填寬度＝當作 600）寬，
        # 設備區一樣是 19 吋 —— 機櫃圖要畫得出這個差別
        rack800 = await one(Rack, name="RACK-800", location_id=loc.id, u_height=42,
                            width_mm=800, depth_mm=1000)

        # ── 區段 / 子網路 ────────────────────────────────────────────
        sec = await one(Section, name="e2e 樣本區段", description="seed_e2e 建立")
        subnets = {}
        for cidr, desc in (
            ("10.20.0.0/24", "伺服器網段"),
            ("198.51.100.0/24", "對外服務網段"),
            ("203.0.113.0/24", "管理網段"),
            # 主控台測試靶都跑在本機（xrdp 容器、vnc-target.py、sshd 容器）
            ("127.0.0.0/24", "主控台測試靶"),
        ):
            sn = (await s.execute(select(Subnet).where(Subnet.cidr == cidr))).scalars().first()
            if not sn:
                sn = Subnet(section_id=sec.id, cidr=cidr, description=desc)
                s.add(sn)
                await s.flush()
            subnets[cidr] = sn

        # ── IP 位址 ─────────────────────────────────────────────────
        async def ip(subnet, addr, hostname, **kw):
            row = (await s.execute(select(IPAddress).where(
                IPAddress.subnet_id == subnet.id, IPAddress.ip == addr))).scalars().first()
            if not row:
                row = IPAddress(subnet_id=subnet.id, ip=addr, hostname=hostname, **kw)
                s.add(row)
                await s.flush()
            else:
                row.hostname = hostname
            return row

        web = await ip(subnets["10.20.0.0/24"], "10.20.0.10", "web-01",
                       description="e2e：IP 詳細資料的主要樣本")
        app01 = await ip(subnets["10.20.0.0/24"], "10.20.0.11", "app-01")
        db01 = await ip(subnets["10.20.0.0/24"], "10.20.0.12", "db-01")
        # 清單 MAC 欄的 OUI 廠商（#38）：用 RFC 7042 保留給文件的 00:00:5E:00:53:xx，
        # 它的 OUI 正式登記給 IANA —— 不會對到任何真實設備。
        db01.mac = "00:00:5e:00:53:12"
        await s.merge(OUIVendor(prefix="00005E", short_name="IANA", name="ICANN, IANA Department",
                                source="e2e"))
        # 主控台 e2e（含 guacd 引擎）：RDP 固定 3389（xrdp 測試靶）、VNC／SSH 的埠在表單上填
        console_ip = await ip(subnets["127.0.0.0/24"], "127.0.0.1", "console-target")
        console_ip.ssh_enabled = True
        console_ip.rdp_enabled = True
        console_ip.vnc_enabled = True
        pub = await ip(subnets["198.51.100.0/24"], "198.51.100.7", "web.example.net",
                       description="e2e：對外開放服務的樣本")
        # OCS 整合頁（代理數要一台電腦一筆）：web-01 與 198.51.100.7 是同一台 OCS 電腦的兩個 IP
        ocs_seen = datetime(2026, 9, 24, 7, 0, tzinfo=UTC)
        for row in (web, pub):
            row.ocs_id, row.last_seen_ocs = 101, ocs_seen
            row.os_ocs, row.ocs_agent, row.ocs_tag = "Ubuntu 24.04 LTS", "OCS-NG_unified_unix_agent_v2.10.0", "E2E"
        await ip(subnets["203.0.113.0/24"], "203.0.113.5", "ipmi-host-a",
                 description="e2e：管理介面樣本")

        # ── 裝置與連接埠 ─────────────────────────────────────────────
        # ⚠️ U 位置刻意放在機櫃頂端（42U 機櫃的 38~41）：機櫃圖由上往下畫，
        # 位置低的裝置在預設視窗下要捲動才看得到，而 e2e 用的是原始座標
        # `mouse.move`（不像 `.hover()` 會自動捲動）—— 放低了 hover 類的斷言
        # 會失敗，而且失敗訊息看起來像功能壞掉。
        nas = await one(Device, name="nas-01", type="storage", vendor="generic",
                        model="NAS-2000", location_id=loc.id, rack_id=rack.id,
                        u_position=40, u_size=2, rack_face="front")
        sw = await one(Device, name="sw-e2e-01", type="switch", vendor="generic",
                       model="SW-48G", location_id=loc.id, rack_id=rack.id,
                       u_position=38, u_size=1, rack_face="front")
        # 三種新型態（rack-more-kinds.spec）：角鋼層架 90×45×180 四層、KALLAX 2×4、兩張疊起來的 LackRack
        angle = await one(Rack, name="ANGLE-90", location_id=loc.id, kind="angle_shelf", u_height=3,
                          width_mm=900, depth_mm=450, finish="black")
        kallax = await one(Rack, name="KALLAX-24", location_id=loc.id, kind="kallax", u_height=4,
                           width_mm=765, depth_mm=390, finish="white")
        lack = await one(Rack, name="LACK-16", location_id=loc.id, kind="lackrack", u_height=16,
                         width_mm=550, depth_mm=550, finish="white")
        # 尺寸照實物（一層橫向與層內上下各 60 格）：90 公分的角鋼一格 15mm、KALLAX 一格 11.4mm。
        # 19 吋交換器 440 寬就是 29 格，不是整層 —— 範例畫成整層寬會被使用者一眼看穿。
        for name, rk, pos, size, slot, span, vspan in (
            ("nas-angle", angle, 1, 1, 0, 14, 27), ("sw-angle", angle, 2, 1, 0, 29, 5),
            ("nas-kallax", kallax, 2, 1, 0, 18, 41), ("ups-kallax", kallax, 1, 1, 30, 13, 43),
            ("sw-lack", lack, 15, 1, 0, 60, 60), ("srv-lack", lack, 6, 2, 0, 60, 60),
            ("ap-lack", lack, 17, 1, 0, 20, 60),             # 放在桌面上（第 17 個位置）
        ):
            await one(Device, name=name, type="storage" if name.startswith(("nas", "ups")) else
                      ("switch" if name.startswith("sw") else "server"),
                      vendor="generic", model="E2E", location_id=loc.id, rack_id=rk.id,
                      u_position=pos, u_size=size, rack_slot=slot, rack_slot_span=span,
                      rack_vslot=0, rack_vslot_span=vspan, rack_face="front")

        # 名稱置中（rack-label.spec）：1U／2U／3U 各一台，放在有兩側走線空間最寬的 800mm 機櫃，
        # 走線空間改的是水平方向，順便確認它沒把垂直置中弄歪
        for name, pos, size in (("dev-1u", 41, 1), ("dev-2u", 38, 2), ("dev-3u", 34, 3)):
            await one(Device, name=name, type="server", vendor="generic", model=f"SRV-{size}U",
                      location_id=loc.id, rack_id=rack800.id, u_position=pos, u_size=size,
                      rack_face="front")
        for dev, names in ((nas, ["eth0", "eth1"]), (sw, ["Gi0/1", "Gi0/2"])):
            for i, n in enumerate(names):
                exists = (await s.execute(select(DevicePort).where(
                    DevicePort.device_id == dev.id, DevicePort.name == n))).scalars().first()
                if not exists:
                    s.add(DevicePort(device_id=dev.id, name=n, type="ethernet", position=i))
        web.device_id = nas.id

        # ── 虛擬化（deep-sweep 期待 vcenter-lab / app-01）────────────
        cluster = await one(VirtCluster, name="vcenter-lab", type="vmware",
                            is_standalone=False, location_id=loc.id,
                            description="e2e fixture")
        # 虛擬化頁會提示「同時有 Proxmox VE 與 VMware」——只有一種平台時提示不出現
        pve = await one(VirtCluster, name="pve-lab", type="proxmox",
                        is_standalone=True, location_id=loc.id, description="e2e fixture")
        if not (await s.execute(select(VirtualMachine).where(
                VirtualMachine.name == "ct-log-01"))).scalars().first():
            s.add(VirtualMachine(cluster_id=pve.id, name="ct-log-01", legacy_vmid=101,
                                 node="pve-01", kind="ct", status="running",
                                 vcpus=2, memory_mb=2048, disk_gb=20))

        # noVNC 主控台（novnc-saved-cred.spec）：一台有 IP 的 PVE VM，連線實例指到 RFC 5737 的
        # 保留位址 —— 一定連不上，測的是「存帳密 → 連線失敗 → 回到表單」那一段，不需要真的 PVE。
        from app.models.virt import ProxmoxInstance
        if not (await s.execute(select(ProxmoxInstance).where(
                ProxmoxInstance.cluster_id == pve.id))).scalars().first():
            s.add(ProxmoxInstance(cluster_id=pve.id, api_url="https://198.51.100.250:8006",
                                  auth_username="e2e@pve", auth_token_id="e2e", enabled=True))
        novnc_ip = await ip(subnets["10.20.0.0/24"], "10.20.0.232", "vm-novnc-01")
        novnc_ip.novnc_enabled = True
        if not (await s.execute(select(VirtualMachine).where(
                VirtualMachine.name == "vm-novnc-01"))).scalars().first():
            s.add(VirtualMachine(cluster_id=pve.id, name="vm-novnc-01", legacy_vmid=102,
                                 node="pve-01", kind="vm", status="running",
                                 primary_ip_id=novnc_ip.id))

        vm = (await s.execute(select(VirtualMachine).where(
            VirtualMachine.name == "app-01"))).scalars().first()
        if not vm:
            vm = VirtualMachine(cluster_id=cluster.id, name="app-01", external_id="vm-101",
                                node="esx-01.example.net", kind="vm", status="running",
                                vcpus=4, memory_mb=8192, disk_gb=80)
            s.add(vm)
            await s.flush()
        if not (await s.execute(select(VMInterface).where(VMInterface.vm_id == vm.id))).scalars().first():
            s.add(VMInterface(vm_id=vm.id, name="nic0", mac="00:00:5e:00:53:01",
                              primary_ip="10.20.0.11", bridge="VM Network"))

        # ── IP 詳細的「各來源最後出現」＋點時間跳到裝置頁的卡片＋虛實標 LXC（ip-seen-sources.spec）──
        # 專用一個位址與裝置，不動 web-01：在既有樣本上補掃描／監控時間會改變它的上線判定，
        # 別的 spec 看的是現在的樣子。時間每次 seed 重新錨定（「距今」才會固定）。
        from app.models.librenms import LibreNMSDevice, LibreNMSInstance
        from app.models.wazuh import WazuhAgent, WazuhInstance
        seen_dev = await one(Device, name="seen-host-01", type="server", vendor="generic", model="E2E")
        seen_ip = await ip(subnets["10.20.0.0/24"], "10.20.0.40", "seen-host-01")
        seen_ip.rustdesk_enabled = True     # 「以 RustDesk 連線」按鈕要逐 IP 開啟（0179）
        # 主控台「連線路徑 → 變更」用（e2e/console-route-note.spec.ts 會改它的出口再還原；不用共用的 console-target）
        await ip(subnets["10.20.0.0/24"], "10.20.0.41", "route-edit-01")
        seen_ip.device_id = seen_dev.id
        seen_dev.primary_ip_id = seen_ip.id
        _n = datetime.now(UTC)
        seen_ip.last_seen_scanner = _n - timedelta(minutes=5)
        seen_ip.last_seen_librenms = _n - timedelta(hours=3)
        seen_ip.last_seen_wazuh = _n - timedelta(days=2)
        lnms = await one(LibreNMSInstance, name="lnms-seen-e2e", api_url="https://librenms.example.net",
                         api_token_enc=b"x", api_token_nonce=b"y", enabled=False)
        await one(LibreNMSDevice, legacy_device_id=9001, instance_id=lnms.id, hostname="seen-host-01",
                  os="linux", jt_ipam_device_id=seen_dev.id)
        wz = await one(WazuhInstance, name="wazuh-seen-e2e", api_url="https://wazuh.example.net",
                       api_user="e2e", api_password_enc=b"x", api_password_nonce=b"y", enabled=False)
        await one(WazuhAgent, agent_id="901", instance_id=wz.id, name="seen-host-01",
                  status="disconnected", jt_ipam_address_id=seen_ip.id)
        # ── RustDesk Server（開源版）：一台伺服器、三個裝置（對應到 seen-host-01／NAT 共用／不在管理網段）──
        from app.models.rustdesk import RustDeskPeer, RustDeskServer
        from app.services import rustdesk as rustdesk_svc
        rd = await one(RustDeskServer, name="rd-e2e", client_address="rd.example.net",
                       agent_version=rustdesk_svc.agent_latest_version(), agent_last_seen_at=_n,
                       agent_hostname="rd-host-e2e", agent_source_ip="192.0.2.73",
                       agent_status={"data_dir": "/var/lib/rustdesk-server",
                                     "receiver": {"listening": True, "port": 21114, "error": None}},
                       public_key="E2EfakeRustDeskKeyForTestsOnly0000000000000=", server_version="1.1.16",
                       last_report_at=_n, file_status={"db": {"path": "/var/lib/rustdesk-server/db_v2.sqlite3",
                                                              "ok": True, "error": None, "truncated": False}},
                       last_summary={"peers": 3, "online": 2, "matched": 1, "removed": 0, "online_ok": True})
        await rustdesk_svc.save_agent_key(s, rd, "rustdesk-e2e-agent-key-" + "0" * 20)
        for rid, rip, on, status, addr in (
                ("100200300", "10.20.0.40", True, "matched", seen_ip.id),
                ("100200301", "192.0.2.1", True, "shared", None),
                ("100200302", "203.0.113.77", False, "unmanaged", None)):
            await one(RustDeskPeer, rustdesk_id=rid, server_id=rd.id, registered_ip=rip, online=on,
                      last_online_at=_n - timedelta(minutes=2) if on else _n - timedelta(days=3),
                      match_status=status, address_id=addr, first_registered_at=_n - timedelta(days=90))
        # 客戶端回報（系統資訊、心跳）與多訊號對應的依據
        p300 = (await s.execute(select(RustDeskPeer).where(RustDeskPeer.rustdesk_id == "100200300"))).scalars().first()
        p300.hostname, p300.os_name, p300.username, p300.client_version = "seen-host-01", "Windows 11 Pro", "alice", "1.5.0"
        # 心跳比掃描代理早：同一個 IP 的「各來源最後出現」測試要掃描代理是最新的那一列
        p300.report_ip, p300.report_ip_at, p300.last_heartbeat_at = "10.20.0.40", _n, _n - timedelta(hours=6)
        p300.match_evidence = ["hostname", "registered_ip", "report_ip"]
        # 稽核：一次完整的連線（建立→驗證→檔案→結束）與一筆暴力破解告警
        from app.models.rustdesk import RustDeskAuditEvent
        await s.execute(delete(RustDeskAuditEvent).where(RustDeskAuditEvent.server_id == rd.id))
        for i, (kind, action, extra) in enumerate((
                ("conn", "new", {"ip": "198.51.100.20"}),
                ("conn", "auth", {"peer_id": "999888777", "peer_name": "helpdesk", "conn_type": 0, "session_id": "4242"}),
                ("file", None, {"peer_id": "999888777", "peer_name": "helpdesk", "ip": "198.51.100.20",
                                "detail": {"direction": 0, "path": "C:/Users/alice/Desktop", "is_file": True, "num": 1,
                                           "files": [["report.pdf", 20480]]}}),
                ("conn", "close", {"session_id": "4242"}),
                ("alarm", None, {"alarm_type": 2, "ip": "203.0.113.66", "peer_id": "555444333",
                                 "detail": {"ip": "203.0.113.66", "id": "555444333", "name": "unknown"}}))):
            s.add(RustDeskAuditEvent(server_id=rd.id, kind=kind, action=action, rustdesk_id="100200300", conn_id=7,
                                     nonce=f"e2e-{i}", occurred_at=_n - timedelta(minutes=30 - i), src_ip="10.20.0.40",
                                     **extra))
        ct = (await s.execute(select(VirtualMachine).where(VirtualMachine.name == "ct-log-01"))).scalars().first()
        if ct and not (await s.execute(select(VMInterface).where(VMInterface.vm_id == ct.id))).scalars().first():
            s.add(VMInterface(vm_id=ct.id, name="net0", primary_ip="10.20.0.40", bridge="vmbr0"))

        # ── 對外開放服務（NAT port forward 指到已登錄的 IP）──────────
        if not (await s.execute(select(NATTranslation).where(
                NATTranslation.name == "e2e-https-forward"))).scalars().first():
            s.add(NATTranslation(name="e2e-https-forward", type="port_forward",
                                 dst_ip_id=pub.id, dst_port=443, protocol="tcp",
                                 src_interface="wan", description="e2e fixture",
                                 disabled=False, source_origin="seed:e2e"))

        # ── DNS（FQDN 視角要看到 A 與 CNAME 各自成列）────────────────
        dsrv = await one(DNSServer, name="e2e-dns", type="bind9",
                         server_address="192.0.2.53", enabled=False)
        zone = (await s.execute(select(DNSZone).where(DNSZone.name == "example.net"))).scalars().first()
        if not zone:
            zone = DNSZone(server_id=dsrv.id, name="example.net", type="forward",
                           managed=False, associated_subnet_ids=[])
            s.add(zone)
            await s.flush()
        for nm, rtype, val in (("web.example.net", "A", "198.51.100.7"),
                               ("meet.example.net", "CNAME", "web.example.net")):
            if not (await s.execute(select(DNSRecord).where(DNSRecord.name == nm))).scalars().first():
                s.add(DNSRecord(zone_id=zone.id, name=nm, type=rtype, value=val, ttl=300,
                                source="from_dns_pulled", consistency_state="consistent",
                                ipam_address_id=pub.id if rtype == "A" else None,
                                last_seen_at=datetime.now(UTC)))

        # ── DNS 比對群組（2026-10-10）：兩台大部分相同、一筆只在 ns1 上 ─────────
        # 成員要「啟用」才會參與比對；e2e 沒有跑同步排程，所以不會真的去連 192.0.2.x
        from app.models.dns_compare_group import DNSCompareGroup
        from app.services.dns_compare import normalize_name, normalize_value
        grp = await one(DNSCompareGroup, name="e2e-dns-group", grace_minutes=0, notify_enabled=False, excluded_zones=[])
        # 兩台同一時間拉取：差異要「沒有的那台在差異出現後拉取過」才確認，時間錯開幾毫秒就會變成待確認
        pulled_at = datetime.now(UTC)
        await s.flush()
        for srv_name, addr, recs in (
            ("e2e-ns1", "192.0.2.61", (("www.corp.example", "A", "192.0.2.80"), ("mail.corp.example", "A", "192.0.2.81"),
                                       ("legacy.corp.example", "A", "192.0.2.82"))),
            ("e2e-ns2", "192.0.2.62", (("WWW.corp.example", "A", "192.0.2.80"), ("mail.corp.example", "A", "192.0.2.81"))),
        ):
            gs = await one(DNSServer, name=srv_name, type="bind9", server_address=addr, enabled=True,
                           compare_group_id=grp.id, last_sync_at=pulled_at, last_error=None)
            await s.flush()
            gz = (await s.execute(select(DNSZone).where(DNSZone.server_id == gs.id,
                                                        DNSZone.name == "corp.example"))).scalars().first()
            if not gz:
                gz = DNSZone(server_id=gs.id, name="corp.example", type="forward", managed=False,
                             associated_subnet_ids=[])
                s.add(gz)
                await s.flush()
            for nm, rtype, val in recs:
                rec = (await s.execute(select(DNSRecord).where(DNSRecord.zone_id == gz.id,
                                                               DNSRecord.name == nm))).scalars().first()
                if not rec:
                    s.add(DNSRecord(zone_id=gz.id, name=nm, type=rtype, value=val, ttl=300,
                                    source="from_dns_pulled", consistency_state="dns_only",
                                    last_seen_at=datetime.now(UTC),
                                    name_norm=normalize_name(nm, "corp.example"),
                                    value_norm=normalize_value(rtype, val)))
                else:
                    # 資料庫退回再升級過 0203 時正規化欄位是空的（＝升級後第一輪拉取前），e2e 要固定結果就補上
                    rec.name_norm = normalize_name(nm, "corp.example")
                    rec.value_norm = normalize_value(rtype, val)
        ns2 = (await s.execute(select(DNSServer).where(DNSServer.name == "e2e-ns2"))).scalars().first()
        xz = (await s.execute(select(DNSZone).where(DNSZone.server_id == ns2.id,
                                                    DNSZone.name == "extra.example"))).scalars().first()
        if not xz:
            xz = DNSZone(server_id=ns2.id, name="extra.example", type="forward", managed=False, associated_subnet_ids=[])
            s.add(xz)
            await s.flush()
        if not (await s.execute(select(DNSRecord).where(DNSRecord.zone_id == xz.id))).scalars().first():
            s.add(DNSRecord(zone_id=xz.id, name="only.extra.example", type="A", value="192.0.2.83", ttl=300,
                            source="from_dns_pulled", consistency_state="dns_only", last_seen_at=datetime.now(UTC),
                            name_norm="only.extra.example", value_norm="192.0.2.83"))

        # ── AI 巡檢發現（UI 測的是渲染，不是模型；沒有 Ollama 也要跑得動）──
        await s.execute(delete(AIFinding).where(AIFinding.model == "gemma4:26b"))
        run_id = uuid.uuid4()
        findings = [
            # 第一筆是**給「忽略」測試用掉的**：spec 是序列跑的，AI 巡檢那組會把最上面
            # 那張卡片忽略掉，之後 deep-sweep 還要看得到「管理介面位於一般用途子網路」。
            # 沒有這筆誘餌的話，兩組測試只能有一組過，而失敗訊息看起來像功能壞掉。
            ("high", "naming", "多個位址共用同一個主機名稱",
             "198.51.100.7 與 10.20.0.10 在不同來源都回報同一個名稱，值班時會指向錯的機器。",
             "確認哪一個才是正解，並把另一個來源的名稱更正或停用。",
             {"ips": ["198.51.100.7", "10.20.0.10"]}),
            ("high", "exposure", "管理介面位於一般用途子網路",
             "203.0.113.5 是 IPMI 管理介面，卻登錄在一般用途的子網路裡。管理平面與服務"
             "平面沒有分開時，任何一台被攻下的服務主機都能直接打到管理介面。",
             "把管理介面移到獨立的管理網段，並限制只有跳板可以連。",
             {"ips": ["203.0.113.5"]}),
            # 至少要有**兩筆帶 IP 的發現**：spec 是序列跑的，前一條測試會忽略掉第一筆，
            # 只留一筆帶 IP 的話，下一條「依據資料的 IP 可以點過去查證」就找不到可點的
            # 位址 —— 失敗訊息看起來像功能壞掉，其實是樣本不夠撐過前一條測試。
            ("medium", "naming", "同一台主機在不同來源有不同名稱",
             "10.20.0.10 在 DNS 與 IPAM 裡的名稱不一致，值班時會對不起來。",
             "以其中一個來源為準，或在來源優先序裡調整順序。",
             {"ips": ["10.20.0.10"]}),
            ("medium", "coverage", "有子網路完全沒有任何監測來源",
             "10.20.0.0/24 裡的位址沒有任何一種存活證據來源（掃描代理／監控／防火牆），"
             "所以「上線」欄位在這個網段一律是未知。",
             "在該網段指派一台掃描代理，或把它納入既有監控的範圍。",
             {"subnets": ["10.20.0.0/24"]}),
        ]
        # 清單依 created_at 由新到舊排。**時間要各自寫死**：同一個交易裡插入的話
        # server_default 會給出一模一樣的時間戳，排序就變成不定 —— e2e 靠「第一張卡片」
        # 操作，順序不定等於測試會間歇性失敗。
        now = datetime.now(UTC)
        for i, (sev, cat, title, detail, rec, ev) in enumerate(findings):
            s.add(AIFinding(run_id=run_id, severity=sev, category=cat, title=title,
                            detail=detail, recommendation=rec, evidence=ev,
                            model="gemma4:26b", status="open",
                            created_at=now - timedelta(minutes=5 * i),
                            fingerprint=hashlib.sha256(title.encode()).hexdigest()[:32]))

        # ── 一個「頻繁更換 MAC」的樣本（開了隱私隨機化的裝置）──────
        # 位址是本地管理位址（第二個十六進位字元是 2/6/a/e），與真實裝置的 MAC 區分得開。
        from app.models.librenms import ARPEntry
        now_arp = datetime.now(UTC)
        for i, mac in enumerate(["0a1b2c000001", "0a1b2c000002", "0e1b2c000003",
                                 "021b2c000004", "0a1b2c000005", "0e1b2c000006"]):
            row = (await s.execute(select(ARPEntry).where(
                ARPEntry.ip == "10.20.0.10", ARPEntry.mac == mac))).scalars().first()
            if row is None:
                row = ARPEntry(ip="10.20.0.10", mac=mac)
                s.add(row)
            # **時間一定要重新錨定，不能「已經有就跳過」**：偵測規則看的是「最近 7 天」，
            # 而這些列的時間是相對 now 算出來的 —— 只建立不更新的話，同一份 fixture
            # 過一個星期就落到窗外，測試開始失敗，訊息卻是「等不到表格列」，
            # 看起來像功能壞了。實際上是 fixture 過期了。
            row.first_seen_at = now_arp - timedelta(days=5)
            row.last_seen_at = now_arp - timedelta(hours=i * 6)
        # ── ARP 資料品質（e2e/anomaly-arp-quality.spec.ts，2026-10-09）─────────────────
        # 雙網卡主機：A 有三台設備看過、B 兩台；路由器 ARP 表切換時 SNMP 讀到一半拼出的兩個假 MAC 只有它回報。
        # 交換器同一個 VLAN 學到兩個子網段的 MAC → 子網段混在同一個二層。另一個 IP 的第二台機器只有一個來源 → 低可信度。
        from app.models.librenms import FDBEntry
        flux_dev = await one(Device, name="arpflux-host-01", type="server", vendor="generic", model="E2E")
        mac_a, mac_b = "02:00:5e:30:00:a1", "06:00:5e:40:00:b2"
        flux_ip = await ip(subnets["10.20.0.0/24"], "10.20.0.50", "arpflux-host-01")
        flux_ip2 = await ip(subnets["203.0.113.0/24"], "203.0.113.50", "arpflux-host-01-nic2")
        flux_ip.mac, flux_ip.device_id = mac_a, flux_dev.id
        flux_ip2.mac, flux_ip2.device_id = mac_b, flux_dev.id
        low_ip = await ip(subnets["10.20.0.0/24"], "10.20.0.60", "low-confidence-01")
        low_ip.mac = "00:00:5e:00:53:c1"
        aq_lnms = await one(LibreNMSInstance, name="lnms-arpq-e2e", api_url="https://librenms-arpq.example.net",
                            api_token_enc=b"x", api_token_nonce=b"y", enabled=False)
        aq = {}
        for n, legacy in (("edge-router-e2e", 9101), ("core-sw-e2e", 9102), ("access-sw-e2e", 9103)):
            aq[n] = await one(LibreNMSDevice, legacy_device_id=legacy, instance_id=aq_lnms.id,
                              hostname=f"192.0.2.{legacy - 9000}", sysname=n)
        _now = datetime.now(UTC)

        async def _arp(ip_text: str, mac: str, dev: Any) -> None:
            row = (await s.execute(select(ARPEntry).where(
                ARPEntry.ip == ip_text, ARPEntry.mac == mac, ARPEntry.device_id == dev.id))).scalars().first()
            if row is None:
                row = ARPEntry(ip=ip_text, mac=mac, device_id=dev.id, instance_id=dev.instance_id,
                               source="librenms", interface="igb0")
                s.add(row)
            row.first_seen_at = _now - timedelta(days=3)
            row.last_seen_at = _now - timedelta(minutes=5)       # 每次 seed 重新錨定（偵測看最近 1 小時）

        for n in aq:
            await _arp("10.20.0.50", mac_a, aq[n])
        await _arp("10.20.0.50", mac_b, aq["edge-router-e2e"])
        await _arp("10.20.0.50", mac_b, aq["core-sw-e2e"])
        await _arp("10.20.0.50", "02:00:5e:40:00:b2", aq["edge-router-e2e"])   # 拼接
        await _arp("10.20.0.50", "06:00:5e:30:00:a1", aq["edge-router-e2e"])   # 拼接
        await _arp("203.0.113.50", mac_b, aq["edge-router-e2e"])
        await _arp("10.20.0.60", "00:00:5e:00:53:c1", aq["edge-router-e2e"])
        await _arp("10.20.0.60", "00:00:5e:00:53:c1", aq["core-sw-e2e"])
        await _arp("10.20.0.60", "00:00:5e:00:53:c9", aq["access-sw-e2e"])      # 只有一個來源
        for mac, port in ((mac_a, "Gi1/0/5"), (mac_b, "Gi1/0/25")):
            row = (await s.execute(select(FDBEntry).where(
                FDBEntry.mac == mac, FDBEntry.device_id == aq["core-sw-e2e"].id))).scalars().first()
            if row is None:
                row = FDBEntry(mac=mac, device_id=aq["core-sw-e2e"].id, instance_id=aq_lnms.id,
                               vlan_id_num=1, port_name=port, source="librenms")
                s.add(row)
            row.first_seen_at = _now - timedelta(days=2)
            row.last_seen_at = _now - timedelta(minutes=5)

        # ⚠️ 忽略清單一定要重設：e2e 會按「忽略這個 IP」，不重設的話第二次跑就
        # 什麼都看不到 —— 而失敗訊息長得像「功能壞了」。
        web.anomaly_ignore = []

        # ⚠️ 語言也要重設。幾乎每一支 spec 都用中文字串找元素，而語言是**存在帳號上**
        # 的偏好 —— 任何跑過日文或英文畫面的工具（locale-ja.spec、量版面的臨時腳本）
        # 只要沒還原，下一次整套跑就會有一大批「找不到元素」。那看起來像功能壞了，
        # 實際上只是上一輪留下的狀態。
        from app.models.user import UserPreference
        for pref in (await s.execute(select(UserPreference))).scalars().all():
            pref.locale = "zh-TW"
        subnets["10.20.0.0/24"].anomaly_enabled = True

        # ── 設備類型（掃描代理定期偵測的判讀，Recog 的其他用途）＋「類型或 OS 突變」的樣本 ──
        from app.models.ip_change_log import IPChangeLog
        db01.device_kind, db01.device_model = "storage", "Synology DS920+"
        app01.device_kind, app01.device_model = "windows", None
        app01.os_family, app01.os_guess = "windows", "Microsoft Windows 10"
        app01.anomaly_ignore = []
        await s.execute(delete(IPChangeLog).where(
            IPChangeLog.ip_id == app01.id, IPChangeLog.event_type.in_(("kind_changed", "os_changed"))))
        for ev, fld, old, new in (("kind_changed", "device_kind", "printer", "windows"),
                                  ("os_changed", "os_family", "linux", "windows")):
            s.add(IPChangeLog(ip_id=app01.id, subnet_id=app01.subnet_id, ip_text="10.20.0.11",
                              event_type=ev, field=fld, old_value=old, new_value=new, source="scanner"))

        # ── MAC 歷程：db-01 的 MAC 以前在 app-01（10.20.0.11）上，被另一個 MAC 取代後搬到 db-01 ──
        await s.execute(delete(IPChangeLog).where(
            IPChangeLog.field == "mac", IPChangeLog.ip_id.in_([app01.id, db01.id])))
        from datetime import UTC as _UTC
        from datetime import datetime as _dt
        from datetime import timedelta as _td
        _now = _dt.now(_UTC)
        for ipa, txt, old, new, ago in ((app01, "10.20.0.11", None, "00:00:5e:00:53:12", 30),
                                        (app01, "10.20.0.11", "00:00:5e:00:53:12", "00:00:5e:00:53:11", 12),
                                        (db01, "10.20.0.12", "00:00:5e:00:53:19", "00:00:5e:00:53:12", 11)):
            s.add(IPChangeLog(ip_id=ipa.id, subnet_id=ipa.subnet_id, ip_text=txt, event_type="mac_changed",
                              field="mac", old_value=old, new_value=new, source="scanner",
                              created_at=_now - _td(days=ago)))

        # ── 防火牆反查（IP 詳細頁的「防火牆規則」「所屬別名」）──────────
        # 198.51.100.7 被一個別名涵蓋，另有一條引用那個別名的規則 —— 兩段都要畫得出來。
        from app.models.firewall import OPNsenseFirewall, OPNsenseSyncedAlias
        from app.models.firewall_rule import OPNsenseRule
        fw = (await s.execute(select(OPNsenseFirewall).where(
            OPNsenseFirewall.name == "fw-e2e"))).scalars().first()
        if not fw:
            fw = OPNsenseFirewall(name="fw-e2e", api_url="https://192.0.2.1",
                                  api_key_enc=b"x", api_key_nonce=b"y",
                                  api_secret_enc=b"x", api_secret_nonce=b"y")
            s.add(fw)
            await s.flush()
            s.add(OPNsenseSyncedAlias(firewall_id=fw.id, name="web_hosts", alias_type="host",
                                      enabled=True, content=["198.51.100.7"],
                                      description="e2e：對外網站主機"))
            s.add(OPNsenseRule(firewall_id=fw.id, legacy_uuid="e2e-rule-1", enabled=True,
                               action="pass", interface="wan", protocol="tcp",
                               source_net="any", destination_net="web_hosts",
                               destination_port="443", description="e2e：HTTPS 進站"))
            await s.flush()
        # 第二條長短不同的規則：驗「欄位對齊」要有兩列以上（後補的，舊 DB 也要補得上）
        if not (await s.execute(select(OPNsenseRule).where(
                OPNsenseRule.legacy_uuid == "e2e-rule-2"))).scalars().first():
            s.add(OPNsenseRule(firewall_id=fw.id, legacy_uuid="e2e-rule-2", enabled=True,
                               action="block", interface="lan", protocol="any",
                               source_net="203.0.113.0/24", destination_net="198.51.100.7",
                               description="e2e：擋掉測試網段直連對外網站主機（說明刻意寫長一點）"))

        # ── 防火牆規則異動（zz-fwchanges-ai 期待 router-e2e）─────────
        if not (await s.execute(select(FwRuleSnapshot).where(
                FwRuleSnapshot.instance_name == "router-e2e"))).scalars().first():
            rules = [{"id": "1", "action": "pass", "interface": "wan",
                      "destination": "198.51.100.7", "destination_port": "8443",
                      "description": "e2e fixture rule"}]
            s.add(FwRuleSnapshot(
                source_type="opnsense", instance_id=uuid.uuid4(), instance_name="router-e2e",
                taken_at=datetime.now(UTC) - timedelta(hours=1),
                rules_hash=hashlib.sha256(b"e2e").hexdigest(), rule_count=len(rules),
                rules=rules, diff={"added": rules, "removed": [], "changed": []}))

        # ── 指示計的「未納管」格子（subnet-grid-unmanaged.spec）：10.20.0.0/24 裡沒有 IP 記錄、但看得到在用的兩個位址 ──
        from app.models.subnet import Subnet as _Sub
        from app.models.unmanaged_sighting import UnmanagedSighting
        sub_um = (await s.execute(select(_Sub).where(_Sub.cidr == "10.20.0.0/24"))).scalars().first()
        if sub_um is not None:
            now_um = datetime.now(UTC)
            for ip_um, age, mac_um, host_um in (("10.20.0.240", timedelta(minutes=3), "00:00:5e:00:53:f0", "laptop-07"),
                                                ("10.20.0.241", timedelta(days=2), None, None)):
                row = (await s.execute(select(UnmanagedSighting).where(
                    UnmanagedSighting.subnet_id == sub_um.id, UnmanagedSighting.ip == ip_um))).scalars().first()
                if row is None:
                    row = UnmanagedSighting(subnet_id=sub_um.id, ip=ip_um, source="scanner", first_seen_at=now_um - age)
                    s.add(row)
                row.last_seen_at, row.mac, row.hostname = now_um - age, mac_um, host_um

        # ── 作業頁（tasks-filters.spec）：代理回報、資料庫更新、沒有錯誤訊息的失敗探測 ─────
        from app.models.background_task import BackgroundTask
        now = datetime.now(UTC)
        for kind, trig, label, st, summ, err in (
            ("rustdesk.sync", "scheduled", "rd-e2e", "succeeded",
             {"peers": 3, "online": 2, "matched": 1, "removed": 0}, None),
            ("oui.refresh", "scheduled", "Wireshark manuf", "succeeded",
             {"downloaded": 1, "parsed": 39000, "inserted": 12, "updated": 3}, None),
            ("ip.identify", "manual", "198.51.100.250 (e2e-probe)", "failed",
             {"job_id": "e2e", "agent": "agent-e2e", "ip": "198.51.100.250"}, None),
        ):
            if not (await s.execute(select(BackgroundTask).where(
                    BackgroundTask.kind == kind, BackgroundTask.target_label == label))).scalars().first():
                s.add(BackgroundTask(kind=kind, trigger=trig, target_label=label, status=st, progress=100,
                                     summary=summ, error=err, queued_at=now, started_at=now, finished_at=now))

        # 儀表板的 AI 巡檢區塊看 `/me` 的 ai_enabled（＝全域 LLM 開關），沒開就整塊
        # 不渲染 —— 那是設計，不是壞掉。這裡只把開關打開，不需要真的有 Ollama：
        # 區塊要的資料來自 ai-audit 的摘要端點，不會去呼叫模型。
        from app.models.system_setting import SystemSetting
        from app.services.system_config import LLM_KEY
        row = (await s.execute(select(SystemSetting).where(
            SystemSetting.key == LLM_KEY))).scalars().first()
        value = dict(row.value or {}) if row else {}
        value.update({"enabled": True, "url": "http://127.0.0.1:11434",
                      "chat_model": "gemma4:26b",
                      "embedding_model": "granite-embedding:278m"})
        if row:
            row.value = value
        else:
            s.add(SystemSetting(key=LLM_KEY, value=value))

        await s.commit()
        print("seed 完成：區段/子網路/IP/裝置/機櫃/虛擬化/NAT/DNS/AI 發現/防火牆快照")


if __name__ == "__main__":
    _guard()
    asyncio.run(seed())
