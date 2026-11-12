"""LDAP / AD 認證。

phpIPAM 缺點：LDAP 設定散落、錯誤訊息不清、不支援 LDAPS+StartTLS 同時誤用。
jt-ipam：設定走 DB（管理區 UI）覆蓋 env；bind password 應用層加密；test endpoint。

OWASP 對應：
- A02：bind password 加密存 DB（AES-GCM）；TLS 預設啟用；不接受 verify=False
- A03：username 透過 ldap3.utils.conv.escape_filter_chars 跳脫 LDAP filter
- A07：認證失敗都回統一訊息；rate limit 沿用 /auth/login 的 auth bucket
"""

from __future__ import annotations

import asyncio
import logging
import re
import ssl
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ldap3 import (
    ALL,
    BASE,
    SUBTREE,
    Connection,
    Server,
    Tls,
)
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from app.core.ui_error import UiError, ui_detail

if TYPE_CHECKING:
    from app.services.system_config import LdapConfig

log = logging.getLogger("ldap_auth")


class LDAPAuthError(UiError):
    pass


class LDAPNotConfigured(LDAPAuthError):
    pass


class LDAPInvalidCredentials(LDAPAuthError):
    pass


def _match_hostname(cert: dict[str, Any], hostname: str) -> None:
    """憑證名稱比對：IP 比對 IP SAN，其他交給 ldap3 原本的比對。

    Python 3.12 拿掉了 ssl.match_hostname，ldap3 退回自帶的舊版，只看 DNS 名稱 —— 伺服器欄位填 IP 時，
    憑證就算有 IP SAN 也永遠「名稱不符」（2026-10-19 用測試靶查到）。"""
    import ipaddress
    try:
        ip = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        _l3_match_hostname(cert, hostname)
        return
    for kind, value in cert.get("subjectAltName", ()):
        if kind == "IP Address":
            try:
                if ipaddress.ip_address(value.strip()) == ip:
                    return
            except ValueError:
                continue
    # 要丟 ldap3 自己的 CertificateError：它的 check_hostname 只攔這個類別，才會繼續試其他名稱、回報「名稱不符」
    raise _l3tls.CertificateError(f"hostname {hostname!r} doesn't match any IP address in the certificate")


import ldap3.core.tls as _l3tls  # noqa: E402

_l3_match_hostname = _l3tls.match_hostname
# ldap3 的 check_hostname 用模組層級的 match_hostname：換成會比對 IP SAN 的版本（只影響 LDAP 連線）
_l3tls.match_hostname = _match_hostname

#: 「允許較弱的憑證金鑰」打開時用的 OpenSSL 安全等級：1 接受 RSA 1024 位元（舊版 AD 網域控制站常見），
#: 憑證鏈與名稱照樣驗證。預設（等級 2）要求 2048 位元以上。
_WEAK_TLS_CIPHERS = "DEFAULT:@SECLEVEL=1"


def _translate(exc: BaseException, prefix: str = "LDAP") -> LDAPAuthError:
    """ldap3／ssl 的例外 → 帶代碼的 LDAPAuthError（前端翻譯成看得懂的說明，原文放在 reason）。

    以前只有 admin bind 有轉，搜尋使用者與使用者 bind 的例外直接穿到端點變成 500，
    畫面只剩「Internal Server Error」（客戶 2026-10-19）。
    """
    if isinstance(exc, LDAPAuthError):
        return exc
    reason = f"{exc.__class__.__name__}: {exc}"[:400]
    low = reason.lower()
    if "key too weak" in low or "ee_key_too_small" in low or "ca_key_too_small" in low:
        code = "ldap_tls_weak_key"
    elif ("unable to get local issuer" in low or "self-signed certificate" in low
          or "self signed certificate" in low or "unable to verify the first certificate" in low):
        code = "ldap_tls_untrusted"
    elif "doesn't match" in low or "hostname mismatch" in low or "not valid for" in low:
        code = "ldap_tls_hostname"
    else:
        code = "ldap_error"
    return LDAPAuthError(f"{prefix} error: {reason}", code=code, reason=reason)


@dataclass
class LDAPUserInfo:
    dn: str
    username: str
    email: str | None
    display_name: str | None
    is_admin: bool   # 透過 admin_groups 判定
    raw_attrs: dict[str, Any]
    #: 目錄回的直接所屬群組（memberOf），給「測試登入」顯示
    groups: list[str] | None = None
    #: 判定成管理員是靠哪一個設定值、怎麼比對到的（direct / name / nested）；沒比對到是 None
    admin_via: dict[str, str] | None = None


def normalize_dn(dn: str | None) -> str:
    """拿掉 DN 裡逗號、加號與等號兩邊的空白（客戶 2026-10-21：`CN=svc, CN=Users, DC=corp, DC=example`）。

    AD 接受這種寫法，但 ldap3 檢查搜尋起點時會報 `character ' ' not allowed in attribute type`。
    值裡面的空白（`CN=John Smith`）與跳脫的字元（`CN=Smith\\, John`）照原樣；只切沒被跳脫的分隔符號。
    """
    s = (dn or "").strip()
    if not s:
        return ""
    rdns: list[list[str]] = [[]]
    buf, i = "", 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            buf += s[i:i + 2]
            i += 2
            continue
        if ch in ",+":
            rdns[-1].append(buf)
            buf = ""
            if ch == ",":
                rdns.append([])
        else:
            buf += ch
        i += 1
    rdns[-1].append(buf)

    def one(ava: str) -> str:
        t, eq, v = ava.partition("=")
        return f"{t.strip()}={v.strip()}" if eq else ava.strip()
    return ",".join("+".join(one(a) for a in rdn) for rdn in rdns)


#: 值是 DN 的屬性：篩選條件裡這些值照 DN 規則正規化（memberOf=CN=IPAM Users, OU=Groups, … 的逗號後空白）
_DN_VALUED = {"memberof", "distinguishedname", "manager", "member", "directreports", "objectcategory"}


#: 篩選條件的長度上限（存檔時檢查；LDAP 篩選條件不會這麼長）
FILTER_MAX_LEN = 1024


def _squeeze_filter_spaces(s: str) -> str:
    """去掉結構上多餘的空白：`(` 後、`)` 前、`)(` 之間、`&|!` 與 `(` 之間；`{ username }` → `{username}`。

    逐字元掃一遍（線性）。以前用 `\\s+\\)` 這類正規表示式，一長串不接 `)` 的空白會變成平方級
    （CodeQL #55 py/polynomial-redos，2026-10-21）。
    """
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch.isspace():
            j = i
            while j < n and s[j].isspace():
                j += 1
            prev = out[-1][-1:] if out else ""
            nxt = s[j] if j < n else ""
            drop = prev == "(" or nxt == ")" or (nxt == "(" and prev in (")", "&", "|", "!"))
            if not drop:
                out.append(s[i:j])
            i = j
            continue
        if ch == "{":
            j = i + 1
            while j < n and s[j].isspace():
                j += 1
            if s.startswith("username", j):
                k = j + len("username")
                while k < n and s[k].isspace():
                    k += 1
                if k < n and s[k] == "}":
                    out.append("{username}")
                    i = k + 1
                    continue
        out.append(ch)
        i += 1
    return "".join(out).strip()


def normalize_filter(flt: str | None) -> str:
    """拿掉篩選條件裡結構上多餘的空白（客戶 2026-10-21）：括號內外、&|! 後面、等號兩邊、`{ username }`。

    值裡面的空白（`(cn=John Smith)`）保留；值是 DN 的屬性（memberOf 等）照 normalize_dn。
    """
    s = _squeeze_filter_spaces((flt or "").strip())

    def leaf(m: re.Match[str]) -> str:
        body = m.group(1)
        op = re.search(r"~=|>=|<=|:=|=", body)
        if not op:
            return m.group(0)
        attr, val = body[:op.start()].strip(), body[op.end():].strip()
        base = attr.split(":", 1)[0].lower()
        if base in _DN_VALUED and "=" in val:
            val = normalize_dn(val)
        return f"({attr}{op.group(0)}{val})"
    return re.sub(r"\(([^()&|!][^()]*)\)", leaf, s)


def _build_server(cfg: LdapConfig) -> Server:
    if not cfg.enabled or not cfg.server:
        raise LDAPNotConfigured("LDAP not configured")

    # 連線前檢查目標位址（core/net_guard）；TLS 要驗憑證名稱，所以 Server 仍給主機名稱
    from app.core.net_guard import check_target
    from app.core.safe_http import UnsafeOutboundURL
    try:
        check_target(cfg.server, cfg.port)
    except UnsafeOutboundURL as exc:
        raise LDAPAuthError(str(exc)) from exc
    # 企業內部 CA（AD 憑證服務）不在系統信任清單裡：有設定就只信任它（ca_certs_data 取代系統清單）
    tls = Tls(validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLS_CLIENT,
              ca_certs_data=cfg.ca_cert_pem or None,
              ciphers=_WEAK_TLS_CIPHERS if cfg.allow_weak_tls else None)
    return Server(
        host=cfg.server,
        port=cfg.port,
        use_ssl=cfg.use_ssl,
        tls=tls,
        get_info=ALL,
        connect_timeout=int(cfg.timeout),
    )


def _bind_admin_sync(cfg: LdapConfig) -> Connection:
    server = _build_server(cfg)
    try:
        return _bind_admin_conn(cfg, server)
    except LDAPAuthError:
        raise
    except LDAPException as exc:
        raise _translate(exc, "LDAP bind") from exc


def _bind_admin_conn(cfg: LdapConfig, server: Server) -> Connection:
    conn = Connection(
        server,
        user=normalize_dn(cfg.bind_dn),
        password=cfg.bind_password,
        auto_bind=False,
        receive_timeout=int(cfg.timeout),
        raise_exceptions=True,
        # 不跟著轉介去連別台：那會繞過 net_guard 的目標檢查（AD 根目錄搜尋常帶轉介回來）
        auto_referrals=False,
    )
    try:
        if cfg.use_starttls and not cfg.use_ssl:
            conn.open()
            conn.start_tls()
        ok = conn.bind()
    except LDAPException as exc:
        # ldap3 在 raise_exceptions=True 時對 bind 失敗丟原生例外（含 AD 帳密錯誤）；轉成我們的型別
        msg = str(exc)
        if "invalidCredentials" in msg or "data 52e" in msg or "InvalidCredentials" in exc.__class__.__name__:
            raise LDAPInvalidCredentials(f"bind rejected（帳號或密碼錯誤）：{msg}",
                                         code="ldap_invalid_credentials",
                                         reason=msg[:200]) from exc
        raise _translate(exc, "LDAP bind") from exc
    if not ok:
        raise LDAPAuthError(f"LDAP admin bind failed: {conn.last_error}", code="ldap_error",
                            reason=f"admin bind: {conn.last_error}")
    return conn


#: AD 的 LDAP_MATCHING_RULE_IN_CHAIN：連巢狀群組一起比對（memberOf 只列直接所屬的群組）
_IN_CHAIN = "1.2.840.113556.1.4.1941"


def _first_rdn_value(dn: str) -> str:
    """`CN=IPAM Admins,OU=Groups,DC=corp` → `IPAM Admins`（跳脫的逗號不切）。"""
    m = re.match(r"\s*[^=]+=((?:\\.|[^,\\])*)", dn or "")
    return m.group(1).replace("\\", "").strip() if m else ""


def _dc_root(dn: str) -> str:
    return ",".join(p for p in normalize_dn(dn).split(",") if p.lower().startswith("dc="))


def resolve_admin(cfg: LdapConfig, conn: Any, user_dn: str, groups: list[str]) -> dict[str, str] | None:
    """依「管理員群組」設定判斷是不是管理員；比對到回 {group, via}，沒有回 None。

    客戶 2026-11-12：設了對應，登入登出都沒有變成管理員。以前只認「完整 DN、而且是直接所屬」：
    - 設定值寫群組名稱（`IPAM-Admins`）而不是 DN → 永遠比對不到 → 現在不含 `=` 的值當名稱，比對群組 DN 的第一段
    - 使用者是經由別的群組間接屬於管理員群組 → memberOf 只列直接所屬 → AD 改用 in-chain 比對規則再查一次
      （OpenLDAP 不支援這個規則，查詢失敗就當沒比對到）
    """
    wanted = [g.strip() for g in cfg.admin_groups if g and g.strip()]
    if not wanted:
        return None
    dns = {normalize_dn(g).lower(): g for g in wanted if "=" in g}
    names = {g.lower(): g for g in wanted if "=" not in g}
    for g in groups:
        nd = normalize_dn(str(g)).lower()
        if nd in dns:
            return {"group": dns[nd], "via": "direct"}
        cn = _first_rdn_value(str(g)).lower()
        if cn and cn in names:
            return {"group": names[cn], "via": "name"}
    if conn is None:
        return None
    member = escape_filter_chars(user_dn)
    for orig in dns.values():
        try:
            conn.search(search_base=normalize_dn(orig), search_filter=f"(member:{_IN_CHAIN}:={member})",
                        search_scope=BASE, attributes=[], time_limit=int(cfg.timeout))
            if conn.entries:
                return {"group": orig, "via": "nested"}
        except LDAPException:
            continue
    root = _dc_root(user_dn)
    for orig in names.values():
        if not root:
            break
        try:
            conn.search(search_base=root, search_scope=SUBTREE, attributes=[], time_limit=int(cfg.timeout),
                        size_limit=1, search_filter=(f"(&(objectClass=group)(cn={escape_filter_chars(orig)})"
                                                     f"(member:{_IN_CHAIN}:={member}))"))
            if conn.entries:
                return {"group": orig, "via": "nested"}
        except LDAPException:
            continue
    return None


def _authenticate_sync(cfg: LdapConfig, username: str, password: str) -> LDAPUserInfo:
    if not cfg.search_base:
        raise LDAPNotConfigured("LDAP search base not set")

    safe_username = escape_filter_chars(username)
    # 只換 {username}：用 str.format 的話篩選條件裡其他大括號會讓它出錯
    user_filter = normalize_filter(cfg.user_filter).replace("{username}", safe_username)

    # 1. admin bind 找出使用者 DN + 屬性
    conn = _bind_admin_sync(cfg)
    try:
        try:
            conn.search(
                search_base=normalize_dn(cfg.search_base),
                search_filter=user_filter,
                search_scope=SUBTREE,
                attributes=[cfg.attr_email, cfg.attr_display_name,
                            cfg.attr_member_of, "cn"],
                time_limit=int(cfg.timeout),
            )
        except LDAPException as exc:
            raise _translate(exc, "LDAP search") from exc
        if not conn.entries:
            raise LDAPInvalidCredentials("user not found")
        if len(conn.entries) > 1:
            raise LDAPAuthError("multiple users matched filter")
        entry = conn.entries[0]
        user_dn = entry.entry_dn
        attrs = entry.entry_attributes_as_dict
        groups = [str(g) for g in (attrs.get(cfg.attr_member_of) or [])]
        # 巢狀群組要用服務帳號再查一次，所以趁連線還開著先判斷（密碼錯的話結果不會被用到）
        admin_via = resolve_admin(cfg, conn, user_dn, groups)
    finally:
        try:
            conn.unbind()
        except LDAPException:
            pass

    # 2. 用 user DN + 密碼 bind 驗證
    server = _build_server(cfg)
    user_conn = Connection(
        server,
        user=user_dn,
        password=password,
        auto_bind=False,
        receive_timeout=int(cfg.timeout),
        raise_exceptions=False,
        auto_referrals=False,
    )
    try:
        if cfg.use_starttls and not cfg.use_ssl:
            user_conn.open()
            if not user_conn.start_tls():
                raise LDAPAuthError("LDAP user StartTLS failed", code="ldap_error",
                                    reason=f"user StartTLS: {user_conn.last_error}")
        bound = user_conn.bind()
    except LDAPException as exc:
        raise _translate(exc, "LDAP user bind") from exc
    try:
        if not bound:
            raise LDAPInvalidCredentials("invalid password")
    finally:
        try:
            user_conn.unbind()
        except LDAPException:
            pass

    # 3. 解析屬性
    email = (attrs.get(cfg.attr_email) or [None])[0]
    display_name = (attrs.get(cfg.attr_display_name) or [None])[0]
    # 群組 DN 比對前都正規化並忽略大小寫：設定裡手打的常有逗號後空白，AD 回來的 memberOf 沒有（resolve_admin）
    is_admin = admin_via is not None

    return LDAPUserInfo(
        dn=user_dn,
        username=username,
        email=email,
        display_name=display_name,
        is_admin=is_admin,
        raw_attrs=attrs,
        groups=groups,
        admin_via=admin_via,
    )


async def authenticate(cfg: LdapConfig, username: str, password: str) -> LDAPUserInfo:
    """非同步入口；ldap3 同步呼叫包進 thread executor。"""
    if not cfg.enabled:
        raise LDAPNotConfigured("LDAP is disabled")
    return await asyncio.to_thread(_authenticate_sync, cfg, username, password)


#: rootDSE supportedCapabilities 裡的 LDAP_CAP_ACTIVE_DIRECTORY_OID（AD 與 Samba AD 都會公告）
_AD_CAPABILITY = "1.2.840.113556.1.4.800"


def _is_active_directory(conn: Any, who: Any) -> bool:
    info = getattr(getattr(conn, "server", None), "info", None)
    other = getattr(info, "other", None) or {}
    caps = other.get("supportedCapabilities") or []
    if _AD_CAPABILITY in [str(c) for c in caps]:
        return True
    # ldap3 讀 rootDSE 時不一定帶 supportedCapabilities；這幾個屬性只有 AD（含 Samba AD）才有（2026-10-21 實測）
    if any(k in other for k in ("domainControllerFunctionality", "forestFunctionality", "dsServiceName")):
        return True
    # 後援：AD 的 Who am I 回「u:網域\\帳號」（OpenLDAP 回「dn:…」）
    return isinstance(who, str) and who.startswith("u:") and "\\" in who


async def test_connection(cfg: LdapConfig) -> dict[str, Any]:
    """admin bind 測試 — 不需要任何使用者密碼。"""
    if not cfg.enabled:
        raise LDAPNotConfigured("LDAP is disabled")

    def _go() -> dict[str, Any]:
        conn = _bind_admin_sync(cfg)
        try:
            # Who am I 是附加資訊：Samba AD 不支援這個擴充操作，不可以因此判定測試失敗（2026-10-21 實測）
            try:
                who = conn.extend.standard.who_am_i()
            except LDAPException:
                who = None
            directory = "ad" if _is_active_directory(conn, who) else "ldap"
            warnings: list[dict[str, Any]] = []
            # 以前只做 bind：搜尋起點寫錯、AD 用了 OpenLDAP 的篩選條件都顯示成功，到登入才爆（客戶 2026-10-21）
            if cfg.search_base:
                try:
                    conn.search(search_base=normalize_dn(cfg.search_base), search_filter="(objectClass=*)",
                                search_scope=BASE, attributes=[], time_limit=int(cfg.timeout))
                except LDAPException as exc:
                    warnings.append(ui_detail("ldap_warn_search_base", f"search base {cfg.search_base} is not readable",
                                              base=cfg.search_base, reason=str(exc)[:200]))
            if directory == "ad" and "uid=" in (cfg.user_filter or "").lower():
                warnings.append(ui_detail("ldap_warn_uid_filter_on_ad",
                                          "Active Directory with an OpenLDAP-style (uid=...) filter; use sAMAccountName",
                                          filter=cfg.user_filter))
            return {
                "bound": True,
                "server": cfg.server,
                "port": cfg.port,
                "tls": "ssl" if cfg.use_ssl else "starttls" if cfg.use_starttls else "none",
                "who_am_i": who,
                "directory": directory,
                "warnings": warnings,
            }
        finally:
            try:
                conn.unbind()
            except LDAPException:
                pass

    return await asyncio.to_thread(_go)
