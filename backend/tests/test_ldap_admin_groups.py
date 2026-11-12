"""LDAP「管理員群組」的比對（客戶 2026-11-12：設了對應，登入登出都還是一般使用者）。

以前只認「完整 DN、直接所屬」。設定值寫成群組名稱、或使用者是經由別的群組間接屬於管理員群組，
都永遠比對不到，而且畫面上看不出原因。真的 AD 的巢狀群組在 tests/test_ldap_real_directory.py。
"""
from __future__ import annotations

from typing import Any

from app.services import ldap_auth
from app.services.system_config import LdapConfig


def _cfg(*groups: str) -> LdapConfig:
    return LdapConfig(enabled=True, server="ldap.example.net", port=389, use_ssl=False, use_starttls=False,
                      bind_dn="", bind_password="", search_base="DC=corp,DC=test", user_filter="(sAMAccountName={username})",
                      attr_email="mail", attr_display_name="displayName", attr_member_of="memberOf",
                      admin_groups=list(groups), timeout=5.0)


USER = "CN=Nest Nine,CN=Users,DC=corp,DC=test"
DIRECT = ["CN=Net Team,OU=Groups,DC=corp,DC=test", "CN=Domain Users,CN=Users,DC=corp,DC=test"]


class _Conn:
    """只回答 in-chain 查詢：群組 `CN=IPAM Admins,…` 間接包含 USER。"""

    def __init__(self, supports_chain: bool = True) -> None:
        self.supports_chain = supports_chain
        self.entries: list[Any] = []
        self.searches: list[tuple[str, str]] = []

    def search(self, search_base: str, search_filter: str, **_: Any) -> None:
        self.searches.append((search_base, search_filter))
        if not self.supports_chain:
            raise ldap_auth.LDAPException("unsupported matching rule")
        hit = "1.2.840.113556.1.4.1941" in search_filter and (
            search_base.lower() == "cn=ipam admins,ou=groups,dc=corp,dc=test" or "(cn=IPAM Admins)" in search_filter)
        self.entries = [object()] if hit else []


def test_full_dn_with_spaces_matches_directly() -> None:
    via = ldap_auth.resolve_admin(_cfg("CN=Net Team, OU=Groups, DC=corp, DC=test"), None, USER, DIRECT)
    assert via == {"group": "CN=Net Team, OU=Groups, DC=corp, DC=test", "via": "direct"}


def test_group_name_instead_of_dn_matches() -> None:
    via = ldap_auth.resolve_admin(_cfg("net team"), None, USER, DIRECT)
    assert via == {"group": "net team", "via": "name"}


def test_nested_group_by_dn_and_by_name() -> None:
    assert ldap_auth.resolve_admin(_cfg("CN=IPAM Admins,OU=Groups,DC=corp,DC=test"), _Conn(), USER, DIRECT) == {
        "group": "CN=IPAM Admins,OU=Groups,DC=corp,DC=test", "via": "nested"}
    c = _Conn()
    assert ldap_auth.resolve_admin(_cfg("IPAM Admins"), c, USER, DIRECT) == {"group": "IPAM Admins", "via": "nested"}
    assert c.searches[0][0] == "DC=corp,DC=test"           # 名稱從網域根往下找


def test_no_match_and_no_mapping() -> None:
    assert ldap_auth.resolve_admin(_cfg("CN=Other,DC=corp,DC=test"), _Conn(), USER, DIRECT) is None
    assert ldap_auth.resolve_admin(_cfg(), _Conn(), USER, DIRECT) is None
    assert ldap_auth.resolve_admin(_cfg("", "  "), _Conn(), USER, DIRECT) is None
    # OpenLDAP 不支援 in-chain：查詢丟例外也只是沒比對到
    assert ldap_auth.resolve_admin(_cfg("IPAM Admins"), _Conn(supports_chain=False), USER, DIRECT) is None


def test_name_match_does_not_touch_the_value_inside_escaped_commas() -> None:
    groups = [r"CN=Ops\, Night,OU=Groups,DC=corp,DC=test"]
    assert ldap_auth.resolve_admin(_cfg("Ops, Night"), None, USER, groups) == {"group": "Ops, Night", "via": "name"}
    assert ldap_auth.resolve_admin(_cfg("Ops"), None, USER, groups) is None
