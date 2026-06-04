"""Univention UCS DNS adapter — 直接打 UDM REST API（不碰 UCS 內的 BIND9）。

UDM REST API（HAL+JSON）文件：https://docs.software-univention.de/developer-reference/latest/en/udm/rest-api.html
  Base：https://<ucs>/univention/udm
  認證：HTTP Basic（建議用具 DNS 寫權的帳號）
  物件：
    GET {base}/dns/forward_zone/                列正解 zone（properties.zone）
    GET {base}/dns/reverse_zone/                列反解 zone（properties.subnet → in-addr.arpa）
    GET {base}/dns/host_record/?superordinate=<zoneDN>   A/AAAA（properties.name + properties.a[]）
    GET {base}/dns/alias/?superordinate=<zoneDN>         CNAME（properties.name + properties.cname）
    GET {base}/dns/ptr_record/?superordinate=<zoneDN>    PTR（properties.address + properties.ptr_record）

所有請求走 safe_http（A10 SSRF 守門）；密碼即時解密、不常駐。
"""

from __future__ import annotations

import base64

import httpx

from app.core.safe_http import UnsafeOutboundURL, safe_request
from app.services.dns.base import DNSAdapter, DNSAdapterError, DNSRecordOp, DNSZoneInfo


class UniventionUCSAdapter(DNSAdapter):
    type = "univention_ucs"

    def __init__(self, *, api_url: str, username: str, password: str, verify_tls: bool = True) -> None:
        # api_url 可給 https://ucs 或 https://ucs/univention/udm，統一正規化到 .../univention/udm
        base = api_url.rstrip("/")
        if not base.endswith("/univention/udm"):
            base = base + "/univention/udm"
        self.base = base
        self.username = username
        self.password = password
        self.verify_tls = verify_tls

    @property
    def _headers(self) -> dict[str, str]:
        token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode("ascii")
        return {"Accept": "application/json", "Authorization": f"Basic {token}"}

    async def _get(self, path: str, params: dict | None = None) -> dict:  # type: ignore[type-arg]
        url = f"{self.base}{path}"
        try:
            resp = await safe_request(
                "GET", url, headers=self._headers, params=params or {},
                timeout=20.0, verify=self.verify_tls,
            )
        except UnsafeOutboundURL as exc:
            raise DNSAdapterError(f"SSRF guard rejected URL: {exc}") from exc
        except httpx.HTTPError as exc:
            raise DNSAdapterError(f"transport: {exc.__class__.__name__}") from exc
        if resp.status_code == 401:
            raise DNSAdapterError("UCS UDM REST 401 — 帳號/密碼或權限不足")
        if resp.status_code != 200:
            raise DNSAdapterError(f"UCS UDM REST {resp.status_code}: {resp.text[:200]}")
        return resp.json()  # type: ignore[no-any-return]

    @staticmethod
    def _objects(data: dict) -> list[dict]:  # type: ignore[type-arg]
        emb = (data.get("_embedded") or {})
        objs = emb.get("udm:object") or emb.get("udm:objects") or []
        return objs if isinstance(objs, list) else [objs]

    async def healthcheck(self) -> dict[str, object]:
        data = await self._get("/dns/forward_zone/", {"limit": 1})
        return {"ok": True, "total": data.get("total")}

    async def list_zones(self) -> list[DNSZoneInfo]:
        out: list[DNSZoneInfo] = []
        fwd = await self._get("/dns/forward_zone/", {"limit": 10000})
        for o in self._objects(fwd):
            zone = (o.get("properties") or {}).get("zone")
            if zone:
                out.append(DNSZoneInfo(name=str(zone).rstrip("."), kind="forward"))
        try:
            rev = await self._get("/dns/reverse_zone/", {"limit": 10000})
            for o in self._objects(rev):
                subnet = (o.get("properties") or {}).get("subnet")
                if subnet:
                    # UDM subnet 例 "1.168.192" → 反轉成 in-addr.arpa
                    parts = str(subnet).split(".")
                    rev_name = ".".join(reversed(parts)) + ".in-addr.arpa"
                    out.append(DNSZoneInfo(name=rev_name, kind="reverse"))
        except DNSAdapterError:
            pass
        return out

    async def _zone_dn(self, zone_name: str) -> str | None:
        data = await self._get("/dns/forward_zone/", {"limit": 10000})
        for o in self._objects(data):
            if str((o.get("properties") or {}).get("zone", "")).rstrip(".") == zone_name.rstrip("."):
                return o.get("dn")
        return None

    async def list_records(self, zone_name: str) -> list[DNSRecordOp]:
        zone = zone_name.rstrip(".")
        dn = await self._zone_dn(zone)
        if not dn:
            return []
        out: list[DNSRecordOp] = []

        def _fqdn(label: str) -> str:
            label = (label or "").strip()
            if not label or label in ("@", zone):
                return zone
            return f"{label}.{zone}"

        # A / AAAA（host_record.a 同時含 v4/v6，用冒號判斷）
        hosts = await self._get("/dns/host_record/", {"superordinate": dn, "limit": 10000})
        for o in self._objects(hosts):
            p = o.get("properties") or {}
            name = _fqdn(str(p.get("name", "")))
            for ip in (p.get("a") or []):
                rtype = "AAAA" if ":" in str(ip) else "A"
                out.append(DNSRecordOp(name=name, type=rtype, value=str(ip)))
        # CNAME（alias）
        try:
            aliases = await self._get("/dns/alias/", {"superordinate": dn, "limit": 10000})
            for o in self._objects(aliases):
                p = o.get("properties") or {}
                out.append(DNSRecordOp(name=_fqdn(str(p.get("name", ""))),
                                       type="CNAME", value=str(p.get("cname", "")).rstrip(".")))
        except DNSAdapterError:
            pass
        return out

    # ── 寫入路徑（建立/刪除 host_record）──
    # 註：UCS 寫入需帳號具 DNS 寫權；不同 UCS 版本 properties 形狀可能略異，
    # 上線前請對實際 UCS 驗證一次。
    async def _modify(self, method: str, path: str, body: dict | None = None) -> dict:  # type: ignore[type-arg]
        url = f"{self.base}{path}"
        try:
            resp = await safe_request(
                method, url, headers={**self._headers, "Content-Type": "application/json"},
                json=body, timeout=30.0, verify=self.verify_tls,
            )
        except UnsafeOutboundURL as exc:
            raise DNSAdapterError(f"SSRF guard rejected URL: {exc}") from exc
        if resp.status_code not in (200, 201, 204):
            raise DNSAdapterError(f"UCS UDM {method} {resp.status_code}: {resp.text[:200]}")
        return resp.json() if resp.content else {}

    async def upsert_record(self, zone_name: str, op: DNSRecordOp) -> None:
        if op.type not in ("A", "AAAA"):
            raise DNSAdapterError(f"UCS adapter 目前只支援推送 A/AAAA（收到 {op.type}）")
        zone = zone_name.rstrip(".")
        dn = await self._zone_dn(zone)
        if not dn:
            raise DNSAdapterError(f"UCS 找不到 forward zone {zone}")
        label = op.name.rstrip(".")
        label = label[: -len(zone) - 1] if label.endswith("." + zone) else label
        if label == zone:
            label = "@"
        body = {"position": dn, "superordinate": dn, "properties": {"name": label, "a": [op.value]}}
        await self._modify("POST", "/dns/host_record/", body)

    async def delete_record(self, zone_name: str, op: DNSRecordOp) -> None:
        zone = zone_name.rstrip(".")
        dn = await self._zone_dn(zone)
        if not dn:
            return
        hosts = await self._get("/dns/host_record/", {"superordinate": dn, "limit": 10000})
        target_label = op.name.rstrip(".")
        target_label = target_label[: -len(zone) - 1] if target_label.endswith("." + zone) else target_label
        for o in self._objects(hosts):
            p = o.get("properties") or {}
            if str(p.get("name", "")) == target_label and op.value in (p.get("a") or []):
                self_link = (((o.get("_links") or {}).get("self") or {}) or {}).get("href")
                if self_link:
                    # self_link 是完整 URL；safe_request 會再過 SSRF 檢查
                    try:
                        await safe_request("DELETE", self_link, headers=self._headers,
                                           timeout=30.0, verify=self.verify_tls)
                    except UnsafeOutboundURL as exc:
                        raise DNSAdapterError(f"SSRF guard rejected URL: {exc}") from exc
                return
