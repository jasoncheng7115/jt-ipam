"""jt-ipam 自己出示的 HTTPS 憑證（2026-11-04）：給派送代理的安裝指令釘選用。

舊的安裝指令一律關掉 TLS 驗證（curl -k、JT_IPAM_INSECURE），能攔截網路的人就能假冒 jt-ipam：拿走代理金鑰、
推一支以 root / SYSTEM 執行的腳本。新的安裝指令帶 jt-ipam 憑證的指紋，代理只跟這台伺服器講話：
- Linux 用 curl 的 `--pinnedpubkey sha256//<base64>`（公鑰的 SHA-256，jt-ipam 換憑證但沿用金鑰時仍有效）
- Windows 用憑證本身的 SHA-256（.NET Framework 取不出 SubjectPublicKeyInfo，PowerShell 5.1 只好比整張憑證）

**只連本機**：127.0.0.1 的同一個埠、SNI 帶瀏覽器網址的主機名稱。拿到的就是這台實際送出去的那張，也不會被拿來
連到別的主機。前面有負載平衡器在終結 TLS 時，本機那張跟使用者看到的不同，畫面會讓管理員改用一般的憑證驗證。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import socket
import ssl
from typing import Any
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _verifies(host: str, port: int, *, check_hostname: bool, timeout: float) -> bool:
    try:
        vctx = ssl.create_default_context()
        vctx.minimum_version = ssl.TLSVersion.TLSv1_2     # CodeQL #61：預設還允許 TLS 1.0 / 1.1
        vctx.check_hostname = check_hostname
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as raw, \
                vctx.wrap_socket(raw, server_hostname=host):
            return True
    except (ssl.SSLError, OSError):
        return False


def _fetch(host: str, port: int, timeout: float = 5.0) -> tuple[bytes, bool, bool]:
    """(本機出示的憑證 DER, 用系統 CA 驗得過且主機名稱相符, 憑證鏈本身是公開 CA 簽的（不管主機名稱）)"""
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2          # CodeQL #62
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as raw, \
            ctx.wrap_socket(raw, server_hostname=host) as tls:
        der = tls.getpeercert(binary_form=True) or b""
    trusted = _verifies(host, port, check_hostname=True, timeout=timeout)
    chain = trusted or _verifies(host, port, check_hostname=False, timeout=timeout)
    return der, trusted, chain


def pins_of(der: bytes) -> dict[str, str]:
    """憑證 DER → 兩種代理用的指紋。"""
    cert = x509.load_der_x509_certificate(der)
    spki = cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return {
        "spki_pin": "sha256//" + base64.b64encode(hashlib.sha256(spki).digest()).decode(),
        "cert_sha256": hashlib.sha256(der).hexdigest().upper(),
        "subject": cert.subject.rfc4514_string(),
        "issuer": cert.issuer.rfc4514_string(),
        "not_after": cert.not_valid_after_utc.isoformat(),
    }


async def local_identity(origin: str) -> dict[str, Any]:
    parts = urlsplit(origin.strip())
    if parts.scheme != "https" or not parts.hostname:
        return {"available": False, "reason": "not_https"}
    port = parts.port or 443
    try:
        der, trusted, chain = await asyncio.to_thread(_fetch, parts.hostname, port)
    except (OSError, ssl.SSLError, ValueError) as exc:
        return {"available": False, "reason": "unreachable", "detail": str(exc)[:200]}
    if not der:
        return {"available": False, "reason": "no_certificate"}
    # chain_valid 但 ca_trusted 不成立：多半是用 IP 開啟、而憑證是發給網域名稱的公開 CA 憑證（例如 Let's Encrypt）。
    # 這種憑證會定期續約、通常連金鑰一起換，釘選的指紋到時就對不上 → 畫面提醒改用網域名稱開啟、走 CA 驗證。
    return {"available": True, "ca_trusted": trusted, "chain_valid": chain, **pins_of(der)}
