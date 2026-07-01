"""BMC OOB主控台（IPMI 2.0 SOL）服務 — 跨廠、耐韌體改版的地基。

設計（見規格）：
- 鍵盤 + 文字畫面走 IPMI SOL（RMCP+, UDP 623），用 ipmitool 子行程承載；vendor-agnostic。
- cipher suite 自動回退（先試 17，退而 3）—— 實測舊韌體（如 Supermicro X11 FW1.74）只吃 3。
- 密碼一律經 IPMI_PASSWORD 環境變數 + `-E` 傳入，**不進 argv / shell**（密碼常含 `!`/`@`）。
- 非破壞：本模組只做連線自我檢查 + SOL（鍵盤/文字畫面），不含電源/感測/開機覆寫。
"""
from __future__ import annotations

import asyncio
import os
import pty
import shutil
import subprocess

# 先試 17（較新韌體），失敗退 3（舊韌體常見）
CIPHERS_TO_TRY = (17, 3)

# BMC 廠商（manufacturer id → 影響 Phase 3 圖形 adapter 選擇；SOL 不需要）
_VENDOR_BY_MFR = {
    "10876": "supermicro",   # Supermicro
    "47488": "supermicro",
    "4753": "ami",           # AMI MegaRAC（ASRock Rack 多為此）
}


def bmc_available() -> bool:
    """後端是否具備 IPMI 工具鏈（ipmitool）。"""
    return shutil.which("ipmitool") is not None


def _base_args(ip: str, user: str, cipher: int) -> list[str]:
    return ["ipmitool", "-I", "lanplus", "-C", str(cipher), "-H", ip, "-U", user, "-E"]


def _env_with_password(password: str) -> dict[str, str]:
    env = dict(os.environ)
    env["IPMI_PASSWORD"] = password  # ipmitool -E 從這裡讀，避免進 argv
    return env


async def _run(
    ip: str, user: str, password: str, cipher: int, extra: list[str], timeout: float = 15.0,
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *_base_args(ip, user, cipher), *extra,
        env=_env_with_password(password),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        return 124, "", "timeout"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def _field(text: str, key: str) -> str | None:
    for line in text.splitlines():
        if line.strip().lower().startswith(key.lower()):
            parts = line.split(":", 1)
            if len(parts) == 2:
                return parts[1].strip()
    return None


async def self_check(ip: str, user: str, password: str) -> dict:
    """連線自我檢查：找出可用 cipher、SOL 是否啟用、廠商、韌體版本。

    回傳 {ok, cipher, sol_enabled, vendor, fw, privilege, error}。
    """
    last_err = ""
    for cipher in CIPHERS_TO_TRY:
        rc, out, err = await _run(ip, user, password, cipher, ["mc", "info"])
        if rc == 0:
            mfr_id = _field(out, "Manufacturer ID") or ""
            mfr_name = _field(out, "Manufacturer Name") or ""
            vendor = _VENDOR_BY_MFR.get(mfr_id.split()[0] if mfr_id else "", "")
            if not vendor and mfr_name:
                low = mfr_name.lower()
                vendor = "supermicro" if "supermicro" in low else ("ami" if "ami" in low else "generic")
            vendor = vendor or "generic"
            _rc2, sout, _serr = await _run(ip, user, password, cipher, ["sol", "info"])
            sol_enabled = (_field(sout, "Enabled") or "").lower() == "true"
            return {
                "ok": True, "cipher": cipher, "sol_enabled": sol_enabled,
                "vendor": vendor, "fw": _field(out, "Firmware Revision"),
                "privilege": _field(sout, "Privilege Level"), "error": None,
            }
        last_err = (err or out).strip().splitlines()[0] if (err or out).strip() else "connection failed"
    return {"ok": False, "cipher": None, "sol_enabled": False, "vendor": None,
            "fw": None, "privilege": None, "error": last_err or "connection failed"}


async def deactivate_sol(ip: str, user: str, password: str, cipher: int) -> None:
    """清掉殘留的 SOL session（SOL 單一 session，連線前先嘗試釋放）。失敗忽略。"""
    try:
        await _run(ip, user, password, cipher, ["sol", "deactivate"], timeout=10.0)
    except Exception:
        pass


def spawn_sol(ip: str, user: str, password: str, cipher: int) -> tuple[subprocess.Popen, int]:
    """以 pty 起一個 `ipmitool sol activate` 子行程，回傳 (proc, master_fd)。

    呼叫端負責 master_fd 的讀寫中繼到 WebSocket，以及結束時 kill + close。
    """
    master, slave = pty.openpty()
    env = _env_with_password(password)
    env["TERM"] = "xterm-256color"
    proc = subprocess.Popen(  # noqa: S603 — 固定 argv、密碼走 env、非 shell
        [*_base_args(ip, user, cipher), "sol", "activate"],
        stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True,
    )
    os.close(slave)
    return proc, master
