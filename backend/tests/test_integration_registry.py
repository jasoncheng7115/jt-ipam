"""共用整合清單要與同步腳本的健康檢查名單一致（少列一個，那個整合壞掉時戰情室與診斷都看不出來）。"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from app.services import integration_registry as reg

SYNC = Path(__file__).resolve().parents[2] / "scripts" / "jt-ipam-sync.py"


def test_registry_matches_the_sync_health_list() -> None:
    src = SYNC.read_text(encoding="utf-8")
    block = src[src.index("insts: list = []"):src.index("check_integration_health(session, insts)")]
    sync_kinds = set(re.findall(r'\("([a-z0-9_]+)",\s*[A-Z]\w+\)', block))
    assert len(sync_kinds) >= 20, "偵測方式失效了：同步腳本的名單只抓到很少幾個"
    assert {k for k, *_ in reg.INTEGRATIONS} == sync_kinds


def test_every_model_imports_and_has_the_status_fields() -> None:
    for kind, _label, model in reg.models():
        cols = set(model.__table__.columns.keys())
        assert {"id", "enabled"} <= cols, kind
        assert "last_error" in cols or "last_sync_at" in cols, kind


def test_state_of() -> None:
    now = datetime(2026, 10, 21, 12, tzinfo=UTC)
    row = SimpleNamespace(enabled=True, last_error=None, last_sync_at=now - timedelta(minutes=5),
                          sync_interval_seconds=600)
    assert reg.state_of(row, now=now) == "ok"
    row.last_sync_at = now - timedelta(minutes=31)
    assert reg.state_of(row, now=now) == "stale"                      # 超過三個週期
    row.last_error = "boom"
    assert reg.state_of(row, now=now) == "error"
    row.enabled = False
    assert reg.state_of(row, now=now) == "disabled"
    assert reg.state_of(SimpleNamespace(enabled=True, last_error=None, last_sync_at=None), now=now) == "never"


def test_every_model_has_a_last_success_time() -> None:
    """每種整合都要有一個看得出「最後成功」的欄位，否則永遠是「尚未同步」
    （10-22 使用者：「為何 RustDesk 是尚未同步」，RustDesk 記的是代理最後回報、ISOinsight 記的是最後完整成功）。"""
    for kind, _label, model in reg.models():
        cols = set(model.__table__.columns.keys())
        assert cols & set(reg.LAST_SUCCESS_FIELDS), kind


def test_push_agents_and_isoinsight_are_not_never() -> None:
    now = datetime(2026, 10, 22, 9, tzinfo=UTC)
    rd = SimpleNamespace(enabled=True, last_error=None, agent_last_seen_at=now - timedelta(seconds=20),
                         last_report_at=now - timedelta(minutes=3))
    assert reg.state_of(rd, now=now) == "ok"
    assert reg.last_success(rd) == now - timedelta(seconds=20)            # 取最新的那個
    iso = SimpleNamespace(enabled=True, last_error=None, last_full_success_at=now - timedelta(hours=5))
    assert reg.state_of(iso, now=now) == "stale"
    assert reg.state_of(SimpleNamespace(enabled=True, last_error=None), now=now) == "never"


def test_stale_limit_counts_the_scheduler_period() -> None:
    """同步排程每 5 分鐘跑一輪：設 300 秒（甚至更短）的整合實際也只能每輪一次，門檻不可以比三輪排程還短
    （10-23 戰情室兩台 DNS 伺服器在「正常」與「逾時」之間跳）。"""
    now = datetime(2026, 10, 23, 9, tzinfo=UTC)
    row = SimpleNamespace(enabled=True, last_error=None, last_sync_at=now - timedelta(minutes=14),
                          sync_interval_seconds=60)
    assert reg.state_of(row, now=now) == "ok"
    row.last_sync_at = now - timedelta(minutes=16)
    assert reg.state_of(row, now=now) == "stale"


def test_sync_due_check_tolerates_scheduler_jitter() -> None:
    """到期判斷要容許半個排程週期：上一輪跑到一半才寫入完成時間，設 300 秒的整合不可以每隔一輪才跑一次。
    DNS 也要用自己的同步間隔（以前寫死 600 秒）。"""
    src = SYNC.read_text(encoding="utf-8")
    assert "+ interval > now" not in src and "+ dns_interval > now" not in src
    assert "def _sync_due(" in src
    assert "timedelta(seconds=600)" not in src.split("# ── DNS servers ──")[1][:600]
