"""LDAP／AD 登入：對真的目錄伺服器跑完整流程（客戶 2026-10-21）。

為什麼要有這一支：先前的 LDAP 測試都是模擬物件，DN 一律沒有空白、篩選條件一律是我們自己的寫法，
所以客戶照 AD 工具複製的 `CN=svc, CN=Users, DC=corp, DC=example`、照預設值留下的 `(uid=…)`、
漏了大括號的 `(sAMAccountName=username{})` 全部要到客戶那邊才爆；Samba AD 不支援 Who am I 擴充操作
讓整個「測試連線」失敗，也只有接真的伺服器才看得到。

沒有設定測試靶時整支略過。發版前要跑（TEST_CHECKLIST「LDAP／AD 真機」）：

    bash scripts/kerberos-test-kdc/run.sh        # Samba AD（CORP.TEST），印出容器 IP
    docker run -d --name jtipam-openldap-dn -p 127.0.0.1:38390:389 -e LDAP_DOMAIN=example.org \\
      -e LDAP_ADMIN_PASSWORD=Admin-Pass-2026 -e LDAP_TLS=false osixia/openldap:1.5.0
    # （OpenLDAP 的測試帳號 alice07 由下面的 fixture 建立）
    JTIPAM_TEST_AD_HOST=<容器 IP> JTIPAM_TEST_OPENLDAP=127.0.0.1:38390 pytest tests/test_ldap_real_directory.py
"""
from __future__ import annotations

import os
from typing import Any

import pytest
from app.services import ldap_auth
from app.services.system_config import LdapConfig

pytestmark = pytest.mark.anyio

AD_HOST = os.environ.get("JTIPAM_TEST_AD_HOST")
OPENLDAP = os.environ.get("JTIPAM_TEST_OPENLDAP")


@pytest.fixture(autouse=True)
def _allow_test_target(monkeypatch):
    # 測試靶在 docker 內部網路／迴路位址：出站檢查只在這支測試放行
    monkeypatch.setattr("app.core.net_guard.check_target", lambda *a, **k: None)


def _ad(**k: Any) -> LdapConfig:
    """客戶實際的寫法：DN 逗號後有空白、管理員群組名稱裡也有空白。"""
    base: dict[str, Any] = dict(
        enabled=True, server=AD_HOST, port=389, use_ssl=False, use_starttls=False,
        bind_dn="CN=Administrator, CN=Users, DC=corp, DC=test", bind_password="Adm1n-Passw0rd!",
        search_base="DC=corp, DC=test", user_filter="(sAMAccountName={username})",
        attr_email="mail", attr_display_name="displayName", attr_member_of="memberOf",
        admin_groups=["CN=IPAM Admins, CN=Users, DC=corp, DC=test"], timeout=10.0)
    base.update(k)
    return LdapConfig(**base)


needs_ad = pytest.mark.skipif(not AD_HOST, reason="JTIPAM_TEST_AD_HOST not set (Samba AD test target)")
needs_openldap = pytest.mark.skipif(not OPENLDAP, reason="JTIPAM_TEST_OPENLDAP not set")


@needs_ad
async def test_ad_connection_test_detects_ad_and_warns_about_uid_filter() -> None:
    r = await ldap_auth.test_connection(_ad(user_filter="(uid={username})"))
    assert r["bound"] is True
    assert r["directory"] == "ad"
    assert [w["code"] for w in r["warnings"]] == ["ldap_warn_uid_filter_on_ad"]
    assert (await ldap_auth.test_connection(_ad()))["warnings"] == []


@needs_ad
async def test_ad_connection_test_reports_a_wrong_search_base() -> None:
    r = await ldap_auth.test_connection(_ad(search_base="DC=nope, DC=test"))
    assert [w["code"] for w in r["warnings"]] == ["ldap_warn_search_base"]


@needs_ad
@pytest.mark.parametrize("flt", ["(sAMAccountName={username})", " ( sAMAccountName = { username } ) "])
async def test_ad_login_with_spaced_dns(flt: str) -> None:
    info = await ldap_auth.authenticate(_ad(user_filter=flt), "user07", "User07-Pass-2026!")
    assert info.dn == "CN=Test User07,CN=Users,DC=corp,DC=test"      # 值裡的空白照原樣
    assert info.display_name == "Test User07"
    assert info.email == "user07@corp.test"
    assert info.is_admin is True                                       # 群組 DN 有空白也比對得到
    plain = await ldap_auth.authenticate(_ad(user_filter=flt), "plain08", "Plain08-Pass-2026!")
    assert plain.is_admin is False


@needs_ad
@pytest.mark.parametrize("groups", [
    ["CN=IPAM Admins, CN=Users, DC=corp, DC=test"],    # 完整 DN（逗號後空白）
    ["ipam admins"],                                    # 只寫群組名稱（客戶 2026-11-12）
])
async def test_ad_admin_group_by_dn_or_name_and_through_a_nested_group(groups: list[str]) -> None:
    """nest09 只在「Net Team」裡，「Net Team」是「IPAM Admins」的成員：memberOf 看不到 IPAM Admins，
    要用 AD 的 in-chain 規則才查得到。以前這種帳號登入登出都還是一般使用者。"""
    cfg = _ad(admin_groups=groups)
    direct = await ldap_auth.authenticate(cfg, "user07", "User07-Pass-2026!")
    assert direct.is_admin is True
    assert direct.admin_via["via"] in ("direct", "name")  # type: ignore[index]
    nested = await ldap_auth.authenticate(cfg, "nest09", "Nest09-Pass-2026!")
    assert nested.groups == ["CN=Net Team,CN=Users,DC=corp,DC=test"]
    assert nested.admin_via == {"group": groups[0], "via": "nested"}
    plain = await ldap_auth.authenticate(cfg, "plain08", "Plain08-Pass-2026!")
    assert plain.is_admin is False
    assert plain.admin_via is None


@needs_ad
@pytest.mark.parametrize("flt", ["(uid={username})", "(sAMAccountName=username)", "(sAMAccountName=username{})"])
async def test_ad_customer_filters_fail_cleanly(flt: str) -> None:
    """客戶這天試過的三種寫法：一律是「找不到使用者」，不可以是 DN 錯誤或 IndexError。"""
    with pytest.raises(ldap_auth.LDAPInvalidCredentials):
        await ldap_auth.authenticate(_ad(user_filter=flt), "user07", "User07-Pass-2026!")


@needs_ad
async def test_ad_wrong_password() -> None:
    with pytest.raises(ldap_auth.LDAPInvalidCredentials):
        await ldap_auth.authenticate(_ad(), "user07", "wrong-password")


@pytest.fixture
def openldap_user() -> None:
    from ldap3 import Connection, Server
    host, port = OPENLDAP.split(":")  # type: ignore[union-attr]
    c = Connection(Server(host, port=int(port)), user="cn=admin,dc=example,dc=org", password="Admin-Pass-2026",
                   auto_bind=True)
    c.add("ou=People,dc=example,dc=org", "organizationalUnit")
    c.add("uid=alice07,ou=People,dc=example,dc=org", ["inetOrgPerson"],
          {"uid": "alice07", "cn": "Alice Seven", "sn": "Seven", "mail": "alice07@example.org",
           "displayName": "Alice Seven", "userPassword": "Alice07-Pass-2026"})
    c.unbind()


def _openldap(flt: str) -> LdapConfig:
    host, port = OPENLDAP.split(":")  # type: ignore[union-attr]
    return LdapConfig(enabled=True, server=host, port=int(port), use_ssl=False, use_starttls=False,
                      bind_dn="cn=admin, dc=example, dc=org", bind_password="Admin-Pass-2026",
                      search_base="ou=People, dc=example, dc=org", user_filter=flt, attr_email="mail",
                      attr_display_name="displayName", attr_member_of="memberOf", admin_groups=[], timeout=10.0)


@needs_openldap
async def test_openldap_is_not_ad_and_uid_works(openldap_user) -> None:
    r = await ldap_auth.test_connection(_openldap("( uid = {username} )"))
    assert r["directory"] == "ldap"
    assert r["warnings"] == []
    info = await ldap_auth.authenticate(_openldap("( uid = { username } )"), "alice07", "Alice07-Pass-2026")
    assert info.dn == "uid=alice07,ou=People,dc=example,dc=org"
    assert info.email == "alice07@example.org"
