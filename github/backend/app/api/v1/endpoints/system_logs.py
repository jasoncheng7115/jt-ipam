"""系統記錄查詢（管理員）：讀 jt-ipam 各 systemd 服務的 journalctl。

OWASP：
- 僅管理員（require_admin）。
- service 走白名單；lines 為整數夾限；以 list 參數呼叫 subprocess（不經 shell，無注入）。
- 僅回讀，不做任何控制（start/stop/restart 不開放）。
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.v1.dependencies import require_admin

router = APIRouter(prefix="/system/logs", tags=["system-logs"],
                   dependencies=[Depends(require_admin)])

# 可查的服務白名單（label → systemd unit）
SERVICES: dict[str, str] = {
    "backend": "jt-ipam-backend",
    "sync": "jt-ipam-sync",
    "scan-agent": "jt-ipam-scan-agent",
    "oui-refresh": "jt-ipam-oui-refresh",
    "geoip-refresh": "jt-ipam-geoip-refresh",
    "backup": "jt-ipam-backup",
}


@router.get("/services")
async def list_services() -> dict[str, list[str]]:
    return {"services": list(SERVICES.keys())}


@router.get("")
async def read_logs(
    service: Annotated[str, Query()] = "backend",
    lines: Annotated[int, Query(ge=10, le=5000)] = 300,
) -> dict[str, object]:
    unit = SERVICES.get(service)
    if unit is None:
        raise HTTPException(status_code=400, detail="Unknown service")
    journalctl = shutil.which("journalctl")
    if journalctl is None:
        raise HTTPException(status_code=503, detail="journalctl not available")
    # 不經 shell；固定參數 + 白名單 unit + 整數 lines
    proc = await asyncio.create_subprocess_exec(
        journalctl, "-u", unit, "-n", str(lines), "--no-pager", "--output", "short-iso",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    except TimeoutError as exc:
        proc.kill()
        raise HTTPException(status_code=504, detail="journalctl timed out") from exc
    text = out.decode("utf-8", errors="replace")
    return {"service": service, "unit": unit, "lines": lines, "text": text}
