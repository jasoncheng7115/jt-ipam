"""裝置名稱來源優先序：依設定挑名稱、可停用來源、manual 不可停用。"""

from __future__ import annotations

from app.services.device_name_precedence import (
    DEFAULT_DEVNAME_ORDER,
    get_devname_disabled,
    pick_name,
    resolve_device_name,
    set_devname_precedence,
)


def test_pick_name_follows_order():
    order = ["manual", "librenms", "dns", "proxmox", "opnsense", "snmp"]
    # 都有 → 取最高優先（manual）
    assert pick_name({"manual": "sw1", "librenms": "SW1.dc", "dns": "x"}, order, []) == "sw1"
    # 沒 manual → 取次高（librenms）
    assert pick_name({"librenms": "SW1.dc", "dns": "x"}, order, []) == "SW1.dc"


def test_pick_name_skips_disabled_and_empty():
    order = ["manual", "librenms", "dns"]
    # librenms 停用 → 跳到 dns
    assert pick_name({"librenms": "a", "dns": "b"}, order, ["librenms"]) == "b"
    # 空字串不算
    assert pick_name({"manual": "  ", "librenms": "c"}, order, []) == "c"
    assert pick_name({}, order, []) is None


async def test_set_get_and_resolve(db_session):
    await set_devname_precedence(
        db_session, order=["dns", "librenms", "manual"], disabled=["snmp"],
    )
    # manual 不能停用；非法來源被濾掉
    assert "manual" not in await get_devname_disabled(db_session)
    # 依新順序：dns 最前
    name = await resolve_device_name(db_session, {"dns": "host.example.com", "librenms": "host"})
    assert name == "host.example.com"


def test_default_order_has_manual_first():
    assert DEFAULT_DEVNAME_ORDER[0] == "manual"
