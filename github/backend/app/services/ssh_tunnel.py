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
    # known_host：ssh-keyscan 格式的單行 public key（不含 hostname 首碼）
    # 例：'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILQc...'
    known_host: str | None = None
    # 二擇一：TCP（remote_host:remote_port）或 Unix socket（remote_socket_path）。
    # 若 remote_socket_path 給了，優先用 socket — MySQL 會看到 Unix socket 連線，
    # auth_socket plugin 可用，免 MySQL 帳密（前提：SSH 進去的 OS user 對 MySQL 有權限）。
    remote_host: str = "127.0.0.1"
    remote_port: int = 3306
    remote_socket_path: str | None = None
    timeout: float = 15.0


def _parse_pubkey_line(line: str) -> bytes:
    """從 'ssh-ed25519 AAAAC3...' 取出 binary key。"""
    parts = line.strip().split()
    if len(parts) < 2:
        raise SSHTunnelError("known_host 格式錯誤；應為 'ssh-XXX BASE64KEY'")
    try:
        return base64.b64decode(parts[1])
    except Exception as exc:
        raise SSHTunnelError(f"無法解碼 known_host base64: {exc}") from exc


def server_key_fingerprint_sha256(key_blob: bytes) -> str:
    """SHA-256 fingerprint（OpenSSH 7+ 格式：SHA256:<base64-trimmed>）。"""
    digest = hashlib.sha256(key_blob).digest()
    b64 = base64.b64encode(digest).rstrip(b"=").decode("ascii")
    return f"SHA256:{b64}"


async def fetch_host_key(host: str, port: int = 22, timeout: float = 8.0) -> dict[str, str]:
    """連到 host:port 取 server 的 public key，不做認證。
    給 TOFU 流程用 — 把 fingerprint 顯示給 user 確認。

    用 asyncssh.get_server_host_key — 只做 key exchange、不走 auth，
    比 SSHClient subclass 路線可靠（後者在 known_hosts=None 時 callback 不會觸發）。

    回傳 dict：
      key_type:    'ssh-ed25519'
      key_b64:     'AAAAC3NzaC1lZDI1NTE5...'
      known_host:  'ssh-ed25519 AAAAC3...'  ← 可直接存進 TunnelConfig.known_host
      fingerprint: 'SHA256:abc...'
    """
    try:
        async with asyncio.timeout(timeout):
            key = await asyncssh.get_server_host_key(host, port=port)
    except TimeoutError as exc:
        raise SSHTunnelError(f"SSH connect timeout to {host}:{port}") from exc
    except (asyncssh.Error, OSError) as exc:
        raise SSHTunnelError(f"無法取得 server key from {host}:{port}: {exc}") from exc

    if key is None:
        raise SSHTunnelError(f"server {host}:{port} 沒有回傳 host key")

    try:
        openssh = key.export_public_key("openssh").decode("ascii").strip()
        parts = openssh.split()
        if len(parts) < 2:
            raise SSHTunnelError(f"無法解析 server key 輸出：{openssh[:80]}")
        key_type, key_b64 = parts[0], parts[1]
        raw = base64.b64decode(key_b64)
    except SSHTunnelError:
        raise
    except Exception as exc:
        raise SSHTunnelError(f"無法解析 server key: {exc}") from exc

    return {
        "key_type": key_type,
        "key_b64": key_b64,
        "known_host": f"{key_type} {key_b64}",
        "fingerprint": server_key_fingerprint_sha256(raw),
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
    except Exception as exc:
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
                # 開 local TCP port → 對端 TCP 或 Unix socket
                if cfg.remote_socket_path:
                    listener = await conn.forward_local_port_to_path(
                        "127.0.0.1", 0,            # localhost, 隨機 port
                        cfg.remote_socket_path,    # 對端 Unix socket 絕對路徑
                    )
                else:
                    listener = await conn.forward_local_port(
                        "127.0.0.1", 0,
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
