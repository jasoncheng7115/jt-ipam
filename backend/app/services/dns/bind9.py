"""BIND 9 adapter — AXFR (zone transfer) 讀 + nsupdate（TSIG）寫。

依賴 dnspython。對外 IP/host 必須過 SSRF 白名單；TSIG 金鑰即時解密、
不在 instance 上常駐。

OWASP 對應：
- A02：tsig_secret 從 SecretStr 解出，僅在呼叫期間在記憶體中
- A03：zone name + record name 透過 dns.name.from_text 嚴格解析
- A10：server_address 透過手動 ipaddress 檢查（DNS 是 UDP/TCP，無法走 safe_http）
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket

import dns.message
import dns.name
import dns.query
import dns.rcode
import dns.rdataclass
import dns.rdatatype
import dns.tsig
import dns.tsigkeyring
import dns.update
import dns.zone
from dns.exception import DNSException

from app.core.config import get_settings
from app.core.safe_http import _BLOCKED_CIDRS, _PRIVATE_CIDRS, _ip_in
from app.services.dns.base import DNSAdapter, DNSAdapterError, DNSRecordOp, DNSZoneInfo


def _check_address_safe(host: str) -> None:
    """A10：BIND 9 server 不能是 metadata IP / loopback；私網需明確允許。"""
    settings = get_settings()
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, 53, proto=socket.IPPROTO_UDP)
        except socket.gaierror as exc:
            raise DNSAdapterError(f"DNS resolution failed for {host}") from exc
        addrs = [ipaddress.ip_address(info[4][0]) for info in infos]
    for ip in addrs:
        if _ip_in(ip, _BLOCKED_CIDRS):
            raise DNSAdapterError(f"Blocked IP for SSRF: {ip}")
        if _ip_in(ip, _PRIVATE_CIDRS) and not settings.outbound_allow_private:
            raise DNSAdapterError(
                f"Private IP {ip} not allowed (set OUTBOUND_ALLOW_PRIVATE=true if intended)"
            )


_TSIG_ALGOS = {
    "hmac-sha256": dns.tsig.HMAC_SHA256,
    "hmac-sha512": dns.tsig.HMAC_SHA512,
    "hmac-sha1": dns.tsig.HMAC_SHA1,
    "hmac-md5": dns.tsig.HMAC_MD5,
}


class Bind9Adapter(DNSAdapter):
    type = "bind9"

    def __init__(
        self,
        *,
        server_address: str,
        tsig_keyname: str,
        tsig_secret: str | None,
        tsig_algorithm: str = "hmac-sha256",
        zones: list[str] | None = None,
        port: int = 53,
        timeout: float = 10.0,
    ) -> None:
        if not server_address:
            raise DNSAdapterError("BIND 9: server_address is required")
        _check_address_safe(server_address)
        self.server_address = server_address
        self.port = port
        self.tsig_keyname = tsig_keyname
        self.tsig_secret = tsig_secret
        self.tsig_algorithm = tsig_algorithm
        self.zones = zones or []
        self.timeout = timeout

    def _keyring(self):  # type: ignore[no-untyped-def]
        if not self.tsig_keyname or not self.tsig_secret:
            return None
        return dns.tsigkeyring.from_text({self.tsig_keyname: self.tsig_secret})

    def _algo(self):  # type: ignore[no-untyped-def]
        return _TSIG_ALGOS.get(self.tsig_algorithm.lower(), dns.tsig.HMAC_SHA256)

    async def healthcheck(self) -> dict[str, object]:
        # 嘗試對第一個 zone 的 SOA 查詢
        if not self.zones:
            return {"server": self.server_address, "note": "no zones configured"}
        zone_name = self.zones[0]

        def _go():  # type: ignore[no-untyped-def]
            q = dns.message.make_query(zone_name, dns.rdatatype.SOA)
            return dns.query.udp(q, self.server_address, port=self.port, timeout=self.timeout)

        try:
            resp = await asyncio.to_thread(_go)
        except DNSException as exc:
            raise DNSAdapterError(f"BIND 9 SOA query failed: {exc}") from exc
        return {"server": self.server_address, "rcode": dns.rcode.to_text(resp.rcode())}

    async def list_zones(self) -> list[DNSZoneInfo]:
        # BIND 沒有「列舉所有 zone」的標準協定（rndc dumpdb 過於侵入）；
        # 使用設定中明列的 zone 清單。
        out: list[DNSZoneInfo] = []
        for z in self.zones:
            kind = (
                "reverse"
                if z.endswith(".in-addr.arpa") or z.endswith(".ip6.arpa")
                else "forward"
            )
            out.append(DNSZoneInfo(name=z.rstrip("."), kind=kind))
        return out

    async def list_records(self, zone_name: str) -> list[DNSRecordOp]:
        keyring = self._keyring()

        def _xfr() -> list[DNSRecordOp]:
            try:
                z = dns.zone.from_xfr(
                    dns.query.xfr(
                        self.server_address,
                        zone_name,
                        keyring=keyring,
                        keyalgorithm=self._algo() if keyring else None,
                        port=self.port,
                        timeout=self.timeout,
                    )
                )
            except DNSException as exc:
                raise DNSAdapterError(f"AXFR {zone_name} failed: {exc}") from exc

            out: list[DNSRecordOp] = []
            zone_origin = dns.name.from_text(zone_name)
            for name, rdataset in z.iterate_rdatasets():
                rtype = dns.rdatatype.to_text(rdataset.rdtype)
                fqdn = name.derelativize(zone_origin).to_text(omit_final_dot=True)
                for rdata in rdataset:
                    out.append(
                        DNSRecordOp(
                            name=fqdn, type=rtype, value=rdata.to_text(),
                            ttl=int(rdataset.ttl),
                        )
                    )
            return out

        return await asyncio.to_thread(_xfr)

    def _build_update(self, zone_name: str):  # type: ignore[no-untyped-def]
        keyring = self._keyring()
        if keyring is None:
            raise DNSAdapterError(
                "BIND 9 write requires TSIG key (tsig_keyname + tsig_secret)"
            )
        return dns.update.Update(
            zone_name, keyring=keyring, keyalgorithm=self._algo(),
        )

    @staticmethod
    def _relname(fqdn: str, zone_name: str) -> str:
        zone = zone_name.rstrip(".")
        name = fqdn.rstrip(".")
        if name == zone:
            return "@"
        if name.endswith("." + zone):
            return name[: -(len(zone) + 1)]
        return name

    async def upsert_record(self, zone_name: str, op: DNSRecordOp) -> None:
        rel_name = self._relname(op.name, zone_name)

        def _go():  # type: ignore[no-untyped-def]
            upd = self._build_update(zone_name)
            upd.replace(rel_name, op.ttl, op.type, op.value)
            try:
                resp = dns.query.tcp(
                    upd, self.server_address, port=self.port, timeout=self.timeout,
                )
            except DNSException as exc:
                raise DNSAdapterError(f"nsupdate replace failed: {exc}") from exc
            if resp.rcode() != dns.rcode.NOERROR:
                raise DNSAdapterError(
                    f"nsupdate rcode={dns.rcode.to_text(resp.rcode())}"
                )

        await asyncio.to_thread(_go)

    async def delete_record(self, zone_name: str, op: DNSRecordOp) -> None:
        rel_name = self._relname(op.name, zone_name)

        def _go():  # type: ignore[no-untyped-def]
            upd = self._build_update(zone_name)
            upd.delete(rel_name, op.type, op.value)
            try:
                resp = dns.query.tcp(
                    upd, self.server_address, port=self.port, timeout=self.timeout,
                )
            except DNSException as exc:
                raise DNSAdapterError(f"nsupdate delete failed: {exc}") from exc
            if resp.rcode() != dns.rcode.NOERROR:
                raise DNSAdapterError(
                    f"nsupdate rcode={dns.rcode.to_text(resp.rcode())}"
                )

        await asyncio.to_thread(_go)
