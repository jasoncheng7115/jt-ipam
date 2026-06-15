"""回歸測試：CSV 實際匯入（非 dry-run）傳給 spawn_task 的 target_label 必須是 str。

subnet.cidr 是 asyncpg 的 IPv4Network 物件；直接塞進 background_tasks.target_label(VARCHAR)
會 asyncpg DataError（dry-run OK、實際匯入 500）。GitHub issue #4 / 客戶回報。
"""

from __future__ import annotations

import uuid

from app.models.section import Section
from app.models.subnet import Subnet


async def test_import_passes_str_label_to_spawn_task(client, auth_headers, db_session, monkeypatch):
    sec = Section(name=f"csv-sec-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="192.72.214.0/24")
    db_session.add(sub)
    await db_session.commit()

    captured: dict = {}

    async def fake_spawn_task(**kwargs):
        captured.update(kwargs)

        class _Task:
            id = uuid.uuid4()
            status = "queued"
        return _Task()

    # 端點內 `from app.services.background_tasks import spawn_task`（呼叫時才 import）→ patch 模組屬性即可
    monkeypatch.setattr("app.services.background_tasks.spawn_task", fake_spawn_task)

    r = await client.post(
        "/api/v1/addresses/import",
        headers=auth_headers,
        data={"subnet_id": str(sub.id), "dry_run": "false"},
        files={"file": ("ips.csv", "ip\n192.72.214.5\n", "text/csv")},
    )
    assert r.status_code == 200, r.text
    assert "target_label" in captured, "spawn_task was not called"
    assert isinstance(captured["target_label"], str), (
        f"target_label must be str, got {type(captured['target_label']).__name__}"
    )
    assert captured["target_label"] == "192.72.214.0/24"
