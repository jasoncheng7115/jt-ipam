"""AI 巡檢：定期讓 LLM 檢視 IPAM 資料，找出可疑、不合理或有資安疑慮的地方。

三個不可妥協的原則：

1. **餵給 LLM 之前先過 RBAC。** 巡檢以「發起者的可見範圍」取樣，不是整庫倒給模型。
   排程執行時用的是設定裡指定的管理員身分。AI 是繞過權限最容易被忽略的一條路
   （相關：MCP 曾經漏掉 `get_topology` 的過濾）。
2. **每一筆發現都要帶 `evidence`。** LLM 會用非常肯定的語氣講錯話；沒有依據資料，
   使用者無從判斷，那些話就會被當成事實。UI 也必須標明來源是 AI 推測。
3. **模型的輸出一律當成不可信輸入。** 嚴重度、分類都對照允許清單，超出的一律降級；
   長度截斷；解析失敗就整批捨棄而不是塞半截資料進資料庫。

刻意**不**做的事：不讓 LLM 決定任何異動。它只產生「發現」，處置由人決定。
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.address import IPAddress
from app.models.ai_finding import AIFinding
from app.models.device import Device
from app.models.physical import DevicePort
from app.models.subnet import Subnet
from app.models.user import User
from app.services.arp_precedence import normalize_mac
from app.services.permission import visible_ids

# 排程判斷住在 `services/schedule.py`（巡檢與異常偵測共用一份）。這裡再匯出，
# 既有的 `from app.services.ai_audit import due` 與測試不必跟著改。
from app.services.schedule import due  # noqa: F401
from app.services.system_config import (
    get_ai_audit_last_run,
    get_ai_audit_running_since,
    get_llm_config,
    mark_ai_audit_finished,
    mark_ai_audit_started,
    record_ai_audit_outcome,
    remember_ai_audit_run_scope,
)

SEVERITIES = ("low", "medium", "high")
CATEGORIES = (
    "exposure",        # 對外暴露 / 不該公開的服務
    "stale",           # 長期未使用、疑似遺留
    "conflict",        # 衝突、重複、矛盾的登記
    "naming",          # 命名與實際用途不符
    "coverage",        # 監測涵蓋不足（沒有存活來源等）
    "policy",          # 與慣例或政策不符
    "other",
)
MAX_SAMPLE = 400          # 送進提示詞的資料筆數上限
MAX_FINDINGS = 40         # 單次採納的發現數上限
# 每批的逾時（互動對話那個預設 90 秒，對巡檢的批次來說太短）。一批卡住就讓它失敗、換下一批 —— 把逾時設得很長只是讓整次巡檢跟著卡死，
# 而且卡住的原因通常是這批太大，等更久也不會變好。
AUDIT_TIMEOUT = 300.0
RESERVED_TOKENS = 2500    # 留給指示與模型回覆的空間（其餘才是每批可放的資料）
MIN_BATCH_TOKENS = 800    # 再怎麼小的上下文也要放得下幾筆資料，否則會切成無限多批
# 就算上下文放得下，一批也不放超過這麼多筆。實測（gemma4:26b）：一批塞滿 13k token
# 要跑超過 15 分鐘，中途完全沒有進度可言；切小之後每批以分鐘計，進度也才看得出來。
MAX_IPS_PER_BATCH = 60
# 單批產出上限：依實際的上下文長度自動計算（ai.output_budget，2026-10-21 起不再寫死 6000）。
# 會思考的模型思考過程也算在額度裡；被切斷時解析會撿回已經寫完的那幾筆（見 _salvage_findings）。
# 切批時要先預留回覆的空間（OUTPUT_SHARE），否則資料把上下文塞滿、回覆只剩一點點。
OUTPUT_SHARE = 0.35


# 同時只允許一次巡檢。LLM 通常只有一張卡，兩次同時跑不是變快，是互相拖死
# ——（實測：第二個請求連 header 都要等兩分鐘以上才回）。
_RUNNING = asyncio.Lock()
#: 跨行程的「正在跑」標記多久之後視為遺留（行程被砍、主機當機不會清掉它）。
#: 要比一次巡檢最長可能跑多久還長：最多 ceil(MAX_SAMPLE / MAX_IPS_PER_BATCH) 批 × 每批 AUDIT_TIMEOUT
RUN_STALE_AFTER = timedelta(hours=1)


class AuditBusy(RuntimeError):
    """已經有一次巡檢在跑。"""


log = structlog.get_logger("ai_audit")


def is_audit_running() -> bool:
    """目前是否有巡檢在跑（給端點先擋掉重複觸發，而不是讓它跑到一半才失敗）。"""
    return _RUNNING.locked()


@dataclass
class AuditRun:
    run_id: uuid.UUID
    findings: int
    skipped: int
    error: str | None = None
    # 確定性的結果（戰情室與狀態記錄用；error 是給人看的原文，可能夾著模型回應，不往外送）：
    # completed＝每批都分析完／partial＝有批次失敗但有結果／failed＝沒有可用的結果
    status: str = "completed"
    error_code: str | None = None
    batches: int = 0


async def _collect(session: AsyncSession, user: User) -> dict[str, Any]:
    """取一份**已依可見範圍過濾**的快照。

    只取結構性欄位（位址、狀態、主機名稱、來源、最後出現時間…），不含任何機密。
    """
    vis_sub = await visible_ids(session, user=user, object_type="subnet", required="read")
    vis_ip = await visible_ids(session, user=user, object_type="ip", required="read")

    # 只巡檢有勾「納入 AI 巡檢」的子網路。這是在 RBAC **之後**再收窄，不是繞過它 ——
    # 勾了但看不到的子網路，仍然看不到。
    sub_q = select(Subnet.id, Subnet.cidr, Subnet.description, Subnet.scan_enabled).where(
        Subnet.ai_audit_enabled.is_(True))
    if vis_sub is not None:
        if not vis_sub:
            return {"subnets": [], "ips": [], "devices": [], "empty": True}
        sub_q = sub_q.where(in_values(Subnet.id, vis_sub))
    sub_rows = (await session.execute(sub_q.limit(MAX_SAMPLE))).all()

    # 每個網段的掃描涵蓋：有沒有在掃、掃到過幾筆。
    # **少了這個，模型分不出「沒在監控」與「在掃但這些位址從來沒回應」** —— 實機上
    # 就把一個有開掃描、233 筆中 130 筆掃到過的網段，報成「可能存在監控盲點」並建議
    # 去檢查監控涵蓋。同一份資料，兩種完全不同的處置。
    cover: dict[Any, tuple[int, int]] = {}
    if sub_rows:
        for sid, seen, total in (await session.execute(
            select(IPAddress.subnet_id,
                   func.count().filter(IPAddress.last_seen_scanner.isnot(None)),
                   func.count())
            .where(in_values(IPAddress.subnet_id, [r[0] for r in sub_rows]))
            .group_by(IPAddress.subnet_id)
        )).all():
            cover[sid] = (int(seen or 0), int(total or 0))

    # 子網路的 id 內部還要用來過濾 IP，送給模型的是 CIDR、說明與掃描涵蓋
    subnets = []
    for sid, c, d, scan_on in sub_rows:
        seen, total = cover.get(sid, (0, 0))
        subnets.append({"cidr": str(c), "description": d,
                        "scan_enabled": bool(scan_on),
                        "ips_seen": seen, "ips_total": total})

    ip_q = select(
        IPAddress.id, IPAddress.ip, IPAddress.hostname, IPAddress.state,
        IPAddress.effective_status, IPAddress.discovery_source, IPAddress.is_dhcp_server,
        IPAddress.last_seen_scanner, IPAddress.last_seen_librenms, IPAddress.description,
        IPAddress.device_id, IPAddress.mac,
    )
    if vis_ip is not None:
        if not vis_ip:
            ip_q = ip_q.where(IPAddress.id.is_(None))
        else:
            ip_q = ip_q.where(in_values(IPAddress.id, vis_ip))
    # IP 也跟著子網路的範圍走 —— 否則勾掉的網段照樣被整段送給模型
    ip_q = ip_q.where(in_values(IPAddress.subnet_id, [r[0] for r in sub_rows])
                      if sub_rows else IPAddress.id.is_(None))
    ip_rows = (await session.execute(ip_q.limit(MAX_SAMPLE))).all()

    vis_dev = await visible_ids(session, user=user, object_type="device", required="read")
    dev_q = select(Device.id, Device.name, Device.type)
    if vis_dev is not None:
        dev_q = dev_q.where(in_values(Device.id, vis_dev)) if vis_dev else dev_q.where(Device.id.is_(None))
    dev_rows = (await session.execute(dev_q.limit(MAX_SAMPLE))).all()
    # 只給名稱與類型，不給 UUID —— 給了模型就會把 UUID 寫進發現裡，
    # 而人看著一串 UUID 完全不知道那是哪台機器
    devices = [{"name": n, "type": t} for _i, n, t in dev_rows]

    # ── 每筆 IP 屬於哪台機器。**沒有這個欄位，模型就只看得到「兩個 IP 剛好同名」**，
    # 於是把一台雙網卡機器報成「重複的 IP 紀錄」（實機誤報）。
    # 兩條線索都要用：device_id 直接指定，以及 MAC 對到某台裝置的連接埠 ——
    # 第二張網卡的 IP 往往沒有 device_id，但它的 MAC 就在該裝置的 eth1 上。
    # 名稱只在該裝置屬於這個帳號可見範圍時才填，否則等於繞過 RBAC 洩漏裝置名稱。
    dev_name = {i: n for i, n, _t in dev_rows}
    port_rows = (await session.execute(
        select(DevicePort.device_id, DevicePort.mac_address)
        .where(DevicePort.mac_address.isnot(None))
    )).all()
    by_port_mac = {normalize_mac(m): d for d, m in port_rows if normalize_mac(m)}

    ips = []
    for row in ip_rows:
        owner = row[10] or by_port_mac.get(normalize_mac(row[11]))
        ips.append({
            "ip": str(row[1]), "hostname": row[2], "state": row[3],
            "status": row[4], "source": row[5], "dhcp_server": row[6],
            "last_seen_scanner": row[7].isoformat() if row[7] else None,
            "last_seen_librenms": row[8].isoformat() if row[8] else None,
            "description": row[9],
            "device": dev_name.get(owner) if owner else None,
        })

    return {"subnets": subnets, "ips": ips, "devices": devices,
            "empty": not (subnets or ips or devices),
            # 這次實際送進模型的物件（不會送給模型，見 _batches）：戰情室用它判斷模型寫的敘述
            # 能不能給權限較小的帳號看，以及標出「涵蓋範圍」與取樣上限
            "_scope": {"subnet_ids": [str(r[0]) for r in sub_rows], "ip_ids": [str(r[0]) for r in ip_rows],
                       "device_ids": [str(i) for i, _n, _t in dev_rows],
                       "capped": {"subnets": len(sub_rows) >= MAX_SAMPLE, "ips": len(ip_rows) >= MAX_SAMPLE,
                                  "devices": len(dev_rows) >= MAX_SAMPLE}}}


_PROMPT = """You are reviewing an IP address management (IPAM) inventory for a network team.

Look for things that are suspicious, inconsistent, or a security concern.

Security is a first-class part of this review, not an afterthought. Look specifically for:
- management or infrastructure interfaces (BMC/iDRAC/IPMI/iLO, switch and firewall management,
  hypervisor consoles) sitting in general-purpose or user subnets instead of a management segment
- hosts acting as DHCP, DNS or gateway that are not recorded as such
- addresses in a subnet that no monitoring source has ever seen — nobody would notice if
  something appeared there
- names suggesting test/temporary/personal equipment inside production ranges
- records whose name, description and observed state contradict each other, which usually means
  the inventory no longer matches reality

Other things worth reporting: addresses recorded as in use but never seen alive; duplicate or
contradictory records; subnets with no monitoring coverage; naming that breaks an otherwise
consistent convention.

Each subnet carries `scan_enabled` and how many of its addresses a scanner has ever seen
(`ips_seen` of `ips_total`). Use them before calling anything a monitoring blind spot: a subnet
that is scanned and where many addresses have been seen is **covered**, and addresses in it that
were never seen are stale records or hosts that answer no probe — not a coverage gap. Recommending
someone check monitoring coverage when scanning already works there sends them to the wrong place.

Each address carries a "device" field naming the machine it belongs to, where that is known.
A multi-homed machine legitimately holds several addresses — one per network interface — so
several addresses sharing a "device", or sharing a hostname while naming the same "device",
is normal and is NOT a duplicate or a conflict. Report a conflict only when the records
genuinely disagree, for example the same address appearing twice, or one hostname naming two
different devices.

Rules you must follow:
- {data_rule}
- Report only what the data below actually supports. Do not speculate beyond it.
- Every finding must cite the specific records it came from.
- If nothing stands out, return an empty list. An empty result is a valid and useful answer;
  do not invent findings to fill space.
- severity must be one of: low, medium, high.
- category must be one of: exposure, stale, conflict, naming, coverage, policy, other.
- Write title, detail and recommendation in {language}. Keep hostnames, IP addresses and
  other identifiers exactly as they appear in the data — do not translate or reword them.

In `evidence`, cite the records using these keys so they can be linked to:
- "ips": the IP addresses, exactly as they appear (e.g. ["10.0.0.1"])
- "devices": device NAMES (e.g. ["switch-005"]) — never internal UUIDs, they mean
  nothing to a person reading the finding and cannot be looked up by eye
- "subnets": subnet CIDRs (e.g. ["10.0.0.0/24"])
- "note": one short sentence on what in the data led to this
Put identifiers in these keys rather than burying them inside "note".

Answer with JSON only, no prose, in exactly this shape:
{"findings":[{"severity":"low","category":"stale","title":"...","detail":"...",
"recommendation":"...","evidence":{"ips":["10.0.0.1"],"devices":["sw1"],"note":"..."}}]}

Inventory:
"""


# 台灣用詞對照。模型的中文預設是中國用語，不給對照表的話幾乎必然寫成「信息」「網絡」
# 「缺失」這一類 —— 使用者一看就知道不是自家的東西。
# 這是**盡力而為**：模型不一定每個都照做，所以 UI 上的固定字串一律走 i18n，
# 只有模型產生的敘述才依賴這份提示。
_ZH_TW_TERMS = (
    "「子網路」不用「子網」、「裝置」不用「設備」、「上線」不用「在線」、"
    "「對應」不用「映射」、「相關」不用「涉及」、「缺少」不用「缺失」、"
    "「資訊」不用「信息」、「網路」不用「網絡」、「伺服器」不用「服務器」、"
    "「預設」不用「默認」、「設定」不用「配置」、「支援」不用「支持」、"
    "「品質」不用「質量」、「透過」不用「通過」、「軟體」不用「軟件」、"
    "「硬體」不用「硬件」、「程式」不用「程序」、「檔案」不用「文件」、"
    "「登入」（login）不用「登錄」（台灣的「登錄」是登記的意思）、「還原」不用「回滾」、"
    "「選用」不用「可選」、「正式環境」不用「生產環境」。"
    "提到資料欄位或狀態值時，寫畫面上的中文名稱並把原文放在括號裡，不要只寫原文，例如"
    "「狀態（state）為使用中（active）」「最後出現（掃描代理，last_seen_scanner）為空值（null）」"
)

_LANGUAGES = {
    "zh-TW": (
        "Traditional Chinese as used in Taiwan（繁體中文，台灣用語，標點用全形）。"
        "特別注意這些對照，寫錯會一眼看出不是台灣的產品：" + _ZH_TW_TERMS
    ),
    "en-US": ("English. When you mention a data field, use its plain name and put the raw field "
              "name in parentheses, e.g. \"last seen (scanner, last_seen_scanner)\"."),
    "ja-JP": ("Japanese（日本語。です・ます調で、専門用語はカタカナまたは英語のまま。"
              "データ項目や状態値は画面上の日本語名を書き、原文を括弧で添えること。例："
              "「状態（state）が使用中（active）」「最終確認（スキャナ、last_seen_scanner）が値なし（null）」）"),
}


async def _locale_for(session: AsyncSession, user: User) -> str:
    """發現內容要用哪種語言寫（zh-TW／en-US／ja-JP）。

    存下來的是一段文字、不是 i18n key（模型的敘述沒辦法預先翻譯），所以只能挑一種語言。
    取執行者的介面偏好 —— 排程執行時就是設定裡指定的那個管理員。
    """
    from app.models.user import UserPreference

    loc = (await session.execute(
        select(UserPreference.locale).where(UserPreference.user_id == user.id)
    )).scalar_one_or_none()
    return loc if loc in _LANGUAGES else "zh-TW"


def _language_instruction(locale: str) -> str:
    """語言指示＋欄位名稱對照（跟畫面一致）。只給範例的話，模型會自己取名：實測把 status 寫成
    「狀態」，跟 state 的「狀態」撞名；兩個最後出現時間也沒標來源。"""
    base = _LANGUAGES[locale]
    labels = _FIELD_LABELS.get(locale, {})
    fields = [k for k in ("state", "status", "last_seen_scanner", "last_seen_librenms", "hostname",
                          "description", "source", "dhcp_server") if k in labels]
    if not fields:
        return base
    pairs = "、".join(f"{k}＝{labels[k]}" for k in fields) if locale != "en-US" else \
        ", ".join(f"{k} = {labels[k]}" for k in fields)
    return f"{base}\nField names on screen: {pairs}"


async def _language_for(session: AsyncSession, user: User) -> str:
    return _language_instruction(await _locale_for(session, user))


# 提示詞裡的用詞對照是「盡力而為」—— 模型不一定照做（實測：叫它別用「涉及」，它
# 下一輪還是寫了）。所以存進資料庫之前再做一次**確定性**的替換。
#
# 只放「在台灣的技術文件裡幾乎不可能是正確用法」的詞。像「通過」（通過測試）、
# 「支持」（支持某個立場）、「程序」（法律程序）這種一詞兩義的，替換會改錯句意，
# 只留在提示詞裡靠模型自律。
_ZH_TW_FIXUPS: tuple[tuple[str, str], ...] = (
    # 正規表示式＋替換。先長後短：「IP 地址」要在「地址」之前，否則會先被短的吃掉。
    # **要看前後文**：單純字串取代會跨詞誤轉 —— 「網路區段內存在個人裝置」（內＋存在）
    # 被換成「區段記憶體在」（2026-09-28 使用者回報）。
    (r"涉及裝置", "相關裝置"), (r"涉及的", "相關的"), (r"涉及到", "相關的"),
    (r"IP ?地址", "IP 位址"), (r"地址", "位址"),
    (r"信息", "資訊"), (r"網絡", "網路"), (r"服務器", "伺服器"),
    (r"默認", "預設"), (r"軟件", "軟體"), (r"硬件", "硬體"),
    (r"內存(?!在)", "記憶體"),               # 「內存在」是「內＋存在」
    (r"端口", "連接埠"),
    (r"缺失", "缺少"),
    (r"(?<![所存])在線(?!路)", "上線"),      # 「所在線路」「存在線上」不是「在線」
    (r"映射", "對應"),
    (r"子網掩碼", "子網路遮罩"), (r"交換機", "交換器"),
    # 台灣說「正式環境」；「生產環境」是中國用語（使用者回報）
    (r"生產(?=環境|網路|伺服器|主機|系統|服務|區段|用途)", "正式"),
    # 刻意不換「登錄」：台灣的「登錄」是登記的意思（「未登錄於 IPAM」），換成「登入」會改錯句意
)
_ZH_TW_RULES = tuple((re.compile(p), r) for p, r in _ZH_TW_FIXUPS)


def zh_tw_fixup(text: str) -> str:
    """把模型寫出來的中國用語換成台灣用語。

    只動敘述文字，**不動 evidence** —— 那裡面是主機名稱與位址，一個字都不能改。
    """
    for pat, good in _ZH_TW_RULES:
        text = pat.sub(good, text)
    return text


# 送給模型的快照用的是資料欄位原名（state、last_seen_scanner……），模型常照抄進敘述，
# 讀的人只看到一串程式代碼（使用者回報：「不要只有原文」）。敘述裡出現的欄位原名與狀態值，
# 換成**畫面上的名稱**並把原文放在括號裡，依發起巡檢的使用者語言；名稱跟前端 i18n 一致。
# 英文本身就是英文字，只替沒有意義的欄位代碼（底線命名）加註。
_FIELD_LABELS: dict[str, dict[str, str]] = {
    "zh-TW": {
        "last_seen_scanner": "最後出現（掃描代理）", "last_seen_librenms": "最後出現（LibreNMS）",
        "dhcp_server": "DHCP 主機", "scan_enabled": "掃描", "ips_seen": "已偵測到的 IP 數",
        "ips_total": "IP 總數", "hostname": "主機名稱", "description": "說明", "state": "狀態",
        "status": "實際狀態", "source": "來源", "device": "裝置",
        "active": "使用中", "reserved": "保留", "offline": "離線", "online": "上線", "unknown": "未知",
        "used": "已使用", "inactive": "停用", "null": "空值",
    },
    "ja-JP": {
        "last_seen_scanner": "最終確認（スキャナ）", "last_seen_librenms": "最終確認（LibreNMS）",
        "dhcp_server": "DHCP サーバー", "scan_enabled": "スキャン", "ips_seen": "検出済み IP 数",
        "ips_total": "IP 総数", "hostname": "ホスト名", "description": "説明", "state": "状態",
        "status": "実効状態", "source": "ソース", "device": "機器",
        "active": "使用中", "reserved": "予約", "offline": "オフライン", "online": "オンライン",
        "unknown": "不明", "used": "使用済み", "inactive": "無効", "null": "値なし",
    },
    "en-US": {
        "last_seen_scanner": "last seen (scanner)", "last_seen_librenms": "last seen (LibreNMS)",
        "dhcp_server": "DHCP server", "scan_enabled": "scanning enabled", "ips_seen": "IPs seen",
        "ips_total": "total IPs",
    },
}
# 前後不可以是英數、底線、點、連字號、斜線、冒號、@：主機名稱（active-dir01、gw-01.example.net）裡的字不算
_FIELD_BOUND_L = r"(?<![\w.\-/:@])"
_FIELD_BOUND_R = r"(?![\w.\-/:@])"


def _inside_parens(text: str, idx: int) -> bool:
    """idx 之前有沒有還沒關閉的括號（已經加註過的「狀態（state）」就不要再加）。"""
    depth = 0
    for ch in reversed(text[:idx]):
        if ch in "）)":
            depth += 1
        elif ch in "（(":
            if depth == 0:
                return True
            depth -= 1
    return False


def annotate_fields(text: str, locale: str) -> str:
    """把敘述裡的欄位原名／狀態值換成「畫面上的名稱（原文）」。"""
    labels = _FIELD_LABELS.get(locale)
    if not labels or not text:
        return text
    cjk = locale in ("zh-TW", "ja-JP")
    sep = "，" if locale == "zh-TW" else "、"
    names = sorted(labels, key=len, reverse=True)
    pat = re.compile(_FIELD_BOUND_L + "(" + "|".join(map(re.escape, names)) + ")" + _FIELD_BOUND_R)

    def repl(m: re.Match[str]) -> str:
        raw = m.group(1)
        if _inside_parens(m.string, m.start()):
            return raw
        label = labels[raw]
        if cjk:
            return f"{label[:-1]}{sep}{raw}）" if label.endswith("）") else f"{label}（{raw}）"
        return f"{label[:-1]}, {raw})" if label.endswith(")") else f"{label} ({raw})"

    out = pat.sub(repl, text)
    if cjk:
        # 中日文裡夾英文時習慣前後留空白；換成全形名稱後，名稱兩側的半形空白就多餘了
        out = re.sub(r"(?<=[^\x00-\x7f]) (?=[^\x00-\x7f])", "", out)
    return out


#: 敘述裡「看起來像位址」的字樣（四段以點分隔）。刻意寫得寬鬆到連壞掉的也抓得到 ——
#: 實機出現過 `192.16CA.1.59`（模型把 `192.168.1.59` 寫壞）。
_IPISH = re.compile(r"(?<![\w.])(?:[0-9A-Za-z]{1,4}\.){3}[0-9A-Za-z]{1,4}(?:/\d{1,2})?(?![\w.])")


def _valid_ipv4(tok: str) -> bool:
    try:
        ipaddress.ip_network(tok, strict=False)
    except ValueError:
        return False
    return True


def strip_unverifiable_addresses(text: str, allowed: set[str]) -> tuple[str, int]:
    """把敘述裡「查不到出處的位址」拿掉，回傳處理後的文字與拿掉幾個。

    模型會在敘述中重寫位址，而且會寫錯：實機同一次巡檢裡出現 `192.16CA.1.59`
    （壞字）與 `196.168.1.39`（合法但不存在，真正的是 `192.168.1.39`）。
    這種錯誤特別危險 —— 它看起來精確、語氣肯定，讀的人會直接照著去查那個位址。

    真正的依據在 `evidence`（那是我們自己從資料庫撈的，不是模型寫的），畫面上也
    一直有顯示。所以敘述裡對不上依據的位址一律拿掉，寧可少一句話，也不要留一個
    **看起來像事實的錯誤**。CIDR（含 `/` 的網段）保留 —— 那通常是在講範圍，不是指某台機器。
    """
    removed = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal removed
        tok = m.group(0)
        if "/" in tok and _valid_ipv4(tok):
            return tok                       # 網段：講範圍用的，不是指認某一台
        if tok in allowed:
            return tok
        removed += 1
        return ""

    out = _IPISH.sub(repl, text)
    if removed:
        # 收拾拿掉之後留下的空括號與連續空白／標點
        out = re.sub(r"[（(]\s*[)）]", "", out)
        out = re.sub(r"\s{2,}", " ", out)
        out = re.sub(r"\s+([，。、）)])", r"\1", out)
        out = re.sub(r"([（(])\s+", r"\1", out)
    return out.strip(), removed


def _clean(text: str | None, limit: int) -> str:
    return (text or "").strip()[:limit]


def parse_findings(raw: str) -> list[dict[str, Any]]:
    """把模型輸出解析成發現清單。解析不出來回空清單。"""
    return _parse(raw) or []


def _salvage_findings(txt: str) -> list[dict[str, Any]] | None:
    """從被切斷的 JSON 裡撿出**完整的**那幾筆發現。

    產出有長度上限，模型寫到一半被切掉是常態；前面幾筆是完整的，只有最後一筆殘缺。
    這裡逐字掃過 `"findings": [` 之後的內容，用大括號深度找出每一個完整物件，
    殘缺的那筆直接丟掉。

    回 `None` 代表連陣列開頭都找不到 —— 那就不是「被切斷」，是根本不是我們要的格式。
    """
    key = txt.find('"findings"')
    if key < 0:
        return None
    bracket = txt.find("[", key)
    if bracket < 0:
        return None

    out: list[dict[str, Any]] = []
    depth = 0
    start = -1
    in_str = False
    escaped = False
    for i in range(bracket + 1, len(txt)):
        ch = txt[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    obj = json.loads(txt[start:i + 1])
                except ValueError:
                    pass
                else:
                    if isinstance(obj, dict):
                        out.append(obj)
                start = -1
        elif ch == "]" and depth == 0:
            break
    return out


def _parse(raw: str, locale: str = "zh-TW") -> list[dict[str, Any]] | None:
    """解析模型輸出。**`None` ＝解析失敗，`[]` ＝解析成功但沒有發現** —— 兩者不同。

    模型輸出**一律當成不可信輸入**：可能夾雜說明文字、用自創的嚴重度、或根本不是 JSON。
    但「這次沒發現問題」是合法且有用的答案（提示詞就是這樣要求的），不能跟「模型壞掉了」
    混為一談 —— 混掉的話，二選一必定出錯：不是把故障當成平安，就是把平安報成故障。
    """
    txt = (raw or "").strip()
    if txt.startswith("```"):
        txt = txt.split("```")[1] if "```" in txt[3:] else txt.strip("`")
        txt = txt.removeprefix("json").strip()
    start, end = txt.find("{"), txt.rfind("}")
    if start < 0 or end <= start:
        return None
    items: Any
    try:
        data = json.loads(txt[start:end + 1])
    except (ValueError, TypeError):
        # JSON 壞掉最常見的原因是被產出長度上限**從中間切斷** —— 前面那幾筆發現是
        # 完整的，只有最後一筆寫到一半。整批丟掉等於把已經算出來的東西浪費掉。
        items = _salvage_findings(txt)
        # 撿不到任何一筆完整的 → 這次真的什麼都沒拿到。JSON 都壞了還回「沒發現問題」，
        # 等於把故障報成平安。
        if not items:
            return None
    else:
        items = data.get("findings") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None

    out: list[dict[str, Any]] = []
    for it in items[:MAX_FINDINGS]:
        if not isinstance(it, dict):
            continue
        title = _clean(it.get("title"), 300)
        if not title:
            continue          # 沒有標題的發現無法呈現，直接丟掉
        sev = str(it.get("severity", "")).lower()
        cat = str(it.get("category", "")).lower()
        ev = it.get("evidence")
        rec = _clean(it.get("recommendation"), 2000) or None
        # 依據資料裡的位址才算數 —— 那是我們自己撈的，不是模型寫的
        allowed = set()
        if isinstance(ev, dict) and isinstance(ev.get("ips"), list):
            allowed = {str(x).strip() for x in ev["ips"] if str(x).strip()}
        dropped = 0

        def _sane(text: str, _allowed: set[str] = allowed) -> str:
            nonlocal dropped
            cleaned, n = strip_unverifiable_addresses(text, _allowed)
            dropped += n
            return cleaned

        def _fix(text: str) -> str:
            if locale == "zh-TW":
                text = zh_tw_fixup(text)
            return annotate_fields(text, locale)

        title = _sane(_fix(title))
        detail = _sane(_fix(_clean(it.get("detail"), 4000)))
        rec_txt = _sane(_fix(rec)) if rec else None
        if dropped:
            log.info("ai_audit dropped unverifiable addresses",
                     count=dropped, category=cat)
        out.append({
            "severity": sev if sev in SEVERITIES else "low",   # 自創等級一律降為 low
            "category": cat if cat in CATEGORIES else "other",
            # 敘述套台灣用詞修正並清掉查不到出處的位址；evidence 不動（那是我們撈的）
            "title": title,
            "detail": detail,
            "recommendation": rec_txt,
            "evidence": ev if isinstance(ev, dict) else ({"note": str(ev)[:2000]} if ev else None),
        })
    return out


def estimate_tokens(text: str) -> int:
    """粗估 token 數（寧可高估）；與輸出上限共用 ai.estimate_tokens 那一份。
    （實測踩過：360 筆 IP 一次送，超出 num_ctx=16384，提示詞被截掉，模型改寫了一篇網路環境介紹回來。）"""
    from app.services.ai import estimate_tokens as _est
    return _est(text)


def _batches(snapshot: dict[str, Any], budget_tokens: int) -> list[dict[str, Any]]:
    """把快照切成數批，每批的估計 token 數不超過預算。

    子網路與裝置清單**每批都附上**：少了它們，模型就無法判斷一個位址所在的網段有沒有
    監測、名稱符不符合該網段的慣例 —— 那正是我們要它找的東西。
    """
    context = {"subnets": snapshot["subnets"], "devices": snapshot["devices"]}
    ctx_tokens = estimate_tokens(json.dumps(context, ensure_ascii=False))
    room = max(budget_tokens - ctx_tokens, MIN_BATCH_TOKENS)

    out: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []
    cur_tokens = 0
    for ip in snapshot["ips"]:
        t = estimate_tokens(json.dumps(ip, ensure_ascii=False))
        if cur and (cur_tokens + t > room or len(cur) >= MAX_IPS_PER_BATCH):
            out.append({**context, "ips": cur})
            cur, cur_tokens = [], 0
        cur.append(ip)
        cur_tokens += t
    if cur or not out:
        out.append({**context, "ips": cur})
    return out


def audit_num_ctx(cfg: Any) -> int:
    """巡檢實際使用的上下文長度：有設就用巡檢自己的，沒設沿用對話模型的。"""
    return int(getattr(cfg, "ai_audit_num_ctx", None)
               or getattr(cfg, "num_ctx", None) or 4096)


def _budget_tokens(cfg: Any) -> int:
    """一批可以用掉多少 token。

    留給指示與模型回覆的空間要先扣掉 —— 把整個上下文都塞滿輸入，模型連話都答不完。
    """
    ctx = audit_num_ctx(cfg)
    return max(ctx - RESERVED_TOKENS - int(ctx * OUTPUT_SHARE), MIN_BATCH_TOKENS)


async def run_audit(
    session: AsyncSession, user: User,
    progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> AuditRun:
    """跑一次巡檢並把發現寫入資料庫。回傳這次的執行摘要。

    資料會依模型的上下文長度切批送 —— 一次全送會超出 num_ctx 被截斷，而截斷是**安靜**
    發生的：模型收到半截提示詞，回來的東西看起來像正常回答，只是完全不照格式。

    `progress` 給 UI 用：每個階段回報一次 {stage, current, total}。
    """
    async def _emit(stage: str, current: int = 0, total: int = 0, **extra: Any) -> None:
        if progress:
            await progress({"stage": stage, "current": current, "total": total, **extra})

    if _RUNNING.locked():
        raise AuditBusy("已經有一次巡檢正在執行，請等它跑完再試")

    async with _RUNNING:
        return await _run_audit(session, user, _emit, want_progress=progress is not None)


async def _run_audit(
    session: AsyncSession, user: User,
    _emit: Callable[..., Awaitable[None]],
    want_progress: bool = False,
) -> AuditRun:
    """實際流程。跟 `run_audit` 分開只是為了讓「同時只跑一次」的鎖包住整段。"""
    run_id = uuid.uuid4()
    # 另一個行程正在跑（手動巡檢在後端、排程在 jt-ipam-ai-audit.service）：不要疊在一起打同一台 LLM
    since = await get_ai_audit_running_since(session)
    if since is not None and datetime.now(UTC) - since < RUN_STALE_AFTER:
        raise AuditBusy("已經有一次巡檢正在執行，請等它跑完再試")
    await _emit("collecting")
    started = datetime.now(UTC)
    snapshot = await _collect(session, user)
    if snapshot.get("empty"):
        try:
            await record_ai_audit_outcome(session, at=datetime.now(UTC), outcome={
                "status": "failed", "error_code": "no_data", "run_id": str(run_id),
                "started_at": started.isoformat()})
        except Exception:
            await session.rollback()
            log.warning("ai_audit.outcome_mark_failed", exc_info=True)
        return AuditRun(run_id=run_id, findings=0, skipped=0,
                        error="沒有可見的資料可分析（檢查此帳號的權限範圍）",
                        status="failed", error_code="no_data")

    # 開始打 LLM 之前就記下這次嘗試：之後被 systemd 砍掉、或 LLM 主機在途中掛掉，
    # 排程都不會 5 分鐘後又整個重來（2026-10-12：10-04、10-10 都因此跑了兩次）
    await mark_ai_audit_started(session, at=started)
    result: AuditRun | None = None
    try:
        result = await _analyze(session, user, run_id, snapshot, _emit, want_progress)
        return result
    finally:
        try:
            await _finish(session, run_id, started, snapshot, result)
        except Exception:                      # 收尾失敗不可以蓋掉巡檢本身的結果或例外
            await session.rollback()
            log.warning("ai_audit.finish_mark_failed", exc_info=True)


async def _finish(session: AsyncSession, run_id: uuid.UUID, started: datetime,
                  snapshot: dict[str, Any], result: AuditRun | None) -> None:
    """記下這次的結果（狀態、錯誤代碼、取樣範圍）。result 是 None＝中途丟出例外（被砍、程式錯誤）＝失敗。"""
    scope = snapshot.get("_scope") if isinstance(snapshot.get("_scope"), dict) else {}
    status = result.status if result else "failed"
    counts = {"subnets": len(scope.get("subnet_ids") or []), "ips": len(scope.get("ip_ids") or []),
              "devices": len(scope.get("device_ids") or []), "capped": dict(scope.get("capped") or {})} \
        if scope else None
    now = datetime.now(UTC)
    await mark_ai_audit_finished(session, at=now, outcome={
        "status": status, "error_code": (result.error_code if result else "exception"),
        "run_id": str(run_id), "started_at": started.isoformat(),
        "batches": result.batches if result else 0, "failed_batches": result.skipped if result else 0,
        "scope_counts": counts,
    })
    if status in ("completed", "partial") and scope:
        await remember_ai_audit_run_scope(session, run_id=str(run_id), at=now, scope=scope)


async def _analyze(
    session: AsyncSession, user: User, run_id: uuid.UUID, snapshot: dict[str, Any],
    _emit: Callable[..., Awaitable[None]], want_progress: bool,
) -> AuditRun:
    """把快照分批送給模型、對齊發現。"""
    from app.services.ai import AIError, AINotConfigured, raw_chat

    cfg = await get_llm_config(session)
    locale = await _locale_for(session, user)
    from app.services.prompt_safety import DATA_RULE_EN, data_block
    prompt = (_PROMPT.replace("{language}", _language_instruction(locale))
              .replace("{data_rule}", DATA_RULE_EN))
    batches = _batches(snapshot, _budget_tokens(cfg))
    total = len(batches)
    await _emit("analyzing", 0, total,
                ips=len(snapshot["ips"]), model=cfg.ai_audit_model or cfg.chat_model)

    items: list[dict[str, Any]] = []
    errors: list[str] = []
    llm_errors = 0                             # 連不上／逾時（其餘的失敗是回應解析不出來）
    for i, batch in enumerate(batches, start=1):
        payload = json.dumps(batch, ensure_ascii=False)
        # 一批可能要跑好幾分鐘。只有批次層級的進度時，畫面會長時間完全不動 ——
        # 邊收邊回報字數，至少看得出模型還在寫東西而不是卡住了。
        written = 0
        phase = "thinking"

        async def _on_chunk(piece: str, kind: str, _i: int = i, _n: int = len(items)) -> None:
            nonlocal written, phase
            if kind != phase:                  # thinking → content：換階段，字數重新算
                phase, written = kind, 0
            written += len(piece)
            if written % 200 < len(piece):     # 每約 200 字回報一次，不要洗版
                await _emit("analyzing", _i - 1, total, batch=_i,
                            written=written, phase=phase, found=_n)

        await _emit("analyzing", i - 1, total, batch=i, found=len(items))
        try:
            # role="audit"：巡檢可以接自己的 LLM Server 與模型，沒設就繼承對話設定
            raw = await raw_chat(session, prompt + data_block(payload), timeout=AUDIT_TIMEOUT,
                                 role="audit", model=cfg.ai_audit_model, force_json=True,
                                 max_output_tokens="auto", no_thinking=True,
                                 num_ctx=cfg.ai_audit_num_ctx,
                                 on_chunk=_on_chunk if want_progress else None)
        except AINotConfigured as exc:
            return AuditRun(run_id=run_id, findings=0, skipped=0, error=str(exc),
                            status="failed", error_code="not_configured", batches=total)
        except AIError as exc:
            # 失敗也算跑過一次：不記的話排程每輪都會重試，而一次逾時要等 AUDIT_TIMEOUT，
            # 重試會直接疊在一起打同一台 LLM。等下一個間隔再試就好。
            errors.append(str(exc))
            llm_errors += 1
            await _emit("analyzing", i, total, error=str(exc))
            continue

        parsed = _parse(raw, locale=locale)
        if parsed is None:
            # 有回應但解析不出來：把模型實際講了什麼帶出來。只說「無法解析」的話，
            # 要查是模型講廢話、回應被截斷、還是換了格式，完全無從下手。
            errors.append("模型回應無法解析成發現清單：" + (raw.strip()[:300] or "（空回應）"))
        else:
            items.extend(parsed)
        await _emit("analyzing", i, total, found=len(items))

    if errors and not items:
        # 每一批都失敗 → 這次巡檢是壞的，不能當成「沒發現問題」
        return AuditRun(run_id=run_id, findings=0, skipped=len(errors),
                        error=errors[0] if len(errors) == 1
                        else f"{len(errors)}/{total} 批分析失敗；第一個錯誤：{errors[0]}",
                        status="failed", error_code="llm_error" if llm_errors else "parse_error",
                        batches=total)

    await _emit("saving", total, total)
    items = _dedupe(items)[:MAX_FINDINGS]

    kept = await reconcile_findings(session, run_id, items,
                                    model_name=cfg.ai_audit_model or cfg.chat_model,
                                    # 有批次失敗 → 這輪不完整，不能拿它去判定誰已解決
                                    partial=bool(errors))
    await session.commit()
    await _emit("done", total, total, found=kept)
    return AuditRun(
        run_id=run_id, findings=kept, skipped=len(errors),
        # 部分批次失敗仍然回報，但不擋掉已經拿到的發現 —— 兩者都要讓人知道。
        # 也要講出「這次沒有移除任何既有發現」，否則使用者會納悶清單為什麼沒縮。
        error=(f"{len(errors)}/{total} 批分析失敗（結果可能不完整）；"
               f"為避免把沒檢查到的問題誤判為已解決，這次不移除既有發現。"
               f"第一個錯誤：{errors[0]}"
               if errors else None),
        status="partial" if errors else "completed",
        error_code="some_batches_failed" if errors else None, batches=total,
    )


async def reconcile_findings(
    session: AsyncSession, run_id: Any, items: list[dict[str, Any]],
    model_name: str | None = None, *, partial: bool = False,
) -> int:
    """把「未處理」清單對齊這一次的結果，回傳這次實際留下的未處理筆數。

    巡檢是**當下狀態的快照**，不是逐次累加的流水帳。以前每跑一次就把整批發現再存一份，
    四次執行累積出 62 筆、大半是同一件事 —— 因為模型每次把 IP 分組的方式不同，
    「分類＋IP 集合」的指紋就跟著不同，於是被當成新發現。

    對齊規則：
    - 已忽略的一律不動（那是抑制的依據），而且同一件事再出現也不重新開啟
    - 這次還在的：沿用原本那一列（保留發現時間，才看得出從什麼時候就這樣）
    - 這次沒有了：刪掉（問題解決了，或模型換了說法）
    - 這次新出現的：新增

    `partial=True`（有批次失敗）時**不刪除**。巡檢是分批送給模型的，只要有一批失敗，
    那一批的資料這次根本沒有被檢查過，它裡面的問題自然不會出現在結果裡 —— 照常對齊
    就會把它們當成「已經解決」而刪掉，畫面上看起來像問題自己好了。寧可留著一筆可能
    已修好的，也不要讓一個還在的問題安靜消失。
    """
    fp_rows = (await session.execute(
        select(AIFinding.fingerprint, AIFinding.dismissed_at, AIFinding.evidence).where(
            AIFinding.status == "dismissed", AIFinding.fingerprint.is_not(None)))).all()
    since = await _device_since_of(session, {ip for _fp, _at, ev in fp_rows for ip in subjects({"evidence": ev})})
    # 忽略之後才換了設備的位址（MAC 變了）：那筆忽略說的是上一台，新設備的同一件事照樣要列出（2026-10-12）
    dismissed_fps = {fp for fp, at, ev in fp_rows
                     if not _changed_after(subjects({"evidence": ev}), at, since)}
    dismissed_by_cat = await dismissed_subjects(session)
    existing = {
        f.fingerprint: f for f in (await session.execute(
            select(AIFinding).where(AIFinding.status == "open"))
        ).scalars().all() if f.fingerprint
    }

    kept = 0
    seen: set[str] = set()
    for it in items:
        fp = fingerprint(it)
        if fp in seen:
            continue
        seen.add(fp)
        if fp in dismissed_fps:
            continue          # 使用者判斷過是誤報 → 不再開啟
        subj = subjects(it)
        if subj and subj <= dismissed_by_cat.get(it.get("category") or "", set()):
            # 這條講的每一台，使用者都已經在同一個分類下忽略過了 → 不要再開一筆。
            # **有新對象時仍然會出現**（那是新資訊，不是重複），這正是想要的行為。
            continue
        cur = existing.get(fp)
        if cur is not None:
            # 同一件事還在：更新敘述（模型可能改寫過），但保留原本的發現時間
            for k, v in it.items():
                setattr(cur, k, v)
            cur.run_id = run_id
        else:
            session.add(AIFinding(run_id=run_id, fingerprint=fp, status="open",
                                  model=model_name, **it))
        kept += 1

    # 這次沒再出現的未處理發現 → 移除（否則清單只會愈長愈長）。
    # 但這一輪如果有批次失敗，「沒再出現」不代表「已經解決」，只代表沒被看過。
    if not partial:
        for fp, row in existing.items():
            if fp not in seen:
                await session.delete(row)

    await session.flush()
    return kept


def subjects(item: dict[str, Any]) -> set[str]:
    """這條發現「在講哪些對象」——依據資料裡的位址，正規化後的集合。

    只留位址：依據資料裡也會混進主機名稱與網段，那些每次措辭都不同，
    拿來當識別只會讓同一件事每次都長得不一樣。
    """
    ev = item.get("evidence") or {}
    ips = ev.get("ips") if isinstance(ev, dict) else None
    out: set[str] = set()
    for raw in (ips or []):
        text = str(raw).strip()
        if not text or "/" in text:          # 網段不算對象（它是範圍，不是那台機器）
            continue
        try:
            out.add(str(ipaddress.ip_address(text)))
        except ValueError:
            continue                          # 主機名稱之類的跳過
    return out


def fingerprint(item: dict[str, Any]) -> str:
    """「同一件事」的指紋：分類＋依據資料裡的位址集合。

    刻意**不用標題**：模型每次都會重新措辭（「重複的紀錄」／「重複的 IP 位址紀錄」），
    用標題比對等於幾乎每次都比不中，忽略過的東西照樣跳回來。

    ⚠️ 但光靠這個指紋**不夠**（2026-09-05 使用者回報「忽略了又出現，只好一直按忽略」）：
    模型每次引用的位址是**不同的子集** —— 實機上同一件「IPMI 在服務網段」被拆成五筆，
    分別引用 `{1.60}`、`{1.46}`、`{1.60,1.46}`、`{1.74,1.60,1.54}`。集合不同 → 指紋不同 →
    忽略當然帶不過去。所以真正讓忽略生效的是 `dismissed_subjects()` 的**逐對象**比對，
    這個指紋只負責「完全一樣的那一筆」與沒有位址可指的發現。

    沒有位址可以指的發現退回用標題 —— 總比完全沒有指紋好。
    """
    subj = subjects(item)
    if subj:
        key = f"{item.get('category', '')}|" + ",".join(sorted(subj))
    else:
        key = f"{item.get('category', '')}|title:{item.get('title', '').strip().casefold()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:64]


async def dismissed_subjects(session: AsyncSession) -> dict[str, set[str]]:
    """每個分類底下，使用者已經表示「這個對象不用再提」的位址集合。

    忽略記在**對象**而不是那一筆發現上：使用者按下忽略的意思是
    「這幾台機器的這件事我知道了」，不是「這串措辭我看過了」。
    """
    out: dict[str, set[str]] = {}
    rows = (await session.execute(
        select(AIFinding.category, AIFinding.evidence, AIFinding.dismissed_at)
        .where(AIFinding.status == "dismissed"))).all()
    since = await _device_since_of(session, {ip for _c, ev, _at in rows for ip in subjects({"evidence": ev})})
    for category, evidence, at in rows:
        # 忽略之後才換了設備的位址不算：那是上一台被忽略，不是現在這台（2026-10-12）
        got = {ip for ip in subjects({"evidence": evidence}) if not _changed_after({ip}, at, since)}
        if got:
            out.setdefault(category or "", set()).update(got)
    return out


async def _device_since_of(session: AsyncSession, ips: set[str]) -> dict[str, datetime]:
    """這些位址目前這台設備從什麼時候開始用（device_since）；沒換過手的不列。"""
    if not ips:
        return {}
    from sqlalchemy import String

    from app.core.sqlin import in_values
    rows = (await session.execute(
        select(func.host(IPAddress.ip), IPAddress.device_since)
        .where(in_values(func.host(IPAddress.ip), list(ips), type_=String()), IPAddress.device_since.is_not(None))
    )).all()
    return {str(h): t for h, t in rows}


def _changed_after(ips: set[str], at: datetime | None, since: dict[str, datetime]) -> bool:
    """這些位址裡有沒有哪一個是在 `at`（忽略的時間）之後才換了設備。"""
    if at is None:
        return False
    return any((t := since.get(ip)) is not None and t > at for ip in ips)


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """跨批去重：同一件事在相鄰批次各被講一次是常態（子網路內容每批都附）。"""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for it in items:
        key = it["title"].strip().casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


async def latest_summary(session: AsyncSession) -> dict[str, Any]:
    """儀表板用：未處理發現的數量分佈與最近一次執行時間。"""
    rows = (await session.execute(
        select(AIFinding.severity, func.count())
        .where(AIFinding.status == "open")
        .group_by(AIFinding.severity)
    )).all()
    counts = dict.fromkeys(SEVERITIES, 0)
    for sev, n in rows:
        counts[sev if sev in counts else "low"] += n
    # 「最後執行」＝真的跑過的時間，不是最後一筆發現的時間。乾淨的巡檢不留發現，
    # 用發現時間顯示的話畫面會停在上次「有問題」那天，看起來像好幾天沒跑。
    last = await get_ai_audit_last_run(session)
    # 有多少個 IP 被點名。發現數不等於問題規模 —— 一筆「命名不一致」可能牽涉 30 個位址，
    # 只看發現數會低估要處理的量。
    ip_count = (await session.execute(text("""
        SELECT count(DISTINCT ip)
          FROM ai_findings f,
               LATERAL jsonb_array_elements_text(f.evidence -> 'ips') AS ip
         WHERE f.status = 'open'
           AND jsonb_typeof(f.evidence -> 'ips') = 'array'
    """))).scalar() or 0
    return {"counts": counts, "total": sum(counts.values()), "ip_count": int(ip_count),
            "last_run_at": last.isoformat() if last else None}

