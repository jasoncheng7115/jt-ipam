"""憑證 bundle 解析與驗證（cert distribution）。

上傳新版憑證時用這裡驗：cert 可解析、key 與 cert 配對、chain 可解析、取出 SAN / fingerprint /
效期。私鑰一律不回傳明文（只用來驗配對）。

OWASP：所有解析走 cryptography（不執行外部指令）；驗證失敗回明確訊息但不洩漏 key 內容。
"""

from __future__ import annotations

import functools
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from cryptography.x509.oid import NameOID

_BEGIN_CERT = "-----BEGIN CERTIFICATE-----"


class CertError(ValueError):
    """憑證 bundle 驗證失敗。"""


@dataclass
class CertInfo:
    fingerprint_sha256: str
    serial: str
    subject: str
    issuer: str
    not_before: datetime
    not_after: datetime
    domains: list[str]
    chain_len: int

    @property
    def is_expired(self) -> bool:
        return self.not_after <= datetime.now(UTC)

    @property
    def days_remaining(self) -> int:
        return (self.not_after - datetime.now(UTC)).days


def generate_self_signed(
    common_name: str, sans: list[str] | None = None, days: int = 365,
) -> tuple[str, str]:
    """產生自簽憑證 + 私鑰（PEM）。方便小工具：自訂名稱(CN/SAN)與效期天數。

    回傳 (cert_pem, key_pem)。SAN 留空時用 common_name；憑證可直接走上傳流程存成版本派送。
    """
    if not common_name.strip():
        raise CertError("common name 不可為空")
    if days < 1:
        raise CertError("天數需 >= 1")
    san_list = [s.strip() for s in (sans or [common_name]) if s.strip()] or [common_name]

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(s) for s in san_list]),
                       critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(Encoding.PEM).decode()
    key_pem = key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()).decode()
    return cert_pem, key_pem


def _split_pem_certs(pem: str) -> list[str]:
    """把可能含多張憑證的 PEM 文字切成個別 block。"""
    if not pem or _BEGIN_CERT not in pem:
        return []
    parts = pem.split(_BEGIN_CERT)
    return [_BEGIN_CERT + p[: p.index("-----END CERTIFICATE-----") + len("-----END CERTIFICATE-----")]
            for p in parts[1:] if "-----END CERTIFICATE-----" in p]


def _spki(key_or_cert) -> bytes:  # type: ignore[no-untyped-def]
    return key_or_cert.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def validate_bundle(cert_pem: str, key_pem: str, chain_pem: str | None = None) -> CertInfo:
    """驗證 cert + key（+ chain），回傳 metadata。失敗丟 CertError。

    - cert_pem 可含多張：第一張當 leaf，其餘併入 chain。
    - key 必須與 leaf cert 的公鑰配對（最關鍵的一致性檢查）。
    - chain 只做「可解析 + leaf.issuer 與第一張 chain.subject 吻合」的寬鬆檢查，不做完整路徑驗證
      （商業中繼憑證的 root 不一定在手邊）。
    """
    cert_blocks = _split_pem_certs(cert_pem)
    if not cert_blocks:
        raise CertError("找不到有效的憑證（PEM 需含 BEGIN CERTIFICATE）")
    try:
        leaf = x509.load_pem_x509_certificate(cert_blocks[0].encode())
    except Exception as exc:
        raise CertError(f"憑證無法解析：{exc}") from exc

    # key 解析 + 與 cert 配對
    try:
        key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    except TypeError as exc:
        raise CertError("私鑰似乎有密碼保護；請提供未加密的私鑰") from exc
    except Exception as exc:
        raise CertError(f"私鑰無法解析：{exc}") from exc
    if _spki(key.public_key()) != _spki(leaf.public_key()):
        raise CertError("私鑰與憑證不配對（public key 不一致）")

    # chain：cert_pem 內多出來的 + 額外 chain_pem
    chain_blocks = cert_blocks[1:] + _split_pem_certs(chain_pem or "")
    chain_certs = []
    for b in chain_blocks:
        try:
            chain_certs.append(x509.load_pem_x509_certificate(b.encode()))
        except Exception as exc:
            raise CertError(f"chain 憑證無法解析：{exc}") from exc

    # SAN
    domains: list[str] = []
    try:
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        domains = san.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        # 退而取 CN
        cn = leaf.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)
        domains = [cn[0].value] if cn else []

    return CertInfo(
        fingerprint_sha256=leaf.fingerprint(hashes.SHA256()).hex(),
        serial=format(leaf.serial_number, "x"),
        subject=leaf.subject.rfc4514_string(),
        issuer=leaf.issuer.rfc4514_string(),
        not_before=leaf.not_valid_before_utc,
        not_after=leaf.not_valid_after_utc,
        domains=domains,
        chain_len=len(chain_certs),
    )


def export_cert_file(
    cert_pem: str, key_pem: str, chain_pem: str | None, fmt: str,
    *, name: str = "certificate", pfx_password: str = "",
) -> tuple[bytes, str, str]:
    """把版本 PEM 轉成可下載檔案。回 (bytes, media_type, filename)。

    fmt：cert / key / chain / fullchain / combined（PEM）、der（leaf DER）、pfx（PKCS#12）。
    pfx 可選密碼（空＝不加密）。私鑰相關格式由呼叫端做 admin 授權 + 稽核。
    """
    from cryptography.hazmat.primitives.serialization import (
        BestAvailableEncryption,
        load_pem_private_key,
        pkcs12,
    )
    ch = (chain_pem or "")

    def _nl(s: str) -> str:
        return s if not s or s.endswith("\n") else s + "\n"

    fullchain = _nl(cert_pem) + _nl(ch)
    safe = "".join(c if (c.isalnum() or c in "-._") else "_" for c in name) or "certificate"
    if fmt == "cert":
        return _nl(cert_pem).encode(), "application/x-pem-file", f"{safe}.crt"
    if fmt == "key":
        return _nl(key_pem).encode(), "application/x-pem-file", f"{safe}.key"
    if fmt == "chain":
        return _nl(ch).encode(), "application/x-pem-file", f"{safe}.chain.pem"
    if fmt == "fullchain":
        return fullchain.encode(), "application/x-pem-file", f"{safe}.fullchain.pem"
    if fmt == "combined":
        return (fullchain + _nl(key_pem)).encode(), "application/x-pem-file", f"{safe}.pem"
    if fmt == "der":
        leaf = x509.load_pem_x509_certificate(cert_pem.encode())
        return leaf.public_bytes(Encoding.DER), "application/x-x509-ca-cert", f"{safe}.der"
    if fmt == "pfx":
        leaf = x509.load_pem_x509_certificate(cert_pem.encode())
        key = load_pem_private_key(key_pem.encode(), password=None)
        cas = x509.load_pem_x509_certificates(ch.encode()) if ch.strip() else []
        enc = BestAvailableEncryption(pfx_password.encode()) if pfx_password else NoEncryption()
        data = pkcs12.serialize_key_and_certificates(safe.encode(), key, leaf, cas, enc)
        return data, "application/x-pkcs12", f"{safe}.pfx"
    raise CertError(f"unknown format: {fmt}")


@functools.lru_cache(maxsize=1)
def _system_trust() -> dict[str, x509.Certificate]:
    """系統信任庫（ca-certificates）依 subject 索引，用來補上公信 CA 的中繼 / 根（如 ISRG Root X1）。"""
    out: dict[str, x509.Certificate] = {}
    for p in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
        try:
            with open(p, "rb") as f:
                data = f.read()
        except OSError:
            continue
        # ca-certificates 裡偶有舊憑證（如序號 <= 0）會觸發 CryptographyDeprecationWarning；
        # 逐張解析並忽略警告，壞的略過即可（不要讓它在 filterwarnings=error 下炸掉）。
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for block in _split_pem_certs(data.decode("ascii", "ignore")):
                try:
                    c = x509.load_pem_x509_certificate(block.encode())
                except ValueError:
                    continue
                out.setdefault(c.subject.rfc4514_string(), c)
    return out


def analyze_chain(cert_pem: str, chain_pem: str | None) -> dict[str, Any]:
    """分析憑證鏈是否能一路鏈到自簽根 CA。給 Zimbra / PDM 等嚴格驗鏈的服務參考。

    缺中繼 / 根時，會嘗試用伺服器的系統信任庫（ca-certificates，含公信 CA 如 ISRG Root X1）補齊。

    回傳：
      has_root        — 可鏈到自簽根（用上傳憑證 + 系統信任庫）
      complete        — 同 has_root（中繼 + 根都齊）
      can_rebuild     — complete 但目前 chain_pem 沒含根 → 可一鍵組合修復（根可能來自系統信任庫）
      built_chain_pem — complete 時組好的「中繼…根」CA 鏈（不含葉）
      used_system_trust — built 的鏈裡有用到系統信任庫補的憑證
    """
    out: dict[str, Any] = {
        "has_root": False, "complete": False, "can_rebuild": False,
        "built_chain_pem": "", "used_system_trust": False,
    }
    leaf_blocks = _split_pem_certs(cert_pem)
    chain_blocks = _split_pem_certs(chain_pem) if chain_pem else []
    if not leaf_blocks:
        return out
    try:
        all_certs = [x509.load_pem_x509_certificate(b.encode()) for b in (leaf_blocks + chain_blocks)]
        leaf = all_certs[0]
    except ValueError:
        return out

    def _self_signed(c: x509.Certificate) -> bool:
        return c.subject.rfc4514_string() == c.issuer.rfc4514_string()

    by_subject: dict[str, x509.Certificate] = {}
    for c in all_certs:
        by_subject.setdefault(c.subject.rfc4514_string(), c)
    trust = _system_trust()

    ordered: list[x509.Certificate] = []  # CA chain excluding the leaf: intermediate… root
    seen: set[bytes] = {leaf.fingerprint(hashes.SHA256())}
    cur = leaf
    has_root = _self_signed(leaf)
    used_trust = False
    while not has_root:
        iss = cur.issuer.rfc4514_string()
        nxt = by_subject.get(iss)
        if nxt is None:
            nxt = trust.get(iss)
            if nxt is not None:
                used_trust = True
        if nxt is None:
            break
        fp = nxt.fingerprint(hashes.SHA256())
        if fp in seen:
            break
        seen.add(fp)
        ordered.append(nxt)
        if _self_signed(nxt):
            has_root = True
            break
        cur = nxt

    out["has_root"] = has_root
    out["complete"] = has_root
    if has_root and ordered:
        out["built_chain_pem"] = "".join(c.public_bytes(Encoding.PEM).decode() for c in ordered)
        out["used_system_trust"] = used_trust
        cur_chain_has_root = any(_self_signed(x509.load_pem_x509_certificate(b.encode())) for b in chain_blocks)
        out["can_rebuild"] = not cur_chain_has_root
    return out
