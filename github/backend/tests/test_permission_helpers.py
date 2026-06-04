"""permission service 的純邏輯單元測試（不碰 DB）。"""

from __future__ import annotations

import pytest

from app.services.permission import has_permission


@pytest.mark.parametrize(
    "actual,required,expected",
    [
        ("none", "read", False),
        ("read", "read", True),
        ("read", "write", False),
        ("write", "read", True),
        ("write", "write", True),
        ("write", "admin", False),
        ("admin", "read", True),
        ("admin", "write", True),
        ("admin", "admin", True),
        ("none", "none", True),
        ("garbage", "read", False),  # unknown 視為 0
    ],
)
def test_has_permission_ranks(actual: str, required: str, expected: bool):
    assert has_permission(actual, required) is expected
