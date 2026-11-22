# OpenStack 測試靶（OpenStack 整合用）

可丟棄的測試環境，給 jt-ipam 的 OpenStack 整合（Keystone v3、Nova、Neutron，唯讀）做端到端驗證。
在 開發機 上用 LXD 開兩個容器，裡面跑 DevStack（`stable/2026.1`）：

| 容器 | 位址 | 內容 |
| --- | --- | --- |
| `jtipam-os-ctl` | `10.241.73.10` | Keystone、Placement、Glance、Nova（API、conductor、scheduler、compute）、Neutron（ML2/OVN）、MySQL、RabbitMQ |
| `jtipam-os-cmp` | `10.241.73.11` | 第二台運算主機：nova-compute、ovn-controller（另開 `q-ovn-agent`，只是為了讓 DevStack 裝 OVS／OVN 套件） |

Nova 用 fake 虛擬化驅動（`VIRT_DRIVER=fake`），不需要 `/dev/kvm`，虛擬機「開機」是瞬間完成的，不會真的跑客體系統。
所以 Nova 的主機對應（`OS-EXT-SRV-ATTR:hypervisor_hostname`）、Neutron 的埠繫結（`binding:host_id`）、
浮動 IP、重疊網段、雙堆疊都是真的 OpenStack API 回出來的，但虛擬機之間沒有實際的封包。

Cinder、Horizon、Swift、Tempest、metadata 代理、VNC 主控台都沒開。

## 安全

- 只接在 LXD 自己的私網橋接 `jtosbr0`（`10.241.73.0/24`，NAT 出網）上；開發機 的區網介面沒有發布任何埠。
  開發機 上的程式（例如本機的 jt-ipam 後端）直接連 `10.241.73.10`。
- 開發機 有 docker，docker 會把 FORWARD 預設改成 DROP，容器會上不了網。`create.sh`／`start.sh` 會在 `DOCKER-USER`
  加兩條規則：放行「從 `jtosbr0` 出去」與「回到 `jtosbr0` 的已建立連線」。區網上的機器就算自己加了路由，也只能收到回程，
  連不進容器。`destroy.sh` 會把這兩條刪掉。
- 兩個容器都是**特權容器**、不套 AppArmor（OVS 的核心 datapath 與 ovn-controller 在非特權容器裡跑不起來），
  等於在 開發機 上有 root 權限，只能當測試用，不可以放任何真實資料。
- 密碼不寫在 repo 裡：第一次 `create.sh` 隨機產生，存在控制節點的 `/root/jt-target.secrets`，`seed.sh --info` 會印出來。
- 種子資料全部是假的：位址只用 RFC 5737（`203.0.113.0/24`）、RFC 3849（`2001:db8::/32`）與私網，名稱一看就知道是測試用。

## 用法

```bash
sudo scripts/openstack-test-target/create.sh      # 建儲存集區／網路／設定檔／兩個容器，各跑一次 stack.sh（實測控制節點約 55 分鐘、運算節點約 15 分鐘）
sudo scripts/openstack-test-target/seed.sh        # 灌測試資料（約 5 分鐘，可重複執行）
sudo scripts/openstack-test-target/seed.sh --info # 再印一次連線資訊與密碼
sudo scripts/openstack-test-target/start.sh       # 開發機 重開機後啟動（載入 OVS 模組、補轉送規則、起容器、等 API）
sudo scripts/openstack-test-target/start.sh stop  # 停止兩個容器（資料保留）
sudo scripts/openstack-test-target/seed.sh --reset  # 重設：刪掉種子資料再重建（所有 UUID 會換新）
sudo scripts/openstack-test-target/destroy.sh     # 全部刪除（容器、設定檔、網路、儲存集區、轉送規則）
```

`create.sh` 可以重複執行：已存在的東西會沿用，已經跑完 `stack.sh` 的節點會跳過（看容器內的 `/opt/stack/.jt-stacked`）。
`stack.sh` 失敗時，主機上的完整輸出在 `/var/tmp/jt-os-target/stack-<容器>.out`，容器內是 `/opt/stack/logs/stack.sh.log`。
要整個重來：`destroy.sh -y && create.sh`。

名稱、位址、記憶體上限都在 `common.sh`，可以用環境變數覆寫（例如 `OS_TT_BRANCH=stable/2026.2`）。

## 連線資訊

| 項目 | 值 |
| --- | --- |
| Keystone v3 | `http://10.241.73.10/identity/v3`（http，沒有 TLS；DevStack 走 Apache 的路徑，不是 `:5000`） |
| 區域／端點介面 | `RegionOne`／`public` |
| Nova | `http://10.241.73.10/compute/v2.1` |
| Neutron | `http://10.241.73.10/networking` |
| 網域 | 使用者與專案都在 `Default` |

帳號（密碼用 `seed.sh --info` 看）：

| 帳號 | 角色 | 用途 |
| --- | --- | --- |
| `admin` | admin（專案 admin，種子腳本也給了 proj-a／b／c 的 admin） | 對照組：看得到全部 |
| `jtipam-reader` | reader：admin、proj-a、proj-b、proj-c；另有 system scope 的 reader | 唯讀服務帳號 |
| `jtipam-member` | member：只有 proj-a | 測「all_tenants 被拒、退回只看自己專案」 |
| 應用程式憑證 `jtipam-reader-ac` | jtipam-reader 建立，範圍 proj-a，角色 reader | 測 `application_credential` 驗證 |

## 種子資料

- 專案：`admin`、`proj-a`、`proj-b`、`proj-c`。DevStack 自己另外建了 `demo`、`alt_demo`、`invisible_to_admin`、`service`（都是空的）。
- 網路：
  - `ext-net`（admin，外部網路，flat／physnet `public`）`203.0.113.0/24`，配置範圍 `.100`～`.199`。
  - `shared-net`（admin，shared）`10.50.0.0/24`，proj-a 與 proj-b 的虛擬機都接在上面。
  - `net-a`（proj-a）與 `net-b`（proj-b）**都是** `192.168.100.0/24`，刻意重疊。
  - `net-c-v6`（proj-c）雙堆疊：`172.31.10.0/24`＋`2001:db8:10::/64`（SLAAC）。
  - `net-c-bulk`（proj-c）`172.31.20.0/24`，給大量虛擬機用。
- 路由器：`router-a`、`router-b`、`router-c`，閘道都在 `ext-net`。
- 浮動 IP：`203.0.113.110` → a1、`203.0.113.120` → b1、`203.0.113.130` 已配置但沒綁定（proj-c）。
- 埠：`port-a-vip`（`192.168.100.200`，沒接任何裝置）、`port-a1`（a1 的網卡，`192.168.100.10`，
  allowed_address_pairs 允許 `192.168.100.200`）。
- 虛擬機（規格 `jt.tiny`，映像 `jt-fake-image`）：
  - proj-a：`a1`（net-a，`192.168.100.10`）、`a2`（net-a＋shared-net 兩張網卡）、`a3`（關機 SHUTOFF）
  - proj-b：`b1`（net-b，同樣是 `192.168.100.10`）、`b2`（shared-net）
  - proj-c：`c1`（雙堆疊，有 IPv4 與 IPv6）、`bulk-01`～`bulk-35`（測 `limit=10` 分頁）
  - 具名的虛擬機指定放在兩台主機上（a1、a3、b2 在 `jtipam-os-ctl`；a2、b1、c1 在 `jtipam-os-cmp`），bulk 由排程器分配。

各物件的 UUID、IP、所在主機：`lxc exec jtipam-os-ctl -- cat /root/jt-target-seed.json`。

## 跑 jt-ipam 的驗收測試

`backend/tests/test_openstack_live_acceptance.py` 對這個測試靶跑（沒設下面兩個環境變數就整支略過）：全專案盤點與分頁、
重疊網段不自動對應、雙堆疊、浮動 IP、reader 退回只讀自己的專案、應用程式憑證、密碼錯誤、即時遷移加改名仍是同一台 VM。
需要兩個檔案，都是從控制節點拿出來的，含密碼，不可以放進 repo：

```bash
D=/var/tmp/jt-os-target; sudo mkdir -p "$D"; sudo chown "$USER" "$D"; umask 077
# 1) 連線資訊＋密碼（ADMIN_PASSWORD、READER_PASSWORD、APPCRED_ID、APPCRED_SECRET 來自 secrets 檔）
sudo lxc exec jtipam-os-ctl -- cat /root/jt-target.secrets > "$D/openstack-target.env"
cat >> "$D/openstack-target.env" <<'ENV'
OS_AUTH_URL=http://10.241.73.10/identity/v3
NOVA_URL=http://10.241.73.10/compute/v2.1
OS_REGION_NAME=RegionOne
OS_INTERFACE=public
ADMIN_USER=admin
READER_USER=jtipam-reader
READER_PROJECT=proj-a
ENV
# 2) 種子摘要（比對用的標準答案）
sudo lxc exec jtipam-os-ctl -- cat /root/jt-target-seed.json > "$D/openstack-target-seed.json"

cd backend
JTIPAM_TEST_DATABASE_URL=<測試用資料庫> \
JTIPAM_OPENSTACK_TARGET_ENV="$D/openstack-target.env" \
JTIPAM_OPENSTACK_TARGET_SEED="$D/openstack-target-seed.json" \
  .venv/bin/pytest -q -p no:cacheprovider tests/test_openstack_live_acceptance.py
```

- 位址跟著 `common.sh`：改過 `NET_PREFIX` 或 `OS_TT_REGION` 時，上面的網址與區域要一起改。
- `seed.sh --reset` 之後 UUID 全部換新，種子摘要要重新拿。
- 「即時遷移加改名」這一項會真的動測試靶：用 admin 把一台 `bulk-*` 搬到另一台主機並改名，做完再改回原名、搬回原主機。
  中途中斷的話，用下面「常用操作」的指令手動改回，否則下一輪比對主機會對不上。
- 基本的實機測試 `tests/test_openstack_live.py` 也可以對這個測試靶跑：`JTIPAM_OPENSTACK_LIVE_URL=http://10.241.73.10/identity/v3`、
  `JTIPAM_OPENSTACK_LIVE_USER=admin`、`JTIPAM_OPENSTACK_LIVE_PASSWORD=<seed.sh --info 印出的密碼>`、`JTIPAM_OPENSTACK_LIVE_PROJECT=admin`、
  `JTIPAM_OPENSTACK_LIVE_REGION=RegionOne`。CubeCOS 的部分改用 `scripts/cubecos-test-target`，用法寫在 `tests/test_openstack_live.py` 開頭的說明。

## 常用操作

```bash
# 在控制節點以 admin 身分下 openstack 指令
lxc exec jtipam-os-ctl -- sudo -u stack -H bash -c 'export OS_CLOUD=devstack-admin; openstack server list --all-projects --long'

# 模擬「虛擬機換主機」（fake 驅動支援即時遷移）。admin 在 admin 專案用名稱找不到別的專案的虛擬機，要先查 UUID
lxc exec jtipam-os-ctl -- sudo -u stack -H bash -c 'export OS_CLOUD=devstack-admin
  ID=$(openstack server list --all-projects --name "^a2$" -f value -c ID)
  openstack server migrate --live-migration --host jtipam-os-ctl --wait "$ID"'

# 看某個服務的記錄
lxc exec jtipam-os-ctl -- journalctl -u devstack@n-api -n 100
```

## 資源用量

建好並灌完種子資料後（2026-10-22 實測）：

| 項目 | 用量 |
| --- | --- |
| 記憶體 | 控制節點約 2.7 GiB（cgroup 含快取 3.4 GiB，上限 8 GiB）；運算節點約 0.5 GiB（cgroup 含快取 2.7 GiB，上限 3 GiB） |
| 磁碟 | 控制節點 5.0 GB、運算節點 3.6 GB、LXD 映像快取 264 MB（都在 `/var/snap/lxd/common/lxd/`） |
| CPU | 各限 4 核；閒置時很低，`stack.sh` 期間會吃滿 |
| 主機端 | 載入 `openvswitch`、`vport_geneve` 核心模組；`DOCKER-USER` 兩條規則；`jtosbr0` 上的 dnsmasq（只聽 `10.241.73.1`） |

`start.sh stop` 再 `start.sh` 約一分鐘恢復，虛擬機、埠、OVN chassis 都會回來。

## 已知限制

- 為什麼要兩個容器：fake 驅動的 nova-compute 不會在 OVS 上建介面，但 Neutron 的 OVN 驅動繫結埠時要求「同名主機上有活著的
  ovn-controller（chassis）」，否則拒絕繫結、虛擬機變 ERROR。一個容器只能跑一個 ovn-controller，所以第二台運算主機
  只能是第二個容器。DevStack 原本把 fake 主機命名成「主機名稱＋1」，跟 chassis 名稱對不上，`create.sh` 會改 DevStack 的
  `lib/nova` 與 `functions`，讓 fake 主機名稱等於容器名稱（`JT_FAKE_HOST`）。
- 埠的狀態都是 `DOWN`（沒有真的介面接上 OVS），虛擬機狀態是 `ACTIVE`／`SHUTOFF`；這跟真實雲不同，測試時不要拿埠狀態當判斷依據。
- 沒有 TLS。要測 CA 憑證的流程得另外在前面放一個 TLS 反向代理，或改用 DevStack 的 `tls-proxy` 服務重建。
- DevStack 會在容器裡用 apt 裝一些用不到的套件（例如 qemu），只佔容器的磁碟，不影響主機。
- DevStack 預設的 KSM 調整會寫主機的 `/sys/kernel/mm/ksm/run`；容器裡這個路徑是唯讀的，`create.sh` 會關掉
  `ENABLE_KSM`／`ENABLE_KSMTUNED` 並讓 DevStack 寫不進去就跳過，不會動到 開發機。
- 開發機 重開機後 OVS 核心模組與 `DOCKER-USER` 規則都會消失，要先跑 `start.sh`。

## 實測：預設政策下各帳號看得到什麼

DevStack 2026.1 的預設政策（新版 RBAC），用 開發機 主機端直接打 API 的結果：

| 呼叫 | reader（專案範圍）／應用程式憑證／member | reader（system 範圍） | admin |
| --- | --- | --- | --- |
| Nova `/servers/detail?all_tenants=1` | 403（`os_compute_api:servers:detail:get_all_tenants`） | 403 | 200，41 台、`limit=10` 分 5 頁 |
| Nova `/servers/detail`（不帶 all_tenants） | 200，只有自己專案；**沒有** `OS-EXT-SRV-ATTR:*` 欄位 | 403 | 200 |
| `hypervisor_hostname` | 看不到（欄位不存在） | － | 看得到 |
| Nova `/os-hypervisors/detail` | 403 | 403 | 200 |
| Neutron 任何 `?all_tenants=1` | **400**（`['all_tenants'] is invalid attribute for filtering`） | 400 | **400** |
| Neutron 不帶參數 | 200，自己專案＋shared／external 網路 | 200 但 0 筆 | 200，全部專案 |
| `binding:host_id` | 看不到（欄位不存在） | － | 看得到 |
| Keystone `/v3/projects` | 403 | 200 | 200 |
| Keystone `/v3/auth/projects` | 200（有角色的專案） | 200 | 200 |

重點：

- Nova 要列全部專案的虛擬機、看主機名稱，只能用 admin（或雲端自訂政策）；reader 不行，system 範圍在 Nova 完全不能用。
- Neutron 不認 `all_tenants`，帶了會 400（admin 也一樣）；admin 不帶參數就回全部專案。
- reader 若範圍是 admin 專案，因為 `shared-net`／`ext-net` 的擁有者是 admin，也看得到別的專案接在這兩個網路上的埠
  （a2、b2 的網卡，路由器閘道埠、浮動 IP 埠）。
- Nova 最大微版本是 2.103；帶 `OpenStack-API-Version: compute 2.103` 時伺服器資料內嵌規格（flavor）。
- fake 驅動支援即時遷移，遷移後 `hypervisor_hostname` 與埠的 `binding:host_id` 都會跟著換。
