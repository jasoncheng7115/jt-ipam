"""主機名稱的逐來源實例目擊與清除（2026-09-26）。

問題：`apply_observation` 只會新增、更新；16 個同步來源沒有一個會在上游不再回報時把名稱清掉
（稽核結果見記憶 project_stale_integration_data）。使用者看到的是：DNS 記錄早就刪了、手動同步過，
IP 還是頂著舊主機的名字 —— 「讓人感覺這系統很不實在」。

為什麼不能直接「沒看到就刪」：
- `ip_hostname_observations` 一個來源只有一筆，兩台 OPNsense、兩個 PVE 叢集、兩台 DNS 共用。
  A 沒看到不代表 B 也沒看到 → 逐台刪會刪到別台的，下一輪又加回來，來回翻動。
- 同步常常部分失敗（某個 zone、某個 VDOM、某台節點），沒看到不代表上游刪了。

所以分兩層：
1. `ip_hostname_reports`：每個來源的**每一台實例**（origin）各記各的，每次看到都更新 last_seen_at。
2. 觀測表的值由第 1 層推導（同一來源有多台時取固定的一個），讀取端完全不必改。

用法（每個同步一輪一次）：

    run = HostnameRun(session, source="opnsense", origin=f"opnsense:{fw.id}", peers=啟用中的同類實例數)
    run.report(ip, hostname)          # 看到就報；hostname 空＝上游說這個 IP 沒有名稱
    summary = await run.finish(complete=本輪是否完整抓到)

只有 complete=True 時才會清掉這台本輪沒報到的；一次要清掉一大半、或完整讀取卻回 0 筆時，
斷路器會擋下並在 summary["breaker"] 說明（多半是 API 權限被收、回傳不完整，而不是上游真的刪了）。
刪除一律經過 apply_observation 重算，ip.hostname 與異動記錄才會跟著變。
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values, not_in_values
from app.models.ip_hostname import HOSTNAME_SOURCES, IPHostnameObservation, IPHostnameReport

if TYPE_CHECKING:
    from app.models.address import IPAddress

#: 遷移前就存在、還沒被任何實例認領的舊觀測
LEGACY_ORIGIN = "legacy"
#: 同類有多台時，舊資料要等這麼久沒人認領才清（每台都有機會同步幾輪）
LEGACY_GRACE = timedelta(hours=24)
#: 斷路器：一次要清超過這麼多筆、而且超過這台原有的一半 → 不清
BREAKER_MIN = 20
BREAKER_RATIO = 0.5
#: 完整讀取卻回 0 筆、而這台原本有這麼多筆以上 → 不清（空結果比「真的全刪了」可能得多）
EMPTY_GUARD = 5


def _clean(hostname: str | None) -> str | None:
    return (hostname or "").strip() or None


class HostnameRun:
    """一台實例的一輪同步。見模組說明。"""

    def __init__(self, session: AsyncSession, *, source: str, origin: str, peers: int = 1) -> None:
        if source not in HOSTNAME_SOURCES or source == "manual":
            raise ValueError(f"not a synced hostname source: {source!r}")
        self.session = session
        self.source = source
        self.origin = origin
        self.peers = max(1, int(peers))
        self.run_at = datetime.now(UTC)
        self._ips: dict[uuid.UUID, IPAddress] = {}
        self._names: dict[uuid.UUID, set[str]] = {}
        self._cleared: set[uuid.UUID] = set()
        self._held: set[str] = set()

    def report(self, ip: IPAddress, hostname: str | None) -> None:
        """記下這台看到的名稱。同一輪同一個 IP 報了好幾個名字 → 結束時取固定的一個。"""
        self._ips[ip.id] = ip
        hn = _clean(hostname)
        if hn:
            self._names.setdefault(ip.id, set()).add(hn)
            self._cleared.discard(ip.id)
        elif ip.id not in self._names:
            self._cleared.add(ip.id)

    def hold(self, hostname: str | None) -> None:
        """這個名稱的實體這一輪資料不確定（例如 VM 的設定讀不到、guest agent 沒回應）：
        它先前回報過的名稱全部保留、不清。實體若已從上游刪除，就不會被 hold，照常清掉。"""
        hn = _clean(hostname)
        if hn:
            self._held.add(hn)

    async def finish(self, *, complete: bool) -> dict[str, Any]:
        s = self.session
        await s.flush()
        affected: set[uuid.UUID] = set()

        # 1. 寫入本輪看到的（名稱取字典序最小：穩定，不會因為迭代順序每輪翻動）。
        #    每一筆都要蓋 last_seen_at（清理靠它），所以整批 upsert：以前每個 IP 各一次，
        #    一輪十萬個名稱就是十萬次查詢。一次 4,000 列（參數上限 32767）。
        # 依 IP id 排序：別的交易同時寫同一批列時，取得鎖的順序一致，不會互相死結
        rows = [{"ip_id": ip_id, "source": self.source, "origin": self.origin, "hostname": min(names),
                 "first_seen_at": self.run_at, "last_seen_at": self.run_at}
                for ip_id, names in sorted(self._names.items())]
        # executemany（語句只編譯一次）：`.values(大清單)` 每批要產生幾萬個參數節點，光編譯就是秒級
        ins = pg_insert(IPHostnameReport)
        upsert = ins.on_conflict_do_update(
            constraint="uq_ip_hostname_reports_ip_source_origin",
            set_={"hostname": ins.excluded.hostname, "last_seen_at": ins.excluded.last_seen_at},
        )
        for i in range(0, len(rows), 10000):
            await s.execute(upsert, rows[i:i + 10000])
        affected.update(self._names)

        # 2. 上游明說沒有名稱的 IP：這台的那一筆清掉（不必等完整）
        if self._cleared:
            await s.execute(delete(IPHostnameReport).where(
                IPHostnameReport.source == self.source, IPHostnameReport.origin == self.origin,
                in_values(IPHostnameReport.ip_id, self._cleared)))
            affected |= self._cleared

        # 3. 認領：真實實例報到了的 IP，舊資料那一筆就不需要了
        if self._names:
            await s.execute(delete(IPHostnameReport).where(
                IPHostnameReport.source == self.source, IPHostnameReport.origin == LEGACY_ORIGIN,
                in_values(IPHostnameReport.ip_id, self._names)))

        pruned, breaker = 0, None
        if complete:
            pruned, breaker, gone = await self._prune()
            affected |= gone

        changed = await self._derive(affected)
        return {"reported": len(self._names), "cleared": len(self._cleared),
                "pruned": pruned, "breaker": breaker, "hostname_changed": changed}

    async def _prune(self) -> tuple[int, str | None, set[uuid.UUID]]:
        s = self.session
        seen = set(self._names)
        mine_total = (await s.execute(select(func.count()).select_from(IPHostnameReport).where(
            IPHostnameReport.source == self.source, IPHostnameReport.origin == self.origin))).scalar_one()
        stale_q = select(IPHostnameReport.ip_id).where(
            IPHostnameReport.source == self.source, IPHostnameReport.origin == self.origin,
            IPHostnameReport.last_seen_at < self.run_at)
        if self._held:
            stale_q = stale_q.where(not_in_values(IPHostnameReport.hostname, self._held))
        stale_mine = set((await s.execute(stale_q)).scalars().all()) - seen
        # 舊資料：只有一台時「這台沒看到」就是「沒人看到」；有多台時等寬限期過了、仍沒人認領
        legacy_q = select(IPHostnameReport.ip_id).where(
            IPHostnameReport.source == self.source, IPHostnameReport.origin == LEGACY_ORIGIN)
        if self._held:
            legacy_q = legacy_q.where(not_in_values(IPHostnameReport.hostname, self._held))
        if self.peers > 1:
            legacy_q = legacy_q.where(IPHostnameReport.last_seen_at < self.run_at - LEGACY_GRACE)
        stale_legacy = set((await s.execute(legacy_q)).scalars().all()) - seen

        total = mine_total + len(stale_legacy)
        doomed = len(stale_mine) + len(stale_legacy)
        if doomed and not seen and total >= EMPTY_GUARD:
            return 0, f"empty read would remove {doomed} of {total}", set()
        if doomed > BREAKER_MIN and doomed > total * BREAKER_RATIO:
            return 0, f"would remove {doomed} of {total}", set()

        if stale_mine:
            await s.execute(delete(IPHostnameReport).where(
                IPHostnameReport.source == self.source, IPHostnameReport.origin == self.origin,
                in_values(IPHostnameReport.ip_id, stale_mine)))
        if stale_legacy:
            await s.execute(delete(IPHostnameReport).where(
                IPHostnameReport.source == self.source, IPHostnameReport.origin == LEGACY_ORIGIN,
                in_values(IPHostnameReport.ip_id, stale_legacy)))
        return doomed, None, stale_mine | stale_legacy

    async def _derive(self, ip_ids: Iterable[uuid.UUID]) -> int:
        """依目擊表重算這些 IP 在這個來源的觀測值；有變才寫（經 apply_observation）。"""
        ids = sorted(ip_ids)    # 寫入順序固定（同上，避免死結）
        if not ids:
            return 0
        from app.services.hostname import apply_observations_bulk

        s = self.session
        rows = (await s.execute(select(
            IPHostnameReport.ip_id, IPHostnameReport.origin, IPHostnameReport.hostname).where(
            IPHostnameReport.source == self.source, in_values(IPHostnameReport.ip_id, ids)))).all()
        real: dict[uuid.UUID, list[str]] = {}
        legacy: dict[uuid.UUID, list[str]] = {}
        for ip_id, origin, hn in rows:
            (legacy if origin == LEGACY_ORIGIN else real).setdefault(ip_id, []).append(hn)
        current = dict((await s.execute(select(
            IPHostnameObservation.ip_id, IPHostnameObservation.hostname).where(
            IPHostnameObservation.source == self.source,
            in_values(IPHostnameObservation.ip_id, ids)))).all())

        wants: dict[uuid.UUID, str | None] = {}
        for ip_id in ids:
            # 真實實例的回報優先；同一來源有多台時取字典序最小（確定、不翻動）
            names = real.get(ip_id) or legacy.get(ip_id) or []
            want = min(names) if names else None
            if (current.get(ip_id) or None) != want:
                wants[ip_id] = want
        # 整批（以前每個有變的 IP 各走一次 apply_observation，約 7 次查詢）
        return await apply_observations_bulk(s, source=self.source, wants=wants, ips=self._ips)


async def forget_origin(session: AsyncSession, *, source: str, origin_prefix: str) -> int:
    """刪除／停用一台實例時：它回報的名稱全部收回，並重算受影響的 IP。回傳受影響 IP 數。"""
    ids = set((await session.execute(select(IPHostnameReport.ip_id).where(
        IPHostnameReport.source == source,
        IPHostnameReport.origin.startswith(origin_prefix, autoescape=True)))).scalars().all())
    if not ids:
        return 0
    await session.execute(delete(IPHostnameReport).where(
        IPHostnameReport.source == source,
        IPHostnameReport.origin.startswith(origin_prefix, autoescape=True)))
    run = HostnameRun(session, source=source, origin=origin_prefix)
    await run._derive(ids)
    return len(ids)


async def enabled_peers(session: AsyncSession, model: Any) -> int:
    """同類整合啟用中的實例數（HostnameRun 的 peers）。模型沒有 enabled 欄位就算全部。"""
    stmt = select(func.count()).select_from(model)
    if hasattr(model, "enabled"):
        stmt = stmt.where(model.enabled.is_(True))
    return max(1, int((await session.execute(stmt)).scalar_one()))


#: 每個來源的 origin `<來源>:<實例 id>` 指向哪個資料表（給「這個名字是哪一台回報的」顯示用，2026-10-11）。
#: 客戶在比對各台掃描代理的結果：只寫「NetBIOS（掃描代理）」看不出是哪一台。新來源要加進來（有守門測試）。
ORIGIN_MODELS: dict[str, tuple[str, str]] = {
    "scanner": ("app.models.scan_agent", "ScanAgent"),
    "netbios": ("app.models.scan_agent", "ScanAgent"),
    "mdns": ("app.models.scan_agent", "ScanAgent"),
    "dns": ("app.models.dns", "DNSServer"),
    "librenms": ("app.models.librenms", "LibreNMSInstance"),
    "opnsense": ("app.models.firewall", "OPNsenseFirewall"),
    "pfsense": ("app.models.pfsense", "PfSenseFirewall"),
    "fortigate": ("app.models.fortigate", "FortiGateFirewall"),
    "paloalto": ("app.models.paloalto", "PaloAltoFirewall"),
    "checkpoint": ("app.models.checkpoint_gaia", "CheckPointGaiaTarget"),
    "mikrotik": ("app.models.mikrotik", "MikroTikRouter"),
    "windows_dhcp": ("app.models.windows_dhcp", "WindowsDhcpServer"),
    "kea_dhcp": ("app.models.dhcp_standalone", "KeaDhcpServer"),
    "isc_dhcp": ("app.models.dhcp_standalone", "IscDhcpServer"),
    "technitium": ("app.models.technitium", "TechnitiumDhcpServer"),
    "isoinsight": ("app.models.isoinsight", "IsoInsightSource"),
    # Proxmox 的 origin 是叢集 id（沒有叢集的連線才用連線本身的 id，那張表沒有名稱 → 顯示 API 主機）
    "proxmox": ("app.models.virt", "VirtCluster"),
    "hyperv": ("app.models.hyperv", "HyperVInstance"),
    "openstack": ("app.models.openstack", "OpenStackInstance"),
    "zabbix": ("app.models.zabbix", "ZabbixInstance"),
    "checkmk": ("app.models.checkmk", "CheckmkInstance"),
    "wazuh": ("app.models.wazuh", "WazuhInstance"),
    "adguard": ("app.models.adguard", "AdGuardInstance"),
    "ocs": ("app.models.ocs", "OcsServer"),
    "rustdesk": ("app.models.rustdesk", "RustDeskServer"),
}
#: 由使用者輸入、不是任何實例回報的來源
NO_ORIGIN_SOURCES = ("manual",)


def _model(source: str) -> Any:
    import importlib
    mod, cls = ORIGIN_MODELS[source]
    return getattr(importlib.import_module(mod), cls)


def _dt(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


async def reports_by_source(session: AsyncSession, ip_id: uuid.UUID) -> dict[str, list[dict[str, Any]]]:
    """單一 IP 每個來源的逐台回報：哪一台（kind／label）、回報的名字、第一次與最後一次看到。

    kind：scan_agent／integration／legacy（遷移前的舊資料，沒有歸屬）／unknown（那台已經刪除，或 origin 認不得）。
    認不得的一律 label=None，不拿 id 去猜名字。"""
    rows = (await session.execute(select(IPHostnameReport).where(IPHostnameReport.ip_id == ip_id)
                                  .order_by(IPHostnameReport.last_seen_at.desc()))).scalars().all()
    wanted: dict[str, set[uuid.UUID]] = {}
    parsed: list[tuple[IPHostnameReport, uuid.UUID | None, str | None]] = []
    for r in rows:
        parts = r.origin.split(":")
        iid: uuid.UUID | None = None
        if r.origin != LEGACY_ORIGIN and len(parts) >= 2 and r.source in ORIGIN_MODELS:
            try:
                iid = uuid.UUID(parts[1])
            except ValueError:
                iid = None
        if iid is not None:
            wanted.setdefault(r.source, set()).add(iid)
        parsed.append((r, iid, ":".join(parts[2:]) or None))

    names: dict[tuple[str, uuid.UUID], str] = {}
    for src, ids in wanted.items():       # 一個 IP 頂多十來個來源，逐來源查一次
        model = _model(src)
        for iid, name in (await session.execute(
                select(model.id, model.name).where(in_values(model.id, list(ids))))).all():
            names[(src, iid)] = name
    rest = [i for i in wanted.get("proxmox", set()) if ("proxmox", i) not in names]
    if rest:
        from urllib.parse import urlsplit

        from app.models.virt import ProxmoxInstance
        for iid, url in (await session.execute(select(ProxmoxInstance.id, ProxmoxInstance.api_url).where(
                in_values(ProxmoxInstance.id, rest)))).all():
            names[("proxmox", iid)] = urlsplit(url or "").hostname or url

    out: dict[str, list[dict[str, Any]]] = {}
    for r, iid, sub in parsed:
        label = names.get((r.source, iid)) if iid is not None else None
        if r.origin == LEGACY_ORIGIN:
            kind = "legacy"
        elif label is None:
            kind = "unknown"
        elif r.source in ("scanner", "netbios", "mdns"):
            kind = "scan_agent"
        else:
            kind = "integration"
        out.setdefault(r.source, []).append({
            "kind": kind, "label": label, "detail": sub, "hostname": r.hostname,
            "first_seen_at": _dt(r.first_seen_at), "last_seen_at": _dt(r.last_seen_at)})
    return out
