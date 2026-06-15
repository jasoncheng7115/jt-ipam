"""回歸測試：UCS 以外的 DNS adapter 連線失敗時，必須拋 DNSAdapterError
（而非漏出 winrm/dnspython/json 的原始例外讓 /dns/servers/{id}/test 變成無訊息 500）。
"""

from __future__ import annotations

import pytest
from app.services.dns.base import DNSAdapterError

HOST = "192.0.2.10"  # TEST-NET-1，非 loopback/private，過 SSRF 檢查


# ───────────────────────── Windows DNS (WinRM) ─────────────────────────

def test_windows_run_ps_wraps_connection_error():
    from app.services.dns.windows_dns import WindowsDNSAdapter
    a = WindowsDNSAdapter(host=HOST, username="u", password="p")

    def boom():
        raise ConnectionError("connection timed out")

    a._session = boom  # 模擬 winrm 連不上
    with pytest.raises(DNSAdapterError):
        a._run_ps("Get-DnsServer")


# ───────────────────────── BIND 9 (dnspython) ─────────────────────────

async def test_bind9_healthcheck_wraps_oserror(monkeypatch):
    from app.services.dns import bind9
    a = bind9.Bind9Adapter(
        server_address=HOST, tsig_keyname="", tsig_secret=None, zones=["example.com"],
    )

    def refused(*args, **kwargs):
        raise ConnectionRefusedError("connection refused")  # OSError，非 DNSException

    monkeypatch.setattr(bind9.dns.query, "udp", refused)
    with pytest.raises(DNSAdapterError):
        await a.healthcheck()


# ───────────────────────── PowerDNS (HTTP API) ─────────────────────────

async def test_powerdns_healthcheck_wraps_non_json(monkeypatch):
    from app.services.dns import powerdns

    class _Resp:
        status_code = 200

        def json(self):
            raise ValueError("not json")  # 認證失敗回 200 + HTML 登入頁

    async def fake_safe_request(method, url, **kw):
        return _Resp()

    monkeypatch.setattr(powerdns, "safe_request", fake_safe_request)
    a = powerdns.PowerDNSAdapter(api_url="https://pdns.example.com", api_key="k")
    with pytest.raises(DNSAdapterError):
        await a.healthcheck()


# ───────────────────────── OPNsense Unbound (HTTP API) ─────────────────────────

async def test_unbound_get_wraps_non_json(monkeypatch):
    from app.services.dns import unbound_opnsense

    class _Resp:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    async def fake_safe_request(method, url, **kw):
        return _Resp()

    monkeypatch.setattr(unbound_opnsense, "safe_request", fake_safe_request)
    a = unbound_opnsense.UnboundOPNsenseAdapter(
        api_url="https://opnsense.example.com", api_key="k", api_secret="s",
    )
    with pytest.raises(DNSAdapterError):
        await a.healthcheck()
