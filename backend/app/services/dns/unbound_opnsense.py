"""OPNsense Unbound REST API adapter。

OPNsense Unbound DNS 透過 REST API 管理 host overrides；認證走 HTTP
Basic（API key 當 username、API secret 當 password）。

主要 endpoints：
  GET  /api/unbound/settings/get                      讀全部設定
  POST /api/unbound/settings/addHostOverride          新增單筆
  POST /api/unbound/settings/setHostOverride/{uuid}   修改
  POST /api/unbound/settings/delHostOverride/{uuid}   刪除
  POST /api/unbound/service/reconfigure               套用變更

OWASP A04：API key/secret 即時解密、不在 instance 上常駐
OWASP A06：所有對外請求一律走 safe_request
"""

from __future__ import annotations

import base64

import httpx

from app.core.safe_http import UnsafeOutboundURL, safe_request
from app.services.dns.base import DNSAdapter, DNSAdapterError, DNSRecordOp, DNSZoneInfo


class UnboundOPNsenseAdapter(DNSAdapter):
    type = "unbound_opnsense"

    def __init__(self, *, api_url: str, api_key: str, api_secret: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret

    @property
    def _auth_header(self) -> dict[str, str]:
        token = base64.b64encode(
            f"{self.api_key}:{self.api_secret}".encode("utf-8")
        ).decode("ascii")
        return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    async def _get(self, path: str) -> dict:  # type: ignore[type-arg]
        url = f"{self.api_url}{path}"
        try:
            resp = await safe_request("GET", url, headers=self._auth_header, timeout=15.0)
        except UnsafeOutboundURL as exc:
            raise DNSAdapterError(f"SSRF guard rejected URL: {exc}") from exc
        except httpx.HTTPError as exc:
            raise DNSAdapterError(f"transport: {exc.__class__.__name__}") from exc
        if resp.status_code != 200:
            raise DNSAdapterError(
                f"OPNsense Unbound GET {path}: {resp.status_code} {resp.text[:200]}"
            )
        return resp.json()

    async def _post(self, path: str, body: dict | None = None) -> dict:  # type: ignore[type-arg]
        url = f"{self.api_url}{path}"
        try:
            resp = await safe_request(
                "POST", url, headers=self._auth_header, json=body or {}, timeout=15.0,
            )
        except UnsafeOutboundURL as exc:
            raise DNSAdapterError(f"SSRF guard rejected URL: {exc}") from exc
        except httpx.HTTPError as exc:
            raise DNSAdapterError(f"transport: {exc.__class__.__name__}") from exc
        if resp.status_code not in (200, 201):
            raise DNSAdapterError(
                f"OPNsense Unbound POST {path}: {resp.status_code} {resp.text[:200]}"
            )
        return resp.json()

    async def healthcheck(self) -> dict[str, object]:
        data = await self._get("/api/unbound/service/status")
        return {"server": self.api_url, "status": data}

    async def list_zones(self) -> list[DNSZoneInfo]:
        # OPNsense Unbound 用 host overrides；把所有出現過的 domain 視作 forward zone
        data = await self._get("/api/unbound/settings/get")
        rows = data.get("unbound", {}).get("hosts", {}).get("host", {})
        domains: set[str] = set()
        if isinstance(rows, dict):
            for entry in rows.values():
                if isinstance(entry, dict):
                    domain = (entry.get("domain") or "").rstrip(".")
                    if domain:
                        domains.add(domain)
        return [DNSZoneInfo(name=d, kind="forward") for d in sorted(domains)]

    async def list_records(self, zone_name: str) -> list[DNSRecordOp]:
        data = await self._get("/api/unbound/settings/get")
        rows = data.get("unbound", {}).get("hosts", {}).get("host", {})
        out: list[DNSRecordOp] = []
        if isinstance(rows, dict):
            for entry in rows.values():
                if not isinstance(entry, dict):
                    continue
                domain = (entry.get("domain") or "").rstrip(".")
                if domain != zone_name.rstrip("."):
                    continue
                hostname = entry.get("hostname") or "@"
                fqdn = f"{hostname}.{domain}" if hostname not in ("", "@") else domain
                rtype = (entry.get("rr") or "A").upper()
                value = entry.get("server") or entry.get("mx") or ""
                if not value:
                    continue
                out.append(DNSRecordOp(name=fqdn, type=rtype, value=str(value), ttl=3600))
        return out

    async def _find_existing_uuid(self, zone_name: str, op: DNSRecordOp) -> str | None:
        data = await self._get("/api/unbound/settings/get")
        rows = data.get("unbound", {}).get("hosts", {}).get("host", {})
        if not isinstance(rows, dict):
            return None
        target_host = op.name.rstrip(".")
        target_domain = zone_name.rstrip(".")
        if target_host.endswith("." + target_domain):
            target_host = target_host[: -(len(target_domain) + 1)]
        for uid, entry in rows.items():
            if not isinstance(entry, dict):
                continue
            domain = (entry.get("domain") or "").rstrip(".")
            hostname = entry.get("hostname") or "@"
            rtype = (entry.get("rr") or "A").upper()
            if domain == target_domain and hostname == target_host and rtype == op.type:
                return uid
        return None

    async def _reconfigure(self) -> None:
        await self._post("/api/unbound/service/reconfigure")

    async def upsert_record(self, zone_name: str, op: DNSRecordOp) -> None:
        if op.type not in ("A", "AAAA", "MX", "CNAME"):
            raise DNSAdapterError(
                f"OPNsense Unbound host override does not support {op.type}"
            )
        target_domain = zone_name.rstrip(".")
        target_host = op.name.rstrip(".")
        if target_host.endswith("." + target_domain):
            target_host = target_host[: -(len(target_domain) + 1)]
        body = {
            "host": {
                "enabled": "1",
                "hostname": target_host,
                "domain": target_domain,
                "rr": op.type,
                "server": op.value,
                "description": "managed by jt-ipam",
            }
        }
        existing = await self._find_existing_uuid(zone_name, op)
        if existing is None:
            await self._post("/api/unbound/settings/addHostOverride", body)
        else:
            await self._post(f"/api/unbound/settings/setHostOverride/{existing}", body)
        await self._reconfigure()

    async def delete_record(self, zone_name: str, op: DNSRecordOp) -> None:
        existing = await self._find_existing_uuid(zone_name, op)
        if existing is None:
            return
        await self._post(f"/api/unbound/settings/delHostOverride/{existing}")
        await self._reconfigure()
