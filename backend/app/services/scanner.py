"""Subnet 主動掃描（Phase 1：ICMP 同步觸發版）。

Phase 1 範圍：
- ICMP ping 每個 IPv4 host
- 並行受 semaphore 控制（避免 fork bomb）
- 結果寫回 ip_addresses.last_seen_scanner / effective_status
- IPv6 / SNMP / Nmap 留待 Phase 2 + Celery 排程

OWASP 對應：
- A03：subprocess.run([...], shell=False)；目標 IP 先過 ipaddress 模組驗證
- A04：concurrency 受限；單次掃描設總時間 cap
"""

from __future__ import annotations

import asyncio
import ipaddress
import shutil
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import IPAddress
from app.models.subnet import Subnet


class ScannerNotAvailable(RuntimeError):
    pass


_SCAN_CONCURRENCY = 32
_SCAN_PER_HOST_TIMEOUT = 1.5  # seconds
_SCAN_TOTAL_TIMEOUT = 600     # 10 min hard cap
_MAX_HOSTS_PER_SCAN = 65_536  # 防呆：拒絕掃 /16 以下（第二期 worker 改進）


def _ping_binary() -> str:
    path = shutil.which("ping")
    if not path:
        raise ScannerNotAvailable("ping(1) not found in PATH")
    return path


async def _ping_host(ip: str, *, sem: asyncio.Semaphore) -> bool:
    """ICMP ping 單一 host；回傳是否回應。"""
    async with sem:
        ping = _ping_binary()
        # `-c 1`: 一個封包；`-W <secs>`: 等待秒數（GNU/BusyBox）；`-n`: 不解析 hostname
        proc = await asyncio.create_subprocess_exec(
            ping, "-c", "1", "-W", "1", "-n", ip,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            rc = await asyncio.wait_for(proc.wait(), timeout=_SCAN_PER_HOST_TIMEOUT)
        except TimeoutError:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            return False
        return rc == 0


def _enumerate_targets(subnet: Subnet) -> list[str]:
    net = ipaddress.ip_network(str(subnet.cidr), strict=False)
    if isinstance(net, ipaddress.IPv6Network):
        # Phase 1 不掃 IPv6（範圍太大）
        return []
    if net.num_addresses > _MAX_HOSTS_PER_SCAN:
        raise ValueError(
            f"Subnet {subnet.cidr} has {net.num_addresses} hosts; exceeds scan cap "
            f"{_MAX_HOSTS_PER_SCAN}. Use a smaller subnet or wait for Phase 2 worker."
        )
    if net.prefixlen >= 31:
        return [str(h) for h in net]
    return [str(h) for h in net.hosts()]


async def scan_subnet_icmp(session: AsyncSession, subnet: Subnet) -> dict[str, int]:
    """執行 ICMP scan；更新 ip_addresses 表中對應 IP 的 last_seen_scanner / effective_status。

    回傳 {"hosts": N, "online": M, "offline": K}。

    若 subnet.scan_agent_id 有設定，理論上應 dispatch 到遠端 agent；
    Phase 1 agent 通訊協定還未實作，先在 log 中記錄並 fall back 到本機掃描。
    """
    if subnet.scan_agent_id is not None:
        import structlog
        structlog.get_logger("scanner").info(
            "scan_agent_assigned_falling_back_to_local",
            subnet_id=str(subnet.id),
            scan_agent_id=str(subnet.scan_agent_id),
        )

    targets = _enumerate_targets(subnet)
    if not targets:
        return {"hosts": 0, "online": 0, "offline": 0}

    sem = asyncio.Semaphore(_SCAN_CONCURRENCY)
    coros = [_ping_host(ip, sem=sem) for ip in targets]
    results: list[bool] = await asyncio.wait_for(
        asyncio.gather(*coros), timeout=_SCAN_TOTAL_TIMEOUT
    )

    online_set = {ip for ip, ok in zip(targets, results, strict=True) if ok}
    online = len(online_set)

    # 更新已存在的 IP 紀錄
    rows = (
        await session.execute(
            select(IPAddress).where(IPAddress.subnet_id == subnet.id)
        )
    ).scalars().all()
    now = datetime.now(UTC)
    for row in rows:
        host_ip = str(row.ip).split("/")[0]
        if host_ip in online_set:
            row.last_seen_scanner = now
            row.effective_status = "online"
        else:
            row.effective_status = "offline"

    await session.commit()
    return {"hosts": len(targets), "online": online, "offline": len(targets) - online}
