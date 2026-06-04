"""裝置型號 (model) 來源優先序：依設定挑型號、可停用來源、manual 不可停用。"""

from __future__ import annotations

from app.services.model_precedence import (
    DEFAULT_MODEL_ORDER,
    get_model_disabled,
    pick_model,
    resolve_device_model,
    set_model_precedence,
)


def test_pick_model_follows_order():
    order = ["manual", "librenms", "proxmox", "opnsense"]
    # 都有 → 取最高優先（manual）
    assert pick_model({"manual": "DGS-1510", "librenms": "Generic x86"}, order, []) == "DGS-1510"
    # 沒 manual → 取次高（librenms）
    assert pick_model({"librenms": "CRS309-1G-8S+"}, order, []) == "CRS309-1G-8S+"


def test_pick_model_skips_disabled_and_empty():
    order = ["manual", "librenms", "proxmox"]
    assert pick_model({"librenms": "a", "proxmox": "b"}, order, ["librenms"]) == "b"
    assert pick_model({"manual": "  ", "librenms": "c"}, order, []) == "c"
    assert pick_model({}, order, []) is None


async def test_set_get_and_resolve(db_session):
    await set_model_precedence(
        db_session, order=["proxmox", "librenms", "manual"], disabled=["opnsense"],
    )
    # manual 不能停用
    assert "manual" not in await get_model_disabled(db_session)
    model = await resolve_device_model(db_session, {"proxmox": "Super Server", "librenms": "x86"})
    assert model == "Super Server"


def test_default_order_has_manual_first():
    assert DEFAULT_MODEL_ORDER[0] == "manual"
