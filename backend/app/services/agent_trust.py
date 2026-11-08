"""代理自動改成驗證 jt-ipam（2026-11-07）。

1.0.20 以前的安裝指令一律不驗證伺服器。修正後，已經裝好的代理如果要驗證，原本得逐台重跑安裝指令。
使用者：「叫已經裝過代理的客戶一台一台去做，非常不合理」。所以改成代理升級後自己處理：

- **jt-ipam 身分金鑰**：每台 jt-ipam 一把 RSA-3072，跟 HTTPS 憑證無關、存在 encrypted_secrets
  （系統匯出／匯入會帶走，搬機後代理照樣認得）。
- **信任資料**（`GET /api/v1/agent-trust`，公開、不收代理金鑰）：目前 HTTPS 憑證的指紋，用身分金鑰簽章。
- **還沒驗證的代理**：抓一次信任資料，先試一般 CA 驗證、不行才釘選指紋；**實際用新設定連得上才換**，
  前面有負載平衡器（本機看到的憑證跟代理看到的不同）時不會把自己鎖在外面。同時記下身分金鑰。
- **換憑證**：已驗證的代理連不上時，不帶金鑰再抓一次信任資料，**用記下的身分金鑰驗章**，
  再實際用新指紋連一次，成功才換。假冒的伺服器拿不出簽得過的資料；轉送真的資料也沒用，因為它沒有那張憑證的私鑰。

第一次採用是「首次信任」：那一刻剛好被攔截，代理會記下攔截者的身分。但這種代理本來就完全不驗證，
不會比原本更糟；之後真的 jt-ipam 那邊會看到它離線。系統設定可以關掉自動採用（簽在資料裡）。

只連本機（127.0.0.1 同一個埠）取憑證，埠限定 443 與設定的對外網址的埠：Host 標頭是請求方給的，
不能讓它把伺服器導去探測本機任意的埠。
"""

from __future__ import annotations

import base64
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt_secret, encrypt_secret
from app.models.encrypted_secret import EncryptedSecret
from app.models.system_setting import SystemSetting

OBJECT_TYPE = "server_identity"
OBJECT_ID = uuid.UUID("00000000-0000-0000-0000-0000a9e70001")
FIELD = "rsa_private_key"
AUTO_PIN_KEY = "agent_auto_pin"
FORMAT_VERSION = "1"
#: 本機憑證查一次記多久（代理每幾分鐘就來問；換憑證後最慢這麼久才反映）
_CACHE_SECONDS = 120.0

_key_cache: rsa.RSAPrivateKey | None = None
_identity_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}


def _aad() -> bytes:
    return f"{OBJECT_TYPE}:{OBJECT_ID}:{FIELD}".encode()


async def _load(session: AsyncSession) -> rsa.RSAPrivateKey | None:
    row = (await session.execute(select(EncryptedSecret).where(
        EncryptedSecret.object_type == OBJECT_TYPE, EncryptedSecret.object_id == OBJECT_ID,
        EncryptedSecret.field == FIELD,
    ))).scalar_one_or_none()
    if row is None:
        return None
    key = serialization.load_pem_private_key(decrypt_secret(row.ciphertext, row.nonce, aad=_aad()), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError("server identity key is not RSA")
    return key


async def identity_key(session: AsyncSession) -> rsa.RSAPrivateKey:
    """這台 jt-ipam 的身分金鑰；第一次用到時產生。"""
    global _key_cache
    if _key_cache is not None:
        return _key_cache
    key = await _load(session)
    if key is None:
        new = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        pem = new.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        enc, nonce = encrypt_secret(pem, aad=_aad())
        try:
            async with session.begin_nested():
                session.add(EncryptedSecret(object_type=OBJECT_TYPE, object_id=OBJECT_ID, field=FIELD,
                                            ciphertext=enc, nonce=nonce))
            await session.commit()
            key = new
        except IntegrityError:                 # 另一個 worker 同時產生了一把 → 用它的
            key = await _load(session)
    if key is None:
        raise RuntimeError("server identity key could not be stored")
    _key_cache = key
    return key


def reset_cache() -> None:
    """系統匯入換了身分金鑰、或測試之間清資料時用。"""
    global _key_cache
    _key_cache = None
    _identity_cache.clear()


async def auto_pin_enabled(session: AsyncSession) -> bool:
    row = await session.get(SystemSetting, AUTO_PIN_KEY)
    if row is None or not isinstance(row.value, dict):
        return True
    return bool(row.value.get("enabled", True))


async def set_auto_pin(session: AsyncSession, *, enabled: bool, updated_by_user_id: Any = None) -> bool:
    row = await session.get(SystemSetting, AUTO_PIN_KEY)
    if row is None:
        row = SystemSetting(key=AUTO_PIN_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    row.value = {"enabled": bool(enabled)}
    row.updated_by = updated_by_user_id
    _identity_cache.clear()
    return bool(enabled)


def allowed_ports() -> set[int]:
    ports = {443}
    s = get_settings()
    for url in (s.app_public_url, s.api_public_url):
        p = urlsplit(str(url))
        if p.scheme == "https":
            ports.add(p.port or 443)
    return ports


def parse_host(host_header: str | None) -> tuple[str, int] | None:
    """Host 標頭 → (主機, 埠)；不像主機名稱或 IP、或埠不在允許範圍 → None。"""
    if not host_header or len(host_header) > 300:
        return None
    try:
        p = urlsplit(f"https://{host_header.strip()}")
        host, port = p.hostname, p.port or 443
    except ValueError:
        return None
    if not host or p.path or p.query or p.username or port not in allowed_ports():
        return None
    if not all(c.isalnum() or c in ".-:_" for c in host):
        return None
    return host, port


async def _local(host: str, port: int) -> dict[str, Any]:
    from app.services.server_tls import local_identity

    now = time.monotonic()
    hit = _identity_cache.get((host, port))
    if hit and now - hit[0] < _CACHE_SECONDS:
        return hit[1]
    shown = f"[{host}]" if ":" in host else host
    info = await local_identity(f"https://{shown}:{port}")
    if info.get("available"):
        _identity_cache[(host, port)] = (now, info)
    return info


def _b64_int(n: int) -> str:
    return base64.b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).decode()


async def bundle(session: AsyncSession, host: str, port: int) -> str | None:
    """簽過章的信任資料（純文字，一行一個 key=value，最後一行是 sig=，簽的是它之前的所有位元組）。

    本機取不到憑證（沒走 HTTPS、取不到）→ None。
    """
    info = await _local(host, port)
    if not info.get("available"):
        return None
    key = await identity_key(session)
    pub = key.public_key()
    nums = pub.public_numbers()
    from datetime import datetime
    not_after = int(datetime.fromisoformat(info["not_after"]).timestamp())
    lines = [
        f"jt-ipam-agent-trust={FORMAT_VERSION}",
        f"auto_pin={1 if await auto_pin_enabled(session) else 0}",
        "identity_spki=" + base64.b64encode(pub.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).decode(),
        f"identity_n={_b64_int(nums.n)}",
        f"identity_e={_b64_int(nums.e)}",
        f"spki_pin={info['spki_pin']}",
        f"cert_sha256={info['cert_sha256']}",
        f"not_after={not_after}",
    ]
    body = "".join(line + "\n" for line in lines)
    sig = key.sign(body.encode(), padding.PKCS1v15(), hashes.SHA256())
    return body + "sig=" + base64.b64encode(sig).decode() + "\n"


def upgraded(old: str | None, new: str | None) -> bool:
    """代理從「不驗證」（或舊版沒回報）變成會驗證：記一筆稽核。"""
    return new in ("pinned", "ca") and old in (None, "insecure")


async def record_tls_mode(session: AsyncSession, request: Any, *, object_type: str, object_id: Any,
                          name: str, old: str | None, header: str | None) -> str | None:
    """代理回報的驗證方式 → 存的值；從不驗證變成會驗證時記一筆稽核（自動採用的結果看得到）。"""
    from app.core.audit import append_audit

    new = header if header in ("pinned", "ca", "insecure") else None
    if upgraded(old, new):
        await append_audit(
            session, actor_user_id=None,
            actor_ip=request.client.host if request.client else None,
            actor_user_agent=f"{object_type}/{name}"[:200],
            object_type=object_type, object_id=str(object_id), action="agent_tls_verified",
            diff={"agent": name, "from": old or "legacy", "to": new},
            request_id=getattr(request.state, "request_id", None),
        )
    return new
