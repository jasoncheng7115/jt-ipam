"""每次巡檢要「取代」上一次的未處理發現，而不是疊上去。

實機情況（v0.5.142）：跑了四次巡檢，四次的發現全部留著累加成 62 筆，而且大半是同一件事。
原因是模型每次把 IP 分組的方式不一樣：

    8/3  {.97, .46, .129} ＋ {.54}
    8/4  {.54, .129, .46} ＋ {.97}

指紋是「分類＋IP 集合」，換個分法就變成不同的指紋，於是被當成新發現存下來。

AI 巡檢是「現在有什麼問題」的**快照**，不是逐次累加的流水帳 —— 所以每次執行完，未處理
清單就該等於這次的結果。已忽略的不動（那是抑制的依據），仍然存在的發現保留原本的發現
時間（才看得出「從什麼時候就這樣了」）。
"""
from __future__ import annotations

import uuid

import pytest
from app.models.ai_finding import AIFinding
from app.services.ai_audit import fingerprint, reconcile_findings
from sqlalchemy import select


def _item(cat: str, ips: list[str], title: str = "t") -> dict:
    return {"severity": "high", "category": cat, "title": title, "detail": "d",
            "recommendation": "r", "evidence": {"ips": ips, "note": "n"}}


async def _existing(db_session, run_id, items, status="open"):
    for it in items:
        db_session.add(AIFinding(run_id=run_id, fingerprint=fingerprint(it),
                                 status=status, **it))
    await db_session.flush()


@pytest.mark.anyio
async def test_previous_open_findings_are_replaced(db_session):
    old_run, new_run = uuid.uuid4(), uuid.uuid4()
    await _existing(db_session, old_run, [_item("exposure", ["10.0.0.1"], "舊的")])
    kept = await reconcile_findings(db_session, new_run,
                                    [_item("exposure", ["10.0.0.2"], "新的")])
    await db_session.flush()
    rows = (await db_session.execute(
        select(AIFinding).where(AIFinding.status == "open"))).scalars().all()
    assert kept == 1
    assert [r.title for r in rows] == ["新的"], "上一次的未處理發現要被這次取代，不是疊加"


@pytest.mark.anyio
async def test_persisting_finding_keeps_its_original_time(db_session):
    """同一件事仍然存在 → 保留原本的發現時間，否則永遠看起來像今天才出現。"""
    old_run, new_run = uuid.uuid4(), uuid.uuid4()
    same = _item("exposure", ["10.0.0.1"])
    await _existing(db_session, old_run, [same])
    first = (await db_session.execute(select(AIFinding))).scalars().first()
    born = first.created_at

    await reconcile_findings(db_session, new_run, [same])
    await db_session.flush()
    rows = (await db_session.execute(
        select(AIFinding).where(AIFinding.status == "open"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].created_at == born


@pytest.mark.anyio
async def test_dismissed_findings_survive_and_still_suppress(db_session):
    """已忽略的是抑制的依據，不能被取代掉；同一件事再出現也不該跳回未處理。"""
    old_run, new_run = uuid.uuid4(), uuid.uuid4()
    it = _item("naming", ["10.0.0.9"])
    await _existing(db_session, old_run, [it], status="dismissed")

    kept = await reconcile_findings(db_session, new_run, [it])
    await db_session.flush()
    assert kept == 0, "被忽略過的不該重新變成未處理"
    opened = (await db_session.execute(
        select(AIFinding).where(AIFinding.status == "open"))).scalars().all()
    assert not opened
    dismissed = (await db_session.execute(
        select(AIFinding).where(AIFinding.status == "dismissed"))).scalars().all()
    assert len(dismissed) == 1


@pytest.mark.anyio
async def test_regrouped_ips_do_not_multiply(db_session):
    """模型換一種分組方式重講同一件事，不能變成兩筆。

    這就是實機上 62 筆的來源：{.97,.46,.129}＋{.54} 隔天變成 {.54,.129,.46}＋{.97}。
    """
    run1, run2 = uuid.uuid4(), uuid.uuid4()
    await reconcile_findings(db_session, run1, [
        _item("exposure", ["192.168.123.97", "192.168.123.46", "192.168.123.129"]),
        _item("exposure", ["192.168.123.54"]),
    ])
    await db_session.flush()
    await reconcile_findings(db_session, run2, [
        _item("exposure", ["192.168.123.54", "192.168.123.129", "192.168.123.46"]),
        _item("exposure", ["192.168.123.97"]),
    ])
    await db_session.flush()
    rows = (await db_session.execute(
        select(AIFinding).where(AIFinding.status == "open"))).scalars().all()
    assert len(rows) == 2, f"換分組不該讓筆數翻倍，實得 {len(rows)}"
    covered = {ip for r in rows for ip in (r.evidence or {}).get("ips", [])}
    assert covered == {"192.168.123.97", "192.168.123.46", "192.168.123.129", "192.168.123.54"}


@pytest.mark.anyio
async def test_a_partial_run_does_not_delete_what_it_never_looked_at(db_session):
    """有批次失敗時，不可以刪掉「這次沒再出現」的發現。

    巡檢是分批送給模型的。只要有一批失敗（逾時、模型回不成 JSON…），那一批的資料
    這次**根本沒有被檢查過** —— 它裡面的問題自然不會出現在結果裡。若照常對齊，
    那些發現會被當成「已經解決」而刪除，畫面上看起來就像問題自己好了。

    所以部分失敗時只做新增與更新，不做刪除；寧可留著一筆可能已修好的，
    也不要讓一個還在的問題安靜消失。
    """
    old_run, new_run = uuid.uuid4(), uuid.uuid4()
    stale = _item("exposure", ["10.0.0.1"], "沒被這輪檢查到的")
    await _existing(db_session, old_run, [stale])

    kept = await reconcile_findings(db_session, new_run,
                                    [_item("stale", ["10.0.0.9"], "這輪找到的")],
                                    partial=True)
    await db_session.flush()
    titles = {r.title for r in (await db_session.execute(
        select(AIFinding).where(AIFinding.status == "open"))).scalars().all()}
    assert "沒被這輪檢查到的" in titles, "把沒檢查過的發現當成已解決刪掉了"
    assert "這輪找到的" in titles
    assert kept == 1, "回傳值仍然是「這次實際看到的筆數」"
