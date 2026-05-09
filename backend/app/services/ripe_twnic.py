"""RIPE / TWNIC whois 文字檔解析 → Subnet 物件。

兩家 NIC 的 whois 文字格式相當相似（key: value 多行），其中我們關心的：

RIPE inetnum / inet6num 物件：
    inetnum:        192.0.2.0 - 192.0.2.255
    netname:        EXAMPLE-NET
    descr:          Example
    country:        TW
    inet6num:       2001:db8::/32

TWNIC 與 RIPE 一致，但 inetnum 通常用 "192.0.2.0/24" 而非範圍。

兩種格式都支援。每個物件以空白行分隔。
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class WhoisRecord:
    cidrs: list[str] = field(default_factory=list)
    netname: str | None = None
    descr: list[str] = field(default_factory=list)
    country: str | None = None
    raw: dict[str, list[str]] = field(default_factory=dict)


_RANGE_RE = re.compile(r"^\s*([0-9.]+)\s*-\s*([0-9.]+)\s*$")


def _parse_inetnum(value: str) -> list[str]:
    """value 可能是 'a.b.c.d - e.f.g.h' 或 'a.b.c.d/24'；轉成 CIDR list。"""
    value = value.strip()
    if "/" in value:
        try:
            net = ipaddress.ip_network(value, strict=False)
            return [str(net)]
        except ValueError:
            return []
    m = _RANGE_RE.match(value)
    if m:
        try:
            start = ipaddress.IPv4Address(m.group(1))
            end = ipaddress.IPv4Address(m.group(2))
        except (ValueError, ipaddress.AddressValueError):
            return []
        return [str(n) for n in ipaddress.summarize_address_range(start, end)]
    return []


def parse_whois(text: str) -> Iterable[WhoisRecord]:
    """把 whois 輸出切成 record（以空白行為界）。"""
    current: dict[str, list[str]] = {}
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("%") or raw_line.lstrip().startswith("#"):
            if current:
                yield _build(current)
                current = {}
            continue
        if ":" not in raw_line:
            continue
        key, _, val = raw_line.partition(":")
        current.setdefault(key.strip().lower(), []).append(val.strip())
    if current:
        yield _build(current)


def _build(d: dict[str, list[str]]) -> WhoisRecord:
    rec = WhoisRecord(raw=d)
    for key in ("inetnum", "inet6num", "cidr", "route", "route6"):
        for v in d.get(key, []):
            rec.cidrs.extend(_parse_inetnum(v))
    rec.netname = (d.get("netname") or [None])[0]
    rec.descr = d.get("descr") or []
    rec.country = (d.get("country") or [None])[0]
    return rec


@dataclass
class ImportPlan:
    cidr: str
    description: str | None
    country: str | None
    netname: str | None


def planify(text: str) -> list[ImportPlan]:
    """把 whois 文字轉成可預覽的匯入計畫（一個 record 可能展開成多個 CIDR）。"""
    plans: list[ImportPlan] = []
    seen: set[str] = set()
    for rec in parse_whois(text):
        descr = " ".join(rec.descr) if rec.descr else None
        for cidr in rec.cidrs:
            if cidr in seen:
                continue
            seen.add(cidr)
            plans.append(ImportPlan(
                cidr=cidr,
                description=" / ".join(filter(None, [rec.netname, descr])),
                country=rec.country,
                netname=rec.netname,
            ))
    return plans
