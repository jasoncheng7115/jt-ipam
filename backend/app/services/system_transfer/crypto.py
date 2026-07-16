"""匯出檔封套加解密。

匯出檔是一份 JSON 封套：metadata 明文（可檢視），實際 payload（gzip 後的 inner
JSON，內含資料＋機密明文）以使用者輸入的密碼保護：scrypt 從密碼導出 32-byte 金鑰，
再用 AES-256-GCM 加密。這一層讓機密與基礎設施資料在檔案裡都是加密的，且與兩端各自的
ENCRYPTION_KEY 無關 —— 匯出時解密機密、封套加密；匯入時封套解密、再以目標金鑰重加密。
"""

from __future__ import annotations

import base64
import gzip
import json
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

FORMAT = "jt-ipam-system-export"
FORMAT_VERSION = 1

# scrypt 參數（互動式解密可接受；記進封套讓未來可調參數而不破相容）
_SCRYPT_N = 2**15  # 32768
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32
_SALT_LEN = 16
_NONCE_LEN = 12


class TransferCryptoError(Exception):
    """密碼錯誤或封套損毀（呼叫端應轉成可讀的 400，而非 500）。"""


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s)


def _derive_key(passphrase: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    kdf = Scrypt(salt=salt, length=_KEY_LEN, n=n, r=r, p=p)
    return kdf.derive(passphrase.encode("utf-8"))


def seal(
    inner: dict[str, Any],
    passphrase: str,
    *,
    metadata: dict[str, Any],
    rng: Any,
) -> dict[str, Any]:
    """把 inner dict 壓縮＋加密成完整封套 dict（可 json.dumps 落檔）。

    `rng` 需提供 `token_bytes(n)`（傳 secrets 模組即可）；抽出來是為了測試可重現。
    `metadata` 會原樣併進封套頂層（format/app_version/schema_version/scope/exported_at…）。
    """
    if not passphrase:
        raise TransferCryptoError("匯出密碼不可為空")
    salt = rng.token_bytes(_SALT_LEN)
    nonce = rng.token_bytes(_NONCE_LEN)
    key = _derive_key(passphrase, salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    raw = gzip.compress(json.dumps(inner, ensure_ascii=False, default=str).encode("utf-8"))
    ct = AESGCM(key).encrypt(nonce, raw, None)
    env: dict[str, Any] = dict(metadata)
    env.update(
        {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "encrypted": True,
            "cipher": "AES-256-GCM",
            "kdf": {"algo": "scrypt", "salt": _b64e(salt), "n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P},
            "nonce": _b64e(nonce),
            "payload": _b64e(ct),
        }
    )
    return env


def read_metadata(env: dict[str, Any]) -> dict[str, Any]:
    """不需密碼即可讀取的頂層 metadata（前端 analyze 顯示來源版本／範圍用）。"""
    if not isinstance(env, dict) or env.get("format") != FORMAT:
        raise TransferCryptoError("這不是有效的 jt-ipam 系統匯出檔")
    return {
        "format_version": env.get("format_version"),
        "app_version": env.get("app_version"),
        "schema_version": env.get("schema_version"),
        "scope": env.get("scope") or [],
        "exported_at": env.get("exported_at"),
        "encrypted": bool(env.get("encrypted", True)),
    }


def open_envelope(env: dict[str, Any], passphrase: str) -> dict[str, Any]:
    """驗證封套、以密碼解密並解壓，回 inner dict。密碼錯 → TransferCryptoError。"""
    if not isinstance(env, dict) or env.get("format") != FORMAT:
        raise TransferCryptoError("這不是有效的 jt-ipam 系統匯出檔")
    fv = env.get("format_version")
    if not isinstance(fv, int) or fv > FORMAT_VERSION:
        raise TransferCryptoError(f"匯出檔格式版本 {fv} 較新，此實例無法解析（請升級後再匯入）")
    kdf = env.get("kdf") or {}
    try:
        salt = _b64d(str(kdf["salt"]))
        nonce = _b64d(str(env["nonce"]))
        ct = _b64d(str(env["payload"]))
        n = int(kdf.get("n", _SCRYPT_N))
        r = int(kdf.get("r", _SCRYPT_R))
        p = int(kdf.get("p", _SCRYPT_P))
    except (KeyError, ValueError, TypeError) as exc:
        raise TransferCryptoError("匯出檔封套損毀或欄位缺失") from exc
    key = _derive_key(passphrase, salt, n=n, r=r, p=p)
    try:
        raw = AESGCM(key).decrypt(nonce, ct, None)
    except InvalidTag as exc:
        raise TransferCryptoError("密碼錯誤或檔案已損毀") from exc
    try:
        inner = json.loads(gzip.decompress(raw).decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise TransferCryptoError("匯出檔內容無法解壓／解析") from exc
    if not isinstance(inner, dict):
        raise TransferCryptoError("匯出檔內容格式不正確")
    return inner
