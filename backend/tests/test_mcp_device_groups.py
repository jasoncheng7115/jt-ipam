"""AI 對話／MCP 查裝置群組（使用者 2026-11-07：「裝置群組，mcp、api、aichat 都有支援吧」）。

以前只有查單一裝置或 IP 時順帶回群組，問「有哪些 HA 群組」「web_ha 的成員」沒有工具可叫。
工具清單已經頂到上限（num_ctx 16384 的站台），所以不另開工具：list_devices 加 group 參數，
每列帶群組名稱、類型、角色。
"""
from __future__ import annotations

import uuid

import pytest

from app.mcp.tools import TOOLS, call_tool
from app.models.device import Device
from app.models.device_group import DeviceGroup

pytestmark = pytest.mark.anyio


async def _group(db, name: str, kind: str, members: list[tuple[str, str]]) -> DeviceGroup:
    g = DeviceGroup(name=name, kind=kind)
    db.add(g)
    await db.flush()
    for i, (dev, role) in enumerate(members, 1):
        db.add(Device(name=dev, type="server", group_id=g.id, group_role=role, group_order=i))
    await db.commit()
    return g


async def test_list_devices_filters_by_group_and_shows_it(db_session, admin_user) -> None:
    tag = uuid.uuid4().hex[:6]
    await _group(db_session, f"web_ha_{tag}", "ha_active_passive", [(f"lb-1-{tag}", "primary"), (f"lb-2-{tag}", "standby")])
    await _group(db_session, f"stack_{tag}", "stack", [(f"sw-a-{tag}", "member")])
    db_session.add(Device(name=f"lonely-{tag}", type="server"))
    await db_session.commit()

    r = await call_tool(db_session, admin_user, "list_devices", {"group": f"web_ha_{tag}"})
    assert sorted(d["name"] for d in r["devices"]) == [f"lb-1-{tag}", f"lb-2-{tag}"]
    first = next(d for d in r["devices"] if d["name"] == f"lb-1-{tag}")
    assert first["group"] == {"name": f"web_ha_{tag}", "kind": "ha_active_passive", "role": "primary", "order": 1}

    r = await call_tool(db_session, admin_user, "list_devices", {"group": "*", "name": tag})
    assert sorted(d["name"] for d in r["devices"]) == [f"lb-1-{tag}", f"lb-2-{tag}", f"sw-a-{tag}"]
    r = await call_tool(db_session, admin_user, "list_devices", {"name": f"lonely-{tag}"})
    assert r["devices"][0]["group"] is None


def test_tool_description_mentions_groups() -> None:
    meta = TOOLS["list_devices"]
    assert "group" in meta["parameters"]["properties"]
    assert "group" in meta["description"].lower()
