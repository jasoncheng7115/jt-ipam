"""升級逐筆核對（交接包 physical-v1.2 UPGRADE.md 的驗收證據，2026-11-21）。

- 沒有變動 → 通過
- 負向：兩條線的對端互換（筆數不變）、未知瓦數被補成 0、刪掉一筆、權限列被改、平面圖檔被換 → 都要擋下
- 新增的列與新增的欄位不算失敗（另行驗證）；刪掉欄位算
- 原本就存在的資料問題不算升級造成的；升級後新出現的才算
- 清冊與報告存成 0600
"""
from __future__ import annotations

import copy
import json
import os
import stat

import pytest
from app.models.device import Device
from app.models.location import Location
from app.models.permission import Permission
from app.models.physical import Cable, CableTermination, DevicePort, DevicePowerPort
from app.services.upgrade_verify import compare, snapshot
from sqlalchemy import update


async def _data(db, tmp_path):  # type: ignore[no-untyped-def]
    loc = Location(name="東京機房", floor_plan_path="floorplans/plan.png")
    sw, srv = Device(name="sw-01"), Device(name="srv-01")
    db.add_all([loc, sw, srv])
    await db.flush()
    (tmp_path / "floorplans").mkdir()
    (tmp_path / "floorplans" / "plan.png").write_bytes(b"\x89PNG original")
    p = [DevicePort(device_id=sw.id, name=f"Gi{i}") for i in (1, 2)] + [DevicePort(device_id=srv.id, name=f"eth{i}") for i in (1, 2)]
    db.add_all(p)
    await db.flush()
    cables = []
    for i, (a, b) in enumerate(((p[0], p[2]), (p[1], p[3]))):
        c = Cable(label=f"C{i}", status="connected")
        db.add(c)
        await db.flush()
        db.add_all([CableTermination(cable_id=c.id, side="A", object_type="device_port", object_id=a.id),
                    CableTermination(cable_id=c.id, side="B", object_type="device_port", object_id=b.id)])
        cables.append(c)
    psu = DevicePowerPort(device_id=srv.id, name="PSU1", max_watts=None)
    db.add(psu)
    db.add(Permission(object_type="location", object_id=loc.id, principal_type="user", principal_id=srv.id, level="read"))
    await db.commit()
    return {"loc": loc, "ports": p, "cables": cables, "psu": psu}


@pytest.mark.anyio
async def test_unchanged_passes(db_session, tmp_path) -> None:
    await _data(db_session, tmp_path)
    before = await snapshot(db_session, upload_dir=str(tmp_path))
    after = await snapshot(db_session, upload_dir=str(tmp_path))
    r = compare(before, after)
    assert r["ok"], r
    assert r["tables"]["cables"]["matched"] == r["tables"]["cables"]["baseline"] >= 2
    assert r["files"]["floor_plans"]["baseline"] == 1


@pytest.mark.anyio
async def test_swapped_cable_ends_are_caught(db_session, tmp_path) -> None:
    d = await _data(db_session, tmp_path)
    before = await snapshot(db_session, upload_dir=str(tmp_path))
    p, (c0, c1) = d["ports"], d["cables"]
    # 兩條線的 B 端互換：筆數一樣
    await db_session.execute(update(CableTermination).where(CableTermination.cable_id == c0.id, CableTermination.side == "B")
                             .values(object_id=p[3].id))
    await db_session.execute(update(CableTermination).where(CableTermination.cable_id == c1.id, CableTermination.side == "B")
                             .values(object_id=p[2].id))
    await db_session.commit()
    r = compare(before, await snapshot(db_session, upload_dir=str(tmp_path)))
    assert not r["ok"]
    t = r["tables"]["cable_terminations"]
    assert t["missing"] == []
    assert len(t["changed"]) == 2
    assert {x["diffs"][0]["field"] for x in t["changed"]} == {"object_id"}


@pytest.mark.anyio
async def test_unknown_watts_turned_into_zero_is_caught(db_session, tmp_path) -> None:
    d = await _data(db_session, tmp_path)
    before = await snapshot(db_session, upload_dir=str(tmp_path))
    await db_session.execute(update(DevicePowerPort).where(DevicePowerPort.id == d["psu"].id).values(max_watts=0))
    await db_session.commit()
    r = compare(before, await snapshot(db_session, upload_dir=str(tmp_path)))
    assert not r["ok"]
    diff = r["tables"]["device_power_ports"]["changed"][0]["diffs"]
    # updated_at 也會跟著變（真的改到資料時本來就該列出來）；重點是瓦數那一筆
    assert {"field": "max_watts", "before": None, "after": 0} in diff


@pytest.mark.anyio
async def test_deleted_row_permission_change_and_file_change(db_session, tmp_path) -> None:
    d = await _data(db_session, tmp_path)
    before = await snapshot(db_session, upload_dir=str(tmp_path))
    await db_session.delete(await db_session.get(Cable, d["cables"][1].id))
    await db_session.execute(update(Permission).where(Permission.object_id == d["loc"].id).values(level="write"))
    await db_session.commit()
    (tmp_path / "floorplans" / "plan.png").write_bytes(b"\x89PNG replaced")
    r = compare(before, await snapshot(db_session, upload_dir=str(tmp_path)))
    assert not r["ok"]
    assert r["tables"]["cables"]["missing"] == [str(d["cables"][1].id)]
    assert r["tables"]["permissions"]["changed"][0]["diffs"][0]["field"] == "level"
    assert r["files"]["floor_plans"]["changed"] == [str(d["loc"].id)]


@pytest.mark.anyio
async def test_new_rows_and_fields_are_not_failures_but_dropped_fields_are(db_session, tmp_path) -> None:
    await _data(db_session, tmp_path)
    before = await snapshot(db_session, upload_dir=str(tmp_path))
    db_session.add(Device(name="new-after-upgrade"))
    await db_session.commit()
    after = await snapshot(db_session, upload_dir=str(tmp_path))
    added = copy.deepcopy(after)
    t = added["tables"]["devices"]
    t["fields"].append("brand_new_column")
    for v in t["rows"].values():
        v.append(None)
    assert compare(before, added)["ok"], "新列、新欄位另行驗證，不算失敗"
    dropped = copy.deepcopy(after)
    i = dropped["tables"]["devices"]["fields"].index("rack_face")
    dropped["tables"]["devices"]["fields"].pop(i)
    for v in dropped["tables"]["devices"]["rows"].values():
        v.pop(i)
    r = compare(before, dropped)
    assert not r["ok"]
    assert r["tables"]["devices"]["missing_fields"] == ["rack_face"]
    assert compare(before, dropped, allow={"devices.rack_face"})["ok"], "刻意轉換、已另行驗證的欄位可以放行"


@pytest.mark.anyio
async def test_only_new_anomalies_fail(db_session, tmp_path) -> None:
    d = await _data(db_session, tmp_path)
    p = d["ports"]
    # 升級前就有：Gi1 → eth1 的單向配對（不對稱）
    await db_session.execute(update(DevicePort).where(DevicePort.id == p[0].id).values(peer_port_id=p[1].id))
    await db_session.commit()
    before = await snapshot(db_session, upload_dir=str(tmp_path))
    r = compare(before, copy.deepcopy(before))
    assert r["ok"]
    assert r["preexisting_anomalies"]["asymmetric_peers"] == 1
    # 升級後多了一個新的不對稱配對 → 失敗（只有新的才算）
    await db_session.execute(update(DevicePort).where(DevicePort.id == p[2].id).values(peer_port_id=p[3].id))
    await db_session.commit()
    after = await snapshot(db_session, upload_dir=str(tmp_path))
    r = compare(before, after, allow={"device_ports.peer_port_id"})
    assert not r["ok"]
    assert r["new_anomalies"]["asymmetric_peers"] == [str(p[2].id)]


@pytest.mark.anyio
async def test_cli_writes_private_files(db_session, tmp_path, monkeypatch) -> None:
    from app.cli import upgrade_verify as cli
    await _data(db_session, tmp_path)
    snap = await snapshot(db_session, upload_dir=str(tmp_path))

    async def fake_snap():  # type: ignore[no-untyped-def]
        return snap
    monkeypatch.setattr(cli, "_snap", fake_snap)
    import asyncio
    before = tmp_path / "out" / "before.json"
    # CLI 自己會 asyncio.run：放到另一個執行緒跑（測試本身在事件迴圈裡）
    assert await asyncio.to_thread(cli.main, ["snapshot", "--out", str(before)]) == 0
    assert stat.S_IMODE(os.stat(before).st_mode) == 0o600
    report = tmp_path / "out" / "report.json"
    assert await asyncio.to_thread(cli.main, ["compare", "--before", str(before), "--report", str(report)]) == 0
    assert stat.S_IMODE(os.stat(report).st_mode) == 0o600
    assert json.loads(report.read_text())["ok"] is True
