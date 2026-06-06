"""掃描探測項目目錄（單一真相）。

三層設定共用此目錄：掃描代理（能力天花板）、子網路（要跑哪些）、IP（略過哪些）。
每個 probe 有自己的執行間隔，輕量項目走「快迴圈」、重項目（os/ports）走「慢迴圈」，
避免 OS 偵測跟 ICMP 同頻。前端 GET /api/v1/scan-agents/probes 取得同一份目錄。
"""

from __future__ import annotations

from typing import Any

# klass: "light" = 存活/名稱類，可高頻；"heavy" = 侵入性，需拉長間隔
# default_interval_seconds: 預設執行間隔（秒）
# min_interval_seconds: UI 允許的最小值（重項目強制下限，避免誤設過頻）
# intrusive: 是否侵入（IDS 可能告警）→ UI 標警語、預設關
# needs: 額外條件（人類可讀；agent 會自我回報實際是否可用）
PROBES: dict[str, dict[str, Any]] = {
    "icmp": {
        "label_en": "ICMP Ping", "label_zh": "ICMP Ping",
        "klass": "light", "default_interval_seconds": 300, "min_interval_seconds": 60,
        "intrusive": False, "default_on": True, "needs": "ping",
    },
    "tcp": {
        "label_en": "TCP port liveness", "label_zh": "TCP 連接埠存活",
        "klass": "light", "default_interval_seconds": 300, "min_interval_seconds": 60,
        "intrusive": False, "default_on": False, "needs": "",
    },
    "arp": {
        "label_en": "ARP (L2)", "label_zh": "ARP 探測（L2）",
        "klass": "light", "default_interval_seconds": 300, "min_interval_seconds": 60,
        "intrusive": False, "default_on": False, "needs": "same L2 segment, root/cap_net_raw",
    },
    "rdns": {
        "label_en": "Reverse DNS (PTR)", "label_zh": "反解 PTR",
        "klass": "light", "default_interval_seconds": 3600, "min_interval_seconds": 300,
        "intrusive": False, "default_on": False, "needs": "DNS reachable",
    },
    "netbios": {
        "label_en": "NetBIOS name", "label_zh": "NetBIOS 名稱",
        "klass": "light", "default_interval_seconds": 3600, "min_interval_seconds": 300,
        "intrusive": False, "default_on": False, "needs": "UDP 137",
    },
    "mdns": {
        "label_en": "mDNS name", "label_zh": "mDNS 名稱",
        "klass": "light", "default_interval_seconds": 3600, "min_interval_seconds": 300,
        "intrusive": False, "default_on": False, "needs": "UDP 5353 (same segment)",
    },
    "os": {
        "label_en": "OS detection", "label_zh": "OS 偵測",
        "klass": "heavy", "default_interval_seconds": 86400, "min_interval_seconds": 21600,
        "intrusive": True, "default_on": False, "needs": "nmap + root/cap_net_raw",
    },
    "ports": {
        "label_en": "Port / service scan", "label_zh": "連接埠 / 服務掃描",
        "klass": "heavy", "default_interval_seconds": 86400, "min_interval_seconds": 21600,
        "intrusive": True, "default_on": False, "needs": "nmap",
    },
}

# 舊詞彙相容：早期 subnet.scan_method 可能存過 "nmap"（含混 os+ports）→ 視為 os
LEGACY_ALIASES: dict[str, str] = {"nmap": "os"}

VALID_PROBES: frozenset[str] = frozenset(PROBES)
DEFAULT_AGENT_PROBES: list[str] = [k for k, v in PROBES.items() if v["default_on"]]  # ["icmp"]
LIGHT_PROBES: frozenset[str] = frozenset(k for k, v in PROBES.items() if v["klass"] == "light")
HEAVY_PROBES: frozenset[str] = frozenset(k for k, v in PROBES.items() if v["klass"] == "heavy")


def normalize_probes(values: list[str] | None) -> list[str]:
    """把清單正規化：套用舊別名、丟掉未知 key、去重並保持目錄順序。"""
    if not values:
        return []
    mapped = {LEGACY_ALIASES.get(v, v) for v in values}
    return [k for k in PROBES if k in mapped]


def effective_probes(
    subnet_methods: list[str] | None,
    excluded: list[str] | None,
    agent_enabled: list[str] | None,
) -> list[str]:
    """某 IP 實際會被執行的探測：子網路要跑 − IP 略過 ∩ 代理能力天花板。
    agent_enabled=None 代表沒指派代理（走本機掃描，無能力上限）。"""
    eff = set(normalize_probes(subnet_methods)) - set(normalize_probes(excluded))
    if agent_enabled is not None:
        eff &= set(normalize_probes(agent_enabled))
    return [k for k in PROBES if k in eff]


def probe_intervals(overrides: dict[str, Any] | None) -> dict[str, int]:
    """合併目錄預設間隔與代理層覆寫（覆寫不得低於各項 min）。"""
    out: dict[str, int] = {k: int(v["default_interval_seconds"]) for k, v in PROBES.items()}
    for k, v in (overrides or {}).items():
        if k in PROBES:
            try:
                out[k] = max(int(v), int(PROBES[k]["min_interval_seconds"]))
            except (TypeError, ValueError):
                continue
    return out


def fast_interval(intervals: dict[str, int]) -> int:
    """快迴圈節奏 = 所有 light 項目間隔的最小值（至少 60s）。"""
    light = [intervals[k] for k in LIGHT_PROBES if k in intervals]
    return max(60, min(light)) if light else 300


def catalog_for_api() -> list[dict[str, Any]]:
    """給前端的目錄（含 key / 雙語 label / 類別 / 預設間隔 / 侵入性）。"""
    return [
        {
            "key": k,
            "label_en": v["label_en"],
            "label_zh": v["label_zh"],
            "klass": v["klass"],
            "intrusive": v["intrusive"],
            "default_on": v["default_on"],
            "default_interval_seconds": v["default_interval_seconds"],
            "min_interval_seconds": v["min_interval_seconds"],
            "needs": v["needs"],
        }
        for k, v in PROBES.items()
    ]
