"""裝置群組（堆疊／HA／叢集，客戶 2026-11-05：兩台 N2048P 堆疊共用一個管理 IP，拓樸圖隨意畫給其中一台）。

定案（方案 B）：共用 IP 屬於群組（`ip_addresses.device_group_id`），同時「設備」欄指向群組的**代表裝置**——
還沒改成認得群組的程式看到的跟以前一樣，不會把它當成「沒連結」而去自動對應、搶走。

守的是：
- 建立／修改／刪除群組要管理員，寫稽核；一台設備只能在一個群組；代表裝置一定是成員
- 共用 IP 的設備欄＝代表裝置；換代表裝置時共用 IP 跟著轉
- 共用 IP 不能被搶走：IP 編輯、設備的主要 IP 這些明確的寫入回 409；
  自動對應這類沒經過檢查的寫入，存檔時自動改回代表裝置（安全網）
- 成員把共用 IP 設成自己的主要 IP 可以，但 IP 仍屬群組
- 刪除群組：成員回到一般設備，IP 留在原本的代表裝置上
- 設備與 IP 的讀取帶出群組資訊
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.address import IPAddress
from app.models.audit import AuditLog
from app.models.device import Device
from app.models.section import Section
from app.models.subnet import Subnet

pytestmark = pytest.mark.anyio
API = "/api/v1/device-groups"


async def _setup(db):
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    db.add(sec)
    await db.flush()
    sn = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    db.add(sn)
    await db.flush()
    sw1 = Device(name=f"N2048P-1-{uuid.uuid4().hex[:4]}", type="switch")
    sw2 = Device(name=f"N2048P-2-{uuid.uuid4().hex[:4]}", type="switch")
    other = Device(name=f"other-{uuid.uuid4().hex[:4]}", type="server")
    db.add_all([sw1, sw2, other])
    await db.flush()
    mgmt = IPAddress(subnet_id=sn.id, ip="198.51.100.246", state="used")
    own = IPAddress(subnet_id=sn.id, ip="198.51.100.11", state="used", device_id=sw2.id)
    db.add_all([mgmt, own])
    await db.commit()
    return sw1, sw2, other, mgmt, own


async def _create(client, headers, sw1, sw2, mgmt, **kw):
    body = {"name": f"stack-{uuid.uuid4().hex[:4]}", "kind": "stack",
            "members": [{"device_id": str(sw1.id), "role": "master", "order": 1},
                        {"device_id": str(sw2.id), "role": "member", "order": 2}],
            "representative_id": str(sw1.id), "shared_ip_ids": [str(mgmt.id)], **kw}
    return await client.post(API, headers=headers, json=body)


async def test_create_links_the_shared_ip_to_the_group_and_its_representative(client, auth_headers, db_session) -> None:
    sw1, sw2, _other, mgmt, own = await _setup(db_session)
    r = await _create(client, auth_headers, sw1, sw2, mgmt)
    assert r.status_code == 201, r.text
    g = r.json()
    assert g["kind"] == "stack" and g["representative_id"] == str(sw1.id)
    assert [m["role"] for m in g["members"]] == ["master", "member"]
    assert [ip["ip"] for ip in g["shared_ips"]] == ["198.51.100.246"]

    await db_session.refresh(mgmt)
    assert str(mgmt.device_group_id) == g["id"] and mgmt.device_id == sw1.id, "設備欄＝代表裝置"
    await db_session.refresh(own)
    assert own.device_group_id is None and own.device_id == sw2.id, "成員自己的 IP 不受影響"

    d2 = (await client.get(f"/api/v1/devices/{sw2.id}", headers=auth_headers)).json()
    assert d2["group"]["name"] == g["name"] and d2["group"]["role"] == "member" and d2["group"]["member_count"] == 2
    ip = (await client.get(f"/api/v1/addresses/{mgmt.id}", headers=auth_headers)).json()
    assert ip["device_group_id"] == g["id"] and ip["device_group_name"] == g["name"]
    assert (await db_session.execute(select(AuditLog).where(
        AuditLog.object_type == "device_group", AuditLog.action == "create"))).first() is not None


async def test_rules_for_members_and_representative(client, auth_headers, db_session) -> None:
    sw1, sw2, other, mgmt, _own = await _setup(db_session)
    r = await _create(client, auth_headers, sw1, sw2, mgmt, representative_id=str(other.id))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "device_group_representative_not_member"
    assert (await _create(client, auth_headers, sw1, sw2, mgmt)).status_code == 201
    r = await client.post(API, headers=auth_headers, json={
        "name": "second", "kind": "ha_active_passive",
        "members": [{"device_id": str(sw2.id), "role": "primary"}, {"device_id": str(other.id), "role": "standby"}]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "device_group_member_taken"
    r = await client.post(API, headers=auth_headers, json={"name": "empty", "kind": "cluster", "members": []})
    assert r.status_code == 422


async def test_only_admins_write(client, db_session) -> None:
    from app.models.permission import Permission
    from app.models.user import User
    from app.services.auth import issue_access_token
    sw1, sw2, _other, mgmt, _own = await _setup(db_session)
    u = User(username=f"v-{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@example.invalid",
             password_hash="x", is_active=True, is_admin=False)
    db_session.add(u)
    await db_session.flush()
    db_session.add(Permission(object_type="device", object_id=None, principal_type="user", principal_id=u.id,
                              level="write"))
    await db_session.commit()
    r = await _create(client, {"Authorization": f"Bearer {issue_access_token(u)}"}, sw1, sw2, mgmt)
    assert r.status_code == 403


async def test_changing_the_representative_moves_the_shared_ips(client, auth_headers, db_session) -> None:
    sw1, sw2, _other, mgmt, _own = await _setup(db_session)
    g = (await _create(client, auth_headers, sw1, sw2, mgmt)).json()
    r = await client.patch(f"{API}/{g['id']}", headers=auth_headers, json={"representative_id": str(sw2.id)})
    assert r.status_code == 200, r.text
    await db_session.refresh(mgmt)
    assert mgmt.device_id == sw2.id, "HA 切換後改代表裝置，共用 IP 跟著轉"
    # 把代表裝置移出群組 → 代表換成剩下的第一台
    r = await client.patch(f"{API}/{g['id']}", headers=auth_headers, json={
        "members": [{"device_id": str(sw1.id), "role": "master"}]})
    assert r.status_code == 200 and r.json()["representative_id"] == str(sw1.id)
    await db_session.refresh(mgmt)
    assert mgmt.device_id == sw1.id
    await db_session.refresh(sw2)
    assert sw2.group_id is None


async def test_explicit_writes_cannot_take_a_shared_ip(client, auth_headers, db_session) -> None:
    sw1, sw2, other, mgmt, _own = await _setup(db_session)
    await _create(client, auth_headers, sw1, sw2, mgmt)
    r = await client.patch(f"/api/v1/addresses/{mgmt.id}", headers=auth_headers, json={"device_id": str(other.id)})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ip_owned_by_group", r.text
    r = await client.patch(f"/api/v1/devices/{other.id}", headers=auth_headers, json={"primary_ip_id": str(mgmt.id)})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ip_owned_by_group", r.text
    # 成員可以把共用 IP 當自己的主要 IP；IP 仍屬群組、設備欄仍是代表裝置
    r = await client.patch(f"/api/v1/devices/{sw2.id}", headers=auth_headers, json={"primary_ip_id": str(mgmt.id)})
    assert r.status_code == 200, r.text
    await db_session.refresh(mgmt)
    assert mgmt.device_id == sw1.id and mgmt.device_group_id is not None


async def test_unchecked_writes_are_put_back_on_save(client, auth_headers, db_session) -> None:
    """自動對應、匯入這類沒經過檢查的寫入：存檔時改回代表裝置（安全網）。"""
    sw1, sw2, other, mgmt, _own = await _setup(db_session)
    await _create(client, auth_headers, sw1, sw2, mgmt)
    ip = (await db_session.execute(select(IPAddress).where(IPAddress.id == mgmt.id))).scalar_one()
    await db_session.refresh(ip)
    ip.device_id = other.id
    await db_session.commit()
    await db_session.refresh(ip)
    assert ip.device_id == sw1.id


async def test_assign_and_release_a_group_from_the_ip_form(client, auth_headers, db_session) -> None:
    sw1, sw2, _other, mgmt, own = await _setup(db_session)
    g = (await _create(client, auth_headers, sw1, sw2, mgmt, shared_ip_ids=[])).json()
    r = await client.patch(f"/api/v1/addresses/{own.id}", headers=auth_headers, json={"device_group_id": g["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["device_group_id"] == g["id"] and r.json()["device_id"] == str(sw1.id)
    r = await client.patch(f"/api/v1/addresses/{own.id}", headers=auth_headers, json={"device_group_id": None})
    assert r.status_code == 200 and r.json()["device_group_id"] is None
    assert r.json()["device_id"] == str(sw1.id), "退出群組：留在原本的代表裝置上"
    r = await client.patch(f"/api/v1/addresses/{own.id}", headers=auth_headers,
                           json={"device_group_id": str(uuid.uuid4())})
    assert r.status_code == 404


async def test_deleting_a_group_keeps_the_ips_on_the_representative(client, auth_headers, db_session) -> None:
    sw1, sw2, _other, mgmt, _own = await _setup(db_session)
    g = (await _create(client, auth_headers, sw1, sw2, mgmt)).json()
    assert (await client.delete(f"{API}/{g['id']}", headers=auth_headers)).status_code == 204
    await db_session.refresh(mgmt)
    await db_session.refresh(sw2)
    assert mgmt.device_group_id is None and mgmt.device_id == sw1.id
    assert sw2.group_id is None and sw2.group_role is None
    lst = (await client.get(API, headers=auth_headers)).json()
    assert all(x["id"] != g["id"] for x in lst["items"])


# ── IP 變更評估：停一台成員不會把共用 IP 判成中斷，整組都停才算 ──────────────────

async def test_impact_counts_a_shared_ip_only_when_the_whole_group_goes_down(client, auth_headers, db_session) -> None:
    from types import SimpleNamespace

    from app.services.change_impact import adapters_m2
    from app.services.change_impact.scenario import device_addresses
    sw1, sw2, _other, mgmt, own = await _setup(db_session)
    await _create(client, auth_headers, sw1, sw2, mgmt)
    assert [str(t.aip) for t in await device_addresses(db_session, sw1.id)] == [], "代表裝置停機不帶走共用 IP"
    assert [str(t.aip) for t in await device_addresses(db_session, sw2.id)] == ["198.51.100.11"]

    ctx = SimpleNamespace(session=db_session, status={f"device:{sw1.id}": adapters_m2.UNAVAILABLE})
    await adapters_m2._device_ip_status(ctx)
    assert f"ip:{mgmt.id}" not in ctx.status, "只停一台：共用 IP 還在"
    ctx = SimpleNamespace(session=db_session, status={f"device:{sw1.id}": adapters_m2.UNAVAILABLE,
                                                      f"device:{sw2.id}": adapters_m2.UNAVAILABLE})
    await adapters_m2._device_ip_status(ctx)
    assert ctx.status.get(f"ip:{mgmt.id}") == adapters_m2.UNAVAILABLE, "整組都停：共用 IP 中斷"
    assert ctx.status.get(f"ip:{own.id}") == adapters_m2.UNAVAILABLE


# ── 異常偵測：共用 IP 在成員之間換 MAC（HA 切換、堆疊換主機）是正常的 ─────────────────

async def test_shared_ip_moving_between_member_nics_is_not_an_anomaly(client, auth_headers, db_session) -> None:
    from datetime import UTC, datetime, timedelta

    from app.models.physical import DevicePort
    from app.services import anomaly
    sw1, sw2, other, mgmt, _own = await _setup(db_session)
    await _create(client, auth_headers, sw1, sw2, mgmt)
    db_session.add_all([DevicePort(device_id=sw1.id, name="mgmt", mac_address="aa:bb:cc:00:00:01"),
                        DevicePort(device_id=sw2.id, name="mgmt", mac_address="aa:bb:cc:00:00:02"),
                        DevicePort(device_id=other.id, name="eth0", mac_address="aa:bb:cc:00:00:99")])
    await db_session.commit()
    linked = datetime.now(UTC) - timedelta(days=3)
    on_member = (mgmt.id, None, None, "aa:bb:cc:00:00:02", sw1.id, None, linked)
    on_stranger = (mgmt.id, None, None, "aa:bb:cc:00:00:99", sw1.id, None, linked)
    kept = await anomaly._drop_own_nic_flips(db_session, [on_member, on_stranger])
    assert on_member not in kept, "共用 IP 換到另一台成員的網卡：不是過期的關聯"
    assert on_stranger in kept, "換到群組以外的機器：照樣列出"
    await db_session.refresh(mgmt)
    macs = await anomaly._group_member_macs(db_session, {mgmt.device_group_id})
    assert macs[mgmt.device_group_id] >= {"aa:bb:cc:00:00:01", "aa:bb:cc:00:00:02"}


# ── 系統匯出／匯入：群組與設備互相指（代表裝置 ↔ 所屬群組），兩個模式都不可以掉 ──────────────

@pytest.mark.parametrize("mode", ["replace", "merge"])
async def test_system_transfer_keeps_groups_members_and_shared_ips(client, auth_headers, db_session, mode) -> None:
    from app.models.device_group import DeviceGroup
    from app.services.system_transfer import exporter, importer
    sw1, sw2, _other, mgmt, _own = await _setup(db_session)
    g = (await _create(client, auth_headers, sw1, sw2, mgmt)).json()
    sw1_id, sw2_id, mgmt_id = sw1.id, sw2.id, mgmt.id
    dump = await exporter.build_export(db_session, ["core"])
    report = await importer.apply_import(db_session, dump, mode=mode, dry_run=False)
    for name in ("device_groups", "devices", "ip_addresses"):
        assert report["tables"][name]["errored"] == 0, f"{name}：{report['tables'][name].get('errors')}"
    db_session.expire_all()
    grp = (await db_session.execute(select(DeviceGroup).where(DeviceGroup.id == uuid.UUID(g["id"])))).scalar_one()
    assert grp.representative_id == sw1_id, "代表裝置掉了"
    m2 = (await db_session.execute(select(Device).where(Device.id == sw2_id))).scalar_one()
    assert (m2.group_id, m2.group_role, m2.group_order) == (grp.id, "member", 2)
    ip = (await db_session.execute(select(IPAddress).where(IPAddress.id == mgmt_id))).scalar_one()
    assert ip.device_group_id == grp.id and ip.device_id == sw1_id


# ── 拓樸：節點帶群組資訊；依子網路篩選時同組成員一起帶進來（堆疊才畫在一起） ─────────────

async def test_topology_nodes_carry_the_group_and_bring_the_whole_group(client, auth_headers, db_session) -> None:
    from app.services.topology import build_topology
    sw1, sw2, _other, mgmt, own = await _setup(db_session)
    own.device_id = None                      # 第二台在這個子網路裡沒有自己的 IP
    await db_session.commit()
    g = (await _create(client, auth_headers, sw1, sw2, mgmt)).json()
    out = await build_topology(db_session, subnet_ids=[mgmt.subnet_id])
    nodes = {n["data"]["id"]: n["data"] for n in out["nodes"]}
    assert str(sw2.id) in nodes, "共用 IP 只連到代表裝置，另一台成員也要一起畫"
    for did, role in ((sw1.id, "master"), (sw2.id, "member")):
        d = nodes[str(did)]
        assert (d["group_id"], d["group_name"], d["group_kind"], d["group_role"]) == (g["id"], g["name"], "stack", role)


# ── 機櫃圖：每台成員帶群組資訊，另外列出不在這個機櫃的成員在哪 ────────────────────

async def test_rack_diagram_marks_group_members_and_where_the_rest_are(client, auth_headers, db_session) -> None:
    from app.models.location import Rack
    sw1, sw2, other, mgmt, _own = await _setup(db_session)
    ra, rb = Rack(name=f"rack-A-{uuid.uuid4().hex[:4]}", u_height=42), Rack(name=f"rack-B-{uuid.uuid4().hex[:4]}", u_height=42)
    db_session.add_all([ra, rb])
    await db_session.flush()
    sw1.rack_id, sw1.u_position, sw1.u_size = ra.id, 10, 1
    sw2.rack_id, sw2.u_position, sw2.u_size = rb.id, 10, 1
    await db_session.commit()
    g = (await _create(client, auth_headers, sw1, sw2, mgmt)).json()
    d = (await client.get(f"/api/v1/racks/{ra.id}/diagram", headers=auth_headers)).json()
    slot = next(x for x in d["devices"] if x["device_id"] == str(sw1.id))
    grp = slot["group"]
    assert (grp["id"], grp["name"], grp["kind"], grp["order"], grp["total"]) == (g["id"], g["name"], "stack", 1, 2)
    assert grp["elsewhere"] == [{"rack_id": str(rb.id), "rack_name": rb.name, "count": 1}]


# ── AI 工具：裝置與 IP 的詳細資料要帶群組（少一個欄位，模型就會說系統沒有這個資料）────────

async def test_ai_tools_report_the_group(client, auth_headers, admin_user, db_session) -> None:
    from app.mcp import tools as T
    sw1, sw2, _other, mgmt, _own = await _setup(db_session)
    g = (await _create(client, auth_headers, sw1, sw2, mgmt)).json()
    n1, n2, id2 = sw1.name, sw2.name, str(sw2.id)
    db_session.expire_all()                       # 群組是 API 那邊建的：這邊的物件還是舊的
    await db_session.refresh(admin_user)
    d = await T.get_device(db_session, user=admin_user, device_id=id2)
    grp = d["group"]
    assert (grp["name"], grp["kind"], grp["role"], grp["order"]) == (g["name"], "stack", "member", 2)
    assert grp["representative"] == n1
    assert {m["name"] for m in grp["members"]} == {n1, n2}
    assert grp["shared_ips"] == ["198.51.100.246"]
    ipd = await T.get_ip_detail(db_session, user=admin_user, ip="198.51.100.246")
    assert ipd["device_group"] == g["name"] and ipd["device"] == n1
    assert (await T.get_ip_detail(db_session, user=admin_user, ip="198.51.100.11"))["device_group"] is None


# ── 裝置搜尋要找得到 IP（使用者 11-06：成員選單用 IP 找不到裝置）────────────────────

async def test_device_search_matches_ip_and_fqdn(client, auth_headers, db_session) -> None:
    sw1, sw2, _other, mgmt, own = await _setup(db_session)
    sw1.fqdn = f"core-{uuid.uuid4().hex[:4]}.corp.example"
    fq = sw1.fqdn
    n2 = sw2.name
    await db_session.commit()
    r = (await client.get("/api/v1/devices", headers=auth_headers, params={"q": "198.51.100.11"})).json()
    assert [d["name"] for d in r["items"]] == [n2]          # own 掛在 sw2
    r = (await client.get("/api/v1/devices", headers=auth_headers, params={"q": "198.51.100.1"})).json()
    assert n2 in [d["name"] for d in r["items"]]            # 部分輸入也找得到
    r = (await client.get("/api/v1/devices", headers=auth_headers, params={"q": fq})).json()
    assert r["total"] == 1
