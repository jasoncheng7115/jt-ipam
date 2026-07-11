"""讀 system_settings table（admin UI 設定）+ env 預設值合併。

對 ai.py 之類消費者：呼叫 get_llm_config(session) → 拿到完整 dict，
DB 有設就用 DB，否則用 env。

有簡單 60s in-process cache 避免每次 LLM call 都 hit DB；改寫時主動 bump 版本。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.system_setting import SystemSetting

LLM_KEY = "llm"
_TTL_SEC = 60.0


@dataclass
class LLMConfig:
    enabled: bool
    url: str
    embedding_model: str
    chat_model: str
    timeout: float
    # 對話模型的上下文長度（Ollama num_ctx）。None＝沿用模型／Ollama 預設（通常 4096）。
    # 工具多、注入資料量大的對話容易超過預設而被截斷，可在此調高（耗更多記憶體/VRAM）。
    num_ctx: int | None = None
    # 對外提供 MCP（讓其它系統以 HTTP 呼叫 /api/mcp）：預設關閉，打開才接受外部 MCP 呼叫。
    mcp_external_enabled: bool = False
    mcp_api_key: str | None = None          # 明文（已解密）；僅程序內使用，不外傳
    mcp_principal_user_id: str | None = None  # MCP 金鑰所代表的管理員身份（唯讀，僅供 RBAC 可見範圍）


_MCP_AAD = b"llm:mcp_api_key"


def _enc_mcp(plain: str) -> str:
    import base64 as _b64

    from app.core.security import encrypt_secret
    ct, nonce = encrypt_secret(plain, aad=_MCP_AAD)
    return "v1:" + _b64.b64encode(nonce).decode() + ":" + _b64.b64encode(ct).decode()


def _dec_mcp(blob: str) -> str | None:
    import base64 as _b64

    from app.core.security import decrypt_secret
    try:
        _ver, b_nonce, b_ct = blob.split(":", 2)
        return decrypt_secret(
            _b64.b64decode(b_ct), _b64.b64decode(b_nonce), aad=_MCP_AAD,
        ).decode("utf-8")
    except Exception:
        return None


_cache: dict[str, tuple[float, LLMConfig]] = {}


def _bust() -> None:
    _cache.pop(LLM_KEY, None)


async def get_llm_config(session: AsyncSession) -> LLMConfig:
    now = time.monotonic()
    cached = _cache.get(LLM_KEY)
    if cached and now - cached[0] < _TTL_SEC:
        return cached[1]

    s = get_settings()
    # env 預設
    cfg = LLMConfig(
        enabled=s.ollama_enabled,
        url=s.ollama_url,
        embedding_model=s.ollama_embedding_model,
        chat_model=s.ollama_chat_model,
        timeout=s.ollama_timeout,
    )
    row = await session.get(SystemSetting, LLM_KEY)
    if row and isinstance(row.value, dict):
        v = row.value
        if "enabled" in v and isinstance(v["enabled"], bool):
            cfg.enabled = v["enabled"]
        if v.get("url"):
            cfg.url = str(v["url"])
        if v.get("embedding_model"):
            cfg.embedding_model = str(v["embedding_model"])
        if v.get("chat_model"):
            cfg.chat_model = str(v["chat_model"])
        if v.get("timeout") is not None:
            try:
                cfg.timeout = float(v["timeout"])
            except (ValueError, TypeError):
                pass
        if v.get("num_ctx") is not None:
            try:
                n = int(v["num_ctx"])
                cfg.num_ctx = n if n > 0 else None
            except (ValueError, TypeError):
                pass
        if isinstance(v.get("mcp_external_enabled"), bool):
            cfg.mcp_external_enabled = v["mcp_external_enabled"]
        if v.get("mcp_api_key_enc"):
            cfg.mcp_api_key = _dec_mcp(str(v["mcp_api_key_enc"]))
        if v.get("mcp_principal_user_id"):
            cfg.mcp_principal_user_id = str(v["mcp_principal_user_id"])

    _cache[LLM_KEY] = (now, cfg)
    return cfg


async def set_llm_config(
    session: AsyncSession,
    *,
    enabled: bool | None = None,
    url: str | None = None,
    embedding_model: str | None = None,
    chat_model: str | None = None,
    timeout: float | None = None,
    num_ctx: int | None = None,
    mcp_external_enabled: bool | None = None,
    updated_by_user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    row = await session.get(SystemSetting, LLM_KEY)
    if row is None:
        row = SystemSetting(key=LLM_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    current: dict[str, Any] = dict(row.value or {})
    if enabled is not None: current["enabled"] = bool(enabled)
    if url is not None: current["url"] = str(url).strip().rstrip("/")
    if embedding_model is not None: current["embedding_model"] = embedding_model.strip()
    if chat_model is not None: current["chat_model"] = chat_model.strip()
    if timeout is not None: current["timeout"] = float(timeout)
    if num_ctx is not None: current["num_ctx"] = int(num_ctx) if int(num_ctx) > 0 else None
    if mcp_external_enabled is not None: current["mcp_external_enabled"] = bool(mcp_external_enabled)
    row.value = current
    row.updated_by = updated_by_user_id
    # JSONB 變更 SQLAlchemy 對 dict in-place 不會偵測 — flag_modified 保險
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    _bust()
    return current


async def rotate_mcp_api_key(
    session: AsyncSession,
    *,
    principal_user_id: uuid.UUID,
    updated_by_user_id: uuid.UUID | None = None,
) -> str:
    """產生一把新的對外 MCP 金鑰（唯讀），加密保存並綁定代表身份；回傳明文（僅此一次完整顯示）。"""
    import secrets

    key = "jtmcp_" + secrets.token_urlsafe(32)
    row = await session.get(SystemSetting, LLM_KEY)
    if row is None:
        row = SystemSetting(key=LLM_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    current: dict[str, Any] = dict(row.value or {})
    current["mcp_api_key_enc"] = _enc_mcp(key)
    current["mcp_principal_user_id"] = str(principal_user_id)
    row.value = current
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    _bust()
    return key


# ─────────────────── AI chat 歷程保留設定 ───────────────────
AI_CHAT_KEY = "ai_chat"
_DEFAULT_RETENTION_DAYS = 90


async def get_ai_chat_retention_days(session: AsyncSession) -> int:
    """AI chat 歷程保留天數；0 = 永久保留。預設 90 天。"""
    row = await session.get(SystemSetting, AI_CHAT_KEY)
    if row and isinstance(row.value, dict):
        v = row.value.get("retention_days")
        if isinstance(v, int) and v >= 0:
            return v
    return _DEFAULT_RETENTION_DAYS


async def set_ai_chat_retention_days(
    session: AsyncSession, *, days: int, updated_by_user_id: uuid.UUID | None = None,
) -> int:
    days = max(0, int(days))
    row = await session.get(SystemSetting, AI_CHAT_KEY)
    if row is None:
        row = SystemSetting(key=AI_CHAT_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    current = dict(row.value or {})
    current["retention_days"] = days
    row.value = current
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    return days


# ─────────────────── 連線管理資安設定（console security）───────────────────
CONSOLE_SECURITY_KEY = "console_security"


async def get_rdp_clipboard_paste(session: AsyncSession) -> bool:
    """是否允許 RDP 控制端把文字貼到被控端（剪貼簿單向重導）。預設關閉（deny by default）。"""
    row = await session.get(SystemSetting, CONSOLE_SECURITY_KEY)
    if row and isinstance(row.value, dict):
        return bool(row.value.get("rdp_clipboard_paste", False))
    return False


async def set_rdp_clipboard_paste(
    session: AsyncSession, *, enabled: bool, updated_by_user_id: uuid.UUID | None = None,
) -> bool:
    row = await session.get(SystemSetting, CONSOLE_SECURITY_KEY)
    if row is None:
        row = SystemSetting(key=CONSOLE_SECURITY_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    current = dict(row.value or {})
    current["rdp_clipboard_paste"] = bool(enabled)
    row.value = current
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    return bool(enabled)


# ─────────────────── 介面顯示設定（UI display）───────────────────
UI_DISPLAY_KEY = "ui_display"
_DEFAULT_CHANGE_LOG_DIM_DAYS = 30


async def get_change_log_dim_days(session: AsyncSession) -> int:
    """異動記錄超過幾天的項目以淡色顯示；0 = 不淡化。預設 30 天。"""
    row = await session.get(SystemSetting, UI_DISPLAY_KEY)
    if row and isinstance(row.value, dict):
        v = row.value.get("change_log_dim_days")
        if isinstance(v, int) and v >= 0:
            return v
    return _DEFAULT_CHANGE_LOG_DIM_DAYS


async def set_change_log_dim_days(
    session: AsyncSession, *, days: int, updated_by_user_id: uuid.UUID | None = None,
) -> int:
    days = max(0, min(3650, int(days)))
    row = await session.get(SystemSetting, UI_DISPLAY_KEY)
    if row is None:
        row = SystemSetting(key=UI_DISPLAY_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    current = dict(row.value or {})
    current["change_log_dim_days"] = days
    row.value = current
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    return days


# ─────────────────── Graylog DSV 查表（lookup table adapter）───────────────────

GRAYLOG_DSV_KEY = "graylog_dsv"


async def get_graylog_dsv(session: AsyncSession) -> dict[str, Any]:
    """Graylog DSV 查表設定：enabled / token / fmt(csv|tsv) / path(URL slug)。"""
    row = await session.get(SystemSetting, GRAYLOG_DSV_KEY)
    v = dict(row.value) if (row and isinstance(row.value, dict)) else {}
    return {
        "enabled": bool(v.get("enabled", False)),
        "token": str(v.get("token") or ""),
        "fmt": v.get("fmt") if v.get("fmt") in ("csv", "tsv") else "csv",
        "path": str(v.get("path") or "ip-fqdn"),
    }


async def set_graylog_dsv(
    session: AsyncSession, *, enabled: bool, fmt: str, path: str,
    regenerate_token: bool = False, updated_by_user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    import re
    import secrets

    from sqlalchemy.orm.attributes import flag_modified

    row = await session.get(SystemSetting, GRAYLOG_DSV_KEY)
    if row is None:
        row = SystemSetting(key=GRAYLOG_DSV_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    cur = dict(row.value or {})
    cur["enabled"] = bool(enabled)
    cur["fmt"] = fmt if fmt in ("csv", "tsv") else "csv"
    # path 限英數 / 連字號 / 底線，避免亂跑路由
    slug = re.sub(r"[^A-Za-z0-9_-]", "", path or "").strip("-") or "ip-fqdn"
    cur["path"] = slug[:48]
    if regenerate_token or not cur.get("token"):
        cur["token"] = secrets.token_urlsafe(24)
    row.value = cur
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.commit()
    return await get_graylog_dsv(session)


# ─────────────────── LDAP / AD（管理區設定，DB 覆蓋 env）───────────────────
import base64  # noqa: E402

from app.core.security import decrypt_secret, encrypt_secret  # noqa: E402

LDAP_KEY = "ldap"
_LDAP_AAD = b"ldap:bind_password"


@dataclass
class LdapConfig:
    enabled: bool
    server: str | None
    port: int
    use_ssl: bool
    use_starttls: bool
    bind_dn: str | None
    bind_password: str | None   # 明文（已解密）；僅在 process 內使用，不外傳
    search_base: str | None
    user_filter: str
    attr_email: str
    attr_display_name: str
    attr_member_of: str
    admin_groups: list[str]
    timeout: float
    default_group_id: str | None = None   # 自動建立帳號時加入的群組（預設角色）


def _enc_pw(pw: str) -> str:
    ct, nonce = encrypt_secret(pw, aad=_LDAP_AAD)
    return "v1:" + base64.b64encode(nonce).decode() + ":" + base64.b64encode(ct).decode()


def _dec_pw(blob: str) -> str | None:
    try:
        _ver, b_nonce, b_ct = blob.split(":", 2)
        return decrypt_secret(
            base64.b64decode(b_ct), base64.b64decode(b_nonce), aad=_LDAP_AAD
        ).decode("utf-8")
    except Exception:
        return None


async def get_ldap_config(session: AsyncSession) -> LdapConfig:
    """合併 env 預設 + DB 覆蓋。DB 沒設就完全等同舊的 env 行為。"""
    s = get_settings()
    cfg = LdapConfig(
        enabled=s.ldap_enabled,
        server=s.ldap_server,
        port=s.ldap_port,
        use_ssl=s.ldap_use_ssl,
        use_starttls=s.ldap_use_starttls,
        bind_dn=s.ldap_bind_dn,
        bind_password=s.ldap_bind_password.get_secret_value() if s.ldap_bind_password else None,
        search_base=s.ldap_search_base,
        user_filter=s.ldap_user_filter,
        attr_email=s.ldap_attr_email,
        attr_display_name=s.ldap_attr_display_name,
        attr_member_of=s.ldap_attr_member_of,
        admin_groups=list(s.ldap_admin_groups),
        timeout=s.ldap_timeout,
    )
    row = await session.get(SystemSetting, LDAP_KEY)
    if row and isinstance(row.value, dict):
        v = row.value
        for k in ("server", "bind_dn", "search_base", "user_filter",
                  "attr_email", "attr_display_name", "attr_member_of"):
            if isinstance(v.get(k), str) and v[k] != "":
                setattr(cfg, k, v[k])
        for k in ("enabled", "use_ssl", "use_starttls"):
            if isinstance(v.get(k), bool):
                setattr(cfg, k, v[k])
        if isinstance(v.get("port"), int):
            cfg.port = v["port"]
        if isinstance(v.get("admin_groups"), list):
            cfg.admin_groups = [str(x) for x in v["admin_groups"]]
        if isinstance(v.get("default_group_id"), str) and v["default_group_id"]:
            cfg.default_group_id = v["default_group_id"]
        if isinstance(v.get("bind_password_enc"), str) and v["bind_password_enc"]:
            pw = _dec_pw(v["bind_password_enc"])
            if pw is not None:
                cfg.bind_password = pw
    return cfg


_LDAP_SCALARS = ("enabled", "server", "port", "use_ssl", "use_starttls", "bind_dn",
                 "search_base", "user_filter", "attr_email", "attr_display_name",
                 "attr_member_of", "admin_groups", "default_group_id")


async def set_ldap_config(
    session: AsyncSession, *, data: dict[str, Any], updated_by_user_id: uuid.UUID
) -> dict[str, Any]:
    """寫入 DB。bind_password：給非空字串才更新；給空字串清除；不給則保留原值。"""
    from sqlalchemy.orm.attributes import flag_modified

    row = await session.get(SystemSetting, LDAP_KEY)
    if row is None:
        row = SystemSetting(key=LDAP_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    val: dict[str, Any] = dict(row.value or {})
    for k in _LDAP_SCALARS:
        if k in data:
            val[k] = data[k]
    if "bind_password" in data:
        pw = data["bind_password"]
        if pw:
            val["bind_password_enc"] = _enc_pw(str(pw))
        elif pw == "":
            val.pop("bind_password_enc", None)
    row.value = val
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.commit()
    return val


# ─────────────────── SSO：OIDC（OpenID Connect）───────────────────
OIDC_KEY = "oidc"
_OIDC_AAD = b"oidc:client_secret"


def _enc_oidc(s: str) -> str:
    ct, nonce = encrypt_secret(s, aad=_OIDC_AAD)
    return "v1:" + base64.b64encode(nonce).decode() + ":" + base64.b64encode(ct).decode()


def _dec_oidc(blob: str) -> str | None:
    try:
        _ver, b_nonce, b_ct = blob.split(":", 2)
        return decrypt_secret(base64.b64decode(b_ct), base64.b64decode(b_nonce),
                              aad=_OIDC_AAD).decode("utf-8")
    except Exception:
        return None


@dataclass
class OidcConfig:
    enabled: bool
    issuer: str | None
    client_id: str | None
    client_secret: str | None   # 明文（已解密），僅 process 內用
    redirect_uri: str | None
    scope: str
    groups_claim: str
    username_claim: str
    admin_groups: list[str]
    default_group_id: str | None = None


async def get_oidc_config(session: AsyncSession) -> OidcConfig:
    s = get_settings()
    cfg = OidcConfig(
        enabled=s.oidc_enabled,
        issuer=s.oidc_issuer,
        client_id=s.oidc_client_id,
        client_secret=s.oidc_client_secret.get_secret_value() if s.oidc_client_secret else None,
        redirect_uri=s.oidc_redirect_uri,
        scope=s.oidc_scope,
        groups_claim=s.oidc_groups_claim,
        username_claim=s.oidc_username_claim,
        admin_groups=list(s.oidc_admin_groups),
    )
    row = await session.get(SystemSetting, OIDC_KEY)
    if row and isinstance(row.value, dict):
        v = row.value
        for k in ("issuer", "client_id", "redirect_uri", "scope",
                  "groups_claim", "username_claim"):
            if isinstance(v.get(k), str) and v[k] != "":
                setattr(cfg, k, v[k])
        if isinstance(v.get("enabled"), bool):
            cfg.enabled = v["enabled"]
        if isinstance(v.get("admin_groups"), list):
            cfg.admin_groups = [str(x) for x in v["admin_groups"]]
        if isinstance(v.get("default_group_id"), str) and v["default_group_id"]:
            cfg.default_group_id = v["default_group_id"]
        if isinstance(v.get("client_secret_enc"), str) and v["client_secret_enc"]:
            sec = _dec_oidc(v["client_secret_enc"])
            if sec is not None:
                cfg.client_secret = sec
    return cfg


_OIDC_SCALARS = ("enabled", "issuer", "client_id", "redirect_uri", "scope",
                 "groups_claim", "username_claim", "admin_groups", "default_group_id")


async def set_oidc_config(
    session: AsyncSession, *, data: dict[str, Any], updated_by_user_id: uuid.UUID
) -> dict[str, Any]:
    from sqlalchemy.orm.attributes import flag_modified
    row = await session.get(SystemSetting, OIDC_KEY)
    if row is None:
        row = SystemSetting(key=OIDC_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    val: dict[str, Any] = dict(row.value or {})
    for k in _OIDC_SCALARS:
        if k in data:
            val[k] = data[k]
    if "client_secret" in data:
        sec = data["client_secret"]
        if sec:
            val["client_secret_enc"] = _enc_oidc(str(sec))
        elif sec == "":
            val.pop("client_secret_enc", None)
    row.value = val
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.commit()
    return val


# ─────────────────── SSO：SAML 2.0 ───────────────────
SAML_KEY = "saml"
_SAML_AAD = b"saml:sp_private_key"


def _enc_saml(s: str) -> str:
    ct, nonce = encrypt_secret(s, aad=_SAML_AAD)
    return "v1:" + base64.b64encode(nonce).decode() + ":" + base64.b64encode(ct).decode()


def _dec_saml(blob: str) -> str | None:
    try:
        _ver, b_nonce, b_ct = blob.split(":", 2)
        return decrypt_secret(base64.b64decode(b_ct), base64.b64decode(b_nonce),
                              aad=_SAML_AAD).decode("utf-8")
    except Exception:
        return None


@dataclass
class SamlConfig:
    enabled: bool
    idp_metadata_url: str | None
    idp_metadata_xml: str | None
    sp_entity_id: str | None
    sp_acs_url: str | None
    sp_sls_url: str | None
    sp_x509_cert: str | None
    sp_private_key: str | None   # 明文（已解密），僅 process 內用
    want_assertions_signed: bool
    want_assertions_encrypted: bool
    want_name_id_encrypted: bool
    authn_requests_signed: bool
    attr_username: str
    attr_email: str
    attr_displayname: str
    attr_groups: str
    admin_groups: list[str]
    default_group_id: str | None = None


async def get_saml_config(session: AsyncSession) -> SamlConfig:
    """env 為預設、DB(system_settings.saml) 覆寫；無 DB row → 行為與舊版讀 env 完全相同。"""
    s = get_settings()
    cfg = SamlConfig(
        enabled=s.saml_enabled,
        idp_metadata_url=s.saml_idp_metadata_url or s.saml_metadata_url,
        idp_metadata_xml=s.saml_idp_metadata_xml,
        sp_entity_id=s.saml_sp_entity_id or s.saml_entity_id,
        sp_acs_url=s.saml_sp_acs_url or s.saml_acs_url,
        sp_sls_url=s.saml_sp_sls_url,
        sp_x509_cert=s.saml_sp_x509_cert,
        sp_private_key=s.saml_sp_private_key.get_secret_value() if s.saml_sp_private_key else None,
        want_assertions_signed=s.saml_want_assertions_signed,
        want_assertions_encrypted=s.saml_want_assertions_encrypted,
        want_name_id_encrypted=s.saml_want_name_id_encrypted,
        authn_requests_signed=s.saml_authn_requests_signed,
        attr_username=s.saml_attr_username,
        attr_email=s.saml_attr_email,
        attr_displayname=s.saml_attr_displayname,
        attr_groups=s.saml_attr_groups,
        admin_groups=list(s.saml_admin_groups),
    )
    row = await session.get(SystemSetting, SAML_KEY)
    if row and isinstance(row.value, dict):
        v = row.value
        for k in ("idp_metadata_url", "idp_metadata_xml", "sp_entity_id", "sp_acs_url",
                  "sp_sls_url", "sp_x509_cert", "attr_username", "attr_email",
                  "attr_displayname", "attr_groups"):
            if isinstance(v.get(k), str) and v[k] != "":
                setattr(cfg, k, v[k])
        for bk in ("enabled", "want_assertions_signed", "want_assertions_encrypted",
                   "want_name_id_encrypted", "authn_requests_signed"):
            if isinstance(v.get(bk), bool):
                setattr(cfg, bk, v[bk])
        if isinstance(v.get("admin_groups"), list):
            cfg.admin_groups = [str(x) for x in v["admin_groups"]]
        if isinstance(v.get("default_group_id"), str) and v["default_group_id"]:
            cfg.default_group_id = v["default_group_id"]
        if isinstance(v.get("sp_private_key_enc"), str) and v["sp_private_key_enc"]:
            pk = _dec_saml(v["sp_private_key_enc"])
            if pk is not None:
                cfg.sp_private_key = pk
    return cfg


_SAML_SCALARS = ("enabled", "idp_metadata_url", "idp_metadata_xml", "sp_entity_id",
                 "sp_acs_url", "sp_sls_url", "sp_x509_cert", "want_assertions_signed",
                 "want_assertions_encrypted", "want_name_id_encrypted",
                 "authn_requests_signed", "attr_username", "attr_email",
                 "attr_displayname", "attr_groups", "admin_groups", "default_group_id")


async def set_saml_config(
    session: AsyncSession, *, data: dict[str, Any], updated_by_user_id: uuid.UUID
) -> dict[str, Any]:
    from sqlalchemy.orm.attributes import flag_modified
    row = await session.get(SystemSetting, SAML_KEY)
    if row is None:
        row = SystemSetting(key=SAML_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    val: dict[str, Any] = dict(row.value or {})
    for k in _SAML_SCALARS:
        if k in data:
            val[k] = data[k]
    if "sp_private_key" in data:
        pk = data["sp_private_key"]
        if pk:
            val["sp_private_key_enc"] = _enc_saml(str(pk))
        elif pk == "":
            val.pop("sp_private_key_enc", None)
    row.value = val
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.commit()
    return val


# ─────────────────── 稽核轉送到 Graylog（syslog / CEF / GELF）───────────────────
AUDIT_FWD_KEY = "audit_forward"


@dataclass
class AuditForwardConfig:
    enabled: bool
    host: str | None
    port: int
    protocol: str   # tcp | udp
    fmt: str        # gelf | syslog | cef


_af_cache: dict[str, tuple[float, AuditForwardConfig]] = {}


async def get_audit_forward(session: AsyncSession) -> AuditForwardConfig:
    now = time.monotonic()
    c = _af_cache.get(AUDIT_FWD_KEY)
    if c and now - c[0] < _TTL_SEC:
        return c[1]
    cfg = AuditForwardConfig(enabled=False, host=None, port=12201, protocol="udp", fmt="gelf")
    row = await session.get(SystemSetting, AUDIT_FWD_KEY)
    if row and isinstance(row.value, dict):
        v = row.value
        if isinstance(v.get("enabled"), bool):
            cfg.enabled = v["enabled"]
        if isinstance(v.get("host"), str):
            cfg.host = v["host"] or None
        if isinstance(v.get("port"), int):
            cfg.port = v["port"]
        if v.get("protocol") in ("tcp", "udp"):
            cfg.protocol = v["protocol"]
        if v.get("fmt") in ("gelf", "syslog", "cef"):
            cfg.fmt = v["fmt"]
    _af_cache[AUDIT_FWD_KEY] = (now, cfg)
    return cfg


async def set_audit_forward(
    session: AsyncSession, *, data: dict[str, Any], updated_by_user_id: uuid.UUID
) -> AuditForwardConfig:
    from sqlalchemy.orm.attributes import flag_modified

    row = await session.get(SystemSetting, AUDIT_FWD_KEY)
    if row is None:
        row = SystemSetting(key=AUDIT_FWD_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    val = dict(row.value or {})
    for k in ("enabled", "host", "port", "protocol", "fmt"):
        if k in data:
            val[k] = data[k]
    row.value = val
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.commit()
    _af_cache.pop(AUDIT_FWD_KEY, None)
    return await get_audit_forward(session)


# ─────────────────── 通知發送管道（Email 已實作；其餘開發中）───────────────────
NOTIFY_CH_KEY = "notification_channels"
_NOTIFY_AAD = b"notification:smtp_password"
_ncfg_cache: dict[str, tuple[float, dict[str, Any]]] = {}

# 規劃支援的管道；available=False 者前端顯示但反灰（開發中）
NOTIFY_CHANNELS: tuple[tuple[str, bool], ...] = (
    ("email", True),
    ("telegram", True),
    ("slack", True),
    ("teams", True),
    ("nextcloud", True),
    ("zulip", True),
    ("webhook", True),
)

# 通知管道設定欄位（除 email/smtp 外的 webhook 類管道）
_NOTIFY_BOOL_KEYS = (
    "email_enabled", "telegram_enabled", "slack_enabled",
    "teams_enabled", "nextcloud_enabled", "zulip_enabled", "webhook_enabled",
)
_NOTIFY_STR_KEYS = (
    "smtp_host", "smtp_username", "smtp_from",
    "telegram_chat_id", "nextcloud_url", "nextcloud_token",
    "zulip_site", "zulip_bot_email", "zulip_stream", "zulip_topic",
)
# 明文設定欄位 -> 加密儲存欄位（get 會反向解密回明文欄位）
_NOTIFY_SECRETS = {
    "smtp_password": "smtp_password_enc",
    "telegram_token": "telegram_token_enc",
    "slack_webhook": "slack_webhook_enc",
    "teams_webhook": "teams_webhook_enc",
    "nextcloud_secret": "nextcloud_secret_enc",
    "zulip_api_key": "zulip_api_key_enc",
    "webhook_url": "webhook_url_enc",      # 通用 webhook（URL 可能含密鑰，一律加密）
    "webhook_token": "webhook_token_enc",  # 選填 Bearer token
}


def _enc_smtp(pw: str) -> str:
    ct, nonce = encrypt_secret(pw, aad=_NOTIFY_AAD)
    return "v1:" + base64.b64encode(nonce).decode() + ":" + base64.b64encode(ct).decode()


def _dec_smtp(blob: str) -> str | None:
    try:
        _ver, b_nonce, b_ct = blob.split(":", 2)
        return decrypt_secret(
            base64.b64decode(b_ct), base64.b64decode(b_nonce), aad=_NOTIFY_AAD
        ).decode("utf-8")
    except Exception:
        return None


def _default_notify() -> dict[str, Any]:
    cfg: dict[str, Any] = dict.fromkeys(_NOTIFY_BOOL_KEYS, False)
    cfg.update({"smtp_port": 587, "smtp_tls": "starttls"})  # none/starttls/tls
    for k in _NOTIFY_STR_KEYS:
        cfg[k] = None
    for enc in _NOTIFY_SECRETS.values():
        cfg[enc] = None
    return cfg


async def get_notification_channels(session: AsyncSession) -> dict[str, Any]:
    """回傳通知管道設定（含解密後的各管道密鑰；僅後端 send 用，API 層會遮蔽）。"""
    now = time.monotonic()
    c = _ncfg_cache.get(NOTIFY_CH_KEY)
    if c and now - c[0] < _TTL_SEC:
        return dict(c[1])
    cfg = _default_notify()
    row = await session.get(SystemSetting, NOTIFY_CH_KEY)
    if row and isinstance(row.value, dict):
        v = row.value
        for k in _NOTIFY_BOOL_KEYS:
            if isinstance(v.get(k), bool):
                cfg[k] = v[k]
        if isinstance(v.get("smtp_port"), int):
            cfg["smtp_port"] = v["smtp_port"]
        if v.get("smtp_tls") in ("none", "starttls", "tls"):
            cfg["smtp_tls"] = v["smtp_tls"]
        for k in (*_NOTIFY_STR_KEYS, *_NOTIFY_SECRETS.values()):
            if isinstance(v.get(k), str) and v[k]:
                cfg[k] = v[k]
    # 解密每個密鑰到對應明文欄位（smtp_password / telegram_token / slack_webhook / …）
    for plain, enc in _NOTIFY_SECRETS.items():
        cfg[plain] = _dec_smtp(cfg[enc]) if cfg.get(enc) else None
    _ncfg_cache[NOTIFY_CH_KEY] = (now, dict(cfg))
    return cfg


async def set_notification_channels(
    session: AsyncSession, *, data: dict[str, Any], updated_by_user_id: uuid.UUID,
) -> dict[str, Any]:
    from sqlalchemy.orm.attributes import flag_modified
    row = await session.get(SystemSetting, NOTIFY_CH_KEY)
    if row is None:
        row = SystemSetting(key=NOTIFY_CH_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    val = dict(row.value or {})
    for k in (*_NOTIFY_BOOL_KEYS, "smtp_port", "smtp_tls", *_NOTIFY_STR_KEYS):
        if k in data:
            val[k] = data[k]
    # 密鑰：給了非空字串才更新（空字串/未給 = 保留原本）；明確傳 null/"" 清除
    for plain, enc in _NOTIFY_SECRETS.items():
        if plain in data:
            secret = data[plain]
            if secret:
                val[enc] = _enc_smtp(str(secret))
            elif secret == "" or secret is None:
                val.pop(enc, None)
    row.value = val
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.commit()
    _ncfg_cache.pop(NOTIFY_CH_KEY, None)
    return await get_notification_channels(session)


# ─────────────────── 通知矩陣（哪些事件、走哪些管道）───────────────────
NOTIFY_MATRIX_KEY = "notification_matrix"
# 可通知事件登錄（矩陣的列）：(key, 預設站內, 預設 email)。新增事件只要在這裡加一列。
NOTIFY_EVENTS: tuple[tuple[str, bool, bool], ...] = (
    ("ip_request.created", True, True),    # 審核者：有新 IP 申請待審
    ("ip_request.approved", True, True),   # 申請人：申請已核准（含配發 IP）
    ("ip_request.rejected", True, True),   # 申請人：申請已拒絕
    ("cert.expiring", True, False),        # 憑證即將到期 / 已過期
    ("cert.deployed", True, False),        # 代理成功部署新憑證
    ("cert.drift", True, False),           # 憑證飄移（某代理未套到最新版）
    ("anomaly.detected", True, False),     # 異常偵測有新發現
)


def _default_matrix() -> dict[str, dict[str, bool]]:
    return {k: {"in_app": ia, "email": em} for k, ia, em in NOTIFY_EVENTS}


async def get_notification_matrix(session: AsyncSession) -> dict[str, dict[str, bool]]:
    """回傳通知矩陣 {event: {in_app, email}}，未設定的事件用預設值補齊。"""
    out = _default_matrix()
    row = await session.get(SystemSetting, NOTIFY_MATRIX_KEY)
    if row and isinstance(row.value, dict):
        for k, v in row.value.items():
            if k in out and isinstance(v, dict):
                if isinstance(v.get("in_app"), bool):
                    out[k]["in_app"] = v["in_app"]
                if isinstance(v.get("email"), bool):
                    out[k]["email"] = v["email"]
    return out


async def set_notification_matrix(
    session: AsyncSession, *, data: dict[str, Any], updated_by_user_id: uuid.UUID,
) -> dict[str, dict[str, bool]]:
    from sqlalchemy.orm.attributes import flag_modified
    row = await session.get(SystemSetting, NOTIFY_MATRIX_KEY)
    if row is None:
        row = SystemSetting(key=NOTIFY_MATRIX_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    defaults = _default_matrix()
    val: dict[str, dict[str, bool]] = {}
    for k, dflt in defaults.items():
        v = data.get(k) if isinstance(data, dict) else None
        ia = bool(v["in_app"]) if isinstance(v, dict) and isinstance(v.get("in_app"), bool) else dflt["in_app"]
        em = bool(v["email"]) if isinstance(v, dict) and isinstance(v.get("email"), bool) else dflt["email"]
        val[k] = {"in_app": ia, "email": em}
    row.value = val
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.commit()
    return await get_notification_matrix(session)
