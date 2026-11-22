"""DHCP 固定分配（reservation / static mapping）的共用寫入層。

為什麼要做這件事：`192.168.123.187` 那次「macOS 的資料跑到 Linux VM 上」，根因是 DHCP
位址被回收再發給別台機器。**有固定分配的位址不會被換人用**，所以「這個位址是不是綁死的」
是追查這類問題時的關鍵資訊，畫面上要看得到。
"""
from __future__ import annotations

import uuid

import pytest
from app.models.address import IPAddress
from app.models.dhcp import DHCPReservation
from app.models.section import Section
from app.models.subnet import Subnet
from app.services.dhcp_reservations import Reservation, replace_reservations
from sqlalchemy import select


async def _ip(db_session, addr):
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    sn = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    db_session.add(sn)
    await db_session.flush()
    ipa = IPAddress(subnet_id=sn.id, ip=addr)
    db_session.add(ipa)
    await db_session.flush()
    return ipa


@pytest.mark.anyio
async def test_reservation_marks_the_ip(db_session):
    ipa = await _ip(db_session, "198.51.100.20")
    n = await replace_reservations(
        db_session, source_type="opnsense", source_id=uuid.uuid4(),
        source_name="fw", engine="kea",
        rows=[Reservation(ip="198.51.100.20", mac="AA-BB-CC-DD-EE-FF", hostname="h1")])
    await db_session.flush()
    assert n == 1
    await db_session.refresh(ipa)
    assert ipa.dhcp_reserved is True
    row = (await db_session.execute(select(DHCPReservation))).scalars().first()
    assert row.mac == "aa:bb:cc:dd:ee:ff", "MAC 要正規化成小寫冒號式，才能跟其他來源比對"
    assert row.ip_address_id == ipa.id


@pytest.mark.anyio
async def test_entry_without_ip_is_skipped(db_session):
    """實機上有「只認網卡、沒指定 IP」的靜態對映 —— 那不是保留位址，不能算。"""
    n = await replace_reservations(
        db_session, source_type="opnsense", source_id=uuid.uuid4(),
        source_name="fw", engine="isc",
        rows=[Reservation(ip="", mac="aa:bb:cc:dd:ee:01", hostname="vdi2")])
    assert n == 0


@pytest.mark.anyio
async def test_removing_a_reservation_clears_the_flag(db_session):
    """來源移除固定分配後，旗標要跟著清掉 —— 否則畫面會一直顯示一個不存在的設定。"""
    ipa = await _ip(db_session, "198.51.100.21")
    src = uuid.uuid4()
    await replace_reservations(db_session, source_type="opnsense", source_id=src,
                               source_name="fw", engine="kea",
                               rows=[Reservation(ip="198.51.100.21", mac="aa:bb:cc:dd:ee:02")])
    await db_session.flush()
    await db_session.refresh(ipa)
    assert ipa.dhcp_reserved is True

    await replace_reservations(db_session, source_type="opnsense", source_id=src,
                               source_name="fw", engine="kea", rows=[])
    await db_session.flush()
    await db_session.refresh(ipa)
    assert ipa.dhcp_reserved is False


@pytest.mark.anyio
async def test_one_source_does_not_wipe_another(db_session):
    """鏡像同步只能清自己的列 —— 兩台 DHCP 各自保留不同位址時不可互相刪除。"""
    a = await _ip(db_session, "198.51.100.22")
    b = await _ip(db_session, "198.51.100.23")
    src_a, src_b = uuid.uuid4(), uuid.uuid4()
    await replace_reservations(db_session, source_type="opnsense", source_id=src_a,
                               source_name="fw-a", engine="kea",
                               rows=[Reservation(ip="198.51.100.22", mac="aa:bb:cc:dd:ee:03")])
    await replace_reservations(db_session, source_type="pfsense", source_id=src_b,
                               source_name="fw-b", engine="pfsense",
                               rows=[Reservation(ip="198.51.100.23", mac="aa:bb:cc:dd:ee:04")])
    await db_session.flush()
    await db_session.refresh(a)
    await db_session.refresh(b)
    assert a.dhcp_reserved is True and b.dhcp_reserved is True
    assert len((await db_session.execute(select(DHCPReservation))).scalars().all()) == 2


@pytest.mark.anyio
async def test_duplicate_rows_are_collapsed(db_session):
    await _ip(db_session, "198.51.100.24")
    n = await replace_reservations(
        db_session, source_type="opnsense", source_id=uuid.uuid4(),
        source_name="fw", engine="isc",
        rows=[Reservation(ip="198.51.100.24", mac="aa:bb:cc:dd:ee:05"),
              Reservation(ip="198.51.100.24", mac="AA:BB:CC:DD:EE:05")])
    assert n == 1


@pytest.mark.anyio
async def test_reservation_for_unknown_ip_is_kept_but_unlinked(db_session):
    """IPAM 裡還沒有這個位址時仍要留著記錄（只比對不新建），只是連不到物件。"""
    n = await replace_reservations(
        db_session, source_type="opnsense", source_id=uuid.uuid4(),
        source_name="fw", engine="kea",
        rows=[Reservation(ip="198.51.100.250", mac="aa:bb:cc:dd:ee:06")])
    assert n == 1
    row = (await db_session.execute(select(DHCPReservation).where(
        DHCPReservation.ip == "198.51.100.250"))).scalars().first()
    assert row is not None and row.ip_address_id is None
