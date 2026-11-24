"""掃描代理：上線偵測與重量探測分開（2026-09-28 使用者要求「一台代理能扛多少」的結構修正）。

正式環境實際發生：一台代理（3 個子網路、約 290 個 IP）跑 OS 指紋那一輪從 12:21 跑到 13:05 還沒完，
這段期間**三個子網路的上線狀態都沒有回報**（整輪跑完才回報一次），連代理自動更新都卡住。
原因是上線偵測之後，反解／NetBIOS／mDNS／OS 指紋對每台在線主機一台接一台跑，全部做完才回報。

修法：
- 快速循環只做上線偵測（ping／TCP／ARP／DHCP），每個子網路做完立刻回報
- 反解、NetBIOS、mDNS、OS 指紋、連接埠丟給背景工作，限量平行，跑完一批回報一批
- 背景工作的結果**不算上線證據**（反解是 DNS 回答的，不是主機本身）：回報時標 liveness=False
- 每輪回報耗時與待辦量（下一步做負載面板與超載通知用）
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.models.address import IPAddress
from app.models.scan_agent import ScanAgent
from app.models.section import Section
from app.models.subnet import Subnet


def _agent_module():
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[2] / "agent" / "jt_ipam_agent.py"
    spec = importlib.util.spec_from_file_location(f"jt_agent_split_{uuid.uuid4().hex[:6]}", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ─────────────────── 代理端 ───────────────────

def test_probes_split_into_liveness_and_heavy() -> None:
    mod = _agent_module()
    light, heavy = mod._split_probes(["icmp", "tcp", "arp", "dhcp", "rdns", "netbios", "mdns", "os", "ports"])
    assert light == ["icmp", "tcp", "arp", "dhcp"]
    assert heavy == ["rdns", "netbios", "mdns", "os", "ports"]


def _fake_server(posts: list[dict]):
    def fake_req(method, path, body=None, extra_headers=None):
        if method == "GET":
            return {"agent": "a", "interval_seconds": 300, "intervals": {"os": 86400},
                    "subnets": [
                        {"subnet_id": "s1", "cidr": "198.51.100.0/30", "probes": ["icmp", "rdns", "os"]},
                        {"subnet_id": "s2", "cidr": "203.0.113.0/30", "probes": ["icmp"]},
                    ]}
        posts.append({"path": path, "body": body})
        return {"updated": 0}
    return fake_req


def test_scan_reports_each_subnet_right_away_and_queues_heavy_work(monkeypatch) -> None:
    mod = _agent_module()
    posts: list[dict] = []
    queued: list[tuple] = []

    def heavy_inline(*a, **k):
        raise AssertionError("heavy probe ran inside the liveness loop")

    monkeypatch.setattr(mod, "_req", _fake_server(posts))
    monkeypatch.setattr(mod, "_capabilities", lambda: list(mod.ALL_PROBES))
    monkeypatch.setattr(mod, "_tools_header", lambda: "")
    monkeypatch.setattr(mod, "_maybe_self_update", lambda sha: None)
    monkeypatch.setattr(mod, "_ping", lambda ip: ip.endswith(".1"))
    monkeypatch.setattr(mod, "_arp_table", lambda: {})
    for name in ("_rdns", "_netbios", "_mdns", "_nmap_os_ports"):
        monkeypatch.setattr(mod, name, heavy_inline)
    monkeypatch.setattr(mod._HEAVY, "submit", lambda subnet_id, ip, probes: queued.append((subnet_id, ip, probes)))

    mod.scan_once()

    reports = [p["body"] for p in posts if p["path"] == "/api/v1/scan-agents/report"]
    # 每個子網路各回報一次，最後再一筆整輪統計
    per_subnet = [r for r in reports if r.get("results")]
    assert [[x["ip"] for x in r["results"]] for r in per_subnet] == [["198.51.100.1"], ["203.0.113.1"]]
    assert per_subnet[0]["results"][0]["probes_run"] == ["icmp"]
    assert queued == [("s1", "198.51.100.1", ["rdns", "os"])]
    cycle = reports[-1]["cycle"]
    assert [s["cidr"] for s in cycle["subnets"]] == ["198.51.100.0/30", "203.0.113.0/30"]
    assert cycle["subnets"][0]["alive"] == 1
    assert cycle["subnets"][0]["hosts"] == 2
    assert cycle["interval_s"] == 300
    assert "duration_s" in cycle
    assert "heavy_backlog" in cycle


def test_the_heavy_queue_does_not_queue_the_same_host_twice() -> None:
    mod = _agent_module()
    q = mod._HeavyQueue()
    q.submit("s1", "198.51.100.1", ["os"])
    q.submit("s1", "198.51.100.1", ["os"])
    q.submit("s1", "198.51.100.2", ["os"])
    assert q.pending() == 2


def test_heavy_results_are_not_liveness_evidence(monkeypatch) -> None:
    mod = _agent_module()
    monkeypatch.setattr(mod, "_rdns", lambda ip: ("h.example.net", False))
    monkeypatch.setattr(mod, "_netbios", lambda ip: "WIN1")
    monkeypatch.setattr(mod, "_mdns", lambda ip: None)
    monkeypatch.setattr(mod, "_nmap_os_ports", lambda ip, want_os, want_ports, gentle=False: {"os_guess": "Linux 5.x"})
    item = mod._heavy_probe_host("198.51.100.1", ["rdns", "netbios", "mdns", "os"])
    assert item["liveness"] is False
    assert item["alive"] is True
    assert item["rdns"] == "h.example.net"
    assert item["netbios"] == "WIN1"
    assert item["os_guess"] == "Linux 5.x"
    assert item["probes_run"] == ["rdns", "netbios", "mdns", "os"]


def test_heavy_worker_runs_hosts_in_parallel_and_reports_in_batches(monkeypatch) -> None:
    mod = _agent_module()
    posts: list[dict] = []
    monkeypatch.setattr(mod, "_req", _fake_server(posts))
    monkeypatch.setattr(mod, "_heavy_names",
                        lambda ip, probes: {"ip": ip, "alive": True, "liveness": False, "probes_run": ["rdns"]})
    q = mod._HeavyQueue()
    for i in range(1, 6):
        q.submit("s1", f"198.51.100.{i}", ["rdns"])
    done = mod._heavy_drain(q, batch=2)
    assert done == 5
    assert q.pending() == 0
    sent = [x["ip"] for p in posts for x in p["body"]["results"]]
    assert sorted(sent) == [f"198.51.100.{i}" for i in range(1, 6)]
    assert all(len(p["body"]["results"]) <= 2 for p in posts)


def test_names_are_reported_without_waiting_for_the_os_fingerprint(monkeypatch) -> None:
    """名稱查詢幾秒就完成；OS 指紋每台可能要一分多鐘。名稱不該等 OS 指紋一起回報。"""
    import threading
    mod = _agent_module()
    posts: list[dict] = []
    release = threading.Event()
    monkeypatch.setattr(mod, "_req", _fake_server(posts))
    monkeypatch.setattr(mod, "_heavy_names",
                        lambda ip, probes: {"ip": ip, "alive": True, "liveness": False, "rdns": "h.example.net",
                                            "probes_run": ["rdns"]})

    def slow_nmap(ip, probes):
        release.wait(5)
        return {"ip": ip, "alive": True, "liveness": False, "os_guess": "Linux", "probes_run": ["os"]}

    monkeypatch.setattr(mod, "_heavy_nmap", slow_nmap)
    monkeypatch.setattr(mod, "HEAVY_FLUSH_S", 0.2)
    q = mod._HeavyQueue()
    q.submit("s1", "198.51.100.1", ["rdns", "os"])
    t = threading.Thread(target=mod._heavy_drain, args=(q,), kwargs={"batch": 50})
    t.start()
    import time
    deadline = time.time() + 3
    while time.time() < deadline and not posts:
        time.sleep(0.05)
    first = [x for p in posts for x in p["body"]["results"]]
    assert first, "名稱查詢的結果應該先回報"
    assert first[0].get("rdns") == "h.example.net"
    assert "os_guess" not in first[0]
    release.set()
    t.join(5)
    assert any(x.get("os_guess") == "Linux" for p in posts for x in p["body"]["results"])


# ─────────────────── 後端 ───────────────────

async def _agent_ip(db):
    from app.api.v1.endpoints.scan_agents import _key_hash
    raw = "s" * 40
    sec = Section(name=f"sec-{uuid.uuid4().hex[:6]}")
    db.add(sec)
    await db.flush()
    agent = ScanAgent(name=f"agent-{uuid.uuid4().hex[:6]}", enroll_key_hash=_key_hash(raw), enabled=True)
    db.add(agent)
    await db.flush()
    sub = Subnet(section_id=sec.id, cidr="198.51.100.0/24", scan_agent_id=agent.id)
    db.add(sub)
    await db.flush()
    old = datetime.now(UTC) - timedelta(hours=5)
    ip = IPAddress(subnet_id=sub.id, ip="198.51.100.7", state="active", last_seen_scanner=old)
    db.add(ip)
    await db.commit()
    return raw, agent, ip, old


async def test_a_heavy_result_does_not_count_as_seen_online(client, db_session) -> None:
    raw, _, ip, old = await _agent_ip(db_session)
    r = await client.post("/api/v1/scan-agents/report", headers={"X-Agent-Key": raw}, json={
        "results": [{"ip": "198.51.100.7", "alive": True, "liveness": False, "os_guess": "Linux 5.x",
                     "probes_run": ["os"]}]})
    assert r.status_code == 200, r.text
    await db_session.refresh(ip)
    assert ip.last_seen_scanner == old                 # 沒有被當成「剛剛看到它上線」
    assert ip.os_guess == "Linux 5.x"                  # 但資料有補上


async def test_a_heavy_result_never_creates_an_ip(client, db_session) -> None:
    raw, agent, _, _ = await _agent_ip(db_session)
    agent.auto_create_ips = True
    await db_session.commit()
    r = await client.post("/api/v1/scan-agents/report", headers={"X-Agent-Key": raw}, json={
        "results": [{"ip": "198.51.100.99", "alive": True, "liveness": False, "rdns": "x.example.net"}]})
    assert r.status_code == 200, r.text
    from sqlalchemy import select
    found = (await db_session.execute(select(IPAddress).where(IPAddress.ip == "198.51.100.99"))).scalars().first()
    assert found is None


async def test_the_latest_cycle_stats_are_kept_on_the_agent(client, db_session) -> None:
    raw, agent, _, _ = await _agent_ip(db_session)
    cycle = {"duration_s": 12.5, "interval_s": 300, "heavy_backlog": 3,
             "subnets": [{"cidr": "198.51.100.0/24", "hosts": 254, "alive": 20, "duration_s": 12.1,
                          "truncated": False}]}
    r = await client.post("/api/v1/scan-agents/report", headers={"X-Agent-Key": raw},
                          json={"results": [], "cycle": cycle})
    assert r.status_code == 200, r.text
    await db_session.refresh(agent)
    assert agent.last_cycle["duration_s"] == 12.5
    assert agent.last_cycle["subnets"][0]["alive"] == 20
    assert agent.last_cycle["at"]                      # 後端蓋上收到的時間


def test_heavy_results_survive_a_server_restart(monkeypatch) -> None:
    """正式環境部署時重啟後端，那幾秒送出的背景結果被 502 擋掉就丟了（要等下一個週期才重跑）。"""
    mod = _agent_module()
    calls: list[int] = []
    delivered: list[dict] = []

    def flaky_req(method, path, body=None, extra_headers=None):
        calls.append(1)
        if len(calls) <= 2:
            raise OSError("HTTP Error 502: Bad Gateway")
        delivered.append(body)
        return {"updated": 1}

    monkeypatch.setattr(mod, "_req", flaky_req)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(mod, "_heavy_names",
                        lambda ip, probes: {"ip": ip, "alive": True, "liveness": False, "probes_run": ["rdns"]})
    q = mod._HeavyQueue()
    q.submit("s1", "198.51.100.1", ["rdns"])
    mod._heavy_drain(q, batch=10)
    assert [x["ip"] for b in delivered for x in b["results"]] == ["198.51.100.1"]


# ─────────────────── 大子網路：分段輪替，不再永遠只掃前段 ───────────────────

def test_large_subnet_is_scanned_in_rotating_chunks(monkeypatch) -> None:
    mod = _agent_module()
    monkeypatch.setattr(mod, "MAX_HOSTS", 100)
    first, total, info = mod._subnet_chunk("s1", "198.18.0.0/24")
    second, _, info2 = mod._subnet_chunk("s1", "198.18.0.0/24")
    third, _, info3 = mod._subnet_chunk("s1", "198.18.0.0/24")
    fourth, _, _ = mod._subnet_chunk("s1", "198.18.0.0/24")
    assert total == 254
    assert (first[0], first[-1], len(first)) == ("198.18.0.1", "198.18.0.100", 100)
    assert (second[0], len(second)) == ("198.18.0.101", 100)
    assert (third[0], third[-1], len(third)) == ("198.18.0.201", "198.18.0.254", 54)
    assert fourth[0] == "198.18.0.1"                       # 掃完一遍從頭開始
    assert (info["chunk"], info["rounds"]) == (1, 3)
    assert (info2["chunk"], info3["chunk"]) == (2, 3)


def test_a_subnet_that_fits_is_scanned_whole_every_cycle(monkeypatch) -> None:
    mod = _agent_module()
    hosts, total, info = mod._subnet_chunk("s2", "198.51.100.0/24")
    assert len(hosts) == total == 254
    assert info == {"chunk": 1, "rounds": 1}


def test_single_cycle_limit_is_4096_by_default() -> None:
    assert _agent_module().MAX_HOSTS == 4096
