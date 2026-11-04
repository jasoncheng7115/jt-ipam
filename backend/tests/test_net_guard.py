"""非 HTTP 的對外連線也要經過位址檢查（core/net_guard；2026-10-09 合規核對）。

以前 SSRF 防護只涵蓋 HTTP（safe_http）。DNS、WinRM、憑證 SFTP 來源各自有一份檢查，LDAP、SMTP、
RADIUS、syslog 轉送、跳板、phpIPAM 搬移與所有主控台都沒有 —— 尤其主控台：IP 記錄是有寫入權限
的人可以自己建的，建一筆 127.0.0.1 或 169.254.169.254 再開主控台，就能把 jt-ipam 當跳板。
"""
from __future__ import annotations

import ast
import ipaddress
import pathlib

import pytest

from app.core import net_guard
from app.core.safe_http import UnsafeOutboundURL

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


@pytest.mark.parametrize(("addr", "policy", "blocked"), [
    ("169.254.169.254", "integration", True),
    ("169.254.169.254", "console", True),
    ("::ffff:169.254.169.254", "console", True),       # IPv4 對映的 IPv6 不能繞過
    ("100.100.100.200", "integration", True),
    ("fd00:ec2::254", "integration", True),
    ("224.0.0.1", "integration", True),
    ("0.0.0.0", "console", True),
    ("127.0.0.1", "console", True),
    ("::1", "console", True),
    ("127.0.0.1", "integration", False),               # 本機郵件轉送等常見設定
    ("192.0.2.10", "console", False),
    ("10.1.2.3", "console", False),
    ("100.64.1.1", "console", False),                   # CGNAT（Tailscale 等）照常可以開主控台
])
def test_policies(addr, policy, blocked) -> None:
    problem = net_guard.address_problem(ipaddress.ip_address(addr), policy)
    assert (problem is not None) is blocked, (addr, policy, problem)


def test_strict_is_the_http_rule() -> None:
    with pytest.raises(net_guard.BlockedTarget) as ei:
        net_guard.check_target("127.0.0.1", policy="strict")
    assert ei.value.reason == "blocked"


def test_host_only_parsing() -> None:
    assert net_guard.host_only("ldaps://ldap.example.com:636") == "ldap.example.com"
    assert net_guard.host_only("[2001:db8::1]:22") == "2001:db8::1"
    assert net_guard.host_only("2001:db8::1") == "2001:db8::1"
    assert net_guard.host_only("mail.example.com:25") == "mail.example.com"


def test_names_are_resolved_and_every_address_checked(monkeypatch) -> None:
    import socket

    def fake(host, *_a, **_k):   # noqa: ANN001, ANN002, ANN003
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.5", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with pytest.raises(UnsafeOutboundURL):
        net_guard.check_target("evil.example", 389)


# ── 守門：所有非 HTTP 的連線呼叫都要經過檢查 ──
_CONNECT_CALLS = {
    "asyncssh.connect", "asyncssh.get_server_host_key", "smtplib.SMTP", "smtplib.SMTP_SSL",
    "winrm.Session", "socket.create_connection", "asyncio.open_connection", "websockets.connect",
    "pymysql.connect", "dns.query.udp", "dns.query.tcp", "dns.query.xfr",
}
_BARE_CALLS = {"Server", "Client"}       # ldap3.Server、pyrad.client.Client
#: 模組裡出現這些就算有檢查（同一個模組的 helper）
_EVIDENCE = ("net_guard", "check_addrs(", "_check_address_safe(", "_check_host_safe(", "guard_ssh_target(",
             "console_target_blocked(", "_guard(")
EXEMPT: dict[str, str] = {
    "services/guacd.py": "guacd 是本機 127.0.0.1:4822 的服務；主控台的目標在交給 guacd 之前已由 console_target_blocked 檢查",
    "services/netdiag.py": "工具頁的網路診斷有自己的出站規則（tests/test_netdiag_http_guard.py、test_safe_http_guard.py）",
    "services/nettools.py": "工具頁的 DNS 查詢，同上",
    "services/rdp_freerdp.py": "主控台目標在 WebSocket 開始時已由 console_target_blocked 檢查",
    "services/server_tls.py": "只連本機 127.0.0.1 的同一個埠（寫死），讀 jt-ipam 自己出示的憑證給代理安裝指令釘選；網址只用來取主機名稱當 SNI",
    "services/sftp.py": "SFTP 主控台的目標在 WebSocket 開始時已由 console_target_blocked 檢查",
}


def _call_name(node: ast.Call) -> str:
    parts = []
    f = node.func
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    return ".".join(reversed(parts))


def test_every_non_http_connection_is_guarded() -> None:
    offenders, found = [], 0
    for p in APP.rglob("*.py"):
        rel = str(p.relative_to(APP))
        src = p.read_text(encoding="utf-8")
        tree = ast.parse(src)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and (_call_name(n) in _CONNECT_CALLS or _call_name(n) in _BARE_CALLS)]
        if rel.endswith("services/ldap_auth.py") or rel.endswith("services/radius_auth.py"):
            calls = calls or [None]       # Server()／Client() 名字太常見，這兩個模組明確要求
        elif any(_call_name(c) in _BARE_CALLS for c in calls if c is not None):
            calls = [c for c in calls if c is not None and _call_name(c) not in _BARE_CALLS]
        if not calls:
            continue
        found += 1
        if rel in EXEMPT:
            continue
        if not any(e in src for e in _EVIDENCE):
            offenders.append(rel)
    assert found >= 12, f"只找到 {found} 個模組，偵測方式可能失效了"
    assert not offenders, f"這些模組對外連線卻沒有經過位址檢查（core/net_guard）：{offenders}"


def test_console_handlers_check_their_target() -> None:
    import importlib
    import inspect
    for name in ("ssh_console", "sftp_console", "rdp_console", "vnc_console", "bmc_console"):
        src = inspect.getsource(importlib.import_module(f"app.api.v1.endpoints.{name}"))
        assert "await require_console_target(" in src, f"{name} 換發 ticket 時沒檢查目標位址"
        assert "await console_target_blocked(" in src, f"{name} 的 WebSocket 沒檢查目標位址"


@pytest.mark.anyio
async def test_console_ticket_for_loopback_target_is_refused(db_session, client, auth_headers) -> None:
    from app.models.address import IPAddress
    from app.models.section import Section
    from app.models.subnet import Subnet
    sec = Section(name="sec-ng")
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="127.0.0.0/8")
    db_session.add(sub)
    await db_session.flush()
    ip = IPAddress(subnet_id=sub.id, ip="127.0.0.1", state="active", ssh_enabled=True)
    db_session.add(ip)
    await db_session.commit()
    r = await client.post(f"/api/v1/addresses/{ip.id}/ssh/ticket", headers=auth_headers)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "console_target_blocked"
