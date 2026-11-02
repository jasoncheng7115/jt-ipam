"""狀態類告警的轉換判斷：只在「開始」與「恢復」時發。

**為什麼需要這一層**：整合同步失敗、代理失聯、系統健康這些描述的是**持續的狀態**，
不是一次性的事件。整合每 5 分鐘同步一次，token 過期的那台會每輪都失敗 ——
每輪發一次通知，一天 288 則，使用者第二天就會把整類關掉，然後真正的新問題
也一起消失。這是通知功能最典型的死法：不是沒發，是發太多所以沒人看。

規則：
- 連續失敗達到 `threshold` 次才算「壞掉」→ 發一次。偶發逾時不吵人。
- 壞著不動的期間不再發。
- 回復正常且**先前曾經吵過** → 發一次「恢復」。沒吵過就恢復的不用特地講。

狀態存在 `system_settings.alert_state`（JSONB）。用設定表而不是新開一張表：
這是幾十筆的小狀態，不值得一次 migration；而且它天生是「目前狀態」，不是歷史。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.system_setting import SystemSetting

ALERT_STATE_KEY = "alert_state"

Transition = Literal["down", "up"]


async def _load(session: AsyncSession) -> tuple[SystemSetting, dict[str, Any]]:
    row = await session.get(SystemSetting, ALERT_STATE_KEY)
    if row is None:
        row = SystemSetting(key=ALERT_STATE_KEY, value={})
        session.add(row)
        await session.flush()
    return row, dict(row.value or {})


async def _save(session: AsyncSession, row: SystemSetting, value: dict[str, Any]) -> None:
    row.value = value
    flag_modified(row, "value")
    await session.flush()


async def observe(
    session: AsyncSession, *, key: str, failing: bool, threshold: int = 2,
) -> Transition | None:
    """記一次觀測，回傳是否發生了值得通知的轉換。

    `key` 要能穩定識別同一個對象（例如 `integration:librenms:<id>`）——
    用名稱當 key 的話，改名就等於「舊的恢復了、新的壞掉了」，會憑空多兩則通知。
    """
    row, state = await _load(session)
    cur = state.get(key) if isinstance(state.get(key), dict) else {}
    fails = int(cur.get("fails") or 0)
    alerted = bool(cur.get("alerted"))
    out: Transition | None = None
    # 維護中（維護排程，2026-11-01）：照常累計，但**不標成已告警**。維護結束時還壞著，下一輪就照常發出；
    # 維護期間壞了又好的，不會在結束後冒出一則「恢復」。只在「本來要發」的那一刻才查，平常不多一次查詢。
    from app.services.maintenance import muted_key

    if failing:
        fails += 1
        if fails >= max(1, threshold) and not alerted:
            if await muted_key(session, key):
                state[key] = {"fails": fails, "alerted": False,
                              "since": cur.get("since") or datetime.now(UTC).isoformat()}
                await _save(session, row, state)
                return None
            alerted = True
            out = "down"
        state[key] = {"fails": fails, "alerted": alerted,
                      "since": cur.get("since") or datetime.now(UTC).isoformat()}
    else:
        if alerted and not await muted_key(session, key):
            out = "up"
        # 恢復就把狀態清掉，而不是留一筆 fails=0 ——
        # 那會讓這份設定隨著時間長出一堆早就正常的東西
        state.pop(key, None)

    await _save(session, row, state)
    return out


async def prune_missing(session: AsyncSession, *, prefix: str, alive: set[str]) -> int:
    """刪掉某個前綴底下、已經不存在的對象的狀態；回傳刪除筆數。

    整合或代理被刪掉之後，它的狀態不該永遠留在設定裡長大 —— 而且留著的話，
    同名的東西再被建出來時會被當成「一直壞著」，不會再通知。
    """
    row, state = await _load(session)
    gone = [k for k in state if k.startswith(prefix) and k not in alive]
    for k in gone:
        state.pop(k, None)
    if gone:
        await _save(session, row, state)
    return len(gone)


async def current(session: AsyncSession, *, prefix: str = "") -> dict[str, Any]:
    """目前處於告警中的對象（給系統診斷頁之類的地方用）。"""
    _, state = await _load(session)
    return {k: v for k, v in state.items()
            if k.startswith(prefix) and isinstance(v, dict) and v.get("alerted")}
