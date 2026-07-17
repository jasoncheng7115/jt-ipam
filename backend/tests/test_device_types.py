"""issue #21：patch_panel / pdu / ups 裝置類型 + LibreNMS 分類對應（純單元，無 DB）。"""

from __future__ import annotations

from app.schemas.device import _VALID_TYPES
from app.services.librenms import _infer_device_type


class _L:
    """最小 LibreNMSDevice 樣板（只帶 _infer_device_type 用到的欄位）。"""

    def __init__(self, **kw):
        for a in ("os", "hardware", "sysObjectID", "type"):
            setattr(self, a, kw.get(a))


def test_new_types_are_valid():
    for t in ("patch_panel", "pdu", "ups"):
        assert t in _VALID_TYPES


def test_infer_power_devices():
    assert _infer_device_type(_L(hardware="APC Smart-UPS 1500")) == "ups"
    assert _infer_device_type(_L(hardware="Eaton 9PX UPS")) == "ups"
    assert _infer_device_type(_L(hardware="Raritan PX3-5190R PDU")) == "pdu"
    assert _infer_device_type(_L(hardware="ServerTech Switched CDU")) == "pdu"
    # LibreNMS 原生 type=power，無 pdu 關鍵字 → 預設 UPS
    assert _infer_device_type(_L(type="power")) == "ups"


def test_infer_native_type_fallback():
    assert _infer_device_type(_L(type="wireless")) == "ap"
    assert _infer_device_type(_L(type="storage")) == "storage"
    assert _infer_device_type(_L(type="firewall")) == "firewall"
    assert _infer_device_type(_L(type="network")) == "switch"
    assert _infer_device_type(_L(type="printer")) == "other"


def test_infer_keyword_still_wins_over_native():
    # 關鍵字比原生 type 更明確時以關鍵字為準
    assert _infer_device_type(_L(hardware="FortiGate 60F", type="network")) == "firewall"
    assert _infer_device_type(_L(hardware="Cisco Catalyst 9300", type="network")) == "switch"


def test_infer_unknown_is_other():
    assert _infer_device_type(_L()) == "other"
    assert _infer_device_type(_L(hardware="mystery box")) == "other"
