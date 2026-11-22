"""OPNsense legacy config.xml 的布林欄位解析。

config.xml 有**兩種**寫法，兩種都要吃：

- 舊式「存在即為真」：`<disabled/>`（沒這個元素就是啟用）
- 新式明寫值　　　　：`<disabled>0</disabled>` / `<disabled>1</disabled>`

原本只判斷元素存不存在，遇到新式寫法時 `<disabled>0</disabled>` 也算成停用 ——
實機上整份 NAT 規則（44 筆）全被標成停用，NAT 頁面因此全灰，
而任何「找出對外開放的規則」的功能都會靜靜地回 0 筆。
"""
from __future__ import annotations

from app.services.opnsense_firewall import _parse_legacy_nat_xml

EXPLICIT = """<opnsense><nat>
  <rule>
    <disabled>0</disabled><nordr>0</nordr><log>1</log>
    <interface>wan</interface><protocol>tcp</protocol>
    <target>192.168.124.1</target><local-port>443</local-port>
    <descr>啟用中的轉發</descr>
    <associated-rule-id>r1</associated-rule-id>
  </rule>
  <rule>
    <disabled>1</disabled><nordr>0</nordr><log>0</log>
    <interface>wan</interface><protocol>tcp</protocol>
    <target>192.168.124.2</target><local-port>22</local-port>
    <descr>已停用的轉發</descr>
    <associated-rule-id>r2</associated-rule-id>
  </rule>
</nat></opnsense>"""

PRESENCE = """<opnsense><nat>
  <rule>
    <interface>wan</interface><protocol>tcp</protocol>
    <target>192.168.124.3</target><local-port>80</local-port>
    <descr>舊式：沒有 disabled 元素 = 啟用</descr>
    <associated-rule-id>r3</associated-rule-id>
  </rule>
  <rule>
    <disabled/>
    <interface>wan</interface><protocol>tcp</protocol>
    <target>192.168.124.4</target><local-port>81</local-port>
    <descr>舊式：有 disabled 元素 = 停用</descr>
    <associated-rule-id>r4</associated-rule-id>
  </rule>
</nat></opnsense>"""


def test_explicit_zero_means_enabled():
    """`<disabled>0</disabled>` 是啟用 —— 這正是實機的寫法，也是原本壞掉的地方。"""
    rules = {r["uuid"]: r for r in _parse_legacy_nat_xml(EXPLICIT)}
    assert rules["r1"]["disabled"] is False
    assert rules["r2"]["disabled"] is True


def test_explicit_values_apply_to_other_flags_too():
    rules = {r["uuid"]: r for r in _parse_legacy_nat_xml(EXPLICIT)}
    assert rules["r1"]["nordr"] is False
    assert rules["r1"]["log"] is True
    assert rules["r2"]["log"] is False


def test_presence_only_style_still_works():
    """舊式設定檔不能因為修新式而壞掉 —— 沒有元素＝啟用，有空元素＝停用。"""
    rules = {r["uuid"]: r for r in _parse_legacy_nat_xml(PRESENCE)}
    assert rules["r3"]["disabled"] is False
    assert rules["r4"]["disabled"] is True


def test_other_fields_still_parsed():
    rules = {r["uuid"]: r for r in _parse_legacy_nat_xml(EXPLICIT)}
    assert rules["r1"]["target"] == "192.168.124.1"
    assert rules["r1"]["target_port"] == "443"
    assert rules["r1"]["interface"] == "wan"
    assert rules["r1"]["description"] == "啟用中的轉發"


def test_truthy_handles_the_string_zero():
    """新 API 會回字串 "0" —— bool("0") 是 True，這是同一個坑的另一半。"""
    from app.services.opnsense_firewall import _truthy
    assert _truthy("0") is False
    assert _truthy("1") is True
    assert _truthy(0) is False
    assert _truthy(None) is False
    assert _truthy(True) is True
    assert _truthy("") is False
    assert _truthy({"0": {"value": "x", "selected": 1}}) is True
    assert _truthy({"0": {"value": "x", "selected": 0}}) is False


# 介面有 <enable> 才是啟用 DHCP（跟發放範圍同一條規則；2026-10-13 起停用介面的固定分配不算）
STATIC_MAPS = """<opnsense><dhcpd>
  <lan>
    <enable>1</enable>
    <staticmap>
      <mac>00:11:22:33:44:55</mac><ipaddr>192.168.124.53</ipaddr>
      <hostname>voip-phone-01</hostname><descr>櫃檯話機</descr>
    </staticmap>
    <staticmap>
      <mac>62:aa:87:68:fa:f1</mac><hostname>vdi2</hostname><descr>SOSI</descr>
    </staticmap>
  </lan>
  <opt1>
    <enable>1</enable>
    <staticmap><mac>aa:bb:cc:dd:ee:ff</mac><ipaddr>10.0.0.5</ipaddr></staticmap>
  </opt1>
</dhcpd></opnsense>"""


def test_static_maps_parsed_across_interfaces():
    """固定分配掛在各介面底下（lan / opt1…），不是只有 lan 要看。"""
    from app.services.opnsense_firewall import _parse_legacy_static_maps
    rows = _parse_legacy_static_maps(STATIC_MAPS)
    ips = {r.ip for r in rows}
    assert ips == {"192.168.124.53", "10.0.0.5"}


def test_static_map_without_ip_is_skipped():
    """沒有 <ipaddr> 的是「認得這張網卡」，沒有保留位址 —— 實機上真的有這種資料。"""
    from app.services.opnsense_firewall import _parse_legacy_static_maps
    rows = _parse_legacy_static_maps(STATIC_MAPS)
    assert not [r for r in rows if r.hostname == "vdi2"]


def test_static_map_fields():
    from app.services.opnsense_firewall import _parse_legacy_static_maps
    r = next(x for x in _parse_legacy_static_maps(STATIC_MAPS) if x.ip == "192.168.124.53")
    assert r.mac == "00:11:22:33:44:55"
    assert r.hostname == "voip-phone-01"
    assert r.description == "櫃檯話機"
