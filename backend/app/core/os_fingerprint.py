"""OS 指紋正規化：把雜亂的原始 OS 字串（nmap -O / TTL / SNMP sysDescr）對應成
固定的「家族 key」，前端再依 key 配 SVG icon，顯示才一致美觀。

前後端共用同一份家族 key（前端 osIcons.ts）。比不到一律回 "unknown"。
"""

from __future__ import annotations

# 家族 key → 雙語 label（icon 在前端 osIcons.ts 依 key 對應）
OS_FAMILIES: dict[str, dict[str, str]] = {
    "windows": {"label_en": "Windows", "label_zh": "Windows"},
    "linux": {"label_en": "Linux", "label_zh": "Linux"},
    "macos": {"label_en": "macOS", "label_zh": "macOS"},
    "bsd": {"label_en": "BSD / firewall", "label_zh": "BSD / 防火牆"},
    "ios": {"label_en": "iOS", "label_zh": "iOS"},
    "android": {"label_en": "Android", "label_zh": "Android"},
    "network": {"label_en": "Network device", "label_zh": "網路裝置"},
    "printer": {"label_en": "Printer", "label_zh": "印表機"},
    "storage": {"label_en": "NAS / storage", "label_zh": "NAS / 儲存"},
    "hypervisor": {"label_en": "Hypervisor", "label_zh": "虛擬化平台"},
    "unknown": {"label_en": "Unknown", "label_zh": "未知"},
}

# 比對順序很重要：較精準/較窄的關鍵字放前面（如 pfSense/OPNsense 要先於泛 BSD；
# ESXi 要先於泛 Linux）。每組 = (family, [關鍵字…])，關鍵字比對小寫子字串。
_RULES: list[tuple[str, list[str]]] = [
    ("hypervisor", ["esxi", "vmware", "proxmox", "hyper-v", "xenserver", "vsphere"]),
    ("storage", ["synology", "qnap", "truenas", "freenas", "netapp", "diskstation", "unraid"]),
    ("bsd", ["pfsense", "opnsense", "freebsd", "openbsd", "netbsd", "bsd"]),
    ("network", ["cisco", "mikrotik", "routeros", "junos", "juniper", "fortinet", "fortigate",
                 "ubiquiti", "edgeos", "aruba", "ios-xe", "ios xe", "switch", "router",
                 "huawei", "zyxel", "tp-link", "draytek", "h3c"]),
    ("printer", ["jetdirect", "printer", "laserjet", "officejet", "brother", "kyocera", "epson"]),
    ("macos", ["mac os", "macos", "os x", "darwin"]),
    ("ios", ["iphone", "ipad", "ios "]),
    ("android", ["android"]),
    ("windows", ["windows", "microsoft", "win32", "winnt"]),
    ("linux", ["linux", "ubuntu", "debian", "centos", "red hat", "redhat", "fedora",
               "rocky", "alma", "suse", "openwrt", "raspbian", "android-x86"]),
]


def normalize_os(raw: str | None) -> str:
    """原始 OS 字串 → 家族 key。比不到回 'unknown'。"""
    if not raw:
        return "unknown"
    s = raw.strip().lower()
    if not s:
        return "unknown"
    for family, kws in _RULES:
        if any(kw in s for kw in kws):
            return family
    return "unknown"


def family_from_ttl(ttl: int | None) -> str | None:
    """純 TTL 粗略推測（無 nmap 時的後備）：64→linux 系、128→windows、255→network。
    僅當其他探測都拿不到 OS 字串時才用，回 None 表示不猜。"""
    if ttl is None:
        return None
    # 容忍經過數個 hop 的衰減
    if 0 < ttl <= 64:
        return "linux"
    if 64 < ttl <= 128:
        return "windows"
    if 128 < ttl <= 255:
        return "network"
    return None


def families_for_api() -> list[dict[str, str]]:
    return [{"key": k, **v} for k, v in OS_FAMILIES.items()]
