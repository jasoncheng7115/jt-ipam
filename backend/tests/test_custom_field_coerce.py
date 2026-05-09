"""Custom Field 純驗證邏輯（_coerce + select 子集）— 不碰 DB。"""

from __future__ import annotations

import pytest

from app.services.custom_field import CustomFieldError, _coerce


@pytest.mark.parametrize(
    "value,field_type,expected",
    [
        ("hello", "text", "hello"),
        (42, "int", 42),
        ("42", "int", 42),
        (3.14, "float", 3.14),
        ("3.14", "float", 3.14),
        (True, "bool", True),
        ("true", "bool", True),
        ("FALSE", "bool", False),
        ("0", "bool", False),
        ("2026-05-09", "date", "2026-05-09"),
        ("a", "select", "a"),
        (["x", "y"], "multi_select", ["x", "y"]),
    ],
)
def test_coerce_valid(value, field_type, expected):
    assert _coerce(value, field_type) == expected


@pytest.mark.parametrize(
    "value,field_type",
    [
        (object(), "text"),
        ("not-a-number", "int"),
        ("xyz", "float"),
        ("notbool", "bool"),
        ("not-a-date", "date"),
        ("oops", "multi_select"),  # not a list
        (["a", 1], "multi_select"),  # contains non-string
    ],
)
def test_coerce_rejects(value, field_type):
    with pytest.raises(CustomFieldError):
        _coerce(value, field_type)


def test_coerce_unknown_type():
    with pytest.raises(CustomFieldError):
        _coerce("x", "telepathy")
