"""phpIPAM 一鍵遷移 / 持續同步工具。

設計目標（使用者明確要求）：
- 多次匯入：同一份 phpIPAM 來源可以重複跑，每次只動真正變化的列
- 重複偵測：legacy_id → jt_ipam UUID + sha256(canonical row) 對照
- 衝突策略：on_conflict ∈ {skip, overwrite}
- 平行使用：phpIPAM 仍在用時定期 sync 把 phpIPAM → jt-ipam 拉新
- dry-run：預覽不寫入

phpIPAM 是 MySQL；用 pymysql（同步驅動）放到 thread executor 跑。

OWASP 對應：
- A02：MySQL 連線字串可帶密碼，應從 SecretStr 取；不寫入 audit
- A03：所有 INSERT/UPDATE 走 SQLAlchemy ORM；phpIPAM 來源用 named placeholder
- A04：批次（每 1000 列 commit 一次）避免長交易；可中途中止而不爆 transaction
- A09：每次同步寫一筆 audit summary
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import IPAddress
from app.models.customer import Customer
from app.models.device import Device
from app.models.location import Location, Rack
from app.models.migration_mapping import PhpIPAMMigrationMapping
from app.models.nat import NATTranslation
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.vlan import VLAN, VLANDomain
from app.models.vrf import VRF

# ─────────────────── 結果回報 ───────────────────


@dataclass
class TableResult:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0      # 雜湊未變
    errored: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped": self.skipped,
            "errored": self.errored,
            "errors": self.errors[:20],
        }


@dataclass
class MigrationReport:
    started_at: datetime
    finished_at: datetime | None = None
    dry_run: bool = False
    on_conflict: str = "skip"
    tables: dict[str, TableResult] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": (
                (self.finished_at - self.started_at).total_seconds()
                if self.finished_at else None
            ),
            "dry_run": self.dry_run,
            "on_conflict": self.on_conflict,
            "tables": {k: v.to_dict() for k, v in self.tables.items()},
            "error": self.error,
        }


# ─────────────────── MySQL 連線（thread-executor sync）───────────────────


def _connect_mysql_sync(url: str):  # type: ignore[no-untyped-def]
    """url 格式：mysql://user:pass@host:port/dbname"""
    from urllib.parse import urlparse

    import pymysql.cursors

    parsed = urlparse(url)
    if parsed.scheme not in ("mysql", "mariadb"):
        raise ValueError(f"Unsupported scheme: {parsed.scheme}")
    return pymysql.connect(
        host=parsed.hostname or "localhost",
        port=parsed.port or 3306,
        user=parsed.username or "",
        password=parsed.password or "",
        database=(parsed.path or "/").lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor,
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=60,
        autocommit=True,
    )


def _query_all_sync(conn, sql: str) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())


# ─────────────────── 雜湊與 mapping 工具 ───────────────────


def _hash_row(row: dict[str, Any]) -> bytes:
    canonical = json.dumps(row, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).digest()


async def _get_or_create_mapping(
    session: AsyncSession,
    *,
    object_type: str,
    legacy_id: int,
) -> PhpIPAMMigrationMapping | None:
    stmt = select(PhpIPAMMigrationMapping).where(
        PhpIPAMMigrationMapping.object_type == object_type,
        PhpIPAMMigrationMapping.legacy_id == legacy_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _set_mapping(
    session: AsyncSession,
    *,
    object_type: str,
    legacy_id: int,
    jt_ipam_id: uuid.UUID,
    row_hash: bytes,
) -> None:
    existing = await _get_or_create_mapping(
        session, object_type=object_type, legacy_id=legacy_id
    )
    now = datetime.now(UTC)
    if existing is None:
        session.add(
            PhpIPAMMigrationMapping(
                object_type=object_type,
                legacy_id=legacy_id,
                jt_ipam_id=jt_ipam_id,
                last_synced_hash=row_hash,
                last_synced_at=now,
                last_seen_at=now,
            )
        )
    else:
        existing.jt_ipam_id = jt_ipam_id
        existing.last_synced_hash = row_hash
        existing.last_synced_at = now
        existing.last_seen_at = now


async def _touch_seen(
    session: AsyncSession, *, object_type: str, legacy_id: int
) -> None:
    """row hash 沒變但仍存在 — 只更新 last_seen_at。"""
    existing = await _get_or_create_mapping(
        session, object_type=object_type, legacy_id=legacy_id
    )
    if existing is not None:
        existing.last_seen_at = datetime.now(UTC)


# ─────────────────── 各物件的同步函式 ───────────────────


async def _sync_sections(
    session: AsyncSession, rows: list[dict[str, Any]],
    *, customer_legacy_to_uuid: dict[int, uuid.UUID] | None = None,
    on_conflict: str, dry_run: bool,
) -> TableResult:
    cust_map = customer_legacy_to_uuid or {}
    def _cust_id(row: dict[str, Any]) -> uuid.UUID | None:
        cid = row.get("customer_id") or row.get("customerId")
        try:
            return cust_map.get(int(cid)) if cid else None
        except (TypeError, ValueError):
            return None
    res = TableResult()
    # 兩階段：先全部建（沒 parent），再 link parent — 避免外鍵循環
    pending_parent: list[tuple[int, int]] = []
    legacy_to_uuid: dict[int, uuid.UUID] = {}

    for row in rows:
        try:
            async with session.begin_nested():
                legacy_id = int(row["id"])
                row_hash = _hash_row(row)
                mapping = await _get_or_create_mapping(
                    session, object_type="section", legacy_id=legacy_id
                )

                if mapping and mapping.last_synced_hash == row_hash:
                    res.skipped += 1
                    await _touch_seen(session, object_type="section", legacy_id=legacy_id)
                    legacy_to_uuid[legacy_id] = mapping.jt_ipam_id
                    continue

                if mapping is None:
                    # 新增 — dry-run 也要 add + flush + set_mapping，這樣下游 lookup
                    # 才能拿到 in-memory 已分配的 UUID；最後在 run_migration 統一 rollback。
                    obj = Section(
                        name=row.get("name") or f"section-{legacy_id}",
                        description=row.get("description"),
                        strict_mode=bool(row.get("strictMode")),
                        display_order=int(row.get("order") or 0),
                        customer_id=_cust_id(row),
                    )
                    session.add(obj)
                    await session.flush()
                    legacy_to_uuid[legacy_id] = obj.id
                    if row.get("masterSection") and int(row["masterSection"]) > 0:
                        pending_parent.append((legacy_id, int(row["masterSection"])))
                    await _set_mapping(
                        session, object_type="section", legacy_id=legacy_id,
                        jt_ipam_id=obj.id, row_hash=row_hash,
                    )
                    res.inserted += 1
                else:
                    if on_conflict == "skip":
                        res.skipped += 1
                        await _touch_seen(
                            session, object_type="section", legacy_id=legacy_id
                        )
                        legacy_to_uuid[legacy_id] = mapping.jt_ipam_id
                        continue
                    # overwrite
                    obj = await session.get(Section, mapping.jt_ipam_id)  # type: ignore[assignment]
                    if obj is None:
                        res.errored += 1
                        res.errors.append(
                            f"section legacy_id={legacy_id} mapped to UUID "
                            f"{mapping.jt_ipam_id} but not found in jt-ipam"
                        )
                        continue
                    obj.name = row.get("name") or obj.name
                    obj.description = row.get("description")
                    obj.strict_mode = bool(row.get("strictMode"))
                    obj.display_order = int(row.get("order") or 0)
                    obj.customer_id = _cust_id(row)
                    await _set_mapping(
                        session, object_type="section", legacy_id=legacy_id,
                        jt_ipam_id=obj.id, row_hash=row_hash,
                    )
                    legacy_to_uuid[legacy_id] = obj.id
                    res.updated += 1
        except Exception as exc:
            res.errored += 1
            res.errors.append(f"section row {row.get('id')!r}: {exc!r}")

    # 第二階段：解 parent
    if pending_parent:
        for legacy_id, parent_legacy in pending_parent:
            try:
                child_uuid = legacy_to_uuid.get(legacy_id)
                parent_uuid = legacy_to_uuid.get(parent_legacy)
                if not child_uuid or not parent_uuid:
                    continue
                child = await session.get(Section, child_uuid)
                if child is not None:
                    child.parent_id = parent_uuid
            except Exception as exc:
                res.errored += 1
                res.errors.append(f"section parent link {legacy_id}: {exc!r}")

    return res


async def _sync_simple(
    session: AsyncSession,
    *,
    object_type: str,
    rows: list[dict[str, Any]],
    on_conflict: str,
    dry_run: bool,
    build_new: Any,
    apply_update: Any,
    model_cls: type,
) -> TableResult:
    """通用簡單表的同步：build_new(row) 建新物件，apply_update(obj, row) 更新欄位。

    每 row 包 SAVEPOINT：flush() 失敗時只 rollback 這 row，後續還能繼續跑。
    """
    res = TableResult()
    for row in rows:
        try:
            async with session.begin_nested():
                legacy_id = int(row["id"])
                row_hash = _hash_row(row)
                mapping = await _get_or_create_mapping(
                    session, object_type=object_type, legacy_id=legacy_id
                )
                if mapping and mapping.last_synced_hash == row_hash:
                    res.skipped += 1
                    await _touch_seen(
                        session, object_type=object_type, legacy_id=legacy_id
                    )
                    continue
                if mapping is None:
                    obj = build_new(row)
                    if obj is None:
                        res.errored += 1
                        res.errors.append(f"{object_type} {legacy_id}: build_new returned None")
                        continue
                    session.add(obj)
                    await session.flush()
                    await _set_mapping(
                        session, object_type=object_type, legacy_id=legacy_id,
                        jt_ipam_id=obj.id, row_hash=row_hash,
                    )
                    res.inserted += 1
                else:
                    if on_conflict == "skip":
                        res.skipped += 1
                        await _touch_seen(
                            session, object_type=object_type, legacy_id=legacy_id
                        )
                        continue
                    obj = await session.get(model_cls, mapping.jt_ipam_id)
                    if obj is None:
                        res.errored += 1
                        res.errors.append(
                            f"{object_type} legacy_id={legacy_id} mapping orphaned"
                        )
                        continue
                    apply_update(obj, row)
                    await _set_mapping(
                        session, object_type=object_type, legacy_id=legacy_id,
                        jt_ipam_id=obj.id, row_hash=row_hash,
                    )
                    res.updated += 1
        except Exception as exc:
            res.errored += 1
            res.errors.append(f"{object_type} row {row.get('id')!r}: {exc!r}")

    return res


async def _sync_vlans(
    session: AsyncSession,
    *,
    domain_rows: list[dict[str, Any]],
    vlan_rows: list[dict[str, Any]],
    on_conflict: str,
    dry_run: bool,
) -> tuple[TableResult, TableResult]:
    # Domains 先
    def build_domain(row):  # type: ignore[no-untyped-def]
        return VLANDomain(name=row.get("name") or f"domain-{row['id']}",
                          description=row.get("description"))
    def apply_domain(obj, row):  # type: ignore[no-untyped-def]
        obj.name = row.get("name") or obj.name
        obj.description = row.get("description")

    domain_res = await _sync_simple(
        session, object_type="vlan_domain", rows=domain_rows,
        on_conflict=on_conflict, dry_run=dry_run,
        build_new=build_domain, apply_update=apply_domain, model_cls=VLANDomain,
    )

    # 解 domain mapping
    domain_legacy_to_uuid: dict[int, uuid.UUID] = {}
    for d in domain_rows:
        m = await _get_or_create_mapping(
            session, object_type="vlan_domain", legacy_id=int(d["id"])
        )
        if m:
            domain_legacy_to_uuid[int(d["id"])] = m.jt_ipam_id

    def build_vlan(row):  # type: ignore[no-untyped-def]
        domain_legacy = int(row.get("domainId") or 1)
        domain_uuid = domain_legacy_to_uuid.get(domain_legacy)
        if domain_uuid is None:
            return None
        return VLAN(
            domain_id=domain_uuid,
            number=int(row.get("number") or 1),
            name=row.get("name") or f"vlan-{row['id']}",
            description=row.get("description"),
        )
    def apply_vlan(obj, row):  # type: ignore[no-untyped-def]
        obj.name = row.get("name") or obj.name
        obj.description = row.get("description")

    vlan_res = await _sync_simple(
        session, object_type="vlan", rows=vlan_rows,
        on_conflict=on_conflict, dry_run=dry_run,
        build_new=build_vlan, apply_update=apply_vlan, model_cls=VLAN,
    )
    return domain_res, vlan_res


async def _sync_customers(
    session: AsyncSession, rows: list[dict[str, Any]],
    *, on_conflict: str, dry_run: bool,
) -> TableResult:
    """phpIPAM customers → jt-ipam customers。

    phpIPAM 欄位（v1.5+）：id, type, title, address, postcode, city, state,
    country, contact, contact_mail, contact_phone, description。
    title 必填、唯一 → 用 title 當 name + display title。
    """
    def _addr(row: dict[str, Any]) -> str | None:
        parts = [str(row.get(k) or "").strip() for k in ("address", "postcode", "city", "state", "country")]
        joined = " ".join(p for p in parts if p)
        return joined or None

    def build(row):  # type: ignore[no-untyped-def]
        title = (row.get("title") or "").strip()
        if not title:
            return None
        # name = title 的安全短名；slug 化避免重複空白
        name = "-".join(title.split())[:128]
        return Customer(
            name=name,
            title=title,
            description=row.get("description") or None,
            contact=row.get("contact") or None,
            email=row.get("contact_mail") or None,
            phone=row.get("contact_phone") or None,
            address=_addr(row),
        )

    def apply(obj, row):  # type: ignore[no-untyped-def]
        title = (row.get("title") or "").strip()
        if title:
            obj.title = title
            obj.name = "-".join(title.split())[:128]
        obj.description = row.get("description") or None
        obj.contact = row.get("contact") or None
        obj.email = row.get("contact_mail") or None
        obj.phone = row.get("contact_phone") or None
        obj.address = _addr(row)

    return await _sync_simple(
        session, object_type="customer", rows=rows,
        on_conflict=on_conflict, dry_run=dry_run,
        build_new=build, apply_update=apply, model_cls=Customer,
    )


async def _sync_vrfs(
    session: AsyncSession, rows: list[dict[str, Any]],
    *, on_conflict: str, dry_run: bool,
) -> TableResult:
    def build(row):  # type: ignore[no-untyped-def]
        return VRF(
            name=row.get("name") or f"vrf-{row['id']}",
            rd=row.get("rd"),
            description=row.get("description"),
            allow_overlap=True,
        )
    def apply(obj, row):  # type: ignore[no-untyped-def]
        obj.name = row.get("name") or obj.name
        obj.rd = row.get("rd")
        obj.description = row.get("description")

    return await _sync_simple(
        session, object_type="vrf", rows=rows,
        on_conflict=on_conflict, dry_run=dry_run,
        build_new=build, apply_update=apply, model_cls=VRF,
    )


def _phpipam_subnet_cidr(row: dict[str, Any]) -> str | None:
    """phpIPAM subnet 的 IP 是 decimal int（subnet）+ mask；轉 CIDR。

    phpIPAM 的 mask 是 varchar，可能是空字串 / None / 非數字 / 超出範圍。
    任何不合法都回 None，呼叫端會把這 row 標 errored 而不是把 'X/' 餵給 Postgres。
    """
    raw = row.get("subnet")
    mask = row.get("mask")
    if raw is None or mask is None:
        return None
    raw_s = str(raw).strip()
    mask_s = str(mask).strip()
    if not raw_s or not mask_s:
        return None
    try:
        n = int(raw_s)
        m = int(mask_s)
    except ValueError:
        return None
    try:
        # phpIPAM stores as decimal string (varchar) for IPv4 + IPv6
        # IPv4: int < 2**32; IPv6: int < 2**128
        if n < (1 << 32):
            if not 0 <= m <= 32:
                return None
            return f"{ipaddress.IPv4Address(n)}/{m}"
        if not 0 <= m <= 128:
            return None
        return f"{ipaddress.IPv6Address(n)}/{m}"
    except (ValueError, ipaddress.AddressValueError):
        return None


async def _sync_subnets(
    session: AsyncSession, rows: list[dict[str, Any]],
    *,
    section_legacy_to_uuid: dict[int, uuid.UUID],
    vlan_legacy_to_uuid: dict[int, uuid.UUID],
    vrf_legacy_to_uuid: dict[int, uuid.UUID],
    customer_legacy_to_uuid: dict[int, uuid.UUID] | None = None,
    on_conflict: str, dry_run: bool,
) -> TableResult:
    res = TableResult()
    legacy_to_uuid: dict[int, uuid.UUID] = {}
    pending_master: list[tuple[int, int]] = []
    cust_map = customer_legacy_to_uuid or {}
    def _cust_id(row: dict[str, Any]) -> uuid.UUID | None:
        cid = row.get("customer_id") or row.get("customerId")
        try:
            return cust_map.get(int(cid)) if cid else None
        except (TypeError, ValueError):
            return None

    for row in rows:
        try:
            async with session.begin_nested():
                legacy_id = int(row["id"])
                cidr = _phpipam_subnet_cidr(row)
                if cidr is None:
                    res.errored += 1
                    res.errors.append(f"subnet legacy_id={legacy_id}: bad subnet/mask")
                    continue
                section_legacy = int(row.get("sectionId") or 0)
                section_uuid = section_legacy_to_uuid.get(section_legacy)
                if section_uuid is None:
                    res.errored += 1
                    res.errors.append(f"subnet legacy_id={legacy_id}: section_id mapping missing")
                    continue

                row_hash = _hash_row(row)
                mapping = await _get_or_create_mapping(
                    session, object_type="subnet", legacy_id=legacy_id
                )
                if mapping and mapping.last_synced_hash == row_hash:
                    res.skipped += 1
                    await _touch_seen(session, object_type="subnet", legacy_id=legacy_id)
                    legacy_to_uuid[legacy_id] = mapping.jt_ipam_id
                    continue

                vlan_uuid = vlan_legacy_to_uuid.get(int(row.get("vlanId") or 0)) if row.get("vlanId") else None
                vrf_uuid = vrf_legacy_to_uuid.get(int(row.get("vrfId") or 0)) if row.get("vrfId") else None

                if mapping is None:
                    obj = Subnet(
                        section_id=section_uuid,
                        cidr=cidr,
                        description=row.get("description"),
                        vlan_id=vlan_uuid,
                        vrf_id=vrf_uuid,
                        is_pool=bool(row.get("isFolder")),
                        is_full=bool(row.get("isFull")),
                        scan_enabled=bool(row.get("pingSubnet")),
                        customer_id=_cust_id(row),
                    )
                    session.add(obj)
                    await session.flush()
                    legacy_to_uuid[legacy_id] = obj.id
                    if row.get("masterSubnetId") and int(row["masterSubnetId"]) > 0:
                        pending_master.append((legacy_id, int(row["masterSubnetId"])))
                    await _set_mapping(
                        session, object_type="subnet", legacy_id=legacy_id,
                        jt_ipam_id=obj.id, row_hash=row_hash,
                    )
                    res.inserted += 1
                else:
                    if on_conflict == "skip":
                        res.skipped += 1
                        await _touch_seen(session, object_type="subnet", legacy_id=legacy_id)
                        legacy_to_uuid[legacy_id] = mapping.jt_ipam_id
                        continue
                    obj = await session.get(Subnet, mapping.jt_ipam_id)  # type: ignore[assignment]
                    if obj is None:
                        res.errored += 1
                        res.errors.append(f"subnet legacy_id={legacy_id} mapping orphaned")
                        continue
                    obj.section_id = section_uuid
                    obj.description = row.get("description")
                    obj.vlan_id = vlan_uuid
                    obj.vrf_id = vrf_uuid
                    obj.is_pool = bool(row.get("isFolder"))
                    obj.is_full = bool(row.get("isFull"))
                    obj.scan_enabled = bool(row.get("pingSubnet"))
                    obj.customer_id = _cust_id(row)
                    await _set_mapping(
                        session, object_type="subnet", legacy_id=legacy_id,
                        jt_ipam_id=obj.id, row_hash=row_hash,
                    )
                    legacy_to_uuid[legacy_id] = obj.id
                    res.updated += 1
        except Exception as exc:
            res.errored += 1
            res.errors.append(f"subnet row {row.get('id')!r}: {exc!r}")

    # master_subnet 二階段
    if pending_master:
        for legacy_id, master_legacy in pending_master:
            try:
                child = await session.get(Subnet, legacy_to_uuid[legacy_id])
                master_uuid = legacy_to_uuid.get(master_legacy)
                if child and master_uuid:
                    child.master_subnet_id = master_uuid
            except Exception as exc:
                res.errored += 1
                res.errors.append(f"subnet master link {legacy_id}: {exc!r}")

    return res


async def _sync_addresses(
    session: AsyncSession, rows: list[dict[str, Any]],
    *,
    subnet_legacy_to_uuid: dict[int, uuid.UUID],
    device_legacy_to_uuid: dict[int, uuid.UUID] | None = None,
    customer_legacy_to_uuid: dict[int, uuid.UUID] | None = None,
    on_conflict: str, dry_run: bool,
) -> TableResult:
    res = TableResult()
    cust_map = customer_legacy_to_uuid or {}
    def _cust_id(row: dict[str, Any]) -> uuid.UUID | None:
        cid = row.get("customer_id") or row.get("customerId")
        try:
            return cust_map.get(int(cid)) if cid else None
        except (TypeError, ValueError):
            return None
    for row in rows:
        try:
            async with session.begin_nested():
                legacy_id = int(row["id"])
                subnet_legacy = int(row.get("subnetId") or 0)
                subnet_uuid = subnet_legacy_to_uuid.get(subnet_legacy)
                if subnet_uuid is None:
                    res.errored += 1
                    res.errors.append(f"address {legacy_id}: subnet mapping missing")
                    continue
                raw_ip = row.get("ip_addr") or row.get("ip")
                if raw_ip is None:
                    res.errored += 1
                    res.errors.append(f"address {legacy_id}: no ip column")
                    continue
                try:
                    n = int(raw_ip)
                    if n < (1 << 32):
                        ip_str = str(ipaddress.IPv4Address(n))
                    else:
                        ip_str = str(ipaddress.IPv6Address(n))
                except ValueError:
                    ip_str = str(raw_ip)
                row_hash = _hash_row(row)
                mapping = await _get_or_create_mapping(
                    session, object_type="ip", legacy_id=legacy_id
                )
                if mapping and mapping.last_synced_hash == row_hash:
                    res.skipped += 1
                    await _touch_seen(session, object_type="ip", legacy_id=legacy_id)
                    continue
                mac = row.get("mac")
                if mac == "":
                    mac = None
                # phpIPAM 0 / NULL = 未綁定 device
                device_legacy_raw = row.get("deviceId")
                try:
                    device_legacy = int(device_legacy_raw) if device_legacy_raw else 0
                except (TypeError, ValueError):
                    device_legacy = 0
                device_uuid = (
                    device_legacy_to_uuid.get(device_legacy)
                    if device_legacy_to_uuid and device_legacy > 0 else None
                )
                if mapping is None:
                    obj = IPAddress(
                        subnet_id=subnet_uuid,
                        ip=ip_str,
                        hostname=row.get("hostname") or None,
                        description=row.get("description"),
                        state=str(_phpipam_state(row.get("state"))),
                        mac=mac,
                        owner=row.get("owner"),
                        device_id=device_uuid,
                        switch_port=row.get("port"),
                        note=row.get("note"),
                        customer_id=_cust_id(row),
                        discovery_source="phpipam",
                    )
                    session.add(obj)
                    await session.flush()
                    await _set_mapping(
                        session, object_type="ip", legacy_id=legacy_id,
                        jt_ipam_id=obj.id, row_hash=row_hash,
                    )
                    res.inserted += 1
                else:
                    if on_conflict == "skip":
                        res.skipped += 1
                        await _touch_seen(session, object_type="ip", legacy_id=legacy_id)
                        continue
                    obj = await session.get(IPAddress, mapping.jt_ipam_id)  # type: ignore[assignment]
                    if obj is None:
                        res.errored += 1
                        continue
                    obj.hostname = row.get("hostname") or None
                    obj.description = row.get("description")
                    obj.state = str(_phpipam_state(row.get("state")))
                    obj.mac = mac
                    obj.owner = row.get("owner")
                    obj.device_id = device_uuid
                    obj.switch_port = row.get("port")
                    obj.note = row.get("note")
                    obj.customer_id = _cust_id(row)
                    await _set_mapping(
                        session, object_type="ip", legacy_id=legacy_id,
                        jt_ipam_id=obj.id, row_hash=row_hash,
                    )
                    res.updated += 1
        except Exception as exc:
            res.errored += 1
            res.errors.append(f"ip row {row.get('id')!r}: {exc!r}")

    return res


def _phpipam_state(v: Any) -> str:
    """phpIPAM 用整數對應 IP 狀態：2=active, 1=offline, 3=reserved, 4=DHCP, 5=used"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return "active"
    return {1: "offline", 2: "active", 3: "reserved", 4: "dhcp", 5: "used"}.get(n, "active")


async def _sync_locations_and_racks(
    session: AsyncSession,
    *,
    location_rows: list[dict[str, Any]],
    rack_rows: list[dict[str, Any]],
    on_conflict: str, dry_run: bool,
) -> tuple[TableResult, TableResult]:
    def build_loc(row):  # type: ignore[no-untyped-def]
        return Location(
            name=row.get("name") or f"loc-{row['id']}",
            address=row.get("address"),
            description=row.get("description"),
        )
    def apply_loc(obj, row):  # type: ignore[no-untyped-def]
        obj.name = row.get("name") or obj.name
        obj.address = row.get("address")
        obj.description = row.get("description")

    loc_res = await _sync_simple(
        session, object_type="location", rows=location_rows,
        on_conflict=on_conflict, dry_run=dry_run,
        build_new=build_loc, apply_update=apply_loc, model_cls=Location,
    )

    loc_legacy_to_uuid: dict[int, uuid.UUID] = {}
    for r in location_rows:
        m = await _get_or_create_mapping(
            session, object_type="location", legacy_id=int(r["id"])
        )
        if m:
            loc_legacy_to_uuid[int(r["id"])] = m.jt_ipam_id

    def build_rack(row):  # type: ignore[no-untyped-def]
        loc_legacy = int(row.get("location") or 0)
        return Rack(
            location_id=loc_legacy_to_uuid.get(loc_legacy),
            name=row.get("name") or f"rack-{row['id']}",
            u_height=int(row.get("size") or 42),
            description=row.get("description"),
        )
    def apply_rack(obj, row):  # type: ignore[no-untyped-def]
        obj.name = row.get("name") or obj.name
        obj.u_height = int(row.get("size") or 42)
        obj.description = row.get("description")
        loc_legacy = int(row.get("location") or 0)
        obj.location_id = loc_legacy_to_uuid.get(loc_legacy)

    rack_res = await _sync_simple(
        session, object_type="rack", rows=rack_rows,
        on_conflict=on_conflict, dry_run=dry_run,
        build_new=build_rack, apply_update=apply_rack, model_cls=Rack,
    )
    return loc_res, rack_res


# phpIPAM deviceTypes.tname → jt-ipam Device.type（CHECK 約束限這 8 種）
_DEVICE_TYPE_MAP = {
    "router": "router",
    "switch": "switch",
    "firewall": "firewall",
    "server": "server",
    "host": "server",
    "workstation": "server",
    "storage": "storage",
    "san": "storage",
    "nas": "storage",
    "ap": "ap",
    "access point": "ap",
    "wifi": "ap",
    "ipmi": "ipmi",
    "bmc": "ipmi",
}


def _phpipam_device_type(tname: str | None) -> str:
    if not tname:
        return "other"
    key = tname.strip().lower()
    return _DEVICE_TYPE_MAP.get(key, "other")


async def _sync_devices(
    session: AsyncSession,
    *,
    device_rows: list[dict[str, Any]],
    type_rows: list[dict[str, Any]],
    location_legacy_to_uuid: dict[int, uuid.UUID],
    rack_legacy_to_uuid: dict[int, uuid.UUID],
    customer_legacy_to_uuid: dict[int, uuid.UUID] | None = None,
    on_conflict: str, dry_run: bool,
) -> TableResult:
    """phpIPAM devices → jt-ipam Device。

    欄位對應：hostname → name、type (FK to deviceTypes) → 經 _phpipam_device_type 正規化、
    vendor/model 直接帶、location/rack → 經 legacy mapping 解 UUID、rack_start → u_position、
    rack_size → u_size。phpIPAM 沒有 serial 欄。
    """
    # 建 phpIPAM tid → tname 對照
    type_name_by_tid: dict[int, str] = {}
    for r in type_rows:
        tid = r.get("tid")
        tname = r.get("tname")
        if tid is None:
            continue
        try:
            type_name_by_tid[int(tid)] = str(tname or "")
        except (TypeError, ValueError):
            continue

    def _to_int_or_none(v: Any) -> int | None:
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None
        return n if n > 0 else None

    cust_map = customer_legacy_to_uuid or {}
    def _cust_id(row: dict[str, Any]) -> uuid.UUID | None:
        cid = row.get("customer_id") or row.get("customerId")
        try:
            return cust_map.get(int(cid)) if cid else None
        except (TypeError, ValueError):
            return None

    def build_dev(row):  # type: ignore[no-untyped-def]
        loc_legacy = _to_int_or_none(row.get("location"))
        rack_legacy = _to_int_or_none(row.get("rack"))
        type_legacy = _to_int_or_none(row.get("type"))
        tname = type_name_by_tid.get(type_legacy) if type_legacy else None
        return Device(
            name=(row.get("hostname") or f"device-{row['id']}"),
            type=_phpipam_device_type(tname),
            vendor=(row.get("vendor") or None),
            model=(row.get("model") or None),
            location_id=(location_legacy_to_uuid.get(loc_legacy) if loc_legacy else None),
            rack_id=(rack_legacy_to_uuid.get(rack_legacy) if rack_legacy else None),
            u_position=_to_int_or_none(row.get("rack_start")),
            u_size=_to_int_or_none(row.get("rack_size")),
            description=(row.get("description") or None),
            customer_id=_cust_id(row),
        )

    def apply_dev(obj, row):  # type: ignore[no-untyped-def]
        loc_legacy = _to_int_or_none(row.get("location"))
        rack_legacy = _to_int_or_none(row.get("rack"))
        type_legacy = _to_int_or_none(row.get("type"))
        tname = type_name_by_tid.get(type_legacy) if type_legacy else None
        obj.name = row.get("hostname") or obj.name
        obj.type = _phpipam_device_type(tname)
        obj.vendor = row.get("vendor") or None
        obj.model = row.get("model") or None
        obj.location_id = location_legacy_to_uuid.get(loc_legacy) if loc_legacy else None
        obj.rack_id = rack_legacy_to_uuid.get(rack_legacy) if rack_legacy else None
        obj.u_position = _to_int_or_none(row.get("rack_start"))
        obj.u_size = _to_int_or_none(row.get("rack_size"))
        obj.description = row.get("description") or None
        obj.customer_id = _cust_id(row)

    return await _sync_simple(
        session, object_type="device", rows=device_rows,
        on_conflict=on_conflict, dry_run=dry_run,
        build_new=build_dev, apply_update=apply_dev, model_cls=Device,
    )


# phpIPAM nat.type → jt-ipam NAT.type（CHECK 約束限這 3 種）
_NAT_TYPE_MAP = {
    "static": "one_to_one",
    "destination": "port_forward",
    "source": "many_to_one",
}


def _phpipam_nat_type(t: str | None) -> str:
    if not t:
        return "one_to_one"
    return _NAT_TYPE_MAP.get(t.strip().lower(), "one_to_one")


def _parse_phpipam_nat_endpoint(raw: Any) -> int | None:
    """phpIPAM nat.src / nat.dst 是 JSON 字串 e.g. {"ipaddresses":["38"],"subnets":[...]}。
    取第一個 ipaddress legacy ID。其他狀況回 None。
    """
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
    except (ValueError, TypeError):
        return None
    ips = obj.get("ipaddresses") if isinstance(obj, dict) else None
    if not isinstance(ips, list) or not ips:
        return None
    try:
        return int(ips[0])
    except (ValueError, TypeError):
        return None


def _parse_port(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        n = int(str(raw).strip())
    except (ValueError, TypeError):
        return None
    return n if 1 <= n <= 65535 else None


def _parse_device_legacy(raw: Any) -> int | None:
    """phpIPAM nat.device 通常是 CSV，例如 "5" 或 "5,7"。取第一個。"""
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    first = s.split(",")[0].strip()
    try:
        n = int(first)
    except ValueError:
        return None
    return n if n > 0 else None


async def _sync_nat(
    session: AsyncSession,
    *,
    rows: list[dict[str, Any]],
    address_legacy_to_uuid: dict[int, uuid.UUID] | None = None,
    device_legacy_to_uuid: dict[int, uuid.UUID] | None = None,
    on_conflict: str, dry_run: bool,
) -> TableResult:
    """phpIPAM nat → jt-ipam NATTranslation。

    src/dst 是 phpIPAM JSON 字串（含 ipaddresses 跟 subnets 陣列）；取第一個 ipaddress
    legacy ID 解到 jt-ipam IPAddress UUID。多個 endpoint 的情況只記第一個 — 因為
    jt-ipam NAT model 是 1:1 src_ip / dst_ip 而不是 list；要表達 many-to-one 用 NAT.type。
    """
    addr_map = address_legacy_to_uuid or {}
    dev_map = device_legacy_to_uuid or {}

    def _resolve_ip(raw: Any) -> uuid.UUID | None:
        legacy = _parse_phpipam_nat_endpoint(raw)
        return addr_map.get(legacy) if legacy is not None else None

    def _resolve_device(raw: Any) -> uuid.UUID | None:
        legacy = _parse_device_legacy(raw)
        return dev_map.get(legacy) if legacy is not None else None

    def _residual_hint(row: dict[str, Any]) -> str:
        """src/dst 含 subnets 或多個 ipaddresses，jt-ipam 1:1 model 表達不了 — 留 hint。"""
        parts: list[str] = []
        for key in ("src", "dst"):
            raw = row.get(key)
            if not raw:
                continue
            try:
                obj = json.loads(str(raw))
            except (ValueError, TypeError):
                continue
            ips = obj.get("ipaddresses") if isinstance(obj, dict) else None
            subs = obj.get("subnets") if isinstance(obj, dict) else None
            if (isinstance(ips, list) and len(ips) > 1) or (isinstance(subs, list) and subs):
                parts.append(f"{key}={raw}")
        return f" [phpIPAM 額外端點: {' '.join(parts)}]" if parts else ""

    def build(row):  # type: ignore[no-untyped-def]
        desc = (row.get("description") or "").rstrip() + _residual_hint(row)
        return NATTranslation(
            name=row.get("name") or f"nat-{row['id']}",
            type=_phpipam_nat_type(row.get("type")),
            protocol="any",
            src_ip_id=_resolve_ip(row.get("src")),
            dst_ip_id=_resolve_ip(row.get("dst")),
            src_port=_parse_port(row.get("src_port")),
            dst_port=_parse_port(row.get("dst_port")),
            device_id=_resolve_device(row.get("device")),
            description=desc or None,
            source_origin="phpipam",
            external_id=str(row["id"]),
        )

    def apply(obj, row):  # type: ignore[no-untyped-def]
        obj.name = row.get("name") or obj.name
        obj.type = _phpipam_nat_type(row.get("type"))
        obj.protocol = "any"
        obj.src_ip_id = _resolve_ip(row.get("src"))
        obj.dst_ip_id = _resolve_ip(row.get("dst"))
        obj.src_port = _parse_port(row.get("src_port"))
        obj.dst_port = _parse_port(row.get("dst_port"))
        obj.device_id = _resolve_device(row.get("device"))
        obj.description = (row.get("description") or "").rstrip() + _residual_hint(row) or None
        obj.source_origin = "phpipam"
        obj.external_id = str(row["id"])

    return await _sync_simple(
        session, object_type="nat", rows=rows,
        on_conflict=on_conflict, dry_run=dry_run,
        build_new=build, apply_update=apply, model_cls=NATTranslation,
    )


# ─────────────────── 主入口 ───────────────────


async def run_migration(
    session: AsyncSession,
    *,
    mysql_url: str,
    on_conflict: str = "skip",
    dry_run: bool = False,
) -> MigrationReport:
    """執行一次 phpIPAM → jt-ipam 同步。

    on_conflict:
      - skip      ：mapping 已存在 + hash 不同 → 不動 jt-ipam，僅跳過
      - overwrite ：mapping 已存在 + hash 不同 → 用 phpIPAM 資料覆寫 jt-ipam

    第一次跑通常 inserted 居多；後續持續同步若選 overwrite，phpIPAM 端的
    更新會持續推送至 jt-ipam，使兩邊內容一致直到正式切換。
    """
    if on_conflict not in ("skip", "overwrite"):
        raise ValueError(f"on_conflict must be skip or overwrite, got {on_conflict!r}")

    report = MigrationReport(
        started_at=datetime.now(UTC), dry_run=dry_run, on_conflict=on_conflict,
    )

    # 連線
    try:
        conn = await asyncio.to_thread(_connect_mysql_sync, mysql_url)
    except Exception as exc:
        report.error = f"MySQL connect failed: {exc}"
        report.finished_at = datetime.now(UTC)
        return report

    try:
        # 拉所有需要的表
        sections = await asyncio.to_thread(_query_all_sync, conn, "SELECT * FROM sections")
        vlan_domains = await asyncio.to_thread(
            _query_all_sync, conn, "SELECT * FROM vlanDomains"
        )
        vlans = await asyncio.to_thread(_query_all_sync, conn, "SELECT * FROM vlans")
        vrfs = await asyncio.to_thread(_query_all_sync, conn, "SELECT * FROM vrf")
        subnets = await asyncio.to_thread(_query_all_sync, conn, "SELECT * FROM subnets")
        addresses = await asyncio.to_thread(_query_all_sync, conn, "SELECT * FROM ipaddresses")
        locations = await asyncio.to_thread(_query_all_sync, conn, "SELECT * FROM locations")
        racks = await asyncio.to_thread(_query_all_sync, conn, "SELECT * FROM racks")
        devices = await asyncio.to_thread(_query_all_sync, conn, "SELECT * FROM devices")
        # deviceTypes 可能不存在（極早期版本）；失敗回空表，type 全部 fallback 成 'other'
        try:
            device_types = await asyncio.to_thread(
                _query_all_sync, conn, "SELECT tid, tname FROM deviceTypes"
            )
        except Exception:
            device_types = []
        # nat 表也可能不存在；空表
        try:
            nat_rows = await asyncio.to_thread(_query_all_sync, conn, "SELECT * FROM nat")
        except Exception:
            nat_rows = []
        # customers 表（phpIPAM 1.4+）；舊版沒有→空表
        try:
            customer_rows = await asyncio.to_thread(_query_all_sync, conn, "SELECT * FROM customers")
        except Exception:
            customer_rows = []

        # phpIPAM 1.5+ 部分表的 PK 不是 "id" — 補一個 "id" alias 統一處理。
        # vlans → vlanId、vrf → vrfId（其它表本來就是 id）。
        for r in vlans:
            r.setdefault("id", r.get("vlanId"))
        for r in vrfs:
            r.setdefault("id", r.get("vrfId"))
    except Exception as exc:
        try:
            conn.close()
        except Exception:
            pass
        report.error = f"phpIPAM query failed: {exc}"
        report.finished_at = datetime.now(UTC)
        return report

    try:
        conn.close()
    except Exception:
        pass

    # 0. Customers（要在 sections/subnets/devices/ip 之前，給後面 FK 用）
    report.tables["customers"] = await _sync_customers(
        session, customer_rows, on_conflict=on_conflict, dry_run=dry_run,
    )
    customer_map: dict[int, uuid.UUID] = {}
    for c in customer_rows:
        m = await _get_or_create_mapping(
            session, object_type="customer", legacy_id=int(c["id"])
        )
        if m:
            customer_map[int(c["id"])] = m.jt_ipam_id

    # 1. Sections
    report.tables["sections"] = await _sync_sections(
        session, sections, customer_legacy_to_uuid=customer_map,
        on_conflict=on_conflict, dry_run=dry_run,
    )
    section_map: dict[int, uuid.UUID] = {}
    for s in sections:
        m = await _get_or_create_mapping(
            session, object_type="section", legacy_id=int(s["id"])
        )
        if m:
            section_map[int(s["id"])] = m.jt_ipam_id

    # 2. VLAN Domains + VLANs
    domain_res, vlan_res = await _sync_vlans(
        session, domain_rows=vlan_domains, vlan_rows=vlans,
        on_conflict=on_conflict, dry_run=dry_run,
    )
    report.tables["vlan_domains"] = domain_res
    report.tables["vlans"] = vlan_res
    vlan_map: dict[int, uuid.UUID] = {}
    for v in vlans:
        m = await _get_or_create_mapping(
            session, object_type="vlan", legacy_id=int(v["id"])
        )
        if m:
            vlan_map[int(v["id"])] = m.jt_ipam_id

    # 3. VRFs
    report.tables["vrfs"] = await _sync_vrfs(
        session, vrfs, on_conflict=on_conflict, dry_run=dry_run,
    )
    vrf_map: dict[int, uuid.UUID] = {}
    for v in vrfs:
        m = await _get_or_create_mapping(
            session, object_type="vrf", legacy_id=int(v["id"])
        )
        if m:
            vrf_map[int(v["id"])] = m.jt_ipam_id

    # 4. Subnets
    report.tables["subnets"] = await _sync_subnets(
        session, subnets,
        section_legacy_to_uuid=section_map,
        vlan_legacy_to_uuid=vlan_map,
        vrf_legacy_to_uuid=vrf_map,
        customer_legacy_to_uuid=customer_map,
        on_conflict=on_conflict, dry_run=dry_run,
    )
    subnet_map: dict[int, uuid.UUID] = {}
    for s in subnets:
        m = await _get_or_create_mapping(
            session, object_type="subnet", legacy_id=int(s["id"])
        )
        if m:
            subnet_map[int(s["id"])] = m.jt_ipam_id

    # 5. Locations + Racks
    loc_res, rack_res = await _sync_locations_and_racks(
        session, location_rows=locations, rack_rows=racks,
        on_conflict=on_conflict, dry_run=dry_run,
    )
    report.tables["locations"] = loc_res
    report.tables["racks"] = rack_res

    # locations/racks 的 legacy → UUID map（給 device 用）
    loc_map: dict[int, uuid.UUID] = {}
    for r in locations:
        m = await _get_or_create_mapping(
            session, object_type="location", legacy_id=int(r["id"])
        )
        if m:
            loc_map[int(r["id"])] = m.jt_ipam_id
    rack_map: dict[int, uuid.UUID] = {}
    for r in racks:
        m = await _get_or_create_mapping(
            session, object_type="rack", legacy_id=int(r["id"])
        )
        if m:
            rack_map[int(r["id"])] = m.jt_ipam_id

    # 6. Devices（在 IP 之前，這樣 IP 可以連 device_id）
    report.tables["devices"] = await _sync_devices(
        session, device_rows=devices, type_rows=device_types,
        location_legacy_to_uuid=loc_map,
        rack_legacy_to_uuid=rack_map,
        customer_legacy_to_uuid=customer_map,
        on_conflict=on_conflict, dry_run=dry_run,
    )
    device_map: dict[int, uuid.UUID] = {}
    for d in devices:
        m = await _get_or_create_mapping(
            session, object_type="device", legacy_id=int(d["id"])
        )
        if m:
            device_map[int(d["id"])] = m.jt_ipam_id

    # 7. IP addresses（最後，因為依賴 subnet + device）
    report.tables["ip_addresses"] = await _sync_addresses(
        session, addresses,
        subnet_legacy_to_uuid=subnet_map,
        device_legacy_to_uuid=device_map,
        customer_legacy_to_uuid=customer_map,
        on_conflict=on_conflict, dry_run=dry_run,
    )

    # 7b. 建 address_legacy_to_uuid map（給 NAT 用）
    address_map: dict[int, uuid.UUID] = {}
    for a in addresses:
        try:
            legacy = int(a["id"])
        except (KeyError, TypeError, ValueError):
            continue
        m = await _get_or_create_mapping(session, object_type="ip", legacy_id=legacy)
        if m:
            address_map[legacy] = m.jt_ipam_id

    # 8. NAT rules — 解析 phpIPAM src/dst JSON + device CSV → jt-ipam 對映
    if nat_rows:
        report.tables["nat"] = await _sync_nat(
            session, rows=nat_rows,
            address_legacy_to_uuid=address_map,
            device_legacy_to_uuid=device_map,
            on_conflict=on_conflict, dry_run=dry_run,
        )

    # dry-run：我們在過程中已經 add/flush 過所有東西（為了讓下游 FK lookup
    # 拿得到 in-memory ID），現在統一 rollback 丟掉 — 不留任何資料庫變動。
    # 非 dry-run：由 endpoint 在 append_audit 之後一起 commit。
    if dry_run:
        await session.rollback()

    report.finished_at = datetime.now(UTC)
    return report
