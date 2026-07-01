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


def test_parse_pf_rule_labels_label_and_alias():
    """pf_statistics/rules → label → alias 名（真實格式：規則文字是 key）。"""
    from app.services.opnsense_firewall import _parse_pf_rule_labels
    data = {"rules": {"filter rules": {
        '@21 block drop in log quick inet from <crowdsec_blocklists:21502> to any label "031d9d1edc75c3c8c634a8aee47134ef"': {},
        '@55 pass in log quick on pppoe0 inet proto tcp from <jasontools:1> to (pppoe0:1) port = 39443 label "7df315ceb66dc1bb2fd503f69343a8b3"': {},
        '@5 block drop in log inet all label "ecd3a310894625657c6591b80daa956a"': {},  # 無 alias → 不收
    }}}
    out = _parse_pf_rule_labels(data)
    assert out["031d9d1edc75c3c8c634a8aee47134ef"]["alias_names"] == ["crowdsec_blocklists"]
    assert out["031d9d1edc75c3c8c634a8aee47134ef"]["action"] == "block"
    assert out["7df315ceb66dc1bb2fd503f69343a8b3"]["alias_names"] == ["jasontools"]
    assert out["7df315ceb66dc1bb2fd503f69343a8b3"]["interface"] == "pppoe0"
    assert "ecd3a310894625657c6591b80daa956a" not in out  # 無引用 alias 的規則略過


def test_parse_pf_rule_labels_uuid_label():
    """rid（filterlog rule label）也可能是含「-」的 UUID 格式；舊 regex 漏抓整條。"""
    from app.services.opnsense_firewall import _parse_pf_rule_labels
    rid = "ace97705-b0f1-4058-ba15-4991f3d1dd0d"
    data = {"rules": {"filter rules": {
        f'@70 block drop in log quick on igb0 from <blocklist_talos:8080> to any label "{rid}"': {},
    }}}
    out = _parse_pf_rule_labels(data)
    assert out[rid]["alias_names"] == ["blocklist_talos"]
    assert out[rid]["action"] == "block"
