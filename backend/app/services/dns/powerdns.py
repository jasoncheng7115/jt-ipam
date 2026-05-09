"""PowerDNS Authoritative HTTP API adapter（v4.x +）。

API 文件：https://doc.powerdns.com/authoritative/http-api/

所有對外請求一律走 safe_http；A02：API key 從加密欄位即時解密；A05：4xx
細節摘要回給呼叫端但不回明文 key。
"""

from __future__ import annotations

import httpx

from app.core.safe_http import UnsafeOutboundURL, safe_request
from app.services.dns.base import DNSAdapter, DNSAdapterError, DNSRecordOp, DNSZoneInfo


class PowerDNSAdapter(DNSAdapter):
    type = "powerdns"

    def __init__(self, *, api_url: str, api_key: str, server_id: str = "localhost") -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.server_id = server_id

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key, "Content-Type": "application/json"}

    async def healthcheck(self) -> dict[str, object]:
        url = f"{self.api_url}/api/v1/servers/{self.server_id}"
        try:
            resp = await safe_request("GET", url, headers=self._headers, timeout=8.0)
        except UnsafeOutboundURL as exc:
            raise DNSAdapterError(f"SSRF guard rejected URL: {exc}") from exc
        except httpx.HTTPError as exc:
            raise DNSAdapterError(f"transport: {exc.__class__.__name__}") from exc
        if resp.status_code != 200:
            raise DNSAdapterError(f"PowerDNS returned {resp.status_code}")
        return resp.json()

    async def list_zones(self) -> list[DNSZoneInfo]:
        url = f"{self.api_url}/api/v1/servers/{self.server_id}/zones"
        try:
            resp = await safe_request("GET", url, headers=self._headers, timeout=15.0)
        except UnsafeOutboundURL as exc:
            raise DNSAdapterError(f"SSRF guard rejected URL: {exc}") from exc
        if resp.status_code != 200:
            raise DNSAdapterError(f"list_zones {resp.status_code}: {resp.text[:200]}")
        out: list[DNSZoneInfo] = []
        for z in resp.json():
            zname = (z.get("name") or "").rstrip(".")
            kind = "reverse" if zname.endswith(".in-addr.arpa") or zname.endswith(".ip6.arpa") else "forward"
            out.append(DNSZoneInfo(name=zname, kind=kind))
        return out

    async def list_records(self, zone_name: str) -> list[DNSRecordOp]:
        zid = zone_name.rstrip(".") + "."
        url = f"{self.api_url}/api/v1/servers/{self.server_id}/zones/{zid}"
        try:
            resp = await safe_request("GET", url, headers=self._headers, timeout=30.0)
        except UnsafeOutboundURL as exc:
            raise DNSAdapterError(f"SSRF guard rejected URL: {exc}") from exc
        if resp.status_code != 200:
            raise DNSAdapterError(f"list_records {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        out: list[DNSRecordOp] = []
        for rrset in data.get("rrsets") or []:
            rname = (rrset.get("name") or "").rstrip(".")
            rtype = rrset.get("type")
            ttl = int(rrset.get("ttl") or 3600)
            for rec in rrset.get("records") or []:
                if rec.get("disabled"):
                    continue
                out.append(DNSRecordOp(
                    name=rname, type=rtype, value=rec.get("content", ""), ttl=ttl
                ))
        return out

    async def upsert_record(self, zone_name: str, op: DNSRecordOp) -> None:
        zid = zone_name.rstrip(".") + "."
        url = f"{self.api_url}/api/v1/servers/{self.server_id}/zones/{zid}"
        body = {
            "rrsets": [
                {
                    "name": op.name.rstrip(".") + ".",
                    "type": op.type,
                    "ttl": op.ttl,
                    "changetype": "REPLACE",
                    "records": [{"content": op.value, "disabled": False}],
                }
            ]
        }
        await self._patch(url, body)

    async def delete_record(self, zone_name: str, op: DNSRecordOp) -> None:
        zid = zone_name.rstrip(".") + "."
        url = f"{self.api_url}/api/v1/servers/{self.server_id}/zones/{zid}"
        body = {
            "rrsets": [
                {
                    "name": op.name.rstrip(".") + ".",
                    "type": op.type,
                    "changetype": "DELETE",
                }
            ]
        }
        await self._patch(url, body)

    async def _patch(self, url: str, body: dict) -> None:  # type: ignore[type-arg]
        try:
            resp = await safe_request(
                "PATCH", url, headers=self._headers, json=body, timeout=30.0
            )
        except UnsafeOutboundURL as exc:
            raise DNSAdapterError(f"SSRF guard rejected URL: {exc}") from exc
        if resp.status_code not in (200, 204):
            raise DNSAdapterError(f"PowerDNS PATCH {resp.status_code}: {resp.text[:200]}")
