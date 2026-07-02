"""相容（legacy）SSH 演算法設定的單元測試（不需 DB / 不需真 server）。

驗證 LEGACY_SSH_ALGS：
- 涵蓋連老舊網路裝置所需的四類演算法（enc / kex / mac / host key）
- 以 "+" 前綴附加（現代裝置仍優先強演算法）
- 刻意排除真正破掉的 arcfour / blowfish / cast / des
- asyncssh 能吃這份設定（SSHClientConnectionOptions 建得起來）
"""

import asyncssh

from app.services.ssh_tunnel import LEGACY_SSH_ALGS


def test_legacy_algs_cover_four_categories():
    assert set(LEGACY_SSH_ALGS) == {
        "encryption_algs",
        "kex_algs",
        "mac_algs",
        "server_host_key_algs",
    }


def test_legacy_algs_are_additive():
    # 每一項都用 "+" 前綴 → 附加到 asyncssh 預設集之後，不取代
    for spec in LEGACY_SSH_ALGS.values():
        assert spec.startswith("+"), spec


def test_legacy_algs_include_old_device_algorithms():
    assert "aes256-cbc" in LEGACY_SSH_ALGS["encryption_algs"]
    assert "3des-cbc" in LEGACY_SSH_ALGS["encryption_algs"]
    assert "diffie-hellman-group1-sha1" in LEGACY_SSH_ALGS["kex_algs"]
    assert "diffie-hellman-group14-sha1" in LEGACY_SSH_ALGS["kex_algs"]
    assert "hmac-sha1" in LEGACY_SSH_ALGS["mac_algs"]
    # 老 D-Link / switch 的 host key 幾乎都是 ssh-rsa（SHA-1）
    assert "ssh-rsa" in LEGACY_SSH_ALGS["server_host_key_algs"]


def test_legacy_algs_exclude_truly_broken_ciphers():
    # 逐 token 比對（避免把刻意保留的 3des-cbc 誤判成單 DES）
    tokens = set()
    for spec in LEGACY_SSH_ALGS.values():
        tokens.update(spec.lstrip("+").split(","))
    for broken in ("arcfour", "blowfish-cbc", "cast128-cbc", "des-cbc"):
        assert broken not in tokens, f"不該啟用已破解的 {broken}"
    # 3des-cbc 是刻意保留的（老裝置相容），確認它在、且不是被誤刪
    assert "3des-cbc" in tokens


def test_asyncssh_accepts_legacy_options():
    # asyncssh 建得起帶 legacy 演算法的 client options，且現代演算法仍在最前面（優先）
    opts = asyncssh.SSHClientConnectionOptions(**LEGACY_SSH_ALGS)
    enc = [a.decode() if isinstance(a, bytes) else a for a in opts.encryption_algs]
    assert "aes256-cbc" in enc
    assert enc[0] != "aes256-cbc"  # 現代演算法排在前面、優先協商
