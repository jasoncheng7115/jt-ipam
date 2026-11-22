"""OCS Inventory NG 同步服務 —— REST（/ocsapi/v1），**全程唯讀（只打 GET）**。

Phase 1 只做**資產身分**：主機名稱、OS、網卡 MAC、序號／型號／廠牌，以及最後盤點時間。
軟體清單、跨系統關聯（LibreNMS 位置、Wazuh 覆蓋、CVE）是後續階段。

2026-09-18 用官方映像檔 2.10／2.11 實機探測，因此有幾件事跟直覺不同（動這裡前先讀完）：

1. **比對只用 MAC，而且只比不建、多筆視為不明確不猜**（比照 Proxmox 的 guest 比對）。
   OCS 是資產系統不是掃描器 —— 它的 IP 只是「機器上次盤點時自己看到的」，拿來建 IP 記錄
   一定會髒；重疊網段（不同客戶都有 192.168.123.10）與複製 VM（同 MAC）也靠「多筆→不猜」擋掉。
2. **增量是版本能力**：sync 時實際探一次 `/computers/lastupdate` —— 200 就走增量（2.11），
   404 就全量分頁（2.10）。這樣 2.10→2.11 升級後不必重新診斷就會自動啟用增量。
   lastupdate 是 `LASTDATE > FROM_UNIXTIME(epoch)`（**嚴格大於**、參數是 epoch），
   所以游標要往前退一個重疊窗，免得邊界那一秒的異動被漏掉。
3. **`/computer/:id` 會把軟體送兩份**（`software` 與空字串 key 各一份）—— 別重複計。
4. **OCS 帳密選用**：REST 預設無驗證。診斷會主動測「沒帶憑證連不連得上」→ 連得上要警告。
5. **過期的 OCS 主機名稱不可壓過新鮮的掃描結果**：超過 `stale_after_days` 沒盤點的機器，
   `apply_observation` 仍會記（多源保存），但我們**不 stamp last_seen_ocs 為現在**，
   而且不覆寫已對到的 Device 序號（避免用一年前的資料蓋掉手填的正確值）。
"""

from __future__ import annotations

import base64
import json
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.safe_http import safe_client, safe_request, transport_detail
from app.core.security import decrypt_secret, encrypt_secret
from app.core.sqlin import in_values, not_in_values
from app.core.ui_error import UiError
from app.models.address import IPAddress
from app.models.device import Device
from app.models.ocs import OcsServer
from app.services import hostname as hostname_svc
from app.services.arp_precedence import normalize_mac

_PAGE = 200                 # /computers 分頁大小
_INCREMENTAL_OVERLAP = 900  # 增量游標往前退的重疊窗（秒）
_DIAG_TIMEOUT = 15.0
_SYNC_TIMEOUT = 60.0
_MAX_PAGES = 2000           # 安全上限：200×2000 = 40 萬台，遠超任何實際規模


class OcsError(UiError):
    """OCS 整合的錯誤，訊息會顯示給使用者。"""


# ─────────────────── 純函式（好測、不碰網路） ───────────────────

def parse_version(text: str | None) -> tuple[int, ...]:
    """把 "2.11.0" 這種字串解析成 (2, 11, 0)。認不得回 ()。"""
    if not text:
        return ()
    nums: list[int] = []
    for part in str(text).split("."):
        digits = "".join(c for c in part if c.isdigit())
        if not digits:
            break
        nums.append(int(digits))
    return tuple(nums)


def usable_nics(networks: Any) -> list[dict[str, Any]]:
    """從 OCS 的 networks 區段挑出「值得拿來比對」的實體網卡。

    丟掉的：虛擬介面（VIRTUALDEV=1，VPN／docker0／vSwitch）、全零 MAC、空 MAC。
    不過濾的話會把一堆假 MAC 灌進來 —— 實測筆電就有一張 VPN 介面掛著 00:00:00:00:00:00。

    也丟掉 `DESCRIPTION=bmc`：Linux agent 會把 IPMI 控制器列成一張網卡，但那是**另一台設備**
    （BMC）的位址與 MAC；拿來比對會把主機的 OS／主機名稱寫到 BMC 的 IP 上（2026-09-26 實機）。

    例外：**全部**網卡都被標成虛擬時，退回用有 MAC、有 IP 的那幾張。舊版 agent（2.4.2 以前）
    在 LXC 裡把 eth0 標成虛擬（容器的網卡背後是 veth），照舊全丟的話這種容器一張都不剩，
    jt-ipam 永遠對不上它的 IP（2026-09-25 實際部署時遇到）。有實體網卡的電腦不受影響。
    """
    real: list[dict[str, Any]] = []
    virtual: list[dict[str, Any]] = []
    for nic in networks or []:
        if not isinstance(nic, dict):
            continue
        mac = normalize_mac(nic.get("MACADDR"))
        if not mac or mac == "000000000000":
            continue
        if str(nic.get("DESCRIPTION") or "").strip().lower() == "bmc":
            continue
        if str(nic.get("VIRTUALDEV") or "0") in ("1", "true", "True"):
            if nic.get("IPADDRESS"):
                virtual.append(nic)
            continue
        real.append(nic)
    return real or virtual


# 修復亂碼。OCS 代理不論機器是哪國語系，回報給伺服器的都是 **UTF-8**；亂碼的成因是 OCS 的
# 資料庫不是 UTF-8（latin1／cp1252／SQL_ASCII 很常見）：UTF-8 位元組被當 8-bit 讀進去、再以
# UTF-8 端出來（雙重編碼）。所以還原是單一且無歧義的：把「解錯的字串」編回資料庫實際存的
# 位元組（latin-1 或 cp1252），再以 UTF-8 解讀 —— 繁中／簡中／日文／韓文一律適用（因為源頭
# 都是 UTF-8）。刻意不猜 Big5／GBK／Shift-JIS 這類 8-bit 母語編碼：同一串位元組在它們之間
# 合法但解出不同字，短字串連統計偵測都不可靠，硬猜只會製造另一種亂碼。
_RECODE_FROM = ("latin-1", "cp1252")
_RECODE_TO = ("utf-8",)


def _score_text(s: str) -> int:
    """越多可列印／CJK、越少替換字元與控制碼 → 分數越高。用來在候選解碼間挑最合理的。"""
    score = 0
    for ch in s:
        o = ord(ch)
        if ch == "�":                    # 替換字元＝解錯了
            score -= 5
        elif o < 0x20 and ch not in "\t\n\r":  # 控制碼
            score -= 3
        elif 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF:
            score += 2                         # CJK 統一漢字（含相容區）
        elif 0x3000 <= o <= 0x30FF or 0xAC00 <= o <= 0xD7A3:
            score += 2                         # 日文假名／韓文
        elif o < 0x80:
            score += 1                         # ASCII
        elif 0x80 <= o <= 0xFF:
            score -= 1                         # latin-1 補充區：多半是 mojibake 的殘渣
    return score


def _repair_text(s: str | None) -> str | None:
    """盡力把任何編碼造成的亂碼還原成正常文字（見上方 _RECODE_* 說明）。

    正確存的中文（字元 > U+00FF）在 encode(latin-1) 會直接丟例外而原封不動；純 ASCII 重解
    後與原字串相同、分數不會更高，也不會被改。只有「重解後明顯更像正常文字」才採用。
    """
    if not s:
        return s
    best, best_score = s, _score_text(s)
    for enc_from in _RECODE_FROM:
        try:
            raw = s.encode(enc_from)
        except UnicodeEncodeError:
            continue
        for enc_to in _RECODE_TO:
            try:
                cand = raw.decode(enc_to)
            except (UnicodeDecodeError, LookupError):
                continue
            sc = _score_text(cand)
            if cand != s and sc > best_score:
                best, best_score = cand, sc
    return best


def hostname_of(hardware: dict[str, Any]) -> str | None:
    """OCS 的電腦名稱。空字串視為沒有。"""
    name = (hardware.get("NAME") or "").strip()
    return _repair_text(name) or None


def parse_os(hardware: dict[str, Any]) -> tuple[str | None, str | None]:
    """回 (os_guess 顯示字串, os_family 供前端配 icon)。

    OSCOMMENTS 是最好讀的（"Windows 10 Pro"／"Ubuntu 24.04.1 LTS"），OSNAME 是家族
    （"Windows"／"Linux"）。os_family 用 OSNAME 正規化。
    """
    comments = (hardware.get("OSCOMMENTS") or "").strip()
    osname = (hardware.get("OSNAME") or "").strip()
    osver = (hardware.get("OSVERSION") or "").strip()
    guess = _repair_text(comments or (f"{osname} {osver}".strip() if osname else None) or None)
    family = None
    low = osname.lower()
    if "windows" in low:
        family = "windows"
    elif "linux" in low or "ubuntu" in low or "debian" in low or "centos" in low:
        family = "linux"
    elif "mac" in low or "darwin" in low:
        family = "macos"
    elif osname:
        family = "other"
    return guess, family


# 主機板沒燒 DMI 時的佔位字串（拿來當序號會製造假資產）。有時真值前面還黏著佔位前綴
# （實機："To Be Filled By O.E.M. X570D4I-2T"）→ 去前綴、剩真值才留；整串就是佔位則視為沒有。
_DMI_JUNK = frozenset({
    "system manufacturer", "system product name", "to be filled by o.e.m.",
    "default string", "not specified", "not available", "none", "o.e.m.",
    "system serial number", "0", "n/a",
})
_DMI_JUNK_PREFIXES = ("to be filled by o.e.m.", "default string", "system manufacturer",
                      "system product name")


def _strip_dmi_placeholder(s: str | None) -> str | None:
    """去掉 DMI 佔位字串／前綴，回真值或 None。"""
    s = (s or "").strip()
    low = s.lower()
    for pre in _DMI_JUNK_PREFIXES:
        if low.startswith(pre):
            s = s[len(pre):].strip(" .-")
            low = s.lower()
    return None if (not s or low in _DMI_JUNK) else s


def _is_dmi_placeholder(s: str | None) -> bool:
    """既有 Device 欄位是不是佔位垃圾（給同步時判斷該不該覆寫）。"""
    return bool(s) and _strip_dmi_placeholder(s) != (s or "").strip()


# 序號欄專用的出廠佔位（別的欄位不套：型號叫 "123456789" 不合理，序號才會這樣填）。
# 實機：Supermicro 的系統序號是 "0123456789"，真正的序號在主機板（MSN）上。
_SERIAL_JUNK = frozenset({
    "0123456789", "1234567890", "123456789", "chassis serial number",
    "base board serial number", "serial number", "type2 - board serial number",
    "sernum0", "xxxxxxxxxx",
})


def _clean_text(v: Any) -> str | None:
    return _strip_dmi_placeholder(_repair_text((str(v or "")).strip()))


def _clean_serial(v: Any) -> str | None:
    """序號：去佔位字串與 Dell 格式前後的斜線（"/SN/BOARD/"）；全是同一個字元的也當佔位。"""
    s = _clean_text(v)
    if s:
        s = s.strip("/").strip() or None
    if not s or s.lower() in _SERIAL_JUNK or len(set(s)) == 1:
        return None
    return s


def _first_row(section: Any) -> dict[str, Any]:
    if isinstance(section, list):
        return section[0] if section and isinstance(section[0], dict) else {}
    return section if isinstance(section, dict) else {}


def bios_asset(bios: Any) -> dict[str, str | None]:
    """從 bios 區段抽出 vendor / model / serial（去佔位字串、修亂碼）。

    系統那組（S*）是佔位時退回主機板那組（M*）：序號最常見 —— 系統序號是出廠佔位、
    主機板序號才是真的。bios 在清單回應裡是 list（0 或 1 筆），在 /computer/:id 也是 list。
    """
    row = _first_row(bios)
    return {
        "vendor": _clean_text(row.get("SMANUFACTURER")) or _clean_text(row.get("MMANUFACTURER")),
        "model": _clean_text(row.get("SMODEL")) or _clean_text(row.get("MMODEL")),
        "serial": _clean_serial(row.get("SSN")) or _clean_serial(row.get("MSN")),
    }


def _pos_int(v: Any) -> int | None:
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _grouped(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """相同的項目合併成一筆＋count（兩顆一樣的 CPU、四條一樣的記憶體），保留出現順序。"""
    out: list[dict[str, Any]] = []
    for it in items:
        for o in out:
            if all(o.get(k) == v for k, v in it.items()):
                o["count"] += 1
                break
        else:
            out.append({**it, "count": 1})
    return out


# 不是實體磁碟的區塊裝置（Linux 代理照 lsblk 全列，OCS 也沒有排除的設定）：
# 記憶體壓縮、loop、光碟機、軟體 RAID／device-mapper、網路區塊裝置，以及 PVE 主機常見的
# Ceph RBD（rbdN）、ZFS zvol（zdN）、DRBD。VM 自己的 vda／xvda 是它的磁碟，不在此列。
_VIRTUAL_DISK = re.compile(r"^(zram|loop|ram|sr|fd|md|dm-|nbd|rbd|zd|drbd)\d*", re.IGNORECASE)
_DEVICE_PATH = ("//./", "\\\\.\\")
# lspci 名稱的公司段：「Intel Corporation …」「ASPEED Technology, Inc. …」
_GPU_COMPANY = re.compile(r"^(?P<co>.*?(?:Corporation|Corp\.|,? Inc\.|Co\., Ltd\.|Ltd\.))\s+(?P<rest>.+)$")
# 顯示記憶體低於這個值多半是 lspci 報的 BAR 大小（256／32 MB），不是顯示卡的記憶體
_GPU_MEM_MIN_MB = 1024


def _gpu_name(raw: str) -> str:
    """lspci 的「公司 代號 [產品名]」→「廠牌 產品名」；Windows／nvidia-smi 的名稱照原樣。"""
    s = " ".join(raw.split())
    m = _GPU_COMPANY.match(s)
    vendor = None
    if m:
        co = m.group("co")
        vendor = "AMD" if co.lower().startswith("advanced micro devices") else co.split()[0].rstrip(",")
    brackets = re.findall(r"\[([^\]]+)\]", s)
    if brackets:
        product = brackets[-1].strip()
        return f"{vendor} {product}" if vendor and not product.lower().startswith(vendor.lower()) else product
    return m.group("rest") if m else s


def hardware_summary(computer: dict[str, Any]) -> dict[str, Any]:
    """OCS 一台電腦的硬體摘要，給裝置明細的 OCS 卡片（存在 IP 的 ocs_hw）。

    系統／主機板／BIOS 各自保留（系統序號是佔位時，卡片改顯示主機板序號）；
    CPU、記憶體模組、磁碟、顯示卡只留看得懂的欄位，相同的合併計數。
    """
    hw = computer.get("hardware") or {}
    b = _first_row(computer.get("bios"))
    summary: dict[str, Any] = {
        "system": {"vendor": _clean_text(b.get("SMANUFACTURER")), "model": _clean_text(b.get("SMODEL")),
                   "serial": _clean_serial(b.get("SSN")), "chassis": _clean_text(b.get("TYPE"))},
        "board": {"vendor": _clean_text(b.get("MMANUFACTURER")), "model": _clean_text(b.get("MMODEL")),
                  "serial": _clean_serial(b.get("MSN"))},
        "bios": {"vendor": _clean_text(b.get("BMANUFACTURER")), "version": _clean_text(b.get("BVERSION")),
                 "date": _clean_text(b.get("BDATE"))},
    }

    cpus = []
    for r in computer.get("cpus") or []:
        if not isinstance(r, dict):
            continue
        model = _clean_text(r.get("TYPE")) or _clean_text(r.get("MANUFACTURER"))
        if model:
            cpus.append({"model": model, "cores": _pos_int(r.get("CORES")),
                         "threads": _pos_int(r.get("LOGICAL_CPUS")),
                         "mhz": _pos_int(r.get("SPEED")) or _pos_int(r.get("CURRENT_SPEED"))})
    summary["cpus"] = _grouped(cpus)

    modules = []
    for r in computer.get("memories") or []:
        if not isinstance(r, dict):
            continue
        mtype = _clean_text(r.get("TYPE"))
        label = " ".join(str(r.get(k) or "") for k in ("CAPTION", "DESCRIPTION")).lower()
        if (mtype or "").lower() == "empty slot" or "not installed" in label or "empty" in label:
            continue
        size = _pos_int(r.get("CAPACITY"))
        if (mtype or "").lower() in {"unknown", "other"}:
            mtype = None
        if size is None and mtype is None:
            continue
        modules.append({"size_mb": size, "type": mtype, "speed": _pos_int(r.get("SPEED"))})
    summary["memory"] = {"total_mb": _pos_int(hw.get("MEMORY")), "modules": _grouped(modules)}

    disks = []
    for r in computer.get("storages") or []:
        if not isinstance(r, dict):
            continue
        name = _clean_text(r.get("NAME")) or ""
        model = _clean_text(r.get("MODEL")) or ""
        kind = str(r.get("TYPE") or "").lower()
        size = _pos_int(r.get("DISKSIZE"))
        if not size or any(k in kind for k in ("removable", "cd", "rom", "floppy")):
            continue
        if _VIRTUAL_DISK.match(name) and not model:
            continue
        if not model or model.startswith(_DEVICE_PATH):
            model = name
        if model:
            disks.append({"model": model, "size_mb": size})
    summary["disks"] = disks

    gpus: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for r in computer.get("videos") or []:
        if not isinstance(r, dict):
            continue
        raw = _repair_text(str(r.get("NAME") or r.get("CHIPSET") or "").strip())
        if not raw:
            continue
        name = _gpu_name(raw)
        mem = _pos_int(r.get("MEMORY"))
        mem = mem if mem and mem >= _GPU_MEM_MIN_MB else None
        key = re.sub(r"[^a-z0-9]", "", name.lower())
        if key in seen:     # 同一張卡被 lspci 與驅動工具各報一次
            if mem and (seen[key]["memory_mb"] or 0) < mem:
                seen[key]["memory_mb"] = mem
            continue
        seen[key] = {"name": name, "memory_mb": mem}
        gpus.append(seen[key])
    summary["gpus"] = gpus
    return summary


# 不是硬體資訊、OCS 有真值就該換掉的裝置欄位值：LibreNMS 建立 Windows／Linux 裝置時
# 把 OS 填進廠牌、把 CPU 架構（"Intel x64"）填進型號（2026-09-27 實機）。
_OS_AS_VENDOR = frozenset({
    "windows", "linux", "freebsd", "openbsd", "netbsd", "macos", "darwin", "unix", "generic",
    "ubuntu", "debian", "centos", "rhel", "rocky", "almalinux", "fedora", "opensuse", "suse",
})
_ARCH_AS_MODEL = re.compile(
    r"^(?:(?:intel|amd)\s*(?:x64|x86)(?:\s.*)?|x86_64|amd64|i[36]86|aarch64|armv7l)$", re.IGNORECASE)


def _not_hardware(value: str | None, field: str) -> bool:
    v = (value or "").strip()
    if field == "vendor":
        return v.lower() in _OS_AS_VENDOR
    if field == "model":
        return bool(_ARCH_AS_MODEL.match(v))
    return False


def ocs_tag_of(computer: dict[str, Any]) -> str | None:
    """資產標籤：OCS 放在 accountinfo（[{HARDWARE_ID, TAG}]）。空／預設值視為沒有。"""
    ai = computer.get("accountinfo")
    row = ai[0] if isinstance(ai, list) and ai else (ai if isinstance(ai, dict) else {})
    tag = _repair_text(str(row.get("TAG") or "").strip())
    if not tag or tag.lower() in {"na", "n/a", "none", "0"}:
        return None
    return tag[:128]


def ocs_agent_of(hardware: dict[str, Any]) -> str | None:
    """OCS 代理版本（hardware.USERAGENT，如 OCS-NG_unified_unix_agent_v2.10.0）。"""
    ua = str(hardware.get("USERAGENT") or "").strip()
    return ua[:128] or None


def ocs_notes_of(computer: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    """最新幾筆備註（itmgmt_comments）。依 ID 由大到小取前 limit 筆；內容修復亂碼。"""
    rows = computer.get("itmgmt_comments")
    if not isinstance(rows, list):
        return []
    def key(r: Any) -> int:
        try:
            return int(r.get("ID") or 0)
        except (ValueError, TypeError):
            return 0
    out: list[dict[str, Any]] = []
    for r in sorted((r for r in rows if isinstance(r, dict)), key=key, reverse=True)[:limit]:
        out.append({
            "date": str(r.get("DATE_INSERT") or "").strip() or None,
            "user": str(r.get("USER_INSERT") or "").strip() or None,
            "comment": _repair_text(str(r.get("COMMENTS") or "").strip()) or None,
            "action": str(r.get("ACTION") or "").strip() or None,
        })
    return out


def lastdate_of(hardware: dict[str, Any], tz: str | None = None) -> datetime | None:
    """解析 LASTDATE（OCS 回 "2026-09-18 08:00:00"）→ UTC。

    LASTDATE 是 MySQL DATETIME，存的是 OCS 主機寫入當下的**本地時間**、沒有時區（issue #51：
    以前一律當成 UTC，UTC+8 的 OCS 會超前 8 小時）。tz 是這套 OCS 的時區（整合設定），沒給＝UTC。"""
    raw = (hardware.get("LASTDATE") or "").strip()
    if not raw:
        return None
    try:
        naive = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    try:
        zone: Any = ZoneInfo(tz) if tz else UTC
    except (ZoneInfoNotFoundError, ValueError):
        zone = UTC
    return naive.replace(tzinfo=zone).astimezone(UTC)


#: 換算後超前現在多少才算「在未來」（時鐘誤差、同步途中剛好盤點）
FUTURE_SLACK = timedelta(minutes=15)


def is_stale(last: datetime | None, *, stale_after_days: int, now: datetime | None = None) -> bool:
    """這台機器的盤點是不是太舊，舊到不該拿它的資料去壓過新鮮來源。"""
    if last is None:
        return True
    now = now or datetime.now(UTC)
    return (now - last) > timedelta(days=max(0, stale_after_days))


def dedup_sections(computer: dict[str, Any]) -> dict[str, Any]:
    """/computer/:id 會把軟體同時放在 `software` 與空字串 key。統一只留具名區段。"""
    return {k: v for k, v in computer.items() if k != ""}


def _same_ip(a: str | None, b: str | None) -> bool:
    import ipaddress
    try:
        return bool(a and b) and ipaddress.ip_address(str(a).strip()) == ipaddress.ip_address(str(b).strip())
    except ValueError:
        return False


def decide_match(mac: str, ip_ids_by_mac: dict[str, list[uuid.UUID]], *,
                 reported_ip: str | None = None,
                 addr_of: dict[uuid.UUID, str] | None = None) -> uuid.UUID | None:
    """一張網卡的 MAC 對到哪個既有 IP。

    **只比不建**：查不到 → None（不新建 IP）。**多筆→不猜**：同一 MAC 對到多個 IP
    （複製 VM、重疊網段）→ None，視為不明確。只有唯一一筆才回那個 IP id。
    唯一的例外：這張網卡回報的 IP 正好是候選之一 → MAC 與 IP 都對上，就是它。最常見的成因是
    舊的 DHCP 位址（改固定 IP 之前拿的）還留著同一個 MAC，以前會讓新位址永遠對不上（2026-09-26）。
    """
    ids = ip_ids_by_mac.get(normalize_mac(mac)) or []
    if len(ids) == 1:
        return ids[0]
    if len(ids) > 1 and reported_ip and addr_of:
        hit = [i for i in ids if _same_ip(addr_of.get(i), reported_ip)]
        if len(hit) == 1:
            return hit[0]
    return None


# ─────────────────── REST 客戶端 ───────────────────

def _aad(server_id: uuid.UUID) -> bytes:
    """密碼加密的 AAD，格式與其他整合一致（系統匯出/匯入的 secrets registry 要對得上）。"""
    return f"ocs_server:{server_id}:api_password".encode()


def _auth(server: OcsServer) -> tuple[str, str] | None:
    if not server.api_username:
        return None
    if server.api_password_enc is None or server.api_password_nonce is None:
        return (server.api_username, "")
    pw = decrypt_secret(server.api_password_enc, server.api_password_nonce,
                        aad=_aad(server.id)).decode()
    return (server.api_username, pw)


def _auth_header(server: OcsServer) -> dict[str, str]:
    """OCS REST 的 Basic Auth 標頭。沒設帳密就回空 dict（OCS 預設無驗證）。

    `safe_request` 沒有 `auth=` 參數，Basic Auth 一律走 Authorization 標頭（比照其他整合）。
    """
    creds = _auth(server)
    if creds is None:
        return {}
    raw = f"{creds[0]}:{creds[1]}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


def _base(server: OcsServer) -> str:
    if not server.base_url:
        raise OcsError("這套 OCS 沒有設定網址", code="ocs_no_url")
    return server.base_url.rstrip("/") + "/ocsapi/v1"


async def _get_json(client: httpx.AsyncClient, url: str,
                    headers: dict[str, str], verify: bool) -> Any:
    resp = await safe_request("GET", url, client=client, headers={**headers, "Accept": "application/json"},
                              verify=verify)
    resp.raise_for_status()
    return _decode_json(resp.content)


def _decode_json(raw: bytes) -> Any:
    """把回應位元組解成 JSON，不倚賴 content-type 的 charset 宣告。

    OCS 站常把非 UTF-8 的中文標成 charset=UTF-8；若真的不是合法 UTF-8，直接 .json() 會炸或
    塞替換字元而掉資料。JSON 骨架本身一定是 ASCII，所以先試合法 UTF-8/16/32（json.loads 會
    自動偵測），失敗就退回 latin-1 保留原始位元組（骨架照樣可解），字串值裡的亂碼交給
    _repair_text 還原。真的不是 JSON 才拋，並帶上底層例外原文（見 FortiGate 那次教訓）。
    """
    try:
        return json.loads(raw)
    except UnicodeDecodeError:
        try:
            return json.loads(raw.decode("latin-1"))
        except json.JSONDecodeError as exc:
            raise OcsError(f"OCS 回應不是有效的 JSON：{exc}", code="ocs_bad_json") from exc
    except json.JSONDecodeError as exc:
        raise OcsError(f"OCS 回應不是有效的 JSON：{exc}", code="ocs_bad_json") from exc


# ─────────────────── 連線診斷 ───────────────────

async def diagnose(server: OcsServer) -> dict[str, Any]:
    """測連線：能不能連、要不要驗證、版本能不能增量、抓得到幾台。

    ⚠️ **主動測「沒帶憑證連不連得上」** —— OCS REST 預設無驗證，連得上就要提醒站台這是
    無認證對外開放。這是這個整合特有的一條診斷。
    """
    base = _base(server)
    hdr = _auth_header(server)
    out: dict[str, Any] = {"base_url": server.base_url, "source_type": server.source_type}
    async with safe_client(timeout=_DIAG_TIMEOUT, verify=server.verify_tls) as client:
        # 1) 基本可達性 + 版本能力（lastupdate 在不在）
        try:
            listid = await _get_json(client, f"{base}/computers/listID", hdr, server.verify_tls)
            out["reachable"] = True
            out["computer_count"] = len(listid) if isinstance(listid, list) else None
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in (401, 403):
                raise OcsError("OCS 需要驗證，但帳密不正確或未提供",
                               code="ocs_auth_required", reason=f"HTTP {code}") from exc
            raise OcsError("連得上網址，但 /ocsapi 沒有正確回應（REST API 可能沒開）",
                           code="ocs_rest_unavailable", reason=f"HTTP {code}") from exc
        except Exception as exc:
            raise OcsError("連不到這套 OCS", code="ocs_unreachable",
                           reason=transport_detail(exc)) from exc

        # 2) 增量能力
        try:
            r = await safe_request("GET", f"{base}/computers/lastupdate", client=client,
                                   headers=hdr, verify=server.verify_tls)
            out["incremental"] = r.status_code == 200
        except Exception:
            out["incremental"] = False

        # 3) 無認證檢查：故意不帶憑證再打一次
        no_auth_ok = False
        try:
            r = await safe_request("GET", f"{base}/computers/listID", client=client,
                                   headers={"Accept": "application/json"}, verify=server.verify_tls)
            no_auth_ok = r.status_code == 200
        except Exception:
            no_auth_ok = False
        out["auth_required"] = not no_auth_ok
        out["unauthenticated_access"] = no_auth_ok  # True = 這套 OCS 沒帶憑證也讀得到 → 警告

    return out


# ─────────────────── 整批同步 ───────────────────

async def _incremental_ids(client, base, hdr, verify, since_epoch: int) -> list[int]:
    data = await _get_json(client, f"{base}/computers/lastupdate/{since_epoch}", hdr, verify)
    return [int(r["ID"]) for r in data if isinstance(r, dict) and "ID" in r]


async def _fetch_computer(client, base, hdr, verify, cid: int) -> dict[str, Any] | None:
    data = await _get_json(client, f"{base}/computer/{cid}", hdr, verify)
    if not isinstance(data, dict):
        return None
    body = data.get(str(cid)) or next(iter(data.values()), None)
    return dedup_sections(body) if isinstance(body, dict) else None


async def _iter_full(client, base, hdr, verify):
    """分頁抓完所有電腦。務必抓到空頁為止 —— 只拿第一頁是本專案踩過的坑。"""
    for page in range(_MAX_PAGES):
        data = await _get_json(
            client, f"{base}/computers?limit={_PAGE}&start={page * _PAGE}", hdr, verify)
        if not isinstance(data, dict) or not data:
            return
        for cid, body in data.items():
            if isinstance(body, dict):
                yield int(cid) if str(cid).isdigit() else cid, dedup_sections(body)
        if len(data) < _PAGE:
            return


async def _prefetch(session: AsyncSession, comps: list[dict[str, Any]],
                    ip_ids_by_mac: dict[str, list[uuid.UUID]],
                    addr_of: dict[uuid.UUID, str] | None) -> list[Any]:
    """把這批電腦會對到的 IP 與它們的裝置一次載入 session（_apply_computer 的 session.get 就不再查）。

    回傳載入的物件：呼叫端要握著它們 —— session 的 identity map 是弱參照，沒人參照的物件會被回收，
    session.get 又得重查一次。
    """
    ids: set[uuid.UUID] = set()
    for comp in comps:
        for nic in usable_nics(comp.get("networks")):
            ip_id = decide_match(nic.get("MACADDR"), ip_ids_by_mac,
                                 reported_ip=nic.get("IPADDRESS"), addr_of=addr_of)
            if ip_id is not None:
                ids.add(ip_id)
    if not ids:
        return []
    ips = list((await session.execute(select(IPAddress).where(in_values(IPAddress.id, ids)))).scalars())
    dev_ids = {i.device_id for i in ips if i.device_id}
    devs = list((await session.execute(select(Device).where(in_values(Device.id, dev_ids)))).scalars()) \
        if dev_ids else []
    return [*ips, *devs]


async def _apply_computer(
    session: AsyncSession, server: OcsServer, computer: dict[str, Any],
    ip_ids_by_mac: dict[str, list[uuid.UUID]], now: datetime,
    ocs_id: int | None = None, hn_run: Any = None,
    addr_of: dict[uuid.UUID, str] | None = None, matched_ids: set[uuid.UUID] | None = None,
    future: list[timedelta] | None = None,
) -> dict[str, int]:
    """把一台 OCS 電腦的資料落到對到的既有 IP（只比不建）。回傳這台命中的計數。"""
    hw = computer.get("hardware") or {}
    hn = hostname_of(hw)
    os_guess = parse_os(hw)[0]
    last = lastdate_of(hw, server.timezone)
    if last is not None and future is not None and last > now + FUTURE_SLACK:
        # 換算後在未來：多半是整合的時區設錯（OCS 是本地時間、這裡設成 UTC）→ 同步結果提醒
        future.append(last - now)
    stale = is_stale(last, stale_after_days=server.stale_after_days, now=now)
    asset = bios_asset(computer.get("bios")) if server.sync_bios else {}
    hwsum = hardware_summary(computer)
    tag = ocs_tag_of(computer)
    agent = ocs_agent_of(hw)
    notes = ocs_notes_of(computer)
    counts = {"matched": 0}

    for nic in usable_nics(computer.get("networks")):
        mac = nic.get("MACADDR")
        ip_id = decide_match(mac, ip_ids_by_mac, reported_ip=nic.get("IPADDRESS"), addr_of=addr_of)
        if ip_id is None:
            continue
        ip = await session.get(IPAddress, ip_id)
        if ip is None:
            continue
        counts["matched"] += 1
        if matched_ids is not None:
            matched_ids.add(ip_id)

        # 記下 OCS 的 systemid／標籤／代理版本／備註，供裝置明細卡片顯示與深連結。
        # 這些是「目前狀態」的識別/描述資訊，非優先序來源，過期與否都更新。
        if isinstance(ocs_id, int):
            ip.ocs_id = ocs_id
        ip.ocs_tag = tag
        ip.ocs_agent = agent
        ip.ocs_notes = notes or None
        ip.ocs_hw = hwsum

        # 主機名稱：過期的也記（多源保存），但由優先序決定要不要當有效值。
        if hn_run is not None:
            hn_run.report(ip, hn)
        elif hn:
            await hostname_svc.apply_observation(session, ip=ip, source="ocs", hostname=hn)
        # OS：寫進 OCS 專屬欄位（不污染掃描代理的 os_guess）；有效值由 os_precedence
        # 決定（ocs 排在 scanner 之上，agent 回報的 OS 蓋過 nmap 指紋猜測）。
        # 過期就清掉：以前只是「不寫」，舊值一直留著並且繼續蓋過掃描代理的即時結果
        # （機器換掉、OCS 裡的舊記錄還在的時候最常見，2026-09-26 稽核）
        ip.os_ocs = os_guess[:160] if (os_guess and not stale) else None
        # MAC 不用寫：我們是**用這張網卡的 MAC 比對到這個 IP 的**，兩邊已定義相等。
        # 盤點時間：只有不算過期時才 stamp 成這次盤點的時間
        if last and not stale:
            ip.last_seen_ocs = last

        # 序號／型號／廠牌落到對到的 Device —— 只補空值，不覆寫（OCS 非權威、可能過期）
        if asset and ip.device_id and not stale:
            dev = await session.get(Device, ip.device_id)
            if dev is not None:
                # 空值或先前存進去的 DMI 佔位垃圾（舊版同步留下的 "To Be Filled By O.E.M. …"）
                # → 換成 OCS 的乾淨值；OCS 也沒有乾淨值時就清掉垃圾（設 None）。使用者手填的
                # 真值一律不動。
                # 另外，別的來源填的「不是硬體資訊」的值（OS 名稱當廠牌、CPU 架構當型號）
                # 在 OCS 有真值時換掉；OCS 也沒有就留著，不清成空。
                def _pick(cur: str | None, new: str | None, field: str) -> str | None:
                    # 序號另外套序號專用的佔位清單（0123456789 等；舊版同步曾把它存進裝置）
                    junk = _is_dmi_placeholder(cur) or (field == "serial" and _clean_serial(cur) is None)
                    if not cur or junk:
                        return new
                    return new if (new and _not_hardware(cur, field)) else cur
                dev.serial = _pick(dev.serial, asset.get("serial"), "serial")
                dev.model = _pick(dev.model, asset.get("model"), "model")
                dev.vendor = _pick(dev.vendor, asset.get("vendor"), "vendor")
    return counts


async def mac_index(session: AsyncSession, server: OcsServer) -> dict[str, list[uuid.UUID]]:
    """MAC → 既有 IP id（一次撈，不逐台查 DB）。

    設了「限定子網路範圍」就只收範圍內的 IP —— 重疊網段裡另一個單位剛好有同一個 MAC 的記錄
    （例如複製出來的 VM），不能被這套 OCS 寫到。
    """
    from app.services.agent_scope import scope_uuids
    stmt = select(IPAddress.id, IPAddress.mac).where(IPAddress.mac.isnot(None))
    scope = scope_uuids(server)
    if scope:
        stmt = stmt.where(in_values(IPAddress.subnet_id, scope))
    out: dict[str, list[uuid.UUID]] = {}
    for ip_id, mac in (await session.execute(stmt)).all():
        out.setdefault(normalize_mac(mac), []).append(ip_id)
    return out


async def sync_instance(session: AsyncSession, server: OcsServer) -> dict[str, Any]:
    """同步一套 OCS。全量或增量由版本能力決定。不 commit（由呼叫端負責）。"""
    if server.source_type != "rest":
        raise OcsError("目前只支援 REST 來源（DB 直讀是第二階段）", code="ocs_db_not_impl")
    base = _base(server)
    hdr = _auth_header(server)
    now = datetime.now(UTC)
    t0 = time.monotonic()

    ip_ids_by_mac = await mac_index(session, server)
    # 同一個 MAC 有多筆時用網卡回報的 IP 分辨（見 decide_match）—— 只撈這些候選的位址
    ambiguous = [i for ids in ip_ids_by_mac.values() if len(ids) > 1 for i in ids]
    addr_of: dict[uuid.UUID, str] = {}
    if ambiguous:
        addr_of = {i: str(a) for i, a in (await session.execute(
            select(IPAddress.id, func.host(IPAddress.ip)).where(in_values(IPAddress.id, ambiguous)))).all()}
    matched_ids: set[uuid.UUID] = set()
    future: list[timedelta] = []          # 換算後落在未來的盤點時間（時區設錯的徵兆）

    seen = matched = 0
    from app.services.hostname_reports import HostnameRun, enabled_peers
    hn_run = HostnameRun(session, source="ocs", origin=f"ocs:{server.id}",
                         peers=await enabled_peers(session, OcsServer))
    async with safe_client(timeout=_SYNC_TIMEOUT, verify=server.verify_tls) as client:
        # 增量能力：實際探一次，不信任儲存的版本（升級後自動啟用）
        incremental = False
        try:
            r = await safe_request("GET", f"{base}/computers/lastupdate", client=client,
                                   headers=hdr, verify=server.verify_tls)
            incremental = r.status_code == 200
        except Exception:
            incremental = False
        # 順便記下偵測到的能力（清單/版本頁顯示）；用能力字串，不假裝知道確切小版號
        server.detected_version = "2.11+" if incremental else "2.10"

        use_incremental = incremental and server.last_incremental_epoch is not None
        mode = "incremental" if use_incremental else "full"

        # 一頁一頁處理：先把這一頁會用到的 IP 與裝置整批載入，再逐台套用 —— 以前每張對到的
        # 網卡各查一次 IP、再各查一次裝置（5,000 台一輪上萬次查詢，2026-09-30 大量資料測試）
        buf: list[tuple[Any, dict[str, Any]]] = []

        async def _flush_page() -> int:
            if not buf:
                return 0
            keep = await _prefetch(session, [c for _i, c in buf], ip_ids_by_mac, addr_of)
            n = 0
            for cid, comp in buf:
                n += (await _apply_computer(
                    session, server, comp, ip_ids_by_mac, now,
                    ocs_id=cid if isinstance(cid, int) else None, hn_run=hn_run,
                    addr_of=addr_of, matched_ids=matched_ids, future=future))["matched"]
            buf.clear()
            del keep
            return n

        if use_incremental:
            since = max(0, int(server.last_incremental_epoch) - _INCREMENTAL_OVERLAP)
            ids = await _incremental_ids(client, base, hdr, server.verify_tls, since)
            for cid in ids:
                comp = await _fetch_computer(client, base, hdr, server.verify_tls, cid)
                if comp is None:
                    continue
                seen += 1
                buf.append((cid, comp))
                if len(buf) >= _PAGE:
                    matched += await _flush_page()
        else:
            async for _cid, comp in _iter_full(client, base, hdr, server.verify_tls):
                seen += 1
                buf.append((_cid, comp))
                if len(buf) >= _PAGE:
                    matched += await _flush_page()
        matched += await _flush_page()

    # 全量而且沒碰到分頁上限，才算完整清單；增量模式只走訪有異動的電腦，不能拿來清
    complete = mode == "full" and seen < _PAGE * _MAX_PAGES
    hn = await hn_run.finish(complete=complete)
    cleared = 0
    if complete and seen:
        cleared = await _clear_unmatched(session, server, matched_ids,
                                         peers=await enabled_peers(session, OcsServer))
    elif matched_ids:
        # 增量同步也要清：這輪對到新位址的電腦，舊位址上的盤點資料（2026-10-12）
        cleared = await _clear_moved(session, server, matched_ids, peers=await enabled_peers(session, OcsServer))

    # 增量游標推進到這次同步的當下（epoch）。因為 lastupdate 是嚴格大於、下次會退重疊窗。
    if incremental:
        server.last_incremental_epoch = int(now.timestamp())
    server.last_sync_at = now
    server.last_success_at = now
    server.last_error = (f"hostname cleanup skipped: {hn['breaker']}" if hn["breaker"] else None)
    server.last_cost = {
        "mode": mode, "computers": seen, "matched_ips": matched,
        "seconds": round(time.monotonic() - t0, 2),
    }
    if cleared:
        server.last_cost["cleared_ips"] = cleared
    if future:
        server.last_cost["future_lastdate"] = {
            "count": len(future), "max_hours": round(max(future).total_seconds() / 3600),
            "timezone": server.timezone}
    return server.last_cost


async def _clear_moved(session: AsyncSession, server: OcsServer, matched_ids: set[uuid.UUID],
                       *, peers: int) -> int:
    """這一輪對到某個位址的 OCS 電腦，殘留在**別的位址**上的舊盤點資料清掉。

    電腦換了 IP（DHCP）時，新位址會被寫上，舊位址卻要等完整同步才清 —— OCS 2.11 起第一次之後每次都是增量，
    完整同步永遠不再發生，舊位址就一直頂著這台的 OS 與盤點編號（2026-10-12 全面殘留檢查）。
    範圍保護跟完整同步的清理一樣：IP 上的 OCS 欄位不記是哪一套寫的，有好幾套又沒設範圍就不清。
    """
    from app.services.agent_scope import scope_uuids
    scope = scope_uuids(server)
    if peers > 1 and not scope:
        return 0
    await session.flush()     # 正式環境 autoflush 關閉：這輪剛寫的 ocs_id 要先送出才查得到
    ids = [i for (i,) in (await session.execute(
        select(IPAddress.ocs_id).where(in_values(IPAddress.id, list(matched_ids)),
                                       IPAddress.ocs_id.isnot(None)))).all()]
    if not ids:
        return 0
    stmt = select(IPAddress).where(in_values(IPAddress.ocs_id, ids), not_in_values(IPAddress.id, matched_ids))
    if scope:
        stmt = stmt.where(in_values(IPAddress.subnet_id, scope))
    rows = (await session.execute(stmt)).scalars().all()
    for ip in rows:
        ip.ocs_id = ip.ocs_tag = ip.ocs_agent = ip.ocs_notes = ip.ocs_hw = None
        ip.os_ocs = None
        ip.last_seen_ocs = None
    return len(rows)


async def _clear_unmatched(session: AsyncSession, server: OcsServer, matched_ids: set[uuid.UUID],
                           *, peers: int) -> int:
    """完整同步後：先前掛著 OCS 資料、這一輪沒對到的 IP → 清掉 OCS 欄位。

    以前永遠不清：電腦從 OCS 刪掉、機器換掉、或對錯到 BMC 的位址，IP 就一直
    頂著別台機器的 OS 與盤點編號（2026-09-26 稽核）。IP 上的 OCS 欄位不記是哪一套 OCS 寫的，
    所以有好幾套時只清這套「限定子網路範圍」內的；沒設範圍又有多套 → 不清（會清到別套的）。
    """
    from app.services.agent_scope import scope_uuids
    scope = scope_uuids(server)
    if peers > 1 and not scope:
        return 0
    stmt = select(IPAddress).where(IPAddress.ocs_id.isnot(None))
    if matched_ids:
        stmt = stmt.where(not_in_values(IPAddress.id, matched_ids))
    if scope:
        stmt = stmt.where(in_values(IPAddress.subnet_id, scope))
    rows = (await session.execute(stmt)).scalars().all()
    # 斷路器（同主機名稱）：一次要清掉一大半，多半是 API 回傳不完整，不是真的換了那麼多台
    from app.services.hostname_reports import BREAKER_MIN, BREAKER_RATIO
    if len(rows) > BREAKER_MIN and len(rows) > (len(rows) + len(matched_ids)) * BREAKER_RATIO:
        return 0
    for ip in rows:
        ip.ocs_id = ip.ocs_tag = ip.ocs_agent = ip.ocs_notes = ip.ocs_hw = None
        ip.os_ocs = None
        ip.last_seen_ocs = None
    return len(rows)


# ─────────────────── 憑證 helper（給 API 存密碼用） ───────────────────

def set_api_password(server: OcsServer, password: str | None) -> None:
    """把 REST 密碼寫進實例（AES-GCM，AAD 綁 id）。None/空 → 清掉。"""
    if not password:
        server.api_password_enc = None
        server.api_password_nonce = None
        return
    enc, nonce = encrypt_secret(password, aad=_aad(server.id))
    server.api_password_enc = enc
    server.api_password_nonce = nonce


# ─────────────────── 整合頁：代理數／未裝 Agent 的 IP（比照 Wazuh） ───────────────────


async def list_agents(session: AsyncSession) -> list[dict[str, Any]]:
    """OCS 盤點到的電腦，**一台一筆**。

    OCS 沒有每台電腦的記錄表 —— 盤點資料是依網卡 MAC 補進 IP，而一台電腦常有好幾個 IP
    （實機一輪同步是 14 台電腦、比對到 60 個 IP）。直接數 IP 會讓「代理數」大好幾倍，
    所以依 `ocs_id` 彙整；沒有 `ocs_id` 的舊資料（0142 以前寫入）各自算一台。
    """
    from sqlalchemy import or_

    rows = (await session.execute(
        select(IPAddress).where(or_(IPAddress.ocs_id.isnot(None),
                                    IPAddress.last_seen_ocs.isnot(None)))
        .order_by(IPAddress.ip)
    )).scalars().all()
    groups: dict[Any, dict[str, Any]] = {}
    for a in rows:
        key = ("ocs", a.ocs_id) if a.ocs_id is not None else ("ip", a.id)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"ocs_id": a.ocs_id, "name": None, "ips": [], "ip_address_ids": [],
                               "os": None, "agent_version": None, "tag": None,
                               "last_inventory": None}
        g["ips"].append(str(a.ip).split("/", 1)[0])
        g["ip_address_ids"].append(str(a.id))
        g["name"] = g["name"] or a.hostname
        # 欄位取最新一次盤點那個 IP 的值：同一台電腦的幾個 IP 可能在不同輪被更新
        if a.last_seen_ocs and (g["last_inventory"] is None or a.last_seen_ocs > g["last_inventory"]):
            g["last_inventory"] = a.last_seen_ocs
            g["os"], g["agent_version"], g["tag"] = a.os_ocs, a.ocs_agent, a.ocs_tag
        else:
            g["os"] = g["os"] or a.os_ocs
            g["agent_version"] = g["agent_version"] or a.ocs_agent
            g["tag"] = g["tag"] or a.ocs_tag
    out = list(groups.values())
    out.sort(key=lambda g: g["last_inventory"] or datetime.min.replace(tzinfo=UTC), reverse=True)
    return out


async def find_missing_agents(
    session: AsyncSession, *, hostnamed_only: bool = True,
    subnet_ids: list[uuid.UUID] | None = None,
) -> list[dict[str, Any]]:
    """應裝 OCS agent 卻從來沒被盤點過的 IP（比照 Wazuh 的 find_missing_agents）。

    `hostnamed_only`=True 只看有主機名稱的 —— 沒名字的多半是 DHCP 臨時位址或設備。
    `subnet_ids` 給了就只看這些子網路（問「某網段有誰沒裝」時必須給）。
    """
    stmt = select(IPAddress.id, IPAddress.ip, IPAddress.hostname).where(
        IPAddress.ocs_id.is_(None), IPAddress.last_seen_ocs.is_(None))
    if hostnamed_only:
        stmt = stmt.where(IPAddress.hostname.is_not(None), IPAddress.hostname != "")
    if subnet_ids is not None:
        if not subnet_ids:
            return []
        stmt = stmt.where(in_values(IPAddress.subnet_id, subnet_ids))
    rows = (await session.execute(stmt.order_by(IPAddress.ip))).all()
    return [{"ip_address_id": str(rid), "ip": str(rip).split("/", 1)[0] if rip else None,
             "hostname": hostname} for rid, rip, hostname in rows]
