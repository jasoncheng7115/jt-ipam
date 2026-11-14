"""系統診斷的「容量用量」：jt-ipam 各個功能吃掉多少空間（使用者 2026-10-14）。

- 資料庫：總大小、每張表（含索引與 TOAST），依功能分類加總
- 主機上的目錄：備份、程式與套件、資料目錄、日誌（都是 jt-ipam 自己的；沙箱裡讀得到）

只在本機計算、只給管理員看。目錄逐一走訪有時間上限，量不完就標「約」；讀不到（權限）就照實說。
結果快取幾分鐘：診斷頁每開一次就走遍幾萬個檔案沒有必要。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ── 資料表 → 功能分類 ─────────────────────────────────────────────────────
# 依序比對，第一個符合的算數（完整名稱或前綴）。新增資料表要加進來：守門測試會擋「其他」。
GROUP_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("history", ("ip_change_log", "ip_liveness_days", "scan_agent_cycles", "anomaly_observations")),
    ("audit", ("audit_logs",)),
    ("notifications", ("notifications",)),
    ("change_impact", ("impact_", "change_plan", "change_tasks")),
    ("ai", ("ai_", "warroom_intel_")),
    ("reference", ("oui_vendors", "recog_databases")),
    ("dns_dhcp", ("dns_", "dhcp_", "kea_", "isc_dhcp", "windows_dhcp", "technitium_", "isoinsight_", "adguard_",
                  "checkpoint_dhcp", "external_dns_", "domains")),
    ("firewall", ("opnsense_", "pfsense_", "fortigate_", "paloalto_", "checkpoint_", "mikrotik_", "pve_firewall_",
                  "nat_translations", "vpn_tunnels", "fw_rule_snapshots", "integration_own_addresses")),
    ("network_data", ("arp_entries", "fdb_entries", "librenms_", "wireless_", "snmp_")),
    ("physical", ("devices", "device_", "racks", "cables", "cable_", "locations", "circuits", "circuit_", "providers",
                  "power_")),
    ("integrations", ("virtual_machines", "vm_interfaces", "virt_clusters", "proxmox_", "esxi_", "hyperv_", "openstack_", "zabbix_", "checkmk_", "wazuh_",
                      "ocs_", "rustdesk_", "scan_agents", "agent_probe_jobs", "cert_", "certificates", "jump_hosts",
                      "ssh_credentials")),
    ("ipam", ("sections", "subnets", "ip_", "vlans", "vlan_domains", "vrfs", "customers", "asns", "tenant",
              "unmanaged_sightings", "custom_field", "contact")),
    ("system", ("users", "user_", "groups", "permissions", "api_tokens", "system_settings", "background_tasks",
                "encrypted_secrets", "event_rules", "webhook_", "alembic_version", "phpipam_migration",
                "maintenance_")),
]


def group_of(table: str) -> str:
    for key, pats in GROUP_RULES:
        if any(table.startswith(p) for p in pats):
            return key
    return "other"


async def database_usage(session: AsyncSession, *, top: int = 12) -> dict[str, Any]:
    total = int(await session.scalar(text("SELECT pg_database_size(current_database())")) or 0)
    rows = (await session.execute(text(
        "SELECT n.nspname, c.relname, pg_total_relation_size(c.oid), GREATEST(c.reltuples, 0)::bigint "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r', 'p', 'm') AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND n.nspname NOT LIKE 'pg_toast%' ORDER BY 3 DESC"))).all()
    groups: dict[str, dict[str, Any]] = {}
    tables = []
    for schema, name, size, est in rows:
        g = group_of(name) if schema == "public" else "other"
        slot = groups.setdefault(g, {"key": g, "bytes": 0, "tables": 0})
        slot["bytes"] += int(size or 0)
        slot["tables"] += 1
        tables.append({"name": name if schema == "public" else f"{schema}.{name}", "group": g,
                       "bytes": int(size or 0), "rows": int(est or 0)})
    return {"bytes": total, "groups": sorted(groups.values(), key=lambda x: -x["bytes"]), "tables": tables[:top]}


# ── 主機上的目錄 ─────────────────────────────────────────────────────────

def _install_root() -> Path:
    return Path(__file__).resolve().parents[3]          # backend/app/services → 安裝目錄


def _walk(path: Path, deadline: float) -> tuple[int, bool, bool]:
    """回傳（佔用的位元組, 是否時間到沒量完, 是否有讀不到的）。用實際佔用的區塊（與 du 相同），不跟符號連結。"""
    total, partial, denied = 0, False, False
    stack = [path]
    while stack:
        if time.monotonic() > deadline:
            return total, True, denied
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        denied = True
                        continue
                    total += getattr(st, "st_blocks", 0) * 512 or st.st_size
                    if e.is_dir(follow_symlinks=False):
                        stack.append(Path(e.path))
        except PermissionError:
            denied = True
        except OSError:
            partial = True
    return total, partial, denied


def measure_dir(key: str, path: Path, *, children: list[tuple[str, list[str]]] | None = None,
                budget: float = 8.0) -> dict[str, Any]:
    out: dict[str, Any] = {"key": key, "path": str(path), "exists": path.is_dir(), "bytes": None,
                           "approx": False, "denied": False, "children": []}
    if not out["exists"]:
        return out
    deadline = time.monotonic() + budget
    size, partial, denied = _walk(path, deadline)
    out.update(bytes=size, approx=partial, denied=denied)
    for ckey, subs in children or []:
        paths = [path / s for s in subs if (path / s).is_dir()]
        if not paths:
            out["children"].append({"key": ckey, "exists": False, "bytes": None, "approx": False})
            continue
        csize, cpartial = 0, False
        for p in paths:
            s, pt, _d = _walk(p, deadline)
            csize += s
            cpartial = cpartial or pt
        out["children"].append({"key": ckey, "exists": True, "bytes": csize, "approx": cpartial or partial})
    return out


def measure_backups(path: Path) -> dict[str, Any]:
    """備份目錄：每日備份的子目錄是 root 建的（0700），後端讀不到（正式站台量到 1.7 MB、實際 40 MB）。
    備份腳本以 root 執行，會把整個目錄的大小寫進 last-run（total_bytes）；讀不到時改用那個數字並標出記錄時間。"""
    out = measure_dir("backups", path)
    if not out["exists"] or not out["denied"]:
        return out
    try:
        status = dict(line.split("=", 1) for line in (path / "last-run").read_text(encoding="utf-8").splitlines()
                      if "=" in line)
        total = int(status.get("total_bytes") or 0)
    except (OSError, ValueError):
        return out
    if total > 0:
        out.update(bytes=total, denied=False, approx=False, from_status=status.get("finished_at") or None)
    return out


def directories_usage() -> list[dict[str, Any]]:
    from app.core.config import get_settings
    root = _install_root()
    upload = Path(get_settings().upload_dir)
    data = upload.parent if upload.name == "uploads" else Path("/var/lib/jt-ipam")
    return [
        measure_backups(Path("/var/backups/jt-ipam")),
        measure_dir("install", root, children=[
            ("python_packages", ["backend/.venv"]), ("frontend_packages", ["frontend/node_modules"]),
            ("frontend_build", ["frontend/dist"]),
        ]),
        measure_dir("data", data, children=[
            ("uploads", ["uploads"]), ("geoip", ["geoip"]), ("package_cache", [".local", ".cache", ".npm"]),
        ]),
        measure_dir("logs", Path("/var/log/jt-ipam")),
    ]


def disk_usage() -> dict[str, Any] | None:
    try:
        du = shutil.disk_usage(_install_root())
    except OSError:
        return None
    return {"path": str(_install_root()), "total": du.total, "used": du.used, "free": du.free}


_CACHE: dict[str, Any] = {}
CACHE_SECONDS = 300


async def collect(session: AsyncSession, *, refresh: bool = False) -> dict[str, Any]:
    from datetime import UTC, datetime
    db = await database_usage(session)            # 資料庫每次都算（便宜，而且是最常看的）
    now = time.monotonic()
    hit = _CACHE.get("fs")
    if refresh or not hit or now - hit[0] > CACHE_SECONDS:
        dirs, disk = await asyncio.to_thread(lambda: (directories_usage(), disk_usage()))
        hit = (now, dirs, disk, datetime.now(UTC).isoformat())
        _CACHE["fs"] = hit
    return {"generated_at": datetime.now(UTC).isoformat(), "database": db,
            "directories": hit[1], "disk": hit[2], "directories_measured_at": hit[3]}
