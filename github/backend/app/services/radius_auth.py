"""Radius 認證（PAP）。

Phase 1：用 pyrad 同步 client，包進 thread executor。

OWASP A04：shared secret 從 SecretStr 取；不接受明文設定。
"""

from __future__ import annotations

import asyncio

from app.core.config import get_settings


class RadiusAuthError(Exception):
    pass


class RadiusNotConfigured(RadiusAuthError):
    pass


class RadiusInvalidCredentials(RadiusAuthError):
    pass


def _authenticate_sync(username: str, password: str) -> bool:
    import pyrad.packet as packet
    from pyrad.client import Client
    from pyrad.dictionary import Dictionary
    s = get_settings()

    if not s.radius_enabled or not s.radius_server or s.radius_secret is None:
        raise RadiusNotConfigured("Radius is not configured")

    # 內建最小字典：User-Name + User-Password + NAS-Identifier
    # （pyrad 一般會帶完整 dict 檔；為避免外部檔案依賴，這裡用 in-memory）
    dict_data = """
ATTRIBUTE	User-Name		1	string
ATTRIBUTE	User-Password		2	string
ATTRIBUTE	NAS-IP-Address		4	ipaddr
ATTRIBUTE	NAS-Identifier		32	string
"""
    import io
    rad_dict = Dictionary(io.StringIO(dict_data))

    client = Client(
        server=s.radius_server,
        authport=s.radius_port,
        secret=s.radius_secret.get_secret_value().encode("utf-8"),
        dict=rad_dict,
    )
    client.timeout = int(s.radius_timeout)

    req = client.CreateAuthPacket(code=packet.AccessRequest, User_Name=username)
    req["NAS-Identifier"] = s.radius_nas_identifier
    req["User-Password"] = req.PwCrypt(password)

    try:
        reply = client.SendPacket(req)
    except (OSError, Exception) as exc:
        raise RadiusAuthError(f"Radius transport: {exc}") from exc

    if reply.code == packet.AccessAccept:
        return True
    if reply.code == packet.AccessReject:
        raise RadiusInvalidCredentials("Radius rejected credentials")
    raise RadiusAuthError(f"unexpected Radius reply code: {reply.code}")


async def authenticate(username: str, password: str) -> bool:
    settings = get_settings()
    if not settings.radius_enabled:
        raise RadiusNotConfigured("Radius is disabled")
    return await asyncio.to_thread(_authenticate_sync, username, password)
