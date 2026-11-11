# jt-ipam v1.0.22

[![License](https://img.shields.io/github/license/jasoncheng7115/jt-ipam?color=blue)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/jasoncheng7115/jt-ipam)](https://github.com/jasoncheng7115/jt-ipam/commits/main)
[![Stars](https://img.shields.io/github/stars/jasoncheng7115/jt-ipam?style=flat)](https://github.com/jasoncheng7115/jt-ipam/stargazers)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![Vue](https://img.shields.io/badge/Vue-3-42b883?logo=vuedotjs&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![OWASP](https://img.shields.io/badge/OWASP-Top%2010%3A2025-000000)

**🌐 [プロジェクトサイト →](https://jasoncheng7115.github.io/jt-ipam/?lang=ja)**

> 自社運用型で連携を中心に据えた IPAM です。phpIPAM の利用者にとって馴染みのある操作の流れを保ちながら独自に開発し、複数の DNS サーバー、LibreNMS、OPNsense、pfSense、FortiGate、Palo Alto、Check Point、MikroTik RouterOS、Windows DHCP Server、単独の Kea と ISC DHCP、Technitium DNS/DHCP、Proxmox VE、VMware ESXi / vCenter、Hyper-V / SCVMM、CubeCOS / OpenStack、Wazuh、Zabbix、そしてローカルの LLM と深く連携します。
>
> Jason Tools Co., Ltd. 提供 · ライセンス：AGPL-3.0 · English: [README.md](README.md) · 繁體中文: [README_zh-TW.md](README_zh-TW.md)

---

## なぜ jt-ipam なのか

phpIPAM の利用者がその日から使えるよう操作の流れを揃えつつ、現代的な技術スタックでゼロから作り直しています（phpIPAM のコードは使っていません）。連携が中核です。

- **DNS**：PowerDNS、BIND 9、OPNsense Unbound、Univention UCS、Microsoft Windows DNS、Technitium DNS Server（読み取りのみ：正引き・逆引きのレコードと整合性を読み取り、DNS サーバーには書き込みません。Windows DNS は WinRM で、既定は NTLM 暗号化を強制した HTTP 5985、HTTPS 5986 も可）
- **LibreNMS**：機器の同期、ARP / FDB の収集、死活状態の突き合わせ、監視への自動登録
- **SNMP 収集（スキャンエージェント）**：LibreNMS のないネットワーク向けに、サブネットを担当するスキャンエージェントが、割り当てたネットワーク機器を読み取り専用の SNMP v2c/v3 で読みます（認証情報は一元管理し、API では返しません）。ルーターの ARP テーブルでルーター越しに見えない MAC を補い、スイッチの MAC テーブル（Q-BRIDGE / BRIDGE-MIB、Cisco は VLAN ごと）でホストの接続ポートを特定し、LLDP / CDP のネイバーでトポロジーにスイッチ間のリンクを描き、インターフェイスを機器ポートにします。エージェントのインストーラーは net-snmp のクライアントツール（`snmp` / `net-snmp-utils` / `net-snmp-tools`）も導入します
- **Zabbix**：監視面を補う読み取り専用の連携です。ホストと IP の対応、実効状態の根拠としての死活、メンテナンス期間、そして**監視の抜け**（IPAM がホスト名を持っているのに Zabbix が見ていないアドレス）が分かります。ARP と FDB は Zabbix の標準データに含まれないため、引き続き LibreNMS が担当します
- **基盤**：Proxmox VE、**VMware ESXi / vCenter（Beta）**：単体の ESXi と vCenter を同じ設定で扱い、vSphere API 経由の読み取り専用で仮想マシン・NIC・アドレスを取得し、Proxmox と同じ仮想化テーブルに格納します。**Microsoft Hyper-V（Beta）**：WinRM（PowerShell、NTLM または Kerberos）経由の読み取り専用で仮想マシン・NIC・VLAN・アドレス・ゲスト OS（KVP）を取得します。ホストに直接（フェールオーバークラスターは全ノード。NTLM はノード間で転送できません）または System Center VMM 経由で接続し、ホストの NIC と仮想スイッチはそのホストの機器のポートとして登録します。**Bigstack CubeCOS / OpenStack**：Keystone v3（パスワードまたはアプリケーション認証情報）、Nova、Neutron 経由の読み取り専用で仮想マシン・Neutron ポート・サブネット・Floating IP を取得し（送るのは GET のみ。POST は Keystone へのトークン要求だけ）、同じ仮想化テーブルに格納します。VM のアドレスを IP レコードに結び付けるのは、Neutron サブネットが jt-ipam のサブネットに対応付けられている場合だけです（CIDR がクラウド内で一意、かつ範囲内に同じ CIDR の jt-ipam サブネットがちょうど 1 つのときだけ自動で対応付け、プロジェクト間で重複するネットワークは「未対応」として管理者が手動で指定）。Floating IP はポートと一緒に表示しますがゲストのアドレスとしては扱わず、CubeCOS 拡張ではノード一覧も取得します。Wazuh、OPNsense / pfSense（エイリアス/ルール/NAT の同期）、**FortiGate**：FortiOS REST API 経由の読み取り専用（DHCP のリースと範囲、ARP、IPsec トンネルと SSL-VPN セッション、ポリシー、NAT、アドレスオブジェクト。マルチ VDOM 対応）、**Palo Alto（Beta）**：PAN-OS API 経由の読み取り専用（ARP、DHCP リース、App-ID を含むセキュリティポリシー、NAT、アドレスオブジェクト。マルチ vsys 対応）、**Check Point（Beta）**：管理サーバーの R81.20 Management API 経由の読み取り専用（ゲートウェイ一覧、ネットワークオブジェクト、インラインレイヤーとヒット数を含むアクセスルール、宛先 NAT。Multi-Domain 対応でドメインごとにログイン、読み取り専用セッション。ゲートウェイごとに Gaia API で DHCP サーバー設定を読み取り専用で取得し、固定の読み取りコマンドで ARP テーブルと DHCP リースも取得。コマンドを実行できないアカウントでは自動的にスキップ）、**MikroTik RouterOS（Beta）**：RouterOS v7 REST API 経由の読み取り専用（filter / mangle / NAT のファイアウォールルール、アドレスリスト、DHCP のリースと範囲、VPN、ARP）。MikroTik は拠点の主力ルーターであることが多いため、区分を直列に実行して間に休止を入れ、CPU 負荷が閾値を超えたらその回を中止し、応答サイズにも上限を設けています（RouterOS の REST にはページングがありません）。負荷の高い区分は既定で無効で、接続診断がエンドポイントごとの行数と秒数を報告します
- **DHCP**：サーバーごとに個別に設定します。OPNsense（Kea / dnsmasq / ISC）と pfSense はそれぞれの REST API でリース・アドレス範囲・固定割り当てを同期し、**Windows DHCP Server**（Beta）は WinRM + PowerShell の読み取り専用です（`Get-*` のみ。WinRM に到達できる必要があり、既定は NTLM で暗号化する HTTP 5985。Windows Server のファイアウォールが既定で 5986 をブロックしているため）。**単独の Kea** は JSON 制御 API（Control Agent、または Kea 3.0 以降の DHCP サーバー自身の HTTP 制御ソケット。リースには lease_cmds フックが必要）で読み取り、**Technitium DHCP** は Technitium の HTTP API で読み取り（トークンは閲覧権限のみ。除外範囲を差し引いたスコープ、予約、リース、スコープごとにクライアントへ配布するゲートウェイ/DNS で、IP 変更評価が照合します）、**単独の ISC DHCP**（isc-dhcp-server にはリースを一覧できる API が無い）は DHCP ホストにインストールしたスキャンエージェントが dhcpd.conf/dhcpd.leases をローカルで解析し、解析済みのプール・固定割り当て・有効なリースだけを報告します。プール内のアドレスは IP 一覧と詳細に表示されます。DHCP サーバーで設定した除外範囲（Windows DHCP、FortiGate、Technitium、Check Point）は先にプールから差し引き、除外されたアドレスには「DHCP 除外」と表示します。**ISOinsight（Beta）** は機器の HTTP(S) インターフェイスにログインし、DHCP リースを読み取り専用で取得します。
- **Wazuh**：エージェント一覧（状態、OS、CVE 件数、SCA）を IP に対応付け、ホスト名があるのに有効なエージェントの無い IP を一覧表示します
- **OCS Inventory NG**：NIC の MAC で PC を既存の IP に対応付け（レコードは作成しません）、装置ページにハードウェア情報を表示し、一度も棚卸しされていない IP を一覧表示します
- **RustDesk Server（オープンソース版）**：RustDesk ホストに入れた専用の RustDesk エージェント（1 行でインストール）が登録済みデバイスの ID とオンライン状態を読み取り専用で読みます（秘密鍵ファイルは決して開きません。jt-ipam とホスト管理者の両方が許可した場合は古い登録の削除もできます）。登録 IP が明確で最近オンラインのときだけ IP レコードに対応付け、IP ページから Web ブラウザー内で直接接続でき（RustDesk 互換の Web 接続。暗号化はブラウザー内で行います）、サーバーと公開鍵を渡してローカルの RustDesk クライアントを起動することもできます（リンクにパスワードは含みません）。クライアント自身の報告から、接続監査、ホスト名 / OS / ユーザー、総当たり警告も得られます（クライアント設定の変更は不要）
- **Graylog**：Graylog の「DSV File from HTTP」データアダプタ向けに、IP→ホスト名 / FQDN のルックアップ用エンドポイントを提供します
- **ローカル AI**：LLM サーバーによる自然言語での問い合わせとセマンティック検索（既定は自社運用なのでデータは外部に出ません。OpenAI 互換エンドポイントを明示的に選ぶこともできます）。加えて MCP サーバー（stdio と Streamable HTTP）を備え、外部の LLM クライアントから IPAM を操作できます。検証では `gemma4:26b` が良好でした。セキュリティ面の AI としては、**ファイアウォールのルール変更監視**（OPNsense・pfSense・FortiGate・Palo Alto・Check Point・MikroTik のファイアウォールについて同期のたびにスナップショットを比較し、夜のうちに許可ルールが増えれば管理者へ通知）、**チャットでの IP フォレンジック**（「この IP は先週誰のものだったか」と尋ねると、項目単位の変更履歴・ARP / MAC の対応・ソース別のホスト名を根拠つきの時系列で返します）、**未許可 IP の AI 判読カード**（OUI ベンダー・ホスト名・スイッチポートをまとめ、「これはおそらく何の機器で、次にどこを見るべきか」を提示。根拠はプロンプトインジェクション対策として区切ります）があります。

**IP 変更の影響評価**：IP の変更、廃止、スイッチの保守、ノードの停止の前に、各連携でそのアドレスやデバイスを参照している箇所（DNS、DHCP、ファイアウォールルールと NAT、監視、仮想化、連携の接続先、証明書…）と影響を受けるサービスを一覧化し、ブロック・要レビュー・要変更・データ不足に分類します。影響別の関係図、レビューの流れ、作業と切り戻し手順付きで、レポートは PDF、Word、OpenDocument（一覧は Excel）でエクスポートできます。読み取りのみで、機器は一切変更しません。

**スキャンエージェント**：各セグメントに置く軽量エージェントが、オンライン状態、MAC、名前（逆引き・NetBIOS・mDNS）、OS を報告します。作業はサーバーが割り当て、エージェントは自動更新し、負荷パネルと過負荷通知で処理が追いついているか確認できます。大きなサブネット（例：/8）も実際のマスクのまま登録してスキャンできます：登録済みの IP と最近 ARP で見えたアドレスは毎周スキャンし、残りはバックグラウンドで新しい機器を探索するので、スキャンのためにネットワークを分割する必要はありません。各 IP のホスト名は、どのエージェントまたは連携が報告し、最後にいつ確認したかも分かります。

さらに標準で備えるもの：**ブラウザ内のリモートコンソール**では、SSH 端末、**SFTP ファイルブラウザ**（別のクライアント無しでアップロード/ダウンロード）、RDP と VNC のデスクトップ、**BMC 帯域外シリアルコンソール**（IPMI SOL）、RustDesk 互換の **Web 接続**（暗号化はブラウザ内で完結、ファイル転送付き、RustDesk クライアント不要）（BMC は **Beta**）をブラウザ内で開けます。ステータスバーには接続時間が表示されます。資格情報は既定では保存せず、利用者ごとの**暗号化された保管庫**も選べます。**踏み台ホスト**（バックエンドから直接届かない拠点では「バックエンド → 踏み台 → 対象」の経路にでき、出口はサブネット単位で設定してアドレス単位で上書きできます。踏み台の鍵はピン留めするまで接続を許可しません）、オブジェクト単位の RBAC、使い捨てチケットから確立する WebSocket セッション、完全な監査（接続ごとに接続時間も記録）（RDP / VNC はプリビルドの wheel がある場合だけ導入される任意依存を使うため、基本のインストールは変わりません）。**申請の承認フロー**（IP 申請と IP 変更評価それぞれに多段階/並行承認を設定可能。アプリ内とメールで通知）、**DNS レコードの点検**（IPAM に対応するアドレスの無いレコードを洗い出します）、**スキャンエージェント**（ICMP / ARP / 逆引き / NetBIOS / mDNS / OS の探査。ホストには自動的に 1 つ導入されます。スキャンは必ずエージェント経由です）、管理者専用の **IP 探索**（サービス・OS フィンガープリント・バナー・証明書からホストの種類を推定し、任意の [Recog](https://github.com/rapid7/recog) フィンガープリント DB とも照合。インストール時にダウンロードし毎週新版を確認。結論にはすべて根拠を表示）、**証明書の集中保管と配布**（商用または自己署名の証明書を一度アップロードすれば、純粋な bash のエージェントがスケジュールで取得し、nginx / apache / caddy / haproxy / Proxmox VE・PMG・PBS / Zimbra などへ配備してサービスを再読み込みします。**Windows / IIS 向けの PowerShell エージェント**もあり、Windows 証明書ストアへ取り込み、HTTPS バインディングを張り替え、実際の TLS 接続で切り替わりを確認し、失敗すれば元へ戻します。**ファイアウォールへの配信**（Beta）はエージェント不要で、FortiGate・OPNsense・Palo Alto・pfSense の管理 API から証明書を差し替え、同じく接続して確認し、失敗すれば元へ戻します。秘密鍵は暗号化され、期限の警告と手動更新にも対応）、**フロアプランとラックの立面図**（標準ラック・産業用ラック・シェルフ/クロムワイヤーシェルフ/アングル棚/木製シェルフ（IKEA IVAR 型）/IKEA KALLAX、さらに LackRack（IKEA LACK サイドテーブルを 19 インチラックとして使用）に対応し、幅を指定でき、段の高さは段ごとに設定でき、最上段の天板の上にも機器を置け、1 段の中で積み重ねられます。1 段に最大 6 台の横並び、前面/背面、SVG / PNG / draw.io への書き出し）、**ケーブル追跡**（マルチホップ）、未使用 IP の回収を伴う IP 変更履歴、そして汎用の列選択と複数形式でのエクスポート。

## Graylog のログ補完（DSV ルックアップ）

jt-ipam は IP → ホスト名 / FQDN の対照表を**リアルタイムに**生成します。Graylog の「DSV File from HTTP」データアダプタがこれを取得すると、IP しか含まないログイベントに読める名前が自動で付きます。

- **管理 → 外部システム連携 → Graylog** で有効にします。パスのスラッグ、出力形式（CSV / TSV）を選び、アクセストークンを生成します
- エンドポイント `GET /api/v1/lookup/<path>?token=<token>` は、リクエストのたびにデータベースから直接生成されます
- **提供される項目**：1 行 2 列で、1 列目が IP（キー）、2 列目がホスト名または FQDN（値）です。ホスト名を持つ IP だけが出力されます
- **データ形式**：UTF-8 のプレーンテキストです。CSV はカンマ区切りで**すべての項目を二重引用符で囲み**（RFC 4180 のエスケープ）、TSV はタブ区切り（引用符なし）です。例：

  ```csv
  "10.1.1.141","log1.example.com"
  "10.1.1.145","mg-host"
  ```

- Graylog の「DSV File from HTTP」アダプタでは、上の URL を設定し、区切り文字を形式に合わせてカンマまたはタブにし、**Key column = 0、Value column = 1** とします（Graylog の列番号は 0 起算です）
- トークンはリクエストごとに検証され、いつでも再生成できます。設定ページには、そのままコピーできる完全な URL が表示されます
- トークンには有効期限があり（30〜365 日、期限前に通知）、管理者が「表示」したときだけ見え、監査記録が残ります
- HTTPS の URL を使ってください。トークンが URL に含まれるため、平文の 8088 番ポートは「平文 HTTP を許可」をオンにしない限り 404 を返します（アップグレード前から使っていたサイトはオンのまま）。「許可する送信元」で取得できるアドレスを制限できます

## BMC 帯域外コンソール（IPMI SOL、Beta）

サーバーの **BMC**（IPMI 2.0 Serial-over-LAN）へ、その IP から直接キーボードとテキストのコンソールを開けます。ベンダーの Java / HTML5 KVM は不要です。IP ごとに有効化し（RBAC の扱いは SSH と同じ）、BMC の資格情報は同じ暗号化された保管庫に保存でき、すべてのセッションが監査に残ります。**破壊的な操作はできません**：キーボードとテキスト画面のみで、電源制御やマウスはありません。

SOL が中継するのはホストの**シリアルポート**だけなので、ホスト側にシリアルコンソールの設定が無ければ画面は真っ白のままです。ホスト側の初回設定は次のとおりです。

1. **SOL に対応するポートを特定する**：`dmesg | grep -iE 'ttyS|SPCR'`（例：`SPCR: console: uart,io,0x3f8,115200` なら `0x3f8` = ttyS0、`0x2f8` = ttyS1）。ポートを間違えると真っ白のままです。
2. **カーネルの console を追加する**（物理モニタにも出力が残るよう `tty0` は残します）：
   - 一般的な Linux（GRUB）：`/etc/default/grub` の `GRUB_CMDLINE_LINUX` に `console=tty0 console=ttyS0,115200n8` を追加し、`update-grub` を実行します。
   - Proxmox VE（systemd-boot / ZFS）：同じ内容を `/etc/kernel/cmdline` に追記し、`proxmox-boot-tool refresh` を実行します。
3. **シリアルログインを有効にする**（即時反映、再起動不要）：`systemctl enable --now serial-getty@ttyS0`。
4. **（任意）BIOS のコンソールリダイレクション**：同じ COM ポート（115200 8N1）に向けると、POST や BIOS も SOL で見られます。項目ごとの設定値（Terminal Type、Flow Control、**Redirection After BIOS POST** など）は [BMC / SOL 設定ガイド](https://jasoncheng7115.github.io/jt-ipam/bmc-sol.html?lang=ja) にあります。
5. **再起動**して `console=` を反映します。以後、SOL には起動全体とカーネルパニックも表示されます。物理モニタには影響しません。

いますぐログインできれば良い場合は、手順 3 だけで十分です。同じガイドはアプリにも組み込まれており、BMC コンソールの**設定ガイド**ボタンから開けます。

**トラブルシューティング（実際にあった落とし穴）：**

- **接続できるが真っ白で、Enter を押しても反応しない**：SOL が SPCR の宣言どおりのポートに対応していない場合があります。SOL を接続した状態で `echo test > /dev/ttyS0`（および `/dev/ttyS1`）を実行し、どちらが表示されるか確かめてください。あるいは `/proc/tty/driver/serial` を見て、`rx` がゼロでない ttyS が SOL のポートです。
- **ログインプロンプトは出るが起動メッセージが出ない**：カーネルコンソールが別の（SOL ではない）ttyS に向いていて、`serial-getty` だけが正しいポートにある状態です。`console=` には **SOL のポートだけ**を書いてください（例：`console=tty0 console=ttyS1,115200n8`）。複数の `ttyS` を書くと、カーネルが意図しない側を選ぶことがあります。`cat /proc/consoles` で確認できます。
- **出力は出るが文字化けする**：シリアルのボーレートが SOL と一致していません。`ipmitool -I open sol info 1 | grep 'Bit Rate'` を確認し、`serial-getty` を同じボーレートに設定してください。
- **枠線や色が崩れる（glances など）**：シリアルログインの `TERM` を `xterm-256color` に設定してください（`serial-getty` の既定は `vt220` であることが多いです）。
- **OS の起動メッセージに絵文字（⚠️ など）が出る**：これは systemd 自身の記号です。カーネルのコマンドラインに `systemd.setenv=SYSTEMD_EMOJI=0` を追加してください。**BIOS** 画面の文字化けについては、BIOS のコンソールリダイレクションで **Terminal Type = VT100+** にします（VT-UTF8 ではありません）。
- **コンソールの表示領域が小さく、周囲に黒い余白が出る**：シリアルはウィンドウサイズを自動で通知できません。コンソールの**ウィンドウに合わせる**ボタン（`stty rows/cols` を送ります。シェルのプロンプトで押してください）を使うか、自分で `stty rows N cols N` を実行してください。

## 自分で IP レコードを作るソースはどれか

連携先が IPAM にまだ無いアドレスを見つけたとき、その扱いはソースごとに異なります。これは重要です。「未許可 IP」の異常検知は **「ARP では見えるが IPAM には無い」** と定義されているからです。そのための対照表が以下です。

| ソース | IPAM にそのアドレスが無いとき | 切り替え | 既定 | サブネットの決め方 |
|---|---|---|---|---|
| **スキャンエージェント** | 作成することがあります | 「未登録の IP を自動的に記録する」 | **無効** | そのエージェントに割り当てられ、スキャンが有効なサブネット内に限ります |
| **LibreNMS** | 作成することがあります（機器の主 IP のみ。ARP の近傍は対象外） | 「検出した IP を自動作成する」 | **既定で有効** | それを含む最小のサブネットに入れます。判別できなければ何も作りません |
| **Proxmox VE** | 作成することがあります | 「仮想化から得た IP を信頼する」 | **既定で無効** | それを含む最小のサブネットに入れます。判別できなければ何も作りません |
| **VMware / ESXi** | 作成することがあります | 「仮想化から得た IP を信頼する」 | **既定で無効** | それを含む最小のサブネットに入れます。判別できなければ何も作りません |
| **Hyper-V / SCVMM** | 作成することがあります | 「仮想化から得た IP を信頼する」 | **既定で無効** | それを含む最小のサブネットに入れます。判別できなければ何も作りません |
| **CubeCOS / OpenStack** | 作成することがあります（対応付け済みの Neutron サブネット内のみ） | 「仮想化から得た IP を信頼する」 | **既定で無効** | その Neutron サブネットに対応付けた jt-ipam サブネットだけに入れます。「未対応」の間は作りません |
| **OPNsense / pfSense** | 作成することがあります（DHCP リース） | 「IPAM に無いアドレスを作成する」 | **既定で無効** | それを含む最小のサブネットに入れます。判別できなければ何も作りません |
| AdGuard / Wazuh / Zabbix / OCS / RustDesk / DNS / Windows DHCP / Kea / ISC DHCP / Technitium DHCP / ISOinsight / FortiGate / Palo Alto / Check Point / MikroTik | **照合のみ。作成しません** | なし | なし | なし |
| CSV 取り込み/phpIPAM 移行 | 取り込んだ内容から作成（利用者の明示的な操作） | なし | なし | 取り込んだとおり |

**共通ルール**：自動作成はすべて同じ判断（`services/ip_autocreate.py`）を通ります。**そのアドレスを含む最小のサブネットに入れる。どれに入れるべきか判別できなければ、何も作らない**。

例：
- `10.1.1.5` は `10.0.0.0/8` と `10.1.1.0/24` の両方に含まれるため、**小さい方**の `10.1.1.0/24` に入ります。
- 組織 A と組織 B が**それぞれ `192.168.1.0/24` を持っている**場合、その機器がどちらのものか知る術がないため、**何も作成しません**。間違った組織に記録するくらいなら、記録が無い方がましだからです。
- そのアドレスがどの既存サブネットにも入らない場合も、何も作成しません（サブネットを勝手に作ることはありません）。

二つ目のケースは、その連携の対象サブネット範囲を自分たちのサブネットに絞れば解消します。候補が自分たちのものだけになれば判別でき、レコードは通常どおり作成されます。

> ⚠️ **自動作成を有効にすると、検知能力の一部を手放すことになります。** アドレスを取得できた機器が、IPAM に記録されるべき機器とは限りません。無断で接続された機器がいったん記録されると、**「未許可 IP」には二度と現れません**。自動作成されたレコードは IP 一覧で「自動記録（未登録）」と表示され（オレンジのアイコン）、サブネットのグリッドでもオレンジの枠で示されます。凡例には「自動記録」の件数も出ますので、定期的に見直してください。

## 主要なエンティティ

`セクション → サブネット → IP アドレス` に加えて、`機器` / `ラック` / `拠点`、`顧客`（管理組織）、`VLAN` / `VRF`、`NAT`、OPNsense / pfSense / FortiGate / Palo Alto / Check Point / MikroTik のファイアウォール、そして IEEE の OUI ベンダーテーブル（毎月更新）です。

## アクセス制御（RBAC）

**7 種類のオブジェクト**（顧客/セクション/サブネット/IP/機器/ラック/拠点）に対するオブジェクト単位の権限です。

- **階層的な継承**：上位（顧客やセクションなど）に権限を与えると、その配下すべてに自動的に及びます（サブネット → IP、拠点 → ラック → 機器）
- オブジェクト種別ごとの **「すべて」** ワイルドカード
- **5 つの組み込みロール**：システム管理者、閲覧専用、ネットワーク運用者、監査者、部門管理者
- 可視性はあらゆる場所で強制されます。一覧のエンドポイント、横断検索、トポロジー図、そしてすべてのドロップダウンは、その利用者が見てよいオブジェクトしか返しません。既定は拒否です。

## セキュリティ（OWASP Top 10:2025）

セキュリティは初日からの要件です。すべてのモジュールと PR を **OWASP Top 10:2025** に照らして確認しています。[`SECURITY_ja.md`](SECURITY_ja.md) を参照してください。**セキュリティの問題はそこに書かれた方法で非公開で報告してください**（Security タブの「Report a vulnerability」または service@jason.tools）。公開の issue には書かないでください。公開済みのアドバイザリは [Security advisories](https://github.com/jasoncheng7115/jt-ipam/security/advisories) にあります。ISO/IEC 27001:2022 と ISO/IEC 42001:2023 の根拠として使える管理策と、導入組織が自ら行うことは [`docs/COMPLIANCE_ja.md`](docs/COMPLIANCE_ja.md) を参照してください。

- **TLS は必須**：nginx のリバースプロキシで終端する（`BACKEND_TLS_MODE=nginx`）か、uvicorn が自己署名証明書で直接提供する（`BACKEND_TLS_MODE=direct`）かのどちらかを選びます
- A01：既定で拒否の RBAC と、オブジェクト単位の判定（上記）
- A02：argon2id によるパスワードのハッシュ化、保存する機密情報のアプリケーション層での暗号化（DNS の資格情報 / SNMP / API トークン）。各機密は用途に結び付けて暗号化し、毎日のバックアップはパスフレーズで暗号化できます
- A03：パラメータ化した SQLAlchemy、厳格な Pydantic v2 の検証、CSP と出力のエスケープ
- A05：HSTS、CSP、X-Frame-Options、Referrer-Policy
- A07：管理者が必須にできる TOTP の二要素認証（リカバリーコード、再使用防止つき）、アカウントロック（認証コードの誤りも数える）、サーバー側のセッション（更新用トークンは HttpOnly + Secure + SameSite=Strict の Cookie にだけ置き、デバイスごとに取り消し可能、無効化は即時に反映、開いているコンソールは 30 秒以内に終了）、API トークンの有効期限
- A08：SHA-256 の監査チェーン。同期のたびに検証し、データベースの外（`/var/lib/jt-ipam/audit-anchors.jsonl` と journald）へ錨を打ちます。チェーンだけでは末尾を切り落とされたことを検知できないためです。`JT_IPAM_AUDIT_CHAIN_BASELINE_ID` で検証の開始 id を指定でき、もはや検証できない古い記録を抱えた環境に使えます。監査テーブルはデータベースで追記のみで、アプリのアカウントでも無効化できません（`scripts/jt-ipam.sh harden-audit`）
- A09：構造化された監査ログ。エクスポートやキー・トークンの表示も記録します
- A10：すべてのプロトコルの外向き接続を確認します（HTTP は接続の時点、LDAP・SMTP・RADIUS・WinRM・DNS・syslog・コンソールは接続前）。メタデータやリンクローカルは遮断し、コンソールはループバックにも接続しません

## 技術スタック

| 層 | 採用 |
|------|--------|
| バックエンド | Python 3.12 · FastAPI · SQLAlchemy 2.0（async）· asyncpg · Alembic · Pydantic v2 |
| データベース | PostgreSQL 16（ネイティブの `inet` / `cidr` / `macaddr`）+ pgvector |
| フロントエンド | Vue 3 · TypeScript · Vite · Naive UI · Pinia · vue-i18n |
| 認証 | argon2id · TOTP · 短命な JWT + リフレッシュ |
| AI | LLM サーバー（ローカル）· pgvector · MCP サーバー |
| デプロイ | systemd + nginx + apt パッケージ、**Docker イメージは不要**（VM / コンテナのどちらでも） |

## インストール（単一ホスト / VM / コンテナ）

> Debian 12 / Ubuntu 22.04 以降（64 ビット）。TLS は必須です。
>
> **最低構成：** 2 vCPU · 4 GB メモリ · 20 GB ディスク。**推奨：** 4 vCPU · 8 GB メモリ · 50 GB ディスク（データベース、監査ログ、IP 変更履歴、バックアップの増加に備えて）。4 GB のマシンではバックエンドのワーカーは 2 つになり、アップグレード時にメモリが足りなければフロントエンドのビルド中はバックエンドを一時停止します。2 GB のスワップがあれば停止しません。RDP コンソールは 1 セッションあたり数百 MB のメモリと CPU を使います。LLM サーバーを同じマシンに置く場合、そのメモリは別に必要です。
>
> 任意のローカル LLM（Ollama）はこの数字に**含まれていません**。別のホストで動かし、選ぶモデルに応じたメモリ / VRAM を用意してください。

```bash
# 前提：最小構成のシステムには curl が無いことがあります（一行インストールに必要です）
sudo apt-get update && sudo apt-get install -y curl
# 一行で完了。/opt/jt-ipam へ自動的に clone してインストールします（git を手で叩く必要はありません）
curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-ipam/main/scripts/bootstrap.sh | sudo bash
```

このスクリプトは `postgresql-16` / `python3.12` / `nginx` / `redis`（PDF レポートに埋め込む中国語・日本語フォント `fonts-noto-cjk` も）を導入し、`jtipam` のシステムアカウントと PG のロールを作成し、鍵を生成して `/etc/jt-ipam/backend.env` に書き込み、`alembic upgrade head` を実行し、フロントエンドをビルドして `jt-ipam-backend.service` を有効にします。

既存の環境の更新は `sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade` です。**スクリプト自身が `git pull` を実行し**、続いてバックアップ → 依存関係 → alembic → ビルド → 再起動を行います。詳細は [`docs/INSTALL_ja.md`](docs/INSTALL_ja.md) を参照してください。

> **0.5.170 以前から上げる場合は `upgrade` を 2 回実行してください。** アップグレードスクリプト自体も `git pull` で更新されますが、その回は古い方のコピーで動いています（0.5.171 以降は、pull の後に新しい方へ引き継ぎます）。その後 `sudo bash /opt/jt-ipam/scripts/jt-ipam.sh doctor` を実行すると結果を確認でき、確認できなかった項目には対処コマンドが表示されます。

> **任意：Docker Compose。** 副次的な導入経路が [`deploy/docker/`](deploy/docker/) にあります（`./gen-env.sh` のあと `docker compose up -d --build`。更新は `./update.sh`）。主たる、そして完全にサポートされる方式は引き続き systemd + apt です。

### インストールしたのに様子がおかしい場合は健全性チェックを

```bash
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh doctor
```

設定ファイル、バックエンドが実際に応答するか、データベースと `pgvector` 拡張、スキーマが最新のリビジョンか、ビルドされたフロントエンドがバックエンドのバージョンと一致するか、タイマーとバックアップ用ディレクトリ、直近の同期結果、ローカルのスキャンエージェントを確認します。**確認できなかった項目には、そのままコピーして実行できるコマンドが付きます**（`→` の行）。ログを掘り返す必要はありません。不具合を報告する際にこの出力を添えていただけると、対応がずっと速くなります。

うまくいかないときは、[インストールとアップグレードのトラブルシューティング](https://jasoncheng7115.github.io/jt-ipam/troubleshooting.html?lang=ja)（検索できる Q&A）もご覧ください。

### 初回ログインと管理者パスワードの再設定

新規インストールでは、スクリプトが **`admin` アカウントをランダムなパスワードで作成し、最後に一度だけ表示します**（同時に `/etc/jt-ipam/.admin-initial-password` に保存されます。root のみ読み取り可能で、`/etc` 配下かつ web ルートの外にあるため HTTP からは到達できません）。ログインしたらすぐに変更し、その後はこのファイルを削除して構いません：`sudo rm /etc/jt-ipam/.admin-initial-password`。

管理者パスワードを再設定する（または管理者がいない環境で最初の管理者を作る）には、サーバー上で次を実行します。

```bash
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; source /etc/jt-ipam/backend.env; set +a; \
  .venv/bin/python -m app.cli.bootstrap create-admin \
    --username admin --email admin@example.com --password-stdin --force-update'
# 続いて新しいパスワードを標準入力から入力します（12 文字以上）
```

`--force-update` を付けなければ、既存の再設定ではなく新しい管理者の作成になります。

唯一の管理者が認証アプリとリカバリーコードの両方を失った場合は、サーバーからその二要素認証を消去します（すべてのデバイスからのログアウトと監査記録も行います）。

```bash
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; source /etc/jt-ipam/backend.env; set +a; \
  .venv/bin/python -m app.cli.bootstrap reset-mfa --username admin'
```

## TLS / HTTPS

HTTPS は必須です。`/etc/jt-ipam/backend.env` の `BACKEND_TLS_MODE` でモードを選びます。

**モード A：nginx のリバースプロキシ（既定・推奨）** `BACKEND_TLS_MODE=nginx`
nginx が TLS を終端し、127.0.0.1:8000 の uvicorn へ中継します。正式な証明書を入れるには次のようにします。

```bash
# 決められたパスの証明書と鍵を上書きして再読み込みします（パスは nginx の設定に直接書かれています）
cp fullchain.pem /etc/jt-ipam/tls/server.crt
cp privkey.pem   /etc/jt-ipam/tls/server.key
chmod 600 /etc/jt-ipam/tls/server.key
nginx -t && systemctl reload nginx
```

Let's Encrypt の場合は、`ssl_certificate` を `/etc/letsencrypt/live/<FQDN>/fullchain.pem` に、`ssl_certificate_key` を `…/privkey.pem` に向け、更新のたびに `systemctl reload nginx` を実行します。自前で立てる場合の最小のリバースプロキシ設定は次のとおりです。

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

**モード B：uvicorn が直接、自己署名で** `BACKEND_TLS_MODE=direct`
uvicorn 自身が TLS を提供します。インストール時に `scripts/generate-self-signed-cert.sh` が自己署名証明書を作成します。差し替えるには同じパスを上書きし、サービスを再起動します。

```bash
cp fullchain.pem /etc/jt-ipam/tls/server.crt
cp privkey.pem   /etc/jt-ipam/tls/server.key
chmod 600 /etc/jt-ipam/tls/server.key
systemctl restart jt-ipam-backend
```

> どちらのモードも証明書のパスは同じ（`/etc/jt-ipam/tls/server.{crt,key}`）で、違いは誰が TLS を終端するかだけです。モード A は nginx を再読み込みし、モード B はバックエンドを再起動します。

**モード C：自前の外部リバースプロキシの背後で**（別の nginx / LB が TLS を終端する場合）
ローカルの nginx は平文 HTTP を提供します。外部プロキシ用のテンプレートを適用してください。

```bash
sudo cp deploy/nginx/jt-ipam-external-proxy.conf         /etc/nginx/sites-available/jt-ipam
sudo cp deploy/nginx/jt-ipam-external-proxy-snippet.conf /etc/nginx/snippets/jt-ipam-proxy.conf
sudo nginx -t && sudo systemctl reload nginx
```

> ⚠️ **必須：公開側の境界でのセキュリティヘッダ。** **利用者に対して TLS を終端する**プロキシが、セキュリティヘッダ（HSTS、CSP `frame-src 'self'`、X-Frame-Options、nosniff、Referrer-Policy、Permissions-Policy、COOP、CORP）と `server_tokens off` を出す必要があります。これらは一段余分にプロキシを経由しても**引き継がれません**。境界の機器が上記とは*別の*マシンであれば、**その機器にもヘッダを設定してください**（同梱のテンプレートには含まれています。nginx 以外の LB では同等の設定を入れてください）。実際の公開 URL で検証します：
> `curl -skI https://your-domain/ | grep -iE 'strict-transport|content-security|x-frame|cross-origin|^server'`
> 各ヘッダは**ちょうど 1 回ずつ**、`Server: nginx`（バージョン表記なし）になるはずです。

外部プロキシを置いても OIDC / M365（Entra ID）のログインは壊れませんが、次の 3 点が正しくないと `ipam.example.com` へリダイレクトされたり、ログイン画面から進めなくなります。
1. `/etc/jt-ipam/backend.env` の `APP_PUBLIC_URL` / `API_PUBLIC_URL` / `CORS_ORIGINS` を実際の公開ドメインに設定し（既定の `ipam.example.com` のままにしないでください）、`systemctl restart jt-ipam-backend` を実行します。
2. 外部プロキシは `X-Forwarded-Proto $scheme`（= https）と `Host $host` を転送する必要があります。テンプレートはそのまま渡すので、バックエンドは https と認識します（Secure Cookie が機能します）。
3. OIDC のリダイレクト URI を、IdP と jt-ipam の UI の両方で `https://your-domain/api/v1/auth/oidc/callback` に設定します。**UI / DB の値が .env より優先される**ため、.env を編集した後は UI 側でも保存し直してください。

## プロジェクトの構成

```
jt-ipam/
├── docs/              # 仕様・セキュリティ・データモデル・API リファレンス
├── backend/           # FastAPI アプリ
│   └── app/
│       ├── core/      # config / db / audit / safe_http / encrypted_secret
│       ├── models/    # SQLAlchemy 2.0
│       ├── schemas/   # Pydantic v2
│       ├── api/v1/    # REST API
│       ├── services/  # 業務ロジック（ai / oui / opnsense / topology / search / permission）
│       ├── mcp/       # MCP サーバーとツール（LLM クライアント向け）
│       └── plugins/   # プラグイン機構
├── frontend/          # Vue 3 + TS
│   └── src/{views,components,composables,api,stores,i18n,router}
└── scripts/           # jt-ipam.sh（install/upgrade/uninstall）、ci.sh、oui_refresh.py
```

## ロードマップの状況

- **フェーズ 1（完了）**：phpIPAM と同等の機能に改善を加えたもの（セクション/サブネット/IP/VLAN/VRF/NAT/機器/ラック/拠点/IP 申請、TOTP/API トークン/RBAC、phpIPAM の取り込み、CSV / RIPE / TWNIC、サブネットの可視化グリッド、TLS の強制）
- **フェーズ 2（完了）**：複数ベンダーの DNS と LibreNMS の深い連携（機器/ARP/FDB/実効状態）、異常検知、SHA-256 の監査チェーン、pgvector による AI セマンティック検索
- **フェーズ 3（完了）**：組織/連絡先/配線/電源/VPN/仮想化、Proxmox VE の同期、Cytoscape のトポロジー、OIDC / SAML の SSO、OPNsense / pfSense / FortiGate / Palo Alto / Check Point / MikroTik のファイアウォール同期、VMware ESXi / vCenter の棚卸し、Wazuh のエージェント棚卸し、Zabbix の監視カバレッジ
- **フェーズ 4（範囲を絞って完了）**：MCP サーバー、ローカル LLM による自然言語（LLM サーバー）、プラグイン機構

### ラック図を他のシステムに埋め込む

ラック図は単一の URL で SVG として提供できるため、他のダッシュボード（たとえば LibreNMS のウィジェット）から単純な `<img>` で表示できます。

```html
<img src="https://your-ipam.example.com/api/v1/racks/<rack-id>/embed.svg?token=<token>">
```

二つのスイッチを両方とも入れる必要があります。**管理 → システム設定**で埋め込みを有効にしてトークンを生成し、さらに**そのラック自身**の「外部への埋め込み」を有効にします（ラック単位で、既定は無効）。ラック図には機器名と位置が表示され、トークンは鍵と同じです。公開の場に貼らないでください。トークンを再生成すると、既存の URL はすべて直ちに無効になります。トークンには有効期限があり（30〜365 日、期限前に通知）、管理者が「表示」したときだけ見え、監査記録が残ります。

iframe ではなく画像として提供しているのは意図的です。このサービスは `frame-ancestors 'none'` を送るため iframe での埋め込みは設計上ブロックされており、特定のオリジンを許可することはクリックジャッキングの余地を開くことになるからです。

## ライセンス

AGPL-3.0-or-later。商用サポートについては Jason Tools までご連絡ください。
