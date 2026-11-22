"""租戶群組、聯絡人群組的上層不可以是自己或自己底下的群組（2026-11-21：表單補上「上層」欄位時一起擋）。"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("path", ["/api/v1/tenant-groups", "/api/v1/contact-groups"])
async def test_group_parent_cannot_loop(client, auth_headers, path) -> None:
    async def mk(parent=None):  # type: ignore[no-untyped-def]
        r = await client.post(path, headers=auth_headers, json={"name": f"g-{uuid.uuid4().hex[:6]}", "parent_id": parent})
        assert r.status_code == 201, r.text
        return r.json()["id"]
    top = await mk()
    child = await mk(top)
    for bad in (top, child):
        r = await client.patch(f"{path}/{top}", headers=auth_headers, json={"name": "x", "parent_id": bad})
        assert r.status_code == 422, (bad, r.text)
        assert r.json()["detail"]["code"] == "parent_cycle"
    other = await mk()
    r = await client.patch(f"{path}/{child}", headers=auth_headers, json={"name": "c2", "parent_id": other})
    assert r.status_code == 200, r.text
