"""DNS 雙向同步 orchestration。

phpIPAM 缺點：DNS 整合粗糙，只 push 不 pull、無不一致報表、錯了不知道。
jt-ipam 設計：
  - push_for_ip(ip)：建立/更新 IP 時呼叫；依 subnet.auto_dns + zone 推送
  - pull_server(server)：定期把 server 上的 zone 全部抓回，比對標出
    consistency_state：consistent / dns_only / ipam_only / mismatch
  - 反解 zone 由 CIDR 自動計算（IPv4 in-addr.arpa、IPv6 ip6.arpa）
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import IPAddress
from app.models.dns import DNSRecord, DNSServer, DNSZone
from app.models.subnet import Subnet
from app.services.dns import DNSAdapterError, get_adapter
from app.services.dns.base import DNSRecordOp
from app.services.hostname import apply_observation

# ─────────────────── 反解 zone 計算 ───────────────────


def reverse_zone_for_cidr(cidr: str) -> str | None:
    """根據 CIDR 推算對應的 in-addr.arpa / ip6.arpa zone 名。

    IPv4：/8/16/24/32 對應的反解 zone；非整 octet boundary 回 None
    （phpIPAM 也避免處理 RFC 2317 classless delegation）
    IPv6：依 nibble boundary（每 4 bits）；/4 boundary
    """
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return None

    if isinstance(net, ipaddress.IPv4Network):
        if net.prefixlen not in (8, 16, 24, 32):
            return None
        octets = str(net.network_address).split(".")
        keep = net.prefixlen // 8
        return ".".join(reversed(octets[:keep])) + ".in-addr.arpa"

    if isinstance(net, ipaddress.IPv6Network):
        if net.prefixlen % 4 != 0:
            return None
        # 取 prefix 的 nibble，反轉
        full = net.network_address.exploded.replace(":", "")
        keep = net.prefixlen // 4
        nibbles = list(full[:keep])
        return ".".join(reversed(nibbles)) + ".ip6.arpa"

    return None


def ptr_name_for_ip(ip: str) -> str:
    return ipaddress.ip_address(ip).reverse_pointer


# ─────────────────── IPAM → DNS push ───────────────────


async def push_ip(
    session: AsyncSession,
    *,
    ip_address: IPAddress,
    forward_zone_suffix: str | None = None,
) -> dict[str, list[str]]:
    """為單一 IP 推送 A/AAAA + PTR 到所有相關 DNS server。

    需要 IPAddress.hostname 才能建 forward；否則只 push PTR。
    forward_zone_suffix：例 "example.com"；hostname 後接此 suffix 形成 FQDN。
    """
    summary: dict[str, list[str]] = {"pushed": [], "errored": []}
    if not ip_address.subnet_id:
        return summary
    subnet = await session.get(Subnet, ip_address.subnet_id)
    if subnet is None or not subnet.auto_dns:
        return summary

    ip_text = str(ip_address.ip).split("/")[0]
    addr = ipaddress.ip_address(ip_text)
    a_or_aaaa = "AAAA" if addr.version == 6 else "A"

    # 找所有 enabled DNS servers（forward zone 必須關聯這個 subnet 或匹配 suffix）
    rev_zone = reverse_zone_for_cidr(str(subnet.cidr))
    forward_zone_name = forward_zone_suffix
    fqdn = f"{ip_address.hostname}.{forward_zone_suffix}" if (
        ip_address.hostname and forward_zone_suffix
    ) else None

    zones = (
        await session.execute(
            select(DNSZone, DNSServer)
            .join(DNSServer, DNSServer.id == DNSZone.server_id)
            .where(DNSServer.enabled.is_(True), DNSZone.managed.is_(True))
        )
    ).all()

    for zone, server in zones:
        try:
            adapter = await get_adapter(session, server)
        except DNSAdapterError as exc:
            summary["errored"].append(f"{server.name}: {exc}")
            continue

        try:
            # forward push：subnet 與 zone 透過 associated_subnet_ids 關聯，或
            # 由呼叫端明確指定 forward_zone_suffix 對應的 zone
            if (
                fqdn
                and zone.type == "forward"
                and (subnet.id in (zone.associated_subnet_ids or [])
                     or zone.name == forward_zone_name)
            ):
                op = DNSRecordOp(name=fqdn, type=a_or_aaaa, value=ip_text, ttl=300)
                await adapter.upsert_record(zone.name, op)
                await _record_local(
                    session, zone=zone, op=op,
                    ipam_address_id=ip_address.id, source="from_ipam",
                )
                summary["pushed"].append(f"{server.name}/{zone.name}: {fqdn} {a_or_aaaa}")

            # reverse push
            if zone.type == "reverse" and rev_zone and zone.name == rev_zone:
                ptr_value = fqdn or (ip_address.hostname or ip_text) + "."
                ptr_op = DNSRecordOp(
                    name=ptr_name_for_ip(ip_text), type="PTR", value=ptr_value, ttl=300,
                )
                await adapter.upsert_record(zone.name, ptr_op)
                await _record_local(
                    session, zone=zone, op=ptr_op,
                    ipam_address_id=ip_address.id, source="from_ipam",
                )
                summary["pushed"].append(f"{server.name}/{zone.name}: PTR")
        except DNSAdapterError as exc:
            summary["errored"].append(f"{server.name}/{zone.name}: {exc}")
        finally:
            await adapter.close()

    return summary


# ─────────────────── DNS → IPAM pull + 不一致比對 ───────────────────


async def pull_server(session: AsyncSession, server: DNSServer) -> dict[str, int]:
    """從 server 端 list_zones + list_records，更新本地 dns_records 表並標
    consistency_state。

    回傳 {pulled_zones, pulled_records, mismatches, dns_only, ipam_only}。
    """
    summary = {
        "pulled_zones": 0,
        "pulled_records": 0,
        "mismatches": 0,
        "dns_only": 0,
        "ipam_only": 0,
    }
    try:
        adapter = await get_adapter(session, server)
    except DNSAdapterError as exc:
        server.last_error = str(exc)
        await session.commit()
        return summary

    try:
        zones_remote = await adapter.list_zones()
    except DNSAdapterError as exc:
        server.last_error = str(exc)
        await session.commit()
        await adapter.close()
        return summary

    try:
        # 收集每個 IP 從 DNS 看到的所有正解名稱，最後只套用一個「穩定」的，
        # 避免同一 IP 有多筆 A 記錄（如 meet3 與 meet3-turn）時每次 sync 挑到不同
        # 名稱 → hostname 反覆跳動、洗版異動記錄。
        dns_ip_names: dict[str, set[str]] = {}
        for zinfo in zones_remote:
            summary["pulled_zones"] += 1
            zone = (
                await session.execute(
                    select(DNSZone).where(
                        DNSZone.server_id == server.id, DNSZone.name == zinfo.name
                    )
                )
            ).scalar_one_or_none()
            if zone is None:
                zone = DNSZone(
                    server_id=server.id,
                    name=zinfo.name,
                    type=zinfo.kind,
                )
                session.add(zone)
                await session.flush()

            try:
                records = await adapter.list_records(zinfo.name)
            except DNSAdapterError as exc:
                server.last_error = f"list_records {zinfo.name}: {exc}"
                continue

            # 與本地比對
            existing = list(
                (
                    await session.execute(
                        select(DNSRecord).where(DNSRecord.zone_id == zone.id)
                    )
                ).scalars().all()
            )
            local_keys = {(r.name, r.type, r.value): r for r in existing}
            seen: set[tuple[str, str, str]] = set()

            for op in records:
                key = (op.name, op.type, op.value)
                seen.add(key)
                summary["pulled_records"] += 1

                # 正解 A/AAAA → 先收集名稱，迴圈跑完再挑穩定的一個套用（見下方）
                if zone.type == "forward" and op.type in ("A", "AAAA") and op.value and op.name:
                    dns_ip_names.setdefault(op.value, set()).add(op.name)

                rec = local_keys.get(key)
                if rec is None:
                    # 全新從 DNS 拉回的紀錄
                    rec = DNSRecord(
                        zone_id=zone.id,
                        name=op.name, type=op.type, value=op.value, ttl=op.ttl,
                        source="from_dns_pulled",
                        consistency_state="dns_only",
                        last_seen_at=datetime.now(UTC),
                    )
                    session.add(rec)
                    summary["dns_only"] += 1
                else:
                    rec.ttl = op.ttl
                    rec.last_seen_at = datetime.now(UTC)
                    if rec.source == "from_ipam":
                        rec.consistency_state = "consistent"
                    elif rec.consistency_state == "ipam_only":
                        rec.consistency_state = "consistent"

            # 標出 ipam_only：本地有 source=from_ipam 但 DNS 看不到
            for key, rec in local_keys.items():
                if key not in seen and rec.source == "from_ipam":
                    rec.consistency_state = "ipam_only"
                    summary["ipam_only"] += 1

            zone.last_sync_at = datetime.now(UTC)

        # 每個 IP 只套用一個穩定的 DNS 名稱（字母序最小），避免多筆 A 記錄造成跳動
        for ip_val, names in dns_ip_names.items():
            ipa = (await session.execute(
                select(IPAddress).where(IPAddress.ip == ip_val)
            )).scalars().first()
            if ipa is not None and names:
                await apply_observation(session, ip=ipa, source="dns", hostname=sorted(names)[0])
                summary["hostname_obs"] = summary.get("hostname_obs", 0) + 1

        server.last_sync_at = datetime.now(UTC)
        server.last_error = None
        await session.commit()
    finally:
        await adapter.close()

    return summary


async def _record_local(
    session: AsyncSession,
    *,
    zone: DNSZone,
    op: DNSRecordOp,
    ipam_address_id: uuid.UUID | None,
    source: str,
) -> None:
    rec = (
        await session.execute(
            select(DNSRecord).where(
                DNSRecord.zone_id == zone.id,
                DNSRecord.name == op.name,
                DNSRecord.type == op.type,
                DNSRecord.value == op.value,
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if rec is None:
        session.add(
            DNSRecord(
                zone_id=zone.id, name=op.name, type=op.type, value=op.value,
                ttl=op.ttl, source=source, consistency_state="ipam_only",
                ipam_address_id=ipam_address_id, last_seen_at=now,
            )
        )
    else:
        rec.ttl = op.ttl
        rec.source = source
        rec.ipam_address_id = ipam_address_id
        rec.last_seen_at = now
