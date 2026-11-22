"""自訂欄位的值：子網路、IP、裝置在新增與更新時都要能填（2026-11-21：定義得出來、任何表單都填不了）。

畫面接上之後，後端這條路要可靠：值會存、必填缺值回 400（不是 500）、未定義的鍵被拒、更新是整份取代。
"""
from __future__ import annotations

import uuid

import pytest

from app.models.custom_field import CustomFieldDefinition
from app.models.section import Section

pytestmark = pytest.mark.anyio


async def _defs(db, object_type: str) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    req, sel = f"asset_{uuid.uuid4().hex[:6]}", f"tier_{uuid.uuid4().hex[:6]}"
    db.add_all([
        CustomFieldDefinition(object_type=object_type, name=req, field_type="text", required=True),
        CustomFieldDefinition(object_type=object_type, name=sel, field_type="select",
                              options={"choices": ["gold", "silver"]}),
    ])
    await db.commit()
    return req, sel


async def _cleanup(db, object_type: str) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import delete
    await db.execute(delete(CustomFieldDefinition).where(CustomFieldDefinition.object_type == object_type))
    await db.commit()


async def test_subnet_custom_fields(client, auth_headers, db_session) -> None:
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.commit()
    req, sel = await _defs(db_session, "subnet")
    try:
        r = await client.post("/api/v1/subnets", headers=auth_headers, json={"section_id": str(sec.id), "cidr": "198.18.10.0/24"})
        assert r.status_code == 400, r.text           # 必填缺值：清楚的 400
        r = await client.post("/api/v1/subnets", headers=auth_headers, json={
            "section_id": str(sec.id), "cidr": "198.18.10.0/24", "custom_fields": {req: "A-1", sel: "gold"}})
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        assert r.json()["custom_fields"] == {req: "A-1", sel: "gold"}
        r = await client.patch(f"/api/v1/subnets/{sid}", headers=auth_headers, json={"custom_fields": {req: "A-2"}})
        assert r.status_code == 200, r.text
        assert r.json()["custom_fields"] == {req: "A-2"}, "整份取代：沒帶的選項清掉"
        r = await client.patch(f"/api/v1/subnets/{sid}", headers=auth_headers, json={"custom_fields": {req: "A", "nope": 1}})
        assert r.status_code == 400
    finally:
        await _cleanup(db_session, "subnet")


async def test_ip_and_device_custom_fields(client, auth_headers, db_session) -> None:
    from app.models.subnet import Subnet
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="198.18.11.0/24")
    db_session.add(sub)
    await db_session.commit()
    req, sel = await _defs(db_session, "ip")
    dreq, dsel = await _defs(db_session, "device")
    try:
        r = await client.post("/api/v1/addresses", headers=auth_headers, json={"subnet_id": str(sub.id), "ip": "198.18.11.5"})
        assert r.status_code == 400, r.text
        r = await client.post("/api/v1/addresses", headers=auth_headers, json={
            "subnet_id": str(sub.id), "ip": "198.18.11.5", "custom_fields": {req: "x", sel: "silver"}})
        assert r.status_code == 201, r.text
        assert r.json()["custom_fields"] == {req: "x", sel: "silver"}
        r = await client.post("/api/v1/devices", headers=auth_headers, json={"name": f"d-{uuid.uuid4().hex[:6]}", "type": "server"})
        assert r.status_code == 400, r.text
        r = await client.post("/api/v1/devices", headers=auth_headers, json={
            "name": f"d-{uuid.uuid4().hex[:6]}", "type": "server", "custom_fields": {dreq: "R-9", dsel: "gold"}})
        assert r.status_code == 201, r.text
        assert r.json()["custom_fields"] == {dreq: "R-9", dsel: "gold"}
        r = await client.post("/api/v1/devices", headers=auth_headers, json={
            "name": f"d-{uuid.uuid4().hex[:6]}", "type": "server", "custom_fields": {dreq: "R", dsel: "bronze"}})
        assert r.status_code == 400, "不在選項裡"
    finally:
        await _cleanup(db_session, "ip")
        await _cleanup(db_session, "device")
