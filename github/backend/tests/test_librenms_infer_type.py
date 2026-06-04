"""LibreNMS → jt-ipam Device.type 粗推：涵蓋本次擴充（unifi→ap、dlink/mellanox→switch、
server OS→server）與「AP 要排在 switch 之前」的排序陷阱。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.librenms import _infer_device_type


def _dev(os="", hardware="", sysobj=""):
    return SimpleNamespace(os=os, hardware=hardware, sysObjectID=sysobj)


@pytest.mark.parametrize(("dev", "expected"), [
    (_dev(os="opnsense"), "firewall"),
    (_dev(hardware="FortiGate 60F"), "firewall"),
    # unifi 同時含 wireless+switch 字樣 → 必須先判定為 ap
    (_dev(hardware="UniFi AP AC Pro", os="airos"), "ap"),
    (_dev(os="aruba"), "ap"),
    (_dev(hardware="D-Link DGS-1510"), "switch"),
    (_dev(hardware="Mellanox Onyx"), "switch"),
    (_dev(os="Cisco Catalyst"), "switch"),
    (_dev(os="routeros", hardware="MikroTik hEX"), "router"),
    (_dev(os="Ubuntu 22.04"), "server"),
    (_dev(os="VMware ESXi"), "server"),
    (_dev(hardware="Synology DSM"), "server"),
    (_dev(os="something weird"), "other"),
])
def test_infer_device_type(dev, expected):
    assert _infer_device_type(dev) == expected


def test_ap_takes_precedence_over_switch():
    # 明確同時含 switch 與 wireless → 仍應是 ap（順序保證）
    assert _infer_device_type(_dev(hardware="UniFi Switch with wireless uplink")) == "ap"
