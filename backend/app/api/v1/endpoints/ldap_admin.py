"""LDAP / AD 認證 — 管理區設定（DB 覆蓋 env）。

  GET  /api/v1/system/ldap        → 目前設定（不含明文密碼，只回 password_set 旗標）
  PUT  /api/v1/system/ldap        → 寫入設定（bind_password 留空＝不變、給空字串＝清除）
  POST /api/v1/system/ldap/test   → 以目前設定（或本次送出的設定）試連 LDAP
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.ui_error import detail_of, ui_detail
from app.models.system_setting import SystemSetting
from app.schemas.base import StrictModel
from app.services import ldap_auth
from app.services.system_config import (
    LDAP_KEY,
    get_ldap_config,
    set_ldap_config,
)

log = logging.getLogger("ldap_admin")

#: CA 憑證欄位上限（一整串內部 CA 鏈也遠低於這個大小）
_CA_PEM_MAX = 64 * 1024


def _ca_subjects(pem: str | None) -> list[str]:
    """解析 PEM 裡的每一張憑證，回傳主旨（畫面顯示「目前信任：…」）；不是有效憑證就丟 ValueError。"""
    if not pem or not pem.strip():
        return []
    from cryptography import x509
    certs = x509.load_pem_x509_certificates(pem.encode())
    return [c.subject.rfc4514_string() for c in certs]


admin_router = APIRouter(
    prefix="/system", tags=["system"], dependencies=[Depends(require_admin)]
)


class LdapOut(StrictModel):
    enabled: bool
    server: str | None
    port: int
    use_ssl: bool
    use_starttls: bool
    bind_dn: str | None
    password_set: bool
    search_base: str | None
    user_filter: str
    attr_email: str
    attr_display_name: str
    attr_member_of: str
    admin_groups: list[str]
    default_group_id: str | None = None
    #: 驗證伺服器憑證用的 CA（企業內部 CA，PEM，可以多張）；空＝系統信任清單
    ca_cert_pem: str | None = None
    ca_cert_subjects: list[str] = []
    #: 接受 RSA 1024 位元等較弱金鑰的伺服器憑證（舊版 AD 網域控制站）；憑證照樣驗證
    allow_weak_tls: bool = False


class LdapPatch(StrictModel):
    enabled: bool = False
    server: Annotated[str | None, None] = None
    port: int = 389
    use_ssl: bool = False
    use_starttls: bool = True
    bind_dn: str | None = None
    bind_password: str | None = None   # None＝不變、""＝清除、其他＝更新
    search_base: str | None = None
    user_filter: str = "(sAMAccountName={username})"
    attr_email: str = "mail"
    attr_display_name: str = "displayName"
    attr_member_of: str = "memberOf"
    admin_groups: list[str] = []
    default_group_id: str | None = None
    ca_cert_pem: str | None = None      # None／""＝不使用自訂 CA
    allow_weak_tls: bool = False


async def _password_set(session: AsyncSession) -> bool:
    row = await session.get(SystemSetting, LDAP_KEY)
    return bool(row and isinstance(row.value, dict) and row.value.get("bind_password_enc"))


async def _as_out(session: AsyncSession) -> dict:  # type: ignore[type-arg]
    cfg = await get_ldap_config(session)
    return {
        "enabled": cfg.enabled, "server": cfg.server, "port": cfg.port,
        "use_ssl": cfg.use_ssl, "use_starttls": cfg.use_starttls, "bind_dn": cfg.bind_dn,
        "password_set": (await _password_set(session)) or bool(cfg.bind_password),
        "search_base": cfg.search_base, "user_filter": cfg.user_filter,
        "attr_email": cfg.attr_email, "attr_display_name": cfg.attr_display_name,
        "attr_member_of": cfg.attr_member_of, "admin_groups": cfg.admin_groups,
        "default_group_id": str(cfg.default_group_id) if cfg.default_group_id else None,
        "ca_cert_pem": cfg.ca_cert_pem, "ca_cert_subjects": _safe_subjects(cfg.ca_cert_pem),
        "allow_weak_tls": cfg.allow_weak_tls,
    }


def _safe_subjects(pem: str | None) -> list[str]:
    try:
        return _ca_subjects(pem)
    except ValueError:
        return []


@admin_router.get("/ldap", response_model=LdapOut)
async def get_ldap(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:  # type: ignore[type-arg]
    return await _as_out(session)


@admin_router.put("/ldap", response_model=LdapOut)
async def put_ldap(
    payload: LdapPatch, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:  # type: ignore[type-arg]
    data = payload.model_dump()
    pem = (payload.ca_cert_pem or "").strip()
    if len(pem) > _CA_PEM_MAX:
        raise HTTPException(422, detail=ui_detail("ldap_ca_invalid", "CA 憑證太大", reason="too large"))
    try:
        if pem and not _ca_subjects(pem):
            raise ValueError("no certificate found")
    except ValueError as exc:
        raise HTTPException(422, detail=ui_detail("ldap_ca_invalid", f"CA 憑證不是有效的 PEM：{exc}",
                                                  reason=str(exc)[:200])) from exc
    data["ca_cert_pem"] = pem + "\n" if pem else ""
    # 空白存檔時就整理好（客戶 2026-10-21：逗號後的空白讓 ldap3 拒絕搜尋起點）；舊設定在使用時再正規化
    data["bind_dn"] = ldap_auth.normalize_dn(payload.bind_dn) or None
    data["search_base"] = ldap_auth.normalize_dn(payload.search_base) or None
    data["admin_groups"] = [g for g in (ldap_auth.normalize_dn(x) for x in payload.admin_groups) if g]
    if len(payload.user_filter or "") > ldap_auth.FILTER_MAX_LEN:
        raise HTTPException(422, detail=ui_detail(
            "ldap_filter_too_long", "使用者篩選條件太長", max=ldap_auth.FILTER_MAX_LEN))
    flt = ldap_auth.normalize_filter(payload.user_filter)
    if "{username}" not in flt:
        raise HTTPException(422, detail=ui_detail(
            "ldap_filter_no_username", "使用者篩選條件裡要有 {username}（登入時換成帳號）", filter=payload.user_filter))
    depth = 0
    for ch in flt:
        depth += 1 if ch == "(" else -1 if ch == ")" else 0
        if depth < 0:
            break
    if depth != 0 or not flt.startswith("("):
        raise HTTPException(422, detail=ui_detail(
            "ldap_filter_invalid", "使用者篩選條件的括號不成對", filter=payload.user_filter))
    data["user_filter"] = flt
    if payload.bind_password is None:   # 不變更密碼
        data.pop("bind_password", None)
    await set_ldap_config(session, data=data, updated_by_user_id=uuid.UUID(str(user.id)))
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system_setting", object_id=None, action="update",
        diff={"setting": "ldap", "enabled": payload.enabled, "server": payload.server,
              "bind_dn": payload.bind_dn, "ca_cert": bool(pem), "allow_weak_tls": payload.allow_weak_tls},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return await _as_out(session)


@admin_router.post("/ldap/test")
async def ldap_test_conn(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    cfg = await get_ldap_config(session)
    try:
        return await ldap_auth.test_connection(cfg)
    except ldap_auth.LDAPNotConfigured as exc:
        raise HTTPException(503, detail=detail_of(exc, "ldap_not_configured")) from exc
    except ldap_auth.LDAPAuthError as exc:
        raise HTTPException(502, detail=detail_of(exc, "ldap_error")) from exc
    except Exception as exc:   # 不可以變成 500：管理員要看得到原因才能修設定
        log.exception("LDAP test connection failed unexpectedly")
        raise HTTPException(502, detail=detail_of(ldap_auth._translate(exc), "ldap_error")) from exc


class LdapTestAuth(StrictModel):
    username: str
    password: str


@admin_router.post("/ldap/test-auth")
async def ldap_test_auth(
    payload: LdapTestAuth, _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """用一組真實帳密跑完整 LDAP 驗證流程（admin bind → 搜尋使用者 → 以該使用者 bind），
    回報找到的 DN / 顯示名稱 / email / 是否為管理員，協助診斷篩選條件與帳密。"""
    cfg = await get_ldap_config(session)
    account = payload.username.split("@")[0].strip()
    try:
        info = await ldap_auth.authenticate(cfg, account, payload.password)
    except ldap_auth.LDAPInvalidCredentials as exc:
        if str(exc) == "user not found" and "uid=" in (cfg.user_filter or "").lower():
            # (uid=…) 是 OpenLDAP 的寫法；AD 的帳號在 sAMAccountName（客戶 2026-10-21 照預設值用在 AD）
            raise HTTPException(401, detail=ui_detail(
                "ldap_user_not_found_uid_filter",
                "找不到這個使用者；篩選條件 (uid=…) 是 OpenLDAP 的寫法，AD 請改成 (sAMAccountName={username})",
                filter=cfg.user_filter)) from exc
        raise HTTPException(401, detail=ui_detail("ldap_bad_credentials", f"帳號或密碼錯誤／找不到使用者：{exc}",
                                 reason=str(exc)[:200])) from exc
    except ldap_auth.LDAPNotConfigured as exc:
        raise HTTPException(503, detail=detail_of(exc, "ldap_not_configured")) from exc
    except ldap_auth.LDAPAuthError as exc:
        raise HTTPException(502, detail=detail_of(exc, "ldap_error")) from exc
    except Exception as exc:   # 以前搜尋階段的例外會穿出來變成 500（客戶 2026-10-19）
        log.exception("LDAP test-auth failed unexpectedly")
        raise HTTPException(502, detail=detail_of(ldap_auth._translate(exc), "ldap_error")) from exc
    return {
        "ok": True, "dn": info.dn, "username": info.username,
        "display_name": info.display_name, "email": info.email,
        "is_admin": info.is_admin,
        # 看得出為什麼是／不是管理員（客戶 2026-11-12：設了對應卻一直是一般使用者，畫面上看不出原因）
        "admin_groups_configured": any((g or "").strip() for g in cfg.admin_groups),
        "admin_via": info.admin_via,
        "groups": (info.groups or [])[:200],
    }
