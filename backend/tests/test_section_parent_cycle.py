"""區段的上層不可以是自己或自己的子孫（2026-11-21 整理區段表單時發現後端沒擋）。

列表頁的上層選單只排除自己，詳細頁根本沒有這個欄位；PATCH 直接送也會成功，形成循環。
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.anyio


async def _mk(client, headers, parent=None):  # type: ignore[no-untyped-def]
    r = await client.post("/api/v1/sections", headers=headers,
                          json={"name": f"sec-{uuid.uuid4().hex[:6]}", "parent_id": parent})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_parent_cannot_be_self_or_descendant(client, auth_headers) -> None:
    top = await _mk(client, auth_headers)
    mid = await _mk(client, auth_headers, top)
    leaf = await _mk(client, auth_headers, mid)
    for bad in (top, leaf, mid):
        r = await client.patch(f"/api/v1/sections/{top}", headers=auth_headers, json={"parent_id": bad})
        assert r.status_code == 422, (bad, r.text)
        assert r.json()["detail"]["code"] == "section_parent_cycle"
    # 往旁邊或往上搬照樣可以
    other = await _mk(client, auth_headers)
    r = await client.patch(f"/api/v1/sections/{mid}", headers=auth_headers, json={"parent_id": other})
    assert r.status_code == 200, r.text
    r = await client.patch(f"/api/v1/sections/{leaf}", headers=auth_headers, json={"parent_id": None})
    assert r.status_code == 200, r.text
