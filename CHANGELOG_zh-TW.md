# 變更記錄

本檔記錄專案所有重要變更，格式大致依循
[Keep a Changelog](https://keepachangelog.com/)；版本對應
`frontend/package.json` / `backend/app/version.py`。

## [0.5.75] — 2026-07-01

### 變更
- **連線清單 OS 欄改成與 IP 詳情頁一致** —— 共用 `OsCell`：OS 圖示 + 在地化家族名 +（來源）標註，滑鼠移上顯示原始偵測字串；顯示的 OS 是依來源順序算出的有效值（與 IP 詳情相同），不再只是掃描代理原始猜測。


## [0.5.74] — 2026-07-01

### 變更
- **連線中斷覆蓋層只蓋顯示區** —— 不再蓋住上方工具列，「重新連線」按鈕保持完整可見、可點。
- **匯出按鈕加上框線** —— 與旁邊的「欄位／重新整理」一致（原本是無框 `quaternary`）；透過共用的 `ExportButton` 套用到所有表格頁。


## [0.5.73] — 2026-07-01

### 修正
- **BMC 空白提示文字不再被資訊 icon 蓋住** —— 上一版收緊行距時連同 alert 左內距（保留給 icon 的空間）一起壓掉了；現在只收上下內距。


## [0.5.72] — 2026-07-01

### 變更
- **掃描代理 —— OS 偵測大幅更準（agent 1.7.0）** —— OS 探測加上 `nmap -sV`（服務/banner 偵測）+ `smb-os-discovery`，改由 **banner** 推導 OS（SSH `OpenSSH … Debian/Ubuntu`、`Service Info: OS:`、SMB），不再輕信純 TCP/IP 堆疊指紋（對裝置/BMC 常自信地誤判）。`-O` 積極猜測降為最後手段，且當結果是裝置型號（NAS/router/OpenWrt…）而非通用 OS 時直接捨棄 —— 寧顯示未知也不給錯型號。實測：Proxmox Datacenter Manager `HP P2000 NAS`→`Debian`、Windows `XP SP3`→`Windows`、BMC `OpenWrt Kamikaze`→未知。


## [0.5.71] — 2026-07-01

### 新增
- **遠端主控台 —— 明顯的「連線已中斷」覆蓋層** —— SSH／RDP／VNC／noVNC／xterm／BMC 連線中斷時，顯示區中央會蓋一層大字 +斷鏈 icon「連線已中斷」，一眼就看得出斷線；重連後自動淡出移除。所有主控台共用 `ConsoleDisconnectedOverlay`。


## [0.5.70] — 2026-07-01

### 變更
- **連線按鈕改為單純按鈕** —— 移除分割按鈕右側的下拉箭頭（「另開視窗」選單），SSH／RDP／VNC／noVNC／BMC 在連線清單頁與 IP 詳情卡片都改成點一下就開主控台（新分頁）。連線清單列高收緊。BMC 空白提示精簡成一行（細節收進「看設定教學」）。


## [0.5.69] — 2026-07-01

### 變更
- **連線按鈕 —— RDP/VNC/noVNC 圖示更好辨識** —— 三者原本共用「螢幕 + 10px 小字母」很難分辨；字母改為大（13.5px）且粗、佔滿螢幕，R / V / N 一眼可辨。分割按鈕的下拉箭頭收窄（連線清單頁 + IP 詳情頁）。


## [0.5.68] — 2026-07-01

### 新增
- **BMC 主控台 —— 「符合視窗」按鈕** —— 序列主控台沒有視窗大小協商，全螢幕程式預設 80×24、四周留黑。此按鈕會把 `stty rows/cols`（用 xterm.js 的真實大小）指令送進 session，對齊瀏覽器視窗。滑過去立即彈出提示，說明它是**送指令**、需在 shell 提示字元按。被控端免裝任何腳本。
- **BMC 設定教學 —— 疑難排解區** —— SPCR 可能指錯 ttyS（用 echo 逐埠測）、baud 要對齊 SOL 的 Bit Rate、`TERM=xterm-256color` 讓 glances 這類 curses 工具不亂碼、以及符合視窗說明。README（中英）同步。


## [0.5.67] — 2026-07-01

### 修正
- **BMC「記住帳密」從未存檔** —— 憑證金庫的建立／列表端點不接受 `protocol='bmc'`（回 400、被 UI 吞掉），導致 BMC 密碼從未儲存、每次連線都重問。現已在建立／列表／權限分派都納入 `bmc`（僅密碼、`can_use_bmc`）。

### 新增
- **BMC 主控台 —— 內建序列主控台設定教學** —— 表單／工具列／空白畫面提示都有 **設定教學** 按鈕，點開逐步彈窗：找出 SOL 對應的 ttyS（ACPI SPCR／dmesg）、加 `console=tty0 console=ttySx,115200n8`（GRUB 或 PVE `/etc/kernel/cmdline`）、啟用 `serial-getty`、選用 BIOS Console Redirection、重新開機。README（中英）與 docs 首頁同步補上。


## [0.5.66] — 2026-07-01

### 變更
- **BMC 主控台空白畫面提示改為說明「兩層」序列主控台需求** —— BIOS Console Redirection（POST/BIOS/開機選單）**加上** OS 序列主控台（核心 `console=ttySx,115200n8` + `serial-getty`；ttyS 由 ACPI SPCR 判定；PVE 走 `/etc/kernel/cmdline` + `proxmox-boot-tool refresh`）。沒設 OS 層，核心載入後 SOL 就空白。
- 測試：`test_map_provider` 接受預設 `builtin` 地圖來源。


## [0.5.65] — 2026-07-01

### 修正
- BMC 主控台終端機：加上明顯陰影，與 RDP/VNC 主控台畫面一致。
- **整合 DNS（Univention UCS）：儲存時帳號改為必填。** 空帳號會讓 UCS 回 `400「basic auth malformed」`，同步靜默抓 0 筆。


## [0.5.64] — 2026-07-01

### 修正
- **BMC 主控台連線按鈕現在會出現在 IP 詳細資料卡片上**（與 SSH/RDP/VNC 並列）—— 先前編輯視窗沒有渲染按鈕／發出事件。
- **BMC 主控台畫面改成與 SSH/RDP/VNC 一致**（卡片高度、左側標籤表單、「記住帳密」用 switch、標題 icon 對齊、狀態膠囊工具列、滿版終端機）＋ 空白畫面時的「按 Enter」提示（SOL 閒置正常）。


## [0.5.63] — 2026-07-01

### 修正
- **連線管理頁 500** —— BMC 讓 `list_connection_targets` 的 tuple 變 5 元素，但有一處仍以 4 元素解包，整頁報錯、0 筆。已修。
- BMC 主控台：IP 詳細資料頁補上連線按鈕（先前只有連線管理頁有）。

### 變更
- 用詞：BMC 主控台 UI 拿掉「帶外」（非台灣用語）；註解用 OOB。


## [0.5.62] — 2026-07-01

### 變更
- BMC 主控台：帳號輸入框改用通用範例（`ADMIN / root`）。


## [0.5.61] — 2026-07-01

### 新增
- **BMC OOB主控台（Beta）** —— 瀏覽器內 IPMI **SOL** 主控台（鍵盤 + 文字畫面），針對 BMC 管理 IP，併入「連線管理」
  與 IP 編輯（per-IP 開關）。走標準、跨廠的傳輸（`ipmitool` SOL over RMCP+），**cipher 自動回退（17→3）**、連線
  自我檢查（SOL 是否啟用 / 權限）、單一 session 處理、憑證金庫（`protocol=bmc`）、**權限與 SSH 同等級**、開/關
  皆稽核。非破壞：只有鍵盤 + 畫面 —— 無滑鼠、無電源/感測/開機控制。Migration 0092（`bmc_enabled`）。安裝/升級
  自動裝 `ipmitool` + `freeipmi-tools`；nginx WebSocket 位置已涵蓋 `bmc`。（圖形截圖 adapter 屬未來、隔離的階段。）


## [0.5.60] — 2026-06-30

### 修正
- **子網路清單：CIDR 欄被擠壓。** `scroll-x` 設得遠小於各欄實際寬度總和，表格便把彈性的 CIDR／說明欄壓到 `minWidth`
  以下。把 `scroll-x` 改成實際總和並加大 CIDR 最小寬度，CIDR（重點欄）就能完整顯示 —— 視窗太窄時表格改用水平捲動。


## [0.5.59] — 2026-06-30

### 變更
- 用詞：把殘留的「前綴」一律改成「首碼」（台灣慣用）—— 主要是 OUI（MAC 製造商）搜尋框的提示文字。


## [0.5.58] — 2026-06-30

### 修正
- **IP 申請清單真正顯示子網路 CIDR。** 0.5.56 讓前端改用 `subnet_cidr`，但清單端點從未填這欄（只有詳情端點有填），
  所以還是退回顯示 UUID。清單回應現在會帶入 `subnet_cidr`。


## [0.5.57] — 2026-06-30

### 新增
- **IP 指示計圖例加上滑過 tooltip**，說明每個狀態（上線／近期出現／離線／保留／未知／閒置），並帶出實際的存活門檻。
  「近期出現」＝最近一次偵測落在「上線門檻（預設 30 分鐘）」到「其 4 倍（預設 2 小時）」之間 —— 多半是剛漏掃或網路抖動。


## [0.5.56] — 2026-06-30

### 修正
- **IP 申請清單：「子網路」欄改顯示子網路 CIDR**，不再顯示子網路的原始 UUID（後端讀取本來就有回 `subnet_cidr`，
  只是清單沒用到）。

### 變更
- 新增 IP 申請對話框：標題與兩個按鈕（取消／送出）補上 icon。


## [0.5.55] — 2026-06-30

### 修正
- **IP 申請核准後，會把申請填的主機名稱與用途寫進配發的 IP。** 主機名稱以 **manual** 觀測記錄（最高優先序，之後
  掃描／同步不會蓋掉），用途寫進該 IP 的**備註**。（描述本來就有帶入。）直接核准與多關卡核准都適用（兩者走同一
  配發流程）。


## [0.5.54] — 2026-06-30

### 變更
- 變更密碼對話框：標題與下方兩個按鈕（取消／變更密碼）補上 icon，與其它對話框一致。


## [0.5.53] — 2026-06-30

### 變更
- **IP 清單：閘道／DHCP 伺服器標記改用緊湊 icon（附 tooltip）**，取代原本的寬文字標籤，不再把 IP 擠成一個字一行
  的直條。落在 DHCP 範圍內以小圓點呈現。
- **IP 清單：加寬 OS 欄位**（110→150 px），OS 類別標籤不再被截斷。


## [0.5.52] — 2026-06-30

### 變更
- **掃描代理 installer 改為先裝 base tools（`curl git sudo`），並預設安裝 `avahi-utils`（mDNS）** —— mDNS 名稱解析
  開箱即用（原本要 `JT_IPAM_ENABLE_MDNS` 才裝）。`avahi-utils` 會帶起 `avahi-daemon`（UDP 5353）；`JT_IPAM_NO_MDNS=1`
  可不裝 mdns、`JT_IPAM_SKIP_PROBE_TOOLS=1` 略過所有探測工具。
- **文件：安裝說明改為先裝 `curl`**（最小化系統可能沒有，一行式安裝需要它）。


## [0.5.51] — 2026-06-30

### 變更
- **LibreNMS「自動加入裝置」改為預設開啟**（既有實例也由遷移一併打開），這樣每次同步／拉取就會順帶把裝置
  match-or-create 成 jt-ipam 裝置 —— 不必每次再手動按「連結裝置」。

### 新增
- **整合 DNS：清單新增「立即同步」按鈕**。DNS 原本只由排程計時器靜默同步（不會出現在「作業」）；手動拉取現在會
  排入 `dns.sync` 作業，像其它整合一樣顯示在「作業」裡。


## [0.5.50] — 2026-06-30

### 變更
- **子網路掃描：啟用掃描時必須明確選擇掃描方式** —— 「本機直接掃（jt-ipam 主機）」或指定一個掃描代理；沒選就
  存檔會被擋下並提示。原本語意含糊的「留空＝主機直接掃」改成明確的**本機直接掃**選項，這樣在主機掃不到 LAN 的
  環境（如 Docker）就不會「掃了卻什麼都沒發生」。既有用本機掃的子網路會顯示為「本機直接掃」。


## [0.5.49] — 2026-06-30

### 新增
- **本機帳號自助變更密碼**：右上角帳號選單新增「變更密碼」，開啟對話框驗證目前密碼並設定新密碼（≥ 12 字）。
  外部認證帳號（LDAP／SSO）不顯示此入口。新端點 `POST /api/v1/auth/change-password`（有稽核）。


## [0.5.48] — 2026-06-30

### 修正
- **pfSense 同步不再因兩種狀況中斷**：(a) 別名的 `detail` 欄回傳為 **list**（現在會攤平成字串）、(b) NAT
  port-forward 的 **target 是別名名稱**而非 IP（現在會略過、不再硬轉 INET）。兩者先前都會丟 asyncpg `DataError`
  並中斷整個抓取。

### 變更
- **掃描代理 OS 偵測改用 `nmap --osscan-guess`**：沒有精確指紋匹配的主機也能得到推測 OS（取信心最高的那個、附
  信心百分比），不再完全空白。代理 v1.6.0（會自動更新）。


## [0.5.47] — 2026-06-30

### 修正
- **IP 關係圖：掛在機櫃上的裝置即使自身沒設地點，也會繼承機櫃所在地點（機房）。** 先前這類裝置（例如某 PVE
  節點的機櫃有設地點、但裝置本身的 `location_id` 是空的）關係鏈會停在機櫃，導致同一機櫃的兩台主機呈現不一致
  —— 一台有機房、一台沒有。


## [0.5.46] — 2026-06-29

### 新增
- **IP 清單：每筆 IP 的特殊角色標記** —— **閘道**（所屬子網路閘道）、**DHCP 伺服器**（對應到已整合 OPNsense／
  pfSense 防火牆 IP 時自動標記，另在 IP 編輯視窗有手動開關）、以及**在 DHCP 範圍／租約內**。以 IP 旁的小色標
  ＋tooltip 呈現。


## [0.5.45] — 2026-06-29

### 變更
- **區段：隱藏「嚴格模式」開關（與欄位）。** 那是 phpIPAM 相容欄位、jt-ipam 從未強制，開關按了沒作用。欄位仍會
  儲存並透過 phpIPAM 相容 API／遷移原樣保留（既有值不動），只是不再以開關呈現。


## [0.5.44] — 2026-06-29

### 修正
- **AI 對話小工具在尚未啟用 LLM/AI 前不再出現**（管理 → LLM/AI）。新安裝時原本還沒設定 LLM 就能輸入並按送出；
  `/me` 現在會回 `ai_enabled`，小工具依此顯示／隱藏。
- **LLM/AI 設定：未開啟「啟用 Ollama 伺服器連接」時不再自動抓模型清單**，因此不會再冒出「無法連 Ollama：
  Internal Server Error」。關閉時會清掉清單與錯誤。
- **LLM/AI：模型名稱的「(未在 Ollama 找到)」前補一個半形空格。**


## [0.5.43] — 2026-06-29

### 新增
- **Docker Compose 內網／離線部署流程**：`offline-export.sh` 在有外網的主機 build 並把四個映像（app + postgres/redis）
  打包成一個壓縮檔；`offline-import.sh` 在沒有外網的主機載入並啟動（`--no-build --pull never`）。安裝與升級同一套流程。
  說明見 `deploy/docker/README*`。

### 變更
- 用詞：異常偵測「MAC 漂移」→「MAC 變動」（台灣慣用）。


## [0.5.42] — 2026-06-29

### 修正
- **IP 清單「交換器位置」欄位加寬**，完整顯示 `交換器@埠號`（例 `switch-003@eth1/0/24`），不再截成
  `switch-003@eth1/…`。


## [0.5.41] — 2026-06-29

### 修正
- **機房／地點地圖（內建底圖）改為放大到剛好框住所有標記**，不再固定用 ~24°×16° 的大視野，鄰近的地點不會再
  擠成看起來像同一點。仍保留很小的最小視野，只為避免單點／極近點過度放大（內建低解析底圖會糊）。


## [0.5.40] — 2026-06-29

### 變更
- **pfSense 整合表格欄位與 OPNsense 一致**（名稱／API URL／TLS／最後同步／最後錯誤／操作）；移除多出來的
  啟用／同步項目／別名數／規則欄位。

### 新增
- **左側功能區會自動展開目前頁面所屬的群組**（管理／進階／子網路群組），不論是導覽過去或直接進入，
  都能一眼看到所在位置。


## [0.5.39] — 2026-06-29

### 修正
- **OPNsense 防火牆欄位選擇不再列出不存在的欄位。** 原本選單會列出 狀態／DHCP／ARP／OpenVPN／Rules／NAT，但表格
  根本沒有這些欄位（全勾選卻永遠不出現）。選單現在與實際欄位一致：名稱、API URL、TLS、最後同步、最後錯誤、操作。


## [0.5.38] — 2026-06-29

### 變更
- **pfSense 整合頁對齊 OPNsense 頁**：新增 TLS 欄位、當有實例關閉 Verify TLS 時顯示「TLS 驗證已停用」警告橫幅、
  表單內相同的 TLS 警告，以及相同的操作鈕順序（編輯／測試／同步／刪除）。
- **PVE LXC（xterm）主控台提示移到工具列**（接在狀態標籤右邊、單行、太長以 … 截斷、可關閉），不再佔整行，
  文字也更精簡。


## [0.5.37] — 2026-06-29

### 新增
- **異動記錄超過指定天數的項目以淡色顯示**，讓近期變更更明顯。天數於 管理 → 系統設定 → 顯示 設定
  （預設 30 天；0 = 不淡化）。套用在 IP 詳情的異動時間軸與「IP 異動記錄」頁。


## [0.5.36] — 2026-06-29

### 新增
- **PVE LXC（xterm）主控台：可關閉的提示橫幅** —— 若畫面只有游標、沒有提示字元，提醒你在畫面中按一下 Enter
  （PVE LXC 主控台的已知特性）。


## [0.5.35] — 2026-06-28

### 修正
- **RDP：修飾鍵組合（Ctrl+V／Ctrl+C／Ctrl+A…）現在可用 —— 剪貼簿貼上也才真的貼得出來。** 原本字母／數字鍵
  以 unicode 字元送出，而 RDP 的 unicode 鍵事件不會與 scancode 的 Ctrl/Alt 修飾鍵組合，所以 Ctrl+V 沒作用
  （只會打出「v」）。現在按住修飾鍵時，該鍵改用 scancode 送出。已對真實 Windows 主機端到端驗證（Ctrl+V 時
  被控端回 `CB_FORMAT_DATA_REQUEST`、我們回傳剪貼簿文字）。
- RDP「貼上」鈕現在會回報實際送進被控端剪貼簿的字數。


## [0.5.34] — 2026-06-28

### 修正
- **RDP 貼上：修正啟用後 RDP 連上約 10～20 秒就斷線。** 被控端在我們還沒設定任何剪貼簿文字時就來要資料，
  會讓 aardwolf 的 cliprdr 通道撞 `'NoneType' object has no attribute 'datatype'` 而整條斷線。改成連線時
  先放一個空字串到剪貼簿，`clipboard.data` 就不會是 null。

### 變更
- **所有主控台（SSH／RDP／VNC／noVNC／xterm）斷線時，被控端顯示區會反灰**（灰階＋變暗、停用互動），
  讓使用者一眼看出已中斷。


## [0.5.33] — 2026-06-28

### 修正
- **使用者管理表格：操作欄改為固定在右側**，視窗較窄、表格水平捲動時操作欄仍看得到（原本會被捲出畫面）。


## [0.5.32] — 2026-06-28

### 新增
- **RDP 主控台：選用的單向貼上（控制端 → 被控端）。** RDP 工具列新增「貼上」鈕，把本機剪貼簿的文字送進被控端
  剪貼簿（純文字，再於遠端按 Ctrl+V）。被控端的剪貼簿**不會**回傳到瀏覽器／伺服器。由管理者設定
  **管理 → 系統設定 → 資安 → 允許 RDP 控制端貼上文字到被控端**控制，**預設關閉（deny by default）**。
  後端只有在開啟時才掛 RDP 剪貼簿（cliprdr）通道，貼上長度有上限。已對真實 Windows RDP 主機端到端驗證。


## [0.5.31] — 2026-06-28

### 修正
- **連線管理頁的 PVE 主控台按鈕對齊 IP 詳情頁** —— 標籤只放 noVNC／xterm，右上角加一個「PVE」小標
  （取代原本內嵌的「·PVE」）。


## [0.5.30] — 2026-06-28

### 修正
- **PVE 主控台（noVNC／xterm）中斷連線行為比照 RDP** —— 按「中斷連線」（或連線掉線）後停在「已關閉」狀態、
  畫面凍結在最後一幀並出現「重新連線」鈕，不再直接退回連線表單。


## [0.5.29] — 2026-06-27

### 修正
- **noVNC／xterm 主控台畫面加上與 RDP 一致的外框**（邊框＋圓角＋陰影），不再是無框貼齊。


## [0.5.28] — 2026-06-27

### 修正
- **PVE 主控台連線表單對齊 SSH 表單。** 自動選最近一筆已存的 PVE 帳密（精簡表單、可直接連線），選了已存帳密時
  提示文字也跟著切換，卡片標題／連線鈕圖示依協定顯示（xterm → 終端機、noVNC → 螢幕）。


## [0.5.27] — 2026-06-27

### 修正
- **PVE xterm（CT）主控台的終端機四周留內距**（比照 SSH 主控台），不再貼齊邊緣。


## [0.5.26] — 2026-06-27

### 修正
- **版本資訊頁補上原本漏列的 noVNC 相依套件**：後端 `websockets`（PVE 主控台代理）與前端 `@novnc/novnc`。
- **連線管理頁的 PVE 主控台按鈕對齊 IP 詳情頁**：依類型顯示 xterm（CT）／noVNC（VM）、改用醒目色（橘／PVE），
  提示文字也改成「xterm 連線」／「noVNC 連線」，不再是籠統的「連線」。
- **全域搜尋：輸入符合的 Proxmox VMID 會直接列出該 VM/CT**（以名稱顯示、歸在「虛擬化」群組）。先前該結果用了
  下拉不認得的型別而被整個濾掉（只剩無關的 IP 比對）。


## [0.5.25] — 2026-06-27

### 修正
- **noVNC 按鈕改用獨立圖示**（螢幕內含「N」），不再沿用 RDP 圖示 —— noVNC 與 RDP 不再長得一模一樣。
- **PVE 主控台連線表單在錯誤狀態下也置中**（原本只有初始表單置中，連線失敗時卡片會卡在左上角）。
- **連線按鈕（SSH／RDP／VNC／noVNC）的提示改用系統內建即時彈窗**，不再用瀏覽器原生 `title` —— 連線管理頁與
  IP 詳情頁標頭都改。
- **修正用「已儲存的 PVE 憑證」連線時的 500** —— 儲存的密碼被重複解碼（`str` 沒有 `.decode()`）；改為比照
  RDP/VNC 只解一次。
- **稽核記錄**的 PVE 憑證目標改顯示其名稱（label），不再顯示原始 UUID。


## [0.5.24] — 2026-06-27

### 修正
- **裝置詳情頁：「編輯」改為就地開視窗**（原本會跳到裝置清單）。裝置編輯視窗抽成共用元件 `DeviceEditModal`。
- **虛擬化 VM 表格篩選：** 輸入數字（如 `102`）不再誤中 `memory_mb`（1024）等內部欄位 —— 快速篩選現在只比對
  **顯示中的欄位**（名稱／VMID／節點／IP／MAC／狀態），且會比對 IP/MAC 清單裡的每一筆。


## [0.5.23] — 2026-06-27

### 修正／變更
- **PVE 主控台（noVNC/xterm）介面對齊 SSH/RDP/VNC。** 一樣的卡片連線表單（帳號 → 密碼 → 認證領域 順序、
  簡短「記住此帳密」），圖形 VM 主控台的工具列加上**送出按鍵 + 縮放（自動縮放／原始解析度）+「中斷連線」**。
  連線鈕用對的圖示／提示（noVNC vs xterm），連線類型篩選不再把「noVNC/xterm」截斷。
- PVE 主控台開關現在會出現在**一台 VM 的所有 IP** 上 —— 多 IP 的 VM 改用網卡 MAC 解析，不再只認主 IP。
- **全域搜尋：** 純數字（如 `227`）現在也會當成可能的 Proxmox **VMID** 找出對應 VM/CT；右側提示顯示
  「VLAN / VMID」而非只有「vlan_number」。
- **機櫃：** 裝置視窗的「U 位 (起始)」欄位加寬（看得到數字），挑選 U 位也正確反映**半 U** 占用（左／右），
  可放進空的那半。


## [0.5.22] — 2026-06-27

### 新增
- **瀏覽器內 PVE 主控台（noVNC / xterm），針對 Proxmox VE 的 VM/CT。** 當某 IP 對應到 Proxmox 的 VM/CT，
  IP 編輯視窗會多一個開關，啟用後出現瀏覽器內主控台按鈕（右上掛 **PVE** 小標）：QEMU VM 開**圖形 noVNC**、
  LXC 容器開 **xterm 終端機**。連線用**你當下輸入的 PVE 帳密**（可選擇存進加密金庫，比照 SSH/RDP/VNC），並由
  PVE 自身權限把關——沒有 `VM.Console` 就連不上。瀏覽器只連 jt-ipam 的**同站** WebSocket，由後端位元組對接到
  PVE 的 `vncwebsocket`（VM 走 vncproxy、CT 走 termproxy）；帳密除選用金庫外不落伺服器、WebSocket 走單次票證、
  每場連線都稽核（`novnc.session_open` / `novnc.session_close`）。
- Proxmox 同步現在會回填每台 VM/CT 的主 IP（`VirtualMachine.primary_ip_id`），讓 IP 能解析到它的 PVE 主控台
  目標（既有 VM 也會補上）。


## [0.5.21] — 2026-06-27

### 修正
- 繁體中文用詞：地圖供應商相關文案與註解改用「內建／本機」，不用對岸用語「自帶／同源」。


## [0.5.20] — 2026-06-27

### 新增／變更
- **地圖供應商預設改為「內建（離線）」** —— 完全內建的世界地圖（不對外連線）。管理員仍可在
  設定 → 系統把地點頁預覽切成 **OpenStreetMap** 或 **Google Maps**。
- **OpenStreetMap 圖磚改走本機後端代理**（`/api/v1/system/map-tile/{z}/{x}/{y}`）：瀏覽器不直連 OSM，
  所以即使管理員選了 OSM，CSP 仍維持 `img-src 'self'` + COEP `require-corp`（ZAP 乾淨）。此代理為受限唯讀
  （URL 由伺服器端組、只連 OSM、圖磚座標驗證、小型記憶體 LRU 快取、nginx 限流）。
- Google Maps：頁內預覽用內建地圖（Google 圖磚依其條款不可代理）；「在外部開啟」連結才開 Google Maps。


## [0.5.19] — 2026-06-27

### 安全
- 針對唯一剩下的已接受發現（CSP `style-src 'unsafe-inline'`，Vue + Naive UI 先天造成——`v-show`／`:style`／
  浮動元件定位會產生 inline style *屬性*，CSP 無法用 nonce／hash 放行）做加固與文件化：開啟 Naive UI 的
  **`inline-theme-disabled`**，把主題樣式從 inline 屬性移進 `<style>` 區塊（縮小 inline 面積 + SSR／效能），並在
  `SECURITY.md`（中／英）記為**附補償控制的已接受風險**：嚴格 `script-src 'self'`（不能執行 JS）+
  `img-src`／`connect-src 'self'`（不能外洩）+ Vue 自動跳脫。實際已無可利用性。


## [0.5.18] — 2026-06-27

### 安全／變更
- **地點地圖改為完全內建、不再嵌入 OpenStreetMap。** 以內建的 Natural Earth 世界輪廓（public domain，本地投影）
  取代 OSM 圖磚。地圖現在在隔離／離線網路也能用、**完全不對 OSM 發請求**（不再洩漏管理員正在看哪些站點），也讓
  安全標頭可以收緊：CSP `img-src` 移除 OSM 例外、`Cross-Origin-Embedder-Policy` 升到最強的 **`require-corp`**
  （全站已零跨來源子資源）。nginx proxy snippet 也 `proxy_hide_header` COEP（單一來源）。
- **所有管理表格的欄位選擇標籤，現在會在即時切換語言時重新翻譯** —— 19 個 picker 改用 `computed`（原本會卡在
  進入頁面當下的語言）。
- pfSense NAT 同步已**用實機 port-forward 驗證**並修正欄位對應（NAT 埠用外部 `destination_port`、`target` 連到內部 IP）。

### 新增
- `deploy/zap-baseline.conf` —— 已記錄的 ZAP 基準三化（已接受、附理由的 low/informational 例外：Naive UI 的
  `style-src 'unsafe-inline'`、IPAM 範例 IP、靜態資產快取、SPA 偵測）。發版關卡＝ZAP 掃描**沒有此基準以外的發現**
  （0 FAIL／0 WARN）。


## [0.5.17] — 2026-06-27

### 變更
- **pfSense／OPNsense 一致性再加強。**「pfSense 防火牆」管理頁移除「檢視規則」按鈕——規則／別名檢視改在
  **進階 → 防火牆 (pfSense)**（唯讀），與 OPNsense 一致。選單更名：**防火牆 (OPN) → 防火牆 (OPNsense)**、
  **防火牆 (pf) → 防火牆 (pfSense)**，內頁標題同步一致；pfSense 規則頁籤改為**「防火牆規則」**。
- NAT 規則的**來源**篩選新增 **pfSense**，並把 pfSense NAT port forward 同步進 NAT 表
  （`source_origin = pfsense:<id>`），與 OPNsense NAT 並列顯示。

### 修正
- 欄位選擇選單的標籤現在會在**即時切換語言（免重整）**時立即重新翻譯——pfSense 各頁與 NAT 來源篩選原本會卡在
  進入頁面當下的語言。


## [0.5.16] — 2026-06-27

### 變更
- **pfSense 介面對齊 OPNsense 各頁。**「pfSense 防火牆」管理表格現在有欄位選擇 + 匯出與合適的預設欄位（操作欄在窄
  寬度下不再被切掉）；新增／編輯視窗間距修正（同步開關 / 提供 DSV 改成表單列分組）；頁面標題改為
  **「pfSense 防火牆」**（原「整合 pfSense」）。
- 進階的「防火牆規則 / 別名」（OPNsense）改名為 **「防火牆 (OPN)」**。

### 新增
- **進階 →「防火牆 (pf)」** —— 唯讀的 pfSense 規則與別名檢視（實例選擇 + 頁籤 + 快速篩選 + 欄位選擇 + 匯出），
  比照 OPNsense 的「防火牆 (OPN)」頁。
- `pfsense` 已納入**主機名稱／ARP 來源順序**，預設排在 `opnsense` 之下。


## [0.5.15] — 2026-06-27

### 安全／文件
- **把安全標頭列為部署的必要設定，並在 install／upgrade 輸出中明確提示。** 當你用自己的邊緣反向代理／負載平衡器
  擋在 jt-ipam 前面（Mode C），**那台代理必須自己設安全標頭**——它們不會自動跨多一跳存活，否則對外網站就會完全沒有
  CSP／HSTS。外部代理 snippet（`jt-ipam-external-proxy-snippet.conf`）現在也會 `proxy_hide_header` 上游的安全標頭
  （去重，比照 v0.5.14 的內部 snippet）；INSTALL（中／英）、README（中／英）、首頁都把這列為**必要**並附上「從對外
  網址驗證」步驟；`jt-ipam.sh install`／`upgrade` 也會印出必要標頭提示。


## [0.5.14] — 2026-06-27

### 安全
- **修正 `/api/*` 回應的安全標頭重複 + 過時 CSP**（由已登入 ZAP 掃描發現）。後端 middleware 仍送著 v0.5.8 之前
  的寬鬆 CSP（`frame-src` 還放行 google／openstreetmap），且在 nginx 之後每個 `/api` 代理回應都帶**兩份**安全標頭
  （HSTS／CSP／X-Frame-Options／Referrer-Policy／Permissions-Policy／COOP／CORP）——ZAP 報「Strict-Transport-Security
  multiple header entries」。已把後端 CSP 收成 `frame-src 'self'`（讓 `direct`／`self-signed` 模式也正確），並在 nginx
  代理 snippet 用 `proxy_hide_header` 把上游的安全標頭擋掉，讓 server 區塊的硬化值成為唯一來源。線上實測：每個標頭各
  一份、CSP 已收緊。


## [0.5.13] — 2026-06-27

### 修正
- **全測試套件與 lint 收綠。** 跑完整 pytest（412 項）+ 全新 DB 套 migration 0001→0088，修掉 4 個落後於先前
  功能異動的測試斷言——新 MCP 工具 `list_connection_targets`（漏進工具參數守門）、Proxmox guest-agent 的
  `timeout` 參數（測試 mock 簽章）、以及對外 MCP 開關關閉時改回 **403**（測試原本斷言 401）。另移除兩個
  無用變數 lint 錯誤、排序 import。不影響產品行為。


## [0.5.12] — 2026-06-27

### 新增
- **pfSense 整合 Phase 2**——防火牆**規則同步** + 唯讀的**規則 / NAT 檢視**（pfSense 頁的眼睛圖示），以及
  pfSense 的 **Graylog DSV** 端點：`…/lookup/pfsense/{id}/aliases`（別名 → 成員）與
  `…/lookup/pfsense/{id}/rules`（filterlog `tracker` → 規則說明），token 保護、每台可開 `expose_dsv`。新增每台
  開關：同步規則、提供 DSV。已對 pfSense CE 2.8.1 驗證。（migration 0088）
- TEST_CHECKLIST：新增 pfSense 整合測試段 + 近期功能抽測。


## [0.5.11] — 2026-06-27

### 新增
- **pfSense 整合（Phase 1）**——獨立的整合、有自己的設定頁（管理 → pfSense），與 OPNsense 完全分開。pfSense CE
  沒有內建 REST API，因此走第三方 **pfSense-pkg-RESTAPI** 套件（pfrest.org）：base path `/api/v2`、`X-API-Key`
  認證。會拉 **ARP 表**與 **DHCP 租約**，在限定的子網路範圍內 stamp IP 存活 / MAC / 主機名稱（重疊網段安全），
  並讀取**防火牆別名**。每台可分別開關同步（DHCP 預設關，避免與另一台 DHCP 衝突）、可限定子網路、Verify TLS、
  測試連線與立即同步；接進每 5 分鐘的定期同步。`pfsense` 已登錄為名稱／ARP 來源。已對 pfSense CE 2.8.1 端到端
  驗證通過。（migration 0087；防火牆規則／NAT／Graylog DSV 留待 Phase 2。）


## [0.5.10] — 2026-06-27

### 修正
- **子網路 IP 清單裡的「新增位址」沒有 IP 欄位可填**，按下新增會以 HTTP 422（缺 IP）失敗（issue #14）。新增表單
  現在會顯示必填的 **IP** 欄位（有帶入值時自動預填），IP 留空送出會在前端先擋下並提示。


## [0.5.9] — 2026-06-27

### 新增
- **通知矩陣**（管理 → 通知發送設定）：以「事件 × 管道（站內鈴鐺 / Email）」的矩陣勾選哪些事件要發通知。
  事件包含：IP 申請待審核 / 已核准 / 已拒絕、憑證即將到期或已過期、**代理成功更換新憑證**（新增）、憑證飄移、
  異常偵測有新發現。所有發通知的地方都改為依矩陣決定；憑證與異常事件現在也能寄 Email（原本只有站內）。
- **新事件 `cert.deployed`**：派送代理成功把某憑證換成新版時通知管理員（回報端點以「同一憑證/服務的舊指紋 vs
  新指紋」差異判定是否真的換新）。
- **憑證派送新增 `files`（僅換檔）服務**：只寫入憑證檔（fullchain + key 到 `/etc/ssl/jt-ipam`），**不做**設定
  測試、不 reload／restart 任何服務——給想自己處理重載的人用。


## [0.5.8] — 2026-06-26

### 安全
- **移除機房／地點頁內嵌的第三方地圖 iframe**（Google 地圖／OpenStreetMap），改成在新分頁開啟。原本內嵌會把
  第三方頁面（及其 JS）載進我方頁面——既是隱私外洩，也是 ZAP 報 **Cross-Domain JavaScript Source File
  Inclusion** 與 **Sub Resource Integrity Attribute Missing** 的來源（那些 script 是 Google／OSM 嵌入頁的，
  不是 jt-ipam 的）。現在只有使用者點了才會連到 Google／OSM。
- **收斂 CSP `frame-src` 為 `'self'`**（不再內嵌任何東西，拿掉 google／openstreetmap 允許來源）。
- **強化 nginx 參考設定**：隱藏上游（uvicorn）的 `Server` / `X-Powered-By` 標頭（不洩漏框架指紋），並加上
  `Cross-Origin-Resource-Policy: same-origin`。

### 文件
- INSTALL（中／英）與首頁現在都把**高安全性 nginx 反向代理列為正式環境標準**（TLS 1.2/1.3、HSTS preload、
  嚴格 CSP、完整安全標頭、隱藏上游版本指紋、後端只綁 loopback）。


## [0.5.7] — 2026-06-26

### 新增
- **MCP 用戶端設定產生器。** 管理 → LLM/AI 的「對外提供 MCP」卡片新增「產生用戶端設定」按鈕，直接產出可貼上的
  MCP 設定片段：Claude Desktop（走 `mcp-remote`）、opencode、mcpo、通用用戶端（Cursor／Cline／VS Code）——
  端點網址與 API 金鑰都已帶入，每段各有複製鈕。


## [0.5.6] — 2026-06-26

### 變更
- **異常偵測頁改成頁籤。** 四種偵測（IP 衝突／MAC 漂移／失聯 IP／未授權 IP）改用頁籤呈現，不再同一頁一直往下堆疊。
- **每個異常表格都可挑欄位**，內部用的 `ip_address_id`（UUID）欄位預設隱藏（仍可在「欄位」勾選）。

### 新增
- **MAC 漂移加上對應 IP／主機名稱**（從 IPAM 解析、ARP 補位）—— 一眼看出漂移的 MAC 是哪台主機。


## [0.5.5] — 2026-06-26

### 新增
- **掃描代理：新增「相依套件」欄。** 代理會回報它的探測工具盤點；欄位顯示裝好幾個（例如 `4/7`），點下去
  開詳情：每個工具裝了沒、版本多少、用於哪些探測（nmap → OS/連接埠、nmblookup → NetBIOS、avahi-resolve
  → mDNS…）、缺的附上安裝指令。可一眼看出「查不到機器名稱」是因為缺 `nmblookup`。代理自我更新到 v1.5.0
  才會回報（migration 0086）。


## [0.5.4] — 2026-06-24

### 修正
- **背景作業重啟後會永遠卡在「進行中」（issue #9）。** 作業是用 `asyncio.create_task` 跑在 worker 程序內，
  後端一重啟（部署 / 升級 / 當機）在跑的作業就消失了、但 DB 狀態還停在 running，於是「作業」頁永遠殘留
  「執行中」清不掉。啟動時改為把殘留的 pending/running 作業標成 `failed`（中斷：後端已重啟）。
- **LibreNMS 同步中途因裝置埠重複而中斷（issue #12）。** 埠同步改用 upsert（`ON CONFLICT (device_id, name)`）
  取代直接 INSERT，所以埠已存在時（例如多台 LibreNMS 裝置對映到同一台 jt-ipam 裝置、或同一介面被重複處理）
  不會再以 `device_port_unique_name` 的 `UniqueViolationError` 炸掉整批同步。


## [0.5.3] — 2026-06-24

### 修正
- **聯絡人群組無法新增 / 編輯 / 刪除——「Method Not Allowed」（issue #11）。** 後端原本只有
  `GET /contact-groups`，補上 `POST` / `PATCH` / `DELETE`。
- 補上 **Provider、電路、無線 SSID、無線連線** 原本缺少的 `DELETE` 端點——這些刪除鈕先前會回 405
  （同一類問題）。


## [0.5.2] — 2026-06-24

### 修正
- **Proxmox VM 清單只顯示 500 台（issue #9）。** 清單改為逐頁抓完整，所有 VM 都會顯示（例如 592 台而非
  500）；同一個「逐頁抓完」修正也涵蓋其他進階資源清單。
- **Proxmox 同步很慢 / 卡在「進行中」（issue #9）。** 每台 VM 的 guest-agent 取 IP 查詢（best-effort）
  改用 6 秒短逾時，避免執行中 VM 的 agent 無回應時，用共用的 20 秒逾時拖垮整批同步。
- **Wazuh agent 只顯示 200 台（issue #10）。** Agent 其實都有同步入庫；管理頁改為逐頁抓完，不再只抓前 200。
- **一併檢查其他整合是否有相同上限。** LibreNMS `/devices`、AdGuard 本來就會回全部；OPNsense 別名 /
  規則 / IPsec 搜尋不再卡在 1000 / 500（`rowCount = -1` ＝ 全部）。

### 變更
- 所有表格的分頁列最左側顯示總筆數（例如「共 592 筆」）。
- 右下角 AI 對話浮動按鈕平常半透明、移過去才變實心顏色。


## [0.5.1] — 2026-06-24

### 新增
- **RDP / VNC「送出按鍵」。** 連線中可送出被瀏覽器或作業系統攔截的特殊組合鍵（Esc、Tab、F1～F12、
  Ctrl + Alt + Del、⊞ Win、Alt + Tab；VNC 另含 macOS ⌘ 組合），選單以鍵帽樣式呈現並依平台帶 icon。
- **RDP「重新調整大小」。** 連線中按一下即以目前視窗大小重新連線、取得原生清晰畫面（aardwolf 無法
  連線中熱改解析度，故改以重連取得相符解析度）。
- **版本資訊頁強化。** 新增 asyncssh / aardwolf / Pillow 等套件版本、本機環境（作業系統 / 核心 /
  nginx / Node.js / PostgreSQL）與前端框架（Vue / Naive UI / Vite…）版本，並重整版面分區。
- **對外提供 MCP（唯讀）。** 管理 → LLM / AI 新增開關，打開後其它系統才能以 HTTP 呼叫本站 MCP
  （`/api/mcp`，Streamable HTTP / JSON-RPC）；可產生 / 重新產生**唯讀** API 金鑰（加密保存），頁面以
  「名稱 → 值」顯示連線網址與認證標頭。唯讀金鑰一律擋下 6 個會異動資料的工具、工具清單也隱藏它們。
  預設關閉（deny by default）；既有 per-user API 權杖認證仍可用，且同受此開關控管。
- 新增 MCP 工具 `list_connection_targets`（唯讀）：列出已啟用瀏覽器遠端連線（SSH / RDP / VNC）且呼叫者
  可連線的 IP / 裝置——絕不回帳密。

### 變更
- 連線主控工具列：主機名稱右側加協定標籤（SSH / RDP / VNC）；按鈕改精簡且更明顯可按、中斷連線改紅色外框。
  進階→連線管理 與 IP 詳細資料的連線按鈕，只在欄寬不足時才收成 icon（門檻隨該列連線種類數放大）。
- 主機為 Proxmox VM 客體時，關係圖會畫出它所在的 PVE 節點（及該節點的機櫃/機房）——IP 與裝置詳情頁皆是。

### 修正
- **Proxmox 同一叢集內同名 VM 無法匯入（issue #8）。** VM 唯一鍵由 `(叢集, 名稱)` 改為 `(叢集, VMID)`
  （migration 0085）——Proxmox 允許同名不同 VMID 的 VM，原本會撞 `vm_cluster_name_uq` 而匯入失敗。
- **AI 對話：還原被當成文字吐出的工具呼叫。** 支援工具呼叫的模型偶發把呼叫寫成文字（而非結構化
  `tool_calls`）→ 改為解析並執行（不再把那段亂碼當答案顯示）；無法還原時顯示中性的重試提示。
- 對外 MCP 子應用不再提供 FastAPI 自動產生的 `/openapi.json`、`/docs`（MCP 以 JSON-RPC `tools/list`
  探索工具，非 OpenAPI；該 schema 對 MCP client 無意義且未經認證）。
- 稽核明細的 `switch_port` 顯示為 `device@port`（與其他頁一致）；憑證目標解析為 label 而非原始 UUID。


## [0.5.0] — 2026-06-22

### 新增
- **瀏覽器內 RDP 連線管理（Beta）。** 直接從 IP 詳細資料頁開 Windows RDP 桌面 —— 已對「強制 NLA 的
  Windows 11」實機驗證。
  - 每 IP `rdp_enabled` 開關（migration 0083）；權限 `can_use_rdp`（deny-by-default，沿用 `can_ssh`
    能力）；詳情頁分割按鈕 + 進階→連線管理 的「RDP」篩選/操作。
  - 後端 `endpoints/rdp_console.py`：單次 ticket → WebSocket 橋接遠端桌面（NLA / CredSSP+NTLM）；
    畫面以 PNG tile 串流到 `<canvas>`，鍵盤/滑鼠/滾輪回送；目標 host 鎖死為編目 IP（防 SSRF）；
    連線開/關稽核（絕不記密碼）；並發上限 `rdp_max_sessions`。
  - 原生 `<canvas>` 繪製，**前端零新增相依**。解析度選單含「自動縮放」。
- **瀏覽器內 VNC 連線管理（Beta）。** 同一套模式套用於 VNC（RFB）目標 —— 已對真實 VNC 伺服器驗證。
  - 每 IP `vnc_enabled` 開關（migration 0084）；權限 `can_use_vnc`；詳情頁分割按鈕 + 連線管理「VNC」。
  - 桌面尺寸由伺服器決定；畫面提供 **自動縮放 / 原始解析度** 切換（縮放時滑鼠座標正確換算）。
  - **VNC 認證僅支援 RFB security type None 與 VncAuth（密碼）。** 帳號型（UltraVNC MS-Logon、
    VeNCrypt、RealVNC RA2/RA2ne）不支援；連線畫面已標示。
- **選用相依、對基礎安裝零影響。** RDP/VNC 使用 `aardwolf`（pin 到有預編譯 manylinux wheel 的版本
  → 免 Rust 工具鏈）。install/upgrade 以 **best-effort** 安裝（`pip install --only-binary=:all: -e
  ".[rdp]"`）；無 wheel 即快速失敗、功能自動停用。後端偵測可用性，未安裝時前端隱藏入口。
- 共用的**個人加密憑證金庫**現可保管 SSH / RDP / VNC 帳密（`protocol` + 選填 `domain`）；憑證稽核
  記錄帶協定（如 `rdp_credential`）。

### 變更
- 進階→連線管理 一併列出 SSH/RDP/VNC 目標；OS 欄改用與詳情頁相同的來源優先序解析。
- nginx WebSocket upgrade location 拓寬涵蓋 SSH/RDP/VNC 主控路徑；升級會就地修補既有站台設定。

### 修正
- 稽核明細的 `switch_port` 顯示為 `device@port`（與其他頁一致）；憑證目標解析為 label 而非原始 UUID。

## [0.4.210] — 2026-06-21

### 新增
- **SSH 連線帳密「記住」功能（by-user 個別保管）。** 每位使用者可儲存自己的密碼／私鑰，
  下次直接選用，不必重打：
  - 後端 `ssh_credentials`（migration 0082）：密碼／私鑰／passphrase 各自**信封加密**
    （per-field 隨機 DEK，DEK 由主 KEK（ENCRYPTION_KEY）包覆，AAD 綁 owner+欄位）；明文絕不落 DB／log／回前端。
  - `GET/POST/DELETE /api/v1/ssh-credentials`：一律 owner-only、僅回遮罩（不含明文）。
  - 連線改以 **reference（credential_id）**：前端只送 id，後端在連線當下記憶體解密、用完即丟；
    仍須通過 `can_use_ssh(target)` 授權，scope 支援「綁定目標」與「個人預設（任一可連 IP）」。
  - 稽核：連線記錄 `credential_id`（永不記明文），接現有 SIEM 轉送；停用使用者即無法使用其帳密。
  - 前端連線表單加「已存帳密」下拉（選用即連）＋「記住此帳密」開關。

### 範圍外（roadmap）
- PTY session 錄製、敏感目標 MFA 二次驗證、外部 Vault/KMS 收 KEK、SSH CA 短效憑證。

## [0.4.209] — 2026-06-21

### 新增
- **進階 → 連線管理頁**：表格列出所有已啟用 SSH 且本人可連線的目標（後端 `GET /addresses/ssh/targets`，與 `can_use_ssh` 同樣 deny-by-default 過濾），支援排序 / 即時篩選 / 選欄位 / 匯出，每列可「SSH 連線」（新分頁）或下拉「另開視窗」。

### 變更
- IP 詳情頁的「SSH 連線」改為**點主鍵開新分頁**、**下拉開新視窗**（移除頁內嵌入式終端機）。
- SSH 連線表單欄位順序調整：認證方式移到最上、密碼緊接帳號下方。
- 連線狀態改成有色圓點藥丸徽章（已連線綠色脈動）、中斷 / 重新連線 / 另開視窗都加上圖示。

### 修正
- 啟用「SSH 連線管理」存檔後，SSH 按鈕需重新整理才出現 —— PATCH `/addresses/{id}` 回應未計算 `ssh_available`，已比照 GET 補上。

## [0.4.208] — 2026-06-21

### 新增
- **IP 位址 SSH 連線管理（嵌入式 / 另開視窗終端機）。** 在 IP 編輯視窗可開關「啟用 SSH 連線管理」；
  啟用後，具權限者會在詳情頁右上（編輯鈕左側）看到「SSH 連線」分割按鈕：主鍵在本頁開啟 xterm.js 終端機，
  右側下箭頭可選「另開視窗」開獨立全頁終端機。
- **連線安全：** 先以 JWT 換取 60 秒單次 ticket，再以 `?ticket=` 開 WebSocket（後端 asyncssh 橋接）。
  帳密／私鑰**只在連線時送出、不落地儲存、不記錄**；目標主機固定為該 IP 記錄的位址（防被當成通用 SSH proxy）；
  主機金鑰採 TOFU 信任後釘選（日後不符即警告）；連線開／關都寫稽核。
- **權限：** 新增獨立的「連線管理權限」(`users.can_ssh`)。admin、對該 IP 有寫入權者、或具連線管理權限且
  至少可檢視該 IP 者，方可使用（deny-by-default）。使用者管理頁可逐人開關。

### 變更
- nginx 站台設定（含外部反向代理範本）新增 WebSocket upgrade 標頭與 SSH 終端機長連線逾時（`deploy/nginx/*.conf`）。
  ⚠️ prod 實機 nginx 需同步套用此設定。
- 前端新增相依 `@xterm/xterm` / `@xterm/addon-fit`（純前端、build 時打包，安裝／升級的 pnpm 安裝會自動帶入）。

## [0.4.207] — 2026-06-19

### 變更
- **Docker Compose 的管理員密碼自動產生。** `gen-env.sh` 現在會連 `admin` 密碼一起隨機產（印在輸出、存進
  `.env` 的 `JT_IPAM_ADMIN_PASSWORD`，0600），backend 首次啟動就用它建好 admin，可直接登入（比照 systemd 安裝的「自動建 admin」體驗）。
- **首頁部署區改成兩區塊：**「主力：systemd + apt」與「選用：Docker Compose」，各自有框 / 標籤 / 淡底，
  並各自列出安裝 / 首次密碼 / 升級指令。Docker 區明確標出升版是 `./update.sh`（**別用 `jt-ipam.sh upgrade`**）。
- docs/INSTALL §2.7 與 deploy/docker README（中英）的「第一個管理員」同步更新成上述自動產密碼行為。

## [0.4.206] — 2026-06-19

### 變更
- **Graylog DSV 設定頁的「格式」與「權杖」改成左右兩張卡片**（各自有框 / 淡底 / 圓角），視覺上明顯分開、
  窄螢幕自動換行，取代原本上下堆疊的排版。

## [0.4.205] — 2026-06-19

### 修正
- **Docker Compose 部署兩個啟動問題**（用 docker compose 完整實跑後抓到）：
  1. **`.env.example` 的 `BACKEND_BIND_HOST=0.0.0.0` 會被安全檢核擋下**（nginx 模式要求綁 loopback）→ 改成
     `127.0.0.1`；容器內 uvicorn 仍以 `0.0.0.0` 綁（由映像 CMD 控制、只在 compose 網路內、不對主機開埠）。
  2. **`sync` / `web` 在資料庫遷移完成前就啟動**（`depends_on: service_started` 只等容器起來）→ `backend`
     加 healthcheck（uvicorn 開始監聽＝遷移已跑完才算 healthy），`sync` / `web` 改 `depends_on: service_healthy`，
     不再出現首次啟動 `relation "opnsense_firewalls" does not exist`。
- 已用 `docker compose up` 完整實跑驗證：5 個服務健康、HTTP→HTTPS 轉址、前端與 `/api` 反代皆 200、admin 自動建立、
  管理員登入回 access_token、`sync` 迴圈 0 錯誤。

## [0.4.204] — 2026-06-19

### 新增
- **選用的 Docker Compose 部署**（`deploy/docker/`）。次要 / 選用方式（主力仍是 systemd + apt）：
  一組 compose 起 `postgres`(pgvector) / `redis` / `backend` / `sync`（背景同步迴圈取代 systemd timer）/
  `web`(nginx，服務前端 + 反代 `/api` + 自簽 HTTPS)。附 `gen-env.sh`（產生隨機密鑰）與 `update.sh`
  （`git pull` → 重建 → 重啟）。**升版只要 `./update.sh`**——backend 容器啟動時自動跑 `alembic upgrade head`，
  不需手動遷移。已實測：映像可建置、fresh pgvector 跑完 0001→0080 全部遷移、自動建管理員、uvicorn 正常啟動。

## [0.4.203] — 2026-06-18

### 變更
- **Proxmox VE VM DSV 改為每叢集一個（支援多個 PVE 叢集 / 獨立節點）。** 因為 vmid 在不同叢集間會重複，
  全域單一 DSV 會混淆。新增每叢集端點 `GET /api/v1/lookup/proxmox/{cluster_id}/vms`；Graylog DSV 設定頁
  的來源表格會**每個叢集各列一筆**（比照 OPNsense 多防火牆），各自獨立網址 / Lookup Table。
  全域 `…/proxmox/vms`（所有叢集、去重）仍保留，單叢集環境可直接用。

## [0.4.202] — 2026-06-18

### 新增
- **Graylog DSV 新增 Proxmox VE VM 來源（vmid → VM 名稱）。** 端點 `GET /api/v1/lookup/proxmox/vms`
  （沿用 Graylog DSV token），key = Proxmox VMID、value = 已同步的 VM 名稱，讓 Graylog 把記錄裡的
  vmid 補上可讀的 VM 名稱。跨叢集 vmid 若重複，每個 vmid 只輸出第一筆。Graylog DSV 設定頁的來源表格
  自動帶出這筆（全域、與「IP → 主機名稱」並列）。

### 修正
- **防火牆 DSV 提示文字的欄位索引也修正**為 key 欄=0、value 欄=1（0 起算；前一版只改了主教學表格、漏了這段提示）。

## [0.4.201] — 2026-06-18

### 變更
- **子網路詳情頁工具列加「刪除」按鈕**（帶確認框）。原本只能回「全部子網路」清單頁、用操作欄垃圾桶或批次刪除，
  而操作欄常被擠到表格最右邊不好按；現在打開某個子網路就能直接刪，刪除後刷新側邊子網路樹並回到清單頁。

## [0.4.200] — 2026-06-18

### 修正
- **版本檢查把舊版誤判成新版。** 「檢查 GitHub 最新版」原本用字串比較（`!=`），會把 `0.4.79` 當成新過
  `0.4.199`（字串 `'7' > '1'`）；且發佈是 push 到 main、不一定建 release/tag，原本退回讀到舊 tag。
  改成**主要讀 main 分支的 `version.py`**（反映真正已發佈的版本），比較一律用**數字序**，tags 退路也取數字序最大者。

### 變更
- **版本資訊頁版面：**「檢查 GitHub 最新版」移到上排第三格（與「現行版本 / Python」同一排），不再獨佔整列。
- **LibreNMS 自動建立 IP 的子網路選擇加固，避免建錯單位。** 落點子網路改為「唯一且最精確（最長首碼）」者：
  多層巢狀取最精確；**重疊網段、多個相同最長首碼都包含時 → 不猜、直接略過**（寧可不建也不要建到別的單位）；
  沒有任何既有子網路包含則不建。要消除歧義就在該 LibreNMS 實例設「限定子網路範圍」。

## [0.4.199] — 2026-06-18

### 修正
- **Graylog DSV 教學的 Key／Value column 索引寫錯。** Graylog「DSV File from HTTP」配接器的欄位索引是
  **從 0 起算**，正確值是 **Key column = 0、Value column = 1**；教學頁與 README 原本誤寫成 1／2。

## [0.4.198] — 2026-06-18

### 修正
- **防火牆規則 DSV（`rid → alias`）漏抓 UUID 格式的規則。** filterlog 的 `rid`（pf 規則 label）有兩種格式：
  32 碼 md5（純 hex）與 UUID（含「-」）。原 `_RL_LABEL` 正規式 `[0-9A-Za-z]+` 不含「-」，導致 UUID label
  的規則整條比對失敗、被漏掉，只剩 md5 那幾條（某台防火牆實測只抓到 10 條、應為 59 條 / 涵蓋 44 個別名）。
  改成抓引號內全部內容（label 內容即 `rid`），md5／UUID／自訂 label 都涵蓋。
  ＞ 註：`rid → alias` 本質上只會涵蓋「被有 label 的規則引用到」的別名；沒有被任何規則用到的別名不會有 `rid`
  （也不會出現在 filterlog），屬正常。

## [0.4.197] — 2026-06-18

### 新增
- **憑證派送代理可對應到裝置。** 編輯派送代理時可選擇「對應裝置」（`cert_agents.device_id`，migration 0080，
  裝置刪除→SET NULL）。對應後：①「派送代理」清單與「進階 → 憑證派送現況」頁的**代理名稱可點**，直接進入該裝置詳情；
  ②**來源 IP 欄可點**——後端把代理回報的來源 IP 解析到 IPAM 對應的位址（重疊網段時優先取掛在該對應裝置上的同 IP），
  前端連到該位址詳情。未對應裝置或來源 IP 在 IPAM 查無對應時，維持純文字。

### 變更
- **Graylog DSV 串接教學調整：**「格式」（輸出設定）與「重新產生權杖」（金鑰）是兩回事，不再並排同列。
  Extractor 與 Pipeline 是**擇一**的兩種做法（不是先後步驟），改成步驟 2 底下的「做法 A／做法 B」、
  共用同一個「要查的 log 欄位」輸入，不再各編號為步驟 2、3。點值複製的提示改為「已複製到剪貼簿」。

## [0.4.196] — 2026-06-18

### 新增
- **LibreNMS 同步可自動建立探索到的 IP。** 每個 LibreNMS 實例新增「自動建立探索到的 IP」開關
  （預設開啟）：同步裝置時，會把受監控裝置的**主 IP**自動建進對應的既有子網路
  （標 `discovery_source=librenms`）。只建裝置主 IP、不含 ARP 鄰居；若該實例設了限定子網路範圍，
  只在範圍內建立；子網路若尚未在 IPAM 建立則略過。解決「只接 LibreNMS、未佈掃描代理」時，子網路內
  0 個 IP、即時狀態與使用率全 0 的困惑（LibreNMS 進來的是裝置，原本只 stamp 既有 IP、不建立）。

### 修正
- **儀表板「即時狀態」把掃描代理／LibreNMS 判定的上線誤算成「未知」。** 計數時比對的是大小寫不符的
  固定字串（`Online (scanner)` 等），但實際寫入值是小寫帶來源後綴（`online (scanner)`／
  `online (librenms)`）→ 改用 `startswith("online")` 比對（比照 `recompute_effective_status`）。

### 變更
- **預設對話模型改為 `gemma4:26b`**（原 `gpt-oss:120b`）——與 README 既有建議一致；未在 LLM 設定頁
  覆寫的環境（含全新安裝）即以此為預設。已覆寫者不受影響。
- **文件：** 本地 AI 加值區塊加註「本套件無內建 LLM Server，請在有 GPU 算力的主機安裝好 LLM Server
  後再提供給 jt-ipam 接取使用」。

## [0.4.195] — 2026-06-18

### 變更
- **Graylog DSV 頁面收尾。** DSV 來源表格的操作欄移除多餘的「複製」鈕（值的複製已在下方教學提供，點即複製）；
  「詳細資料」鈕更名為「網址 / 設定」，更貼切其顯示的查表網址與設定內容。
- **「要查的 log 欄位」輸入框移進步驟 2（Extractor）。** 原本孤立在步驟 1 與步驟 2 之間、沒有步驟編號；
  現在放在它第一個被用到的地方（Extractor 的 Source field 上方），步驟 3（Pipeline）的說明也改成指向「步驟 2 設定的 log 欄位」。

## [0.4.194] — 2026-06-18

### 變更
- **Graylog DSV 串接教學再優化。** 步驟改用明顯的數字圓圈（比照憑證安裝說明），每個來源——包含防火牆規則／
  別名 DSV——都同時提供 **Extractor 與 Pipeline 兩種**做法（各自帶該來源的實際欄位／Lookup Table／輸出欄位）。
  設定表格左欄（欄位名）加上淡底與值區分；每個要貼進 Graylog 的值都**點一下即複製**（點任何灰底的值）。

## [0.4.193] — 2026-06-18

### 變更
- **Graylog DSV 頁面：端點清單改成真正的資料表格，並驅動下方教學。** DSV 來源表格加上排序、欄位選擇、
  篩選框與重新整理；點某一列即選取該來源，下方 Graylog 串接教學會重繪成那個來源的版本（正確的查表網址、
  Lookup Table 名稱、key／value 欄位與對應的 pipeline rule——IP→主機名稱保留 LAN cidr_match 判斷、防火牆
  規則／別名則用單純的 rid／alias 查表），切換時帶淡入淡出過場。頁面也移除固定寬度限制、改用全寬。
  用詞：「詳情」→「詳細資料」。

## [0.4.192] — 2026-06-18

### 變更
- **Graylog DSV 頁面改成一張可擴充的端點表格 ＋ 詳情抽屜。** 原本每個 DSV 來源各攤一張卡片、兩個網址框，
  防火牆一多就很亂；現在所有 DSV 端點（IP→主機名稱、每台防火牆的規則與別名查表）統一列在一張表
  （名稱／對照／狀態／操作），點「詳情」開抽屜顯示 HTTPS ＋ 內網 HTTP 網址、複製鈕與該來源設定
  （IP→主機名稱的開關／路徑放在抽屜內）。共用的格式與權杖放在表格上方。日後新增 DSV 類型只要多一列，版面自動容納。

## [0.4.191] — 2026-06-18

### 新增
- **OPNsense 防火牆 Graylog DSV（規則 label→alias、alias→成員）。** 除了既有的 IP→主機名稱 DSV，每台
  OPNsense 防火牆可再對外提供兩支 token 保護的查表給 Graylog 補實防火牆 log：
  `/api/v1/lookup/firewall/{id}/rule-aliases`（key=filterlog `rid`／pf 規則 label，value=該規則引用的
  alias 名）與 `/api/v1/lookup/firewall/{id}/aliases`（key=alias 名，value=成員清單）。規則對照每輪同步從
  `/api/diagnostics/firewall/pf_statistics/rules` 解析（涵蓋使用者／外掛／自動規則）；別名 DSV 用已同步的
  alias 內容。每台防火牆用新的「提供防火牆 DSV」開關啟用（整合 → OPNsense），各自的查表網址（不同 path）
  顯示在 Graylog DSV 設定頁。Migration 0078（opnsense_rule_labels ＋ opnsense_firewalls.expose_dsv）。

## [0.4.190] — 2026-06-17

### 變更
- **電路表格新增「速率 / 固定 IP / 閘道」欄位。** 這些欄位電路本來就有（編輯表單也有），只是清單沒顯示；
  補上易讀的速率欄（↓下載 / ↑上傳，自動換算 Gbps/Mbps/kbps）以及固定 IP/CIDR、閘道欄（都可在欄位選擇器開關）。

## [0.4.189] — 2026-06-17

### 安全性
- **清掉 Dependabot 開啟中的警示**（前端建置工具鏈），用 `pnpm.overrides` 釘到修補版：`form-data` ≥4.0.6
  （CRLF 注入，GHSA-hmw2-7cc7-3qxx——經 axios/jsdom 引入）、`vite` ≥6.4.3（Windows `server.fs.deny` 繞過，
  GHSA-fx2h-pf6j-xcff——同時修掉內含的 launch-editor NTLMv2 警示）、`js-yaml` ≥4.2.0（merge key 二次方
  複雜度 DoS）。`pnpm audit` 已乾淨、建置不變（vite 仍在 6.x）。這些都是建置／開發相依，不在前端正式 bundle 內。

## [0.4.188] — 2026-06-17

### 變更
- **掃描代理 installer 預設不再安裝 avahi（mDNS）。** `avahi-utils` 相依 `avahi-daemon`，裝了會起一個常駐
  服務、監聽 UDP 5353 並對外廣播主機 mDNS——對多數伺服器是不必要的副作用。installer 現在預設只裝 `nmap`
  （OS）與 `samba-common-bin`（NetBIOS），兩者都不起 daemon；mDNS 改用 `JT_IPAM_ENABLE_MDNS=1` 才裝。
  （主伺服器安裝/升級從來不碰這些。）安裝說明也標明 avahi-utils 會一併啟動 avahi-daemon。

## [0.4.187] — 2026-06-17

### 變更
- **NetBIOS / mDNS 主機名稱來源在 IP 詳情顯示在地化標籤**（來源標籤與「釘選主機名稱來源」下拉），與來源優先序頁
  一致。新增回歸測試：驗證掃描代理回報的 NetBIOS / mDNS 名稱會各自記成獨立的 `netbios` / `mdns` 觀測來源。

## [0.4.186] — 2026-06-17

### 修正
- **IP 位址編輯視窗的「儲存」按鈕沒反應／改動遺失（issue #6，感謝 @lin-junyou）。** 條件渲染的操作按鈕
  （儲存／編輯／新增／取消／返回）與刪除確認框以 `v-if`/`v-else` 共用同一位置、且沒有唯一 `:key`，導致 Vue 在
  檢視↔編輯切換時重用 vnode、保留了**上一個分支的** `@click` —— 按「儲存」實際觸發的是返回/編輯，改動就被默默
  丟掉。已為每個條件按鈕／確認框補上穩定 `key`（行內 `#header-extra` 與視窗 `#footer` 都修）。
- **Ubuntu 26 安裝失敗「requires a different Python: 3.14 not in '<3.14,>=3.11'」（issue #5，感謝 @Ghucos）。**
  Ubuntu 26.04 預設 Python 3.14，但後端 `requires-python` 把上限卡在 3.14 以下，pip 直接拒裝。放寬為
  `>=3.11,<3.15` 以允許 3.14。

## [0.4.185] — 2026-06-16

### 新增
- **掃描代理的 NetBIOS 與 mDNS 名稱探測真正實作了**（先前這兩個探測可勾選、但其實是 Phase B 空殼、不會產生
  任何名稱）。代理現在會對「有勾選且存活」的主機實際執行 `nmblookup -A <ip>`（或 `nbtscan`）查 NetBIOS 名、
  `avahi-resolve -a <ip>` 查 mDNS（.local）名，並回報結果。兩者各自記為**獨立的主機名稱來源**（`netbios` /
  `mdns`），可在 **名稱 / ARP 來源順序** 頁分別排序或停用。代理升到 v1.4.0（會自我更新）。SNMP 仍刻意不實作
  （需社群字串/憑證）。無 migration（觀測表 source 欄無 CHECK 限制）。

## [0.4.184] — 2026-06-16

### 變更
- **登入頁語言切換改為點開下拉再選**（列出兩種語言），不再是一按就切。
- **來源順序頁的「儲存順序」按鈕加上儲存 icon**（五個區塊都加）。

## [0.4.183] — 2026-06-16

### 變更
- **登入頁加上語言切換**（繁體中文 ⇄ English）於卡片標題列，登入前就能切換語言。
- **通知鈴鐺整理：**「通知」標題前與「全部標為已讀」按鈕都加了 icon；通知很多時改為在彈窗內捲動（限制高度），
  不再往下長過畫面。
- **IP 申請通知改為中文**（「IP 申請已核准」／「IP 申請已拒絕」），取代原本寫死的英文「IP request approved/rejected」
  （與其他站內通知一致）。
- **掃描代理表格欄寬：** 來源 IP 欄不再換行，多餘寬度由名稱與最後錯誤兩欄分攤，名稱欄不再過寬留白。

## [0.4.182] — 2026-06-16

### 變更
- **登入：SSO 按鈕只在該供應商已設定時才顯示。** `/auth/realms` 會一併回報 OIDC / SAML 是否啟用，登入頁
  只在該供應商真的設定好時才顯示對應按鈕——點「用 SAML 單一登入」不會再跳出原始的
  `{"detail":"SAML is disabled"}` 錯誤頁；兩者都沒啟用時整個「或使用 SSO」區塊隱藏。
- **登入：標題前方加上 jt-ipam logo。**
- **Webhook：事件改成附說明的勾選清單**，不再是自由輸入標籤。清單就是後端實際會發送的事件
  （`subnet.created`、`ip_request.created`／`.fulfilled`／`.rejected`、`anomaly.detected`）加上 `*`（全部），
  每項都有一行說明。
- **整合限定子網路範圍：版面更整齊。** 六個整合設定頁的子網路下拉與重疊警告改為整列堆疊，不再左右擠在一起。
- **RIPE／TWNIC 匯入：欄位不再太擠**——Handle／CIDR／目標 section 各列之間加了適當間距，提示文字不再緊貼下一個標籤。

### 新增
- **LLM 設定：可選的「對話上下文長度」(`num_ctx`)。** 讓管理員調高對話模型的上下文視窗，避免工具多、注入資料量
  大的 MCP 對話超過 Ollama 預設（約 4096）而被默默截斷。留空／0＝沿用模型/Ollama 預設；只帶進對話的 Ollama
  `options.num_ctx`（不影響嵌入模型）。

## [0.4.181] — 2026-06-16

### 變更
- **憑證詳細資訊排版更整齊。** 憑證「檔案」視窗裡各版本的詳細資訊（網域／主體／簽發者／序號／有效期／
  指紋／上傳時間）改成兩欄對齊的格線（定義清單），所有值對齊到同一欄，序號與指紋改用等寬字型。原本是
  參差不齊的「標籤：值」逐行清單。

## [0.4.180] — 2026-06-16

### 修正
- **Debian 13 上 nginx 設定測試失敗：`"server_tokens" directive is duplicate`。** 我們的 nginx 站台把
  `server_tokens off;` 放在 http 層級（被 include 檔的最上方）。Debian 13 的原廠 `nginx.conf` 現在自己的
  `http{}` 就有 `server_tokens off;`，同一層級再來一個就是致命 `[emerg]`（舊版 Debian/Ubuntu 是註解掉的、
  所以一直沒衝突）。把 `server_tokens off;` 改放進 `jt-ipam.conf` 與外部反代範本的每個 `server{}` 區塊 ——
  server 層級會與 http 層級共存／覆寫，在各發行版都正常。已用「父層 `http{}` 先設 server_tokens」的情境跑
  `nginx -t` 驗證。純設定範本改動。

## [0.4.179] — 2026-06-15

### 修正
- **沒有 `~/.nvm` 的主機上，安裝在印完 `Building frontend…` 後靜默中斷**（與 v0.4.178 同一類 `set -e` +
  `pipefail` 雷）。`ensure_node` 裡 `nb=$(find ~/.nvm/... | sort | head -1)`，當 `find` 遇到不存在的目錄
  （或 `head` 對 `sort` SIGPIPE）整個賦值就失敗，`set -e` 下會**沒有任何錯誤訊息**直接結束腳本 —— Node 沒
  裝、前端沒 build，但看起來只是「停住」。已對這行與其他 pipe-in-`$()` 之處（nvm 探測、admin 密碼產生、
  備份檔查找）補上 `|| true`，讓失敗／SIGPIPE 的管線不再中斷安裝。成功路徑完全不變（管線成功時 `|| true`
  為 no-op），原本能裝好的環境不受影響。純安裝腳本改動。

## [0.4.178] — 2026-06-15

### 修正
- **Debian 13 安裝失敗的真正根因：套件偵測在 `set -o pipefail` 下踩到 `grep -q` 的 SIGPIPE 雷。**
  `apt-cache madison <套件> | grep -q .` 在 madison 輸出多行版本時（例如 trixie 的 `postgresql-17` 會列
  兩筆——17.10 來自 -security、17.9 來自 main）會把套件誤判成「不存在」：`grep -q` 命中第一行就關閉管
  線，`apt-cache` 寫下一行時收到 SIGPIPE（rc 141），`pipefail` 再把整條管線判失敗。於是安裝腳本「看不到」
  原生的 PG 17＋pgvector（其實都在），退回 PGDG 又 FATAL。改用無管線的 `_pkg_installable()`（命令替換＋
  `[ -n ]`），同時套用到 PostgreSQL 與 Python 偵測迴圈。單一版本的發行版（Ubuntu 24.04）只輸出一行、不會
  踩到，所以只在 Debian 13 浮現。純安裝腳本改動。

## [0.4.177] — 2026-06-15

### 變更
- **安裝腳本在退回 PGDG 前先重整 apt 索引並重試。** 若第一次在預設庫找不到「PostgreSQL（>= 16）＋對應
  `postgresql-N-pgvector` 成對」的版本，腳本會先跑一次 `apt-get update` 再檢查，仍找不到才補 PGDG ——
  這樣「安裝當下 apt index 還沒更新好」的暫時狀況（Debian 13 明明有原生 PG 17＋pgvector 卻沒被選到的
  推測主因）就會乾淨走原生套件，而不是白繞 PGDG。純安裝腳本改動。

## [0.4.176] — 2026-06-15

### 修正
- **Debian 13（trixie）安裝不再卡在「`postgresql-16-pgvector` 無法安裝」而中斷**（客戶回報）。原本安裝腳本
  只挑「server 套件」再於退路硬寫 PG 16，但 PGDG 對 trixie 目前只出新版（17/18）的 pgvector、沒有
  `postgresql-16-pgvector` → 整支安裝 FATAL。改成挑選「**server 與對應 `postgresql-N-pgvector` 兩者都裝得
  到**」的 PostgreSQL 版本（先在預設庫試 16 → 17 → 18，找不到才補 PGDG 再找一次），不再硬退回 16。
  純安裝腳本改動。

## [0.4.175] — 2026-06-15

### 變更
- **設定檔產生器的服務多選格不再把長標籤折行**——服務多選改用 auto-fill 欄寬（最小 135px），足以容下
  最長的 profile 名稱（`wazuh-dashboard`）並讓每項標籤維持單行，原本只有那一項被折成兩行的情況解除。
- 文件：憑證派送說明改為「憑證檔案可以手動上傳或設定 URL／SFTP 來源定期自動同步」。

## [0.4.174] — 2026-06-15

### 變更
- **暫時隱藏 `jitsi` 與 `coturn` 兩個憑證派送服務類型**——docker-jitsi-meet 尚未正式支援，故服務選單與文件
  都不再列出這兩項（代理端的 profile 程式碼保留為休眠狀態，日後要重新開放很容易）。同時更新文件「畫面導覽」
  （新增一張憑證派送截圖）與功能地圖的「憑證集中保管與派送」分支。

## [0.4.173] — 2026-06-15

### 新增
- **自動抓取的憑證（SFTP／URL 來源）會自動補完整鏈。** 當同步抓到只含葉+中繼的新憑證時，jt-ipam 會在存檔前
  自動組好「中繼+根」的完整鏈（用抓來的檔案，或伺服器系統信任庫的公信根如 ISRG Root X1）—— 這樣每次續簽後
  Zimbra／PDM 等嚴格驗鏈服務都還是驗得過，不用再手動按一次「組合完整鏈」。
- 新增派送 profile **`jitsi`**（docker-jitsi-meet web：`/root/.jitsi-meet-cfg/web/keys/cert.{crt,key}`，
  重啟 jitsi web 容器）與 **`coturn`**（`/etc/coturn/certs/turn.{crt,key}`，root:65534 讓容器內使用者可讀 key；
  重啟 coturn 容器或原生 systemd coturn）。可一台同時派 jitsi + coturn，取代手動 renew 腳本。

## [0.4.172] — 2026-06-15

### 修正
- **派送代理 installer 在 LXC／容器裡不再無聲卡死**（IPv6 路徑死掉或防火牆黑洞時）。curl 改帶
  `--connect-timeout 10 --max-time 60 --retry 2`（IPv6 連不上約 10 秒就退回 IPv4，不再永遠卡住），
  並印出「Downloading agent…」、下載失敗時給清楚錯誤與連線測試提示。

## [0.4.171] — 2026-06-15

### 變更
- 派送代理對 Zimbra 的慢步驟即使沒加 `--debug` 也會印進度（「驗證中… / 部署中… / 重啟 Zimbra
  （zmcontrol restart，可能要幾分鐘）…」），正常執行時不再像當機卡住（zmcontrol restart 本來就要數分鐘）。
- 安裝程式產生的 nginx 站台設定檔（`deploy/nginx/*.conf` → `/etc/nginx/sites-enabled/jt-ipam`）
  註解全部改成**純英文**（在客戶端落地的設定檔不應有中文）。

## [0.4.170] — 2026-06-15

### 修正
- **Zimbra 派送以 root 跑 `zmcertmgr` 失敗**（`zmcertmgr: ERROR: no longer runs as root!`）。改成透過
  `su - zimbra` 執行，並把 cert/chain/key 暫存到 zimbra 可讀的目錄（`/opt/zimbra/ssl/jt-ipam`），
  比照 Proxmox/Zimbra 官方流程。
- 憑證派送現況不再對「實際失敗」的部署顯示「最新」— 狀態現在要同時指紋相符**且**回報 ok。

### 新增
- **憑證鏈檢查 + 一鍵修復。** 憑證資訊／檔案視窗會分析每個版本的鏈：「完整鏈」（已鏈到根 CA）、
  「可組合完整鏈」（根在但目前鏈沒帶到 → 按**組合完整鏈**就地修正、指紋不變）、或「缺根 CA」
  （附上如何取得並重新上傳根 CA 的提示）。Zimbra / PDM 等嚴格驗鏈服務需要完整鏈。
- **檔案視窗改為詳細的憑證資訊頁**：網域（SAN）、主體、簽發者、序號、有效期、完整 SHA-256 指紋
  （可複製）、上傳時間，加上各格式下載。
- 憑證、派送代理、憑證派送現況三頁加上**匯出按鈕**（後兩頁原本漏做）。
- 憑證派送現況改為**一個代理一列**、服務彙整在欄位內（如 `pbs, pve`），不再每個 deployment 各一列；
  狀態 hover 會列出每個憑證／服務。

## [0.4.169] — 2026-06-15

### 修正
- **修正 `pdm`(Proxmox Datacenter Manager)profile** 為官方路徑與服務:cert+chain →
  `/etc/proxmox-datacenter-manager/auth/api.pem`、key → `…/auth/api.key`(root:www-data 640),
  重載 `systemctl restart proxmox-datacenter-api.service`。(先前的路徑/服務是錯誤推測。)
- **所有產生的含 sudo 指令都改為會判斷 root。** 抽出共用 `SUDO`(`$([ "$(id -u)" -ne 0 ] && echo sudo)`),
  套用到:派送代理的 dry-run/正式執行指令與安裝/移除一行式、掃描代理安裝一行式、探測工具 `apt install` 提示。
  在本來就是 root、且沒有 sudo 的主機上現在可直接執行。

### 新增
- 派送代理新增 **`--debug`** 旗標(預設關閉):印出每個執行的指令並顯示 config-test／reload／`zmcertmgr`／
  下載 的完整輸出 — 方便診斷例如 Zimbra `verifycrt` 失敗(常見原因是憑證鏈缺了根 CA)。

### 變更
- 安裝說明第 3 步改成**先帶到「產生設定檔」工具**(快速路徑),手動編輯設定降為次要說明。

## [0.4.168] — 2026-06-15

### 修正
- **重要:0.4.167 的條件式 sudo 一行式指令在 root 下會壞。** `$(…)` 在 root 展開成空字串時,後面的
  `VAR=value` 會被當成「指令」而非賦值(`JT_IPAM_URL=…: No such file or directory`)。改用 `env` 當指令字
  (`… | $([ "$(id -u)" -ne 0 ] && echo sudo) env JT_IPAM_URL=… bash`),root / 非 root 都正確。
- AI 對話標題列的動作鈕改為確實靠右(標題列換行時原本會偏左)。

### 變更
- 派送代理的**安裝說明視窗不再重複放完整安裝指令** — 每個代理自己的視窗已顯示帶好 key、自動判斷 sudo 的
  一行式指令,安裝說明改為指向那裡,只保留支援作業系統一覽。
- 一行式指令標題由「(root)」改為「(自動判斷 root / sudo)」;「發行版」→「發行版本」。

## [0.4.167] — 2026-06-15

### 修正
- 派送代理的安裝／移除一行式指令改成**只有非 root 時才加 `sudo`**(`$([ "$(id -u)" -ne 0 ] && echo sudo)`)。
  在本來就是 root、且沒有 `sudo` 的主機(Proxmox VE／PBS／PDM 與精簡 appliance 很常見)原本 `| sudo … bash`
  會出 `sudo: command not found`;現在直接以 root 執行。

## [0.4.166] — 2026-06-15

### 修正
- **刪除仍被派送代理選用的憑證會被擋下**(409,並列出使用它的代理名稱),不再讓代理的可取憑證殘留孤兒 UUID。
  編輯代理視窗對已經是孤兒的項目也改顯示「<id>…（憑證已刪除）」方便移除,不再顯示裸 UUID。

### 新增
- 新增派送 profile:**`pdm`**(Proxmox Datacenter Manager)與 **`wazuh-dashboard`**(OpenSearch Dashboards)。
  Univention UCS 評估後刻意留給手動模式(憑證路徑含 FQDN、且由 UCS 內部 CA 管理,內建固定路徑不適合)。
- 派送代理清單可**依憑證篩選**(篩出可取某張憑證的代理),與既有的名稱/IP 篩選並列。

### 變更
- 派送代理的**「已部署／回報」數字**改為 hover 顯示實際派送了哪些憑證／服務與狀態。
- **精簡派送代理 installer 安裝完成後的輸出** — 改成一段精簡摘要(timer、設定檔狀態、可派送憑證、測試/套用指令、記錄),
  不再一長串。

## [0.4.165] — 2026-06-15

### 變更 — 表格分頁一致化 + 篩選框對齊
- 把全站共用的 `useTablePagination`(每頁筆數綁使用者偏好、跨裝置同步)套到所有還沒套的 client 端清單表格：
  憑證 + 派送代理表格、唯讀「憑證派送現況」頁,以及進階資源、實體(佈線/電力/VPN)、虛擬化、VLAN/VRF、
  NAT、裝置、掃描代理、群組、權限指派、Wazuh、異常偵測、防火牆別名對應、客戶子表與裝置連接埠等一輪掃過。
  server 端分頁的表格(IP 位址、稽核、使用者、作業、IP 異動)與小型固定設定/實例面板維持不分頁。
- 修正憑證/代理/現況頁的**篩選框**比工具列按鈕矮的問題(工具列按鈕被強制 34px,篩選框改用預設尺寸對齊)。

## [0.4.164] — 2026-06-15

### 新增 — 憑證的 AI 對話 / MCP 工具
- 新增兩個唯讀 MCP 工具,讓 AI 對話(與外部 MCP 客戶端)能回答憑證相關問題:
  - `list_certificates` — 憑證中繼資料:名稱、網域、目前版本指紋、到期日、剩餘天數、版本數、是否自簽、
    自動抓取來源;`expiring_within_days` 可只列即將到期的。
  - `list_cert_distribution` — 派送代理與各站台部署現況(憑證/服務、是否最新或飄移、到期日、代理版本,
    以及同一把 Key 是否被多台主機共用)。
- 兩者皆**唯讀、絕不回傳私鑰 / PEM**,並比照憑證派送現況頁歸為全域基礎設施資料(僅管理員或具全域讀取權限者),
  零權限/部門帳號預設看不到。

## [0.4.163] — 2026-06-15

### 新增
- **自簽憑證手動續簽** — 自簽憑證新增「續簽」動作，沿用目前 CN／SAN 重新簽發一張新版本（可調效期天數），
  派送代理下次比對指紋不同就自動更新各站台。
- **同一把 Key 多主機偵測** — 代理記錄近期回報來源 IP（migration 0077，`recent_sources`）；同一把 Key 在
  7 天內被多個 IP 回報時，派送代理清單會在來源 IP 旁標示警告，新增代理視窗與安裝說明也提示**一台主機一把 Key**。
- **代理 CLI 選項** — `--help` 用法、`--upgrade`（更新代理自己到 server 最新版後結束，即使 `AUTO_UPDATE=false` 也會更新）、
  `--force`（即使已是最新也強制重新派送）。
- 憑證與派送代理表格新增名稱／IP **篩選框**；唯讀「憑證派送現況」頁（進階）新增欄位選擇、欄位排序、篩選列，
  以及**來源 IP + 代理版本**欄。頁籤加上 icon。

### 變更／修正
- **代理已是最新時也會回報** — 原本重設 Key 後的代理會顯示 `0／0`，因為「已是最新」的路徑不回報；
  現在每次執行都回報目前狀態。
- 版本欄「可更新」由文字標籤改成單一 icon，且與版本同列不換行。

## [0.4.162] — 2026-06-15

### 新增 — 派送代理支援更多網頁伺服器／服務 profile
- 憑證派送代理（以及**產生設定檔**工具與安裝腳本）新增 9 個 profile：
  **caddy / traefik / lighttpd / zoraxy / jetty / exim4 / mosquitto / cockpit / webmin**（原有
  nginx / apache / haproxy / postfix / dovecot / pve / pmg / pbs / zimbra 之外）。每個 profile 都提供固定
  寫入路徑與重載指令；**jetty** 會收到 **PKCS#12 keystore**（`<cert>.p12`），由 `GET /cert-agents/bundle/raw`
  新增的 `part=pkcs12` 提供。

### 變更 — 安裝說明 UX
- 支援的作業系統／發行版以醒目標籤呈現（Debian／Ubuntu／RHEL 家族／Fedora／SUSE）。
- 修正 curl 一行式指令第一行開頭被縮排一格的問題（行內 `<code>` 改 `display: block`）。
- 隱藏工具列獨立的**設定檔說明**按鈕 — 設定檔產生改由各代理操作欄的**產生設定檔**功能負責；安裝說明第 3 步
  指向它（並附上該按鈕圖示）。

## [0.4.161] — 2026-06-15

### 新增 — 憑證檔案檢視與多格式下載
- 憑證列操作欄新增**檔案**鈕：點開列出該憑證各版本（指紋 / 到期日 / 網域 / 目前版本），可逐版本**下載**並選
  格式：完整鏈 / 憑證(.crt) / 中繼鏈 / 私鑰(.key) / 合併 / **DER** / **PKCS#12(.pfx)**（後端用 cryptography 轉檔）。
  含私鑰的格式（key / combined / pfx）逐次寫稽核（`GET /certificates/{id}/versions/{vid}/file?fmt=`）。

## [0.4.160] — 2026-06-15

### 變更
- 憑證 / 派送代理表格操作欄最右側（刪除鈕）加留白，不再貼齊邊緣。
- 憑證已設來源或已有版本時，**「產生自簽」按鈕停用**（避免覆蓋現有憑證），hover 有說明。
- installer 設定檔註解加提示：可到 jt-ipam 用該代理操作欄的「產生設定檔」工具快速產生內容。

### 安全
- 修 Dependabot 警示(GHSA-gv7w-rqvm-qjhr，High)：透過 pnpm override 把 **esbuild 升到 0.28.1**（原 0.25.12
  經 vite 帶入，<0.28.1 有「Deno 模組二進位完整性」漏洞）。屬建置期 dev 相依、且本專案走 Node/vite 不用其
  Deno 安裝路徑，實際不可觸發;升級後前端建置正常通過。

## [0.4.159] — 2026-06-15

### 變更 — 設定檔產生器更完整
- 每個「憑證／服務」區塊現在會**直接產生該服務的 SSL 設定片段**（如 nginx 的 ssl_certificate /
  ssl_certificate_key、apache 的 SSLCertificate* 等），並對**每個寫入路徑與設定片段都加複製按鈕**。
  pve/pmg/pbs 等讀固定路徑的服務則標示「不需改服務設定」。
- 底部加上**完整的試跑 / 正式執行指令**（含 sudo bash 全路徑）＋複製按鈕，不必再自己拼。
- 服務勾選改為**整齊的格狀排列**。

### 新增 — 派送代理可編輯 / 啟用切換
- 派送代理操作欄新增**編輯**鈕：可事後改名稱、**調整可取憑證範圍**（之後要多放幾個網站直接加）、啟用狀態。
- 「啟用」欄改成**開關**，一鍵停用 / 啟用。
- 「可取憑證」欄滑過去顯示**是哪些憑證**（名稱）。
- 「重設 Key」「檢視 Key」提示文字標明是**代理連線用的 Key（非 SSL 憑證）**。
- 安裝說明步驟 3 提示可用操作欄的「產生設定檔」工具（附 icon）；工具列移除「設定檔說明」按鈕（改由安裝說明內進入）。

## [0.4.158] — 2026-06-15

### 新增 — 派送代理頁多項改善
- **設定檔產生器**（操作欄工具鈕）：選憑證（此代理範圍內）＋勾選服務（nginx/apache/pve…可多選）即自動產生
  快速模式設定；可展開「進階／手動模式」填自訂路徑。即時預覽 ＋ 一鍵複製，貼到主機設定檔即可。
  下方並**列出快速模式憑證會寫到主機的完整路徑與檔名**（每個 cert／key／chain），方便把服務設定指過去。
- 「已部署 / 回報」欄加 tooltip 說明（成功套用數 / 代理回報的派送總數）。
- **安裝說明瘦身**：把設定檔格式說明拆成獨立的**「設定檔說明」**按鈕，安裝說明只留安裝/移除步驟。
- **顯示伺服器最新代理版本**：派送代理頁工具列顯示 server 端目前 agent.sh 版本（`GET /cert-agents/server-version`）。
- 派送代理資訊視窗的「關閉」按鈕補上 icon。

## [0.4.157] — 2026-06-15

### 變更
- installer 設定檔範例的 `DEPLOY_1_CERT` 改用通用佔位符 `example.com`（RFC 2606 保留網域），不再放真實憑證
  名稱；真實可派送的憑證名稱仍列在上方「This agent is allowed to deploy」清單供使用者替換。

## [0.4.156] — 2026-06-15

### 變更 — installer 自動帶出此代理可派送的憑證名稱
- 安裝時 installer 會用代理金鑰向 server 查「這把代理可派送哪些憑證」，把實際的憑證名稱**列在設定檔註解
  並預填到 `DEPLOY_1_CERT` 範例**，使用者不必再猜 `DEPLOY_<N>_CERT` 要填什麼（＝jt-ipam 憑證頁的名稱）。
- 安裝/重跑結束時也會印出此代理可派送的憑證清單（設定檔已存在時不覆蓋，但會印清單供參考）。

## [0.4.155] — 2026-06-15

### 修正
- 派送代理表格:版本欄「可更新」標籤改為**可換行**並加寬欄位,不再溢出到「來源 IP」欄。
- 名稱與最後回報兩欄都設為彈性,多餘寬度由兩欄平分,名稱欄不再獨自過寬。

## [0.4.154] — 2026-06-15

### 變更
- 派送代理設定檔範本重新分成兩區塊:**快速模式（優先）**與**手動模式**。快速模式的註解直接列出**每個 profile
  會把 cert / key / chain 寫到哪個路徑與檔名**,並附上對應的 nginx / apache 指令,讓你知道要把服務設定指到哪。

## [0.4.153] — 2026-06-14

### 變更
- 派送代理設定檔範本的註解**完整列出所有內建 profile**（nginx / apache / haproxy / postfix / dovecot /
  pve / pmg / pbs / zimbra / generic），含各自的預設檔案路徑與重載指令，打開設定檔就看得到能用哪些。

## [0.4.152] — 2026-06-14

### 變更
- 派送代理設定改以 `DEPLOY_<N>_PROFILE`（服務）為主，**重載指令由 profile 提供**，一般情況不必再寫
  `DEPLOY_<N>_RELOAD`。可只設「憑證 + 服務」，或再加自訂路徑（`FULLCHAIN`/`KEY`…）覆寫位置但仍沿用 profile
  的重載；`DEPLOY_<N>_RELOAD` 降為自訂服務時才用的進階覆寫。範本與安裝說明同步更新。

## [0.4.151] — 2026-06-14

### 變更 — 派送代理設定改一行一個設定
- 派送代理設定檔從一行擠一堆（`DEPLOY_1="cert=..; profile=..; fullchain_path=.."`）改成**一行一個設定**的
  `DEPLOY_<N>_*` 群組,好讀好改:
  - `DEPLOY_1_CERT=`（要派送的憑證）、`DEPLOY_1_FULLCHAIN=`（憑證檔路徑）、`DEPLOY_1_KEY=`（私鑰路徑）、
    `DEPLOY_1_RELOAD=`（重載指令）;另有 `DEPLOY_1_CHAIN/CRT/COMBINED/TEST` 可選。
  - 或只設 `DEPLOY_1_CERT=` ＋ `DEPLOY_1_PROFILE=nginx`（內建 profile 用固定路徑）。
- installer 設定檔範本、安裝說明彈窗範例同步更新。已對 prod 實機驗證新格式 dry-run ＋ 真套用。

## [0.4.150] — 2026-06-14

### 變更
- 派送代理腳本（`jt_ipam_cert_agent.sh` 與 installer）全部改英文（註解、終端輸出、設定檔範本），
  比照 `scripts/*.sh` 慣例——在客戶終端機執行的腳本不夾中文。
- installer 新增**移除**模式：`JT_IPAM_UNINSTALL=1` 會停用並移除 timer / service、代理程式、設定檔與狀態
  （已派送到各服務的憑證檔保留不動）；安裝說明彈窗加上「移除派送代理」的一行式指令。

## [0.4.149] — 2026-06-14

### 新增 — 派送代理可再次檢視 Key 與安裝指令
- 派送代理的 enroll key 現在除了存 hash，另以 AES-GCM 加密保存，**之後可在列表的「檢視」再次取得**
  （管理員限定，`GET /cert-agents/{id}/key`）。操作欄新增「檢視」按鈕，點開顯示 Key ＋ 一行式安裝指令
  （已含 key）＋複製按鈕。
- 建立 / 重設 Key 的視窗也一併顯示一行式安裝指令；「離開後無法再取得」改為「之後可在列表按『檢視』再次取得」。
- 刪除代理時一併清掉加密保存的 Key。
- 舊版建立、未保存明文的代理檢視會回提示，請改用「重設 Key」。

## [0.4.148] — 2026-06-14

### 變更
- 按下「自動產生並安裝金鑰」後，「登入私鑰」欄位改為唯讀（disabled）並顯示「已由 jt-ipam 產生並儲存」，
  避免使用者誤以為還要自己貼私鑰。

## [0.4.147] — 2026-06-14

### 修正
- 憑證表格欄寬：操作欄改 `fixed: "right"` 釘在右側（視窗再窄也不會被推出畫面），加寬到容得下全部 icon；
  名稱與網域兩欄設為彈性平分多餘寬度。
- 繁中文案改用全形標點（，。；：（）「」），並把對岸用語改成台灣慣例：回滾→還原、一次性→單次、
  原子覆蓋／原子寫入→不中斷換檔／不中斷寫入（派送代理安裝說明、來源設定、代理腳本註解等）。

## [0.4.146] — 2026-06-14

### 變更 — 派送代理改純 bash（移除 Python / PyYAML 相依）
- 派送代理重寫為**純 bash**(`jt_ipam_cert_agent.sh`),只相依 **curl + coreutils**,不再需要 Python / jq / YAML。
  設定檔改 `KEY=VALUE`(`/etc/jt-ipam-cert-agent/config`,`DEPLOY_N="cert=..; profile=.."`);profiles / 原子寫入 /
  config-test / reload / 回滾 / `--dry-run` / 自我更新 全部保留。
- 後端配合純 bash:`GET /cert-agents/check?format=text`(逐行,免解 JSON)、新增
  `GET /cert-agents/bundle/raw?cert=&part=cert|key|chain|fullchain|combined`(直接回原始 PEM,`curl -o` 寫檔,
  附 `X-Cert-Fingerprint` header)、`POST /report` 兼收 TSV。下載端點改 `agent.sh`,版本/自我更新比對改 `.sh`。
  installer 不再裝 python3-yaml。
- 安裝說明彈窗重新排版(編號步驟 + 留白),需求改「純 bash,只需 curl + coreutils」。

## [0.4.145] — 2026-06-14

### 修正 / 變更
- 憑證 / 派送代理表格補上 `:scroll-x`(對齊全站作法):名稱欄不再過寬撐版、操作欄不再被推出畫面右側;
  視窗較窄時改為水平捲動而非裁切。
- 憑證來源類型選擇器:**被選中的類型整顆填綠底白字**(原本只有細邊框,看不出選了哪個);
  「不自動（手動上傳）」文字精簡為**「手動上傳」**。

## [0.4.144] — 2026-06-14

### 變更
- 憑證 / 派送代理操作欄按鈕改**靠左對齊**（移除置中）,與全站其它列表頁一致。

## [0.4.143] — 2026-06-14

### 修正 — commit 後序列化的一類 500（流程檢查找出）
- `updated_at` 有 SQL 端 `onupdate=func.now()`,UPDATE flush 後該欄過期;憑證模組數個端點 commit 後直接
  `model_validate` ORM 物件 → 同步情境 lazy IO `MissingGreenlet` 500。補上 commit 後 `session.refresh`
  (與其它端點一致):`PATCH /certificates/{id}`、`PATCH /cert-agents/{id}`、`POST /cert-agents/{id}/rotate-key`
  (v0.4.142 已先修 `PUT /certificates/{id}/source`)。

### 變更 — 自動產生金鑰時直接登入主機安裝公鑰
- 既然 jt-ipam 已有 SFTP 登入密碼,「自動產生金鑰」現在會**直接用密碼登入主機,把公鑰寫進
  `~/.ssh/authorized_keys`**(冪等、不重複),免使用者手動貼。安裝成功顯示「已安裝」;沒有密碼或安裝失敗
  則金鑰仍已產生,退回顯示公鑰供手動貼上並附原因(`POST /certificates/{id}/source/ssh-keypair` 改收來源
  設定 + 回 installed/message)。

## [0.4.142] — 2026-06-14

### 修正
- **設定 SFTP/URL 來源儲存時 500**(MissingGreenlet):`PUT /certificates/{id}/source` commit 後
  `model_validate` 在同步情境觸發 lazy IO → 改 commit 後 `session.refresh(cert)` 再序列化。

### 新增 — 憑證來源測試連線 + 自動產生 SSH 金鑰
- 來源設定加**「測試連線」**鈕:以表單目前內容(密碼/私鑰留空＝沿用已存)實際試連 URL / SFTP,
  回成功訊息或可讀失敗原因,不存檔(`POST /certificates/{id}/source/test`)。
- SFTP 登入私鑰加**「自動產生金鑰」**鈕:jt-ipam 產生 ed25519 金鑰對、私鑰 AES-GCM 加密儲存(不回明文),
  回**公鑰**供貼到 SFTP 主機 `authorized_keys`(`POST /certificates/{id}/source/ssh-keypair`)。

### 變更
- 憑證 / 派送代理操作欄按鈕改 **icon-only + hover tooltip**(與全站列表一致)、欄寬收緊並置中,
  解決過寬左空、右側溢出、icon 偏左問題。

## [0.4.141] — 2026-06-14

### 修正 / 變更
- 「系統有更新」重新整理橫幅的 icon 與文字未垂直置中 — icon 改置中於 16×16 框、容器與文字 `line-height:1`。
- 憑證表格「到期」欄拆成**「到期日」**與**「剩餘天數」**兩個獨立欄位（各自可排序、可選擇）。
- 憑證 / 派送代理表格操作欄 icon 改**置中**（欄位 `align:center` + NSpace `justify:center`）。

## [0.4.140] — 2026-06-14

### 變更 — 憑證自動抓取來源設定 UX
- SFTP 來源設定釐清:**「登入密碼」/「登入私鑰（SSH 金鑰，PEM）」**獨立成「SFTP 登入認證」區塊
  並移到帳號下方,加說明「用來登入 SFTP 主機,密碼或 SSH 私鑰二擇一(私鑰優先);憑證本身的私鑰是
  下方 key_path 遠端檔案,與此無關」。遠端檔案路徑(cert_path/key_path/chain_path)另成一區。
  (後端早已支援 SSH 私鑰登入,只是欄位位置/命名易誤解為憑證私鑰。)
- 來源類型「不自動」改顯示**「不自動（手動上傳）」**,讓使用者知道仍可手動上傳 / 貼上 / 自簽。

### 變更 — 憑證 / 派送代理表格比照全站
- 兩張表格欄位**可排序**(autoSort)、加**欄位選擇器**(ColumnPicker,偏好存後端跨裝置同步)。
- 操作欄按鈕改 **icon + 文字**,欄寬不足時自動收成**只剩 icon**(col-actions 容器查詢,hover 仍有提示)。

## [0.4.139] — 2026-06-14

### 新增 — 派送代理版本顯示與自我更新
- 管理頁「派送代理」分頁新增**版本號**(落後 server 時標「可更新」並提示)與**來源 IP** 欄位,
  比照掃描代理。
- 派送代理新增**自我更新**:`/check` 回傳 server 端 agent.py 的 sha256,代理比對自己不同就下載新版、
  原子覆蓋並以新版重新執行(下載後驗 sha 才覆蓋,失敗只記錄不中斷部署)。config 設
  `auto_update: false` 可停用。
- 唯讀「憑證派送現況」頁(`GET /cert-agents/status`)一併回傳 `last_source_ip` /
  `server_agent_version`。

## [0.4.138] — 2026-06-13

### 新增 — 憑證自動抓取來源
- 憑證除了上傳 / 貼上 / 自簽,現在可設**自動抓取來源**:系統定期(或按「立即抓取」)從來源拉續約後
  的 bundle,**只有內容真的變了才存新版** —— fingerprint 與目前版本相同就跳過(不處理)。來源若沒
  提供私鑰,沿用目前版本的 key(多數續約不換 key)。
- 來源:**URL**(走 SSRF 防護的 safe_http)與 **SFTP**(asyncssh;host 先過 SSRF 黑名單)。帳密
  (SFTP 密碼 / 私鑰)AES-GCM 加密(`encrypted_secret`)、不回明文。新增 migration `0076`。
- 端點:`PUT /certificates/{id}/source`、`POST /certificates/{id}/fetch-now`;sync timer 依各憑證
  間隔自動抓取。前端:每張憑證的來源設定(URL/SFTP)+「立即抓取」,並顯示上次抓取錯誤。
- CIFS / NFS 暫不做(backend 非 root 無法掛載);請改用預掛路徑或 URL/SFTP。

## [0.4.137] — 2026-06-13

### 修正
- **憑證頁 405 /「伺服器發生錯誤」(憑證 API client 路徑漏 /api/v1)** — `certificates.ts` 的
  API 呼叫(以及 `integrations.ts` 的重疊網段檢查)漏掉共用 axios client 需要的 `/api/v1` 前綴
  (它的 baseURL 是 `/`),導致請求打到 SPA 路徑(`/certificates`、`/cert-agents`)→ nginx 對
  POST 回 405、對 GET 回 index.html。已全部補上正確前綴。憑證管理頁、派送代理、產自簽、進階現況
  頁都正常了。
- 補上憑證/代理「儲存」按鈕漏掉的 icon。

## [0.4.136] — 2026-06-13

### 憑證派送 — 體驗
- 憑證版本上傳新增**貼上 PEM 文字**(憑證 / 私鑰 / 中繼)選項,與上傳檔案二選一(對話框上方切換)。
- 進階選單的唯讀憑證頁標籤改與管理頁一致(憑證派送)。

## [0.4.135] — 2026-06-13

### 憑證派送 — 後續補強
- **跨發行版 agent 安裝器** — cert-agent 安裝器自動偵測套件管理器(apt / dnf / yum / zypper),
  支援 Debian 11/12/13、Ubuntu 22.04/24.04/26.04、RHEL / Rocky / AlmaLinux / CentOS、Fedora、
  openSUSE/SLES(皆 systemd);PyYAML 依各發行版正確套件名安裝。
- **新增 profile** — 加入 `pbs`(Proxmox Backup Server:`proxy.pem`/`proxy.key`,重載
  `proxmox-backup-proxy`);`apache` profile 改為重載 `apache2` 或 `httpd`(哪個有用哪個),
  Debian/Ubuntu 與 RHEL/SUSE 都能用。
- **「安裝說明」按鈕**(派送代理分頁,比照掃描代理):一行式安裝指令、設定檔範例、支援的發行版、
  `--dry-run` 提示。
- **進階下的唯讀憑證現況** — 非管理員的唯讀檢視者(具萬用讀取)現在可在「進階」看各代理的派送現況
  (最後更新、有效日、到期日、剩餘天數、是否最新/飄移)。新增 `GET /cert-agents/status`
  (`require_global_read` 把關)。

## [0.4.134] — 2026-06-13

### 修正
- **Debian 12 加 PGDG repo 時若 keyring 檔已存在會失敗（客戶回報）** — 安裝腳本把
  `gpg --dearmor` 寫到 `/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg`（那是
  `postgresql-common` 套件自己的檔）。該檔已存在時 gpg 會跳「File exists. Overwrite?」、
  非互動下直接失敗 → 金鑰沒寫成 → PGDG 簽章無效 → `postgresql-16-pgvector` 變「not installable」。
  現在改寫到自有的 `/etc/apt/keyrings/jt-ipam-pgdg.gpg` 並加 `gpg --dearmor --yes`（不撞檔、可重入）。
  已在 Debian 12 容器端到端驗證。

### 新增 — 憑證集中保管 + 派送（商業憑證一次上傳、派到所有站台）
- 集中保管商業憑證,搭配 pull 模型的派送代理。續約後只要上傳一次 bundle(crt/key/chain),
  各站台代理會自動取走新版、寫到正確路徑、跑 config-test、重載服務,失敗自動回滾。
- **後端**:migration `0075`(`certificates` / `cert_versions` / `cert_agents`);私鑰以 AES-GCM
  加密儲存,任何管理 API 都不回傳明文。`/certificates` admin CRUD + `POST /{id}/versions`
  (驗證 key↔cert 配對、SAN/效期,擋不配對/過期/重複)+ **`POST /{id}/self-signed`**
  (產生自簽憑證,可自訂 CN/SAN/天數 —— 商業憑證還沒到時先頂著)。`/cert-agents` admin CRUD +
  key 輪替,以及 agent 協定(`X-Agent-Key`):`check` / `bundle`(解密私鑰,scope 限定、逐次稽核)
  / `report`。
- **代理**(`agent/jt_ipam_cert_agent.py` + 安裝器):pull 模型,內建 service profiles
  (nginx / apache / haproxy / pve / pmg / postfix / dovecot / zimbra / generic),原子寫入 +
  時間戳備份 + config-test gate + 回滾,冪等,並支援 **`--dry-run`**。設定是每台主機一份小 YAML,
  列出哪些憑證用哪個 profile 派送。
- **監控**:每日到期告警 +**飄移偵測**(某代理回報的指紋不是目前版本 → 那台沒換成功)走既有
  通知/鈴鐺。
- **前端**:憑證管理頁(上傳、產自簽、版本/到期狀態、代理 + 一次性 key、scope)。

## [0.4.133] — 2026-06-13

### 修正
- **最小化 Debian 12 / 13 容器安裝失敗（客戶回報）** — 乾淨容器映像上兩個缺口：
  - 「加 PGDG repo」那步用 `curl | gpg`、後面 PostgreSQL 設定全用 `sudo -u postgres`，但
    `ca-certificates` / `curl` / `gnupg` / `sudo` 不保證存在（最小化 Debian 容器常拿掉）。
    Debian 12 預設庫是 PG15、必走 PGDG → 在 `curl` 那步失敗；補完 PG 後又卡在
    `sudo: command not found`。現在開頭就先把這四個裝齊。
  - 搭配 v0.4.131 的 `apt-cache madison` 版本偵測，現在的對應是：Debian 12 → PGDG 裝 PG16；
    Debian 13 → 用發行版自帶的 PG17（+ `postgresql-17-pgvector`，不必 PGDG）；Ubuntu 24.04 →
    自帶 16；Ubuntu 26.04 → 自帶 17/18。app 對 PG 16/17/18 皆相容。

## [0.4.132] — 2026-06-12

### 修正
- **CSV 實際匯入 500（客戶回報 / issue #4）** — 匯入端點把 `subnet.cidr`（asyncpg 回傳的
  `IPv4Network` 物件，不是 str）當成背景作業的 VARCHAR `target_label` → asyncpg `DataError`。
  dry-run 不受影響（不會 spawn task），所以「模擬匯入正常、實際匯入 500」。已用 `str()` 轉型。
- **IP 申請列表在有手動指定 IP 時 500（issue #4）** — asyncpg 從 `INET` 欄位回傳 `IPv4Address`，
  但 `IPRequestRead.requested_ip` 宣告為 `str`，Pydantic 驗證失敗 → 整個列表頁 500。比照
  `IPAddressRead.ip` / `SubnetRead.cidr` 既有作法加 `mode="before"` 強制轉 str。
- **掃描代理無法回傳主機名稱（客戶回報）** — 回報帶 rdns/NetBIOS/mDNS/OS 主機名稱時，對「新發現
  的 IP」會 500。session `autoflush=False` + UUID 由 DB 端產生，剛 add 的 `IPAddress` 此時 `id=None`，
  `apply_observation` 建 hostname 觀測 FK row 時違反 `NOT NULL`。改成建立 IP 後立即 flush，讓
  id 先有值。（只有 icmp+arp 的回報不受影響，因為不會呼叫 `apply_observation`。）
- **一併修補同類 asyncpg INET/CIDR 當 str 的隱性 bug**：其他以 `model_validate(ORM)` 組裝、漏掉
  轉型的 read schema — `APITokenRead.last_used_ip`、`VMInterfaceRead`（`primary_ip`/`mac`）、
  `ARPEntryRead`/`FDBEntryRead`（`ip`/`mac`）。

## [0.4.131] — 2026-06-12

### 修正
- **Ubuntu 26.04 安裝失敗（客戶回報）** — 安裝腳本原本寫死 `postgresql-16`，但 Ubuntu 26.04
  預設庫沒有 PG 16（改出 PG 17/18）。舊的退路會去加 PGDG 對應「新發行版 codename」的庫，而
  PGDG 對剛發布的 Ubuntu codename 常延遲數月才上架 → `apt-get update` 404、整個安裝中斷。
  現在改成偵測「已啟用的庫裡可用的 PostgreSQL 版本」（優先 16，否則用發行版自帶的 17/18…）
  並安裝對應的 `postgresql-N-pgvector`；只有在完全找不到 `postgresql-N`（>=16）時才退回 PGDG。
  app 對 PG 16/17/18 皆相容。Python 偵測也加入 `python3.14`（Ubuntu 26.04 預設）。

### 修正
- **ARP 紀錄保留** — `arp_entries` 原本只新增/更新、從不回收，長期會無限累積（MAC↔IP 變動、
  來源 device 被刪的孤兒 row 都各留一筆）。同步排程現在每輪刪除 `last_seen_at` 超過
  `ARP_RETENTION_DAYS`（預設 30 天；設 0 停用）的舊 ARP，含孤兒 row。

### 新增
- **整合設定頁的重疊網段警告** — 當存在重疊網段（同一 IP 可能出現在多個子網路）且某整合
  （LibreNMS / OPNsense / Wazuh / Proxmox / AdGuard / DNS）未設定限定子網路範圍時，設定表單
  會顯示警告：同步可能把存活狀態／DHCP／MAC 標到錯誤單位的同 IP，並引導去設定關聯子網路範圍。
  新增 `GET /subnets/overlaps/exists`（admin）。

### 說明
- 沒有新的重複 IP／重複 ARP 風險：`ip_addresses` 對 `(subnet_id, ip)` 唯一；`arp_entries` 以
  `(ip, mac, device_id)` upsert；只有 LibreNMS 會寫 ARP（scanner 與 OPNsense 只 stamp 既有 IP）。
  重疊網段下「同 IP 字串跨子網路多筆」屬設計上正常。

## [0.4.129] — 2026-06-11

### 安全性
- **RBAC IDOR 修補** — 多個詳情／彙整端點只憑物件 id 取資料、未做物件層級可見性檢查，
  讓任何已登入帳號都能讀到範圍外的物件：`GET /devices/{id}` 及其子資源（`/integrations`
  會洩漏 Wazuh CVE 數量＋Proxmox VM，另含 `/librenms`、`/vlans`、`/relations`）、
  `GET /customers/{id}` 與 `/{id}/summary`（傾印整個客戶資產）、`GET /racks/{id}/diagram`。
  以上全部改為需要物件 `read` 權限（無權限回 404）。MCP `get_topology` 工具不再把整張拓樸
  洩漏給受限帳號（原本漏傳 `user` 過濾），並歸為全域讀取；REST `GET /topology` 也補上
  `require_global_read` 一致化。
- **OIDC ID Token 驗簽** — callback 原本只把 ID Token base64 解開就信任其 claims（含決定
  admin 提權的 `groups`），未驗簽章。現在改用 provider 的 JWKS 驗證 ID Token（簽章＋
  `aud`/`iss`/`nonce`）後才信任 claims；驗證失敗則退回只用 userinfo，不信任未驗的 groups。
- **CSV 匯出公式注入** — IP 位址 CSV 匯出對以 `= + - @`／tab／CR 開頭的欄位前置跳脫，
  避免試算表把內容當公式執行。

### 修正
- **整合同步韌性** — `jt-ipam-sync.py` 在每個整合的例外處理寫 `last_error` 前先 rollback；
  單一實例失敗（例如 AdGuard 在重疊網段上 `MultipleResultsFound`）不再中斷整輪同步。
- **重疊網段** — AdGuard 同步（`sync_clients` / `sync_rewrites`）與 MCP ARP 查詢以
  `scalar_one_or_none()` 比對 `IPAddress.ip`；重疊網段下同一 IP 會有多筆 → `MultipleResultsFound`。
  改用 `limit(1)` + `first()`。
- **UCS 以外的 DNS 伺服器連線測試** — BIND 9（dnspython `OSError`／連線被拒）、Windows DNS
  （WinRM／`requests` 例外）、PowerDNS 與 OPNsense Unbound（認證失敗回非 JSON）會漏出原始例外，
  而 `/dns/servers/{id}/test` 端點沒接到 → 變成無訊息的 500。各 adapter 現在把這些包成
  `DNSAdapterError`，端點也加了安全網把任何非預期錯誤轉成可讀的 502。

### UI / 文件
- 修正區段詳情頁缺少的 i18n key（「顯示順序」原本顯示原始 key）。
- 通知「全部標為已讀」與群組成員操作補上錯誤提示。
- 用詞：繁中文件 plugin 一律用「外掛」（不用「插件」）。

## [0.4.128] — 2026-06-10

### 修正 / 改善
- **外部反向代理 + OIDC / Microsoft 365(Entra ID) 登入**：OIDC/SAML callback 後前端能正確解析
  網址 fragment 裡帶回的 token（原本會被忽略、卡在登入頁）；後端合併 **ID Token** 的 claims —
  Entra ID 的 `groups` 只在 ID Token、不在 Graph userinfo，補上後管理員群組才比對得到。新增
  `deploy/nginx/jt-ipam-external-proxy.{conf,snippet}` 外部代理模式範本（HTTP-only、不送 HSTS、
  `X-Forwarded-Proto` 透傳）；README 新增「模式 C — 外部反向代理」與 `APP_PUBLIC_URL` 等設定提醒。
- **安裝（Ubuntu 24.04）**：`ensure_node` 不再把 NodeSource 安裝輸出丟 `/dev/null`，且安裝後**驗證
  Node ≥ 18**，否則直接停止並印出手動補裝指令——修「Node 安裝靜默失敗、前端沒 build、卻看似安裝成功」。
- **AI 對話**：Ollama 未啟用 / 連不上 / 設定錯時，改顯示**友善且可行動**的錯誤訊息（指向 管理 → LLM / AI），
  不再是 `Ollama is disabled` / `transport: …` 這類難懂字串。
- **電路**：編輯時「關聯裝置」下拉空白修復（裝置查詢 `page_size` 超過後端上限被擋）；電路表格新增
  「關聯裝置 / 說明」欄（欄位選擇器可選），狀態欄改顯示中文（active → 使用中）。
- **掃描代理 / 裝置詳情**：表格欄寬收緊——操作欄不再被擠出右側、空欄不再吃滿寬度、MAC 與時間不再折行。
- **NAT 規則**：從頂層選單移到「進階」群組；點某一列改為**唯讀檢視**（欄位禁用），編輯改走操作欄鉛筆鈕。
- **更新提示橫幅**：改為有框 + 陰影、整塊可點的方塊、SVG icon（非 emoji），文案改「系統有更新，請按此重新整理載入最新版本」。
- **全站表格每頁筆數**改為記住使用者偏好（`user_preferences.page_size`，跨裝置同步）。

## [0.4.114] — 2026-06-09

### 新增 / 改善
- **DNS 記錄頁**：可依伺服器 / 型別篩選（型別下拉帶筆數統計）、來源欄顯示**來源 DNS 伺服器**、IP 對應
  改用**實際 IP 值**查 `ip_addresses`（修「子網路裡明明有、卻顯示查無對應」）、欄位選擇器；DNS 同步只
  保留 **A / AAAA / PTR**（IP↔名稱對應用途），不再存 CNAME/MX/TXT 等。
- **IP 位址**：新增 `in_dhcp_lease`（migration 0074）由 OPNsense DHCP lease 同步**自動標記/撤銷**；
  phpIPAM 匯入來源改標 `phpipam`（原本一律誤標「手動」）；OPNsense DHCP/ARP 同步加上防火牆關聯子網路
  範圍 + `limit(1)`，修重疊網段同 IP 的 `MultipleResultsFound` 整批 crash。
- **全域搜尋**：支援**部分 MAC 前綴**（如 `bc:24`）；DNS 記錄搜尋結果改導向「進階 → DNS 記錄」並把名稱代入搜尋。
- **機櫃**：合併單卡也能匯出 **SVG / PNG / draw.io**（整機房多櫃並排）；draw.io 方塊改**直角**與畫面一致。
- **AI 對話**：零相依 Markdown 渲染器支援 **GFM 表格**（修表格亂掉）。
- **MCP**：新增 `list_dns_records` 工具；AI 回答「某子網路還有幾個可用 IP」改呼叫實際資料而非純 CIDR 算術。
- **IP 申請審核通知信**：加上**可點連結**（未登入先導登入、登入後自動返回審核頁）。
- IP 異動記錄的 `switch_port` 顯示改「**裝置@埠號**」。

## [0.4.113] — 2026-06-09

### 新增 — IP 申請審核關卡 + 通知
- **可設定的審核政策**（管理 → IP 申請審核設定），四種模式供各站台選用：僅管理員；管理員 +
  指定使用者/群組（單關卡，任一核准）；**多組會簽**（多關卡、不分先後、全部都要核准）；
  **依序多關卡**（有序關卡，每關各自指定審核人，須逐關通過）。另有「是否允許自核」職責分離
  開關。逐關核准記錄存於新表 `ip_request_stage_approvals`（migration 0073）。核准/駁回端點
  改依政策授權（不再只看是否為 admin）。
- 申請詳情頁顯示**關卡進度**（哪些關卡已過、目前待哪一關）；依序模式下，輪到某關才通知該關審核人。
- IP 申請清單對審核人的待審項目直接提供 **核准 / 駁回** 按鈕（不必點進詳情）。
- 申請詳情頁全面中文化；顯示子網路 CIDR（可點）與「**將配發的 IP**」——含系統自動挑的第一個
  空位，且**審核人可在核准前改成別的 IP**。
- **審核人通知**：申請送出時，每位審核人會收到站內鈴鐺通知；若已啟用 Email 管道也會收到信。
- **通知發送設定**（管理 → 通知發送設定）：Email/SMTP 管道（主機/埠/TLS/帳密/寄件者，密碼
  加密儲存，可寄測試信）。Telegram / Slack / Teams / Nextcloud / Zulip 顯示「開發中」。

### 新增 — DHCP
- 子網路詳情在有 OPNsense DHCP 發放範圍時多顯示 **DHCP 發放範圍** 欄（無資料則不顯示），
  IP 清單並新增 **只看 DHCP** 篩選。

### 新增 — DNS 記錄（進階 → DNS 記錄）
- 新頁面列出從整合 DNS Server 取回的記錄，可搜尋、**用 IP 反查**對應記錄（正解 A/AAAA 或該 IP
  的 PTR），以及**只看「沒有對應 IPAM IP」**的 A/AAAA 記錄。

## [0.4.112] — 2026-06-09

### 修正
- **人工編輯的 MAC 未受同步覆寫保護。** 不像 hostname（會記一筆 `manual` 觀測），在 UI
  改 IP 的 MAC 只設了 `ip.mac`、沒標 `mac_source="manual"`，導致下次掃描 / ARP 同步可能
  把它蓋掉。IP 編輯端點現在會在人工改 MAC 時標記 `mac_source="manual"`（ARP 優先序最高），
  清空 MAC 時一併清掉來源。
  （hostname 的人工 vs 優先序流程已端到端驗證正確；若人工填的主機名稱看似消失，多半是
  瀏覽器跑到舊的 SPA bundle，請強制重新整理，而非後端問題。）
- IP 申請工具列：狀態篩選下拉是 `small`、旁邊按鈕是預設大小，導致下拉較矮 → 調成同高。

## [0.4.111] — 2026-06-08

### 安全（MCP 逐物件 RBAC 範圍）
- 多個 MCP/AI 清單工具會回到使用者可見範圍以外的資料。`list_racks` /
  `list_locations` / `list_sections` / `list_customers` 現在依逐物件可見性過濾列；
  `recent_ip_changes` 限定可見子網路；`get_customer_summary` 對不可見客戶回查無；
  `stats_overview` 的逐物件計數依範圍縮放，且對無全域讀取者不洩漏全域基礎設施計數。
  `dns_lookup` 改歸類為全域基礎設施工具。
- 新增回歸測試（`test_mcp_rbac_scope.py`）：零權限全擋、部門帳號擋全域工具、清單列
  範圍過濾、stats 計數縮放。

## [0.4.110] — 2026-06-08

### 修正（create-admin CLI）
- 當指定的帳號名稱命中某帳號、而 email 命中另一個帳號（或同一 email 被多個帳號共用）時，
  `create-admin` 會以 `MultipleResultsFound` 崩潰。改成分開查 username 與 email，遇到衝突
  時回清楚的錯誤而非崩潰。
- `--force-update` 現在會把指定的 username/email 一併寫回該帳號 —— 先前只重設密碼，導致
  帶了新的 `--email` 卻被忽略、畫面仍顯示舊位址。

## [0.4.109] — 2026-06-08

### 新增（MCP / AI 工具）
- **10 個新 MCP 工具**，補上先前 AI 查不到的實體：`list_circuits`、`list_providers`、
  `list_asns`、`list_tenants`、`list_contacts`、`list_ssids`、`list_cables`、
  `cable_trace`、`list_power`、`list_wazuh_agents`。

### 變更（MCP 欄位跟上近期功能成長）
- `list_subnet_ips` 補回 `effective_status`（上線/離線）與 `os_family`。
- `list_nat` 解析出真正的來源/目的 IP，並加入介面、別名、disabled/no_rdr、ip_version
  （原本只有名稱/協定/埠）。
- `get_subnet_detail` 補 scan_method、掃描代理、VRF、上層子網路、是否歸檔。
- `get_device` 補單位、fqdn、地點、說明、電源埠；`list_devices` 補單位 + fqdn。
- `list_vms` 補租戶/主要 IP/裝置 + 網路介面。
- `get_ip_detail` 補 `effective_probes`；`list_customers` 補 title/address；
  `stats_overview` 加計 VM / 電路 / 供應商 / ASN / 租戶 / 聯絡人 / 佈線。

### 安全（MCP RBAC 收斂）
- MCP HTTP/stdio 的 `tools/call` 先前**完全沒有**可見性閘。現在 MCP 協定與 NL chat
  共用同一個 `authorize_tool`：零權限帳號擋掉所有資料工具；全域基礎設施工具
  （VLAN/VRF/NAT/防火牆/DNS/VM/VPN/電路/佈線/電力/Wazuh…）需 admin 或萬用讀取；
  異動工具需 admin。`tools/list` 與 LLM 工具清單都依可呼叫範圍過濾。

## [0.4.108] — 2026-06-07

### 修正
- **掃描代理回報主機存活後，「實際狀態」仍卡在「離線」。** 代理 `/report` 端點只更新
  `last_seen_scanner`，卻沒有重算 `effective_status`，導致實際狀態停留在上次 LibreNMS
  重算結果（可能已過時好幾天）。現在只要代理當次掃到回應，便立即把該 IP 翻成
  `online (scanner)` / `online`，並記錄離線→上線的異動。

### 新增
- **安裝程式自動建立第一個 `admin` 帳號並產生隨機密碼**，於結束時印出一次（亦存到
  `/etc/jt-ipam/.admin-initial-password`，僅 root 可讀）。README 補上
  `create-admin --force-update` 重置密碼的 CLI 說明。
- **掃描代理安裝程式會一併安裝選用探測工具**（`nmap`、`samba-common-bin`、
  `avahi-utils`），讓 OS / NetBIOS / mDNS 探測開箱即用；可用
  `JT_IPAM_SKIP_PROBE_TOOLS=1` 略過。
- **不可用探測旁新增「安裝說明」彈出**（掃描代理頁與子網路編輯對話框），顯示解鎖該探測
  所需的套件 / 指令。

## [0.4.107] — 2026-06-07

### 新增
- **Wazuh / Proxmox VE / AdGuard / DNS 整合的限定子網路範圍**（migration 0072），比照
  LibreNMS：各整合可限定只在指定子網路內比對 IP，避免不相關系統的重疊網段把 hostname /
  OS 等資訊誤掛到別的 IP。留空＝全域比對。

### 變更
- 子網路編輯：所選掃描代理不支援的探測項目會反灰（與掃描代理頁一致）。
- NAT 表格：點規則列可開啟該筆細節（會略過 IP / 裝置連結）。
- 子網路列表：樹狀展開箭頭改放在 CIDR 欄，不再佔用釘選欄。

### 修正
- switch_port 提示改顯示 `裝置@連接埠` 格式（非 `裝置 / 連接埠`）。

## [0.4.106] — 2026-06-07

### 新增
- **OPNsense 防火牆關聯範圍**（migration 0071）：每台防火牆可依機房 / 單位 / 明確子網路 /
  介面→子網路對應設定範圍。同步進來的 NAT 規則只會在範圍內子網路配對 IP，多台防火牆共用
  同一 RFC1918 網段時不再誤掛到錯的 jt-ipam IP；未設定範圍者沿用原本全域 IP 字串比對。
- NAT 頁：滑過已關聯到 jt-ipam 的 IP 會即時彈出該 IP 細節（主機名稱 / 狀態 / MAC / 製造商 /
  子網路 / 單位 / 裝置 / 交換器位置…），懶載入；點擊進入該 IP 詳情頁。

### 變更
- 新增的子網段繼承父網段的單位；`rebuild_subnet_hierarchy` 會自動修補（多層串接）。
- 左側子網路樹：下層子網段改為真正可展開的巢狀節點＋連接線（取代「↳」前綴）；父節點標題仍可點進詳情。
- 左側選單版本號字體放大。

### 修正
- 淺色主題下含連結的 tooltip（如表格 ellipsis tooltip）綠字在深底看不清；連結改用 tooltip 淺色字。
- 防火牆關聯範圍表單的「單位 / 客戶」下拉顯示「無資料」（選項未載入）。

## [0.4.105] — 2026-06-07

### 修正
- 子網路存檔出現「Invalid request」：編輯表單會送 `master_subnet_id`，但 `SubnetUpdate`
  （嚴格 schema、禁止額外欄位）沒宣告它。已補上欄位，編輯子網路恢復正常。
- 子網路列表：點「展開下層」的箭頭會誤進入該子網路而非展開；已讓 row 點擊忽略展開鈕。

### 變更
- **統一子網路編輯**：子網路列表與子網路詳情頁改共用同一個 `SubnetEditModal` 元件，
  兩處編輯欄位完全一致（區段 / VLAN / VRF / 上層子網路 / 逐項掃描 / 掃描代理…），不再分歧。
- 左側選單：子網段依 `master_subnet_id` 巢狀排在父網段之後並以「↳」縮排（仍可點擊進入）。
- 頂列 RWD 美化：語言 / 佈景 / 帳號改 pill 按鈕含 hover、鈴鐺兩側分隔線、與搜尋框垂直置中；
  移除下拉小箭頭以省寬度。
- Graylog 教學：建議 Title / Description / Name（`jt_ipam_adapter` / `jt_ipam_cache` /
  `jt_ipam_table`）；HTTPS 與明文 HTTP(8088) 兩種網址；Line Separator `\n`、Ignore
  characters `#`、Refresh interval 300s、Expire after access 300s、Default single/multi
  value 留空；IP 欄位名輸入框即時改寫 pipeline rule 並做 Graylog 欄位名驗證；rule 名為
  `jt-ipam enrich <欄位> -> <欄位>_hostname`；pipeline `lookup_value()` 用對照表名稱；
  範例改用 `src_ip_hostname`。

## [0.4.104] — 2026-06-07

### 修正
- Graylog DSV 對照表端點改為每個 IP 只輸出一次。同一個 IP 可能存在於多個（重疊）
  子網路，原本會產生重複列，導致 Graylog「DSV File from HTTP」資料配接器報
  「Multiple entries with same key」。現已依 IP 去重（依排序取第一筆）。

## [0.4.103] — 2026-06-07

### 變更
- 頂列改為 RWD：螢幕較窄時，語言 / 佈景 / 帳號改為只顯示 icon（icon 觸發下拉）、
  搜尋框自動縮小，不再換行擠成多列。
- 新增子網路時，未指定單位則**自動繼承父網段的單位**，子網段會歸到側邊選單同一個
  單位群組下；新增 / 編輯 / 刪除後左側子網路樹立即刷新。
- IP 編輯視窗：OS 探測顯示與子網路 / 掃描代理設定相同的「侵入性」標籤 + 提示。

## [0.4.102] — 2026-06-07

### 變更
- 異常偵測（MAC 漂移）：「出現位置」的交換器改顯示友善名（LibreNMS sysname / hostname），
  不再顯示原始裝置 UUID；裝置 / 埠 / 最後出現三欄改為對齊網格呈現。

## [0.4.101] — 2026-06-06

### 變更
- 儀表板**區段熱度**卡片重新設計：熱度條改用「平均子網路使用率」（不再被單一大型稀疏網段
  稀釋成趨近 0%），並列出該區段子網路依使用率分布（滿載 / 偏高 / 中等 / 偏低）與
  子網路 / 已用摘要，資訊更充實。

### 修正
- 機櫃：最左機櫃的框線左邊被水平捲動容器裁掉；於兩側補小邊距使其完整顯示。
- 「OS 來源優先順序」區塊標題用詞。

## [0.4.100] — 2026-06-06

### 新增
- **OS 來源優先順序**（掃描代理 / LibreNMS / Wazuh）：在「名稱 / ARP 來源順序」頁可拖拉
  排序，決定多個來源都回報 OS 時以誰為準；IP 詳情的 OS 欄位會顯示解析出的來源。
- 機櫃：原「合併單卡」開關改為清楚的兩段式切換（獨立卡片 / 合併卡片）；合併卡片新增
  共用的正面 / 背面切換與匯出（合併裝置清單）。

### 變更
- 稽核轉送設定由「Graylog」改為通用的「Log 伺服器」（GELF / syslog 適用任何收集器）。
- IP 清單 OS 欄改為一行顯示（icon 永遠不縮、空間不足時 label 裁切）；主機名稱欄縮窄讓位。
- 掃描代理表格欄寬調整，使「最後回報」不再折行。
- switch_port 提示一律顯示完整 `裝置@連接埠` 全文（低信心時再附說明）。

## [0.4.99] — 2026-06-06

### 變更
- MCP 工具帶出新的掃描 / OS 欄位：`get_ip_detail` 回傳 OS 推測 / 家族 / 來源與排除的探測；
  `list_scan_agents` 回傳已啟用 / 可用探測與最後來源 IP。

## [0.4.98] — 2026-06-06

### 變更
- OS 偵測結果在 IP 詳情主要欄位表中以**「作業系統」**欄位呈現（上方），子網路 IP
  清單也新增可切換的 **OS** 欄。

## [0.4.97] — 2026-06-06

### 新增
- 掃描代理：新增**「立刻執行一次」**動作，代理下次 poll 時立即跑所有已啟用的探測
  （migration 0070）；OS 偵測（`nmap -O`）在裝有 nmap 的代理主機上端到端驗證可用
  （以 root 執行）。
- 探測間隔輸入框顯示單位（秒）與換算提示。

## [0.4.96] — 2026-06-06

### 變更
- 掃描代理的探測間隔輸入框加上單位後綴（秒）與換算提示（如 86400 → 「1 天」）。

## [0.4.95] — 2026-06-06

### 變更
- 掃描探測：移除連接埠探測選項（TCP 連接埠存活、連接埠 / 服務掃描）與 SNMP —
  jt-ipam 不開放需要憑證或連接埠掃描類的探測。保留：ICMP / ARP / 反解 DNS /
  NetBIOS / mDNS / OS 偵測。
- 掃描代理：當代理回報的版本落後 server 內建的代理程式時，顯示**「可更新」**標籤
  （代理是從 jt-ipam server 自我更新，非 GitHub；此標籤用來凸顯自我更新失敗的代理）。
- 拓樸圖：子網路中心點分開時，VPN 對連的防火牆會保持靠近，不再被推到相對最遠的兩端。

> 英文版見 [CHANGELOG.md](CHANGELOG.md)。

## [0.4.94] — 2026-06-06

### 新增
- **可組態的掃描探測**，三層模型（migration 0069）：
  - **探測項目目錄**（icmp / tcp / arp / rdns / netbios / mdns / os / ports），
    每項分輕量 / 重量、各自預設間隔與侵入性；預設**只開 ICMP**。重項目（OS / 連接埠
    掃描）走**自己的長間隔**，絕不跟 ICMP 同頻。
  - **掃描代理**：勾選可執行的探測 + 各重項目間隔；代理會自我回報實際裝得起哪些
    （其餘反灰）。
  - **子網路**：選擇要跑的探測（`scan_method`）。
  - **IP 位址**：逐項略過探測（原「不掃 ping」一般化，icmp 保持同步）。IP 詳情頁
    顯示**實際生效**的探測集（子網路要跑 − IP 略過 ∩ 代理能力）。
- **OS 偵測顯示**：掃描結果正規化成 OS 家族（Windows / Linux / macOS / BSD /
  網路裝置 / 印表機 / 儲存 / 虛擬化…），並以每家族 SVG icon 呈現（IP 清單欄 +
  IP 詳情），tooltip 顯示原始字串。
- 代理 poll/report 協定帶上每子網路探測、各探測間隔、逐 IP 略過覆寫，以及更豐富的
  結果（rdns / os_guess / open_ports / probes_run）；內建代理新增 tcp/arp/rdns 探測
  與快 / 慢迴圈排程。

## [0.4.92] — 2026-06-06

### 新增
- 進階（租戶 / 電路 / 聯絡人）與電力頁：同頁多張表格改拆成**內層頁籤**（比照防火牆
  規則 / 別名的樣式）。進階 / 電力 / 虛擬化每張表格都改成**統一工具列**：
  篩選 + 重新整理 + 新增 + 欄位選擇 + 匯出。
- 機櫃：新增**合併單卡**檢視模式（同機房所有機櫃排在同一張卡）。
- IP 拓樸圖：**選取連線後維持高亮**，並以卡片顯示兩端（IP / 裝置 / 連接埠，單邊
  已知也顯示）；多個子網路中心點分散，雙網段裝置不再擠在一起。
- 電路：**固定 IP 欄位**（IP / 閘道 / 遮罩 / DNS）+ 關聯裝置（migration 0067）；
  內建**電路類型**並可新增 / 刪除管理。
- 掃描代理：顯示**最近來源 IP**（migration 0068）。
- 裝置詳情：IP 清單新增一鍵「**新增 IP 對應**」按鈕。
- Graylog DSV 設定獨立成「**整合 Graylog**」頁面（放在整合 Wazuh 下），附串接教學。

### 變更
- nginx API 限流上調（100 → 1200 r/m、burst 20 → 80），解決 API 密集頁面偶發的
  「連線失敗」。
- IP 位址編輯：儲存鈕改綠色；存檔 / 取消後回到該 IP 詳情頁。

### 修正
- **舊 bundle 換頁卡住**：換頁載入分割 chunk 失敗時，路由會自動重載一次；建置改為
  **跨版本保留 hash 資產**（清理 7 天前的舊檔），讓部署前開著的分頁換頁時不再
  抓不到 chunk（404）而卡住。
- 聯絡群組表格誤用租戶群組欄位（打到錯的 API）— 已用專屬欄位修正。
- ruff：修正 `audit.py` 的 noqa 規則碼、`sso.py` 的 import 排序。

### 安全 / 雜項
- **vitest** 開發相依升級 3.2.6 → 4.1.8（修補「Vitest UI server 任意檔讀取 / 執行」
  critical 通報；僅開發用，不在正式 bundle 內）。
- 新增雙語文件：`CHANGELOG_zh-TW.md`、`SECURITY_zh-TW.md`，以及英文
  `TEST_CHECKLIST.md`（搭配 `TEST_CHECKLIST_zh-TW.md`）。
- `scripts/*.sh` 全面英文化（註解與訊息）；行為不變。

## [0.4.79] — 2026-06-06

### 新增
- **SSO 網頁設定**：OIDC 與 SAML 改為 DB 化並提供管理員網頁設定（env 預設 + DB
  覆寫，secret 以 AES-GCM 加密）；新增 LDAP 管理頁。
- **裝置電源埠 ↔ PDU 插座**建模（NetBox PowerPort 風，migration 0066）。
- **版本自動重載**：輪詢 `dist/version.json`，部署新版時提示重新整理（這是「存檔
  沒生效」舊 bundle 問題的根因）。
- **完整雙語文件**：README 與所有 `docs/*.md` 提供中英版本；GitHub Pages 功能地圖
  樹狀頁。

### 變更
- 全站通用表格**欄位選擇 + 多格式匯出**（含零相依 `.ods` / `.odt`、`.xlsx`、PDF）。
- 通用釘選改存後端偏好（migration 0065）；機櫃正面 / 背面安裝面支援。

## [0.4.61] — 2026-06-05

### 新增
- **全域基礎設施資料的 RBAC 收斂**：`require_global_read` / `has_global_read` /
  `can_edit`。列表、詳情、搜尋、儀表板彙總、計數與趨勢全部依使用者可見範圍縮放；
  操作按鈕依能力反灰。
- **纜線追蹤**（NetBox 風多跳，migration 0063）：新增 `device_ports` 表，支援
  bridge → NIC → 外部裝置穿透。
- 機櫃**半 U** 支援與正 / 背面視覺化；裝置詳情的機櫃圖標示本裝置位置。

### 變更
- AI / MCP：100 題實測修一輪；工具清單依權限過濾；任何異動前加**確認 gate**；
  大量結果支援 cursor 分頁與「下一批」續抓。
- 歸檔的子網路連帶隱藏其 IP（清單 + 搜尋）。

### 修正
- 修補 AI 對話 / 拓樸圖的 **RBAC 繞過**（零權限帳號原本可問到 IP / 裝置、看得到
  拓樸）。

## [0.4.43] — 2026-06-04

### 新增
- **裝置間纜線 / 連接埠**連線管理；佈線與電力資源支援完整 CRUD 編輯。
- LDAP 管理頁；AI 異動確認 gate；Graylog DSV 查表（埠 8088 明文 HTTP）。
- 儀表板圖表；裝置詳情顯示所在機櫃圖。

### 變更
- Proxmox 連線設定移到管理區；撈取節點網路介面（bridge / bond / NIC）並可追蹤。

### 修正
- 主機名稱同步反覆抖動；左半 U 放入裝置存檔 500；稽核 `object_id` 必須是 UUID
  （`append_audit` 要帶 `request_id`）。

## [0.4.32] — 2026-06-02

### 新增
- 裝置安裝方向（migration 0057）：裝置可標記安裝於**機櫃正面或背面**；移除機櫃
  呈現面欄位。
- 版本資訊管理頁：顯示目前版本 + Python 與主要後端套件版本，並可一鍵檢查 GitHub
  最新版。
- 地點清單顯示**機櫃數 / 裝置數**欄；子網路清單顯示**釘選**欄並可一鍵切換。
- IP 位址編輯器提供一鍵**關聯相符裝置**（與裝置→IP 關聯鈕互為鏡像）。
- 機櫃表單常見寬 / 深**快選膠囊**；進機櫃頁自動選取已釘選的地點。
- 遠端分頁清單（IP 位址 / 稽核 / 使用者 / 裝置）的匯出可抓**完整資料集**（非僅
  當前頁）。
- GitHub Actions CI 實際執行並把關：前端（eslint flat config / vue-tsc / vitest /
  build）與後端（alembic / pytest / bandit / gitleaks）。
- 裝置 ↔ IP 關聯：裝置清單推導有效管理 IP（primary_ip → LibreNMS 管理 IP →
  名稱即 IP），有相符 IP 物件時 render 成可點連結，未掛上時提供一鍵「關聯」鈕；
  IP 編輯器可挑裝置，`/devices/{id}/relations` 提供關係鏈。
- 掃描代理自動探索：代理對其負責且開啟掃描的子網路內未知 IP 推送時，自動建立 IP
  物件（自帶說明，非抄 phpIPAM）；重疊網段僅在代理自己的子網路內以最長首碼比對。
- 各來源優先序拆成獨立卡片：主機名稱、裝置名稱、ARP/MAC，以及新的**裝置型號**
  優先序（各卡中手動最高且不可關閉）。
- IP 搜尋新增**完全相符**切換（IP / 主機名稱須等於關鍵字，`192.168.1.1` 不再也
  比對到 `192.168.1.1xx`）。
- 子網路可透過 `allow_overlap` 明確**允許重疊**（如不同租戶 / 地點下的同一 CIDR）。
- OPNsense 別名同步：別名拉進 `opnsense_synced_aliases`。
- 儀表板：釘選地點 / 釘選機櫃卡片；機櫃頁可用迷你機櫃挑選器把裝置放入空 U 位。
- GitHub Pages 站：專案 logo + favicon、內嵌 SVG 圖示（無 emoji）、修正定位
  （非「建構於 phpIPAM 之上」）。

### 變更
- 來自 LibreNMS 的裝置命名改優先 **sysName** 而非 hostname（常只是 IP）；裝置名稱
  優先序預設順序對應調整，型號由 LibreNMS 硬體資訊回填。
- DNS 同步對每個 IP 套用一個決定性主機名稱（排序後），修正 IP 有多筆 A 記錄時的
  名稱抖動。
- 主機名稱優先序納入 Wazuh 與 AdGuard 來源（原本有觀測到卻沒列在優先序 UI）。
- 平面圖機櫃可旋轉**任意角度**（軟貼齊正交），不限 0/90/180/270；佔地依實際寬 / 深
  縮放。
- VPN 隧道配對依方法標示（migration 0058）：WireGuard 公鑰（可靠）vs IPsec 端點
  比對（盡力）；IPsec 比對也對應各防火牆自身隧道本地端點以提高命中率。
- 全站文案從「新世代」改為「可自架、以整合為核心」（Pages / README / SPEC / app）；
  Pages 強調開源整合 + phpIPAM 匯入、加上強調色、拆分安裝 / 升級 / 解除安裝。

### 安全
- OPNsense config.xml 改用 **defusedxml** 解析（XXE）；子網路重疊 / master SQL 全
  參數化；bandit 在 medium+ 嚴重度下乾淨。

### 修正
- 子網路釘選重整後不一致 — 改為切換時同步持久化，而非靠元件層 watcher。
- GeoIP 資料庫下載改回 legacy `geoip_download` 端點（新 permalink 會 302 到 S3 並
  拒絕轉發的授權標頭）。
- 平面圖上傳 500（uploads 目錄擁有者）；稽核記錄文件表格列因未跳脫 SQL `||` 損壞。

### 測試
- 新增回歸覆蓋：型號優先序、子網路重疊、完全相符 IP 搜尋、掃描自動探索、主機名稱
  來源清除、主機名稱順序完整性、裝置 IP 比對旗標、裝置 / IP 關係鏈、OPNsense 別名
  解析 + 同步、LibreNMS 裝置連結、DNS 拉取命名、Wazuh/Proxmox 同步、IPsec 配對、
  版本端點、機櫃面 / 地點計數，以及前端 usePinned 單元測試。

## [0.4.31] — 2026-06-01

### 新增
- NAT / 電路欄位擴充（migration 0053）：NAT 補齊完整 OPNsense 規則集（停用 /
  no-RDR / IP 版本 / 來源·目的反向 / 埠範圍 / 記錄 / 分類 / NAT reflection / pool /
  filter-rule / 別名參照）；電路新增上 / 下行頻寬。OPNsense 同步會填入。
- 裝置詳情：Wazuh 代理 + Proxmox VM 面板（依 IP 比對）；編輯鈕。
- 工具：DNS / 郵件診斷（MX/SPF/DKIM/DMARC）+ 機房電力試算。
- 機櫃圖 → 可用 draw.io 編輯的 SVG 匯出；機房釘選；清單頁快速篩選；使用者選單內
  自己的 AI 對話歷程；權限總覽。
- 拓樸圖：縮放 / 適配按鈕、可點圖例切換、預設顯示釘選子網路。

### 變更
- 機房 = 地點（選單改標「機房 / 地點」）；站對站 VPN；NAT 別名參照可點到防火牆頁。
- VPN WireGuard 配對交叉填入兩端真實 WAN IP（原本顯示 LAN）。
- 平面圖：固定大小控制點、0/90/180/270 旋轉貼齊、工具列移到畫布下方。
- 全站卡片標題色帶 + 深色模式表格 / 卡片層次。

### 修正
- 拓樸圖子網路篩選漏掉以名稱 / ARP 推導的裝置。
- 多處 i18n / 用詞 / 按鈕高度修正（協定、配電盤 / 饋線 / 插座、通知…）。

## [0.4.30] — 2026-06-01

### 新增
- 管理表格的匯出（CSV / Markdown / PDF / ODS / ODT）：使用者、稽核、DNS、LibreNMS、
  Wazuh（執行個體 / 代理 / 失聯）、防火牆（防火牆 / 對應 / 規則）與掃描代理。

### 變更
- 全域**地圖供應商**選擇從地點頁移到 **設定 → 系統**（僅管理員）。新增非管理員的
  `GET /system/map-provider` 讓地點地圖預覽對所有人可 render，`PUT` 仍限管理員。

### 修正
- 資料表標頭不再換行：全域規則讓短的 CJK 標題（如 子網路）不受排序箭頭間距影響
  維持單行。

## [0.4.x] — 2026-05/06

### 新增
- **物件層級 RBAC**，涵蓋 7 種物件（客戶 / 區段 / 子網路 / IP / 裝置 / 機櫃 /
  地點），含階層繼承、各類型「全部」萬用，與 5 個內建角色（系統管理員、唯讀檢視者、
  網路操作員、稽核員、部門管理員）。可見性套用於列表端點、全域搜尋、拓樸圖與所有
  選擇器。
- **權限管理 UI** — 主體（使用者 / 群組）挑選器、授權表，與「全部」/ 特定多選 +
  讀 / 寫 / 管理層級的授權流程。
- **MCP 伺服器** — 擴充工具集，支援 stdio 與 Streamable HTTP；掛在 `/api/mcp`，可
  透過 nginx 反代連到。寫入工具自我限管理員。
- **客戶**（管理單位）掛到區段 / 子網路 / 裝置 / IP，以及 IEEE **OUI 廠商**表與
  月排程更新。
- **AI 對話**改進 — 歷程持久化、每則時間戳、模型與耗時顯示、模型參數 tooltip
  （家族 / 參數量 / 量化 / 上下文長度，經 Ollama `/api/show`）。
- **全域搜尋**擴及 VPN、客戶、機櫃、地點、NAT、DNS 記錄、防火牆與 IP 申請 — 全部
  經 RBAC 過濾。
- 寬表格的浮動黏附水平捲軸；精緻淺 / 深主題；佈線 / 電力 / VPN 拆成三個獨立頁面。

### 變更
- 正式資料庫由 `SQL_ASCII` 轉 `UTF8`。
- 台灣用語修正（如 首碼 取代 前綴）。

### 修正
- 多項 QA 驅動的 UI 修正（欄寬、儀表板小工具樣式、淺色模式文字選取對比、拓樸節點
  詳情 popover、tooltip 裁切）。

## [0.3] — Phase 1–3 baseline

- phpIPAM 對等（區段 / 子網路 / IP / VLAN / VRF / NAT / 裝置 / 機櫃 / 地點 /
  IP 申請）、TOTP + API token、強制 TLS。
- 多廠牌 DNS、深度 LibreNMS 整合、異常偵測、SHA-256 稽核鏈、pgvector 語意搜尋。
- 租戶 / 佈線 / 電力 / VPN / 虛擬化、Proxmox 同步、Cytoscape 拓樸、OIDC/SAML SSO、
  OPNsense 防火牆同步、Wazuh 代理盤點。
