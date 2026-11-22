"""重疊網段下，整合比對不到唯一的 IP 物件時要「跳過」，不能任意挑一筆。

情境：甲、乙兩個單位都用 192.168.123.0/24。同一個 IP 字串在 IPAM 裡是**兩台不同機器**，
光靠 IP 分不出來。原本的作法是把候選塞進 dict（後者覆寫前者）或 `.limit(1)` —— 等於
依資料庫回傳順序任意挑一筆。

掛錯比沒有更糟：**沒有資料你會去查，掛錯了你不會知道**；而且在多單位環境下，甲單位的
主機名稱／OS 會出現在乙單位的紀錄上 —— 那是跨單位的資料外洩。

所以：能唯一對應才寫入；不明確就跳過並計數（讓使用者知道要去設定「限定子網路範圍」）。
這與 Proxmox 用 MAC 比對時「多筆視為不明確、不猜」是同一個原則。
"""
from __future__ import annotations

import uuid

import pytest
from app.models.address import IPAddress
from app.models.section import Section
from app.models.subnet import Subnet
from app.services.wazuh import build_ip_map


@pytest.fixture
async def two_customers(db_session):
    """兩個單位、同樣的 CIDR、同樣的 IP —— 真實的多租戶重疊情境。"""
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    a = Subnet(section_id=sec.id, cidr="198.51.100.0/24", description="甲單位")
    b = Subnet(section_id=sec.id, cidr="198.51.100.0/24", description="乙單位")
    db_session.add_all([a, b])
    await db_session.flush()
    ips = []
    for sn in (a, b):
        ipa = IPAddress(subnet_id=sn.id, ip="198.51.100.10")
        db_session.add(ipa)
        ips.append(ipa)
    # 只存在於甲單位的位址（不重疊）
    only_a = IPAddress(subnet_id=a.id, ip="198.51.100.11")
    db_session.add(only_a)
    await db_session.commit()
    return a, b, ips, only_a


@pytest.mark.anyio
async def test_ambiguous_ip_is_skipped(db_session, two_customers):
    """同一個 IP 對應到兩筆 → 不對應到任何一筆，並計入「不明確」。"""
    _a, _b, _ips, _only = two_customers
    ip_map, ambiguous = await build_ip_map(db_session, scope_ids=set())
    assert "198.51.100.10" not in ip_map, "不明確時不可以挑一筆硬塞"
    assert "198.51.100.10" in ambiguous


@pytest.mark.anyio
async def test_unambiguous_ip_still_maps(db_session, two_customers):
    """只有一筆的位址照常對應 —— 修正不該讓正常情況失效。"""
    _a, _b, _ips, only_a = two_customers
    ip_map, _amb = await build_ip_map(db_session, scope_ids=set())
    assert ip_map.get("198.51.100.11") == only_a.id


@pytest.mark.anyio
async def test_scope_resolves_the_ambiguity(db_session, two_customers):
    """設定「限定子網路範圍」之後，候選只剩一筆 → 恢復可以對應。

    這正是限定範圍存在的意義：多單位共用同一個 CIDR 時，一個整合實例只該負責一個單位。
    """
    a, _b, ips, _only = two_customers
    ip_map, ambiguous = await build_ip_map(db_session, scope_ids={a.id})
    assert ip_map.get("198.51.100.10") == ips[0].id
    assert not ambiguous


@pytest.mark.anyio
async def test_librenms_also_refuses_ambiguous(db_session, two_customers):
    """LibreNMS 走的是另一段程式碼，同樣的原則要一起套用（否則只修一半）。"""
    from app.models.librenms import LibreNMSDevice, LibreNMSInstance
    from app.services.librenms import link_librenms_device

    from app.models.device import Device

    a, _b, ips, _only = two_customers
    # 兩筆**都**要連到裝置（各自不同的一台）。
    # 只給其中一筆的話，舊寫法有一半機率剛好挑到沒有裝置的那筆而回 None，
    # 測試就會時紅時綠 —— 而「挑到哪一筆看資料庫順序」正是這個 bug 本身。
    devs = []
    for _ in range(2):
        d = Device(name=f"dev-{uuid.uuid4().hex[:8]}", type="server")
        db_session.add(d)
        devs.append(d)
    await db_session.flush()
    ips[0].device_id = devs[0].id
    ips[1].device_id = devs[1].id
    await db_session.flush()

    inst = LibreNMSInstance(name=f"ln-{uuid.uuid4().hex[:6]}", api_url="https://x",
                            api_token_enc=b"a", api_token_nonce=b"b")
    db_session.add(inst)
    await db_session.flush()
    ldev = LibreNMSDevice(instance_id=inst.id, legacy_device_id=1,
                          hostname="dup", primary_ip="198.51.100.10")
    db_session.add(ldev)
    await db_session.flush()

    # 這個位址在 IPAM 裡對到兩筆（甲、乙單位各一），兩筆各自連著不同的裝置。
    # 分不出來是誰的時候，**不可以**隨便接到其中一台上。
    dev_id, created = await link_librenms_device(
        db_session, ldev, create=False, scope_ids=set())
    assert dev_id is None and created is False
