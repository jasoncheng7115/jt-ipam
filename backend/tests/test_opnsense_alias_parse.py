"""OPNsense get-view 格式解析（純函式）：select 欄位取被選中的 key、
alias content 轉成員清單。涵蓋 dict / 純量 / 逗號 / 換行 / list 等變形。"""

from __future__ import annotations

from app.services.opnsense_firewall import _opn_members, _opn_selected


def test_opn_selected_dict_picks_selected_key():
    v = {"host": {"value": "Host(s)", "selected": 1},
         "network": {"value": "Network(s)", "selected": 0}}
    assert _opn_selected(v) == "host"


def test_opn_selected_multiple_selected_comma_joined():
    v = {"a": {"selected": "1"}, "b": {"selected": "0"}, "c": {"selected": "1"}}
    assert _opn_selected(v) == "a,c"


def test_opn_selected_scalar_and_none():
    assert _opn_selected("1") == "1"
    assert _opn_selected(None) == ""
    assert _opn_selected(0) == "0"


def test_opn_members_dict_selected_subset():
    content = {"10.0.0.1": {"selected": 1}, "10.0.0.2": {"selected": 0},
               "10.0.0.3": {"selected": 1}}
    assert _opn_members(content) == ["10.0.0.1", "10.0.0.3"]


def test_opn_members_dict_none_selected_falls_back_to_all_keys():
    content = {"a": {"selected": 0}, "b": {"selected": 0}}
    assert _opn_members(content) == ["a", "b"]


def test_opn_members_string_comma_and_newline():
    assert _opn_members("10.0.0.1, 10.0.0.2") == ["10.0.0.1", "10.0.0.2"]
    assert _opn_members("10.0.0.1\n10.0.0.2\n") == ["10.0.0.1", "10.0.0.2"]


def test_opn_members_list_and_empty():
    assert _opn_members(["x", 1]) == ["x", "1"]
    assert _opn_members(None) == []
    assert _opn_members(123) == []
