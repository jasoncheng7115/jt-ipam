#!/usr/bin/env python3
"""定時同步腳本：跑所有 enabled 的 OPNsense / Wazuh / LibreNMS / AdGuard / Proxmox 實例。

由 systemd timer 觸發；每次只跑那些 last_sync_at 距現在已經超過
sync_interval_seconds 的實例（避免短時間內重複跑）。

用法：
    sudo -u jtipam env $(cat /etc/jt-ipam/backend.env | xargs) \\
        /opt/jt-ipam/backend/.venv/bin/python /opt/jt-ipam/scripts/jt-ipam-sync.py

退出碼：
    0 — 全部成功（或沒到時間）
    1 — 至少一個實例 sync 失敗（last_error 已寫回 DB；syslog 也會看到）
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("jt-ipam-sync")


async def _run() -> int:
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models.adguard import AdGuardInstance
    from app.models.dns import DNSServer
    from app.models.firewall import OPNsenseFirewall
    from app.models.librenms import LibreNMSInstance
    from app.models.virt import ProxmoxInstance
    from app.models.wazuh import WazuhInstance
    from app.services import adguard as adguard_svc
    from app.services import librenms as librenms_svc
    from app.services import opnsense_firewall as fw_svc
    from app.services import proxmox as proxmox_svc
    from app.services import wazuh as wazuh_svc
    from app.services.dns.factory import get_adapter as _dns_adapter  # noqa: F401
    from app.services.dns_sync import pull_server

    failed = 0

    async with SessionLocal() as session:
        now = datetime.now(UTC)

        # ── OPNsense ──
        fws = (
            await session.execute(
                select(OPNsenseFirewall).where(OPNsenseFirewall.enabled.is_(True))
            )
        ).scalars().all()
        for fw in fws:
            interval = timedelta(seconds=fw.sync_interval_seconds)
            if fw.last_sync_at and fw.last_sync_at + interval > now:
                continue
            try:
                results = await fw_svc.sync_all_for_firewall(session, fw)
                await session.commit()
                log.info("opnsense %s: %d mappings", fw.name, len(results))
            except Exception as exc:  # noqa: BLE001
                fw.last_error = str(exc)
                await session.commit()
                log.error("opnsense %s sync failed: %s", fw.name, exc)
                failed += 1

        # ── Wazuh ──
        wzs = (
            await session.execute(
                select(WazuhInstance).where(WazuhInstance.enabled.is_(True))
            )
        ).scalars().all()
        for inst in wzs:
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and inst.last_sync_at + interval > now:
                continue
            try:
                summary = await wazuh_svc.sync_agents(session, inst)
                await session.commit()
                log.info("wazuh %s: %s", inst.name, summary)
            except Exception as exc:  # noqa: BLE001
                inst.last_error = str(exc)
                await session.commit()
                log.error("wazuh %s sync failed: %s", inst.name, exc)
                failed += 1

        # ── LibreNMS ──
        lns = (
            await session.execute(
                select(LibreNMSInstance).where(LibreNMSInstance.enabled.is_(True))
            )
        ).scalars().all()
        for inst in lns:
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and inst.last_sync_at + interval > now:
                continue
            try:
                summary = await librenms_svc.sync_instance(session, inst)
                await session.commit()
                log.info("librenms %s: %s", inst.name, summary)
            except Exception as exc:  # noqa: BLE001
                inst.last_error = str(exc)
                await session.commit()
                log.error("librenms %s sync failed: %s", inst.name, exc)
                failed += 1

        # ── AdGuard ──
        ags = (
            await session.execute(
                select(AdGuardInstance).where(AdGuardInstance.enabled.is_(True))
            )
        ).scalars().all()
        for inst in ags:
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and inst.last_sync_at + interval > now:
                continue
            try:
                summary = await adguard_svc.sync_instance(session, inst)
                await session.commit()
                log.info("adguard %s: %s", inst.name, summary)
            except Exception as exc:  # noqa: BLE001
                inst.last_error = str(exc)
                await session.commit()
                log.error("adguard %s sync failed: %s", inst.name, exc)
                failed += 1

        # ── Proxmox（同一 cluster 多節點 → 自動挑健康節點同步，故障換手）──
        pvs = (
            await session.execute(
                select(ProxmoxInstance)
                .where(ProxmoxInstance.enabled.is_(True))
                .order_by(ProxmoxInstance.cluster_id, ProxmoxInstance.created_at)
            )
        ).scalars().all()
        pv_groups: dict[object, list] = {}
        for inst in pvs:
            pv_groups.setdefault(inst.cluster_id, []).append(inst)
        for cluster_id, insts in pv_groups.items():
            interval = timedelta(seconds=min(i.sync_interval_seconds for i in insts))
            lasts = [i.last_sync_at for i in insts if i.last_sync_at]
            if lasts and max(lasts) + interval > now:
                continue
            try:
                summary = await proxmox_svc.sync_cluster(session, insts)
                await session.commit()
                log.info("proxmox cluster %s: %s", cluster_id, summary.to_dict())
            except Exception as exc:  # noqa: BLE001
                await session.commit()  # 寫回各節點 healthcheck 失敗的 last_error
                log.error("proxmox cluster %s sync failed: %s", cluster_id, exc)
                failed += 1

        # ── DNS servers ──（沒有 sync_interval_seconds 欄；用固定 10 分鐘 throttle）
        dns_interval = timedelta(seconds=600)
        dnss = (
            await session.execute(
                select(DNSServer).where(DNSServer.enabled.is_(True))
            )
        ).scalars().all()
        for srv in dnss:
            last = getattr(srv, "last_pull_at", None) or getattr(srv, "last_sync_at", None)
            if last and last + dns_interval > now:
                continue
            try:
                summary = await pull_server(session, srv)
                await session.commit()
                log.info("dns %s: %s", srv.name, summary)
            except Exception as exc:  # noqa: BLE001
                log.error("dns %s sync failed: %s", srv.name, exc)
                failed += 1

    return 1 if failed else 0


def main() -> None:
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
