"""RIPE / TWNIC whois 解析測試（純文字運算）。"""

from __future__ import annotations

from app.services.ripe_twnic import parse_whois, planify


RIPE_SAMPLE = """\
inetnum:        192.0.2.0 - 192.0.2.255
netname:        EXAMPLE-NET
descr:          Example Inc.
descr:          Taipei datacenter
country:        TW

inet6num:       2001:db8::/48
netname:        EXAMPLE-V6
descr:          Example v6 block
country:        TW
"""

TWNIC_SAMPLE = """\
% This is comment
inetnum:        140.123.0.0/16
netname:        NCNU-NET
descr:          National Chi Nan University
country:        TW
"""


def test_parse_basic_record_count():
    rs = list(parse_whois(RIPE_SAMPLE))
    assert len(rs) == 2


def test_inetnum_range_summarised_to_cidr():
    rs = list(parse_whois(RIPE_SAMPLE))
    assert rs[0].cidrs == ["192.0.2.0/24"]
    assert rs[1].cidrs == ["2001:db8::/48"]


def test_descr_concat():
    plans = planify(RIPE_SAMPLE)
    p = plans[0]
    assert p.cidr == "192.0.2.0/24"
    assert "Example Inc." in (p.description or "")
    assert "Taipei datacenter" in (p.description or "")
    assert p.country == "TW"


def test_skip_comments_in_twnic():
    plans = planify(TWNIC_SAMPLE)
    assert len(plans) == 1
    assert plans[0].cidr == "140.123.0.0/16"


def test_dedup_same_cidr():
    text = RIPE_SAMPLE + "\n" + RIPE_SAMPLE
    plans = planify(text)
    cidrs = [p.cidr for p in plans]
    assert cidrs == sorted(set(cidrs), key=cidrs.index)  # 順序保留但無重複
