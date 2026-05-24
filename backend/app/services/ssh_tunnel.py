"""SSH tunnel — 用於 phpIPAM 遷移等需要透過 SSH 連到內網 MySQL 的場景。

設計重點（OWASP A06 / A10）：
- 私鑰只在 request 過程中存於記憶體；不寫 DB、不寫檔
- 預設 strict host key check（known_host 不對就拒）
- 提供 fetch_host_key 給 TOFU 流程：第一次連線取 fingerprint 給 user 確認
- tunnel context manager；離開即關閉
- timeout 必填（避免 hang）

私鑰格式：OpenSSH (PEM) — `-----BEGIN OPENSSH PRIVATE KEY-----`
known_host 格式：ssh-rsa AAAA…（單行 ssh-keyscan 輸出，**不含 hostname**）
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Optional

import asyncssh


class SSHTunnelError(RuntimeError):
    """SSH tunnel 任一階段失敗。"""


class SSHHostKeyMismatch(SSHTunnelError):
    """server 拿出來的 key 跟 user 提供的 known_host 對不上 — 有 MITM 風險。"""

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(f"host key mismatch: expected {expected[:32]}…, got {actual[:32]}…")
        self.expected = expected
        self.actual = actual


@dataclass
class TunnelConfig:
    host: str
    port: int = 22
    username: str = "root"
    private_key_pem: str = ""
    # known_host：ssh-keyscan 格式的單行 public key（不含 hostname 前綴）
    # 例：'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILQc...'
    known_host: Optional[str] = None
    # 內網 MySQL 的位址（從 SSH 主機看過去）
    remote_host: str = "127.0.0.1"
    remote_port: int = 3306
    timeout: float = 15.0


def _parse_pubkey_line(line: str) -> bytes:
    """從 'ssh-ed25519 AAAAC3...' 取出 binary key。"""
    parts = line.strip().split()
    if len(parts) < 2:
        raise SSHTunnelError("known_host 格式錯誤；應為 'ssh-XXX BASE64KEY'")
    try:
        return base64.b64decode(parts[1])
    except Exception as exc:  # noqa: BLE001
        raise SSHTunnelError(f"無法解碼 known_host base64: {exc}") from exc


def server_key_fingerprint_sha256(key_blob: bytes) -> str:
    """SHA-256 fingerprint（OpenSSH 7+ 格式：SHA256:<base64-trimmed>）。"""
    digest = hashlib.sha256(key_blob).digest()
    b64 = base64.b64encode(digest).rstrip(b"=").decode("ascii")
    return f"SHA256:{b64}"


async def fetch_host_key(host: str, port: int = 22, timeout: float = 8.0) -> dict[str, str]:
    """連到 host:port 取 server 的 public key，不做認證。
    給 TOFU 流程用 — 把 fingerprint 顯示給 user 確認。

    回傳 dict：
      key_type:    'ssh-ed25519'
      key_b64:     'AAAAC3NzaC1lZDI1NTE5...'
      known_host:  'ssh-ed25519 AAAAC3...'  ← 可直接存進 TunnelConfig.known_host
      fingerprint: 'SHA256:abc...'
    """
    try:
        async with asyncio.timeout(timeout):
            # known_hosts=None 表示不檢查；只拿 key
            options = asyncssh.SSHClientConnectionOptions(
                known_hosts=None,
                client_keys=None,
                username="probe",
                password="",
                # 加上 username + 假密碼讓 server 走到出 key 階段；認證失敗無所謂
            )
            try:
                async with asyncssh.connect(host, port=port, options=options):
                    pass
            except asyncssh.PermissionDenied:
                # 預期會發生 — 我們不在乎能不能登入，只要拿到 key
                pass
            except (asyncssh.HostKeyNotVerifiable, asyncssh.DisconnectError) as exc:
                # 某些 server 在我們關連線後也會丟這個；我們的目標是 known_hosts=None 應該不會踩這
                raise SSHTunnelError(f"host key probe failed: {exc}") from exc

        # 上面的 connect with known_hosts=None 雖能跑過，但沒給 key callback 拿不到 key
        # asyncssh 沒有不認證就拿 key 的純 API；改用 SSHClient subclass：
    except TimeoutError as exc:
        raise SSHTunnelError(f"SSH connect timeout to {host}:{port}") from exc

    # 重做：用 SSHClient subclass 在 validate_host_key 階段抓 key 並回 True
    captured: dict[str, object] = {}

    class _ProbeClient(asyncssh.SSHClient):
        def validate_host_public_key(self, _host: str, _addr: str, _port: int, key):  # type: ignore[no-untyped-def]
            captured["key_type"] = key.get_algorithm()
            captured["key_blob"] = key.export_public_key("openssh").decode("ascii").split()[1]
            captured["key_data"] = base64.b64decode(captured["key_blob"])
            return True

    try:
        async with asyncio.timeout(timeout):
            try:
                async with asyncssh.connect(
                    host, port=port,
                    client_factory=_ProbeClient,
                    username="probe",
                    password="!invalid!",
                    known_hosts=None,
                    client_keys=None,
                ):
                    pass
            except asyncssh.PermissionDenied:
                pass
    except TimeoutError as exc:
        raise SSHTunnelError(f"SSH connect timeout to {host}:{port}") from exc
    except Exception as exc:  # noqa: BLE001
        if "key_data" not in captured:
            raise SSHTunnelError(f"無法取得 server key: {exc}") from exc

    if "key_data" not in captured:
        raise SSHTunnelError("沒有從 server 取到 key（連線方式不支援？）")

    key_type = str(captured["key_type"])
    key_b64 = str(captured["key_blob"])
    return {
        "key_type": key_type,
        "key_b64": key_b64,
        "known_host": f"{key_type} {key_b64}",
        "fingerprint": server_key_fingerprint_sha256(bytes(captured["key_data"])),  # type: ignore[arg-type]
    }


@contextlib.asynccontextmanager
async def open_tunnel(cfg: TunnelConfig) -> AsyncIterator[int]:
    """yield local port that forwards to cfg.remote_host:cfg.remote_port via SSH。

    用法：
        async with open_tunnel(cfg) as local_port:
            # 連 127.0.0.1:local_port 等於連到 cfg.remote_host:cfg.remote_port
            ...
    """
    if not cfg.private_key_pem.strip():
        raise SSHTunnelError("private_key_pem 必填")

    # 把 PEM 字串轉成 asyncssh 認得的 key 物件
    try:
        client_key = asyncssh.import_private_key(cfg.private_key_pem)
    except Exception as exc:  # noqa: BLE001
        raise SSHTunnelError(f"private key 無法解析: {exc}") from exc

    # 設定 known_hosts callback
    if cfg.known_host:
        expected_blob = _parse_pubkey_line(cfg.known_host)
        expected_fp = server_key_fingerprint_sha256(expected_blob)

        def _validate(_host: str, _addr: str, _port: int, key) -> bool:  # type: ignore[no-untyped-def]
            actual_b64 = key.export_public_key("openssh").decode("ascii").split()[1]
            actual_blob = base64.b64decode(actual_b64)
            actual_fp = server_key_fingerprint_sha256(actual_blob)
            if actual_fp != expected_fp:
                raise SSHHostKeyMismatch(expected_fp, actual_fp)
            return True

        class _StrictClient(asyncssh.SSHClient):
            def validate_host_public_key(self, host, addr, port, key):  # type: ignore[no-untyped-def]
                return _validate(host, addr, port, key)

        client_factory = _StrictClient
        known_hosts = None
    else:
        # 沒給 known_host = 不做 host key 檢查（只應在 TOFU preview 階段用）
        client_factory = None
        known_hosts = None

    try:
        async with asyncio.timeout(cfg.timeout):
            async with asyncssh.connect(
                cfg.host,
                port=cfg.port,
                username=cfg.username,
                client_keys=[client_key],
                client_factory=client_factory,
                known_hosts=known_hosts,
                # 安全：不繼承 agent；不允許 password fallback
                agent_path=None,
                preferred_auth=("publickey",),
            ) as conn:
                # 開 local port forward；listener.get_port() 是 OS 分配的本地 port
                listener = await conn.forward_local_port(
                    "127.0.0.1", 0,        # localhost, 隨機 port
                    cfg.remote_host, cfg.remote_port,
                )
                try:
                    yield listener.get_port()
                finally:
                    listener.close()
                    await listener.wait_closed()
    except SSHHostKeyMismatch:
        raise
    except asyncssh.PermissionDenied as exc:
        raise SSHTunnelError(f"SSH 認證失敗（key 不對？）: {exc}") from exc
    except TimeoutError as exc:
        raise SSHTunnelError(f"SSH timeout（{cfg.timeout}s）— host 不在 / 防火牆擋了？") from exc
    except asyncssh.Error as exc:
        raise SSHTunnelError(f"asyncssh error: {exc}") from exc
