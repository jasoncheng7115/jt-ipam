"""機密欄位編解碼。

匯出（在來源機執行）：用來源機 ENCRYPTION_KEY 解密所有機密 → 明文，放進匯出包。
匯入（在目標機執行）：用目標機 ENCRYPTION_KEY 以「相同 AAD」重新加密明文。

因為匯入會原樣保留來源 UUID，所有綁 object_id 的 AAD（如 b"dns_server:<id>:api_key"）
在目標機仍成立。四種機密表徵：
1. 欄位對 `<f>_enc` + `<f>_nonce`（直接掛在 model 上）—— COLUMN_SECRETS
2. 中央 `encrypted_secrets` 表（AAD = f"{object_type}:{object_id}:{field}"）—— 通用
3. envelope JSONB `ssh_credentials.secrets_enc`（每欄位一個信封）—— ENVELOPE_SECRETS
4. `system_settings` 值內嵌的 "v1:nonce:ct" 或 b64 機密字串 —— SETTINGS_SECRETS

匯出時解不開的機密（金鑰不符 / 資料損毀）記為 None、匯入時該欄留空，不中斷整批。
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

from app.core.security import (
    decrypt_secret,
    encrypt_secret,
    envelope_decrypt,
    envelope_encrypt,
)

# ─────────────────────────── 1. 欄位對 *_enc / *_nonce ───────────────────────────
# 每項：table -> list of (logical, enc_col, nonce_col, aad_from_row)
# aad_from_row(row: dict) -> bytes，可用 row 內其他欄位（id / certificate_id / fingerprint…）


def _aad_id(fmt: str) -> Callable[[dict[str, Any]], bytes]:
    return lambda row: fmt.format(id=row["id"]).encode()


COLUMN_SECRETS: dict[str, list[tuple[str, str, str, Callable[[dict[str, Any]], bytes]]]] = {
    "users": [("totp", "totp_secret_enc", "totp_nonce", _aad_id("user:{id}:totp"))],
    "adguard_instances": [
        ("api_password", "api_password_enc", "api_password_nonce", _aad_id("adguard:{id}:api_password")),
    ],
    "wazuh_instances": [
        ("api_password", "api_password_enc", "api_password_nonce", _aad_id("wazuh_instance:{id}:api_password")),
    ],
    "librenms_instances": [
        ("api_token", "api_token_enc", "api_token_nonce", _aad_id("librenms_instance:{id}:api_token")),
    ],
    "pfsense_firewalls": [
        ("api_key", "api_key_enc", "api_key_nonce", _aad_id("pfsense_firewall:{id}:api_key")),
    ],
    "opnsense_firewalls": [
        ("api_key", "api_key_enc", "api_key_nonce", _aad_id("opnsense_firewall:{id}:api_key")),
        ("api_secret", "api_secret_enc", "api_secret_nonce", _aad_id("opnsense_firewall:{id}:api_secret")),
    ],
    "webhook_subscriptions": [
        ("secret", "secret_enc", "secret_nonce", _aad_id("webhook:{id}:secret")),
    ],
    "cert_versions": [
        (
            "key",
            "key_enc",
            "key_nonce",
            lambda row: f"cert_version:{row['certificate_id']}:{row['fingerprint_sha256']}".encode(),
        ),
    ],
}

# 這些欄位對雖存在於 model，但已無寫入路徑（legacy），無從得知 AAD → 匯出時直接丟棄。
DROP_COLUMNS: dict[str, list[str]] = {
    "scan_agents": ["api_token_enc", "api_token_nonce"],
}

# ─────────────────────────── 3. envelope JSONB ───────────────────────────
# table -> (json_col, aad(owner_id, field))
ENVELOPE_SECRETS: dict[str, tuple[str, str, Callable[[Any, str], bytes]]] = {
    # (json_col, owner_col, aad)
    "ssh_credentials": (
        "secrets_enc",
        "owner_user_id",
        lambda owner, field: f"ssh_cred:{owner}:{field}".encode(),
    ),
}

# ─────────────────────────── 4. system_settings 內嵌機密 ───────────────────────────
# key -> {enc_field: aad_bytes}；均為 "v1:b64nonce:b64ct" 格式（除 phpipam 為 b64 pair）
_SETTINGS_V1: dict[str, dict[str, bytes]] = {
    "llm": {"mcp_api_key_enc": b"llm:mcp_api_key"},
    "ldap": {"bind_password_enc": b"ldap:bind_password"},
    "oidc": {"client_secret_enc": b"oidc:client_secret"},
    "saml": {"sp_private_key_enc": b"saml:sp_private_key"},
    "notification_channels": dict.fromkeys(("smtp_password_enc", "telegram_token_enc", "slack_webhook_enc", "teams_webhook_enc", "nextcloud_secret_enc", "zulip_api_key_enc", "webhook_url_enc", "webhook_token_enc"), b"notification:smtp_password"),
}
# phpipam_migration 用 b64(ct)+b64(nonce) 且無 AAD
_SETTINGS_B64PAIR = {"phpipam_migration": ("key_enc", "key_nonce")}

_PLAIN = "__plain__"  # 匯出包內明文哨兵：{"__plain__": "<明文>"} 或 None


# ══════════════════════════════ COLUMN 機密 ══════════════════════════════
def strip_column_secrets(table: str, row: dict[str, Any]) -> dict[str, Any] | None:
    """就地把 row 內欄位對機密解密→放進回傳的 `__secrets__`，並移除 enc/nonce 欄位。

    回 None 表示此表無欄位機密（呼叫端無需附掛）。
    """
    for col in DROP_COLUMNS.get(table, []):
        row.pop(col, None)
    specs = COLUMN_SECRETS.get(table)
    if not specs:
        return None
    out: dict[str, Any] = {}
    for logical, enc_col, nonce_col, aad_fn in specs:
        enc = row.pop(enc_col, None)
        nonce = row.pop(nonce_col, None)
        out[logical] = _decrypt_col(enc, nonce, aad_fn, row)
    return out


def _decrypt_col(enc: Any, nonce: Any, aad_fn: Callable[[dict], bytes], row: dict) -> str | None:
    if not enc or not nonce:
        return None
    try:
        return decrypt_secret(_as_bytes(enc), _as_bytes(nonce), aad=aad_fn(row)).decode("utf-8")
    except Exception:
        return None


def apply_column_secrets(table: str, row: dict[str, Any], secrets: dict[str, Any] | None) -> None:
    """匯入：把明文機密以目標金鑰重加密，寫回 row 的 enc/nonce 欄位（就地修改 row）。"""
    for col in DROP_COLUMNS.get(table, []):
        row.pop(col, None)
    specs = COLUMN_SECRETS.get(table)
    if not specs:
        return
    secrets = secrets or {}
    for logical, enc_col, nonce_col, aad_fn in specs:
        plain = secrets.get(logical)
        if plain:
            enc, nonce = encrypt_secret(str(plain), aad=aad_fn(row))
            row[enc_col] = enc
            row[nonce_col] = nonce
        else:
            row[enc_col] = None
            row[nonce_col] = None


# ══════════════════════════════ envelope JSONB ══════════════════════════════
def strip_envelope_secrets(table: str, row: dict[str, Any]) -> dict[str, Any] | None:
    spec = ENVELOPE_SECRETS.get(table)
    if not spec:
        return None
    json_col, owner_col, aad_fn = spec
    blob = row.get(json_col)
    owner = row.get(owner_col)
    plain: dict[str, Any] = {}
    if isinstance(blob, dict):
        for field, env in blob.items():
            try:
                plain[field] = envelope_decrypt(env, aad=aad_fn(owner, field))
            except Exception:
                plain[field] = None
    row[json_col] = None
    return {json_col: plain}


def apply_envelope_secrets(table: str, row: dict[str, Any], secrets: dict[str, Any] | None) -> None:
    spec = ENVELOPE_SECRETS.get(table)
    if not spec:
        return
    json_col, owner_col, aad_fn = spec
    owner = row.get(owner_col)
    plain = (secrets or {}).get(json_col) or {}
    rebuilt: dict[str, Any] = {}
    for field, value in plain.items():
        if value is None:
            continue
        rebuilt[field] = envelope_encrypt(str(value), aad=aad_fn(owner, field))
    row[json_col] = rebuilt or None


# ══════════════════════════════ system_settings 內嵌 ══════════════════════════════
def transform_settings_out(key: str, value: Any) -> Any:
    """匯出：把 system_settings 某 row 的 value（JSONB）內的機密字串換成明文哨兵。"""
    if not isinstance(value, dict):
        return value
    v = dict(value)
    for enc_field, aad in _SETTINGS_V1.get(key, {}).items():
        blob = v.get(enc_field)
        if isinstance(blob, str) and blob:
            v[enc_field] = {_PLAIN: _dec_v1(blob, aad)}
    if key in _SETTINGS_B64PAIR:
        enc_f, nonce_f = _SETTINGS_B64PAIR[key]
        ct, nonce = v.get(enc_f), v.get(nonce_f)
        if isinstance(ct, str) and isinstance(nonce, str) and ct and nonce:
            v[enc_f] = {_PLAIN: _dec_b64pair(ct, nonce)}
            v.pop(nonce_f, None)
    return v


def transform_settings_in(key: str, value: Any) -> Any:
    """匯入：把明文哨兵以目標金鑰重加密回機密字串。"""
    if not isinstance(value, dict):
        return value
    v = dict(value)
    for enc_field, aad in _SETTINGS_V1.get(key, {}).items():
        cell = v.get(enc_field)
        if isinstance(cell, dict) and _PLAIN in cell:
            plain = cell[_PLAIN]
            v[enc_field] = _enc_v1(str(plain), aad) if plain else None
    if key in _SETTINGS_B64PAIR:
        enc_f, nonce_f = _SETTINGS_B64PAIR[key]
        cell = v.get(enc_f)
        if isinstance(cell, dict) and _PLAIN in cell:
            plain = cell[_PLAIN]
            if plain:
                enc, nonce = encrypt_secret(str(plain))
                v[enc_f] = base64.b64encode(enc).decode()
                v[nonce_f] = base64.b64encode(nonce).decode()
            else:
                v[enc_f] = None
                v[nonce_f] = None
    return v


def _dec_v1(blob: str, aad: bytes) -> str | None:
    try:
        _ver, b_nonce, b_ct = blob.split(":", 2)
        return decrypt_secret(base64.b64decode(b_ct), base64.b64decode(b_nonce), aad=aad).decode("utf-8")
    except Exception:
        return None


def _enc_v1(plain: str, aad: bytes) -> str:
    ct, nonce = encrypt_secret(plain, aad=aad)
    return "v1:" + base64.b64encode(nonce).decode() + ":" + base64.b64encode(ct).decode()


def _dec_b64pair(ct_b64: str, nonce_b64: str) -> str | None:
    try:
        return decrypt_secret(base64.b64decode(ct_b64), base64.b64decode(nonce_b64)).decode("utf-8")
    except Exception:
        return None


# ══════════════════════════════ 中央 encrypted_secrets ══════════════════════════════
def central_secret_aad(object_type: str, object_id: Any, field: str) -> bytes:
    return f"{object_type}:{object_id}:{field}".encode()


def export_central_row(row: dict[str, Any]) -> dict[str, Any]:
    """把一列 encrypted_secrets 解密成 {object_type, object_id, field, key_id, plaintext}。"""
    aad = central_secret_aad(row["object_type"], row["object_id"], row["field"])
    plain: str | None
    try:
        plain = decrypt_secret(_as_bytes(row["ciphertext"]), _as_bytes(row["nonce"]), aad=aad).decode("utf-8")
    except Exception:
        plain = None
    return {
        "object_type": row["object_type"],
        "object_id": str(row["object_id"]),
        "field": row["field"],
        "key_id": row.get("key_id", "primary"),
        "plaintext": plain,
    }


def import_central_row(entry: dict[str, Any]) -> dict[str, Any] | None:
    """把匯出包內的中央機密明文以目標金鑰重加密成可插入 encrypted_secrets 的 row。"""
    plain = entry.get("plaintext")
    if plain is None:
        return None
    aad = central_secret_aad(entry["object_type"], entry["object_id"], entry["field"])
    ct, nonce = encrypt_secret(str(plain), aad=aad)
    return {
        "object_type": entry["object_type"],
        "object_id": entry["object_id"],
        "field": entry["field"],
        "key_id": entry.get("key_id", "primary"),
        "ciphertext": ct,
        "nonce": nonce,
    }


def _as_bytes(v: Any) -> bytes:
    if isinstance(v, memoryview):
        return v.tobytes()
    if isinstance(v, bytes):
        return v
    if isinstance(v, str):  # 來自 JSON 的 b64
        return base64.b64decode(v)
    return bytes(v)
