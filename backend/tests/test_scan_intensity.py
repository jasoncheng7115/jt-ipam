"""偵測強度：對端點防護友善（客戶回報 2026-11-24）。

客戶的 Symantec Endpoint Protection（SEP）在 jt-ipam「偵測全開」時跳出通知並封鎖掃描代理：
定期 OS 偵測是 nmap -T4、前 100 個埠、-O 作業系統指紋、-sV 加 smb-os-discovery，正是端點入侵防護
會判成攻擊的組合（埠掃描、OS 指紋）。子網路多一個「偵測強度」：
- normal：照舊
- gentle：不做 OS 指紋（-O）、放慢（-T2）、只看判斷設備類型用的那十幾個埠、不跑 SMB 腳本；
  banner、網頁標題、憑證照讀（伺服器判讀設備類型還用得到）
伺服器只在 gentle 時把 intensity 帶給代理；舊代理看不懂這個欄位，照舊掃。
"""
from __future__ import annotations

import importlib.util
import pathlib
import uuid

import pytest
from app.models.scan_agent import ScanAgent
from app.models.section import Section
from app.models.subnet import Subnet

pytestmark = pytest.mark.anyio


async def test_subnet_api_takes_the_intensity(client, auth_headers, db_session) -> None:
    sec = Section(name=f"sec-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.commit()
    r = await client.post("/api/v1/subnets", headers=auth_headers, json={"section_id": str(sec.id), "cidr": "198.51.100.0/24"})
    assert r.status_code == 201, r.text
    assert r.json()["scan_intensity"] == "normal", "預設照舊"
    sid = r.json()["id"]
    r = await client.patch(f"/api/v1/subnets/{sid}", headers=auth_headers, json={"scan_intensity": "gentle"})
    assert r.status_code == 200, r.text
    assert r.json()["scan_intensity"] == "gentle"
    r = await client.patch(f"/api/v1/subnets/{sid}", headers=auth_headers, json={"scan_intensity": "stealth"})
    assert r.status_code == 422


async def test_poll_tells_the_agent_only_when_gentle(client, db_session) -> None:
    from app.api.v1.endpoints.scan_agents import _key_hash
    raw = "g" * 40
    agent = ScanAgent(name=f"a-{uuid.uuid4().hex[:6]}", enroll_key_hash=_key_hash(raw), enabled=True,
                      enabled_probes=["icmp", "os", "ports"])
    sec = Section(name=f"sec-{uuid.uuid4().hex[:6]}")
    db_session.add_all([agent, sec])
    await db_session.flush()
    a = Subnet(section_id=sec.id, cidr="198.51.100.0/24", scan_enabled=True, scan_agent_id=agent.id,
               scan_method=["icmp", "os", "ports"], scan_intensity="gentle")
    b = Subnet(section_id=sec.id, cidr="203.0.113.0/24", scan_enabled=True, scan_agent_id=agent.id,
               scan_method=["icmp", "os", "ports"])
    db_session.add_all([a, b])
    await db_session.commit()
    body = (await client.get("/api/v1/scan-agents/poll", headers={"X-Agent-Key": raw})).json()
    by_cidr = {x["cidr"]: x for x in body["subnets"]}
    assert by_cidr["198.51.100.0/24"]["intensity"] == "gentle"
    assert "intensity" not in by_cidr["203.0.113.0/24"], "一般強度不帶：跟舊伺服器送的一模一樣"


# ─────────────────── 代理：nmap 參數 ───────────────────

def _agent():  # type: ignore[no-untyped-def]
    path = pathlib.Path(__file__).resolve().parents[2] / "agent" / "jt_ipam_agent.py"
    spec = importlib.util.spec_from_file_location("jt_ipam_agent_intensity", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _argv(mod, monkeypatch, probes):  # type: ignore[no-untyped-def]
    seen: list[list[str]] = []

    class R:
        stdout = ""

    monkeypatch.setattr(mod.shutil, "which", lambda _n: "/usr/bin/nmap")
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **_kw: (seen.append(cmd), R())[1])
    mod._heavy_nmap("198.51.100.7", probes)
    return seen


def test_normal_mode_is_unchanged(monkeypatch) -> None:
    mod = _agent()
    first = _argv(mod, monkeypatch, ["os", "ports"])[0]
    assert "-O" in first and "-T4" in first and "--top-ports" in first
    assert "smb-os-discovery" in first[first.index("--script") + 1]


def test_gentle_mode_skips_os_fingerprint_and_slows_down(monkeypatch) -> None:
    mod = _agent()
    runs = _argv(mod, monkeypatch, ["os", "ports", "gentle"])
    for argv in runs:          # 包括「沒資料時再跑一次」的備援，也不可以偷偷加回 -O
        assert "-O" not in argv and "--osscan-guess" not in argv
        assert "-T2" in argv and "-T4" not in argv
        assert "--top-ports" not in argv, "只看判斷設備類型用的那十幾個埠"
        if "--script" in argv:
            assert "smb-os-discovery" not in argv[argv.index("--script") + 1]
    first = runs[0]
    ports = first[first.index("-p") + 1].split(",")
    assert "554" in ports and "9100" in ports and len(ports) < 30
    assert "-sV" in first, "服務 banner 照讀：伺服器判讀設備類型要用"


def test_gentle_marker_is_not_reported_as_a_probe(monkeypatch) -> None:
    mod = _agent()
    monkeypatch.setattr(mod.shutil, "which", lambda _n: None)
    item = mod._heavy_nmap("198.51.100.7", ["os", "gentle"])
    assert item["probes_run"] == ["os"]


def test_queue_keeps_gentle_when_merging() -> None:
    """同一台主機被兩個子網路排進佇列：任一個是 gentle 就用 gentle（寧可保守）。"""
    mod = _agent()
    q = mod._HeavyQueue()
    q.submit("s1", "198.51.100.7", ["os"])
    q.submit("s2", "198.51.100.7", ["ports", "gentle"])
    (ip, probes), = q.take(5)
    assert set(probes) == {"os", "ports", "gentle"}
