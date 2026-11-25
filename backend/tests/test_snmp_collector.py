"""SNMP 收集（0217）：憑證、收集目標、收集者判定、代理協定、伺服器端寫入。

起因（客戶 2026-10-15）：掃描代理隔著路由器掃另一個網段，ping 得到卻沒有 MAC。路由器的 ARP 表、
交換器的 MAC 表、LLDP／CDP、介面都在設備上 —— 由負責那個網段的掃描代理用 SNMP 唯讀讀回來。

這裡守的是伺服器端：
- 憑證只寫不讀（has_*）、留空＝不變、控制字元拒收、版本規則；稽核不含機密
- 收集者：auto＝管理位址所在子網路的負責代理；代理要有 SNMP 能力才會被指派；判定不出來給錯誤代碼
- 輪詢只把解密後的憑證交給被指派的代理；測試／立即收集的工作記錄裡沒有明文，領取時才展開
- 回報：ARP 補 MAC（唯一才算）、reachable 才算上線證據、FDB／鄰居／介面完整讀到才清舊資料
"""
from __future__ import annotations

import uuid
from typing import Any

from app.api.v1.endpoints.scan_agents import _key_hash
from app.models.address import IPAddress
from app.models.agent_probe_job import AgentProbeJob
from app.models.audit import AuditLog
from app.models.device import Device
from app.models.librenms import FDBEntry
from app.models.physical import DevicePort
from app.models.scan_agent import ScanAgent
from app.models.section import Section
from app.models.snmp import SnmpCredential, SnmpNeighbor, SnmpRun, SnmpTarget
from app.models.subnet import Subnet
from app.services import snmp as svc
from sqlalchemy import select

KEY = "snmp-agent-key-" + "x" * 30
KEY2 = "snmp-agent-key2-" + "y" * 30
TOOLS = [{"name": "snmpbulkwalk", "installed": True, "version": "5.9.4"}]


async def _site(db, *, agent_version: str = "1.19.0", tools: list | None = TOOLS) -> dict[str, Any]:
    """代理 A 負責 192.0.2.0/24；核心交換器 core-sw（主要 IP 192.0.2.1）；另一台代理 B 沒有被指派。"""
    sec = Section(name=f"snmp-{uuid.uuid4().hex[:6]}")
    a = ScanAgent(name=f"agent-a-{uuid.uuid4().hex[:4]}", enroll_key_hash=_key_hash(KEY), enabled=True,
                  agent_version=agent_version, tools=tools)
    b = ScanAgent(name=f"agent-b-{uuid.uuid4().hex[:4]}", enroll_key_hash=_key_hash(KEY2), enabled=True,
                  agent_version="1.19.0", tools=TOOLS)
    db.add_all([sec, a, b])
    await db.flush()
    sub = Subnet(cidr="192.0.2.0/24", section_id=sec.id, scan_agent_id=a.id)
    db.add(sub)
    await db.flush()
    dev = Device(name="core-sw", type="switch")
    db.add(dev)
    await db.flush()
    ip = IPAddress(subnet_id=sub.id, ip="192.0.2.1", state="used", device_id=dev.id)
    db.add(ip)
    await db.flush()
    dev.primary_ip_id = ip.id
    await db.commit()
    return {"agent": a, "agent_b": b, "subnet": sub, "device": dev, "ip": ip, "section": sec}


async def _cred(client, headers, **kw) -> dict:
    body = {"name": f"c-{uuid.uuid4().hex[:6]}", "version": "v2c", "community": "s3cret-Comm", **kw}
    r = await client.post("/api/v1/snmp/credentials", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _target(client, headers, device_id, **kw) -> dict:
    r = await client.post("/api/v1/snmp/targets", json={"device_id": str(device_id), **kw}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _agent_headers(key: str = KEY, version: str = "1.19.0") -> dict:
    return {"X-Agent-Key": key, "X-Agent-Version": version,
            "X-Agent-Tools": "python3|1|3.12;snmpbulkwalk|1|5.9.4"}


# ─────────────────── 憑證 ───────────────────
async def test_credential_secrets_are_write_only_and_kept_when_left_empty(client, auth_headers, db_session) -> None:
    c = await _cred(client, auth_headers, is_default=True)
    assert c["has_community"] is True and "community" not in c
    assert c["is_default"] is True and c["target_count"] == 0
    row = await db_session.get(SnmpCredential, uuid.UUID(c["id"]))
    assert b"s3cret" not in bytes(row.community_enc)
    assert svc.get_secret(row, "community") == "s3cret-Comm"

    r = await client.patch(f"/api/v1/snmp/credentials/{c['id']}", json={"description": "x", "community": ""},
                           headers=auth_headers)
    assert r.status_code == 200 and r.json()["has_community"] is True
    db_session.expire_all()
    assert svc.get_secret(await db_session.get(SnmpCredential, uuid.UUID(c["id"])), "community") == "s3cret-Comm"

    r = await client.patch(f"/api/v1/snmp/credentials/{c['id']}", json={"version": "v3"}, headers=auth_headers)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "snmp_cred_username_required"
    v3 = {"version": "v3", "username": "monitor", "security_level": "authPriv", "auth_protocol": "SHA-256",
          "auth_passphrase": "short", "priv_protocol": "AES-128", "priv_passphrase": "priv-Pass-456"}
    r = await client.patch(f"/api/v1/snmp/credentials/{c['id']}", json=v3, headers=auth_headers)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "snmp_cred_passphrase_short"
    r = await client.patch(f"/api/v1/snmp/credentials/{c['id']}", json={**v3, "auth_passphrase": "auth-Pass-123"},
                           headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # 換成 v3：社群字串清掉、v3 的兩個密碼都在
    assert (body["has_community"], body["has_auth_passphrase"], body["has_priv_passphrase"]) == (False, True, True)
    assert body["auth_protocol"] == "SHA-256" and body["priv_protocol"] == "AES-128"

    audits = (await db_session.execute(select(AuditLog).where(AuditLog.object_type == "snmp_credential"))).scalars().all()
    assert {a.action for a in audits} >= {"create", "update"}
    for a in audits:
        txt = str(a.diff)
        assert "s3cret" not in txt and "auth-Pass" not in txt and "priv-Pass" not in txt


async def test_credential_rules(client, auth_headers, db_session) -> None:
    r = await client.post("/api/v1/snmp/credentials", json={"name": "bad", "version": "v2c", "community": "a\nb"},
                          headers=auth_headers)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "snmp_cred_bad_chars"
    r = await client.post("/api/v1/snmp/credentials", json={"name": "empty", "version": "v2c"}, headers=auth_headers)
    assert r.json()["detail"]["code"] == "snmp_cred_community_required"
    r = await client.post("/api/v1/snmp/credentials", json={
        "name": "nopriv", "version": "v3", "username": "u", "security_level": "authPriv", "auth_protocol": "SHA",
        "auth_passphrase": "12345678"}, headers=auth_headers)
    assert r.json()["detail"]["code"] == "snmp_cred_priv_required"
    r = await client.post("/api/v1/snmp/credentials", json={
        "name": "noauthnopriv", "version": "v3", "username": "u", "security_level": "noAuthNoPriv"},
        headers=auth_headers)
    assert r.status_code == 422                     # 不開放 noAuthNoPriv
    a = await _cred(client, auth_headers, is_default=True)
    b = await _cred(client, auth_headers, is_default=True)        # 新的預設取代舊的
    rows = {str(c.id): c.is_default for c in (await db_session.execute(select(SnmpCredential))).scalars().all()}
    assert rows == {a["id"]: False, b["id"]: True}
    r = await client.post("/api/v1/snmp/credentials", json={"name": b["name"], "version": "v2c", "community": "x"},
                          headers=auth_headers)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "snmp_credential_name_taken"


async def test_non_admin_cannot_touch_snmp(client, db_session) -> None:
    from app.core.security import hash_password
    from app.models.user import User
    from app.services.auth import issue_access_token
    u = User(username=f"v-{uuid.uuid4().hex[:6]}", email=f"v-{uuid.uuid4().hex[:6]}@t.local", display_name="v",
             password_hash=hash_password("TestPassword2026!"), auth_provider="local", is_active=True, is_admin=False)
    db_session.add(u)
    await db_session.commit()
    h = {"Authorization": f"Bearer {issue_access_token(u)}"}
    assert (await client.get("/api/v1/snmp/credentials", headers=h)).status_code == 403
    assert (await client.get("/api/v1/snmp/targets", headers=h)).status_code == 403


# ─────────────────── 收集目標與收集者 ───────────────────
async def test_target_uses_default_credential_and_resolves_the_subnet_agent(client, auth_headers, db_session) -> None:
    s = await _site(db_session)
    r = await client.post("/api/v1/snmp/targets", json={"device_id": str(s["device"].id)}, headers=auth_headers)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "snmp_no_default_credential"
    cred = await _cred(client, auth_headers, is_default=True)
    t = await _target(client, auth_headers, s["device"].id)
    assert t["credential_id"] == cred["id"] and t["mgmt_ip"] == "192.0.2.1"
    assert t["effective_agent_id"] == str(s["agent"].id) and t["collector_error"] is None
    assert t["collector_subnet_cidr"] == "192.0.2.0/24"
    assert t["effective_intervals"] == {"arp": 300, "fdb": 900, "neighbors": 3600, "interfaces": 3600}
    r = await client.post("/api/v1/snmp/targets", json={"device_id": str(s["device"].id)}, headers=auth_headers)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "snmp_target_exists"

    # 管理位址覆寫到沒有子網路的位址 → 判定不出來，給代碼
    r = await client.patch(f"/api/v1/snmp/targets/{t['id']}", json={"ip_override": "203.0.113.9"},
                           headers=auth_headers)
    assert r.json()["collector_error"]["code"] == "snmp_no_subnet"
    # 指定代理 B
    r = await client.patch(f"/api/v1/snmp/targets/{t['id']}", json={"collector": "agent", "agent_id": str(s["agent_b"].id)},
                           headers=auth_headers)
    assert r.json()["effective_agent_id"] == str(s["agent_b"].id) and r.json()["collector_error"] is None
    r = await client.patch(f"/api/v1/snmp/targets/{t['id']}", json={"collector": "agent", "agent_id": None},
                           headers=auth_headers)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "snmp_agent_required"
    # 收集間隔覆寫低於下限會拉回下限
    r = await client.patch(f"/api/v1/snmp/targets/{t['id']}", json={"intervals": {"arp": 60, "fdb": 60}},
                           headers=auth_headers)
    assert r.json()["effective_intervals"]["arp"] == 60 and r.json()["effective_intervals"]["fdb"] == 300

    # 刪憑證：還在用 → 409 並列出裝置
    r = await client.delete(f"/api/v1/snmp/credentials/{cred['id']}", headers=auth_headers)
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "snmp_credential_in_use" and d["params"]["count"] == 1
    assert d["params"]["targets"][0]["device_name"] == "core-sw"


async def test_target_from_a_gateway_ip(client, auth_headers, db_session) -> None:
    """子網路頁「對閘道啟用 SNMP 收集」：給 IP 就好；沒連結裝置的 IP 以它建一台裝置。"""
    s = await _site(db_session)
    await _cred(client, auth_headers, is_default=True)
    gw = IPAddress(subnet_id=s["subnet"].id, ip="192.0.2.254", state="used", hostname="gw-1")
    second = IPAddress(subnet_id=s["subnet"].id, ip="192.0.2.2", state="used", device_id=s["device"].id)
    db_session.add_all([gw, second])
    await db_session.commit()
    gw_id, second_id = gw.id, second.id
    r = await client.post("/api/v1/snmp/targets", json={"ip_address_id": str(gw_id)}, headers=auth_headers)
    assert r.status_code == 201, r.text
    t = r.json()
    assert (t["device_name"], t["mgmt_ip"], t["ip_override"]) == ("gw-1", "192.0.2.254", None)
    assert t["effective_agent_id"] == str(s["agent"].id)
    db_session.expire_all()
    dev = await db_session.get(Device, uuid.UUID(t["device_id"]))
    assert dev.type == "router" and dev.primary_ip_id == gw_id
    # 已連結裝置、但不是它的主要 IP → 用那台裝置，管理位址指定成這筆
    r = await client.post("/api/v1/snmp/targets", json={"ip_address_id": str(second_id)}, headers=auth_headers)
    assert r.status_code == 201, r.text
    assert (r.json()["device_name"], r.json()["mgmt_ip"], r.json()["ip_override"]) == \
        ("core-sw", "192.0.2.2", "192.0.2.2")
    r = await client.post("/api/v1/snmp/targets", json={}, headers=auth_headers)
    assert r.status_code == 422


async def test_agent_without_snmp_tools_is_not_a_collector(client, auth_headers, db_session) -> None:
    s = await _site(db_session, tools=[{"name": "nmap", "installed": True}])
    await _cred(client, auth_headers, is_default=True)
    t = await _target(client, auth_headers, s["device"].id)
    assert t["collector_error"]["code"] == "snmp_agent_no_snmp"
    assert t["collector_error"]["params"]["agent"] == s["agent"].name
    r = await client.get("/api/v1/snmp/collectors", params={"ip": "198.51.100.5"}, headers=auth_headers)
    assert r.json()["error"]["code"] == "snmp_no_subnet"
    r = await client.get("/api/v1/snmp/collectors", params={"device_id": str(s["device"].id)}, headers=auth_headers)
    assert r.json()["agent_id"] == str(s["agent"].id) and r.json()["capable"] is False
    agents = (await client.get("/api/v1/snmp/agents", headers=auth_headers)).json()["items"]
    a = next(x for x in agents if x["agent_id"] == str(s["agent"].id))
    assert a["capable"] is False and a["tool_installed"] is False


async def test_credential_limited_to_a_unit(client, auth_headers, db_session) -> None:
    from app.models.customer import Customer
    s = await _site(db_session)
    cust = Customer(name=f"unit-{uuid.uuid4().hex[:4]}")
    db_session.add(cust)
    await db_session.commit()
    c = await _cred(client, auth_headers, customer_id=str(cust.id))
    r = await client.post("/api/v1/snmp/targets", json={"device_id": str(s["device"].id), "credential_id": c["id"]},
                          headers=auth_headers)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "snmp_credential_scope"
    r = await client.post("/api/v1/snmp/targets/bulk", json={"device_ids": [str(s["device"].id)],
                                                             "credential_id": c["id"]}, headers=auth_headers)
    assert r.json()["created"] == 0 and r.json()["skipped"][0]["code"] == "snmp_credential_scope"


async def test_bulk_assign_creates_and_updates(client, auth_headers, db_session) -> None:
    s = await _site(db_session)
    c1 = await _cred(client, auth_headers, is_default=True)
    c2 = await _cred(client, auth_headers)
    other = Device(name="edge-sw")
    db_session.add(other)
    await db_session.commit()
    await _target(client, auth_headers, s["device"].id)
    r = await client.post("/api/v1/snmp/targets/bulk", json={
        "device_ids": [str(s["device"].id), str(other.id), str(uuid.uuid4())], "credential_id": c2["id"],
        "collect_fdb": False}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert (r.json()["created"], r.json()["updated"]) == (1, 1)
    assert r.json()["skipped"][0]["code"] == "snmp_device_not_found"
    rows = (await db_session.execute(select(SnmpTarget))).scalars().all()
    assert {str(t.credential_id) for t in rows} == {c2["id"]} and all(t.collect_fdb is False for t in rows)
    assert c1["id"] != c2["id"]


async def test_bulk_new_targets_follow_the_device_type_when_items_are_not_given(client, auth_headers, db_session) -> None:
    """裝置清單批次指派沒指定收集項目時，新建的收集目標照單筆表單同一套規則：路由器 ARP＋介面、
    交換器 MAC 表＋鄰居＋介面、其他只讀介面（以前一律全開，路由器也去讀 MAC 表）。"""
    await _cred(client, auth_headers, is_default=True)
    devs = [Device(name=f"bk-{k}-{uuid.uuid4().hex[:4]}", type=k) for k in ("router", "switch", "server")]
    db_session.add_all(devs)
    await db_session.commit()
    r = await client.post("/api/v1/snmp/targets/bulk", json={"device_ids": [str(d.id) for d in devs]},
                          headers=auth_headers)
    assert r.status_code == 200 and r.json()["created"] == 3, r.text
    got = {t.device_id: (t.collect_arp, t.collect_fdb, t.collect_neighbors, t.collect_interfaces)
           for t in (await db_session.execute(select(SnmpTarget))).scalars().all()}
    assert got[devs[0].id] == (True, False, False, True)
    assert got[devs[1].id] == (False, True, True, True)
    assert got[devs[2].id] == (False, False, False, True)


# ─────────────────── 輪詢：憑證只給被指派、有能力的代理 ───────────────────
async def test_poll_hands_credentials_only_to_the_assigned_capable_agent(client, auth_headers, db_session) -> None:
    s = await _site(db_session)
    await _cred(client, auth_headers, is_default=True)
    t = await _target(client, auth_headers, s["device"].id)
    r = await client.get("/api/v1/scan-agents/poll", headers=_agent_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["snmp"]) == 1
    spec = body["snmp"][0]
    assert spec["target_id"] == t["id"] and spec["ip"] == "192.0.2.1" and spec["port"] == 161
    assert spec["credential"]["version"] == "v2c" and spec["credential"]["community"] == "s3cret-Comm"
    assert spec["sections"]["arp"] == 300 and body["snmp_budget"]["max_rows"] > 0
    # 沒被指派的代理拿不到
    r = await client.get("/api/v1/scan-agents/poll", headers=_agent_headers(KEY2))
    assert r.json()["snmp"] == []
    # 舊代理（不認得這個欄位、也不會做）拿不到
    r = await client.get("/api/v1/scan-agents/poll", headers={**_agent_headers(), "X-Agent-Version": "1.18.2"})
    assert r.json()["snmp"] == []
    # 停用的目標不收
    await client.patch(f"/api/v1/snmp/targets/{t['id']}", json={"enabled": False}, headers=auth_headers)
    r = await client.get("/api/v1/scan-agents/poll", headers=_agent_headers())
    assert r.json()["snmp"] == []


# ─────────────────── 回報 ───────────────────
async def _extra_ips(db, s) -> dict[str, IPAddress]:
    """192.0.2.50／.51 在 A 的子網路；198.51.100.60 在兩個重疊的子網路各一筆（不明確）；host-pc 有 MAC。"""
    sec2 = Section(name=f"ovl-{uuid.uuid4().hex[:6]}")
    db.add(sec2)
    await db.flush()
    o1 = Subnet(cidr="198.51.100.0/24", section_id=s["section"].id)
    o2 = Subnet(cidr="198.51.100.0/24", section_id=sec2.id)
    db.add_all([o1, o2])
    await db.flush()
    pc = Device(name="host-pc")
    db.add(pc)
    await db.flush()
    ips = {"192.0.2.50": IPAddress(subnet_id=s["subnet"].id, ip="192.0.2.50", state="used"),
           "192.0.2.51": IPAddress(subnet_id=s["subnet"].id, ip="192.0.2.51", state="used"),
           "192.0.2.77": IPAddress(subnet_id=s["subnet"].id, ip="192.0.2.77", state="used", device_id=pc.id,
                                   mac="00:00:5e:00:53:77"),
           "ovl1": IPAddress(subnet_id=o1.id, ip="198.51.100.60", state="used"),
           "ovl2": IPAddress(subnet_id=o2.id, ip="198.51.100.60", state="used")}
    db.add_all(ips.values())
    await db.commit()
    return ips


def _report(target_id: str, **sections) -> dict:
    return {"target_id": target_id, "elapsed_ms": 1234,
            "system": {"sys_name": "core-sw", "sys_descr": "Cisco IOS", "sys_object_id": "1.3.6.1.4.1.9.1.1208",
                       "sys_uptime": 1000},
            "sections": sections}


async def test_report_writes_arp_fdb_interfaces_and_neighbors(client, auth_headers, db_session) -> None:
    import app.services.snmp_collect as sc
    sc._DERIVE_STATE["at"] = 0.0
    s = await _site(db_session)
    dev_id = s["device"].id
    ips = await _extra_ips(db_session, s)
    await _cred(client, auth_headers, is_default=True)
    t = await _target(client, auth_headers, s["device"].id)
    await client.get("/api/v1/scan-agents/poll", headers=_agent_headers())     # 工具盤點送進來
    payload = _report(
        t["id"],
        arp={"status": "ok", "table": "physical", "rows": [
            ["192.0.2.50", "00:50:56:00:00:50", 5, 1, 3],        # reachable → arp:snmp_live（會過期）
            ["192.0.2.51", "00:50:56:00:00:51", 5, 2, 3],        # stale → arp:snmp（只補 MAC）
            ["198.51.100.60", "00:50:56:00:00:60", 6, 1, 3],     # 重疊網段兩筆 → 不寫
            ["192.0.2.99", "00:50:56:00:00:99", 5, 1, 3],        # IPAM 沒有、沒開自動建立 → 不建
            ["192.0.2.52", "", 5, 7, 3]]},                       # incomplete → 略過
        fdb={"status": "ok", "rows": [["00:00:5e:00:53:77", "Gi1/0/7", 10], ["00:50:56:00:00:50", "Gi1/0/8", 10],
                                      ["00:50:56:00:00:51", "Gi1/0/9", None]]},
        interfaces={"status": "ok", "rows": [
            {"if_index": 1, "name": "Gi1/0/7", "descr": "GigabitEthernet1/0/7", "alias": "to pc", "type": 6,
             "mac": "00:11:22:33:44:07", "admin": 1, "oper": 1},
            {"if_index": 100, "name": "Vl100", "descr": "Vlan100", "type": 53, "admin": 1, "oper": 1}],
            "addresses": [{"ip": "192.0.2.1", "if_index": 100}]},
        neighbors={"status": "ok", "rows": [
            {"protocol": "lldp", "local_port": "Gi1/0/1", "remote_sysname": "dist-sw", "remote_port": "Te1/1/1",
             "remote_chassis_id": "00:11:22:33:44:55", "remote_mac": "00:11:22:33:44:55",
             "remote_mgmt_address": "192.0.2.77", "remote_platform": "Cisco IOS XE"}]})
    r = await client.post("/api/v1/scan-agents/snmp-report", json=payload, headers=_agent_headers())
    assert r.status_code == 200, r.text
    assert r.json()["result"] == "success"
    db_session.expire_all()

    by_ip = {str(i.ip).split("/")[0]: i for i in
             (await db_session.execute(select(IPAddress))).scalars().all() if str(i.ip).startswith("192.0.2.")}
    ip50, ip51 = by_ip["192.0.2.50"], by_ip["192.0.2.51"]
    assert str(ip50.mac) == "00:50:56:00:00:50" and ip50.mac_source == "snmp"
    assert "arp:snmp_live" in (ip50.arp_seen or {}) and "arp:snmp" not in (ip50.arp_seen or {})
    assert str(ip51.mac) == "00:50:56:00:00:51" and set(ip51.arp_seen or {}) == {"arp:snmp"}
    assert "192.0.2.99" not in by_ip
    for o in (await db_session.execute(select(IPAddress).where(IPAddress.ip == "198.51.100.60"))).scalars().all():
        assert o.mac is None                         # 重疊網段：不明確就不寫
    assert "iface:snmp" in (by_ip["192.0.2.1"].arp_seen or {})

    fdb = (await db_session.execute(select(FDBEntry).where(FDBEntry.source == "snmp"))).scalars().all()
    assert len(fdb) == 3 and all(f.switch_device_id == dev_id for f in fdb)
    assert all(str(f.snmp_target_id) == t["id"] for f in fdb)
    # 交換器位置（與 LibreNMS／MikroTik 同一套推導）
    assert by_ip["192.0.2.77"].switch_port == "core-sw / Gi1/0/7"

    ports = {p.name: p for p in (await db_session.execute(
        select(DevicePort).where(DevicePort.device_id == dev_id))).scalars().all()}
    assert set(ports) == {"Gi1/0/7"}                 # VLAN 介面不是連接埠
    assert ports["Gi1/0/7"].description == "to pc" and ports["Gi1/0/7"].source_origin == f"snmp:{t['id']}"

    nb = (await db_session.execute(select(SnmpNeighbor))).scalars().one()
    assert (nb.local_port, nb.remote_sysname, nb.remote_port, nb.protocol) == ("Gi1/0/1", "dist-sw", "Te1/1/1", "lldp")
    target = await db_session.get(SnmpTarget, uuid.UUID(t["id"]))
    assert target.last_ok_at is not None and target.last_error is None
    assert target.last_summary["system"]["sys_name"] == "core-sw"
    assert target.last_summary["sections"]["arp"]["matched"] == 2
    run = (await db_session.execute(select(SnmpRun))).scalars().one()
    assert (run.kind, run.trigger, run.result) == ("collect", "scheduled", "success")

    # 鄰居 API：依宣告的管理位址對到 host-pc
    nbs = (await client.get("/api/v1/snmp/neighbors", params={"target_id": t["id"]}, headers=auth_headers)).json()
    assert nbs["items"][0]["remote_device_name"] == "host-pc"
    # 拓樸：鄰居與 FDB 都畫得出來
    from app.services.topology import build_topology
    g = await build_topology(db_session, include_wireless=False, include_l3=False)
    edges = [e["data"] for e in g["edges"]]
    core, pc = str(dev_id), str(by_ip["192.0.2.77"].device_id)
    assert any(e.get("via") == "neighbor" and {e["source"], e["target"]} == {core, pc} for e in edges), edges
    assert any(e["kind"] == "l2" and {e["source"], e["target"]} == {core, pc} for e in edges), edges

    # ── 部分失敗：舊資料保留；完整讀到：不再出現的清掉 ──
    partial = _report(t["id"], fdb={"status": "partial", "error": "timed out",
                                    "rows": [["00:00:5e:00:53:77", "Gi1/0/7", 10]]},
                      neighbors={"status": "error", "error": "timed out", "rows": []})
    r = await client.post("/api/v1/scan-agents/snmp-report", json=partial, headers=_agent_headers())
    assert r.json()["result"] == "failed" or r.json()["result"] == "partial"
    db_session.expire_all()
    assert len((await db_session.execute(select(FDBEntry).where(FDBEntry.source == "snmp"))).scalars().all()) == 3
    assert len((await db_session.execute(select(SnmpNeighbor))).scalars().all()) == 1
    target = await db_session.get(SnmpTarget, uuid.UUID(t["id"]))
    assert target.last_error_code == "snmp_sections_failed"
    full = _report(t["id"], fdb={"status": "ok", "rows": [["00:00:5e:00:53:77", "Gi1/0/7", 10]]},
                   neighbors={"status": "absent", "rows": []})
    await client.post("/api/v1/scan-agents/snmp-report", json=full, headers=_agent_headers())
    db_session.expire_all()
    assert len((await db_session.execute(select(FDBEntry).where(FDBEntry.source == "snmp"))).scalars().all()) == 1
    assert (await db_session.execute(select(SnmpNeighbor))).scalars().all() == []

    # 刪掉收集目標：FDB／鄰居／記錄跟著走，沒接線的埠收回
    r = await client.delete(f"/api/v1/snmp/targets/{t['id']}", headers=auth_headers)
    assert r.status_code == 204
    db_session.expire_all()
    assert (await db_session.execute(select(FDBEntry).where(FDBEntry.source == "snmp"))).scalars().all() == []
    assert (await db_session.execute(select(DevicePort).where(
        DevicePort.device_id == dev_id))).scalars().all() == []


async def test_report_for_a_target_this_agent_does_not_collect_is_404(client, auth_headers, db_session) -> None:
    s = await _site(db_session)
    await _cred(client, auth_headers, is_default=True)
    t = await _target(client, auth_headers, s["device"].id)
    r = await client.post("/api/v1/scan-agents/snmp-report", json=_report(t["id"]), headers=_agent_headers(KEY2))
    assert r.status_code == 404
    r = await client.post("/api/v1/scan-agents/snmp-report", json=_report(str(uuid.uuid4())), headers=_agent_headers())
    assert r.status_code == 404


async def test_auto_create_from_reachable_arp_only(client, auth_headers, db_session) -> None:
    s = await _site(db_session)
    await _cred(client, auth_headers, is_default=True)
    t = await _target(client, auth_headers, s["device"].id, auto_create_ips=True)
    payload = _report(t["id"], arp={"status": "ok", "rows": [
        ["192.0.2.80", "00:50:56:00:00:80", 5, 1, 3],     # reachable：設備自己證明最近看過 → 建
        ["192.0.2.81", "00:50:56:00:00:81", 5, None, 3],  # 舊表、沒有時間，又沒有 MAC 表佐證 → 不建
        ["192.0.2.82", "00:50:56:00:00:82", 5, 1, 4]]})   # 靜態項目 → 不建
    r = await client.post("/api/v1/scan-agents/snmp-report", json=payload, headers=_agent_headers())
    assert r.status_code == 200, r.text
    db_session.expire_all()
    got = {str(i.ip).split("/")[0]: i for i in (await db_session.execute(select(IPAddress))).scalars().all()}
    assert got["192.0.2.80"].discovery_source == "snmp_arp" and str(got["192.0.2.80"].mac) == "00:50:56:00:00:80"
    assert "192.0.2.81" not in got and "192.0.2.82" not in got
    target = await db_session.get(SnmpTarget, uuid.UUID(t["id"]))
    assert target.last_summary["sections"]["arp"]["created"] == 1


# ─────────────────── 測試與立即收集（代理工作通道）───────────────────
async def test_test_job_keeps_secrets_out_of_the_database(client, auth_headers, db_session) -> None:
    s = await _site(db_session)
    cred = await _cred(client, auth_headers, is_default=True)
    other = await _cred(client, auth_headers, community="second-Comm")
    t = await _target(client, auth_headers, s["device"].id)
    await client.get("/api/v1/scan-agents/poll", headers=_agent_headers())
    r = await client.post(f"/api/v1/snmp/targets/{t['id']}/test",
                          json={"credential_ids": [cred["id"], other["id"]]}, headers=auth_headers)
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert r.json()["agent_id"] == str(s["agent"].id)
    job = await db_session.get(AgentProbeJob, uuid.UUID(job_id))
    assert job.kind == "snmp_test" and "Comm" not in str(job.params)

    got = (await client.get("/api/v1/scan-agents/jobs", headers=_agent_headers())).json()["jobs"]
    assert len(got) == 1 and got[0]["kind"] == "snmp_test"
    p = got[0]["params"]
    assert p["ip"] == "192.0.2.1" and [c["community"] for c in p["credentials"]] == ["s3cret-Comm", "second-Comm"]
    assert p["budget"]["max_rows"] > 0
    # 別的代理領不到
    assert (await client.get("/api/v1/scan-agents/jobs", headers=_agent_headers(KEY2))).json()["jobs"] == []

    result = {"items": [
        {"credential_id": cred["id"], "ok": False, "error": "Timeout: No Response", "sections": {}},
        {"credential_id": other["id"], "ok": True, "elapsed_ms": 900,
         "system": {"sys_name": "core-sw", "sys_descr": "Cisco IOS", "sys_object_id": "1.3.6.1.4.1.9.1.1",
                    "sys_uptime": 5},
         "sections": {"arp": {"status": "ok", "rows": 1200, "elapsed_ms": 400}}, "community": "leak?"}]}
    r = await client.post(f"/api/v1/scan-agents/jobs/{job_id}/result", json={"result": result},
                          headers=_agent_headers())
    assert r.status_code == 200
    st = (await client.get(f"/api/v1/snmp/jobs/{job_id}", headers=auth_headers)).json()
    assert st["status"] == "done"
    items = st["result"]["items"]
    assert [i["ok"] for i in items] == [False, True]
    assert items[1]["credential_name"] == other["name"] and items[1]["sections"]["arp"]["rows"] == 1200
    assert "leak" not in str(st)
    assert st["run"]["kind"] == "test" and st["run"]["result"] == "success"
    audits = (await db_session.execute(select(AuditLog).where(AuditLog.action == "test"))).scalars().all()
    assert len(audits) == 1 and "Comm" not in str(audits[0].diff)


async def test_collect_now_fills_the_manual_run(client, auth_headers, db_session) -> None:
    s = await _site(db_session)
    await _cred(client, auth_headers, is_default=True)
    t = await _target(client, auth_headers, s["device"].id)
    await client.get("/api/v1/scan-agents/poll", headers=_agent_headers())
    r = await client.post(f"/api/v1/snmp/targets/{t['id']}/collect", headers=auth_headers)
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    got = (await client.get("/api/v1/scan-agents/jobs", headers=_agent_headers())).json()["jobs"]
    assert got[0]["kind"] == "snmp_collect" and got[0]["params"]["credential"]["community"] == "s3cret-Comm"
    assert set(got[0]["params"]["sections"]) == {"arp", "fdb", "neighbors", "interfaces"}
    payload = {**_report(t["id"], arp={"status": "ok", "rows": []}), "job_id": job_id}
    r = await client.post("/api/v1/scan-agents/snmp-report", json=payload, headers=_agent_headers())
    assert r.status_code == 200, r.text
    await client.post(f"/api/v1/scan-agents/jobs/{job_id}/result", json={"result": {"result": "success"}},
                      headers=_agent_headers())
    runs = (await client.get(f"/api/v1/snmp/targets/{t['id']}/runs", headers=auth_headers)).json()["items"]
    assert len(runs) == 1 and (runs[0]["trigger"], runs[0]["result"]) == ("manual", "success")
    all_runs = (await client.get("/api/v1/snmp/runs", headers=auth_headers)).json()["items"]
    assert all_runs[0]["device_name"] == "core-sw"


async def test_test_refused_when_no_capable_collector(client, auth_headers, db_session) -> None:
    s = await _site(db_session, agent_version="1.18.2")
    await _cred(client, auth_headers, is_default=True)
    t = await _target(client, auth_headers, s["device"].id)
    r = await client.post(f"/api/v1/snmp/targets/{t['id']}/test", headers=auth_headers)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "snmp_agent_no_snmp"


# ─────────────────── 讀取端不分來源 ───────────────────
async def test_mac_drift_sees_snmp_fdb_rows(db_session) -> None:
    """MAC 漂移以前只看 LibreNMS 的 FDB：只有 SNMP 收集的站台永遠偵測不到換埠。"""
    from datetime import UTC, datetime, timedelta

    from app.services import anomaly
    s = await _site(db_session)
    sub = await db_session.get(Subnet, s["subnet"].id)
    sub.anomaly_enabled = True
    cred = SnmpCredential(id=uuid.uuid4(), name="c", version="v2c")
    svc.set_secret(cred, "community", "x")
    db_session.add(cred)
    await db_session.flush()
    t = SnmpTarget(device_id=s["device"].id, credential_id=cred.id)
    db_session.add(t)
    await db_session.flush()
    now = datetime.now(UTC)
    mac = "00:00:5e:00:53:20"
    db_session.add(IPAddress(subnet_id=sub.id, ip="192.0.2.20", mac=mac, hostname="printer-3f"))
    db_session.add_all([
        FDBEntry(mac=mac, port_name="Gi1/0/18", source="snmp", switch_device_id=s["device"].id, snmp_target_id=t.id,
                 first_seen_at=now - timedelta(days=60), last_seen_at=now - timedelta(days=2)),
        FDBEntry(mac=mac, port_name="Gi1/0/21", source="snmp", switch_device_id=s["device"].id, snmp_target_id=t.id,
                 first_seen_at=now - timedelta(hours=20), last_seen_at=now - timedelta(hours=2))])
    await db_session.commit()
    [item] = await anomaly.detect_mac_drifts(db_session)
    assert (item["category"], item["device_name"], item["from_port"], item["to_port"]) == \
        ("device_move", "core-sw", "Gi1/0/18", "Gi1/0/21")


# ─────────────────── 登記與翻譯 ───────────────────
def test_evidence_and_precedence_registration() -> None:
    from app.services.arp_precedence import ARP_SOURCES
    from app.services.evidence import LIVENESS_SOURCES, SOURCES_ADDED_LATER, is_aging
    assert not is_aging("arp:snmp") and is_aging("arp:snmp_live") and is_aging("iface:snmp")
    assert {"arp:snmp", "arp:snmp_live", "iface:snmp"} <= set(LIVENESS_SOURCES)
    assert {"arp:snmp_live", "iface:snmp"} <= set(SOURCES_ADDED_LATER)
    assert "snmp" in ARP_SOURCES


def test_every_snmp_error_code_is_translated() -> None:
    """服務層用位置參數帶出的代碼（SnmpConfigError、Resolution.fail、last_error_code）守門掃不到，這裡補。"""
    import json
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[2]
    src = "".join((root / "backend" / "app" / "services" / f).read_text() for f in ("snmp.py", "snmp_collect.py"))
    src += (root / "agent" / "jt_ipam_agent.py").read_text()
    codes = set(re.findall(r'"(snmp_(?:cred_[a-z_]+|no_[a-z_]+|agent_[a-z_]+|collector_[a-z_]+|unreachable|'
                           r'collect_failed|sections_failed|test_failed|job_expired))"', src))
    assert len(codes) > 15, codes
    for loc in ("zh-TW", "en-US", "ja-JP"):
        errors = json.loads((root / "frontend" / "src" / "i18n" / f"{loc}.json").read_text())["errors"]
        missing = sorted(c for c in codes if c not in errors)
        assert not missing, f"{loc}: {missing}"


async def test_match_neighbors_by_sysname(db_session) -> None:
    """管理位址與 MAC 都對不到時用系統名稱（去掉網域）比對裝置名稱。

    以前這條路徑引用了不存在的 Device.archived_at，一走到就 AttributeError —— 拓樸圖與鄰居清單跟著 500
    （11-25 寫佈線自動推斷時發現）。"""
    from types import SimpleNamespace

    from app.models.device import Device
    from app.services.snmp_collect import match_neighbors
    d = Device(name=f"edge-{uuid.uuid4().hex[:4]}", type="switch")
    db_session.add(d)
    await db_session.flush()
    row = SimpleNamespace(id=uuid.uuid4(), remote_mgmt_address=None, remote_mac=None,
                          remote_sysname=f"{d.name.upper()}.example.net")
    got = await match_neighbors(db_session, [row])
    assert got[row.id]["remote_device_id"] == str(d.id)
