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
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("jt-ipam-sync")

#: 到期判斷容許的誤差：排程（jt-ipam-sync.timer）每 5 分鐘跑一輪，上一次的完成時間是上一輪跑到一半才寫入的。
#: 不容許誤差的話，設 300 秒的整合常常差幾十秒沒到期，被延到下一輪，實際變成 10 分鐘（10-23 DNS 實際 16 分鐘一次）。
DUE_SLACK = timedelta(seconds=150)


def _sync_due(last: datetime, interval: timedelta) -> bool:
    """距離上次同步已經超過「間隔減去誤差」：這一輪該跑了。用當下時間，不用這一輪開始的時間。"""
    return datetime.now(UTC) >= last + interval - DUE_SLACK


def _is_deadlock(exc: BaseException) -> bool:
    """例外鏈上有 PostgreSQL 的死結（asyncpg DeadlockDetectedError，外面可能包了 SQLAlchemy 的例外）。"""
    e: BaseException | None = exc
    seen = 0
    while e is not None and seen < 10:
        if type(e).__name__ == "DeadlockDetectedError" or "deadlock detected" in str(e):
            return True
        e = e.__cause__ or e.__context__
        seen += 1
    return False


async def _commit_with_retry(session, obj, label: str, fn, *args):  # type: ignore[no-untyped-def]
    """跑一個整合的同步並 commit；遇到資料庫死結就 rollback、整筆重做一次。

    會寫 IP 上線證據的同步（防火牆 ARP、DHCP 租約）跟同時在寫同一批 IP 的掃描代理回報會互鎖：
    兩邊都分好幾個階段鎖列，排序救不了（prod 兩週 53 次，全在 commit 時 flush 的
    `UPDATE ip_addresses SET arp_seen`）。PostgreSQL 的標準做法是重做整筆交易。
    rollback 會讓物件過期，重做前要 refresh；第二次還死結就照常往外丟、記成失敗。
    """
    for attempt in (1, 2):
        try:
            result = await fn(*args)
            await session.commit()
            return result
        except Exception as exc:
            if attempt == 2 or not _is_deadlock(exc):
                raise
            await session.rollback()
            await session.refresh(obj)
            log.warning("%s: deadlock with a concurrent writer, retrying once", label)
            await asyncio.sleep(1)
    return None


async def _run() -> int:
    from app.core.db import SessionLocal
    from app.models.adguard import AdGuardInstance
    from app.models.dns import DNSServer
    from app.models.esxi import ESXiInstance
    from app.models.hyperv import HyperVInstance
    from app.models.openstack import OpenStackInstance
    from app.models.firewall import OPNsenseFirewall
    from app.models.fortigate import FortiGateFirewall
    from app.models.mikrotik import MikroTikRouter
    from app.models.ocs import OcsServer
    from app.models.paloalto import PaloAltoFirewall
    from app.models.checkpoint import CheckPointServer
    from app.models.checkpoint_gaia import CheckPointGaiaTarget
    from app.models.librenms import LibreNMSInstance
    from app.models.pfsense import PfSenseFirewall
    from app.models.virt import ProxmoxInstance, VirtCluster
    from app.models.wazuh import WazuhInstance
    from app.models.zabbix import ZabbixInstance
    from app.models.checkmk import CheckmkInstance
    from app.models.windows_dhcp import WindowsDhcpServer
    from app.models.dhcp_standalone import IscDhcpServer, KeaDhcpServer
    from app.models.isoinsight import IsoInsightSource
    from app.models.technitium import TechnitiumDhcpServer
    from app.models.rustdesk import RustDeskServer
    from app.services import adguard as adguard_svc
    from app.services import fortigate as fortigate_svc
    from app.services import mikrotik as mikrotik_svc
    from app.services import ocs as ocs_svc
    from app.services import paloalto as paloalto_svc
    from app.services import checkpoint as checkpoint_svc
    from app.services import checkpoint_gaia as checkpoint_gaia_svc
    from app.services import librenms as librenms_svc
    from app.services import opnsense_firewall as fw_svc
    from app.services import pfsense as pfsense_svc
    from app.services import proxmox as proxmox_svc
    from app.services import wazuh as wazuh_svc
    from app.services import zabbix as zabbix_svc
    from app.services import checkmk as checkmk_svc
    from app.services import windows_dhcp as windows_dhcp_svc
    from app.services import kea_dhcp as kea_dhcp_svc
    from app.services import technitium_dhcp as technitium_dhcp_svc
    from app.services import dhcp_standalone as dhcp_standalone_svc
    from app.services import rustdesk as rustdesk_svc
    from app.services.background_tasks import upsert_scheduled_task as _hb
    from app.services.dns.factory import get_adapter as _dns_adapter  # noqa: F401
    from app.services.dns_sync import pull_server
    from sqlalchemy import select

    failed = 0

    async with SessionLocal() as session:
        now = datetime.now(UTC)

        # ── 維護排程：每輪最先判斷開始與結束（發通知、結束時補發），後面的告警才看得到最新的維護狀態 ──
        try:
            from app.services import maintenance as _maint_svc
            mstat = await _maint_svc.tick(session)
            await session.commit()
            if mstat["started"] or mstat["ended"]:
                log.info("maintenance: %s", mstat)
        except Exception as exc:
            await session.rollback()
            log.error("maintenance tick failed: %s", exc)

        # ── OPNsense ──
        fws = (
            await session.execute(
                select(OPNsenseFirewall).where(OPNsenseFirewall.enabled.is_(True))
            )
        ).scalars().all()
        for fw in fws:
            interval = timedelta(seconds=fw.sync_interval_seconds)
            if fw.last_sync_at and not _sync_due(fw.last_sync_at, interval):
                continue
            name = fw.name
            try:
                results = await _commit_with_retry(session, fw, f"opnsense {name}", fw_svc.sync_all_for_firewall, session, fw)
                log.info("opnsense %s: %d mappings", name, len(results))
                await _hb(session, kind="opnsense.sync", target_type="opnsense_firewall",
                          target_id=fw.id, target_label=name, ok=True,
                          summary={"mappings": len(results)})
            except Exception as exc:
                # 失敗的 transaction 先 rollback，否則接著的 commit 會二次爆 → 中斷整輪 sync
                await session.rollback()
                fw.last_error = str(exc)
                await session.commit()
                log.error("opnsense %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="opnsense.sync", target_type="opnsense_firewall",
                          target_id=fw.id, target_label=name, ok=False, error=str(exc))

        # ── pfSense ──
        pfws = (
            await session.execute(
                select(PfSenseFirewall).where(PfSenseFirewall.enabled.is_(True))
            )
        ).scalars().all()
        for fw in pfws:
            interval = timedelta(seconds=fw.sync_interval_seconds)
            if fw.last_sync_at and not _sync_due(fw.last_sync_at, interval):
                continue
            name = fw.name
            try:
                counts = await _commit_with_retry(session, fw, f"pfsense {name}", pfsense_svc.sync_instance, session, fw)
                log.info("pfsense %s: %s", name, counts)
                await _hb(session, kind="pfsense.sync", target_type="pfsense_firewall",
                          target_id=fw.id, target_label=name, ok=True,
                          summary=counts if isinstance(counts, dict) else {"result": str(counts)})
            except Exception as exc:
                await session.rollback()
                fw.last_error = str(exc)
                await session.commit()
                log.error("pfsense %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="pfsense.sync", target_type="pfsense_firewall",
                          target_id=fw.id, target_label=name, ok=False, error=str(exc))


        # ── ESXi / vCenter ──
        for inst in (await session.execute(
            select(ESXiInstance).where(ESXiInstance.enabled.is_(True))
        )).scalars().all():
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and not _sync_due(inst.last_sync_at, interval):
                continue
            name = inst.name
            try:
                from app.services import esxi as esxi_svc
                summary = await esxi_svc.sync_instance(session, inst)
                await session.commit()
                log.info("esxi %s: %s", name, summary)
                await _hb(session, kind="esxi.sync", target_type="esxi_instance",
                          target_id=inst.id, target_label=name, ok=True, summary=summary)
            except Exception as exc:
                # 先 rollback 再寫 last_error：不 rollback 會二次爆、連鎖中斷整輪
                await session.rollback()
                inst.last_error = str(exc)[:2000]
                await session.commit()
                log.error("esxi %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="esxi.sync", target_type="esxi_instance",
                          target_id=inst.id, target_label=name, ok=False, error=str(exc))

        # ── Hyper-V（直連主機或 SCVMM）──
        for inst in (await session.execute(
            select(HyperVInstance).where(HyperVInstance.enabled.is_(True))
        )).scalars().all():
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and not _sync_due(inst.last_sync_at, interval):
                continue
            name = inst.name
            try:
                from app.services import hyperv as hyperv_svc
                summary = await hyperv_svc.sync_instance(session, inst)
                await session.commit()
                log.info("hyperv %s: %s", name, summary)
                await _hb(session, kind="hyperv.sync", target_type="hyperv_instance",
                          target_id=inst.id, target_label=name, ok=True, summary=summary)
            except Exception as exc:
                # 先 rollback 再寫 last_error：不 rollback 會二次爆、連鎖中斷整輪
                await session.rollback()
                inst.last_error = str(exc)[:2000]
                await session.commit()
                log.error("hyperv %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="hyperv.sync", target_type="hyperv_instance",
                          target_id=inst.id, target_label=name, ok=False, error=str(exc))

        # ── Bigstack CubeCOS／OpenStack（Keystone＋Nova＋Neutron，選用 CubeCOS 節點）──
        for inst in (await session.execute(
            select(OpenStackInstance).where(OpenStackInstance.enabled.is_(True))
        )).scalars().all():
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and not _sync_due(inst.last_sync_at, interval):
                continue
            name = inst.name
            try:
                from app.services import openstack as openstack_svc
                summary = await openstack_svc.sync_instance(session, inst)
                await session.commit()
                log.info("openstack %s: %s", name, summary)
                await _hb(session, kind="openstack.sync", target_type="openstack_instance",
                          target_id=inst.id, target_label=name, ok=True, summary=summary)
            except Exception as exc:
                # 先 rollback 再寫 last_error：不 rollback 會二次爆、連鎖中斷整輪
                await session.rollback()
                inst.last_error = str(exc)[:2000]
                await session.commit()
                log.error("openstack %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="openstack.sync", target_type="openstack_instance",
                          target_id=inst.id, target_label=name, ok=False, error=str(exc))
        # ── Wazuh ──
        wzs = (
            await session.execute(
                select(WazuhInstance).where(WazuhInstance.enabled.is_(True))
            )
        ).scalars().all()
        for inst in wzs:
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and not _sync_due(inst.last_sync_at, interval):
                continue
            name = inst.name
            try:
                summary = await wazuh_svc.sync_agents(session, inst)
                # SCA（資安組態評估）：用同一組 API 憑證，失敗不影響 agent 同步本身
                try:
                    n = await wazuh_svc.sync_sca(session, inst)
                    if n and isinstance(summary, dict):
                        summary["sca"] = n
                except Exception as exc:
                    log.warning("wazuh %s sca: %s", inst.name, exc)
                await session.commit()
                log.info("wazuh %s: %s", name, summary)
                await _hb(session, kind="wazuh.sync", target_type="wazuh_instance",
                          target_id=inst.id, target_label=name, ok=True,
                          summary=summary if isinstance(summary, dict) else None)
            except Exception as exc:
                await session.rollback()
                inst.last_error = str(exc)
                await session.commit()
                log.error("wazuh %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="wazuh.sync", target_type="wazuh_instance",
                          target_id=inst.id, target_label=name, ok=False, error=str(exc))

        # ── Zabbix ──
        zbxs = (
            await session.execute(
                select(ZabbixInstance).where(ZabbixInstance.enabled.is_(True))
            )
        ).scalars().all()
        for inst in zbxs:
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and not _sync_due(inst.last_sync_at, interval):
                continue
            name = inst.name
            try:
                summary = await zabbix_svc.sync_instance(session, inst)
                await session.commit()
                log.info("zabbix %s: %s", name, summary)
                await _hb(session, kind="zabbix.sync", target_type="zabbix_instance",
                          target_id=inst.id, target_label=name, ok=True,
                          summary=summary if isinstance(summary, dict) else None)
            except Exception as exc:
                await session.rollback()
                inst.last_error = str(exc)[:2000]
                await session.commit()
                log.error("zabbix %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="zabbix.sync", target_type="zabbix_instance",
                          target_id=inst.id, target_label=name, ok=False, error=str(exc))

        # ── Checkmk ──
        cmks = (
            await session.execute(
                select(CheckmkInstance).where(CheckmkInstance.enabled.is_(True))
            )
        ).scalars().all()
        for inst in cmks:
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and not _sync_due(inst.last_sync_at, interval):
                continue
            name = inst.name
            try:
                summary = await checkmk_svc.sync_instance(session, inst)
                await session.commit()
                log.info("checkmk %s: %s", name, summary)
                await _hb(session, kind="checkmk.sync", target_type="checkmk_instance",
                          target_id=inst.id, target_label=name, ok=True,
                          summary=summary if isinstance(summary, dict) else None)
            except Exception as exc:
                await session.rollback()
                inst.last_error = str(exc)[:2000]
                await session.commit()
                log.error("checkmk %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="checkmk.sync", target_type="checkmk_instance",
                          target_id=inst.id, target_label=name, ok=False, error=str(exc))

        # ── LibreNMS ──
        lns = (
            await session.execute(
                select(LibreNMSInstance).where(LibreNMSInstance.enabled.is_(True))
            )
        ).scalars().all()
        for inst in lns:
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and not _sync_due(inst.last_sync_at, interval):
                continue
            name = inst.name
            try:
                summary = await librenms_svc.sync_instance(session, inst)
                await session.commit()
                log.info("librenms %s: %s", name, summary)
                # sync_instance 回的是 SyncSummary 物件：以前這裡只收 dict，排程同步在背景作業頁的摘要一直是空的
                # （手動同步才有）；依 ARP 建立了幾筆、沒建的原因也因此看不到
                await _hb(session, kind="librenms.sync", target_type="librenms_instance",
                          target_id=inst.id, target_label=name, ok=True,
                          summary=summary.to_dict() if hasattr(summary, "to_dict")
                          else (summary if isinstance(summary, dict) else None))
            except Exception as exc:
                await session.rollback()
                inst.last_error = str(exc)
                await session.commit()
                log.error("librenms %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="librenms.sync", target_type="librenms_instance",
                          target_id=inst.id, target_label=name, ok=False, error=str(exc))

        # ── ARP 過期清除（每輪一次，與 instance 是否到期無關）──
        # arp_entries 只新增不回收，靠這裡刪掉超過保留天數的舊紀錄（含孤兒 row）。
        try:
            from app.core.config import get_settings
            pruned = await librenms_svc.prune_stale_arp(
                session, max_age_days=get_settings().arp_retention_days,
            )
            await session.commit()
            if pruned:
                log.info("arp prune: removed %d stale entries", pruned)
        except Exception as exc:
            await session.rollback()
            log.error("arp prune failed: %s", exc)

        # ── 健康告警：整合同步失敗／代理失聯／系統檢查（每輪一次）──
        # 這三類資料早就在資料庫裡（last_error / last_seen_at / 系統診斷），只是要有人
        # 主動去點才看得到 —— 實務上沒有人每天點。只在「開始」與「恢復」時發，
        # 中間持續壞著不再吵（見 services/state_alert）。
        try:
            from app.models.certificate import CertAgent
            from app.models.scan_agent import ScanAgent
            from app.services.health_alert import (
                check_agent_health,
                check_integration_health,
                check_system_health,
            )
            from app.services.self_check import run_checks

            insts: list = []
            # 這份清單要**與上面實際會同步的整合一致** —— 少列一個，那個整合壞掉時
            # 就永遠不會有人知道，而且畫面上完全看不出少了誰。
            for kind, model in (
                ("librenms", LibreNMSInstance), ("wazuh", WazuhInstance),
                ("zabbix", ZabbixInstance), ("checkmk", CheckmkInstance), ("adguard", AdGuardInstance),
                ("proxmox", ProxmoxInstance), ("esxi", ESXiInstance), ("hyperv", HyperVInstance),
                ("openstack", OpenStackInstance),
                ("opnsense", OPNsenseFirewall), ("pfsense", PfSenseFirewall),
                ("fortigate", FortiGateFirewall), ("paloalto", PaloAltoFirewall),
                ("checkpoint", CheckPointServer), ("checkpoint_gaia", CheckPointGaiaTarget),
                ("mikrotik", MikroTikRouter), ("windows_dhcp", WindowsDhcpServer),
                ("kea_dhcp", KeaDhcpServer), ("isc_dhcp", IscDhcpServer),
                ("isoinsight", IsoInsightSource), ("technitium", TechnitiumDhcpServer),
                ("rustdesk", RustDeskServer),
                ("ocs", OcsServer),
                ("dns", DNSServer),
            ):
                rows = (await session.execute(
                    select(model).where(model.enabled.is_(True)))).scalars().all()
                insts.extend((kind, r) for r in rows)
            await check_integration_health(session, insts)

            agents: list = []
            for kind, model in (("scan", ScanAgent), ("cert", CertAgent)):
                rows = (await session.execute(select(model))).scalars().all()
                agents.extend((kind, r) for r in rows)
            await check_agent_health(session, agents)

            await check_system_health(session, (await run_checks(session)).checks)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            log.error("health alerts failed: %s", exc)

        # ── 容量與資安告警：DHCP 集區／跳板金鑰／憑證來源（每輪一次）──
        # 跳板金鑰是這三個裡面最重要的：指紋改變是中間人攻擊的訊號，而原本只有
        # 「有人開主控台」時才會被擋下來 —— 沒人連的時候完全沒人知道。
        try:
            from app.models.certificate import Certificate
            from app.models.dhcp import DHCPPoolRange
            from app.models.jump_host import JumpHost
            from app.services.capacity_alert import (
                check_cert_sources,
                check_dhcp_pools,
                check_jump_host_keys,
            )
            from app.services.dhcp_usage import pool_usage

            pools = await pool_usage(session)
            await check_dhcp_pools(session, pools)

            jhs = (await session.execute(
                select(JumpHost).where(JumpHost.enabled.is_(True)))).scalars().all()
            await check_jump_host_keys(session, list(jhs))

            # `source_type` 是 NOT NULL、沒設來源時是字串 'none' —— 用 is_not(None)
            # 會把每一張憑證都撈進來，包含根本沒有自動抓取的那些。
            certs = (await session.execute(
                select(Certificate).where(Certificate.source_type != "none")
            )).scalars().all()
            await check_cert_sources(session, list(certs))
            await session.commit()
        except Exception as exc:
            await session.rollback()
            log.error("capacity/security alerts failed: %s", exc)

        # ── 疑似暴力破解（每輪一次）──
        # 單一使用者忘記密碼被鎖不是資安事件；**多個帳號同時處於鎖定中**才是形狀。
        try:
            from app.models.user import User
            from app.services.security_alert import check_lockouts

            locked = (await session.execute(
                select(User).where(User.locked_until.is_not(None)))).scalars().all()
            await check_lockouts(session, list(locked))
            await session.commit()
        except Exception as exc:
            await session.rollback()
            log.error("brute force check failed: %s", exc)

        # ── FDB 過期清除（每輪一次）──
        # 與 ARP 同樣是只新增不回收的表，但保留期限長得多（預設一年）：
        # 這張表的價值就是「那台機器以前接在哪個埠」，砍太快等於把查案依據丟掉。
        # 注意：**保留 ≠ 採信** —— 推導「目前接在哪個埠」時只會用近期仍在的條目
        # （見 librenms.current_fdb_cutoff），舊的只留著給人查。
        try:
            from app.core.config import get_settings as _gs
            pruned_fdb = await librenms_svc.prune_stale_fdb(
                session, max_age_days=_gs().fdb_retention_days,
            )
            await session.commit()
            if pruned_fdb:
                log.info("fdb prune: removed %d stale entries", pruned_fdb)
        except Exception as exc:
            await session.rollback()
            log.error("fdb prune failed: %s", exc)

        # ── 冷卻紀錄回收 ──
        # 只增不刪會無限累積；到期後仍多留一段時間，因為「這位址上一手是誰」
        # 最常在冷卻剛結束那幾天被問到。
        try:
            from app.services.ip_lifecycle import purge_expired
            removed = await purge_expired(session)
            await session.commit()
            if removed:
                log.info("cooldown purge: removed %d expired rows", removed)
        except Exception as exc:
            await session.rollback()
            log.error("cooldown purge failed: %s", exc)

        # ── AdGuard ──
        ags = (
            await session.execute(
                select(AdGuardInstance).where(AdGuardInstance.enabled.is_(True))
            )
        ).scalars().all()
        for inst in ags:
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and not _sync_due(inst.last_sync_at, interval):
                continue
            name = inst.name
            try:
                summary = await adguard_svc.sync_instance(session, inst)
                await session.commit()
                log.info("adguard %s: %s", name, summary)
                await _hb(session, kind="adguard.sync", target_type="adguard_instance",
                          target_id=inst.id, target_label=name, ok=True,
                          summary=summary if isinstance(summary, dict) else None)
            except Exception as exc:
                await session.rollback()
                inst.last_error = str(exc)
                await session.commit()
                log.error("adguard %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="adguard.sync", target_type="adguard_instance",
                          target_id=inst.id, target_label=name, ok=False, error=str(exc))

        # ── FortiGate（Beta；FortiOS REST API 唯讀）──
        fgs = (
            await session.execute(
                select(FortiGateFirewall).where(FortiGateFirewall.enabled.is_(True))
            )
        ).scalars().all()
        for inst in fgs:
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and not _sync_due(inst.last_sync_at, interval):
                continue
            name = inst.name
            try:
                summary = await _commit_with_retry(session, inst, f"fortigate {name}", fortigate_svc.sync_instance, session, inst)
                log.info("fortigate %s: %s", name, summary)
                await _hb(session, kind="fortigate.sync", target_type="fortigate_firewall",
                          target_id=inst.id, target_label=name, ok=True,
                          summary=summary if isinstance(summary, dict) else None)
            except Exception as exc:
                await session.rollback()
                inst.last_error = str(exc)
                await session.commit()
                log.error("fortigate %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="fortigate.sync", target_type="fortigate_firewall",
                          target_id=inst.id, target_label=name, ok=False, error=str(exc))

        # ── Palo Alto（Beta；PAN-OS REST + XML 唯讀）──
        pas = (
            await session.execute(
                select(PaloAltoFirewall).where(PaloAltoFirewall.enabled.is_(True))
            )
        ).scalars().all()
        for inst in pas:
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and not _sync_due(inst.last_sync_at, interval):
                continue
            name = inst.name
            try:
                summary = await _commit_with_retry(session, inst, f"paloalto {name}", paloalto_svc.sync_instance, session, inst)
                log.info("paloalto %s: %s", name, summary)
                await _hb(session, kind="paloalto.sync", target_type="paloalto_firewall",
                          target_id=inst.id, target_label=name, ok=True,
                          summary=summary if isinstance(summary, dict) else None)
            except Exception as exc:
                # 先 rollback 再寫 last_error —— 不 rollback 會二次爆並中斷整輪
                await session.rollback()
                inst.last_error = str(exc)
                await session.commit()
                log.error("paloalto %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="paloalto.sync", target_type="paloalto_firewall",
                          target_id=inst.id, target_label=name, ok=False, error=str(exc))

        # ── Check Point（Beta；管理伺服器的 Management API 唯讀，一台管多個閘道）──
        cps = (
            await session.execute(
                select(CheckPointServer).where(CheckPointServer.enabled.is_(True))
            )
        ).scalars().all()
        for inst in cps:
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and not _sync_due(inst.last_sync_at, interval):
                continue
            name = inst.name
            try:
                summary = await _commit_with_retry(session, inst, f"checkpoint {name}", checkpoint_svc.sync_instance, session, inst)
                log.info("checkpoint %s: %s", name, summary)
                await _hb(session, kind="checkpoint.sync", target_type="checkpoint_server",
                          target_id=inst.id, target_label=name, ok=True,
                          summary=summary if isinstance(summary, dict) else None)
            except Exception as exc:
                # 先 rollback 再寫 last_error —— 不 rollback 會二次爆並中斷整輪
                await session.rollback()
                inst.last_error = str(exc)
                await session.commit()
                log.error("checkpoint %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="checkpoint.sync", target_type="checkpoint_server",
                          target_id=inst.id, target_label=name, ok=False, error=str(exc))

        # ── Check Point 閘道的 Gaia API（第二階段：DHCP 設定；選用的寫死指令讀 ARP／租約）──
        cpgs = (
            await session.execute(
                select(CheckPointGaiaTarget).where(CheckPointGaiaTarget.enabled.is_(True))
            )
        ).scalars().all()
        for inst in cpgs:
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and not _sync_due(inst.last_sync_at, interval):
                continue
            name = inst.name
            try:
                summary = await _commit_with_retry(session, inst, f"checkpoint_gaia {name}", checkpoint_gaia_svc.sync_target, session, inst)
                log.info("checkpoint_gaia %s: %s", name, summary)
                await _hb(session, kind="checkpoint_gaia.sync", target_type="checkpoint_gaia_target",
                          target_id=inst.id, target_label=name, ok=True,
                          summary=summary if isinstance(summary, dict) else None)
            except Exception as exc:
                # 先 rollback 再寫 last_error —— 不 rollback 會二次爆並中斷整輪
                await session.rollback()
                inst.last_error = str(exc)
                await session.commit()
                log.error("checkpoint_gaia %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="checkpoint_gaia.sync", target_type="checkpoint_gaia_target",
                          target_id=inst.id, target_label=name, ok=False, error=str(exc))

        # ── MikroTik RouterOS（v7 REST 唯讀）──
        # ⚠️ 這一家的同步間隔預設是 900 秒（其他整合 300）：客戶的 MikroTik 是主力
        # 路由器，服務本身也會在 CPU 超標時停掉本輪剩下的區段（見 services/mikrotik.py）。
        mts = (
            await session.execute(
                select(MikroTikRouter).where(MikroTikRouter.enabled.is_(True))
            )
        ).scalars().all()
        for inst in mts:
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and not _sync_due(inst.last_sync_at, interval):
                continue
            name = inst.name
            try:
                summary = await _commit_with_retry(session, inst, f"mikrotik {name}", mikrotik_svc.sync_instance, session, inst)
                log.info("mikrotik %s: %s", name, summary)
                await _hb(session, kind="mikrotik.sync", target_type="mikrotik_router",
                          target_id=inst.id, target_label=name, ok=True,
                          summary=summary if isinstance(summary, dict) else None)
            except Exception as exc:
                # 先 rollback 再寫 last_error —— 不 rollback 會二次爆並中斷整輪
                await session.rollback()
                inst.last_error = str(exc)
                await session.commit()
                log.error("mikrotik %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="mikrotik.sync", target_type="mikrotik_router",
                          target_id=inst.id, target_label=name, ok=False, error=str(exc))

        # ── OCS Inventory NG（端點資產盤點；唯讀 REST，只比不建）──
        ocs_servers = (
            await session.execute(
                select(OcsServer).where(OcsServer.enabled.is_(True))
            )
        ).scalars().all()
        for inst in ocs_servers:
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and not _sync_due(inst.last_sync_at, interval):
                continue
            name = inst.name
            try:
                summary = await ocs_svc.sync_instance(session, inst)
                await session.commit()
                log.info("ocs %s: %s", name, summary)
                await _hb(session, kind="ocs.sync", target_type="ocs_server",
                          target_id=inst.id, target_label=name, ok=True,
                          summary=summary if isinstance(summary, dict) else None)
            except Exception as exc:
                await session.rollback()
                inst.last_error = str(exc)
                await session.commit()
                log.error("ocs %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="ocs.sync", target_type="ocs_server",
                          target_id=inst.id, target_label=name, ok=False, error=str(exc))

        # ── Windows DHCP Server（Beta；WinRM 唯讀拉 scope/租約）──
        wdhcps = (
            await session.execute(
                select(WindowsDhcpServer).where(WindowsDhcpServer.enabled.is_(True))
            )
        ).scalars().all()
        for inst in wdhcps:
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and not _sync_due(inst.last_sync_at, interval):
                continue
            name = inst.name
            try:
                summary = await _commit_with_retry(session, inst, f"windows_dhcp {name}", windows_dhcp_svc.sync_instance, session, inst)
                log.info("windows_dhcp %s: %s", name, summary)
                await _hb(session, kind="windows_dhcp.sync", target_type="windows_dhcp_server",
                          target_id=inst.id, target_label=name, ok=True,
                          summary=summary if isinstance(summary, dict) else None)
            except Exception as exc:
                await session.rollback()
                inst.last_error = str(exc)
                await session.commit()
                log.error("windows_dhcp %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="windows_dhcp.sync", target_type="windows_dhcp_server",
                          target_id=inst.id, target_label=name, ok=False, error=str(exc))

        # ── 獨立 Kea DHCP Server（issue #45；JSON 控制 API 唯讀拉範圍／保留／租約）──
        keas = (
            await session.execute(
                select(KeaDhcpServer).where(KeaDhcpServer.enabled.is_(True))
            )
        ).scalars().all()
        for inst in keas:
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and not _sync_due(inst.last_sync_at, interval):
                continue
            name = inst.name
            try:
                summary = await _commit_with_retry(session, inst, f"kea_dhcp {name}", kea_dhcp_svc.sync_instance, session, inst)
                log.info("kea_dhcp %s: %s", name, summary)
                await _hb(session, kind="kea_dhcp.sync", target_type="kea_dhcp_server",
                          target_id=inst.id, target_label=name, ok=True, summary=summary)
            except Exception as exc:
                await session.rollback()
                inst.last_error = str(exc)
                await session.commit()
                log.error("kea_dhcp %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="kea_dhcp.sync", target_type="kea_dhcp_server",
                          target_id=inst.id, target_label=name, ok=False, error=str(exc))

        # ── Technitium DHCP（HTTP API 唯讀拉範圍／保留／選項／租約；DNS 那一半在下面的 DNS 區塊）──
        tdns = (
            await session.execute(
                select(TechnitiumDhcpServer).where(TechnitiumDhcpServer.enabled.is_(True))
            )
        ).scalars().all()
        for inst in tdns:
            interval = timedelta(seconds=inst.sync_interval_seconds)
            if inst.last_sync_at and not _sync_due(inst.last_sync_at, interval):
                continue
            name = inst.name
            try:
                summary = await _commit_with_retry(session, inst, f"technitium {name}",
                                                   technitium_dhcp_svc.sync_instance, session, inst)
                log.info("technitium %s: %s", name, summary)
                await _hb(session, kind="technitium_dhcp.sync", target_type="technitium_dhcp_server",
                          target_id=inst.id, target_label=name, ok=True, summary=summary)
            except Exception as exc:
                await session.rollback()
                inst.last_error = str(exc)
                await session.commit()
                log.error("technitium %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="technitium_dhcp.sync", target_type="technitium_dhcp_server",
                          target_id=inst.id, target_label=name, ok=False, error=str(exc))

        # ── ISOinsight 整合（登入 HTTP(S) 介面讀 DHCP 租約）──
        # 排程條件（開了排程、沒有因登入失敗暫停、不在 429 延後期間、到期；沒成功預覽過的只記「要先預覽」）、跨行程的鎖
        # （與網頁上的「立即同步」共用）、交易與同步記錄都在 services/isoinsight/job.py；每個來源用自己的 session。
        try:
            from app.services.isoinsight import job as isoinsight_job
            for res in await isoinsight_job.run_due(SessionLocal, now):
                ok = res.get("result") in ("success", "partial", "skipped")
                info = {k: v for k, v in res.items() if k not in ("id", "name", "error")}
                log.info("isoinsight %s: %s", res["name"], info)
                if not ok:
                    failed += 1
                await _hb(session, kind="isoinsight.sync", target_type="isoinsight_source",
                          target_id=res["id"], target_label=res["name"], ok=ok, summary=info,
                          error=None if ok else f"{res.get('error_code')}: {res.get('error') or ''}".strip())
        except Exception as exc:
            await session.rollback()
            log.error("isoinsight scheduled sync failed: %s", exc)
            failed += 1

        # ── 獨立 ISC DHCP Server：資料由掃描代理回報，這裡只抓「代理多久沒回報」──
        try:
            if await dhcp_standalone_svc.mark_stale_isc(session, now):
                await session.commit()
        except Exception as exc:
            await session.rollback()
            log.error("isc_dhcp stale check failed: %s", exc)

        # ── RustDesk Server（開源版）：資料由專用 RustDesk 代理回報，這裡只抓「代理多久沒回報」──
        try:
            if await rustdesk_svc.mark_stale_rustdesk(session, now):
                await session.commit()
        except Exception as exc:
            await session.rollback()
            log.error("rustdesk stale check failed: %s", exc)

        # ── RustDesk 客戶端稽核：保留 400 天 ──
        try:
            if await rustdesk_svc.prune_audit(session, now):
                await session.commit()
        except Exception as exc:
            await session.rollback()
            log.error("rustdesk audit prune failed: %s", exc)

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
            if lasts and not _sync_due(max(lasts), interval):
                continue
            # 作業表格顯示用的標籤：優先用叢集名稱（ProxmoxInstance 無 name/host 欄，
            # 別直接印 cluster_id UUID），退而用實例 api_url，再退 "proxmox"。
            cname = None
            if cluster_id:
                cname = await session.scalar(
                    select(VirtCluster.name).where(VirtCluster.id == cluster_id)
                )
            pv_label = cname or getattr(insts[0], "api_url", None) or "proxmox"
            try:
                summary = await proxmox_svc.sync_cluster(session, insts)
                await session.commit()
                log.info("proxmox cluster %s: %s", cluster_id, summary.to_dict())
                await _hb(session, kind="proxmox.sync", target_type="proxmox_cluster",
                          target_id=cluster_id, target_label=pv_label, ok=True,
                          summary=summary.to_dict())
            except Exception as exc:
                # 失敗的 transaction 無法 commit，先 rollback 讓 session 恢復可用，
                # 否則下一個 cluster 的查詢會 PendingRollbackError 連鎖中斷整輪
                await session.rollback()
                log.error("proxmox cluster %s sync failed: %s", cluster_id, exc)
                failed += 1
                await _hb(session, kind="proxmox.sync", target_type="proxmox_cluster",
                          target_id=cluster_id, target_label=pv_label, ok=False, error=str(exc))

        # ── DNS servers ──（用各自的 sync_interval_seconds；以前寫死 600 秒，加上排程誤差實際 16 分鐘才一次）
        dnss = (
            await session.execute(
                select(DNSServer).where(DNSServer.enabled.is_(True))
            )
        ).scalars().all()
        for srv in dnss:
            last = getattr(srv, "last_pull_at", None) or getattr(srv, "last_sync_at", None)
            if last and not _sync_due(last, timedelta(seconds=srv.sync_interval_seconds or 600)):
                continue
            name = srv.name
            try:
                summary = await pull_server(session, srv)
                await session.commit()
                log.info("dns %s: %s", name, summary)
                await _hb(session, kind="dns.sync", target_type="dns_server",
                          target_id=srv.id, target_label=name, ok=True,
                          summary=summary if isinstance(summary, dict) else None)
            except Exception as exc:
                # rollback 讓 session 恢復可用，否則下一個 DNS server 的查詢會連鎖失敗
                await session.rollback()
                log.error("dns %s sync failed: %s", name, exc)
                failed += 1
                await _hb(session, kind="dns.sync", target_type="dns_server",
                          target_id=srv.id, target_label=name, ok=False, error=str(exc))

        # ── 憑證自動抓取來源（URL / SFTP，依各憑證 fetch_interval 節流）──
        try:
            from app.models.certificate import Certificate
            from app.services.cert_fetch import fetch_certificate
            srcs = (await session.execute(
                select(Certificate).where(Certificate.source_type != "none")
            )).scalars().all()
            for c in srcs:
                interval = timedelta(seconds=c.fetch_interval_seconds)
                if c.last_fetch_at and not _sync_due(c.last_fetch_at, interval):
                    continue
                res = await fetch_certificate(session, c, actor_user_id=None)  # 自行 commit
                if res.get("status") in ("updated", "error"):
                    log.info("cert fetch %s: %s", c.name, res)
        except Exception as exc:
            await session.rollback()
            log.error("cert fetch sweep failed: %s", exc)

        # ── 憑證自動推送到防火牆（0227）：目標上的版本不是目前版本才推；失敗的隔一小時再試 ──
        # 一個目標一個交易：某台推失敗不影響其他台；推送結果與稽核由 engine 記錄
        try:
            from app.services.cert_push.engine import due_targets, push_target
            for t in await due_targets(session):
                try:
                    res = await push_target(session, t, trigger="auto")
                    await session.commit()
                    log.info("cert push %s: %s", t.name, res.get("status"))
                except Exception as exc:
                    await session.rollback()
                    log.error("cert push %s failed: %s", t.name, exc)
        except Exception as exc:
            await session.rollback()
            log.error("cert push sweep failed: %s", exc)

        # ── 憑證到期 / 飄移告警 ──（去重保證每輪呼叫也不洗版）
        try:
            from app.services.cert_alert import check_cert_alerts
            stats = await check_cert_alerts(session)
            await session.commit()
            if stats.get("expiry") or stats.get("drift"):
                log.info("cert alerts: %s", stats)
        except Exception as exc:
            await session.rollback()
            log.error("cert alert check failed: %s", exc)

        # ── API 權杖／MCP 金鑰／公開端點權杖即將到期 ──（同一個門檻只通知一次）
        try:
            from app.services.credential_expiry import check_credential_expiry
            stats = await check_credential_expiry(session)
            await session.commit()
            if stats.get("notified"):
                log.info("credential expiry: %s", stats)
        except Exception as exc:
            await session.rollback()
            log.error("credential expiry check failed: %s", exc)

        # ── 網域：RDAP（到期日、NS、註冊商）每個網域大約一天查一次；到期通知每輪檢查（同一門檻只發一次）──
        try:
            from app.services import domains as _dom_svc
            dsum = await _dom_svc.refresh_all(session, limit=50, pause=1.0, stale_hours=20)
            await session.commit()
            if dsum["checked"]:
                await _hb(session, kind="domain.rdap", target_type="system", target_id=None,
                          target_label="domains", ok=dsum["failed"] == 0, summary=dsum,
                          error=None if dsum["failed"] == 0 else f"{dsum['failed']} failed")
            dstats = await _dom_svc.check_domain_expiry(session)
            await session.commit()
            if dstats.get("notified"):
                log.info("domain expiry: %s", dstats)
        except Exception as exc:
            await session.rollback()
            log.error("domain check failed: %s", exc)

        # ── 稽核鏈驗證與外部錨定 ──
        # 雜湊鏈抓得到中間竄改，**抓不到從尾端整段截斷**（被刪的是還沒人引用的最後幾筆）。
        # 每輪把最新雜湊錨定到資料庫外面，之後那個位置消失或改變就是證據。
        # 驗證是增量的（自上次錨定起），所以每輪的成本與新增筆數成正比，不是整條鏈。
        try:
            from app.services.audit_anchor import verify_and_anchor
            res = await verify_and_anchor(session)
            await session.commit()
            if not res.get("ok"):
                log.error("audit chain verification FAILED: %s", res.get("detail"))
                # 斷鏈是資安事件，不能只寫 log：通知所有管理員（同一種失敗會去重）
                from app.services.audit_anchor import notify_chain_failure
                sent = await notify_chain_failure(session, res)
                await session.commit()
                log.error("audit chain alert sent to %d admin(s)", sent)
        except Exception as exc:
            await session.rollback()
            log.error("audit anchor failed: %s", exc)

        # ── 依網卡 MAC 把 IP 掛回所屬裝置 ──
        # 預設關閉：升級之後突然多出一個每 5 分鐘自動改資料的作業，本身就是不該
        # 發生的事。開啟後也只填空的、不覆寫、不移除，並依十條規則跳過任何有疑慮的
        # 情形。跳過的筆數一起記進 log —— 「全部被守門擋下」不能看起來跟「沒事可做」
        # 一樣。
        try:
            from app.services.ip_device_link import link_by_port_mac
            from app.services.system_config import get_autolink_config
            cfg = await get_autolink_config(session)
            if cfg["enabled"]:
                st = await link_by_port_mac(
                    session, scope_subnet_ids=cfg["scope_subnet_ids"])
                if st.linked or st.skipped_ambiguous or st.skipped_customer:
                    log.info("ip-device autolink: %s", st.summary())
        except Exception as exc:
            await session.rollback()
            log.error("ip-device autolink failed: %s", exc)

        # ── 裝置類型自動判斷：工作站／伺服器（每輪一次）──
        # 只碰「other 而且沒人定過」或「上次就是自動判斷」的裝置，人工設定的一律不動；
        # 依據是這台裝置 IP 上代理回報的作業系統（Wazuh／RustDesk／OCS），其次 nmap，再其次 OCS 機殼。
        try:
            from app.services.device_type_auto import refresh_auto_types
            n_types = await refresh_auto_types(session)
            await session.commit()
            if n_types:
                log.info("device types auto-set: %d", n_types)
        except Exception as exc:
            await session.rollback()
            log.error("device type auto-set failed: %s", exc)

        # ── 未納管位址的目擊：30 天沒再看到就清掉（每輪一次）──
        try:
            from app.services.unmanaged import purge as purge_unmanaged
            n_purged = await purge_unmanaged(session)
            await session.commit()
            if n_purged:
                log.info("unmanaged sightings purged: %d", n_purged)
        except Exception as exc:
            await session.rollback()
            log.error("unmanaged sightings purge failed: %s", exc)

        # ── IP 變更評估：回收沒有心跳的分析（標失敗或重新排隊，由網頁程序啟動）、清除過期的計畫 ──
        try:
            from app.services.change_impact.jobs import reclaim_stale
            from app.services.change_impact.retention import purge as purge_impact
            await reclaim_stale(session)
            gone = await purge_impact(session)
            await session.commit()
            if gone["plans"] or gone["failed_runs"]:
                log.info("change impact purged: %s", gone)
        except Exception as exc:
            await session.rollback()
            log.error("change impact housekeeping failed: %s", exc)

        # ── 偵測到的 DHCP 發放範圍 → 子網路的「位址範圍（集區）」（每輪一次，全站對帳）──
        # 使用者要求（2026-09-27）：子網路上方寫著 DHCP 發放範圍，下面的集區卻是空的。
        # 自動建的跟著上游走；手動建的不動；落點不唯一、跟手動的重疊都不建。
        try:
            from app.services.ip_ranges import sync_auto_dhcp_ranges
            res = await sync_auto_dhcp_ranges(session)
            await session.commit()
            if res["created"] or res["removed"] or res["updated"]:
                log.info("auto DHCP pool ranges: %s", res)
        except Exception as exc:
            await session.rollback()
            log.error("auto DHCP pool ranges failed: %s", exc)

        # ── 上線判定（每輪一次，不依附 LibreNMS）──
        # 以前只在 LibreNMS 同步（而且勾了「用於上線判定」）時重算：沒有 LibreNMS 的站台，
        # 掃描代理或防火牆標成上線的 IP 永遠不會變回離線（2026-09-26 稽核）。放在異常偵測之前，
        # 它讀的是這一輪的結果。
        try:
            await librenms_svc.recompute_effective_status(session)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            log.error("liveness recompute failed: %s", exc)

        # ── 異常偵測（排程）──
        # 與 AI 巡檢同樣沿用這個 timer：每輪只判斷「是否已越過設定的時刻」，沒到就跳過。
        # 預設關閉。跑的是同一支 run_detection，差別在通知只發「與上次相比是新的」——
        # 偵測結果是目前的狀態不是事件流，每天把同一個未處理的衝突再吼一次，
        # 只會讓人把整類通知關掉。
        try:
            from app.services.anomaly import run_scheduled
            from app.services.schedule import due as _due
            from app.services.system_config import (
                get_anomaly_config,
                get_anomaly_last_run,
            )

            acfg = await get_anomaly_config(session)
            if acfg.schedule_enabled:
                alast = await get_anomaly_last_run(session)
                if _due(alast, acfg.times, frequency=acfg.frequency,
                        weekdays=acfg.weekdays, month_day=acfg.month_day,
                        interval_minutes=acfg.interval_minutes):
                    rep = await run_scheduled(session)
                    log.info("anomaly scan: %s finding(s)", rep.total())
        except Exception as exc:
            await session.rollback()
            log.error("anomaly scheduled scan failed: %s", exc)

        # ── 外部 DNS 解析（2026-10-19）── 對外網域底下的名稱，照設定的間隔問外部解析器與權威伺服器。
        # 沒設定解析器或對外網域就什麼都不做。一輪有名稱上限、每筆查詢 3 秒逾時、併發有上限，
        # 不會把這支排程拖過 TimeoutStartSec。
        try:
            from datetime import datetime as _dt

            from app.services import external_dns as _xdns
            from app.services.schedule import due as _due

            xcfg = await _xdns.get_settings(session)
            if _xdns.configured(xcfg):
                xlast_raw = (await _xdns.get_state(session)).get("last_run_at")
                xlast = _dt.fromisoformat(xlast_raw) if xlast_raw else None
                if _due(xlast, [], frequency="interval", interval_minutes=xcfg["interval_hours"] * 60):
                    xsum = await _xdns.run_round(session)
                    await session.commit()
                    await _hb(session, kind="external_dns.run", target_type="system", target_id=None,
                              target_label="external-dns", ok=True, summary=xsum)
                    log.info("external dns: %s", xsum)
        except Exception as exc:
            await session.rollback()
            log.error("external dns round failed: %s", exc)
            await _hb(session, kind="external_dns.run", target_type="system", target_id=None,
                      target_label="external-dns", ok=False, error=str(exc))

        # ── AI 巡檢 ── 不在這裡跑了：改由 jt-ipam-ai-audit.timer 執行本腳本的 --ai-audit 模式（2026-10-12）。
        # 這個服務每輪最多 10 分鐘（TimeoutStartSec=600），巡檢一跑超過就被 systemd 砍掉，
        # 下一輪又判定「還沒跑」整個重來；而且巡檢跑的那幾分鐘，所有整合的同步都被卡住。


    # 個別整合連不到對方（防火牆離線、憑證過期…）是**回報事項**，不是這支排程失敗：
    # 錯誤已寫進該實例的 last_error、畫面上看得到、log 也有。以前這裡回 1，於是
    # systemd 把整輪標成 failed，`systemctl status` 永遠紅著 —— 真正壞掉的時候反而
    # 分不出來（客戶與我們自己都因此誤判過一次）。只有「這一輪根本跑不完」才回非零。
    if failed:
        log.info("sync completed with %d integration error(s) — see last_error on each instance",
                 failed)
    return 0



async def _run_ai_audit() -> int:
    """排程 AI 巡檢（jt-ipam-ai-audit.timer → 本腳本 --ai-audit；2026-10-12 從一般同步搬出來）。

    每 5 分鐘只判斷「是否已越過設定的時刻」，沒到就結束。開始打 LLM 之前就記下這次嘗試
    （system_config.mark_ai_audit_started），所以被砍或 LLM 主機掛掉都不會 5 分鐘後又重來；
    另一個行程（手動巡檢）正在跑就略過。預設關閉，要在 管理 → LLM / AI 明確打開才會跑。
    """
    from app.core.db import SessionLocal
    from app.models.user import User
    from app.services.ai_audit import AuditBusy, due, run_audit
    from app.services.system_config import get_ai_audit_last_run, get_llm_config
    from sqlalchemy import select as _s

    async with SessionLocal() as session:
        try:
            cfg = await get_llm_config(session)
            if not (cfg.enabled and cfg.ai_audit_enabled):
                return 0
            # 用獨立記錄的「上次執行時間」，不是最後一筆發現的時間 ——
            # 沒有發現的巡檢什麼都不會寫，靠發現回推會判成從沒跑過而每輪重跑
            last = await get_ai_audit_last_run(session)
            if not due(last, cfg.ai_audit_times, frequency=cfg.ai_audit_frequency,
                       weekdays=cfg.ai_audit_weekdays, month_day=cfg.ai_audit_month_day):
                return 0
            # 排程沒有「發起者」，取一個管理員當取樣身分 —— 巡檢仍然走 RBAC，
            # 只是這裡的可見範圍由該管理員決定，而不是無條件全庫。
            principal = None
            if cfg.mcp_principal_user_id:
                principal = await session.get(User, cfg.mcp_principal_user_id)
            if principal is None:
                principal = (await session.execute(
                    _s(User).where(User.is_admin.is_(True), User.is_active.is_(True))
                    .order_by(User.created_at).limit(1)
                )).scalars().first()
            if principal is None:
                log.warning("ai audit: no admin to run as, skipped")
                return 0
            r = await run_audit(session, principal)
            if r.error:
                log.error("ai audit failed: %s", r.error)
            else:
                log.info("ai audit: %s finding(s)", r.findings)
        except AuditBusy:
            log.info("ai audit: another run is in progress, skipped")
        except Exception as exc:
            await session.rollback()
            log.error("ai audit check failed: %s", exc)
    # LLM 主機連不上、模型回錯都是回報事項（寫在日誌與巡檢狀態），不是這支排程失敗
    return 0


async def _run_and_dispose(job: Any = None) -> int:
    """跑完一定要 dispose engine —— 這是短命腳本用 async engine 的必要收尾。

    少了它，連線集區裡的 asyncpg 連線會留到直譯器關閉時才被 GC 回收；那時事件迴圈與
    greenlet 都已經沒了，SQLAlchemy 在 finalizer 裡呼叫 `terminate()` 會炸出
    `RuntimeError: greenlet is being finalized` / `MissingGreenlet`，行程以非零結束。
    systemd 於是把每一輪都記成 failed（我們自己的 prod 24 小時內 259 次），而且
    **排在腳本最後面的 AI 巡檢再也沒被執行過** —— 表面症狀是「排程設了卻不會跑」。
    """
    from app.core.db import engine

    try:
        return await (job or _run)()
    finally:
        # dispose 本身出問題也不該蓋掉主要結果，這裡吞掉並記錄就好
        try:
            await engine.dispose()
        except Exception as exc:
            log.warning("engine dispose failed: %s", exc)


def main() -> None:
    # --ai-audit：只跑排程 AI 巡檢（jt-ipam-ai-audit.service，逾時上限放得下最長的巡檢）
    job = _run_ai_audit if "--ai-audit" in sys.argv[1:] else None
    sys.exit(asyncio.run(_run_and_dispose(job)))


if __name__ == "__main__":
    main()
