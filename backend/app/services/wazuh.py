"""Wazuh API client + agent inventory 同步。

API ref: https://documentation.wazuh.com/current/user-manual/api/reference.html

主要 endpoints：
  POST /security/user/authenticate     拿 JWT (basic auth)
  GET  /agents                         列出所有 agent
  GET  /sca/{agent_id}                 資安組態評估（SCA）各政策的通過／未通過數

漏洞（CVE）資料**不在這裡**：Wazuh 4.8 起 manager API 已無漏洞端點（實機 4.14.5 的
150 條路徑裡一條都沒有），唯一來源是 Wazuh Indexer —— 那需要另一組能讀取整個 SIEM
事件的憑證，代價與收益不成比例，因此不接。資安體質改用 SCA 呈現。

OWASP：
- A02：API password 雙欄 AES-GCM，aad 綁 instance id
- A05/A10：safe_request；verify_tls 旗標
- A09：sync 寫 audit；missing-agent 異常事件
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.core.safe_http import UnsafeOutboundURL, safe_request, transport_detail
from app.core.security import decrypt_secret, encrypt_secret
from app.core.sqlin import in_values, not_in_values
from app.models.address import IPAddress
from app.models.wazuh import WazuhAgent, WazuhInstance

logger = logging.getLogger(__name__)


class WazuhError(RuntimeError):
    pass


def _scope_subnet_uuids(obj: WazuhInstance) -> set[Any]:
    """obj.scope_subnet_ids（JSONB 字串陣列）→ UUID set；空回空 set（不限範圍）。"""
    out: set[Any] = set()
    for s in (obj.scope_subnet_ids or []):
        try:
            out.add(uuid.UUID(str(s)))
        except (ValueError, TypeError):
            pass
    return out


# ─────────────────── 加解密 ───────────────────


def _aad(instance_id) -> bytes:  # type: ignore[no-untyped-def]
    return f"wazuh_instance:{instance_id}:api_password".encode()


def encrypt_password(instance_id, raw: str) -> tuple[bytes, bytes]:  # type: ignore[no-untyped-def]
    return encrypt_secret(raw, aad=_aad(instance_id))


def _decrypt_password(inst: WazuhInstance) -> str:
    return decrypt_secret(
        inst.api_password_enc, inst.api_password_nonce, aad=_aad(inst.id)
    ).decode("utf-8")


# ─────────────────── JWT cache ───────────────────


@dataclass
class _Token:
    jwt: str
    expires_at: float


_token_cache: dict[str, _Token] = {}   # key: instance.id


async def _authenticate(inst: WazuhInstance) -> str:
    """拿 Wazuh JWT；TTL ~15 分鐘，本端 cache 12 分。"""
    cached = _token_cache.get(str(inst.id))
    if cached and cached.expires_at > time.time() + 30:
        return cached.jwt

    pwd = _decrypt_password(inst)
    auth = base64.b64encode(f"{inst.api_user}:{pwd}".encode()).decode("ascii")
    url = f"{inst.api_url.rstrip('/')}/security/user/authenticate"
    try:
        resp = await safe_request(
            "POST", url,
            headers={"Authorization": f"Basic {auth}"},
            timeout=15.0, verify=inst.verify_tls,
        )
    except UnsafeOutboundURL as exc:
        raise WazuhError(f"SSRF guard rejected URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise WazuhError(f"transport: {transport_detail(exc)}") from exc
    if resp.status_code != 200:
        raise WazuhError(f"Wazuh auth {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    token = (data.get("data") or {}).get("token") or data.get("token")
    if not token:
        raise WazuhError(f"Wazuh auth: no token in response: {data}")
    _token_cache[str(inst.id)] = _Token(jwt=token, expires_at=time.time() + 12 * 60)
    return token  # type: ignore[no-any-return]


def _invalidate_token(inst: WazuhInstance) -> None:
    _token_cache.pop(str(inst.id), None)


# ─────────────────── 低階 HTTP ───────────────────


async def _api_get(
    inst: WazuhInstance, path: str, params: dict[str, Any] | None = None,
    *, timeout: float = 30.0,
) -> dict[str, Any]:
    token = await _authenticate(inst)
    url = f"{inst.api_url.rstrip('/')}{path}"
    try:
        resp = await safe_request(
            "GET", url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params=params, timeout=timeout, verify=inst.verify_tls,
        )
    except UnsafeOutboundURL as exc:
        raise WazuhError(f"SSRF guard rejected URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise WazuhError(f"transport: {transport_detail(exc)}") from exc
    if resp.status_code == 401:
        # token 失效；重發
        _invalidate_token(inst)
        token = await _authenticate(inst)
        resp = await safe_request(
            "GET", url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params=params, timeout=timeout, verify=inst.verify_tls,
        )
    if resp.status_code != 200:
        raise WazuhError(f"Wazuh GET {path}: {resp.status_code} {resp.text[:200]}")
    return resp.json()  # type: ignore[no-any-return]


async def healthcheck(inst: WazuhInstance) -> dict[str, Any]:
    return await _api_get(inst, "/", timeout=8.0)


# ─────────────────── Agent inventory ───────────────────


def _index_by_ip(ip_rows: Any) -> tuple[dict[str, Any], set[str]]:
    """IP 字串 → IPAddress.id；**對應到多筆的一律不收**。

    重疊網段（例如甲乙兩單位都用 192.168.123.0/24）下，同一個 IP 字串是兩台不同機器。
    以前這裡用 dict 覆寫，等於依資料庫回傳順序任意挑一筆 —— 掛錯比沒有更糟：
    沒有資料使用者會去查，掛錯了不會知道，而且在多單位環境下是跨單位的資料外洩。

    要讓這些位址對應得上，就在該整合實例設定「限定子網路範圍」，把候選縮到一個單位。
    """
    seen: dict[str, Any] = {}
    ambiguous: set[str] = set()
    for aid, ip in ip_rows:
        key = str(ip).split("/", 1)[0]
        if key in ambiguous:
            continue
        if key in seen and seen[key] != aid:
            del seen[key]
            ambiguous.add(key)
            continue
        seen[key] = aid
    return seen, ambiguous


async def build_ip_map(
    session: AsyncSession, *, scope_ids: set[Any],
) -> tuple[dict[str, Any], set[str]]:
    """取得（IP→IPAddress.id 對應表, 不明確的 IP 集合），已套用限定子網路範圍。"""
    stmt = select(IPAddress.id, IPAddress.ip)
    if scope_ids:
        stmt = stmt.where(in_values(IPAddress.subnet_id, scope_ids))
    return _index_by_ip((await session.execute(stmt)).all())


def _clean_ip(s: str | None) -> str | None:
    """register_ip / ip 欄位是 INET；Wazuh 可能回 'any' 等非 IP 值 → 存 NULL 避免 DataError。"""
    if not s:
        return None
    import ipaddress
    try:
        ipaddress.ip_address(s.strip())
    except ValueError:
        return None
    return s.strip()


def _inet_str(v: Any) -> str | None:
    """INET 欄位讀回來的值（位址物件或字串）→ 不帶遮罩的字串，拿來跟上游的字串比。"""
    return None if v is None else str(v).split("/", 1)[0]


def _parse_keep_alive(s: str | None) -> datetime | None:
    if not s:
        return None
    # Wazuh: "2024-01-15T10:23:45Z" 或 "9999-12-31T23:59:59Z"（never disconnected）
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.year > 9000:
        return None
    return dt


def agent_predates_device(agent: Any, ip: Any) -> bool:
    """位址換給另一台設備（device_since）之後就沒再回報過的 agent：說的是上一台（2026-10-12）。"""
    since = getattr(ip, "device_since", None)
    ka = getattr(agent, "last_keep_alive", None)
    return since is not None and (ka is None or ka < since)


#: 斷線的代理最多代表它登記的 IP 多久（天）；超過就當作那個位址已經沒有這台
AGENT_STALE_DAYS = 30


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def agent_represents_ip(agent: Any, ip: Any, *, grace_hours: int = 24) -> bool:
    """這個 Wazuh agent 現在還代表這個 IP 嗎？

    只比對 IP 位址是不夠的：**DHCP 位址會被回收**。實機上 agent 015
    （`laptop-a1.local`，macOS）失聯後，它登記的 192.168.123.187 被 Proxmox 上的
    Linux VM 拿去用，我們卻把 macOS 貼到了那台 VM 上。

    判準：agent 還連著就算數；已失聯的話，只要這個 IP 在它失聯**之後**還被偵測到活著，
    就代表現在佔用這個位址的是別台機器。

    只是關機的機器不受影響 —— 沒有更新的存活證據時仍然採用它的資料，否則修掉一個錯誤
    會製造另一個（每台關機的主機都立刻失去 OS 資訊）。
    """
    from datetime import timedelta

    ka = getattr(agent, "last_keep_alive", None)
    if agent_predates_device(agent, ip):
        return False
    if (getattr(agent, "status", "") or "").lower() == "active":
        return True
    if ka is None:
        return False   # 從未回報過，又不是 active → 沒有理由相信這個對映
    # 斷線太久的代理不再代表這個 IP（使用者 2026-10-13：「停用的不要抓」）。以前沒有上限：
    # 失聯好幾年的代理只要那個 IP 沒有別的存活證據，就一直替它命名、提供 OS。
    # 30 天內的「只是關機」照舊採用（與 OCS 預設的過期天數相同）
    if _aware(ka) < datetime.now(UTC) - timedelta(days=AGENT_STALE_DAYS):
        return False
    from app.services.arp_seen import newest_aging
    from app.services.evidence import DETAILED_SOURCES
    # 防火牆的 ARP／VPN 表也算「這個位址現在有人在用」的證據（會逾時淘汰的那些），
    # 只看 scanner/librenms 會漏掉沒裝掃描代理、也沒接 LibreNMS 的站台。
    fw_seen, _ = newest_aging(ip, set(DETAILED_SOURCES))
    seen = [t for t in (getattr(ip, "last_seen_scanner", None),
                        getattr(ip, "last_seen_librenms", None),
                        fw_seen) if t is not None]
    if not seen:
        return True    # 沒有其他存活證據 → 可能只是關機，仍然採用
    return ka >= max(seen) - timedelta(hours=grace_hours)


async def fetch_agents(inst: WazuhInstance, *, batch: int = 500) -> list[dict[str, Any]]:
    """分頁拉所有 agent。"""
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        data = await _api_get(
            inst, "/agents",
            params={"limit": batch, "offset": offset, "select":
                    "id,name,ip,registerIP,status,os.platform,os.version,os.name,version,"
                    "group,node_name,lastKeepAlive"},
        )
        items = (data.get("data") or {}).get("affected_items") or []
        if not items:
            break
        out.extend(items)
        total = (data.get("data") or {}).get("total_affected_items") or len(items)
        offset += len(items)
        if offset >= int(total):
            break
    return out


async def sync_agents(session: AsyncSession, inst: WazuhInstance) -> dict[str, Any]:
    """從 Wazuh 拉 agents，upsert 到 wazuh_agents；對映到 IPAddress。

    查詢次數不隨代理數成長（2026-09-30 大量資料測試：以前每個代理各查一次鏡像列、兩次 IP，
    3 萬個代理一輪 15 萬次查詢、334 秒，什麼都沒變也要 155 秒）：鏡像列與要用到的 IP 先整批載入，
    「這一輪看到了」的時間最後一次寫完。
    """
    agents_raw = await fetch_agents(inst)
    now = datetime.now(UTC)
    seen_ids: set[str] = set()
    matched_ip = 0
    new_count = 0
    upd_count = 0

    # 重疊網段：若 instance 設了 scope_subnet_ids，IP→IPAddress 比對限定在這些子網路內
    scope_ids = _scope_subnet_uuids(inst)
    ip_map, ambiguous = await build_ip_map(session, scope_ids=scope_ids)
    from types import SimpleNamespace

    from app.models.wazuh import WazuhInstance as _Inst
    from app.services.hostname_reports import HostnameRun, enabled_peers
    hn_run = HostnameRun(session, source="wazuh", origin=f"wazuh:{inst.id}",
                         peers=await enabled_peers(session, _Inst))

    by_id: dict[str, WazuhAgent] = {a.agent_id: a for a in (await session.execute(
        select(WazuhAgent).where(WazuhAgent.instance_id == inst.id))).scalars()}
    wanted = {ip_map[ip] for ip in (_clean_ip(r.get("ip")) for r in agents_raw) if ip and ip in ip_map}
    ips: dict[Any, IPAddress] = {i.id: i for i in (await session.execute(
        select(IPAddress).where(in_values(IPAddress.id, wanted)))).scalars()} if wanted else {}

    for raw in agents_raw:
        agent_id = str(raw.get("id") or "").strip()
        if not agent_id or agent_id == "000":
            # 000 是 manager 自己；不算 agent
            continue
        if agent_id in seen_ids:
            # 同一個代理在回應裡出現兩次（上游分頁重疊）：以前第二次會再新增一筆、撞唯一鍵，
            # 整個 Wazuh 同步失敗
            continue
        seen_ids.add(agent_id)

        ip = _clean_ip(raw.get("ip"))
        register_ip = _clean_ip(raw.get("registerIP"))
        os_block = raw.get("os") or {}
        group = (",".join(raw.get("group") or []) if isinstance(raw.get("group"), list)
                 else raw.get("group"))

        addr_id = ip_map.get(ip) if ip else None
        keep_alive = _parse_keep_alive(raw.get("lastKeepAlive"))
        ipa = ips.get(addr_id) if addr_id is not None else None
        if addr_id is not None:
            matched_ip += 1
        if ipa is not None:
            # 上線判定：agent 的 keep-alive 是 manager 端維護、會過期的存活證據
            # （使用者要求納入）。**記 keep-alive 本身的時間，不是同步當下的時間** ——
            # 記成 now 的話，一台三個月前就失聯的 agent 每次同步都會讓那個 IP 變上線。
            # 同一個 IP 可能有多個 agent 登記（DHCP 位址被回收），取最新的一個。
            if keep_alive is not None and (ipa.last_seen_wazuh is None or ipa.last_seen_wazuh < keep_alive):
                ipa.last_seen_wazuh = keep_alive
            # 回填 IP 主機名稱（來源 "wazuh"，依名稱順序決定是否採用）。
            # 只採用「現在還代表這個 IP」的代理（與 OS 同一條判準）：DHCP 位址被回收再配給
            # 別台後，舊代理的登記還在 —— 以前照樣寫進去、而且永遠不清（2026-09-26 稽核）。
            # 多個代理都代表同一個 IP 時，HostnameRun 同一輪內取固定的一個（不再每輪互相覆寫）
            probe = SimpleNamespace(status=raw.get("status"), last_keep_alive=keep_alive)
            if agent_represents_ip(probe, ipa):
                hn_run.report(ipa, (raw.get("name") or "").strip() or None)

        existing = by_id.get(agent_id)
        if existing is None:
            by_id[agent_id] = WazuhAgent(
                instance_id=inst.id,
                agent_id=agent_id,
                name=raw.get("name"),
                ip=ip, register_ip=register_ip,
                status=raw.get("status"),
                os_platform=os_block.get("platform"),
                os_version=os_block.get("version"),
                os_name=(os_block.get("name") or None),
                agent_version=raw.get("version"),
                group=group,
                node_name=raw.get("node_name"),
                last_keep_alive=keep_alive,
                last_seen_at=now,
                jt_ipam_address_id=addr_id,
            )
            session.add(by_id[agent_id])
            new_count += 1
        else:
            # 值沒變的欄位 ORM 不會寫；last_seen_at 迴圈外一次寫完
            existing.name = raw.get("name") or existing.name
            # INET 讀回來是位址物件、上游給的是字串：直接指派每一輪都算「有變」，每一列都被重寫
            if _inet_str(existing.ip) != ip:
                existing.ip = ip
            if _inet_str(existing.register_ip) != register_ip:
                existing.register_ip = register_ip
            existing.status = raw.get("status")
            existing.os_platform = os_block.get("platform")
            existing.os_version = os_block.get("version")
            existing.os_name = os_block.get("name") or None
            existing.agent_version = raw.get("version")
            existing.group = group
            existing.node_name = raw.get("node_name")
            existing.last_keep_alive = keep_alive
            existing.jt_ipam_address_id = addr_id
            upd_count += 1

    # 分頁任何一頁失敗都會往外拋 → 走到這裡就是完整的代理清單
    hn = await hn_run.finish(complete=True)

    # 已從 Wazuh 刪除的代理：鏡像列也刪。以前 seen_ids 收集了卻從來沒用，幽靈代理永遠留著，
    # 刪除時若是 active，它的 OS 會一直被當成那個 IP 的有效 OS（2026-09-26 稽核）。
    # 讀到 0 個代理、先前卻有：多半是權限或 API 出問題，不刪。
    removed = 0
    if seen_ids:
        await session.flush()
        removed = (await session.execute(delete(WazuhAgent).where(
            WazuhAgent.instance_id == inst.id, not_in_values(WazuhAgent.agent_id, seen_ids)))).rowcount or 0
        # 剩下的都是這一輪看到的
        await session.execute(update(WazuhAgent).where(WazuhAgent.instance_id == inst.id)
                              .values(last_seen_at=now).execution_options(synchronize_session=False))
        for a in by_id.values():
            if a.agent_id in seen_ids:
                set_committed_value(a, "last_seen_at", now)

    inst.last_sync_at = now
    inst.last_error = (f"hostname cleanup skipped: {hn['breaker']}" if hn["breaker"] else None)

    return {
        "removed": removed,
        "fetched": len(agents_raw),
        "new": new_count,
        "updated": upd_count,
        "matched_ip": matched_ip,
        # 因為同一個 IP 字串在多個子網路中都有紀錄而**沒有**對應的數量。
        # 不是零的話，該實例應設定「限定子網路範圍」把候選縮到一個單位。
        "ambiguous_ip": len(ambiguous),
        "synced_at": now.isoformat(),
    }


async def find_missing_agents(
    session: AsyncSession, *, instance_id: uuid.UUID | None = None, hostnamed_only: bool = True,
    subnet_ids: list[uuid.UUID] | None = None,
) -> list[dict[str, Any]]:
    """找應該裝 Wazuh 卻沒有 active agent 的 IP。

    判斷條件：
    - IP 在 jt_ipam 有設 hostname（hostnamed_only=True）
    - 該 IP 沒有對映到 active 狀態的 WazuhAgent

    `instance_id`=None → 跨所有 Wazuh instance 比對。
    `subnet_ids`=None → 全域；給了就只看這些子網路（問「某網段有誰沒裝」時必須給，
    否則會把全站的缺口當成該網段的答案回去）。
    """
    sub = select(WazuhAgent.jt_ipam_address_id).where(
        WazuhAgent.status == "active",
        WazuhAgent.jt_ipam_address_id.is_not(None),
    )
    if instance_id is not None:
        sub = sub.where(WazuhAgent.instance_id == instance_id)
    stmt = select(IPAddress.id, IPAddress.ip, IPAddress.hostname).where(
        IPAddress.id.not_in(sub),
    )
    if hostnamed_only:
        stmt = stmt.where(IPAddress.hostname.is_not(None), IPAddress.hostname != "")
    if subnet_ids is not None:
        if not subnet_ids:
            return []
        stmt = stmt.where(in_values(IPAddress.subnet_id, subnet_ids))
    rows = (await session.execute(stmt)).all()
    return [
        {
            "ip_address_id": str(rid),
            "ip": str(rip).split("/", 1)[0] if rip else None,
            "hostname": hostname,
        }
        for rid, rip, hostname in rows
    ]


#: 每個代理的 SCA 隔多久查一次（Wazuh 預設 12 小時跑一次 SCA 掃描）
SCA_REFRESH = timedelta(hours=12)
#: 每一輪同步最多查幾個代理（同步每 5 分鐘一輪 → 一天約 5.7 萬個；遠低於 API 預設每分鐘 300 個的限流）
SCA_MAX_PER_RUN = 200
#: 同時查幾個
SCA_CONCURRENCY = 4


async def fetch_sca(inst: WazuhInstance, agent_id: str) -> list[dict[str, Any]]:
    """某個 agent 的 SCA 政策結果。用現有的 manager API 帳號即可，不需要額外憑證。"""
    resp = await _api_get(inst, f"/sca/{agent_id}")
    rows = ((resp.get("data") or {}).get("affected_items") or [])
    return [r for r in rows if isinstance(r, dict)]


async def sync_sca(session: AsyncSession, inst: WazuhInstance, *, max_agents: int | None = None) -> int:
    """把每個 agent 的 SCA 摘要寫回 wazuh_agents。

    一台機器可能同時跑多個基準（CIS、廠商自訂…）。畫面上只放得下一個數字時，
    存**分數最低**的那一個 —— 挑最好看的等於自我安慰。

    單一 agent 查詢失敗不影響其他台：SCA 沒跑過的 agent 本來就會回空清單。
    """
    # 超大規模（2026-09-30）：以前每一輪都對**每個**代理打一次 API。Wazuh API 預設每分鐘
    # 只收 300 個請求，3 萬個代理光一輪就要 100 分鐘以上，被限流的請求還被當成「沒有 SCA」
    # 安靜略過；整輪同步又是依序跑的，其他整合全被卡住。改成：每個代理隔 SCA_REFRESH 才查一次、
    # 每輪最多 SCA_MAX_PER_RUN 個、最久沒查的先查、被限流就停（後面的一樣會被擋）。
    limit = SCA_MAX_PER_RUN if max_agents is None else max_agents
    now = datetime.now(UTC)
    agents = (await session.execute(
        select(WazuhAgent).where(
            WazuhAgent.instance_id == inst.id,
            or_(WazuhAgent.sca_checked_at.is_(None), WazuhAgent.sca_checked_at < now - SCA_REFRESH))
        .order_by(WazuhAgent.sca_checked_at.asc().nulls_first(), WazuhAgent.agent_id)
        .limit(limit)
    )).scalars().all()
    n = 0
    for i in range(0, len(agents), SCA_CONCURRENCY):
        chunk = agents[i:i + SCA_CONCURRENCY]

        async def _one(agent_id: str) -> Any:
            try:
                return await fetch_sca(inst, agent_id)
            except WazuhError as exc:
                return exc
        results = await asyncio.gather(*(_one(a.agent_id) for a in chunk))
        limited = False
        for a, rows in zip(chunk, results, strict=True):
            if isinstance(rows, WazuhError):
                limited = limited or " 429" in str(rows)
                continue                       # 沒查到的不算查過，下一輪再來
            a.sca_checked_at = now
            if not rows:
                continue
            worst = min(rows, key=lambda r: int(r.get("score") or 0))
            a.sca_policy = str(worst.get("name") or "")[:128] or None
            a.sca_score = int(worst.get("score") or 0)
            a.sca_pass = int(worst.get("pass") or 0)
            a.sca_fail = int(worst.get("fail") or 0)
            a.sca_policy_count = len(rows)
            a.sca_scanned_at = now
            n += 1
        if limited:
            logger.warning("wazuh %s: SCA rate limited after %d agents; the rest wait for the next run",
                           inst.name, i + len(chunk))
            break
    return n
