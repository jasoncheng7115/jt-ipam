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
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.address import IPAddress
from app.models.dns import DNSRecord, DNSServer, DNSZone
from app.models.subnet import Subnet
from app.services.dns import DNSAdapterError, get_adapter
from app.services.dns.base import DNSRecordOp
from app.services.dns_compare import normalize_name, normalize_value

#: 某個 zone 讀到 0 筆（A/AAAA/PTR）、本地卻有這麼多筆以上：當成讀取有問題，不刪
_log = logging.getLogger("jt_ipam.dns_sync")
STALE_EMPTY_GUARD = 5


def _scope_subnet_uuids(server: DNSServer) -> set[uuid.UUID]:
    """server.scope_subnet_ids（JSONB 字串陣列）→ UUID set；空回空 set（不限範圍）。"""
    out: set[uuid.UUID] = set()
    for s in (server.scope_subnet_ids or []):
        try:
            out.add(uuid.UUID(str(s)))
        except (ValueError, TypeError):
            pass
    return out


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
    forward_zone_suffix：例 "example.net"；hostname 後接此 suffix 形成 FQDN。
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


def _canon_ip(raw: str | None) -> str | None:
    """DNS 記錄的位址 → 與資料庫相同的標準寫法（IPv6 小寫壓縮）；不是位址就 None。"""
    import ipaddress
    try:
        return ipaddress.ip_address(str(raw or "").strip()).compressed if raw else None
    except ValueError:
        return None


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
        # 連不上 / 認證錯 = 硬失敗：寫 last_error 後往上拋，讓作業顯示「失敗」而非「成功 0」
        server.last_error = str(exc)
        await session.commit()
        raise

    try:
        zones_remote = await adapter.list_zones()
    except DNSAdapterError as exc:
        server.last_error = str(exc)
        await session.commit()
        await adapter.close()
        raise

    try:
        # 收集每個 IP 從 DNS 看到的所有正解名稱，最後只套用一個「穩定」的，
        # 避免同一 IP 有多筆 A 記錄（如 old 與 old-host）時每次 sync 挑到不同
        # 名稱 → hostname 反覆跳動、洗版異動記錄。
        dns_ip_names: dict[str, set[str]] = {}
        # 同一輪用同一個時間戳：記錄的 last_seen_at 才比得起來
        run_at = datetime.now(UTC)
        # 讀取失敗或結果可疑的 zone：它的記錄與名稱這一輪都不清（沒看到不代表伺服器上刪了）
        zone_problems: list[str] = []
        summary.setdefault("removed_records", 0)
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
                zone_problems.append(f"list_records {zinfo.name}: {exc}")
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
                # 只保留與「IP↔名稱對應」相關的型別：正向 A/AAAA + 反向 PTR；
                # CNAME/MX/TXT/NS/SOA/SRV/CAA 等對 IPAM 無對應價值，不取也不存。
                if op.type not in ("A", "AAAA", "PTR"):
                    continue
                key = (op.name, op.type, op.value)
                seen.add(key)
                summary["pulled_records"] += 1

                # 正解 A/AAAA → 先收集名稱，迴圈跑完再挑穩定的一個套用（見下方）
                if zone.type == "forward" and op.type in ("A", "AAAA") and op.value and op.name:
                    dns_ip_names.setdefault(op.value, set()).add(op.name)

                # 正規化後的名稱與值：比對群組的比對與合併顯示用（各廠牌大小寫、結尾點、IPv6 寫法不同）
                name_norm = normalize_name(op.name, zinfo.name)
                value_norm = normalize_value(op.type, op.value)
                rec = local_keys.get(key)
                if rec is None:
                    # 全新從 DNS 拉回的紀錄
                    rec = DNSRecord(
                        zone_id=zone.id,
                        name=op.name, type=op.type, value=op.value, ttl=op.ttl,
                        source="from_dns_pulled",
                        consistency_state="dns_only",
                        last_seen_at=run_at,
                        name_norm=name_norm, value_norm=value_norm,
                    )
                    session.add(rec)
                    summary["dns_only"] += 1
                else:
                    rec.ttl = op.ttl
                    rec.last_seen_at = run_at
                    if rec.name_norm != name_norm or rec.value_norm != value_norm:
                        rec.name_norm, rec.value_norm = name_norm, value_norm
                    if rec.source == "from_ipam":
                        rec.consistency_state = "consistent"
                    elif rec.consistency_state == "ipam_only":
                        rec.consistency_state = "consistent"

            # 標出 ipam_only：本地有 source=from_ipam 但 DNS 看不到
            for key, rec in local_keys.items():
                if key not in seen and rec.source == "from_ipam":
                    rec.consistency_state = "ipam_only"
                    summary["ipam_only"] += 1

            # 伺服器上已經刪掉的記錄要跟著刪（以前從不刪：刪掉的 A 記錄一直留著，
            # 餵給主機名稱、異常偵測、搜尋、AI —— 2026-09-26：IP 換了主機、DNS 記錄已刪，仍顯示舊名）。
            # 讀到 0 筆、本地卻有不少筆：多半是讀取出了問題（權限、adapter 把錯誤變成空清單），不刪。
            gone = [rec for key, rec in local_keys.items()
                    if key not in seen and rec.source == "from_dns_pulled"]
            if gone and not seen and len(gone) >= STALE_EMPTY_GUARD:
                zone_problems.append(f"{zinfo.name}: empty read, kept {len(gone)} records")
                continue
            for rec in gone:
                await session.delete(rec)
            summary["removed_records"] += len(gone)

            zone.last_sync_at = run_at

        # 每個 IP 只套用一個穩定的 DNS 名稱（字母序最小），避免多筆 A 記錄造成跳動
        # 重疊網段：若 server 設了 scope_subnet_ids，IP→IPAddress 比對限定在這些子網路內
        scope_ids = _scope_subnet_uuids(server)
        from app.models.ip_hostname import IPHostnameObservation
        from app.services.hostname_reports import HostnameRun
        peers = (await session.execute(select(func.count()).select_from(DNSServer).where(
            DNSServer.enabled.is_(True)))).scalar_one()
        run = HostnameRun(session, source="dns", origin=f"dns:{server.id}", peers=peers)
        # 整批（2026-09-30 大量資料測試：以前每個位址各查一次 IP、一次其他來源的名稱 ——
        # 10 萬筆 A 記錄就是 20 萬次查詢）。比對改成「唯一才算」：以前 .first() 在重疊網段
        # 又沒設範圍時任意挑一筆，名稱掛到別的單位名下（其他整合 2026-09-26 就改了，這裡漏了）
        from app.services.ip_autocreate import match_existing_many
        by_ip: dict[str, set[str]] = {}
        for ip_val, names in dns_ip_names.items():
            key = _canon_ip(ip_val)
            if key and names:
                by_ip.setdefault(key, set()).update(names)
        matches = await match_existing_many(session, set(by_ip), scope_ids) if by_ip else {}
        hits = {k: m[0] for k, m in matches.items() if m[0] is not None}
        other_names: dict[uuid.UUID, set[str]] = {}
        if hits:
            for ip_id, hn in (await session.execute(
                    select(IPHostnameObservation.ip_id, IPHostnameObservation.hostname).where(
                        in_values(IPHostnameObservation.ip_id, [i.id for i in hits.values()]),
                        IPHostnameObservation.source != "dns"))).all():
                other_names.setdefault(ip_id, set()).add(hn)
        for key, names in by_ip.items():
            ipa = hits.get(key)
            if ipa is None:
                continue
            # 其他來源怎麼稱呼這台機器 —— DNS 裡若有同一個名字，那才是它的名字
            others = set(other_names.get(ipa.id, set()))
            if ipa.hostname:
                others.add(ipa.hostname)
            chosen = pick_dns_hostname(set(names), others=others)
            # None＝這個位址上的名字太多、沒有哪一個代表這台機器 → 清掉 dns 這個來源，
            # 而不是留著一個先前硬挑的名字
            run.report(ipa, chosen)
            if chosen:
                summary["hostname_obs"] = summary.get("hostname_obs", 0) + 1
        # 所有 zone 都完整讀到，才清掉這台伺服器不再回報的名稱
        hn = await run.finish(complete=not zone_problems)
        summary["hostname_removed"] = hn["pruned"]
        if hn["breaker"]:
            zone_problems.append(f"hostname cleanup skipped: {hn['breaker']}")

        server.last_sync_at = datetime.now(UTC)
        # 部分失敗要看得出來：以前最後一律設回 None，失敗的 zone 完全消失
        server.last_error = "; ".join(zone_problems) or None
        await session.commit()
    finally:
        await adapter.close()

    # 比對群組：這台同步完就比對它所在的組（用已同步的紀錄，不另外連 DNS）。比對出錯不影響同步本身
    if server.compare_group_id is not None:
        from app.services.dns_compare import check_group_of_server
        try:
            result = await check_group_of_server(session, server)
            await session.commit()
            if result:
                summary["compare_group"] = result.get("status")
        except Exception as exc:
            await session.rollback()
            _log.warning("dns comparison group check failed after %s: %s", server.name, exc)
    return summary



# 一個位址上出現幾個名字之後，就視為「共用入口」而不是一台機器的名字。
# 反向代理／負載平衡器／共用主機底下常常掛數十個服務名 —— 從中挑一個當作
# 「這台機器叫什麼」是硬猜，而且會在主機名稱來源那裡製造出假的矛盾。
MANY_NAMES = 4


def pick_dns_hostname(names: set[str], *, others: set[str]) -> str | None:
    """一個 IP 有多筆 A 記錄時，DNS 這個來源要回報哪一個名字（沒有合適的就回 None）。

    1. **有佐證的優先**：其他來源已經在用的名字，若 DNS 裡也有，那顯然才是這台機器的名字
       （其他來源常給短名、DNS 給 FQDN，所以比對第一段）。
    2. **名字太多就不報**：那是共用入口的樣態，沒有哪一個名字代表那台機器。
       寧可留白，也不要硬挑一個 —— 挑了會被當成「各來源說法不一致」。
    3. 其餘取字母序最小：維持原本的穩定行為，不會每次同步跳來跳去洗版異動記錄。
    """
    clean = {n.strip() for n in names if n and n.strip()}
    if not clean:
        return None
    if others:
        low = {o.strip().lower().split(".")[0] for o in others if o and o.strip()}
        for n in sorted(clean):
            if n.lower().split(".")[0] in low:
                return n
    if len(clean) >= MANY_NAMES:
        return None
    return sorted(clean)[0]


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
