# jt-ipam v1.0.19

[![License](https://img.shields.io/github/license/jasoncheng7115/jt-ipam?color=blue)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/jasoncheng7115/jt-ipam)](https://github.com/jasoncheng7115/jt-ipam/commits/main)
[![Stars](https://img.shields.io/github/stars/jasoncheng7115/jt-ipam?style=flat)](https://github.com/jasoncheng7115/jt-ipam/stargazers)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![Vue](https://img.shields.io/badge/Vue-3-42b883?logo=vuedotjs&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![OWASP](https://img.shields.io/badge/OWASP-Top%2010%3A2025-000000)

**🌐 [專案介紹網站 / Project site →](https://jasoncheng7115.github.io/jt-ipam/?lang=zh-TW)**

> 可自架、以整合為核心的 IPAM，操作流程沿襲 phpIPAM 使用者熟悉的風格、全新獨立開發，整合多家 DNS Server、LibreNMS、OPNsense、pfSense、FortiGate、Palo Alto、Check Point、MikroTik RouterOS、Windows DHCP Server、獨立的 Kea 與 ISC DHCP、Technitium DNS/DHCP、Proxmox VE、VMware ESXi / vCenter、Hyper-V / SCVMM、CubeCOS / OpenStack、Wazuh、Zabbix 與本地 AI。
>
> 作者：Jason Tools Co., Ltd.（節省工具箱）｜授權：AGPL-3.0｜English: [README.md](README.md)｜日本語: [README_ja.md](README_ja.md)

---

## 為什麼是 jt-ipam？

phpIPAM 老使用者幾乎零學習成本；以現代技術全新打造（非基於 phpIPAM 程式碼）。深度整合：

- **DNS**：PowerDNS、BIND 9、OPNsense Unbound、Univention UCS、Microsoft Windows DNS、Technitium DNS Server（唯讀：讀取正反解紀錄與一致性，不會寫入 DNS 伺服器；Windows DNS 走 WinRM，預設強制 NTLM 加密的 HTTP 5985，也可走 HTTPS 5986）
- **LibreNMS**：裝置同步、ARP / FDB 抓取、上線狀態互補、自動加入監控
- **SNMP 收集（掃描代理）**：沒有 LibreNMS 的網路，由負責子網路的掃描代理以唯讀 SNMP v2c/v3 讀取你指派的網路設備（憑證集中管理、API 不回傳）：路由器的 ARP 表補上隔著路由器看不到的 MAC、交換器的 MAC 表（Q-BRIDGE / BRIDGE-MIB，Cisco 逐 VLAN）找出主機接在哪個埠、LLDP / CDP 鄰居在拓樸畫出交換器之間的連線、介面寫進裝置連接埠。代理安裝腳本會一併安裝 net-snmp 用戶端工具（`snmp` / `net-snmp-utils` / `net-snmp-tools`）
- **Zabbix**：監控面的唯讀補充，涵蓋主機↔IP 對應、把存活狀態當作實際狀態的額外證據、維護狀態，以及**未監控的位址**（IPAM 有主機名稱、Zabbix 卻沒在看的位址）。ARP/FDB 仍以 LibreNMS 為主，那不在 Zabbix 的內建資料裡
- **基礎設施**：Proxmox VE、**VMware ESXi / vCenter（Beta）**，同一套設定同時涵蓋單機 ESXi 與 vCenter，走 vSphere API 唯讀盤點虛擬機、網卡與 IP，與 Proxmox 寫進同一組虛擬化資料表；**Microsoft Hyper-V（Beta）**，走 WinRM（PowerShell，NTLM 或 Kerberos）唯讀盤點虛擬機、網卡、VLAN、IP 與客體作業系統（KVP），可直連主機（容錯移轉叢集要填每個節點，NTLM 不能在節點之間轉連）或經 System Center VMM，主機的網卡與虛擬交換器建成該主機裝置的連接埠；**Bigstack CubeCOS / OpenStack（Beta）**，走 Keystone v3（帳號密碼或應用程式憑證）、Nova、Neutron 唯讀盤點虛擬機、Neutron 網卡（port）、子網路與浮動 IP（只送 GET，唯一的 POST 是向 Keystone 取得 token），寫進同一組虛擬化資料表；VM 的位址只有在 Neutron 子網路已對應到 jt-ipam 子網路時才連到 IP 記錄（CIDR 在這個雲裡唯一、而且範圍內恰好一個 jt-ipam 子網路是這個 CIDR 才自動對應，不同專案的重疊網段留在「待對應」由管理員手動指定），浮動 IP 跟著網卡顯示、不當成客體位址，CubeCOS 延伸另外讀取節點清單；Wazuh、OPNsense / pfSense（別名 / 規則 / NAT 同步），**FortiGate**，透過 FortiOS REST API 唯讀同步（DHCP 租約與發放範圍、ARP、IPsec 通道與 SSL-VPN 連線、防火牆政策、NAT、位址物件；支援多 VDOM），**Palo Alto（Beta）**，透過 PAN-OS API 唯讀同步（ARP、DHCP 租約、含 App-ID 的安全政策、NAT、位址物件；支援多 vsys），**Check Point（Beta）**，透過管理伺服器的 R81.20 Management API 唯讀同步（閘道清單、網路物件、含內嵌層與命中數的存取規則、目的地 NAT；支援 Multi-Domain，每個網域各登入一次，只開唯讀工作階段；每台閘道另可接 Gaia API，唯讀讀 DHCP 伺服器設定，並用寫死的讀取指令讀 ARP 表與 DHCP 租約，帳號不能執行指令時自動略過），以及 **MikroTik RouterOS（Beta）**，透過 RouterOS v7 REST API 唯讀同步（防火牆規則 filter/mangle/NAT、address-list、DHCP 租約與發放範圍、VPN、ARP）。MikroTik 常是站台的主力路由器，所以同步序列執行、區段之間停頓、CPU 超過門檻就停掉本輪剩下的區段，並有回應大小上限（RouterOS 的 REST 沒有分頁）；重的區段預設關閉，連線診斷會回報每支端點的列數與耗時
- **DHCP**：各家各自設定，OPNsense（Kea/dnsmasq/ISC）與 pfSense 透過各自的 REST API 同步租約、發放範圍與固定分配；**Windows DHCP Server（Beta）** 走 WinRM + PowerShell 唯讀（只跑 `Get-*`，需 WinRM 可連線，預設 HTTP 5985 並以 NTLM 加密，因為 Windows Server 防火牆預設封鎖 5986）；**獨立的 Kea** 走它的 JSON 控制 API（控制代理，或 Kea 3.0 起 DHCP 伺服器自己的 HTTP 控制通道；租約需要 lease_cmds）；**Technitium DHCP** 走 Technitium 的 HTTP API，token 只需檢視權限（範圍扣掉排除區間、保留、租約，以及每個範圍發給用戶端的閘道/DNS，IP 變更評估會比對）；**獨立的 ISC DHCP**（isc-dhcp-server 沒有能列出租約的 API）由裝在 DHCP 主機上的掃描代理在本機解析 dhcpd.conf/dhcpd.leases，只回報解析後的範圍、固定分配與有效租約。落在發放範圍內的位址會在 IP 清單與詳細資料標示出來；DHCP 伺服器設定的排除區間（Windows DHCP、FortiGate、Technitium、Check Point）會先從發放範圍扣掉，被排除的位址另外標示「DHCP 排除」；**ISOinsight（Beta）** 登入設備的 HTTP(S) 介面唯讀讀取 DHCP 租約。
- **Wazuh**：代理清單（狀態、作業系統、CVE 數、SCA）對到 IP，並列出有主機名稱卻沒有啟用中代理的 IP
- **OCS Inventory NG**：電腦資產依網卡 MAC 對到既有 IP（不會新建記錄），裝置頁顯示硬體資訊，並列出 OCS 從未盤點過的 IP
- **RustDesk Server（開源版）**：RustDesk 主機上的專用 RustDesk 代理（一行指令安裝）唯讀讀取已註冊的裝置 ID 與線上狀態（私鑰檔絕不讀取；jt-ipam 與主機管理員兩邊都允許時，也可以刪除舊註冊）；登記 IP 明確且最近上線才對應到 IP 記錄，IP 頁可在網頁裡直接連線操作（相容 RustDesk 的網頁連線，加密在瀏覽器裡完成），或叫出電腦上的 RustDesk 客戶端並帶入伺服器與公鑰（網址不含密碼）；另收客戶端自己的回報，得到連線稽核、主機名稱 / OS / 使用者與暴力破解告警，不用改任何客戶端設定
- **Graylog**：提供 IP→主機名稱/FQDN 的 DSV 對照表端點，供 Graylog「DSV File from HTTP」資料配接器抓取
- **本地 AI**：LLM Server 自然語言查詢 + 語意搜尋（預設自架、資料不外送；也可明確改接 OpenAI 相容端點），並提供 MCP server（stdio / Streamable HTTP）；實測搭配 `gemma4:26b` 效果良好。資安面：**防火牆規則異動偵測**（OPNsense、pfSense、FortiGate、Palo Alto、Check Point、MikroTik 的規則每輪同步做快照 diff，半夜多出一條放行規則會通知管理員）、**IP 鑑識問答**（在 AI 對話問「這個 IP 上週是誰」，回欄位級異動＋ARP/MAC＋各來源主機名稱的證據時間軸）、**未授權 IP 的 AI 鑑識卡**（把 OUI/主機名稱/交換器埠彙整成「這最可能是什麼設備＋下一步查哪」的判讀，證據定界防 prompt-injection）

**IP 變更評估**：改 IP、除役、交換器維護、節點停機之前，把所有整合裡引用到這個位址或裝置的地方（DNS、DHCP、防火牆規則與 NAT、監控、虛擬化、整合連線位址、憑證…）與受影響的服務列出來，分成阻擋、需覆核、需變更與資料不足；附依影響分層的關係圖、覆核流程、待辦與復原步驟，報告可匯出 PDF、Word、OpenDocument，清單匯出 Excel。只讀，不會改任何設備。

**掃描代理**：裝在各網段的輕量代理，回報上線狀態、MAC、名稱（反查、NetBIOS、mDNS）與 OS，伺服器派工、代理自動更新，並有負載面板與超載通知。大子網路照實際遮罩登記也掃得動（例如 /8）：已登記的 IP 與最近 ARP 看過的位址每一輪都掃，其餘位址在背景慢慢探索新設備，不必為了掃描把網段切小。每個 IP 的主機名稱看得出是哪一台代理或哪個整合回報、最後什麼時候看到。

也內建：**瀏覽器內遠端連線管理**，提供 SSH 終端機、**SFTP 檔案瀏覽器**（免另開工具就能上下傳檔案），外加 RDP、VNC 桌面、**BMC 序列主控台**（IPMI SOL，不經作業系統的獨立連線）與相容 RustDesk 的**網頁連線**（加密在瀏覽器裡完成，含檔案傳輸，不用裝 RustDesk 客戶端）（BMC 為 **Beta**），全部在瀏覽器內，狀態列顯示連線時間，連線帳密預設不儲存、可選用**個人加密憑證金庫**（by-user、AES-GCM），**跳板主機**（後端連不到的站台，主控台可改走「後端 → 跳板 → 目標」；出口設在子網路、個別 IP 可覆寫，主機金鑰必須先釘選才允許連線）、物件層級 RBAC、單次 ticket→WebSocket 連線與完整稽核（每次連線都記下連了多久；RDP 與 VNC 走 **guacd**，這是 jt-ipam 逐 OS 版本預編、安裝時自動裝上的必要元件；舊的純 Python 引擎 aardwolf 改為選用的備用引擎；SSH 也可以改用 guacd）、**申請審核流程**（IP 申請與 IP 變更評估各自設定審核關卡：多關卡會簽 / 依序關卡，站內 + Email 通知）、**DNS 記錄檢視**（找出沒有對應 IPAM 的記錄）、**掃描代理**（ICMP/ARP/反解/NetBIOS/mDNS/OS 探測；安裝時會在主機上自動裝一個，掃描一律由代理執行）、管理員限定的 **IP 探測**（由服務、OS 指紋、banner、憑證推出這是什麼主機，並比對選用的 [Recog](https://github.com/rapid7/recog) 指紋庫（安裝時下載、每週檢查新版）；每個結論都附上依據）、**憑證集中保管與派送**（商業 / 自簽憑證一次上傳，純 bash 代理依排程自動派送到 nginx/apache/caddy/haproxy/Proxmox VE·PMG·PBS/Zimbra…等服務並重載；另有 **Windows / IIS 的 PowerShell 代理**，匯入 Windows 憑證存放區、換上 HTTPS 繫結，並實際連線確認送出的是新憑證，不對就自動還原；**推送到防火牆**（Beta）免裝代理，直接用 FortiGate、OPNsense、Palo Alto、pfSense 的管理 API 換上憑證，推完同樣連線確認、不對就換回；私鑰加密儲存、到期告警、可手動續簽）、**機房平面圖 + 機櫃立面圖**（標準機櫃、工業機櫃與層架/鍍鉻層架/角鋼層架/木質層架（IKEA IVAR 型）/IKEA KALLAX，以及 LackRack（LACK 邊桌當 19 吋機櫃），寬度可自訂、層高逐層設定、頂板上方也放得了設備、同一層還能上下疊放；同一列最多並排 6 台、正背面、SVG/PNG/draw.io 匯出）、**纜線追蹤**（多跳穿透）、IP 異動記錄與失聯 IP 回收、通用表格欄位選擇 + 多格式匯出。

## Graylog 記錄補實（DSV 對照表）

jt-ipam 會**即時**產生一份 IP → 主機名稱 / FQDN 的對照表，讓 Graylog 的「DSV File from HTTP」資料配接器直接抓取，把記錄裡只有 IP 的事件自動補上可讀名稱。

- 在 **管理 → 外部系統整合 → Graylog** 啟用：設定路徑代稱、輸出格式（CSV / TSV）並產生存取 token
- 端點 `GET /api/v1/lookup/<路徑>?token=<token>`，每次請求即時查詢資料庫產生
- **提供的欄位**：每列兩欄，第 1 欄＝IP（key），第 2 欄＝主機名稱或 FQDN（value）；只輸出「有主機名稱」的 IP
- **資料格式**：UTF-8 純文字。CSV 以逗號分隔、**每欄用雙引號包覆**並依 RFC 4180 跳脫；TSV 以 Tab 分隔（不加引號）。例如：

  ```csv
  "10.1.1.141","log1.example.com"
  "10.1.1.145","mg-host"
  ```

- 在 Graylog 的「DSV File from HTTP」配接器：URL 填上方網址、分隔符依格式選逗號或 Tab、**Key column = 0、Value column = 1**（Graylog 欄位索引從 0 起算）
- token 逐次驗證、可隨時重新產生；設定頁直接提供可複製的完整對照表網址
- 權杖有效期限（30 到 365 天，到期前通知），管理員按「顯示」才看得到，並留稽核記錄
- 請用 HTTPS 網址：權杖放在網址上，所以明文的 8088 埠預設回 404，要打開「允許明文 HTTP」才服務（升級前就在用的站台維持開啟）；「允許的來源位址」可限制哪些位址能來抓

## BMC 主控台（IPMI SOL，Beta；不經作業系統的獨立連線）

直接從伺服器的 IP 開一個鍵盤 + 文字主控台到它的 **BMC**（IPMI 2.0 Serial-over-LAN），免裝各廠 Java/HTML5 KVM。逐 IP 啟用（RBAC 與 SSH 同級），BMC 帳密收進同一個加密金庫，每次連線都留稽核。**非破壞**：只有鍵盤 + 文字畫面，不含電源控制或滑鼠。

SOL 只是把主機的**序列埠**轉播出來，所以主機端要先設好序列主控台，否則畫面一片空白。主機一次性設定：

1. **找出 SOL 對應的埠**：`dmesg | grep -iE 'ttyS|SPCR'`（例：`SPCR: console: uart,io,0x3f8,115200` → `0x3f8`＝ttyS0、`0x2f8`＝ttyS1）。插錯埠一樣空白。
2. **加入核心 console 參數**（保留 `tty0` 讓實體螢幕不失輸出）：
   - 一般 Linux（GRUB）：在 `/etc/default/grub` 的 `GRUB_CMDLINE_LINUX` 加 `console=tty0 console=ttyS0,115200n8`，再 `update-grub`。
   - Proxmox VE（systemd-boot/ZFS）：把同一段加到 `/etc/kernel/cmdline`，再 `proxmox-boot-tool refresh`。
3. **啟用序列登入**（立即生效、免重開機）：`systemctl enable --now serial-getty@ttyS0`。
4. **（選用）BIOS Console Redirection**：指到同一個 COM 埠（115200 8N1），SOL 才看得到 POST/BIOS。逐欄建議值（Terminal Type、Flow Control、**Redirection After BIOS POST** …）見[BMC / SOL 設定教學](https://jasoncheng7115.github.io/jt-ipam/bmc-sol.html?lang=zh-TW)。
5. **重新開機**讓 `console=` 生效，之後 SOL 就能看到完整開機與 kernel panic。實體螢幕不受影響。

只想馬上能登入？做步驟 3 就夠了。同一份教學也內建在 App 裡，從 BMC 主控台的 **設定教學** 按鈕打開。

**疑難排解（實測常見坑）：**

- **連上但一片空白/按 Enter 沒反應**：SOL 對應的埠未必是 SPCR 宣告的那個。連著 SOL 時 `echo test > /dev/ttyS0`（與 `/dev/ttyS1`）看哪個出現；或看 `/proc/tty/driver/serial`，`rx` 有值的 ttyS 就是 SOL。
- **有 login 但看不到開機訊息**：核心 console 掛到錯的 ttyS（非 SOL 埠），serial-getty 卻在對的埠。`console=` **只掛 SOL 那一個埠**（如 `console=tty0 console=ttyS1,115200n8`），不要同時掛多個 `ttyS`，掛多個核心可能挑錯。用 `cat /proc/consoles` 確認。
- **有畫面但亂碼**：序列 baud 沒對齊 SOL。查 `ipmitool -I open sol info 1 | grep 'Bit Rate'`，把 `serial-getty` 設成同一個 baud。
- **方框字/顏色亂（例如 glances）**：把序列登入的 `TERM` 設成 `xterm-256color`（serial-getty 預設常是 `vt220`）。
- **OS 開機訊息有 emoji（⚠️ 等）**：那是 systemd 自己的符號；核心 cmdline 加 `systemd.setenv=SYSTEMD_EMOJI=0`。**BIOS** 畫面的 emoji 則把 BIOS Console Redirection 的 **Terminal Type 設 VT100+**（不要 VT-UTF8）。
- **畫面範圍很小、四周留黑**：序列無法自動傳視窗大小；按主控台的 **符合視窗**（它會送一段 `stty rows/cols` 指令，請在 shell 提示字元按），或自行 `stty rows N cols N`。

## 各來源會不會自己新增 IP 紀錄？

整合看到一個 IPAM 裡還沒有的位址時，行為不是每個來源都一樣，這件事會直接影響
「未授權 IP」異常偵測（它的判定是「**ARP 看得到、IPAM 沒有**」），所以整理成一張表：

| 來源 | IPAM 沒有該位址時 | 開關 | 預設 | 落點判斷 |
|---|---|---|---|---|
| **掃描代理** | 可自動建立 | 「自動收錄未登錄的 IP」 | **預設關閉** | 只建在「已指派給該代理且有開掃描」的子網路內 |
| **LibreNMS** | 可自動建立（只建裝置主 IP，不建 ARP 鄰居） | 「自動建立探索到的 IP」 | **預設開啟** | 放進「包含它的最小網段」；分不出來就不建 |
| **Proxmox VE** | 可自動建立 | 「信任虛擬化取得的 IP」 | **預設關閉** | 放進「包含它的最小網段」；分不出來就不建 |
| **VMware / ESXi** | 可自動建立 | 「信任虛擬化取得的 IP」 | **預設關閉** | 放進「包含它的最小網段」；分不出來就不建 |
| **Hyper-V / SCVMM** | 可自動建立 | 「信任虛擬化取得的 IP」 | **預設關閉** | 放進「包含它的最小網段」；分不出來就不建 |
| **CubeCOS / OpenStack** | 可自動建立（只在已對應的 Neutron 子網路內） | 「信任虛擬化取得的 IP」 | **預設關閉** | 只放進該 Neutron 子網路對應的 jt-ipam 子網路；還在「待對應」時不建 |
| **OPNsense / pfSense** | 可自動建立（DHCP 租約） | 「自動建立 IPAM 沒有的位址」 | **預設關閉** | 放進「包含它的最小網段」；分不出來就不建 |
| AdGuard / Wazuh / Zabbix / OCS / RustDesk / DNS / Windows DHCP / Kea / ISC DHCP / Technitium DHCP / ISOinsight / FortiGate / Palo Alto / Check Point / MikroTik | **只比對既有，不建** | 無 | 無 | 無 |
| CSV 匯入 / phpIPAM 遷移 | 由匯入內容建立（使用者明示的動作） | 無 | 無 | 依匯入資料 |

**共通規則**：自動建立一律走同一套判斷（`services/ip_autocreate.py`），
**把位址放進「包含它的最小網段」；如果分不出來該放哪個，就不建**。

舉例：
- `10.1.1.5` 同時落在 `10.0.0.0/8` 與 `10.1.1.0/24` → 放進**比較小**的 `10.1.1.0/24`。
- 甲、乙兩個單位**各自都有一個 `192.168.1.0/24`** → 系統無從得知這台機器算誰的，
  **不建立**。把紀錄掛到錯的單位，比沒有這筆紀錄更糟。
- 位址不在任何既有網段內 → 不建立（不會自己生一個網段出來）。

第二種情況只要在該整合的設定裡指定「限定子網路範圍」（只勾自己那組網段），
候選就只剩自己的，分得出來了，就會正常建立。

> ⚠️ **開啟自動建立＝放棄一部分偵測能力。** 拿得到位址的機器不等於該被收錄的機器：
> 私接的設備一旦被自動建進 IPAM，就**不會再出現在「未授權 IP」異常偵測裡**。
> 自動建立的紀錄在 IP 清單上會標示為「自動收錄（未登記）」（橘色 icon），子網路的 IP 指示計上則是**綠底加橘框**，圖例另有「自動收錄」筆數，請定期檢視。

## 核心物件

`區段 → 子網路 → IP 位址`，外加 `裝置` / `機櫃` / `地點`、`客戶`（管理單位）、`VLAN` / `VRF`、`NAT`、OPNsense / pfSense / FortiGate / Palo Alto / Check Point / MikroTik 防火牆，以及 IEEE OUI 廠商對照表（每月更新）。

## 權限（RBAC）

物件級權限，涵蓋 **7 種物件類型**（客戶 / 區段 / 子網路 / IP / 裝置 / 機櫃 / 地點）：

- **階層繼承**：授權上層（如某客戶或區段）自動涵蓋其下所有物件（子網路 → IP；地點 → 機櫃 → 裝置）
- 每種物件類型可用 **「全部」wildcard**
- **5 個內建角色**：系統管理員、唯讀檢視者、網路操作員、稽核員、部門管理員
- 可見性處處強制：清單端點、全域搜尋、拓樸圖、所有下拉選單，永遠只會出現使用者可見的物件。預設關閉（deny-by-default）。

## 安全（OWASP Top 10:2025）

安全是 day-one 需求，每個模組與 PR 都對齊 **OWASP Top 10:2025**，詳見 [`SECURITY_zh-TW.md`](SECURITY_zh-TW.md)；**資安問題請依 SECURITY.md 私下通報**（Security 分頁的「Report a vulnerability」或 service@jason.tools），不要開公開 issue。已發布的公告見 [Security advisories](https://github.com/jasoncheng7115/jt-ipam/security/advisories)。可作為 ISO/IEC 27001:2022 與 ISO/IEC 42001:2023 佐證的控制，以及導入組織要自己做的事，見 [`docs/COMPLIANCE_zh-TW.md`](docs/COMPLIANCE_zh-TW.md)。

- **強制 TLS**：二擇一，nginx 反代終止 TLS（`BACKEND_TLS_MODE=nginx`），或 uvicorn 直接掛自簽憑證（`BACKEND_TLS_MODE=direct`）
- A01：deny-by-default RBAC、物件級檢查（如上）
- A02：argon2id 密碼雜湊；儲存的敏感資料（DNS 憑證 / SNMP / API token）應用層加密，每一筆綁定用途；每日備份可用密碼加密
- A03：參數化 SQLAlchemy、嚴格 Pydantic v2 驗證、CSP + 輸出跳脫
- A05：HSTS、CSP、X-Frame-Options、Referrer-Policy
- A07：管理員可要求使用的 TOTP 雙因素驗證（附復原碼、防重放）、帳號鎖定（驗證碼打錯也算）、伺服器端工作階段（更新用權杖只放在 HttpOnly+Secure+SameSite=Strict cookie，可逐裝置撤銷，停用立即生效，開著的主控台 30 秒內結束）、API token TTL
- A08：SHA-256 稽核鏈，每輪同步驗證一次並錨定到資料庫外面
  （`/var/lib/jt-ipam/audit-anchors.jsonl` 與 journald），因為只有鏈本身抓不到「尾端被切掉」。
  `JT_IPAM_AUDIT_CHAIN_BASELINE_ID` 可指定驗證起點，給既有站台那些再也驗不回來的舊記錄用。
  稽核表在資料庫層只能新增，應用程式帳號也無法停用（`scripts/jt-ipam.sh harden-audit`）
- A09：結構化稽核記錄，匯出與檢視金鑰、權杖也留記錄
- A10：每一種協定的對外連線都檢查（HTTP 在連線當下；LDAP、SMTP、RADIUS、WinRM、DNS、syslog 與主控台在連線前）；封鎖 metadata / link-local，主控台另外不連本機

## 技術堆疊

| 層 | 選用 |
|------|--------|
| 後端 | Python 3.12 · FastAPI · SQLAlchemy 2.0（async）· asyncpg · Alembic · Pydantic v2 |
| 資料庫 | PostgreSQL 16（原生 `inet`/`cidr`/`macaddr`）+ pgvector |
| 前端 | Vue 3 · TypeScript · Vite · Naive UI · Pinia · vue-i18n |
| 認證 | argon2id · TOTP · 短效 JWT + refresh |
| AI | LLM Server（本地）· pgvector · MCP server |
| 部署 | systemd + nginx + apt 套件，**不需 Docker image**（適合虛擬機 / 容器） |

## 安裝（單機 / 虛擬機 / 容器）

> **支援版本：** Debian 12/13、Ubuntu 22.04/24.04/26.04，x86_64（amd64）；建議 Ubuntu 24.04 LTS 或 Debian 12/13。強制 HTTPS。
> 這正是 jt-ipam 替 guacd（必要的 RDP/VNC 主控台引擎）預編的版本；其他版本、衍生發行版（如 Linux Mint）或 ARM 機器，安裝會停下來。
> 作業系統出新版時會跟著加入，見 [docs/INSTALL_zh-TW.md](docs/INSTALL_zh-TW.md#支援的發行版本)。
>
> **最低需求：** 2 核心 CPU · 4 GB 記憶體 · 20 GB 磁碟。**建議：** 4 核心 · 8 GB 記憶體 · 50 GB 磁碟（保留空間給資料庫、稽核記錄、IP 異動記錄與備份成長）。4 GB 的機器後端只開 2 個 worker，升級時記憶體不夠會在前端 build 期間暫停後端；加 2 GB swap 就不會。每條 RDP 主控台約佔數百 MB 記憶體與一些 CPU。LLM 伺服器裝在同一台的話，記憶體要另外算。
>
> 選用的本地 LLM（Ollama）**不含**在上述數字內，請另跑於獨立主機，並依所選模型自行配足記憶體 / 顯示記憶體。

```bash
# 前置：最小化系統可能沒有 curl（一行式安裝需要它）
sudo apt-get update && sudo apt-get install -y curl
# 一行完成：自動 clone 到 /opt/jt-ipam 並安裝（不必先手動 git）
curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-ipam/main/scripts/bootstrap.sh | sudo bash
```

腳本會安裝 `postgresql-16` / `python3.12` / `nginx` / `redis`（以及 PDF 報告內嵌用的中文/日文字型 `fonts-noto-cjk`），建立 `jtipam` 系統帳號與 PG 角色，產生金鑰寫入 `/etc/jt-ipam/backend.env`，跑 `alembic upgrade head`，build 前端並啟用 `jt-ipam-backend.service`。

升級：`sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade`（**腳本內含 `git pull`**，直接跑即可），接著備份 → 相依 → alembic → build → 重啟。詳見 [`docs/INSTALL.md`](docs/INSTALL.md)。

> **從 0.5.170 以前的版本升上來時，請跑兩次 `upgrade`。** 升級腳本自己也會被 `git pull` 更新，但那一次仍由舊腳本主導後續步驟（0.5.171 起才會在 pull 後交棒給新版）。跑完後可用 `sudo bash /opt/jt-ipam/scripts/jt-ipam.sh doctor` 確認，它會把過不了的項目連同修法一起印出來。

> **選用：Docker Compose。** 另有一條次要部署路徑在 [`deploy/docker/`](deploy/docker/)（`./gen-env.sh` 後 `docker compose up -d --build`；之後用 `./update.sh` 升版）。主力且完整支援的仍是 systemd + apt。

### 裝好了但怪怪的？先跑健檢

```bash
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh doctor
```

一次檢查設定檔、後端有沒有真的回應、資料庫與 pgvector、資料庫版本是否到最新、前端版本是否與後端一致、排程與備份目錄、最近一次同步結果、本機掃描代理。**每個檢查不到的項目都會直接印出可以照著執行的修法**（`→` 開頭那行），不必先讀 log 猜原因。回報問題時附上這份輸出最省時間。

### 首次登入與重置管理員密碼

全新安裝時，腳本會**自動建立 `admin` 帳號、產生隨機密碼並在結束時印出一次**（也存到 `/etc/jt-ipam/.admin-initial-password`，僅 root 可讀；該檔位於 `/etc` 之下、不在 web root 內，無法透過 HTTP 連到）。登入後請立即更換，之後即可安全刪除此檔：`sudo rm /etc/jt-ipam/.admin-initial-password`。

重置管理員密碼（或在尚無管理員時建立第一個），在伺服器上執行：

```bash
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; source /etc/jt-ipam/backend.env; set +a; \
  .venv/bin/python -m app.cli.bootstrap create-admin \
    --username admin --email admin@example.com --password-stdin --force-update'
# 接著在 stdin 輸入新密碼（≥ 12 字元）
```

不加 `--force-update` 則是新建管理員，而非重置既有帳號。

唯一的管理員把驗證器與復原碼都弄丟時，從伺服器清掉他的雙因素驗證（同時登出所有裝置並留稽核記錄）：

```bash
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; source /etc/jt-ipam/backend.env; set +a; \
  .venv/bin/python -m app.cli.bootstrap reset-mfa --username admin'
```

## TLS / HTTPS

強制 HTTPS，兩種模式擇一（`/etc/jt-ipam/backend.env` 的 `BACKEND_TLS_MODE`）：

**模式 A：nginx 反代（預設、建議）** `BACKEND_TLS_MODE=nginx`
nginx 終止 TLS、反代到本機 uvicorn(127.0.0.1:8000)。換成正式憑證：

```bash
# 把正式憑證/私鑰覆蓋到固定路徑後 reload（路徑已寫死在 nginx 設定）
cp fullchain.pem /etc/jt-ipam/tls/server.crt
cp privkey.pem   /etc/jt-ipam/tls/server.key
chmod 600 /etc/jt-ipam/tls/server.key
nginx -t && systemctl reload nginx
```

Let's Encrypt：把 `ssl_certificate` 指到 `/etc/letsencrypt/live/<FQDN>/fullchain.pem`、`ssl_certificate_key` 指到 `…/privkey.pem`，續期後 `systemctl reload nginx`。自架 nginx 反代最小設定：

```nginx
server {
    listen 443 ssl;
    server_name ipam.example.com;
    ssl_certificate     /etc/jt-ipam/tls/server.crt;
    ssl_certificate_key /etc/jt-ipam/tls/server.key;
    root /opt/jt-ipam/frontend/dist;
    index index.html;
    location /api/ { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; proxy_set_header X-Forwarded-Proto $scheme; }
    location / { try_files $uri $uri/ /index.html; }
}
```

**模式 B：uvicorn 直接自簽** `BACKEND_TLS_MODE=direct`
uvicorn 自己掛 `--ssl-*`，安裝時 `scripts/generate-self-signed-cert.sh` 會產自簽憑證。換憑證：把正式(或自管)憑證覆蓋同一組路徑後重啟服務：

```bash
cp fullchain.pem /etc/jt-ipam/tls/server.crt
cp privkey.pem   /etc/jt-ipam/tls/server.key
chmod 600 /etc/jt-ipam/tls/server.key
systemctl restart jt-ipam-backend
```

> 兩種模式憑證路徑相同(`/etc/jt-ipam/tls/server.{crt,key}`)，差別只在「誰終止 TLS」：模式 A reload nginx、模式 B 重啟 backend。

**模式 C：前面已有外部反向代理負責 SSL**（你自己有一台 nginx / LB 終止 TLS）
本機 nginx 只做 HTTP，套用外部代理模式範本：

```bash
sudo cp deploy/nginx/jt-ipam-external-proxy.conf         /etc/nginx/sites-available/jt-ipam
sudo cp deploy/nginx/jt-ipam-external-proxy-snippet.conf /etc/nginx/snippets/jt-ipam-proxy.conf
sudo nginx -t && sudo systemctl reload nginx
```

> ⚠️ **必要：公開邊緣的安全標頭。** 真正**為使用者終結 TLS 的那台代理**必須送出安全標頭（HSTS、CSP `frame-src 'self'`、
> X-Frame-Options、nosniff、Referrer-Policy、Permissions-Policy、COOP、CORP）與 `server_tokens off`。這些標頭**不會**
> 自動跨多一跳存活，所以如果你的邊緣機是上面這台以外的**另一台**，**那台邊緣機也要設**（內建範本已含；非 nginx 的 LB
> 請照樣複製一份）。請從真正的對外網址驗證：
> `curl -skI https://你的網域/ | grep -iE 'strict-transport|content-security|x-frame|cross-origin|^server'`
> 並確認每個標頭都**剛好出現一次**、`Server: nginx`（無版本）。

外部代理本身**不會**影響 OIDC / M365(Entra ID) 登入，但有三個一定要對，否則登入會被導到 `ipam.example.com` 或卡在登入頁：

1. **`/etc/jt-ipam/backend.env`** 的 `APP_PUBLIC_URL` / `API_PUBLIC_URL` / `CORS_ORIGINS` 都設成對外網域（`https://ipam.your-domain.com`），不要留預設 `ipam.example.com`（後端簽發 token、OIDC 回呼網址都看它）→ 改完 `systemctl restart jt-ipam-backend`。
2. **外部 nginx 轉發時要送** `proxy_set_header X-Forwarded-Proto $scheme;`（=https）與 `Host $host;`；本機範本會把它透傳給後端（避免後端誤判成 http、Secure cookie 設不起來）。
3. **OIDC Redirect URI** 在 IdP 與 jt-ipam UI（管理 → 系統設定 → 單一登入（OIDC））都填 `https://ipam.your-domain.com/api/v1/auth/oidc/callback`。注意 **UI 存過的 DB 值優先於 .env**，改 .env 後要在 UI 再存一次。
> HSTS 由持有憑證的外部 nginx 送出，本機（HTTP）不送。

## 專案結構

```
jt-ipam/
├── docs/              # 規格、安全、資料模型、API 參考
├── backend/           # FastAPI app
│   └── app/
│       ├── core/      # config / db / audit / safe_http / encrypted_secret
│       ├── models/    # SQLAlchemy 2.0
│       ├── schemas/   # Pydantic v2
│       ├── api/v1/    # REST API
│       ├── services/  # 商業邏輯（ai / oui / opnsense / topology / search / permission）
│       ├── mcp/       # MCP server + tools（給 LLM 用戶端）
│       └── plugins/   # 外掛系統
├── frontend/          # Vue 3 + TS
│   └── src/{views,components,composables,api,stores,i18n,router}
└── scripts/           # jt-ipam.sh（install/upgrade/uninstall）、ci.sh、oui_refresh.py
```

## 藍圖進度

- **Phase 1（完成）**：phpIPAM 對等功能 + 改良（區段/子網路/IP/VLAN/VRF/NAT/裝置/機櫃/地點/IP 申請、TOTP/API-Token/RBAC、phpIPAM 匯入、CSV/RIPE/TWNIC、視覺化子網路格、強制 TLS）
- **Phase 2（完成）**：多家 DNS + 深度 LibreNMS 整合（裝置/ARP/FDB/實際狀態）+ 異常偵測 + SHA-256 稽核鏈 + pgvector AI 語意搜尋
- **Phase 3（完成）**：租戶/聯絡人/佈線/電力/VPN/虛擬化 + Proxmox VE 同步 + Cytoscape 拓樸 + OIDC/SAML SSO + OPNsense / pfSense / FortiGate / Palo Alto / Check Point / MikroTik 防火牆同步 + VMware ESXi / vCenter 盤點 + Wazuh agent 盤點 + Zabbix 監控涵蓋
- **Phase 4（完成、已縮減範圍）**：MCP server + 本地 LLM 自然語言（LLM Server）+ 外掛機制

### 機櫃圖嵌入其他系統

機櫃示意圖可以用一個網址對外提供 SVG，讓別的儀表板（例如 LibreNMS 的 widget）用
`<img>` 直接顯示：

```html
<img src="https://your-ipam.example.com/api/v1/racks/<rack-id>/embed.svg?token=<token>">
```

兩道開關都要成立才會給圖：**管理 → 系統設定**啟用嵌入並產生權杖，加上**該機櫃**自己的
「對外嵌入」開關（逐櫃、預設關）。機櫃圖會顯示裝置名稱與位置，權杖等同鑰匙，請勿貼到
公開場合；權杖可隨時重新產生，舊網址即刻失效。權杖有效期限（30 到 365 天，到期前通知），
管理員按「顯示」才看得到，並留稽核記錄。

用圖片而不是 iframe 是刻意的：本服務送 `frame-ancestors 'none'`，iframe 嵌入本來就會被
擋，而放行特定來源等於自己打開點擊劫持的面。

## 授權

AGPL-3.0-or-later｜商業支援請聯繫 Jason Tools。
