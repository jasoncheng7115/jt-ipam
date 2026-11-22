"""掃描代理定期偵測接上 Recog（2026-10-01，Recog 的其他用途 ①）。

代理 1.14.0 起，定期 OS 偵測連同 nmap 結構化結果一起回報；伺服器用 IP 探測同一套判讀推出
OS、設備類型與廠牌型號寫回 IP。OS 家族或設備類型從一個確定的值變成另一個時寫異動記錄 ——
異常偵測「類型或 OS 突變」的依據。舊代理只送 os_guess 一行，照舊處理。
"""
from __future__ import annotations

from app.models.ip_change_log import IPChangeLog
from app.services.device_identity import apply_summary, model_text
from sqlalchemy import select

from tests.test_agent_scan_split import _agent_ip


def _cam_nmap(product: str = "Hikvision IP camera rtspd") -> dict:
    return {"ports": [{"port": 554, "proto": "tcp", "state": "open", "service": "rtsp", "product": product,
                       "version": "", "extrainfo": "", "scripts": {}, "script_data": {}}],
            "os": [{"name": "Linux 3.2 - 4.14", "accuracy": 95, "type": "webcam", "vendor": "Linux"}],
            "closed": 10, "host_scripts": {}}


def _nas_nmap() -> dict:
    return {"ports": [{"port": 445, "proto": "tcp", "state": "open", "service": "microsoft-ds",
                       "product": "Samba smbd", "version": "4", "extrainfo": "", "scripts": {}, "script_data": {}},
                      {"port": 5000, "proto": "tcp", "state": "open", "service": "http",
                       "product": "Synology DiskStation", "version": "", "extrainfo": "",
                       "scripts": {}, "script_data": {}}],
            "os": [{"name": "Linux 4.15 - 5.8", "accuracy": 96, "type": "general purpose", "vendor": "Linux"}],
            "closed": 3, "host_scripts": {}}


async def _logs(db, ip_id):
    return (await db.execute(select(IPChangeLog).where(IPChangeLog.ip_id == ip_id)
                             .order_by(IPChangeLog.created_at))).scalars().all()


def test_model_text_does_not_repeat_the_vendor() -> None:
    assert model_text({"vendor": "Hikvision", "model": "DS-2CD2143G0-I"}) == "Hikvision DS-2CD2143G0-I"
    assert model_text({"vendor": "Synology", "model": "Synology DS920+"}) == "Synology DS920+"
    assert model_text({"vendor": None, "model": None}) is None


async def test_first_identification_is_not_a_change_but_a_flip_is(db_session) -> None:
    _raw, _agent, ip, _old = await _agent_ip(db_session)
    await apply_summary(db_session, ip, {"device_type": "printer", "os": "HP embedded",
                                         "vendor": "HP", "model": "LaserJet M404"})
    await db_session.commit()
    assert (ip.device_kind, ip.device_model) == ("printer", "HP LaserJet M404")
    assert ip.device_identified_at is not None
    assert await _logs(db_session, ip.id) == []          # 從「不知道」到知道不算變更

    await apply_summary(db_session, ip, {"device_type": "unknown", "os": None})
    await db_session.commit()
    assert ip.device_kind == "printer"                   # 判讀不出來時保留上一次的結果

    await apply_summary(db_session, ip, {"device_type": "server", "os": "Windows Server 2019"})
    await db_session.commit()
    logs = await _logs(db_session, ip.id)
    kinds = [(x.event_type, x.old_value, x.new_value) for x in logs]
    assert ("kind_changed", "printer", "server") in kinds
    assert any(e == "os_changed" and n == "windows" for e, _o, n in kinds)


async def test_report_with_nmap_detail_is_judged_like_the_ip_probe(client, db_session) -> None:
    raw, _agent, ip, _old = await _agent_ip(db_session)
    r = await client.post("/api/v1/scan-agents/report", headers={"X-Agent-Key": raw}, json={
        "results": [{"ip": "198.51.100.7", "alive": True, "liveness": False, "probes_run": ["os"],
                     "os_guess": "Linux 4.15 - 5.8", "nmap": _nas_nmap()}]})
    assert r.status_code == 200, r.text
    await db_session.refresh(ip)
    assert ip.device_kind == "storage"                   # 代理那行只說 Linux；判讀看得出是 NAS
    assert ip.os_family == "linux"


async def test_old_agents_without_nmap_detail_still_work(client, db_session) -> None:
    raw, _agent, ip, _old = await _agent_ip(db_session)
    r = await client.post("/api/v1/scan-agents/report", headers={"X-Agent-Key": raw}, json={
        "results": [{"ip": "198.51.100.7", "alive": True, "liveness": False, "probes_run": ["os"],
                     "os_guess": "Linux 5.x"}]})
    assert r.status_code == 200, r.text
    await db_session.refresh(ip)
    assert ip.os_guess == "Linux 5.x"
    assert ip.device_kind is None


async def test_oversized_detail_is_ignored_not_fatal(client, db_session) -> None:
    raw, _agent, ip, _old = await _agent_ip(db_session)
    huge = _cam_nmap()
    huge["ports"][0]["scripts"] = {"banner": "x" * 70_000}
    r = await client.post("/api/v1/scan-agents/report", headers={"X-Agent-Key": raw}, json={
        "results": [{"ip": "198.51.100.7", "alive": True, "liveness": False, "probes_run": ["os"],
                     "os_guess": "Linux 5.x", "nmap": huge}]})
    assert r.status_code == 200, r.text
    await db_session.refresh(ip)
    assert ip.os_guess == "Linux 5.x"                    # 退回代理那行
    assert ip.device_kind is None


async def test_a_camera_turning_into_a_windows_box_is_logged(client, db_session) -> None:
    raw, _agent, ip, _old = await _agent_ip(db_session)
    for nmap in (_cam_nmap(), {"ports": [{"port": 3389, "proto": "tcp", "state": "open",
                                          "service": "ms-wbt-server", "product": "Microsoft Terminal Services",
                                          "version": "", "extrainfo": "", "scripts": {}, "script_data": {}}],
                               "os": [{"name": "Microsoft Windows 10", "accuracy": 97,
                                       "type": "general purpose", "vendor": "Microsoft"}],
                               "closed": 5, "host_scripts": {}}):
        r = await client.post("/api/v1/scan-agents/report", headers={"X-Agent-Key": raw}, json={
            "results": [{"ip": "198.51.100.7", "alive": True, "liveness": False, "probes_run": ["os"],
                         "nmap": nmap}]})
        assert r.status_code == 200, r.text
    logs = [(x.event_type, x.old_value, x.new_value) for x in await _logs(db_session, ip.id)]
    assert ("kind_changed", "camera", "windows") in logs
    assert ("os_changed", "linux", "windows") in logs


# ─────────────────── 代理端（1.14.0）───────────────────

_XML = """<?xml version="1.0"?><nmaprun><host><status state="up"/>
<address addr="198.51.100.7" addrtype="ipv4"/><address addr="00:00:5E:00:53:07" addrtype="mac" vendor="Synology"/>
<ports><port protocol="tcp" portid="5000"><state state="open"/>
<service name="http" product="Synology DiskStation" extrainfo="x"/>
<script id="http-title" output="Synology DiskStation - nas-01"/>
<script id="banner" output="BANNER_LONG"/></port></ports>
<os><osmatch name="Linux 4.15 - 5.8" accuracy="96"><osclass type="general purpose" vendor="Linux" osfamily="Linux"/></osmatch></os>
</host></nmaprun>"""


def test_agent_periodic_os_probe_returns_nmap_detail(monkeypatch) -> None:
    import os
    from types import SimpleNamespace

    from tests.test_agent_scan_split import _agent_module
    mod = _agent_module()
    assert tuple(int(x) for x in mod.AGENT_VERSION.split(".")) >= (1, 14, 0)   # 這個功能從 1.14.0 起
    seen: dict = {}

    def fake_run(args, **kw):
        seen["args"] = args
        xml_path = args[args.index("-oX") + 1]
        seen["xml"] = xml_path
        with open(xml_path, "w", encoding="utf-8") as fh:
            fh.write(_XML.replace("BANNER_LONG", "B" * 2000))
        return SimpleNamespace(stdout="5000/tcp open  http  Synology DiskStation\nOS details: Linux 4.15 - 5.8\n",
                               stderr="", returncode=0)
    monkeypatch.setattr(mod.shutil, "which", lambda _n: "/usr/bin/nmap")
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    out = mod._nmap_os_ports("198.51.100.7", True, True)
    assert "http-title" in seen["args"][seen["args"].index("--script") + 1]
    assert not os.path.exists(seen["xml"])                       # 暫存的 XML 用完就刪
    port = out["nmap"]["ports"][0]
    assert port["product"] == "Synology DiskStation"
    assert port["scripts"]["http-title"].startswith("Synology DiskStation")
    assert len(port["scripts"]["banner"]) == mod._OS_MAX_TEXT    # 每段文字有上限
    assert out["nmap"]["os"][0]["name"] == "Linux 4.15 - 5.8"
    assert "os_guess" in out                                      # 舊伺服器只認這個，照樣附上


def test_agent_without_os_probe_sends_no_detail(monkeypatch) -> None:
    from types import SimpleNamespace

    from tests.test_agent_scan_split import _agent_module
    mod = _agent_module()
    monkeypatch.setattr(mod.shutil, "which", lambda _n: "/usr/bin/nmap")
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda args, **kw: SimpleNamespace(stdout="22/tcp open ssh\n", stderr="", returncode=0))
    out = mod._nmap_os_ports("198.51.100.7", False, True)
    assert out == {"open_ports": [22]}


async def test_topology_uses_the_primary_ip_kind_for_unknown_devices(db_session) -> None:
    """拓樸：裝置類型不明（other）時，參考主要 IP 判讀出的類型（Recog 的其他用途 ③）。"""
    from app.models.address import IPAddress
    from app.models.device import Device
    from app.models.section import Section
    from app.models.subnet import Subnet
    from app.services.topology import build_topology

    sec = Section(name="topo-kind")
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="203.0.113.0/24")
    db_session.add(sub)
    await db_session.flush()
    devs = {}
    for name, kind, typ in (("sw-unknown", "switch", "other"), ("cam-1", "camera", "other"),
                            ("fw-known", "storage", "firewall")):
        d = Device(name=name, type=typ)
        db_session.add(d)
        await db_session.flush()
        ip = IPAddress(subnet_id=sub.id, ip=f"203.0.113.{len(devs) + 10}", device_id=d.id, device_kind=kind)
        db_session.add(ip)
        await db_session.flush()
        d.primary_ip_id = ip.id
        devs[name] = d
    await db_session.commit()
    g = await build_topology(db_session, include_wireless=False, include_l3=False)
    types = {n["data"].get("label"): n["data"].get("type") for n in g["nodes"]}
    assert types["sw-unknown"] == "switch"       # other → 主要 IP 判讀出交換器
    assert types["cam-1"] == "other"             # 拓樸沒有攝影機這一類，維持 other
    assert types["fw-known"] == "firewall"       # 已知的類型不被覆蓋


def test_agent_dhcp_parser_survives_truncated_options() -> None:
    """截斷的 DHCP 選項不可以丟例外：以前會中止整個偵測視窗，網段上任何主機送一個壞封包就能藏住
    非法 DHCP 伺服器（CodeQL 判讀附帶發現，代理 1.14.1）。"""
    from tests.test_agent_scan_split import _agent_module
    mod = _agent_module()
    head = bytearray(240)
    head[0] = 2
    head[16:20] = bytes([192, 0, 2, 50])
    head[236:240] = mod.DHCP_MAGIC
    ok = bytes(head) + bytes([53, 1, 2, 54, 4, 192, 0, 2, 1, 255])
    assert mod._dhcp_parse(ok)["server_id"] == "192.0.2.1"
    for tail in (bytes([54, 4, 192, 0]), bytes([53, 1]), bytes([1, 4, 255])):
        out = mod._dhcp_parse(bytes(head) + tail)          # 不丟例外
        assert out is not None
        assert "server_id" not in out


async def test_a_container_is_not_judged_by_its_tcp_fingerprint_class(client, db_session) -> None:
    """PVE 回報這個位址是容器的網卡：nmap 指紋說 HP NAS 也不可以把它判成儲存設備（2026-10-02 使用者回報）。"""
    import uuid

    from app.models.virt import VirtCluster, VirtualMachine, VMInterface
    raw, _agent, ip, _old = await _agent_ip(db_session)
    cl = VirtCluster(name=f"pve-{uuid.uuid4().hex[:4]}")
    db_session.add(cl)
    await db_session.flush()
    ct = VirtualMachine(cluster_id=cl.id, name="ct-app-01", status="running", kind="ct")
    db_session.add(ct)
    await db_session.flush()
    db_session.add(VMInterface(vm_id=ct.id, name="eth0", primary_ip="198.51.100.7"))
    # 已經被舊邏輯判錯的樣子：下一次 OS 偵測要能改過來（「不明」不會覆寫，型號也不可沿用）
    ip.device_kind, ip.device_model = "storage", "HP"
    await db_session.commit()
    fake_nas = {"ports": [], "closed": 5, "host_scripts": {},
                "os": [{"name": "HP P2000 G3 NAS device", "accuracy": 93, "type": "storage-misc", "vendor": "HP"}]}
    r = await client.post("/api/v1/scan-agents/report", headers={"X-Agent-Key": raw}, json={
        "results": [{"ip": "198.51.100.7", "alive": True, "liveness": False, "probes_run": ["os"],
                     "nmap": fake_nas}]})
    assert r.status_code == 200, r.text
    await db_session.refresh(ip)
    assert ip.device_kind == "server"
    assert ip.device_model != "HP"


async def test_same_kind_without_model_keeps_the_model_but_a_new_kind_drops_it(db_session) -> None:
    """同一類型、這次沒帶型號（定期偵測常常如此）→ 保留原型號；類型換了 → 舊型號屬於上一次的判讀，不沿用。"""
    from app.services.device_identity import apply_summary
    _raw, _agent, ip, _old = await _agent_ip(db_session)
    ip.device_kind, ip.device_model = "storage", "Synology DS920+"
    await apply_summary(db_session, ip, {"device_type": "storage", "vendor": None, "model": None, "evidence": []})
    assert ip.device_model == "Synology DS920+"
    await apply_summary(db_session, ip, {"device_type": "server", "vendor": None, "model": None,
                                         "evidence": ["recog-os:Ubuntu 22.04"]})
    assert ip.device_kind == "server" and ip.device_model is None


def test_a_slow_service_does_not_throw_away_the_whole_os_probe(monkeypatch) -> None:
    """2026-10-04 正式環境：一台 PVE 的 8006 埠讓 nmap 的版本偵測（-sV，預設強度試幾十種探針、每種等好幾秒）
    跑超過 90 秒的單台時限，nmap 就把這台的結果**整筆丟掉**（OS 指紋單獨只要 12 秒）→ 那台永遠沒有設備類型。
    ① 版本偵測用輕量模式（--version-light）；② 還是逾時（XML 裡沒有這台的結果）→ 退回只做 OS 指紋再試一次。"""
    from types import SimpleNamespace

    from tests.test_agent_scan_split import _agent_module
    mod = _agent_module()
    calls: list[list[str]] = []

    def fake_run(args, **kw):
        calls.append(list(args))
        xml_path = args[args.index("-oX") + 1]
        with open(xml_path, "w", encoding="utf-8") as fh:
            if "-sV" in args:          # 第一次：單台逾時，nmap 不寫出這台的任何資料
                fh.write('<?xml version="1.0"?><nmaprun><runstats><hosts up="1" down="0"/></runstats></nmaprun>')
                return SimpleNamespace(stdout="Skipping host 198.51.100.7 due to host timeout\n", stderr="", returncode=0)
            fh.write(_XML.replace("BANNER_LONG", "x"))
            return SimpleNamespace(stdout="OS details: Linux 4.15 - 5.8\n", stderr="", returncode=0)
    monkeypatch.setattr(mod.shutil, "which", lambda _n: "/usr/bin/nmap")
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    out = mod._nmap_os_ports("198.51.100.7", True, False)
    assert "--version-light" in calls[0]
    assert len(calls) == 2, "逾時後要退回只做 OS 指紋再試一次"
    assert "-sV" not in calls[1] and "-O" in calls[1] and "--script" not in calls[1]
    assert out["nmap"]["os"][0]["name"] == "Linux 4.15 - 5.8"
    assert out["os_guess"] == "Linux 4.15 - 5.8"


async def test_periodic_probe_uses_our_oui_vendor_not_nmaps(client, db_session) -> None:
    """2026-10-04 正式環境：IP 記錄上的 MAC 是 SuperMicro，nmap 卻報 Hewlett Packard —— 不是 nmap 的資料庫錯（它的
    那個前綴也是 Super Micro），而是當下回應 ARP 的是這台的另一張 HP 擴充網卡（兩個網段在同一個廣播網域，Linux 預設
    任何一張網卡都會回答本機任何一個位址）。「探測」頁判讀時帶 jt-ipam 用 IP 記錄的 MAC 查到的廠商，定期 OS 偵測卻沒帶
    → 退回用 nmap 的 → 設備類型旁邊寫著 HP。定期偵測也要用 IP 記錄的 MAC 查 jt-ipam 的 OUI 表。"""
    from app.models.oui import OUIVendor
    raw, _agent, ip, _old = await _agent_ip(db_session)
    ip.mac = "00:00:5e:00:53:21"
    await db_session.merge(OUIVendor(prefix="00005E", short_name="IANA", name="ICANN, IANA Department", source="test"))
    await db_session.commit()
    # 有 OpenSSH 才算一般主機（只有 Linux 指紋是「不明」，2026-10-05 起）
    nmap = {"ports": [{"port": 22, "proto": "tcp", "state": "open", "service": "ssh", "product": "OpenSSH"}],
            "closed": 5, "host_scripts": {}, "mac_vendor": "Hewlett Packard",
            "os": [{"name": "Linux 5.3 - 5.4", "accuracy": 94, "type": "general purpose", "vendor": "Linux"}]}
    r = await client.post("/api/v1/scan-agents/report", headers={"X-Agent-Key": raw}, json={
        "results": [{"ip": "198.51.100.7", "alive": True, "liveness": False, "probes_run": ["os"], "nmap": nmap}]})
    assert r.status_code == 200, r.text
    await db_session.refresh(ip)
    assert ip.device_kind == "server"
    # 2026-10-05 起網卡廠牌（OUI）不再當成設備的廠牌型號寫進 device_model（Mac 接 CalDigit 擴充座曾顯示「CalDigit」）；
    # 網卡廠牌在 IP 頁的 MAC 旁邊。這裡要守的是：nmap 報的 HP 不可以跑進來
    assert ip.device_model is None
