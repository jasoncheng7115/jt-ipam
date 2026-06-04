"""依 DNSServer.type 建出對應的 adapter。"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_secret
from app.models.dns import DNSServer
from app.models.encrypted_secret import EncryptedSecret
from app.services.dns.base import DNSAdapter, DNSAdapterError


def _aad(server_id: str, field: str) -> bytes:
    return f"dns_server:{server_id}:{field}".encode()


async def _load_secret(
    session: AsyncSession, server: DNSServer, field: str
) -> str | None:
    row = (
        await session.execute(
            select(EncryptedSecret).where(
                EncryptedSecret.object_type == "dns_server",
                EncryptedSecret.object_id == server.id,
                EncryptedSecret.field == field,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return decrypt_secret(row.ciphertext, row.nonce, aad=_aad(str(server.id), field)).decode("utf-8")


async def get_adapter(session: AsyncSession, server: DNSServer) -> DNSAdapter:
    """主入口：給定 DNSServer 物件，回對應 adapter（密鑰已解密）。"""
    extra = json.loads(server.extra_config) if server.extra_config else {}

    if server.type == "powerdns":
        from app.services.dns.powerdns import PowerDNSAdapter
        api_key = await _load_secret(session, server, "api_key")
        if not server.api_url or not api_key:
            raise DNSAdapterError("PowerDNS requires api_url + api_key")
        return PowerDNSAdapter(
            api_url=server.api_url,
            api_key=api_key,
            server_id=str(extra.get("server_id", "localhost")),
        )

    if server.type == "bind9":
        from app.services.dns.bind9 import Bind9Adapter
        tsig_key = await _load_secret(session, server, "tsig_key")
        return Bind9Adapter(
            server_address=server.server_address or "",
            tsig_keyname=str(extra.get("tsig_keyname", "")),
            tsig_secret=tsig_key,
            tsig_algorithm=str(extra.get("tsig_algorithm", "hmac-sha256")),
            zones=list(extra.get("zones", [])),
        )

    if server.type == "unbound_opnsense":
        from app.services.dns.unbound_opnsense import UnboundOPNsenseAdapter
        api_key = await _load_secret(session, server, "api_key")
        api_secret = await _load_secret(session, server, "api_secret")
        if not server.api_url or not api_key or not api_secret:
            raise DNSAdapterError(
                "OPNsense Unbound requires api_url + api_key + api_secret"
            )
        return UnboundOPNsenseAdapter(
            api_url=server.api_url,
            api_key=api_key,
            api_secret=api_secret,
        )

    if server.type == "windows_dns":
        from app.services.dns.windows_dns import WindowsDNSAdapter
        password = await _load_secret(session, server, "password")
        return WindowsDNSAdapter(
            host=server.server_address or "",
            username=str(extra.get("username", "")),
            password=password or "",
            port=int(extra.get("winrm_port", 5986)),
            use_ssl=bool(extra.get("use_ssl", True)),
        )

    if server.type == "univention_ucs":
        from app.services.dns.ucs import UniventionUCSAdapter
        password = await _load_secret(session, server, "password")
        if not server.api_url or not password:
            raise DNSAdapterError("Univention UCS requires api_url + username + password")
        return UniventionUCSAdapter(
            api_url=server.api_url,
            username=str(extra.get("username", "")),
            password=password,
            verify_tls=bool(extra.get("verify_tls", True)),
        )

    raise DNSAdapterError(f"Unknown DNS server type: {server.type}")
