"""跨物件搜尋。

主動超越 phpIPAM 搜尋的設計：
- 查詢自動偵測類型（CIDR / IP / MAC / hostname / 自由文字）
- pg_trgm 模糊相似度 + 排序
- 跨 Section / Subnet / IPAddress / Device / VLAN
- 分類回傳，每筆帶 score 0..1（trigram 為主，精確命中固定 1.0）
- 透過 RBAC `filter_visible` 過濾無權看到的物件

OWASP A05：所有輸入只在 SQL 中以參數化使用；不字串拼接。
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import String, cast, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import IPAddress
from app.models.subnet import Subnet
from app.models.user import User
from app.models.vlan import VLAN
from app.services.permission import filter_visible

ResultType = Literal[
    "section", "subnet", "ip_address", "device", "vlan", "vm", "vm_record",
    "vpn", "customer", "rack", "location",
    "nat", "dns_record", "firewall", "ip_request",
]

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:\-]){2,5}[0-9A-Fa-f]{2}$|^[0-9A-Fa-f]{6,12}$")
# MAC 片段（首碼）：含分隔符的 hex 片段，如 "bc:24" / "bc:24:11" / "bc-24"。
# 至少一組分隔，每段 1~2 個 hex；用來讓使用者打部分 MAC 也能搜到。
_MAC_FRAGMENT_RE = re.compile(r"^[0-9A-Fa-f]{2}([:\-][0-9A-Fa-f]{1,2}){1,5}$")


@dataclass
class SearchHit:
    type: ResultType
    id: str
    label: str
    sublabel: str | None
    score: float
    # 真的包含查詢字（不是只有 trigram 相似）；「子字串優先」篩選看這個，不從畫面上的文字推斷。
    # 精確類查詢（IP／CIDR／MAC／VLAN 編號／VMID）一律算。不輸出給前端。
    substr: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "type": self.type,
            "id": self.id,
            "label": self.label,
            "sublabel": self.sublabel,
            "score": round(self.score, 3),
        }


def _detect_query_kind(q: str) -> str:
    """heuristic 判斷查詢類型。

    回傳：'cidr' | 'ip' | 'mac' | 'vlan_number' | 'free'
    """
    q = q.strip()
    if not q:
        return "free"
    if "/" in q:
        try:
            ipaddress.ip_network(q, strict=False)
            return "cidr"
        except ValueError:
            pass
    try:
        ipaddress.ip_address(q)
        return "ip"
    except ValueError:
        pass
    if _MAC_RE.match(q) or _MAC_FRAGMENT_RE.match(q):
        return "mac"
    if q.isdigit() and int(q) >= 1:
        # 純數字可能是 VLAN 編號（1–4094）或 Proxmox VMID（任意正整數）→ 兩者都查
        return "number"
    return "free"


# 備註命中的分數帶。刻意壓得比其他命中低一整級：備註是自由文字，「這台備援指向
# 203.0.113.9」不代表使用者在找這台。帶內仍分三級，讓整段就是備註的那筆排前面。
# 說明與擁有者不壓分 —— 那兩欄是在描述這台機器本身，命中的理由跟主機名稱同級。
_NOTE_EXACT = 0.15
_NOTE_PREFIX = 0.10
_NOTE_SUBSTR = 0.05
# 主機名稱含查詢字時的分數下限。主機名稱是這個物件的身分 —— 打「kappa5」的人要的
# 就是那台叫 kappa5 的機器。純看 trigram 會反過來：「kappa5 小組」比「kappa5-web」
# 短，相似度反而高，於是「某某組負責的機器」排到「那台機器」前面。
# 只墊底不封頂，所以更好的命中（精確 IP 1.0、MAC 0.95）仍然在前面。
_HOSTNAME_SUBSTR_FLOOR = 0.85
# 這些欄位可能很長；副標只放命中的那一段前後，整段塞進去會把結果列撐爆。
_SNIPPET_PAD = 24


def _note_score(note: str | None, q: str) -> float:
    n = (note or "").strip().lower()
    ql = q.strip().lower()
    if not n or not ql:
        return _NOTE_SUBSTR
    if n == ql:
        return _NOTE_EXACT
    if n.startswith(ql):
        return _NOTE_PREFIX
    return _NOTE_SUBSTR


def _snippet(value: str | None, q: str) -> str | None:
    """欄位裡命中的那一段（含前後文）。"""
    if not value:
        return None
    text_ = value.strip()
    i = text_.lower().find(q.strip().lower())
    if i < 0:
        return text_[:_SNIPPET_PAD * 2] or None
    start = max(0, i - _SNIPPET_PAD)
    end = min(len(text_), i + len(q) + _SNIPPET_PAD)
    return ("…" if start else "") + text_[start:end] + ("…" if end < len(text_) else "")


async def _search_ip_exact(
    session: AsyncSession, *, user: User, ip: str, limit: int
) -> list[SearchHit]:
    # 帶出所屬子網路：重疊網段下同一個 IP 會有多筆，兩列長得一模一樣的話，
    # 使用者是在猜自己點的是哪一筆 —— 而那些紀錄的存活狀態可能完全不同。
    pairs = list((await session.execute(
        select(IPAddress, Subnet.cidr)
        .join(Subnet, IPAddress.subnet_id == Subnet.id)
        .where(IPAddress.ip == ip, Subnet.archived_at.is_(None))
        .limit(limit)
    )).all())
    rows = [r for r, _ in pairs]
    cidr_by_id = {r.id: str(c) for r, c in pairs}
    if not rows:
        return []
    visible_subnets = set(
        await filter_visible(
            session, user=user, object_type="subnet",
            object_ids=[r.subnet_id for r in rows], required="read",
        )
    )
    return [
        SearchHit(
            type="ip_address",
            id=str(r.id),
            label=str(r.ip).split("/")[0],
            sublabel=" · ".join(x for x in (r.hostname, cidr_by_id.get(r.id)) if x),
            score=1.0,
        )
        for r in rows
        if r.subnet_id in visible_subnets
    ]


async def _search_subnet_cidr(
    session: AsyncSession, *, user: User, cidr: str, limit: int
) -> list[SearchHit]:
    """同時找：cidr 完全相符、cidr 包含 q、q 包含 cidr。"""
    sql = text(
        """
        SELECT id, cidr::text AS cidr, description,
            CASE
                WHEN cidr = CAST(:q AS cidr) THEN 1.0
                WHEN cidr >> CAST(:q AS cidr) THEN 0.85
                WHEN cidr << CAST(:q AS cidr) THEN 0.85
                ELSE 0.7
            END AS score
        FROM subnets
        WHERE (cidr = CAST(:q AS cidr)
           OR cidr >> CAST(:q AS cidr)
           OR cidr << CAST(:q AS cidr))
          AND archived_at IS NULL
        ORDER BY score DESC, masklen(cidr)
        LIMIT :limit
        """
    )
    rows = (await session.execute(sql, {"q": cidr, "limit": limit})).all()
    if not rows:
        return []
    sids = [r.id for r in rows]
    visible = set(
        await filter_visible(
            session, user=user, object_type="subnet", object_ids=sids, required="read"
        )
    )
    return [
        SearchHit(
            type="subnet",
            id=str(r.id),
            label=r.cidr,
            sublabel=r.description,
            score=float(r.score),
        )
        for r in rows
        if r.id in visible
    ]


async def _search_mac(
    session: AsyncSession, *, user: User, mac: str, limit: int
) -> list[SearchHit]:
    cleaned = mac.lower().replace("-", ":")
    rows = list(
        (
            await session.execute(
                select(IPAddress)
                .where(cast(IPAddress.mac, String).ilike(f"%{cleaned}%"),
                       IPAddress.subnet_id.in_(select(Subnet.id).where(Subnet.archived_at.is_(None))))
                .limit(limit)
            )
        ).scalars().all()
    )
    visible = set(
        await filter_visible(
            session, user=user, object_type="subnet",
            object_ids=[r.subnet_id for r in rows], required="read",
        )
    )
    return [
        SearchHit(
            type="ip_address",
            id=str(r.id),
            label=str(r.mac),
            sublabel=f"{str(r.ip).split('/')[0]} {r.hostname or ''}".strip(),
            score=0.95,
        )
        for r in rows
        if r.subnet_id in visible
    ]


async def _search_mac_vm_and_ports(
    session: AsyncSession, *, user: User, mac: str, limit: int
) -> list[SearchHit]:
    """MAC 也比對 VM 網卡與裝置自己的連接埠（2026-11-03：以前只比對 IP 記錄的 MAC，
    VM 沒有 IP 記錄、或 MAC 只記在裝置連接埠上時，搜尋框與 AI 都說找不到）。

    VM：主要 IP 看得到就導到那個 IP（type=vm），否則到虛擬化頁（type=vm_record，search() 最後只留給全域讀取）。
    連接埠：type=device，照裝置可見性。完整 MAC 用去掉分隔符號的比對（連接埠的 MAC 寫法各來源不一）。"""
    from app.models.device import Device
    from app.models.physical import DevicePort
    from app.models.virt import VirtCluster, VirtualMachine, VMInterface
    from app.services.mac_history import _hex_of, normalize_mac
    from app.services.permission import visible_ids

    full = normalize_mac(mac)
    cleaned = mac.lower().replace("-", ":")
    hits: list[SearchHit] = []

    vm_cond = (VMInterface.mac == full) if full else cast(VMInterface.mac, String).ilike(f"%{cleaned}%")
    vm_rows = (await session.execute(
        select(VirtualMachine.id, VirtualMachine.name, VirtualMachine.kind, VirtualMachine.primary_ip_id,
               VirtCluster.type, VirtCluster.name, VMInterface.mac)
        .join(VirtualMachine, VirtualMachine.id == VMInterface.vm_id)
        .join(VirtCluster, VirtCluster.id == VirtualMachine.cluster_id)
        .where(vm_cond).order_by(VirtualMachine.name).limit(limit))).all()
    ip_ids = [r[3] for r in vm_rows if r[3]]
    ips = {ip.id: ip for ip in (await session.execute(
        select(IPAddress).where(IPAddress.id.in_(ip_ids))  # bounded: one page of search hits
    )).scalars().all()} if ip_ids else {}
    vis = set(await filter_visible(session, user=user, object_type="subnet",
                                   object_ids=[ip.subnet_id for ip in ips.values()], required="read"))
    for vid, name, kind, pip, platform, cluster, imac in vm_rows:
        kindlabel = "CT" if kind == "ct" else "VM"
        ip = ips.get(pip) if pip else None
        if ip is not None and ip.subnet_id in vis:
            hits.append(SearchHit(type="vm", id=str(ip.id), label=name,
                                  sublabel=f"{kindlabel} · {cluster} · {imac}", score=0.95))
        else:
            hits.append(SearchHit(type="vm_record", id=f"{platform}.{vid}", label=name,
                                  sublabel=f"{kindlabel} · {cluster} · {imac}", score=0.95))

    port_cond = (_hex_of(DevicePort.mac_address) == full.replace(":", "")) if full \
        else DevicePort.mac_address.ilike(f"%{cleaned}%")
    port_rows = (await session.execute(
        select(Device.id, Device.name, DevicePort.name, DevicePort.mac_address)
        .join(Device, Device.id == DevicePort.device_id)
        .where(port_cond).order_by(Device.name, DevicePort.name).limit(limit))).all()
    vis_dev = await visible_ids(session, user=user, object_type="device") if port_rows else None
    for did, dname, pname, pmac in port_rows:
        if vis_dev is not None and did not in vis_dev:
            continue
        hits.append(SearchHit(type="device", id=str(did), label=dname,
                              sublabel=f"{pname} · {pmac}", score=0.95))
    return hits


async def _search_vlan_number(
    session: AsyncSession, *, _user: User, number: int, limit: int
) -> list[SearchHit]:
    rows = list(
        (
            await session.execute(
                select(VLAN).where(VLAN.number == number).limit(limit)
            )
        ).scalars().all()
    )
    return [
        SearchHit(
            type="vlan",
            id=str(r.id),
            label=f"VLAN {r.number} — {r.name}",
            sublabel=r.description,
            score=1.0,
        )
        for r in rows
    ]


async def _search_vmid(
    session: AsyncSession, *, user: User, vmid: int, limit: int
) -> list[SearchHit]:
    """以 Proxmox VMID 找 VM/CT，回傳其主 IP（導到 IP 詳細資料，可開 PVE 主控台）。"""
    from app.models.address import IPAddress
    from app.models.virt import VirtualMachine
    rows = list((await session.execute(
        select(VirtualMachine).where(VirtualMachine.legacy_vmid == vmid).limit(limit)
    )).scalars().all())
    if not rows:
        return []
    ip_ids = [vm.primary_ip_id for vm in rows if vm.primary_ip_id]
    if not ip_ids:
        return []
    ips = {ip.id: ip for ip in (await session.execute(
        select(IPAddress).where(IPAddress.id.in_(ip_ids))  # bounded: one page of search hits
    )).scalars().all()}
    visible = set(await filter_visible(
        session, user=user, object_type="subnet",
        object_ids=[ip.subnet_id for ip in ips.values()], required="read",
    ))
    hits: list[SearchHit] = []
    for vm in rows:
        ip = ips.get(vm.primary_ip_id) if vm.primary_ip_id else None
        if ip is None or ip.subnet_id not in visible:
            continue
        kindlabel = "CT" if vm.kind == "ct" else "VM"
        # type=vm → 前端歸「虛擬化」群組、以 VM 名稱為主標（點擊導到該 IP 詳細資料，可開主控台）
        hits.append(SearchHit(
            type="vm", id=str(ip.id), label=vm.name,
            sublabel=f"{kindlabel} · VMID {vmid} · {ip.ip}", score=0.99,
        ))
    return hits


def _ilike_any(cols: list[str]) -> str:
    """欄位裡有沒有查詢字（子字串）。cols 只會是本檔寫死的欄位運算式（允許清單），不是使用者輸入。

    包 COALESCE：欄位是 NULL 時 ILIKE 得 NULL，而 PostgreSQL 降冪排序把 NULL 排在最前面 ——
    「不相干、某欄是空的」那幾筆會排到真正命中的前面，又把它擠出 LIMIT（寫測試時抓到）。"""
    return "COALESCE((" + " OR ".join(f"{c} ILIKE :qlike" for c in cols) + "), FALSE)" if cols else "FALSE"


def _sim_max(cols: list[str]) -> str:
    return "GREATEST(" + ", ".join(f"COALESCE(similarity({c}, :q), 0)" for c in cols) + ")" if cols else "0"


def _pick(fields: set[str], mapping: dict[str, str]) -> list[str]:
    """設定裡開著的欄位 → SQL 欄位運算式（照 mapping 的順序）。"""
    return [col for key, col in mapping.items() if key in fields]


async def _simple_block(
    session: AsyncSession, *, q: str, qlike: str, fuzzy: bool, limit: int,
    table: str, fields: set[str], mapping: dict[str, str], extra_cols: str = "",
    fuzzy_col: str | None = "name", fuzzy_min: float = 0.3, where_extra: str = "",
) -> list[Any]:
    """只比對名稱一類欄位的區塊：先排真的包含查詢字的（sub），再排相似度。
    以前只依相似度排，長名稱相似度低，被不含查詢字的短名稱擠出 LIMIT（issue #55）。"""
    cols = _pick(fields, mapping)
    if not cols:
        return []
    fuzzy_sql = (f"OR (:fuzzy AND similarity({fuzzy_col}, :q) > {fuzzy_min})"
                 if fuzzy_col and fuzzy_col in cols else "")
    # 表名與欄位都是本檔寫死的允許清單（呼叫端傳常數、欄位經 _pick 從 mapping 取），查詢字一律走參數
    sql_str = f"""
        SELECT id{extra_cols}, {_sim_max(cols)} AS score, {_ilike_any(cols)} AS sub
          FROM {table}
         WHERE ({_ilike_any(cols)} {fuzzy_sql}) {where_extra}
         ORDER BY sub DESC, score DESC
         LIMIT :limit
        """  # noqa: S608
    sql = text(sql_str)
    return list((await session.execute(sql, {"q": q, "qlike": qlike, "fuzzy": fuzzy, "limit": limit})).all())


def _why(q: str, label: str | None, values: list[tuple[str | None, bool]]) -> list[str]:
    """命中的字不在標題裡時，副標指出是哪一欄命中的（含前後文）。
    否則畫面上只是一個跟查詢字看不出關係的名稱；以前設備用說明／序號命中時就是這樣（issue #55）。"""
    ql = q.strip().lower()
    if label and ql in label.lower():
        return []
    return [s for v, on in values if on and v and ql in v.lower() and (s := _snippet(v, q))]


async def _search_text_trgm(
    session: AsyncSession, *, user: User, q: str, limit: int,
    fields: dict[str, set[str]] | None = None,
) -> list[SearchHit]:
    """跨多表搜尋（子字串＋trigram 模糊）。fields：開著的類型 → 開著的欄位（全站搜尋的設定）。"""
    from app.services.search_settings import enabled_fields
    F = fields if fields is not None else enabled_fields(None)

    def on(t: str, f: str) -> bool:
        return f in F.get(t, set())

    qlike = f"%{q}%"
    # 看起來像「IP 片段」：只含數字與點且至少有一個點
    #   e.g. "192.168"（首碼）/ ".1.189"（結尾）/ "1.189"（中段）/ "10.1."
    # 不用正規表示式：`[0-9.]*\.[0-9.]*` 在一長串數字上會回溯到平方時間（CodeQL 標出）
    looks_like_ip_fragment = ("." in q and set(q) <= set("0123456789.")
                              and any(ch.isdigit() for ch in q))
    ipfrag = f"%{q}%"  # IP 片段一律走子字串比對（首碼/結尾/中段都能撞到）
    # IP 片段 / IP / CIDR / MAC 查詢時不要用 trigram 模糊比對（否則 .200 會誤中 .201/.208）
    fuzzy = not (looks_like_ip_fragment or _detect_query_kind(q) in ("ip", "cidr", "mac"))
    out: list[SearchHit] = []
    params = {"q": q, "qlike": qlike, "fuzzy": fuzzy, "limit": limit}

    # IP 片段比對（子字串）：打 "192.168" 找 192.168.*；打 ".1.189" 找 *.1.189
    if looks_like_ip_fragment:
        if on("subnet", "cidr"):
            sub_pref_rows = (await session.execute(text(
                """
                SELECT id, cidr::text AS cidr, description
                  FROM subnets
                 WHERE (host(cidr) LIKE :ipfrag OR cidr::text LIKE :ipfrag) AND archived_at IS NULL
                 ORDER BY masklen(cidr)
                 LIMIT :limit
                """
            ), {"ipfrag": ipfrag, "limit": limit})).all()
            if sub_pref_rows:
                visible = set(await filter_visible(
                    session, user=user, object_type="subnet",
                    object_ids=[r.id for r in sub_pref_rows], required="read"))
                out += [SearchHit(type="subnet", id=str(r.id), label=r.cidr, sublabel=r.description, score=0.9)
                        for r in sub_pref_rows if r.id in visible]
        if on("ip_address", "ip"):
            ip_pref_rows = (await session.execute(text(
                """
                SELECT a.id, host(a.ip) AS ip, a.hostname, a.subnet_id
                  FROM ip_addresses a
                 WHERE host(a.ip) LIKE :ipfrag
                   AND a.subnet_id IN (SELECT id FROM subnets WHERE archived_at IS NULL)
                 ORDER BY a.ip
                 LIMIT :limit
                """
            ), {"ipfrag": ipfrag, "limit": limit})).all()
            if ip_pref_rows:
                visible_ip_subs = set(await filter_visible(
                    session, user=user, object_type="subnet",
                    object_ids=[r.subnet_id for r in ip_pref_rows], required="read"))
                out += [SearchHit(type="ip_address", id=str(r.id), label=r.ip, sublabel=r.hostname, score=0.92)
                        for r in ip_pref_rows if r.subnet_id in visible_ip_subs]

    # Sections
    sec_rows = await _simple_block(
        session, q=q, qlike=qlike, fuzzy=fuzzy, limit=limit, table="sections",
        fields=F.get("section", set()), mapping={"name": "name", "description": "description"},
        extra_cols=", name, description", fuzzy_min=0.2)
    visible_sec = set(await filter_visible(
        session, user=user, object_type="section", object_ids=[r.id for r in sec_rows], required="read"))
    out += [SearchHit(type="section", id=str(r.id), label=r.name, sublabel=r.description,
                      score=min(float(r.score or 0), 1.0), substr=bool(r.sub))
            for r in sec_rows if r.id in visible_sec]

    # Subnets（說明；CIDR 由 cidr／ip 類查詢與上面的 IP 片段處理）
    sub_rows = await _simple_block(
        session, q=q, qlike=qlike, fuzzy=fuzzy, limit=limit, table="subnets",
        fields=F.get("subnet", set()), mapping={"description": "description"},
        extra_cols=", cidr::text AS cidr, description", fuzzy_col="description", fuzzy_min=0.2,
        where_extra="AND archived_at IS NULL")
    visible_sub = set(await filter_visible(
        session, user=user, object_type="subnet", object_ids=[r.id for r in sub_rows], required="read"))
    out += [SearchHit(type="subnet", id=str(r.id), label=r.cidr, sublabel=r.description,
                      score=min(float(r.score or 0), 1.0), substr=bool(r.sub))
            for r in sub_rows if r.id in visible_sub]

    # IP addresses（主機名稱／說明／擁有者／備註）
    #
    # 排序：主機名稱／說明／擁有者真的包含查詢字的（rank 0）→ 只有備註包含的（rank 1）→ 只是相似的（rank 2）。
    # 以前先看 strong，而 strong 把「相似度 > 0.2」也算進去：主機名稱越長相似度越低，svc11-frontend-docker
    # 輸給不含查詢字的 svc-bi-d，每類 8 筆的 LIMIT 先被模糊命中佔掉（issue #55）。
    # 備註是使用者自己寫的自由文字，命中的理由最弱 —— 只做子字串比對，分數壓在 _NOTE_* 那一段。
    strong_cols = _pick(F.get("ip_address", set()),
                        {"hostname": "a.hostname", "description": "a.description", "owner": "a.owner"})
    note_on = on("ip_address", "note")
    host_on = on("ip_address", "hostname")
    if strong_cols or note_on:
        fuzzy_sql = "OR (:fuzzy AND similarity(a.hostname, :q) > 0.2)" if host_on else ""
        note_sql = "a.note ILIKE :qlike" if note_on else "FALSE"
        # 欄位是本檔寫死的允許清單（經 _pick 從 mapping 取），查詢字一律走參數
        ip_sql_str = f"""
            SELECT a.id, host(a.ip) AS ip, a.hostname, a.subnet_id, a.description, a.owner, a.note,
                   {_sim_max(strong_cols)} AS score,
                   CASE WHEN {_ilike_any(strong_cols)} THEN 0 WHEN {note_sql} THEN 1 ELSE 2 END AS rank
              FROM ip_addresses a
             WHERE ({_ilike_any(strong_cols)} OR {note_sql} {fuzzy_sql})
               AND a.subnet_id IN (SELECT id FROM subnets WHERE archived_at IS NULL)
             ORDER BY rank, score DESC
             LIMIT :limit
            """  # noqa: S608
        ip_sql = text(ip_sql_str)
        ip_rows = (await session.execute(ip_sql, params)).all()
        visible_ip_subnets = set(await filter_visible(
            session, user=user, object_type="subnet", object_ids=[r.subnet_id for r in ip_rows], required="read"))
        qlow = q.strip().lower()
        for r in ip_rows:
            if r.subnet_id not in visible_ip_subnets:
                continue
            hostname_hit = bool(host_on and r.hostname and qlow in r.hostname.lower())
            # 命中的字不在 label 裡時，要指出是哪一欄命中的 —— 否則畫面上只是一個跟查詢字看不出關係的 IP
            parts: list[str | None] = [r.ip if r.hostname else None]
            if not (hostname_hit or qlow in r.ip):
                parts += _why(q, None, [(r.description, on("ip_address", "description")),
                                        (r.owner, on("ip_address", "owner")), (r.note, note_on)])
            if r.rank == 1:
                score = _note_score(r.note, q)
            else:
                score = min(float(r.score or 0), 1.0)
                if hostname_hit:
                    score = max(score, _HOSTNAME_SUBSTR_FLOOR)
            out.append(SearchHit(type="ip_address", id=str(r.id), label=r.hostname or r.ip,
                                 sublabel=" · ".join(x for x in parts if x) or None,
                                 score=score, substr=r.rank < 2))

    # Devices（名稱／序號／說明）—— 序號、說明命中時副標寫出命中的那一段
    dev_f = F.get("device", set())
    for r in await _simple_block(
            session, q=q, qlike=qlike, fuzzy=fuzzy, limit=limit, table="devices", fields=dev_f,
            mapping={"name": "name", "serial": "serial", "description": "description"},
            extra_cols=", name, vendor, model, serial, description"):
        why = _why(q, r.name, [(r.serial, "serial" in dev_f), (r.description, "description" in dev_f)])
        out.append(SearchHit(type="device", id=str(r.id), label=r.name,
                             sublabel=" · ".join(x for x in [" ".join(filter(None, [r.vendor, r.model])), *why] if x)
                             or None, score=min(float(r.score or 0), 1.0), substr=bool(r.sub)))

    # 虛擬機（名稱）—— 以前只有查詢是純數字時比對 VMID，沒有 IP 記錄的 VM 用名字搜不到（issue #55）
    if on("vm", "name"):
        vm_rows = (await session.execute(text(
            """
            SELECT v.id, v.name, v.kind, v.legacy_vmid, v.primary_ip_id, c.type AS platform, c.name AS cluster,
                   similarity(v.name, :q) AS score, (v.name ILIKE :qlike) AS sub
              FROM virtual_machines v JOIN virt_clusters c ON c.id = v.cluster_id
             WHERE v.name ILIKE :qlike OR (:fuzzy AND similarity(v.name, :q) > 0.3)
             ORDER BY sub DESC, score DESC
             LIMIT :limit
            """
        ), params)).all()
        ip_ids = [r.primary_ip_id for r in vm_rows if r.primary_ip_id]
        ips = {ip.id: ip for ip in (await session.execute(
            select(IPAddress).where(IPAddress.id.in_(ip_ids))  # bounded: one page of search hits
        )).scalars().all()} if ip_ids else {}
        vis = set(await filter_visible(session, user=user, object_type="subnet",
                                       object_ids=[ip.subnet_id for ip in ips.values()], required="read"))
        for r in vm_rows:
            kindlabel = "CT" if r.kind == "ct" else "VM"
            ip = ips.get(r.primary_ip_id) if r.primary_ip_id else None
            score = min(float(r.score or 0), 1.0)
            if ip is not None and ip.subnet_id in vis:
                # 有看得到的 IP 記錄：導到那個 IP（可開主控台），跟 VMID 搜尋一樣
                out.append(SearchHit(type="vm", id=str(ip.id), label=r.name,
                                     sublabel=f"{kindlabel} · {r.cluster} · {str(ip.ip).split('/')[0]}",
                                     score=score, substr=bool(r.sub)))
            else:
                # 沒有（或看不到）IP 記錄：導到虛擬化頁（要全域讀取權限，search() 最後會濾）
                out.append(SearchHit(type="vm_record", id=f"{r.platform}.{r.id}", label=r.name,
                                     sublabel=f"{kindlabel} · {r.cluster}", score=score, substr=bool(r.sub)))

    # VPN tunnels（名稱 / 對端）
    vpn_f = F.get("vpn", set())
    for r in await _simple_block(
            session, q=q, qlike=qlike, fuzzy=fuzzy, limit=limit, table="vpn_tunnels", fields=vpn_f,
            mapping={"name": "name", "peer": "b_endpoint"}, extra_cols=", name, type, status, b_endpoint"):
        why = _why(q, r.name, [(r.b_endpoint, "peer" in vpn_f)])
        out.append(SearchHit(type="vpn", id=str(r.id), label=r.name,
                             sublabel=" · ".join(x for x in [" ".join(filter(None, [r.type, r.status])), *why] if x)
                             or None, score=min(float(r.score or 0), 1.0), substr=bool(r.sub)))

    # Customers（單位）
    for r in await _simple_block(
            session, q=q, qlike=qlike, fuzzy=fuzzy, limit=limit, table="customers",
            fields=F.get("customer", set()), mapping={"name": "name", "title": "title"},
            extra_cols=", name, title"):
        out.append(SearchHit(type="customer", id=str(r.id), label=r.name, sublabel=r.title or None,
                             score=min(float(r.score or 0), 1.0), substr=bool(r.sub)))

    # 名稱一欄的類型：機櫃、地點、NAT 規則、防火牆
    for rtype, table, extra in (("rack", "racks", ", name"), ("location", "locations", ", name"),
                                ("nat", "nat_translations", ", name, type"),
                                ("firewall", "opnsense_firewalls", ", name")):
        for r in await _simple_block(
                session, q=q, qlike=qlike, fuzzy=fuzzy, limit=limit, table=table,
                fields=F.get(rtype, set()), mapping={"name": "name"}, extra_cols=extra):
            out.append(SearchHit(type=rtype, id=str(r.id), label=r.name,  # type: ignore[arg-type]
                                 sublabel=getattr(r, "type", None) if rtype == "nat" else None,
                                 score=min(float(r.score or 0), 1.0), substr=bool(r.sub)))

    # DNS 紀錄（名稱 / 值）
    for r in await _simple_block(
            session, q=q, qlike=qlike, fuzzy=fuzzy, limit=limit, table="dns_records",
            fields=F.get("dns_record", set()), mapping={"name": "name", "value": "value"},
            extra_cols=", name, type, value"):
        out.append(SearchHit(type="dns_record", id=str(r.id), label=r.name,
                             sublabel=" ".join(filter(None, [r.type, r.value])) or None,
                             score=min(float(r.score or 0), 1.0), substr=bool(r.sub)))

    # IP 申請（requested_ip / hostname）
    for r in await _simple_block(
            session, q=q, qlike=qlike, fuzzy=fuzzy, limit=limit, table="ip_requests",
            fields=F.get("ip_request", set()), mapping={"ip": "host(requested_ip)", "hostname": "hostname"},
            extra_cols=", requested_ip, hostname, status", fuzzy_col="hostname"):
        lbl = str(r.requested_ip).split("/")[0] if r.requested_ip else (r.hostname or "(request)")
        out.append(SearchHit(type="ip_request", id=str(r.id), label=lbl,
                             sublabel=" ".join(filter(None, [r.hostname, r.status])) or None,
                             score=min(float(r.score or 0), 1.0), substr=bool(r.sub)))

    # VLAN（名稱；數字已於 number 類查詢處理）
    for r in await _simple_block(
            session, q=q, qlike=qlike, fuzzy=fuzzy, limit=limit, table="vlans",
            fields=F.get("vlan", set()), mapping={"name": "name"}, extra_cols=", number, name"):
        out.append(SearchHit(type="vlan", id=str(r.id), label=f"VLAN {r.number} — {r.name}",
                             sublabel=None, score=min(float(r.score or 0), 1.0), substr=bool(r.sub)))

    # RBAC：過濾掉不可見的 device/customer/rack/location 結果（subnet/section/ip 已於各區塊內過濾）
    from app.services.permission import visible_ids
    for otype in ("device", "customer", "rack", "location"):
        vis_ids = await visible_ids(session, user=user, object_type=otype)
        if vis_ids is not None:
            allow = {str(x) for x in vis_ids}
            out = [h for h in out if h.type != otype or h.id in allow]

    # 子字串優先：只要有任一「真的包含查詢字」的命中，就濾掉所有純 trigram 相似的命中
    # （查 "db2" 不該冒出 db3 / db4 / DB4）。完全沒有子字串命中時（例如打錯字）才保留模糊結果。
    # 看的是 SQL 算出來的旗標，不是畫面上的文字：以前從標題／副標推斷，設備用說明／序號命中時那兩欄
    # 不在畫面上，就被當成模糊命中整批濾掉（issue #55）。
    if any(h.substr for h in out):
        out = [h for h in out if h.substr]

    out.sort(key=lambda h: h.score, reverse=True)
    return out


async def search(
    session: AsyncSession,
    *,
    user: User,
    q: str,
    limit_per_type: int = 8,
    settings: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, object]]]:
    """主搜尋入口；依偵測到的查詢類型走最相關的子搜尋，並補上 trigram 結果。

    settings：全站搜尋的設定（搜哪些類型、比對哪些欄位、排列順序）。None＝全部（AI 工具等呼叫端）。"""
    from app.services.search_settings import enabled_fields
    from app.services.search_settings import order as search_order
    F = enabled_fields(settings)

    def on(t: str, f: str) -> bool:
        return f in F.get(t, set())

    q = q.strip()
    if len(q) < 2:
        return {"detected": "empty", "results": [], "order": search_order(settings)}  # type: ignore[dict-item]

    kind = _detect_query_kind(q)
    hits: list[SearchHit] = []

    if kind == "cidr":
        if on("subnet", "cidr"):
            hits.extend(await _search_subnet_cidr(session, user=user, cidr=q, limit=limit_per_type))
    elif kind == "ip":
        if on("ip_address", "ip"):
            hits.extend(await _search_ip_exact(session, user=user, ip=q, limit=limit_per_type))
    if kind == "ip" and on("subnet", "cidr"):
        # 也找包含此 IP 的 subnet
        sql = text(
            """
            SELECT id, cidr::text AS cidr, description
              FROM subnets
             WHERE cidr >> CAST(:q AS inet)
             ORDER BY masklen(cidr) DESC
             LIMIT :limit
            """
        )
        rows = (await session.execute(sql, {"q": q, "limit": limit_per_type})).all()
        sids = [r.id for r in rows]
        visible = set(
            await filter_visible(
                session, user=user, object_type="subnet",
                object_ids=sids, required="read",
            )
        )
        for r in rows:
            if r.id in visible:
                hits.append(SearchHit(
                    type="subnet", id=str(r.id), label=r.cidr,
                    sublabel=r.description, score=0.9,
                ))
    if kind == "mac":
        if on("ip_address", "mac"):
            hits.extend(await _search_mac(session, user=user, mac=q, limit=limit_per_type))
        # VM 網卡、裝置連接埠（類型關掉的，下面一起濾掉）
        hits.extend(await _search_mac_vm_and_ports(session, user=user, mac=q, limit=limit_per_type))
    elif kind == "number":
        n = int(q)
        if 1 <= n <= 4094 and on("vlan", "number"):
            hits.extend(await _search_vlan_number(session, _user=user, number=n, limit=limit_per_type))
        if on("vm", "vmid"):
            hits.extend(await _search_vmid(session, user=user, vmid=n, limit=limit_per_type))

    # 一律補上 trigram 模糊搜尋（讓使用者打 IP 也能撞到 hostname 等）
    hits.extend(await _search_text_trgm(session, user=user, q=q, limit=limit_per_type, fields=F))
    # 關掉的類型一律不出現（沒有 IP 記錄的 VM 歸在「虛擬機」底下）
    hits = [h for h in hits if (("vm" if h.type == "vm_record" else h.type) in F)]

    # 去重（同 (type, id) 取較高分）
    best: dict[tuple[str, str], SearchHit] = {}
    for h in hits:
        key = (h.type, h.id)
        cur = best.get(key)
        if cur is None or h.score > cur.score:
            best[key] = h

    final = sorted(best.values(), key=lambda h: h.score, reverse=True)

    # RBAC：全域基礎設施類結果（VLAN / NAT / 防火牆 / 站對站 VPN / DNS / IP 申請）
    # 只有管理員或具萬用讀取權限者可見；只被指派特定物件的帳號不得從搜尋窺見。
    from app.services.permission import visible_ids as _vis
    is_global = user.is_admin
    if not is_global:
        for ot in ("subnet", "device", "customer", "section", "rack", "location"):
            if await _vis(session, user=user, object_type=ot) is None:
                is_global = True
                break
    if not is_global:
        # 沒有 IP 記錄的 VM 要到虛擬化頁才看得到，而虛擬化頁要全域讀取權限
        _global_types = {"vlan", "nat", "firewall", "vpn", "dns_record", "ip_request", "vm_record"}
        final = [h for h in final if h.type not in _global_types]

    return {
        "detected": kind,  # type: ignore[dict-item]
        "results": [h.as_dict() for h in final],
        # 結果的排列順序（全站搜尋的設定）；前端照這個分組排列
        "order": search_order(settings),  # type: ignore[dict-item]
    }
