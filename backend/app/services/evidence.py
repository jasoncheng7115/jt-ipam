"""證據契約：每個「來源」在說什麼，以及它的話能不能撐到明天。

## 為什麼需要這個檔案

jt-ipam 的每一個屬性（主機名稱、MAC、OS、裝置名稱、型號、存活狀態）都可能同時被
好幾個來源回報，而它們的**性質差很多**：

- 人工填的不會過期，但也不會自己更新
- 掃描代理探測到的有明確時間，過了就該失效
- **ARP／FDB 學到的沒有時間概念** —— LibreNMS 的 ARP API 連時間欄位都不回，
  我們只能因為「這筆還在清單裡」就蓋上同步當下的時鐘

第三點的代價是實際發生過的：一台關機好幾週的 VM 顯示「52 天全綠」，因為某台 AP 的
ARP 快取一直留著那筆記錄（v0.5.206/0.5.207 修）。當時的問題不是少了某個功能，而是
**「這個來源的證據會不會過期」這件事沒有任何地方寫下來**，散在各個模組的字串比對裡
（`if "scanner" in status`）。新增一個來源時，沒有任何東西會提醒你回答這個問題。

所以這裡把它變成契約：**每個來源都必須登記 tier 與 aging**，
未登記的來源一律視為「不會過期」（保守：不知道能撐多久，就不要拿它宣稱現在還活著）。

## tier 的意思

| tier | 意思 | 例 |
|---|---|---|
| `asserted` | 人宣告的意圖，不是觀測 | manual |
| `probed` | 我們主動去探、當場得到回應 | scanner |
| `monitored` | 第三方系統維護、且**由對方負責過期**的狀態證據 | librenms、zabbix、checkmk、wazuh、防火牆的 ARP 表／VPN 連線 |
| `learned` | 被動學到的對應關係，**不代表現在還成立** | arp、fdb、dns、dhcp、虛擬化平台的設定 |

`learned` 一律 `aging=False`：它們回答的是「這個對應曾經被學到」，不是「現在活著」。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 人宣告的意圖（不是觀測結果）
TIER_ASSERTED = "asserted"
#: 我們主動探測、當場得到回應
TIER_PROBED = "probed"
#: 第三方監控系統回報的裝置狀態
TIER_MONITORED = "monitored"
#: 被動學到的對應關係 —— 不代表現在還成立
TIER_LEARNED = "learned"


@dataclass(frozen=True)
class Source:
    """一個證據來源的契約。

    `aging`：這個來源給的資料有沒有「時間概念」——
    也就是「超過一段時間沒再回報，就該視為失效」這件事成不成立。
    只有 `aging=True` 的來源可以用來宣稱「現在是上線的」。
    """

    name: str
    tier: str
    aging: bool


def _s(name: str, tier: str, aging: bool) -> Source:
    return Source(name=name, tier=tier, aging=aging)


#: 全站來源登記表。新增整合時**必須**在這裡登記，否則守門測試會擋下來。
SOURCES: dict[str, Source] = {s.name: s for s in (
    # 人工
    _s("manual", TIER_ASSERTED, aging=False),

    # 主動探測：掃描代理實際打封包，有明確時間
    _s("scanner", TIER_PROBED, aging=True),

    # 第三方監控：裝置狀態由對方輪詢維護，會過期
    _s("librenms", TIER_MONITORED, aging=True),
    _s("zabbix", TIER_MONITORED, aging=True),
    _s("checkmk", TIER_MONITORED, aging=True),
    _s("wazuh", TIER_MONITORED, aging=True),

    # 被動學到的對應：不代表現在還活著
    # ARP 是這裡最容易誤用的一個 —— LibreNMS 的 ARP API 不回時間，
    # 來源設備（AP／路由器）的快取不老化，關機的機器也會一直看起來剛出現。
    _s("arp", TIER_LEARNED, aging=False),
    _s("fdb", TIER_LEARNED, aging=False),
    _s("dns", TIER_LEARNED, aging=False),
    _s("netbios", TIER_LEARNED, aging=False),
    _s("mdns", TIER_LEARNED, aging=False),
    _s("snmp", TIER_LEARNED, aging=False),

    # 防火牆／DHCP：回報的是設定與租約，不是活性
    _s("opnsense", TIER_LEARNED, aging=False),
    _s("pfsense", TIER_LEARNED, aging=False),
    _s("fortigate", TIER_LEARNED, aging=False),
    _s("paloalto", TIER_LEARNED, aging=False),
    _s("checkpoint", TIER_LEARNED, aging=False),
    _s("mikrotik", TIER_LEARNED, aging=False),
    # OCS Inventory：agent 自報的資產盤點（hostname／OS 觀測）。學到的事實，不過期。
    _s("ocs", TIER_LEARNED, aging=False),
    # RustDesk：客戶端自報的主機名稱（只收已對應到 IP 記錄的裝置）。學到的事實，不過期
    _s("rustdesk", TIER_LEARNED, aging=False),
    # ── 防火牆給的逐來源證據 ────────────────────────────────────
    # 為什麼要拆到這麼細：這些資料原本全都被寫進 `last_seen_scanner`，於是畫面上
    # 出現「online (scanner)」卻根本沒有掃描代理。來源看不出來，就沒辦法只採信
    # 其中一部分 —— 而它們的可信度差很多：
    #
    #   arp:<廠牌>   防火牆自己的 ARP 表 → aging=True。**理由不是「還在表裡」**，
    #                而是**條目自己帶時間**：OPNsense／pfSense 給 `expires`（剩餘秒數，
    #                從 FreeBSD 的 max_age 1200 往下數，實機兩台皆是）、FortiOS 給 `age`、
    #                PAN-OS 給 `ttl`。我們用它推回「真正被更新的時間」再記錄，
    #                所以一筆快過期的條目不會被講成「剛剛才看到」。
    #                （靜態／永久項目與已標記 expired 的條目一律跳過，見 arp_seen.py）
    #   vpn:<廠牌>   目前已建立的 VPN 連線／隧道。對方此刻連著才會出現 → aging=True。
    #   lease:<廠牌> DHCP 租約。租期常常是好幾天，比機器的開機時間長得多 ——
    #                「有租約」不等於「現在活著」→ aging=False，預設不採信。
    #
    # 對照組：`arp:librenms`（＝舊的籠統 "arp"）不會過期。**差別就在有沒有時間**：
    # LibreNMS 的 ARP API 一個時間欄位都不回，我們只能蓋上同步當下的時鐘，而來源設備
    # （AP／路由器）的快取不老化，關機數週的機器也會一直看起來剛剛才出現 ——
    # 那正是 0.5.206 那次「52 天全綠」事故的成因。同樣叫 ARP，可信度完全不同。
    _s("arp:opnsense", TIER_MONITORED, aging=True),
    _s("arp:pfsense", TIER_MONITORED, aging=True),
    _s("arp:fortigate", TIER_MONITORED, aging=True),
    _s("arp:paloalto", TIER_MONITORED, aging=True),
    # Check Point（第二階段，選用的 run-script）：閘道上 `ip -s neigh show` 的 `used a/b/c`，
    # 第二個數字是「幾秒前確認過」→ 推回真正被確認的時間（跟 FortiOS 的 age 同一個道理）；
    # 沒有計時資訊時只收 REACHABLE（跟 MikroTik 同一個判斷），PERMANENT／NOARP 不記（services/checkpoint_gaia.py）。
    _s("arp:checkpoint", TIER_MONITORED, aging=True),
    # MikroTik 是唯一**不是靠時間欄位**取得資格的一家：RouterOS 的 `/ip/arp`
    # 沒有 age／TTL／到期秒數（唯讀屬性只有 complete / dhcp / dynamic / invalid /
    # status / VRF）。它給的是**鄰居狀態**，而 `reachable` 的定義就是「在可達性
    # 逾時（約 30 秒）內被確認過」—— 我們只收這個狀態，其餘（stale / delay /
    # probe / permanent…）一律不記，所以蓋上同步當下的時間仍然站得住。
    # ⚠️ 若哪天改成收 `stale`，這一列就必須改回 aging=False。
    _s("arp:mikrotik", TIER_MONITORED, aging=True),
    _s("arp:librenms", TIER_LEARNED, aging=False),
    # SNMP 收集（掃描代理讀路由器／L3 交換器的 ARP 表，0217）。同樣叫 ARP，依「有沒有時間」分成兩個來源：
    #   arp:snmp       ipNetToMediaTable（舊 MIB，沒有任何狀態或時間）以及新表裡不是 reachable 的項目 →
    #                  跟 arp:librenms 同一個毛病（快取不老化，關機的機器也一直在），只補 MAC、不算上線
    #   arp:snmp_live  IP-MIB ipNetToPhysicalTable 的 ipNetToPhysicalState＝reachable(1) 的動態項目：
    #                  定義就是「在可達性逾時內被確認過」，跟 arp:mikrotik 只收 reachable 同一個道理 → 會過期
    #   ⚠️ 若哪天改成 stale 也算 live，arp:snmp_live 必須改回 aging=False。
    _s("arp:snmp", TIER_LEARNED, aging=False),
    _s("arp:snmp_live", TIER_MONITORED, aging=True),
    _s("vpn:opnsense", TIER_MONITORED, aging=True),
    _s("vpn:pfsense", TIER_MONITORED, aging=True),
    _s("vpn:fortigate", TIER_MONITORED, aging=True),
    _s("vpn:mikrotik", TIER_MONITORED, aging=True),
    _s("lease:opnsense", TIER_LEARNED, aging=False),
    _s("lease:pfsense", TIER_LEARNED, aging=False),
    _s("lease:fortigate", TIER_LEARNED, aging=False),
    _s("lease:paloalto", TIER_LEARNED, aging=False),
    _s("lease:checkpoint", TIER_LEARNED, aging=False),
    _s("lease:mikrotik", TIER_LEARNED, aging=False),
    _s("windows_dhcp", TIER_LEARNED, aging=False),
    _s("kea_dhcp", TIER_LEARNED, aging=False),
    _s("isc_dhcp", TIER_LEARNED, aging=False),
    # Technitium DHCP：租約只標旗標、MAC、主機名稱，不當上線證據（比照 Kea）
    _s("technitium", TIER_LEARNED, aging=False),
    # ISOinsight：DHCP 租約（依時間推定的租約狀態），不當上線證據
    _s("isoinsight", TIER_LEARNED, aging=False),
    _s("adguard", TIER_LEARNED, aging=False),

    # ── 設備自己的介面位址（客戶 2026-10-14：多 LAN 的閘道不回 ping 卻在用，顯示紅燈）──
    #   iface:<整合>  整合回報「這個位址設定在我某個啟用中的介面上」。只在整合的 API 這一輪有回應、
    #                 介面是啟用（LibreNMS 再加上設備在線、埠 up）時才蓋上同步當下的時間 → 設備掛了
    #                 同步失敗、不再蓋時間，證據自然過期 → aging=True，可以宣稱上線
    #   device        「上線狀態跟著設備」：每輪把同一台設備其他位址最新的**會過期**證據搬過來
    #                 （services/device_liveness），時間是原本那筆證據的時間 → 同樣會過期
    _s("iface:opnsense", TIER_MONITORED, aging=True),
    _s("iface:pfsense", TIER_MONITORED, aging=True),
    _s("iface:fortigate", TIER_MONITORED, aging=True),
    _s("iface:mikrotik", TIER_MONITORED, aging=True),
    _s("iface:librenms", TIER_MONITORED, aging=True),
    # SNMP 收集：介面位址（IP-MIB ipAddressTable／ipAddrTable）且該介面 ifOperStatus＝up，這一輪讀得到才蓋時間
    _s("iface:snmp", TIER_MONITORED, aging=True),
    _s("device", TIER_MONITORED, aging=True),

    # 虛擬化平台：回報的是「設定上這台 VM 有這個 IP」
    _s("proxmox", TIER_LEARNED, aging=False),
    _s("hyperv", TIER_LEARNED, aging=False),
    # OpenStack：Neutron port 的配置 IP（控制平面），不代表客體在線
    _s("openstack", TIER_LEARNED, aging=False),
    _s("esxi", TIER_LEARNED, aging=False),

    # 匯入來源
    _s("phpipam", TIER_ASSERTED, aging=False),
)}


#: 實際會餵進上線判定的來源（在 ip_addresses 上有對應的 last_seen_* 欄位）。
#: 這是「有登記」與「真的有資料進來」的交集 —— 不要把只登記、沒接線的來源列進設定頁。
LIVENESS_SOURCES: tuple[str, ...] = (
    "scanner", "librenms", "wazuh", "zabbix", "checkmk",
    # 舊的籠統 "arp" 保留在清單裡：既有站台存下來的設定不會因為升級被丟掉。
    # 它對應的是 LibreNMS 寫的 `last_seen_arp`，語意等同 `arp:librenms`。
    "arp",
    # `arp:librenms` 有登記（見上），但**不列進設定頁** —— 它就是上面那個 "arp"，
    # 兩個都列只會讓人以為是兩種不同的證據。
    "arp:opnsense", "arp:pfsense", "arp:fortigate", "arp:paloalto", "arp:mikrotik", "arp:checkpoint",
    "vpn:opnsense", "vpn:pfsense", "vpn:fortigate", "vpn:mikrotik",
    "lease:opnsense", "lease:pfsense", "lease:fortigate", "lease:paloalto",
    "lease:mikrotik", "lease:checkpoint",
    "iface:opnsense", "iface:pfsense", "iface:fortigate", "iface:mikrotik", "iface:librenms", "device",
    # SNMP 收集（0217）：arp:snmp 不會過期（預設不採信，跟 "arp" 一樣可以自己勾）
    "arp:snmp", "arp:snmp_live", "iface:snmp",
)

#: 2026-10-14 才加入的來源。在這之前存過設定的站台（存的值沒有 `known`）不認得它們，
#: 讀設定時把其中會過期的自動算進去：新功能升級後要自己生效，不可以要人再去勾一次
SOURCES_ADDED_LATER: tuple[str, ...] = (
    "iface:opnsense", "iface:pfsense", "iface:fortigate", "iface:mikrotik", "iface:librenms", "device",
    # 2026-10-15 SNMP 收集（0217）：會過期的兩個升級後自動採信；arp:snmp 不會過期，本來就不在預設裡
    "arp:snmp_live", "iface:snmp",
    # 2026-11-14 Checkmk：跟 Zabbix 一樣是會過期的監控證據
    "checkmk",
)

#: 存在 `ip_addresses.arp_seen` JSONB 裡的那些（其餘各有自己的 last_seen_* 欄位）。
DETAILED_SOURCES: tuple[str, ...] = tuple(
    s for s in LIVENESS_SOURCES if ":" in s and s != "arp:librenms"
)


def default_liveness_sources() -> list[str]:
    """預設採信哪些：會過期的才算數。ARP 因此自動落在預設之外，不必另外寫死清單。"""
    return [s for s in LIVENESS_SOURCES if is_aging(s)]


def get_source(name: str | None) -> Source | None:
    return SOURCES.get((name or "").strip().lower())


def is_aging(name: str | None) -> bool:
    """這個來源的證據會不會過期。**未登記者一律視為不會過期**（保守）。"""
    src = get_source(name)
    return bool(src and src.aging)


def tier_of(name: str | None) -> str:
    src = get_source(name)
    return src.tier if src else TIER_LEARNED


def aging_sources() -> list[str]:
    """可以用來宣稱「現在上線」的來源。"""
    return sorted(s.name for s in SOURCES.values() if s.aging)


#: `effective_status` 長成 `online (scanner)` 這種；把括號裡的來源取出來。
def source_from_status(status: str | None) -> str | None:
    """從狀態字串取出來源後綴，取不到回 None（例如純 `online` / `offline`）。"""
    if not status:
        return None
    text = status.strip().lower()
    start = text.find("(")
    end = text.find(")", start + 1)
    if start < 0 or end < 0:
        return None
    inner = text[start + 1:end].strip()
    return inner or None
