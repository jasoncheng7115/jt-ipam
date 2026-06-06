"""掃描探測目錄 + OS 指紋的純函式單元測試（免 DB）。"""

from __future__ import annotations

from app.core import scan_probes as sp
from app.core.os_fingerprint import family_from_ttl, normalize_os


def test_default_agent_probes_is_icmp_only() -> None:
    assert sp.DEFAULT_AGENT_PROBES == ["icmp"]


def test_normalize_probes_aliases_and_filters() -> None:
    # nmap 舊別名 → os；未知 key 丟掉；去重並保持目錄順序
    assert sp.normalize_probes(["nmap", "icmp", "bogus", "tcp"]) == ["icmp", "tcp", "os"]
    assert sp.normalize_probes(None) == []


def test_effective_probes_three_layers() -> None:
    # 子網路要跑 − IP 略過 ∩ 代理能力
    eff = sp.effective_probes(["icmp", "os", "tcp"], ["os"], ["icmp", "tcp"])
    assert eff == ["icmp", "tcp"]
    # 沒指派代理（None）→ 無能力上限
    assert sp.effective_probes(["icmp", "rdns"], [], None) == ["icmp", "rdns"]
    # IP 略過 icmp
    assert sp.effective_probes(["icmp"], ["icmp"], ["icmp"]) == []


def test_probe_intervals_clamps_to_min() -> None:
    iv = sp.probe_intervals({"os": 100, "icmp": 30})
    assert iv["os"] == sp.PROBES["os"]["min_interval_seconds"]   # 100 < min → clamp
    assert iv["icmp"] == sp.PROBES["icmp"]["min_interval_seconds"]  # 30 < 60 → clamp
    # 未覆寫的用預設
    assert iv["rdns"] == sp.PROBES["rdns"]["default_interval_seconds"]


def test_fast_interval_is_min_light_at_least_60() -> None:
    iv = sp.probe_intervals(None)
    assert sp.fast_interval(iv) >= 60


def test_os_normalize() -> None:
    assert normalize_os("Microsoft Windows Server 2019") == "windows"
    assert normalize_os("Ubuntu 22.04 Linux") == "linux"
    assert normalize_os("pfSense 2.7") == "bsd"
    assert normalize_os("Cisco IOS XE") == "network"
    assert normalize_os("ESXi 8.0") == "hypervisor"
    assert normalize_os("Synology DSM") == "storage"
    assert normalize_os(None) == "unknown"
    assert normalize_os("totally random") == "unknown"


def test_ttl_fallback() -> None:
    assert family_from_ttl(64) == "linux"
    assert family_from_ttl(128) == "windows"
    assert family_from_ttl(255) == "network"
    assert family_from_ttl(None) is None


def test_catalog_for_api_shape() -> None:
    cat = sp.catalog_for_api()
    assert any(p["key"] == "icmp" and p["default_on"] for p in cat)
    assert all({"key", "label_en", "label_zh", "klass", "intrusive"} <= set(p) for p in cat)
