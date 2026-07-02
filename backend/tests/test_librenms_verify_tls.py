"""LibreNMS verify_tls 旗標要確實帶進 safe_request（關閉＝接受自簽/名稱不符）。"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import librenms as lib


class _Resp:
    status_code = 200

    def json(self):
        return {"status": "ok"}


async def test_verify_tls_flag_passthrough(monkeypatch):
    seen: dict[str, object] = {}

    async def fake_safe_request(method, url, *, headers=None, params=None, json=None,
                                content=None, timeout=30.0, verify=True, max_redirects=5):
        seen["verify"] = verify
        return _Resp()

    monkeypatch.setattr(lib, "safe_request", fake_safe_request)
    monkeypatch.setattr(lib, "_decrypt_token", lambda inst: "tok")

    await lib._api_get(SimpleNamespace(api_url="https://librenms:8443", verify_tls=False), "/api/v0/system")
    assert seen["verify"] is False

    await lib._api_get(SimpleNamespace(api_url="https://librenms:8443", verify_tls=True), "/api/v0/system")
    assert seen["verify"] is True
