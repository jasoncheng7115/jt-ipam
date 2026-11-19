"""編輯 NAT 規則按儲存一律 422（GitHub issue #59）。

前端新增與編輯共用同一份資料、裡面有 type（編輯時類型下拉也可以改），後端的 NATUpdate 卻沒有 type，
又是 StrictModel 不收多餘欄位 —— 不管有沒有改類型，PATCH 都被拒。改成：更新也可以改類型（同一套驗證）。
另外 name / type / protocol 是不可為空的欄位：PATCH 明確送 null 以前會寫進資料庫變成 500，改回 422。
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.anyio


def _form(name: str, type_: str = "one_to_one") -> dict:
    """前端 NAT.vue submit() 送的完整欄位（新增與編輯共用）。"""
    return {"name": name, "type": type_, "protocol": "any", "src_ip_id": None, "dst_ip_id": None,
            "device_id": None, "src_interface": None, "src_port": None, "dst_port": None, "description": None,
            "disabled": False, "no_rdr": False, "ip_version": "inet", "src_not": False, "dst_not": False,
            "src_port_to": None, "dst_port_to": None, "log": False, "category": None, "nat_reflection": None,
            "pool_options": None, "filter_rule": None}


async def test_saving_the_edit_form_unchanged_works(client, auth_headers) -> None:
    name = f"nat-{uuid.uuid4().hex[:6]}"
    r = await client.post("/api/v1/nat", headers=auth_headers, json=_form(name))
    assert r.status_code == 201, r.text
    nid = r.json()["id"]
    r = await client.patch(f"/api/v1/nat/{nid}", headers=auth_headers, json=_form(name))
    assert r.status_code == 200, r.text
    assert r.json()["type"] == "one_to_one"


async def test_type_can_be_changed_and_is_validated(client, auth_headers) -> None:
    name = f"nat-{uuid.uuid4().hex[:6]}"
    nid = (await client.post("/api/v1/nat", headers=auth_headers, json=_form(name))).json()["id"]
    r = await client.patch(f"/api/v1/nat/{nid}", headers=auth_headers, json=_form(name, "port_forward"))
    assert r.status_code == 200, r.text
    assert r.json()["type"] == "port_forward"
    r = await client.patch(f"/api/v1/nat/{nid}", headers=auth_headers, json={"type": "hairpin"})
    assert r.status_code == 422
    assert (await client.get(f"/api/v1/nat/{nid}", headers=auth_headers)).json()["type"] == "port_forward"


@pytest.mark.parametrize("field", ["name", "type", "protocol"])
async def test_required_fields_cannot_be_nulled(client, auth_headers, field) -> None:
    nid = (await client.post("/api/v1/nat", headers=auth_headers,
                             json=_form(f"nat-{uuid.uuid4().hex[:6]}"))).json()["id"]
    r = await client.patch(f"/api/v1/nat/{nid}", headers=auth_headers, json={field: None})
    assert r.status_code == 422, r.text
