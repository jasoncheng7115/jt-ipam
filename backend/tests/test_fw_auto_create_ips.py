"""DHCP 有、IPAM 沒有的位址：只有在「開了開關」且「落點唯一」時才建。

這個功能的價值在於少一步手動登記；它的風險在於**把不該收錄的東西收錄進來**
（有人私接一台機器、拿到租約，就被自動建成正式紀錄，而且從此不再出現在
「未授權 IP」異常偵測裡 —— 那道偵測的判定正是「ARP 看得到、IPAM 沒有」）。

所以這裡守的不是「會不會建」，而是**「不該建的時候有沒有忍住」**。
"""
from __future__ import annotations

import ipaddress
import uuid

import pytest

from app.services.ip_autocreate import pick_subnet_for_ip


def _nets(*cidrs: str):
    """照 addable_subnets 的輸出格式（依首碼長度由長到短）造候選。"""
    out = [(ipaddress.ip_network(c), uuid.uuid4()) for c in cidrs]
    out.sort(key=lambda x: x[0].prefixlen, reverse=True)
    return out


def test_picks_the_most_specific_subnet() -> None:
    """巢狀網段取最精確的那個，不是隨便一個包含它的。"""
    nets = _nets("10.0.0.0/8", "10.1.1.0/24")
    sid = pick_subnet_for_ip(nets, ipaddress.ip_address("10.1.1.5"))
    assert sid == next(s for n, s in nets if str(n) == "10.1.1.0/24")


def test_refuses_when_two_subnets_overlap_exactly() -> None:
    """重疊網段（兩個單位各有 192.168.123.0/24）→ 不建。

    這是本專案存在的理由：猜錯單位會讓資料靜靜掛到別人名下，比不建糟得多。
    """
    nets = _nets("192.168.123.0/24", "192.168.123.0/24")
    assert pick_subnet_for_ip(nets, ipaddress.ip_address("192.168.123.50")) is None


def test_refuses_when_no_subnet_contains_it() -> None:
    """不在任何既有子網路內 → 不建（更不會憑空生一個子網路出來）。"""
    nets = _nets("10.0.0.0/24")
    assert pick_subnet_for_ip(nets, ipaddress.ip_address("172.16.5.5")) is None


def test_empty_candidates_never_create() -> None:
    """沒設關聯子網路又沒有候選時不能亂建。"""
    assert pick_subnet_for_ip([], ipaddress.ip_address("10.0.0.1")) is None


@pytest.mark.anyio
async def test_scope_limits_the_candidates(db_session) -> None:
    """關聯子網路（scope）有設時，候選只會有那些 —— 這正是消除重疊歧義的方法。"""
    from app.models.section import Section
    from app.models.subnet import Subnet
    from app.services.ip_autocreate import addable_subnets

    sec = Section(name=f"sec-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    mine = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    theirs = Subnet(section_id=sec.id, cidr="203.0.113.0/24")
    db_session.add_all([mine, theirs])
    await db_session.flush()

    scoped = await addable_subnets(db_session, [mine.id])
    ids = {sid for _, sid in scoped}
    assert mine.id in ids
    assert theirs.id not in ids, "設了關聯子網路卻仍把別人的網段當候選"


@pytest.mark.anyio
async def test_virtualization_defaults_to_off_too(db_session) -> None:
    """Proxmox 與 VMware 也預設關。

    Proxmox 原本是**無條件自動建立**，改成開關後預設關 —— 這是刻意的行為變更：
    自動收錄會讓那些位址不再出現在「未授權 IP」偵測裡，該由使用者明示同意。
    """
    from app.models.esxi import ESXiInstance
    from app.models.virt import ProxmoxInstance

    px = ProxmoxInstance(api_url="https://192.0.2.3", auth_username="root@pam",
                         auth_token_id="jt")
    esx = ESXiInstance(name=f"esx-{uuid.uuid4().hex[:6]}", api_url="https://192.0.2.4",
                       username="ro", password_enc=b"x", password_nonce=b"y")
    db_session.add_all([px, esx])
    await db_session.flush()
    assert px.auto_create_ips is False
    assert esx.auto_create_ips is False


@pytest.mark.anyio
async def test_default_is_off_on_both_firewalls(db_session) -> None:
    """預設必須是關閉的 —— 升級後不該有人突然發現 IPAM 多出一批沒登記過的機器。"""
    from app.models.firewall import OPNsenseFirewall
    from app.models.pfsense import PfSenseFirewall

    opn = OPNsenseFirewall(name=f"opn-{uuid.uuid4().hex[:6]}", api_url="https://192.0.2.1",
                           api_key_enc=b"x", api_key_nonce=b"y",
                           api_secret_enc=b"x", api_secret_nonce=b"y")
    pf = PfSenseFirewall(name=f"pf-{uuid.uuid4().hex[:6]}", api_url="https://192.0.2.2",
                         api_key_enc=b"x", api_key_nonce=b"y")
    db_session.add_all([opn, pf])
    await db_session.flush()
    assert opn.auto_create_ips is False
    assert pf.auto_create_ips is False
