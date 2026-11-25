"""使用者貼上的 CA 憑證（PEM）：LDAP、SMTP 等內部服務用企業 CA 或自簽憑證時共用。"""
from __future__ import annotations

#: CA 憑證欄位上限（一整串內部 CA 鏈也遠低於這個大小）
CA_PEM_MAX = 64 * 1024


def ca_subjects(pem: str | None) -> list[str]:
    """解析 PEM 裡的每一張憑證，回傳主旨（畫面顯示「目前信任：…」）；不是有效憑證就丟 ValueError。"""
    if not pem or not pem.strip():
        return []
    from cryptography import x509
    certs = x509.load_pem_x509_certificates(pem.encode())
    return [c.subject.rfc4514_string() for c in certs]


def safe_subjects(pem: str | None) -> list[str]:
    try:
        return ca_subjects(pem)
    except ValueError:
        return []


def clean_ca_pem(pem: str | None) -> str:
    """存檔前檢查並整理：空白＝不使用自訂 CA（回傳 ""）；太大或解析不出憑證丟 ValueError。"""
    pem = (pem or "").strip()
    if not pem:
        return ""
    if len(pem) > CA_PEM_MAX:
        raise ValueError("too large")
    try:
        found = ca_subjects(pem)
    except ValueError:
        found = []      # cryptography 的原文是英文長句加網址，對使用者沒有幫助
    if not found:
        raise ValueError("no -----BEGIN CERTIFICATE----- block could be read")
    return pem + "\n"
