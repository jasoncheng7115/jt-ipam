# jt-ipam インストールと運用の手順書

> English: [INSTALL.md](INSTALL.md) · 繁體中文版：[INSTALL_zh-TW.md](INSTALL_zh-TW.md)

対象は **Proxmox LXC、ベアメタル、仮想マシン**で、OS は Debian 12/13 または Ubuntu 22.04/24.04/26.04（x86_64、[対応ディストリビューション](#対応ディストリビューション)を参照）です。
**主たる推奨の導入方法**は、**systemd + apt** を直接使う方式です（Docker は使いません）。
Docker Compose の経路もありますが、**任意かつ副次的で、優先される方式ではありません**。
[§2.8](#28-任意docker-composeこれは優先される方式ではありません) を参照してください。

> セキュリティは初日からの要件です。どの環境でも HTTPS を強制します。証明書は nginx の
> リバースプロキシ経由でも、uvicorn が直接提供する自己署名でも構いません。SSL が設定されて
> いない場合、バックエンドは**起動しません**（A02）。

うまくいかないときは、[インストールとアップグレードのトラブルシューティング](https://jasoncheng7115.github.io/jt-ipam/troubleshooting.html?lang=ja)（検索できる Q&A）もご覧ください。

---

## 1. システム要件

| 項目 | 最低 | 推奨 | 備考 |
|---|---|---|---|
| OS | Debian 12/13、Ubuntu 22.04/24.04/26.04（x86_64） | **Ubuntu 24.04 LTS** | 対応はこれらのみ（下記参照）。24.04 は Python 3.12 + PG 16 を同梱しており手間が省けます |
| CPU | 2 vCPU | 4 vCPU | アップグレード時にフロントエンドをビルドします（約 1 分）。RDP コンソール（guacd）とローカルのスキャンエージェント（nmap）が CPU を使います。埋め込みは LLM サーバー側で計算され、このマシンではありません |
| メモリ | 4 GB | 8 GB | ワーカー 4 つで通常約 1.8 GB を使用し、アップグレード時のフロントエンドのビルドは最大約 1.6 GB。4 GB または 2 コアのマシンではワーカーは 2 つになり、メモリが足りなければビルド中はバックエンドを一時停止します（2 GB のスワップがあれば停止しません）。RDP コンソールは 1 セッションあたり数百 MB。LLM サーバーを同居させるならさらに 8 GB 以上 |
| ディスク | 20 GB | 50 GB | インストール自体は約 2 GB（Python パッケージ、node_modules、キャッシュ）と OS。データベース、監査ログ、IP 変更履歴、バックアップはネットワークの規模に応じて増えます。journald のログには上限があります |
| Python | 3.11 | 3.12 | 24.04 の既定は 3.12 |
| PostgreSQL | 16 + pgvector | なし | 22.04 では PGDG リポジトリが必要です（スクリプトが自動で追加します） |
| Redis | 7 | なし | 24.04 の既定は 7.0.15 |
| Node | 22 LTS | なし | フロントエンドのビルドにだけ使います。`jt-ipam.sh` がインストール時に NodeSource 22 を入れ（22 を同梱しているのは Ubuntu 26.04 だけ）、アップグレード時に古い Node を 22 に上げます |
| guacd | jt-ipam がこの OS 向けにビルドしたもの | なし | **必須**：RDP/VNC コンソールの接続エンジン。`jt-ipam.sh` が入れます（下の guacd の節）。旧エンジンの aardwolf は任意 |
| Recog | 最新リリース | なし | **任意**：IP 探索が機器や OS バージョンを識別するためのフィンガープリント DB。`jt-ipam.sh` がダウンロードし、毎週新版を確認（下の Recog の節） |
| CJK フォント | `fonts-noto-cjk` | なし | **PDF レポートに必要**：バックエンドが PDF に埋め込む中国語・日本語フォント（ダウンロード約 60 MB）。`jt-ipam.sh` がインストール時とアップグレード時に入れます。アップグレード時に apt のミラーへ接続できない場合は警告だけで、PDF のエクスポートはフォント不足を表示します（DOCX/ODT は使えます）。あとから `sudo apt install fonts-noto-cjk` で追加できます |
| SNMP クライアントツール | `snmp`（net-snmp） | なし | **任意**：ローカルのスキャンエージェントによる SNMP 収集（収集者「サーバー」）で使う `snmpget`/`snmpbulkwalk`。`jt-ipam.sh` がインストール時とアップグレード時に入れます（アップグレードで入れられない場合は警告のみ）。リモートのスキャンエージェントはそのインストーラーが入れます（apt は `snmp`、dnf は `net-snmp-utils`、apk は `net-snmp-tools`）。入っていないエージェントは SNMP 機能がないだけです |

### 対応ディストリビューション

| ディストリビューション | バージョン | アーキテクチャ |
|---|---|---|
| Debian | 12（bookworm）、13（trixie） | x86_64（amd64） |
| Ubuntu | 22.04 LTS、24.04 LTS、26.04 LTS | x86_64（amd64） |

これらに限られる理由：RDP と VNC コンソールのエンジンである guacd は**必須コンポーネント**ですが、使える guacd を
提供しているディストリビューションはありません（Debian は削除、Ubuntu にはリモートコード実行の脆弱性がある 1.3.0 のみ）。
jt-ipam は上記の各バージョン向けにビルドしており、それ以外では `install` が止まり、`upgrade` は警告します。
インストーラは `/etc/os-release` の `ID` ＋ `VERSION_ID` で対応するビルドを選ぶため：

- 上記バージョンの **Proxmox LXC テンプレート、仮想マシン、ベアメタル**で動きます。Proxmox VE ホスト自体（Debian）も可。
- **派生ディストリビューション**（Linux Mint、Pop!_OS、Zorin など）、**その他のバージョン**（Debian 11、Ubuntu 20.04、
  ビルドのない非 LTS など）、**ARM**（Raspberry Pi、Ampere）は対象外です。
- **OS の新バージョン**はリリースごとに確認し（`scripts/guacd/check-new-os.sh`）、ビルドと検証が済んでから追加します。
  この表に載るまで、jt-ipam ホストの OS をメジャーアップグレードしないでください。
- 上級者向け：他の Debian/Ubuntu バージョン用は `scripts/guacd/build.sh`（Docker が必要）でビルドし、
  `jt-ipam.sh install --guacd-tarball <ファイル>` で入れられます（当方では未検証）。

**仮想環境での注意**：Proxmox の VM / LXC では、起動や再起動の直後 1〜2 分ほど load average が
跳ね上がることがあります（ハイパーバイザ上の他の VM が CPU を取り合っているためで、`mpstat` の
`%steal` に現れます）。その VM 自体が忙しいわけではないので、そのままインストールして構いません。

---

## 2. 一発インストール

### 2.1 事前準備：apt update と再起動（強く推奨）

カーネルと libc の食い違いを避けるため、新しいマシンではインストール前に OS を完全に更新します。

```bash
sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get -y -qq upgrade
sudo systemctl reboot
```

### 2.2 一発インストール

最も手早いのはブートストラップです（/opt/jt-ipam へ自動的に clone し、統合された導入スクリプトを
実行します。インストール用のフラグもそのまま渡せます）。

```bash
# 前提：最小構成のシステムには curl が無いことがあります（一行インストールに必要です）
sudo apt-get update && sudo apt-get install -y curl
curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-ipam/main/scripts/bootstrap.sh \
  | sudo bash -s -- --tls-mode nginx --public-fqdn ipam.example.com
```

手動で clone して、統合された導入スクリプト `scripts/jt-ipam.sh` を実行することもできます。

```bash
git clone https://github.com/jasoncheng7115/jt-ipam.git /opt/jt-ipam
cd /opt/jt-ipam

# 3 つの TLS モードから 1 つを選びます：
#
#   nginx         ：nginx が HTTPS を終端し、バックエンドはループバック。証明書が無ければ自己署名で自動起動
#   self-signed   ：uvicorn が自前の自己署名証明書で直接提供（nginx 不要。最も早く動きます）
#   direct        ：uvicorn が直接提供し、証明書は自分で用意（無ければ自己署名にフォールバック）

# (A) nginx ＋一時的な自己署名（後から本物の証明書を cp します）、本番に推奨
sudo ./scripts/jt-ipam.sh install --tls-mode nginx --public-fqdn ipam.example.com

# (B) uvicorn 直接の自己署名（社内や検証で最も手早い）
sudo ./scripts/jt-ipam.sh install --tls-mode self-signed --public-fqdn ipam.local
```

> `scripts/install-debian.sh` は互換のための薄い入り口として残してあり（`jt-ipam.sh install` へ
> 転送します）、以前のコマンドもそのまま使えます。

スクリプトが行うこと：

1. PostgreSQL 16 + pgvector + Redis 7 の導入（Ubuntu 22.04 では PGDG リポジトリを自動追加、Ubuntu 24.04 は公式のものを直接使用）
2. `jtipam` システムユーザーと `/opt/jt-ipam/backend/.venv` の作成、Python の依存関係の導入
3. `jt_ipam` の DB ロールとデータベースの作成、alembic のマイグレーション適用
4. `SECRET_KEY` / `ENCRYPTION_KEY` / `AUDIT_CHAIN_GENESIS` を生成して `/etc/jt-ipam/backend.env`（0640）へ書き込み
5. `/etc/jt-ipam/tls/` に自己署名証明書を生成（ECDSA P-384、5 年。SAN には `localhost / FQDN / 短いホスト名 / 127.0.0.1 / ::1 / ホストの IP` を自動的に含めます）
6. pnpm install とフロントエンドの vite ビルド
7. `jt-ipam-backend.service` と `jt-ipam-sync.timer`（5 分ごと）、`jt-ipam-ai-audit.timer`（定期 AI 点検の時刻になったかを 5 分ごとに確認。有効にするまで何もしません）、`jt-ipam-backup.timer`（毎日 03:30）の導入
8. nginx モードのときのみ：nginx のサイト設定を導入して再読み込み

### 2.3 最初の管理者を作る

```bash
# ランダムで強いパスワードを生成し、標準入力から読ませます（シェルの履歴に残りません）
ADMIN_PW=$(openssl rand -base64 24)
sudo -u jtipam env $(grep -v '^#' /etc/jt-ipam/backend.env | xargs) \
    /opt/jt-ipam/backend/.venv/bin/python -m app.cli.bootstrap create-admin \
    --username admin --email admin@your.domain --password-stdin <<<"$ADMIN_PW"
echo "ADMIN_PASSWORD=$ADMIN_PW"   # 安全に保管してください
```

ブラウザで `https://<your-fqdn>/` を開いてログインします。自己署名証明書の警告が出るので、
詳細設定 → 続行 を選んでください。

### 2.4 端から端までの動作確認

```bash
# healthz
curl -kfsS https://127.0.0.1/healthz                       # ok が返ること

# ログインとチェーンの検証（A08 の確認）
TOKEN=$(curl -kfsS -X POST https://127.0.0.1/api/v1/auth/login \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"admin\",\"password\":\"$ADMIN_PW\"}" | jq -r .access_token)
curl -kfsS -X POST https://127.0.0.1/api/v1/audit/verify \
    -H "Authorization: Bearer $TOKEN" | jq .
# {"ok": true, "broken_at_id": null, "checked": N} が返るはずです

# systemd のセキュリティスコア
systemd-analyze security jt-ipam-backend | tail -3
# 目標は 3.5 以下。実測値は 1.3 です
```

### 2.5 本物の TLS 証明書に入れ替える（nginx モード）

自己署名で立ち上げた後、本物の証明書が手に入ったら、**上書きして nginx を再読み込みするだけ**です。

```bash
# ベンダーの証明書と鍵を決められたパスへ（権限は root:jtipam のまま）
sudo install -m 0644 -o root -g jtipam /path/to/your-cert.pem  /etc/jt-ipam/tls/server.crt
sudo install -m 0640 -o root -g jtipam /path/to/your-key.pem   /etc/jt-ipam/tls/server.key

# fullchain（中間証明書を含む）と鍵がある場合は fullchain を使ってください
sudo install -m 0644 -o root -g jtipam /path/to/fullchain.pem  /etc/jt-ipam/tls/server.crt

# 検証して再読み込み
sudo nginx -t && sudo systemctl reload nginx

# 新しい証明書が提供されていることを確認
openssl s_client -connect ipam.example.com:443 -servername ipam.example.com </dev/null 2>/dev/null \
    | openssl x509 -noout -issuer -subject -dates
```

その後さらに入れ替えるときも、上の 3 手順を繰り返すだけです。インストールをやり直す必要はありません。

Let's Encrypt を使う場合：

```bash
sudo apt install -y certbot python3-certbot-nginx
# certbot が ssl_certificate を /etc/letsencrypt/live/... に自動で書き換えます
sudo certbot --nginx -d ipam.example.com
```

### 2.6 本物の TLS 証明書に入れ替える（uvicorn 直接 / 自己署名モード）

`BACKEND_TLS_MODE=direct`（または `self-signed`）では、**uvicorn 自身が TLS を提供**し、nginx は
使いません。証明書のパスは nginx モードと同じで、違いは入れ替えた後に **nginx の再読み込みではなく
バックエンドを再起動する**点だけです。

```bash
# 本物の（あるいは自分で管理している）証明書と鍵を決められたパスへ
sudo install -m 0644 -o root -g jtipam /path/to/fullchain.pem /etc/jt-ipam/tls/server.crt
sudo install -m 0640 -o root -g jtipam /path/to/your-key.pem  /etc/jt-ipam/tls/server.key

# 反映のためサービスを再起動します（uvicorn は起動時に --ssl-certfile / --ssl-keyfile を読みます）
sudo systemctl restart jt-ipam-backend

# 新しい証明書が提供されていることを確認します（ポートについては下の「待ち受けポート」を参照）
openssl s_client -connect ipam.example.com:8443 -servername ipam.example.com </dev/null 2>/dev/null \
    | openssl x509 -noout -issuer -subject -dates
```

> 自己署名証明書を自分で作り直すには：`sudo bash /opt/jt-ipam/scripts/generate-self-signed-cert.sh` を
> 実行し、`systemctl restart jt-ipam-backend` します。

#### 待ち受けポートはどこか（**既定は 443 ではなく 8443** です）

`self-signed` / `direct` モードでは uvicorn 自身が TLS を終端し、**既定で 8443 を待ち受けます**。
URL は `https://<your-fqdn>:8443/` です。インストール後に「443 がいつまでも開かない」という場合、
ほとんどがこれであって、インストールの失敗ではありません。実際の値は `/etc/jt-ipam/backend.env` の
`BACKEND_BIND_PORT` にあります。

nginx 無しで 443 を使うには：

```bash
# インストール時に指定するのが確実です（特権ポートに必要な capability も併せて付与されます）
sudo ./scripts/jt-ipam.sh install --tls-mode self-signed --public-fqdn ipam.example.com --bind-port 443

# 既存の環境で変更する場合：
sudo sed -i 's/^BACKEND_BIND_PORT=.*/BACKEND_BIND_PORT=443/' /etc/jt-ipam/backend.env
sudo sed -i 's#^APP_PUBLIC_URL=.*#APP_PUBLIC_URL=https://ipam.example.com#' /etc/jt-ipam/backend.env
sudo install -d /etc/systemd/system/jt-ipam-backend.service.d
sudo tee /etc/systemd/system/jt-ipam-backend.service.d/20-bind-privileged-port.conf >/dev/null <<'EOF'
[Service]
AmbientCapabilities=CAP_NET_RAW CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_RAW CAP_NET_BIND_SERVICE
EOF
sudo systemctl daemon-reload && sudo systemctl restart jt-ipam-backend
```

> このサービスは root で動かないため、1024 未満のポートを bind するには
> `CAP_NET_BIND_SERVICE` が**必要**です。無いとユニットは起動直後に `Permission denied` で
> 落ちます。証明書の問題のように見えますが、ポートの問題です。

#### 自己署名モードで nginx は必要か

**不要です。** `self-signed` / `direct` は、それだけで完結した HTTPS サービスであり、web サーバーは
要りません。それでも nginx を足す理由があるとすれば、サービスに capability を与えずに 443 を使いたい、
同じホストの他サイトと 443 を共有したい、あるいはリバースプロキシの堅牢なヘッダが欲しい（2.7 を参照）
場合です。その場合は、TLS の前段にさらに TLS を置くのではなく、**nginx モードへ切り替えて**ください。

```bash
sudo apt install -y nginx

# 1) バックエンドをループバックの平文 HTTP に戻します（TLS は nginx が終端します）
sudo sed -i 's/^BACKEND_TLS_MODE=.*/BACKEND_TLS_MODE=nginx/' /etc/jt-ipam/backend.env
sudo sed -i 's/^BACKEND_BIND_PORT=.*/BACKEND_BIND_PORT=8000/' /etc/jt-ipam/backend.env
sudo sed -i 's#^APP_PUBLIC_URL=.*#APP_PUBLIC_URL=https://ipam.example.com#' /etc/jt-ipam/backend.env

# 2) nginx のサイト設定を導入します（テンプレートの server_name を自分の FQDN に置き換えます）
sudo install -d -m 0755 /etc/nginx/snippets
sudo install -m 0644 /opt/jt-ipam/deploy/nginx/jt-ipam-proxy.conf /etc/nginx/snippets/jt-ipam-proxy.conf
sudo sed 's/ipam\.example\.com/ipam.example.com/g' /opt/jt-ipam/deploy/nginx/jt-ipam.conf \
    | sudo tee /etc/nginx/sites-available/jt-ipam >/dev/null
sudo ln -sf /etc/nginx/sites-available/jt-ipam /etc/nginx/sites-enabled/jt-ipam
sudo rm -f /etc/nginx/sites-enabled/default

# 3) nginx が読める場所に証明書を置きます。テンプレートは
#    /etc/jt-ipam/tls/server.crt と server.key を読みます（既存の自己署名のままで動きます）

sudo nginx -t && sudo systemctl restart jt-ipam-backend && sudo systemctl reload nginx
```

> 同梱の nginx テンプレートには、コンソール（SSH / SFTP / RDP / VNC / noVNC / BMC）に必要な
> WebSocket のアップグレード用ブロックが既に含まれています。**自分で設定を書く場合は、その
> ブロックを必ずコピーしてください。** アップグレードのヘッダが無いとコンソールは接続できず、
> ブラウザには素の 404 しか表示されません。

### 2.7 本番の標準：堅牢化した nginx リバースプロキシ

インターネットに公開する、あるいは本番の環境では、**同梱の堅牢化した nginx リバースプロキシの
背後で jt-ipam を動かすのが標準です**（`--tls-mode nginx`、参照設定は `deploy/nginx/jt-ipam.conf`）。
nginx が TLS を終端し、バックエンドはループバックに留まり、プロキシが厳しいセキュリティの基準を
適用するため、アプリサーバーが直接さらされることはありません。

- **TLS**：TLS 1.2 / 1.3 のみ、現代的な暗号スイート、OCSP stapling、セッションチケットは無効。
- **HSTS**：`max-age` 2 年 + `includeSubDomains` + `preload`。
- **CSP**：`default-src 'self'`、`script-src 'self'`、`connect-src 'self'`、`frame-src 'self'`、
  `frame-ancestors 'none'`、`base-uri 'self'`、`form-action 'self'`。第三者のスクリプトやフレームの
  オリジンは一切ありません。
- **ヘッダ**：`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy`、
  `Permissions-Policy`（位置情報 / マイク / カメラ / 決済 / USB を無効）、`Cross-Origin-Opener-Policy`、
  `Cross-Origin-Resource-Policy: same-origin`。
- **バナーを漏らさない**：`server_tokens off` とし、上流（uvicorn）の `Server` / `X-Powered-By`
  ヘッダも隠します。バージョンやフレームワークの痕跡は出ません。
- バックエンドは `127.0.0.1` のみを待ち受け、公開の待ち受けは nginx だけです。

> uvicorn を直接インターネットへ公開**しないでください**。`--tls-mode self-signed` / `direct` は
> 社内や検証向けです。

> ### ⚠️ 自前のリバースプロキシを前段に置く場合の必須事項（モード C）
> 上記のセキュリティヘッダを適用するのは、**公開側の境界で TLS を終端する** nginx です。別の
> リバースプロキシ（社内の境界 nginx やロードバランサなど）を前に置く場合、**そのプロキシ自身が
> セキュリティヘッダを設定しなければなりません**。一段余分に経由すると自動では引き継がれないため、
> 公開サイトに CSP / HSTS / Permissions-Policy が*まったく無い*状態になってしまいます。これは
> 任意ではなく、**必須**の作業です。
>
> その境界の機器に、同梱の堅牢化した外部プロキシ用設定を導入してください：
> [`deploy/nginx/jt-ipam-external-proxy.conf`](https://github.com/jasoncheng7115/jt-ipam/blob/main/deploy/nginx/jt-ipam-external-proxy.conf)
> と [`jt-ipam-external-proxy-snippet.conf`](https://github.com/jasoncheng7115/jt-ipam/blob/main/deploy/nginx/jt-ipam-external-proxy-snippet.conf)
> （HSTS preload、絞り込んだ CSP、X-Frame-Options DENY、nosniff、Referrer-Policy、Permissions-Policy、
> COOP と CORP、`server_tokens off` を設定し、上流の複製を `proxy_hide_header` で隠して各ヘッダが
> 一度だけ現れるようにします）。

**利用者が実際にアクセスする公開 URL を通して**（ローカルの機器だけでなく、境界のプロキシ越しに）
ヘッダが出ていることを確認してください。

```bash
curl -skI https://ipam.example.com/ \
  | grep -iE 'strict-transport|content-security|x-frame|x-content|referrer|permissions|cross-origin|^server'
# 次が出ること：HSTS、Content-Security-Policy（frame-src 'self'）、X-Frame-Options、X-Content-Type-Options、
# Referrer-Policy、Permissions-Policy、COOP、CORP：それぞれちょうど 1 回ずつ、そして Server: nginx（バージョン表記なし）。
```

**コンソールと SFTP はエッジプロキシを通ります。** SFTP を含むすべてのコンソールは、
`/api/v1/addresses/<id>/(ssh|sftp|rdp|vnc|novnc|bmc|rustdesk)/ws` の長時間の WebSocket 1 本です。SFTP はこの WebSocket で
ファイルを 256 KB ずつ送るため、nginx の `client_max_body_size` のような HTTP 本文のサイズ上限はファイルサイズを**制限しません**。
大きな転送を妨げるのは、経路上の次のような層です：

- このパスで WebSocket のアップグレードを転送しない（コンソールがまったく接続できません）；
- WebSocket メッセージ 1 件のサイズを制限する（一部の WAF。1 MB 以上を許可してください。RustDesk 互換の Web 接続は映像のキーフレームを丸ごと送るため 16 MB まで許可してください）；
- WebSocket 接続 1 本の転送量や時間を制限する、または 30 秒未満のアイドルで切断する（jt-ipam は 20 秒ごとにキープアライブを送ります）。

各層の設定を読み解く必要はありません。**管理 → システム設定**の「**SFTP の 1 ファイルあたりの転送上限**」を既定値より大きくすると、
ブラウザーから各層（エッジプロキシ、IPAM の nginx、バックエンド）を通して実際に転送テストを行い、どの種類の制限に当たったかを表示します。
測定した速度から、上限サイズのファイルの転送にかかる時間も見積もります。

### 2.8 任意：Docker Compose（これは優先される方式ではありません）

> ⚠️ **Docker Compose は副次的・任意の経路であり、本プロジェクトが優先する主たる導入方式では
> ありません。** サポートされ、推奨される導入は **systemd + apt**（上の各節）です。Compose は
> 手早く評価したい場合や、コンテナ中心の環境向けに使ってください。systemd の経路が最もよく
> テストされています。

ファイルは [`deploy/docker/`](https://github.com/jasoncheng7115/jt-ipam/tree/main/deploy/docker)
にあります。1 つの compose ファイルで `postgres`（pgvector）、`redis`、`backend`（FastAPI / uvicorn）、
`sync`（systemd タイマーの代わりになるバックグラウンドの同期ループ）、`web`（フロントエンドを配信し
`/api` をリバースプロキシする nginx。初回起動時に自己署名の HTTPS 証明書を用意します）が立ち上がります。

前提：**git** と **`docker compose` v2 プラグインを含む Docker Engine** です。公式の
`get.docker.com` スクリプトなら両方入ります。`apt install docker.io` は**使わないでください**。
`docker compose` サブコマンドがありません。Debian 系以外では、各自のパッケージマネージャで git を
導入してください。

```bash
# 前提：先に curl / git、次に Docker Engine（compose プラグインを含む）
sudo apt-get update && sudo apt-get install -y curl git
curl -fsSL https://get.docker.com | sudo sh
docker compose version         # v2.x と表示されるはずです

# 先にリポジトリを clone します。gen-env.sh と docker-compose.yml はその中の deploy/docker/ にあります
git clone https://github.com/jasoncheng7115/jt-ipam.git
cd jt-ipam/deploy/docker
./gen-env.sh                   # ランダムな機密情報を含む .env を作成します（一度だけ）
docker compose up -d --build   # イメージをビルドしてスタックを起動します
# その後 https://localhost を開きます（初回は自己署名証明書なので、警告を承認してください）
```

- **最初の管理者：** `gen-env.sh` がランダムな `admin` のパスワードを生成し（その出力に表示され、
  `.env` の `JT_IPAM_ADMIN_PASSWORD` にモード 0600 で保存されます）、バックエンドが初回起動時に
  管理者を作成します。初回ログイン後に変更してください。自分で決めたい場合は、最初の `up` の前に
  `.env` の `JT_IPAM_ADMIN_PASSWORD` を設定します。空のままにして、後から
  `docker compose exec backend python -m app.cli.bootstrap create-admin --username admin --email admin@example.com --password-stdin`
  で作成することもできます。
- **本物の TLS 証明書：** `deploy/docker/certs/` に `server.crt` / `server.key` を置くと、自己署名を
  上書きできます。
- **ポートやドメイン：** `.env` の `HTTP_PORT` / `HTTPS_PORT` / `JT_IPAM_SERVER_NAME` と、対応する
  `APP_PUBLIC_URL` / `CORS_ORIGINS` を編集してください。

**新しいバージョンへの更新**は 1 コマンドです。

```bash
./update.sh    # git pull → docker compose build → docker compose up -d
```

データベースのマイグレーションは、バックエンドのコンテナが起動したときに**自動で**実行されます
（エントリポイントが `alembic upgrade head` を走らせます）。別途の作業は不要です。

**インターネットに接続できないホスト**（外でビルドして中で動かす）：接続できるホストでイメージを
ビルドして持ち込み、読み込ませます。インストールもアップグレードも同じ流れです。

```bash
# インターネットに接続できるホストで：ソースを取得し、ビルドしてまとめます
git clone https://github.com/jasoncheng7115/jt-ipam.git
cd jt-ipam/deploy/docker
./offline-export.sh                    # → jt-ipam-images-<sha>.tar.gz（アプリと postgres / redis のイメージ）
                                       # （後で上げるときは、先に git pull してから offline-export.sh を再実行します）

# その書庫と jt-ipam のリポジトリ全体を閉域のホストへコピーし、deploy/docker/ で：
./gen-env.sh                           # 初回インストール時のみ（openssl が必要。インターネットは不要）
./offline-import.sh jt-ipam-images-<sha>.tar.gz   # docker load して up -d --no-build --pull never
```

閉域のホストを更新するには、オンラインのホストで `git pull` してから再度 export し、新しい書庫を
コピーして `./offline-import.sh <新しい書庫>` を実行します（`.env` はそのままです）。

> Compose の構成に含まれないもの：平文の Graylog DSV ポート 8088 と、GeoIP / OUI の定期更新です。
> 詳細は [`deploy/docker/README.md`](https://github.com/jasoncheng7115/jt-ipam/blob/main/deploy/docker/README.md)
> を参照してください。

---

## 3. 環境変数

主な設定ファイル：`/etc/jt-ipam/backend.env`（root:jtipam 0640）

| 変数 | 必須 | 備考 |
|---|---|---|
| `SECRET_KEY` | ✓ | JWT の署名用。インストーラが 64 バイトの hex を生成します |
| `ENCRYPTION_KEY` | ✓ | AES-256-GCM の鍵（DNS / SNMP / API の資格情報を暗号化します） |
| `AUDIT_CHAIN_GENESIS` | ✓ | SHA-256 チェーンの起点。**絶対に変更しないでください**（A08） |
| `POSTGRES_*` | ✓ | DB の接続情報 |
| `REDIS_PASSWORD` | ✓ | レート制限とキャッシュ |
| `BACKEND_TLS_MODE` | ✓ | `nginx` または `direct` |
| `APP_PUBLIC_URL` | ✓ | フロントエンドのベース URL |
| `API_PUBLIC_URL` | ✓ | OIDC / SAML のコールバックに使います |
| `CORS_ORIGINS` | ✓ | カンマ区切り |
| `OUTBOUND_ALLOW_CIDRS` | なし | safe_http の SSRF 許可リスト。空欄なら公開インターネットのみ |
| `FDB_CURRENT_MAX_AGE_HOURS` | 24 | FDB のエントリを「現在のもの」とみなす期間。それより古いものは履歴として残りますが、スイッチポートの判定には使いません |
| `FDB_RETENTION_DAYS` | 365 | FDB 履歴の保持日数。0 で無期限。ARP より意図的にずっと長くしています。このテーブルの値打ちは「その機器が以前どのポートにいたか」が分かることだからです |
| `ARP_RETENTION_DAYS` | 30 | ARP エントリの保持日数。0 で削除しません |
| `OIDC_*` | なし | OIDC の SSO を有効にします |
| `SAML_*` | なし | SAML の SSO を有効にします |
| `LDAP_*` | なし | LDAP / AD の認証 |
| `OLLAMA_ENABLED` | なし | AI のセマンティック検索とチャットを有効にします |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | LLM サーバーのアドレス |
| `OLLAMA_CHAT_MODEL` | `gemma4:26b` | チャット用モデル |
| `OLLAMA_EMBEDDING_MODEL` | `granite-embedding:278m` | 埋め込み用モデル。**その次元が `EMBEDDING_DIM` と一致している必要があります** |
| `EMBEDDING_DIM` | `768` | データベースの `vector(N)` 列のサイズ。変更するならマイグレーションも変える必要があります |

全一覧は `app/core/config.py` の Settings クラスを参照してください。

---

## 4. 連携の設定（インストール後）

連携はすべて管理画面の「管理 → 外部システム連携」（例：`/firewall`、`/wazuh`、`/librenms`、`/dns`）から追加します。設定済みの連携はメニューに印が付きます。
追加すると、既定では `jt-ipam-sync.timer` が 5 分ごとに自動で同期します。

> 補足：v0.4.76 以降、OIDC と SAML の SSO も管理 → システム設定 の Web UI から設定できます
> （env を編集する必要はありません）。下の env 変数は既定値として引き続き有効です。

### OPNsense ファイアウォール

1. OPNsense → System → Access → Users でサービス用のユーザーを追加し、API のキーとシークレットを取得します
2. jt-ipam → ファイアウォール → 追加 で `https://opnsense:443`、キー、シークレットを入力します
3. エイリアスの対応付けを追加します（セレクタ JSON の例：`{"type":"section","section_id":"<uuid>"}`）

### pfSense ファイアウォール

pfSense には REST API が標準で無いため、サードパーティの **pfSense-pkg-RESTAPI** パッケージ
（pfrest.org）を導入します。

1. pfSense → System → Package Manager で **pfSense-pkg-RESTAPI** を導入します
2. System → REST API → Settings で認証方式に **API Key** を追加します（既定は BasicAuth のみです）
3. System → REST API → Keys でキーを作成します
4. jt-ipam の 管理 → 外部システム連携 → pfSense → 追加 で `https://pfsense` と API キーを入力します。自己署名証明書の場合は TLS の検証を無効にします
5. DHCP / ARP / エイリアス / ルール / NAT を同期します（ベースパスは `/api/v2`、認証は `X-API-Key`）。pfSense は独自の設定ページを持ちます（OPNsense とは共用しません）

### Wazuh

1. Wazuh manager の API ユーザー（既定は `wazuh-wui`、または自分で作ったもの）を用意します
2. jt-ipam → Wazuh → 追加 で `https://wazuh:55000`、ユーザー、パスワードを入力します
3. 同期を押すと、以後はエージェントが無い IP の一覧ページが表示されます

### LibreNMS

1. LibreNMS → API でトークンを生成します
2. jt-ipam → LibreNMS → 追加 で URL とトークンを入力します

### OIDC（Keycloak / Azure AD / Google）

Web UI（管理 → システム設定 → シングルサインオン（OIDC））で設定するか、
`/etc/jt-ipam/backend.env` に追記します。

```
OIDC_ENABLED=true
OIDC_ISSUER=https://accounts.google.com
OIDC_CLIENT_ID=xxx
OIDC_CLIENT_SECRET=yyy
OIDC_REDIRECT_URI=https://ipam.example.com/api/v1/auth/oidc/callback
OIDC_ADMIN_GROUPS=jt-ipam-admins
```

`systemctl restart jt-ipam-backend` を実行すると、ログイン画面に「OIDC でサインイン」のボタンが出ます。

### SAML（AD FS / Shibboleth）

```
SAML_ENABLED=true
SAML_IDP_METADATA_URL=https://idp.example.com/FederationMetadata.xml
SAML_ADMIN_GROUPS=jt-ipam-admins
```

インターネットに接続できない環境では
`SAML_IDP_METADATA_XML="<EntityDescriptor>...</EntityDescriptor>"` を使います。
再起動後、SP のメタデータを IdP に登録してください：
`curl https://ipam.example.com/api/v1/auth/saml/metadata`。

---

## 5. バックアップと復元

### Recog フィンガープリント DB（任意）

IP 探索は、ホスト自身が返す文字列（SSH バナー、HTTP `Server` ヘッダー、ページタイトル、TLS 証明書、SMB の OS 名）
を [Recog](https://github.com/rapid7/recog)（Rapid7、BSD-2-Clause）と照合し、機器（ベンダーの出荷時証明書や管理画面）や
正確な OS バージョンを識別します。未インストールでも探索は動作しますが、識別できる範囲が狭くなります。

- `jt-ipam.sh install` と `upgrade` が最新リリースをダウンロードします（`api.github.com`、`github.com`、
  `release-assets.githubusercontent.com` への HTTPS 接続が必要）。失敗しても警告のみです。
- `jt-ipam-recog-refresh.timer` が毎週月曜に新しいリリースを確認し（Recog は 1〜6 週ごとにリリース）、新版がある
  ときだけダウンロードします。「バージョン情報」にインストール済みのリリース、最終確認日時、「今すぐ更新を確認」ボタンが
  あり、未インストールまたは 3 週間更新に成功していない場合はシステム診断が警告します。
- オフラインの場合：[リリースページ](https://github.com/rapid7/recog/releases) から `recog-content-<バージョン>.zip` を
  ダウンロードし、`sudo /opt/jt-ipam/scripts/jt-ipam.sh upgrade --recog-zip <ファイル>`（または
  `install --recog-zip <ファイル>`）を実行します。

### 外部 DNS の名前解決（任意）

「公開されているサービス」の FQDN タブで、各名前がインターネット上でどう解決されるかを表示できます。「管理 → システム設定」の
「DNS リゾルバー」カードで、リゾルバー（例：1.1.1.1）を 1 台以上、確認する公開ドメイン、必要に応じて自組織のグローバルアドレス範囲を
設定します。公開ドメイン配下の名前だけを外部に問い合わせます。サーバーからリゾルバーへの UDP と TCP の 53 番ポートへの外向き接続が必要です。
「権威サーバーにも直接問い合わせる」を有効にした場合は、各ドメインの権威サーバーへの接続も必要です。追加のパッケージは不要です。

### LLM / AI（任意）

AI 対話とセマンティック検索は、既定では**自社運用の Ollama** に対して実行されるため、データは
自分たちのネットワークに留まります。インストーラは LLM サーバーを導入しません。通常は GPU を
備えた別のマシンに置きます。

```bash
# LLM サーバー側で（例）
ollama pull gemma4:26b                 # チャット用モデル
ollama pull granite-embedding:278m     # 埋め込み用モデル（768 次元、多言語）
```

続いて **管理 → LLM / AI** で URL と 2 つのモデルを入力し、**次元を確認**を押して一致することを
確かめ、**索引を再構築**を押して既存のレコードにベクトルを付けます。

> **アップグレードした方は必ずお読みください。** アップグレードだけではセマンティック検索は
> 直りません。3 つ手を入れる必要があります。
> 1. 同梱の既定は `granite-embedding:278m` になりましたが、それが適用されるのは**設定ページで
>    埋め込みモデルを一度も保存していない場合だけ**です。データベースの値が優先されるため、
>    おそらく古いモデルのままです。
> 2. 新しいモデルは、ご自身の LLM サーバーで pull する必要があります。
> 3. 既存のレコードにベクトルが付くのは、**索引を再構築**を押したときだけです。
>
> 設定ページは読み込み時に次元を確認し、一致しなければその旨を表示します。まさにこれが以前は
> 欠けていたもので、不一致でもエラーはまったく出ず、「セマンティック検索が何も返さない」と
> いう症状だけが残っていました。

> ⚠️ **埋め込みモデルの出力次元は `EMBEDDING_DIM`（既定は 768）と一致している必要があります**。
> これはデータベースの `vector(N)` 列のサイズです。不一致でも**エラーメッセージは一切出ません**。
> 索引への書き込みがすべて失敗するため、セマンティック検索が何も返さないという症状だけが現れます。
> 埋め込みモデルを変更したら、設定ページの**次元を確認**を押してください。モデルが返した次元と、
> 列が保持する次元を表示します。
>
> もうひとつ、**英語のみの埋め込みモデル（`nomic-embed-text` など）は、ラテン文字以外の異なる
> 説明文を同じベクトルに潰してしまいます**。次元は正しく見え、検索も結果を返しますが、
> 並び順に意味がありません。多言語のモデルを選び、自分たちの説明文をいくつか比べて、
> 異なる結果になることを確認してください。

「OpenAI 互換」を選んで API キー（AES-GCM で暗号化して保存されます）を与えれば、外部の
OpenAI 互換エンドポイント（ChatGPT、vLLM、LM Studio、OpenRouter など）に向けることもできます。
**その場合、サブネット・ホスト名・トポロジーがその提供元へ送信されます。** データを外部に
出せない場合は、自社運用の Ollama のままにしてください。

### 自動バックアップ

インストーラは日次バックアップ（`jt-ipam-backup.timer`）をインストールして有効にし、アップグレード時にはバックアップスクリプトも更新します。
インストーラを使っていない場合は、次のように手で追加できます。

```bash
sudo cp /opt/jt-ipam/scripts/jt-ipam-backup.sh /usr/local/bin/
sudo install -m 0644 /opt/jt-ipam/deploy/systemd/jt-ipam-backup.{service,timer} \
    /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jt-ipam-backup.timer
```

既定では毎日 03:30 に実行し、`pg_dump -Fc` と `/etc/jt-ipam/backend.env`、TLS の証明書を
`/var/backups/jt-ipam/` にまとめ、14 日間保持します。

実行のたびに（成功でも失敗でも）`/var/backups/jt-ipam/last-run` に結果、時刻、最後に成功した時刻、失敗の理由を書きます。「システム診断」画面の「日次バックアップ」と `sudo /opt/jt-ipam/scripts/jt-ipam.sh doctor` はこのファイルを見ます。バックアップが失敗したり 48 時間成功していなかったりすると診断が赤くなり、管理者にシステムアラートが送られます。よくある原因は、jt-ipam のロールが所有していないテーブル（手で残したコピーなど）があり、pg_dump が読めずに中止することです。診断画面に実行するコマンドが表示されます。

### バックアップの暗号化

バックアップのディレクトリにはデータベースのダンプ**と** `backend.env`（ダンプ内の機密を復号する鍵）が入っています。**管理 → システム設定 → 日次バックアップの暗号化** でバックアップのパスフレーズ（12 文字以上）を設定すると、毎日のバックアップを 1 つのファイル `/var/backups/jt-ipam/jt-ipam-<日付>.jtbak` にまとめて暗号化し（scrypt で導出した鍵、1 MiB 単位の AES-256-GCM。切り詰め・入れ替え・改ざんがあると復号できません）、平文のディレクトリは削除します。`last-run` に `encrypted=1` が記録され、パスフレーズが未設定の間はシステム診断と `doctor` が警告します。

パスフレーズはこのホスト以外（パスワードマネージャーなど）に保管してください。忘れるとバックアップを復号できません。変更後も、以前のバックアップには当時のパスフレーズが必要です。復号ツールは Python 3 と `cryptography`（`apt install python3-cryptography`）だけで動くため、jt-ipam を入れ直す前の新しいマシンでも使えます：

```bash
python3 /opt/jt-ipam/backend/app/services/backup_crypt.py decrypt \
    /var/backups/jt-ipam/jt-ipam-2026-10-09.jtbak --out /root/jt-ipam-restore
# → /root/jt-ipam-restore/2026-10-09/（ダンプ、backend.env、tls.tar.gz、uploads.tar.gz）
```

### 遠隔地へのバックアップ

`/var/backups/jt-ipam/` を NAS / S3 / 別のマシンへ rsync します。

```bash
# 例：毎日 04:00 に NAS へ送る
0 4 * * * rsync -a /var/backups/jt-ipam/ jtipam@nas.local:/backups/jt-ipam/
```

### 復元

```bash
# 0. サービスを停止します
sudo systemctl stop jt-ipam-backend jt-ipam-sync.timer jt-ipam-ai-audit.timer

# 0b. 暗号化したバックアップ（.jtbak）は先に復号します（バックアップのパスフレーズを聞かれます）
python3 /opt/jt-ipam/backend/app/services/backup_crypt.py decrypt \
    /var/backups/jt-ipam/jt-ipam-2026-05-10.jtbak --out /root/jt-ipam-restore
# 以下の /var/backups/jt-ipam/2026-05-10/ は /root/jt-ipam-restore/2026-05-10/ に読み替えます

# 1. 空のデータベースを作り直します
sudo -u postgres dropdb jt_ipam
sudo -u postgres createdb -O jt_ipam jt_ipam
sudo -u postgres psql -d jt_ipam -c '
    CREATE EXTENSION IF NOT EXISTS pgcrypto;
    CREATE EXTENSION IF NOT EXISTS citext;
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE EXTENSION IF NOT EXISTS btree_gist;
    CREATE EXTENSION IF NOT EXISTS vector;
'

# 2. ダンプを戻します（注意：DNS や API の資格情報など機密の項目を復号するには、同じ ENCRYPTION_KEY が必要です）
sudo -u postgres pg_restore -d jt_ipam \
    /var/backups/jt-ipam/2026-05-10/jt-ipam-2026-05-10.dump

# 3. 設定ファイルを戻します（残っていれば）
sudo cp /var/backups/jt-ipam/2026-05-10/backend.env /etc/jt-ipam/

# 4. 監査テーブルをログインできない所有者に戻してから（アプリのロールは読み取りと追加のみ）起動します
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh harden-audit
sudo systemctl start jt-ipam-backend jt-ipam-sync.timer jt-ipam-ai-audit.timer

# 5. チェーンを検証します（改ざんされた行があれば直ちに分かります）
curl -X POST https://ipam.example.com/api/v1/audit/verify \
    -H "Authorization: Bearer <admin token>"
```

> バックアップファイルには機密情報が含まれます（DB には暗号化された API の資格情報、env には
> SECRET_KEY と ENCRYPTION_KEY）。バックアップの暗号化パスフレーズを設定し（上記）、`0600` の権限で保管し、暗号化された経路で転送してください
> （ssh 越しの rsync、S3 のサーバー側暗号化など）。

---

## 6. アップグレード

統合スクリプトによる一括アップグレード（git pull → バックアップ → pip → 書き込み停止 → 基準の一覧 → alembic → 1 件ずつの照合 → ビルド → 再起動）：

```bash
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade
# すでに pull 済みで git pull を省く場合：  sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade --no-pull
```

同等の手動手順（必要に応じて個別に実行します）：

```bash
cd /opt/jt-ipam
git pull
sudo systemctl stop jt-ipam-backend
sudo -u jtipam /opt/jt-ipam/backend/.venv/bin/pip install -e backend
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; . /etc/jt-ipam/backend.env; set +a; .venv/bin/python -m app.cli.upgrade_verify snapshot --out /var/lib/jt-ipam/upgrade-checks/manual/before.json'
sudo -u jtipam /opt/jt-ipam/backend/.venv/bin/alembic -c backend/alembic.ini upgrade head
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; . /etc/jt-ipam/backend.env; set +a; .venv/bin/python -m app.cli.upgrade_verify compare --before /var/lib/jt-ipam/upgrade-checks/manual/before.json --report /var/lib/jt-ipam/upgrade-checks/manual/report.json'
cd frontend && sudo -u jtipam pnpm install --frozen-lockfile && sudo -u jtipam pnpm run build
sudo systemctl start jt-ipam-backend
```

**データの照合。**インプレースのアップグレードで既存データを失ってはいけません。マイグレーションの前にスクリプトは書き込みを止め（バックエンドと jt-ipam のタイマー）、ロケーション、ラック、機器の配置、電源、配線、機器と IP の関連付け、権限をすべて記録し、マイグレーション後に**1 件ずつ・項目ごとに**照合します（件数だけではなく、フロアプランのファイルはハッシュで比較）。以前からあったデータの問題は別に表示し、新しく生じたものだけを失敗とします。欠けたり変わったりしていればスクリプトは停止し、取得したバックアップの復元方法を表示して、**バックエンドとタイマーを停止したまま**にします。一覧とレポート（`/var/lib/jt-ipam/upgrade-checks/<時刻>/`）にはサイトのデータが含まれ、サービスアカウントだけが読めます（0600）。

0.4.x からの移行には、専用の手順書 [UPGRADE_FROM_0.4_ja.md](UPGRADE_FROM_0.4_ja.md) があります。

---

## 7. 監視と通知

### ジャーナル

```bash
journalctl -u jt-ipam-backend -f          # バックエンド
journalctl -u jt-ipam-sync -n 200          # 定期同期
journalctl -u jt-ipam-ai-audit -n 200      # 定期 AI 点検
journalctl -u jt-ipam-backup -n 50         # バックアップ
```

### ヘルスチェック

`https://<your-fqdn>/api/v1/healthz` が 200 を返せば正常です。

### SIEM / Slack への転送

バックエンドは Webhook の購読に対応しています。管理 → Webhook で Webhook の URL を
追加してください。`BACKEND_*` の env に Graylog の GELF エンドポイントを設定して、監査ログを
すべて転送することもできます。

---

## 8. アンインストール

統合スクリプトによるアンインストール（既定ではサービスの停止と、systemd のユニット / タイマー、
nginx のサイト設定の削除だけを行い、**DB・設定・ソースは残します**）：

```bash
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh uninstall
# DB・設定・アップロード・システムユーザーも消す場合（確認を求めます。--yes で省略できます）：
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh uninstall --purge
```

> `uninstall` が `/opt/jt-ipam` のソースを削除することはありません。

同等の手動手順：

```bash
sudo systemctl disable --now jt-ipam-backend jt-ipam-sync.timer jt-ipam-ai-audit.timer jt-ipam-backup.timer
sudo rm /etc/systemd/system/jt-ipam-{backend,sync,backup}.{service,timer}
sudo rm -rf /etc/jt-ipam /var/log/jt-ipam /var/lib/jt-ipam
sudo -u postgres dropdb jt_ipam
sudo -u postgres dropuser jt_ipam
sudo userdel -r jtipam
```

バックアップ（`/var/backups/jt-ipam/`）を残すかどうかは、ご自身で判断してください。

---

## 9. よくある質問

**Q：`--tls-mode self-signed` で入れました。nginx や apache は別途必要ですか。**
A：**不要です。** このモードでは uvicorn 自身が TLS を終端するため、web サーバー無しで完結した
HTTPS サービスになっています。URL は `https://<your-fqdn>:8443/` です（**既定は 443 ではなく
8443**）。確認するには：

```bash
sudo systemctl status jt-ipam-backend        # active (running) であること
sudo ss -ltnp | grep 8443                    # uvicorn が待ち受けていること
curl -kI https://127.0.0.1:8443/             # HTTP/1.1 200 が返ること
```

443 へ移す、あるいは後から nginx を足す場合は、2.6 の「待ち受けポート」と「自己署名モードで
nginx は必要か」を参照してください。

**Q：インストールが `extension "vector" is not available` で止まりました。postgresql-16-pgvector は入っているのに。**
A：このホストにはほぼ確実に**既存の PostgreSQL クラスタ**があります（SonarQube や GitLab などが
入れたもの）。jt-ipam は `127.0.0.1:5432`、つまりその既存クラスタに接続するため、pgvector は
**そのクラスタの**メジャーバージョン向けに入れる必要があります。別のバージョン向けに入れても
効果はありません。

```bash
sudo -u postgres psql -tAc 'SHOW server_version_num'   # 例：180004 → メジャー 18
sudo apt install -y postgresql-18-pgvector
sudo -u postgres psql -d jt_ipam -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

> v0.5.161 以降、インストーラは稼働中クラスタのバージョンを検出して対応する pgvector を入れ、
> サーバーパッケージを重ねて入れること（＝二つ目のクラスタができてしまう）もなくなりました。
> 拡張の作成に失敗した場合も、後から alembic のトレースバックとして現れるのではなく、その場で
> 読める形で止まります。

**Q：インストールが `/usr/local/bin/pnpm: No such file or directory` で止まりました。**
A：pnpm の導入に失敗し、以前のスクリプトは npm のエラーを捨てていました。導入してからやり直して
ください（インストーラは再実行しても安全で、完了済みの手順は飛ばされます）。

```bash
sudo npm install -g pnpm@9      # または：curl -fsSL https://get.pnpm.io/install.sh | sh -
sudo ./scripts/jt-ipam.sh install --tls-mode self-signed --public-fqdn ipam.example.com
```

> v0.5.161 以降、スクリプトは npm の出力を残し、pnpm の取得を 3 通り試し、先へ進む前に
> `pnpm --version` が動くことを確認します。

**Q：インストーラは「Done」と言うのに、何も動いていません。**
A：v0.5.161 以降、インストーラは「Done」と言う前に自己点検を行い（env ファイル、フロントエンドの
dist、サービスの状態、待ち受けポート、nginx モードなら nginx）、足りないものを列挙します。
それ以前のバージョンでは手作業で確認してください。

```bash
ls -l /etc/jt-ipam/backend.env                 # 設定はあるか
ls -l /opt/jt-ipam/frontend/dist/index.html    # フロントエンドはビルドされたか
sudo systemctl status jt-ipam-backend
sudo journalctl -u jt-ipam-backend -n 50 --no-pager
```

**Q：バックエンドが起動せず、ジャーナルに「ENCRYPTION_KEY: invalid format」と出ます。**
A：`ENCRYPTION_KEY` は 32 バイトを base64 にしたもの（44 文字で末尾が `=`）である必要があります。
インストーラが生成しますが、手で設定する場合は
`python -c 'import base64,os; print(base64.b64encode(os.urandom(32)).decode())'` を使ってください。

**Q：バックエンドが OOM で落ちます。**
A：argon2id の既定は 64 MiB / 並列度 4 です。メモリが厳しい場合（2 GB 未満）は
`ARGON2_MEMORY_COST_KIB=32768` に下げてください。

**Q：nginx が 502 を返します。**
A：バックエンドは既定で `127.0.0.1:8000` を bind します。`systemctl status jt-ipam-backend` が
active であること、nginx のサイト設定の upstream も `127.0.0.1:8000` を指していることを確認してください。

**Q：pgvector が見つかりません。**
A：Ubuntu 22.04 には同梱されていません。インストーラが PGDG リポジトリと
`postgresql-16-pgvector` を自動で追加します。手動で直すには
`sudo apt install postgresql-16-pgvector` の後、`CREATE EXTENSION vector;` を実行します。

**Q：`/api/v1/audit/verify` が `{"ok": false}` を返し、チェーンが切れています。**
A：既知の過去の不具合です。v0.3.0 より前は、nginx が自分の `$request_id`（ハイフン無しの 32 桁の
hex）をバックエンドへ渡し、バックエンドがそれを監査の正規形に書き込んでいましたが、PG は
ハイフン付きの UUID として読み戻すため検証が一致しませんでした。0.3.1 以降はミドルウェアが
正規化します（`app/core/middleware.py` の `RequestIDMiddleware`）。すでにチェーンが壊れていて、
まだ本番のデータが入っていないのであれば：

```bash
# チェーンを初期化します（audit_logs をすべて消します。本番ではない環境でのみ行ってください）
sudo -u postgres psql -d jt_ipam -c "TRUNCATE audit_logs RESTART IDENTITY;"
sudo systemctl restart jt-ipam-backend
```

**Q：インストーラが `nginx: ssl_stapling ignored, issuer certificate not found` と警告します。**
A：自己署名証明書には発行者チェーンが無いため OCSP stapling を使えません。無害な警告で、
正式な証明書や Let's Encrypt に切り替えれば出なくなります。

**Q：統合テストには `JTIPAM_TEST_DATABASE_URL` が必要ですが、本番にも必要ですか。**
A：不要です。本番に必要なのは `backend.env` の `POSTGRES_*` だけです。
`JTIPAM_TEST_DATABASE_URL` は pytest 用の別 DB を指すもので（本番のデータを汚さないため）、
本番とは関係ありません。

**Q：インストール後、IP で接続すると jt-ipam ではなく「Welcome to nginx」が出ます。**
A：既知の問題です（0.3.1 より前）。apt が nginx の `default` サイトを有効にするため、IP での
アクセスがそちらに吸われます。0.3.1 以降の `install-debian.sh` は、(1) `/etc/nginx/sites-enabled/default`
を自動で削除し、(2) jt-ipam のサイトに `listen ... default_server` を追加するので、IP でも FQDN でも
どのホスト名でも jt-ipam に届きます。古い環境では手動で直してください。

```bash
sudo rm /etc/nginx/sites-enabled/default
sudo sed -i 's|listen 80;|listen 80 default_server;|; s|listen 443 ssl http2;|listen 443 ssl http2 default_server;|' /etc/nginx/sites-available/jt-ipam
sudo nginx -t && sudo systemctl reload nginx
```

**Q：既定の管理者の資格情報は何ですか。忘れた場合はどうすればよいですか。**
A：**既定の資格情報はありません。** インストール後に手動で作成する必要があり（§2.3）、
パスワードは `openssl rand` でランダムに生成され、**作成時に一度だけ表示されます**。控えて
おいてください。忘れた場合や変更したい場合は次のようにします。

```bash
# 方法 A：別の管理者を作る（元の管理者がまだ使えるなら、その管理者が UI の /users からパスワードを変更できます）
ADMIN_PW=$(openssl rand -base64 24)
sudo -u jtipam env $(grep -v '^#' /etc/jt-ipam/backend.env | xargs) \
    /opt/jt-ipam/backend/.venv/bin/python -m app.cli.bootstrap create-admin \
    --username admin2 --email admin2@your.domain --password-stdin <<<"$ADMIN_PW"
echo "$ADMIN_PW"

# 方法 B：元の管理者が締め出された/失われた場合、DB を直接編集してロックを解除しパスワードを再設定します
sudo -u jtipam env $(grep -v '^#' /etc/jt-ipam/backend.env | xargs) \
    /opt/jt-ipam/backend/.venv/bin/python -c '
import asyncio, sys
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.user import User
from sqlalchemy import select

async def main(username, new_pwd):
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.username == username))).scalar_one()
        u.password_hash = hash_password(new_pwd)
        u.locked_until = None
        u.failed_login_count = 0
        u.is_active = True
        u.is_admin = True
        await s.commit()
        print(f"reset {u.username}")

asyncio.run(main(sys.argv[1], sys.argv[2]))
' admin "MyNewPassword2026!"
```

**Q：`Node.js install failed or too old (need >= 22)` と出る、またはアップグレードで「Node.js 22 could not be installed」の警告枠が出ます。**
A：Node.js 22 LTS はフロントエンドのビルドにだけ使います（Node 20 は 2026-04-30 でサポート終了）。`jt-ipam.sh`
はインストール時もアップグレード時も NodeSource（`setup_22.x`）から導入するので、通常は何もする必要はありません。
導入できない場合（deb.nodesource.com に届かない、プロキシ、apt の競合）、`install` は止まります。`upgrade` は既存の
Node 20 以降でビルドしてそのまま完了し、最後にその警告枠を出します。このとき pnpm も
`Unsupported engine: wanted: {"node":">=22"}` を出し、`doctor` は古い Node を指摘し続けます。手動で導入してから
同じコマンドをやり直してください。

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash -
sudo apt install -y nodejs
node -v      # v22.x
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade      # または install
```

apt がディストリビューション同梱パッケージとの競合（`trying to overwrite ... libnode-dev`）を報告する場合は、先に
`sudo apt-get purge -y nodejs libnode-dev` で削除してください。nvm を使っている場合、`sudo` を実行したユーザーの
nvm に Node 22 があればそれをそのまま使い、古い nvm の Node は使いません。

## guacd コンソールエンジン（RDP/VNC の既定）

guacd は [Apache Guacamole](https://guacamole.apache.org/) のサーバー側で、**RDP と VNC コンソールの既定エンジン**で、
**必須コンポーネント**でもあります（0.6.49 から。既存の環境もアップグレードで切り替わります）。
SSH も「管理 → システム設定」で切り替えられます。`install` が入れ、入れられなければそこで止まります。`upgrade` は未導入なら入れます。
guacd が止まっている間、RDP と VNC は任意の組み込みエンジン（aardwolf、入っていれば）に戻り、`doctor`、
「管理 → システムチェック」、「バージョン情報 → 必須コンポーネント」が問題を表示します。

別途インストールが必要な理由：Debian は guacd の提供をやめ、Ubuntu にはリモートコード実行の脆弱性がある 1.3.0 しかありません。
そのため jt-ipam はサポート中の OS バージョンごとにビルドを用意しています（Debian 12/13、Ubuntu 22.04/24.04/26.04）。
OS 自身のライブラリにリンクするので、FreeRDP や libvncclient などのセキュリティ更新は引き続き apt から届きます。

```
sudo /opt/jt-ipam/scripts/jt-ipam.sh install                     # guacd も入ります
sudo /opt/jt-ipam/scripts/jt-ipam.sh upgrade                     # 未導入なら入れ、導入済みなら更新
sudo /opt/jt-ipam/scripts/jt-ipam.sh upgrade --guacd-tarball ./jt-ipam-guacd-...-ubuntu24.04-amd64.tar.gz
                                                                 # オフライン：同じ場所に .deps ファイルが必要
```

- `jt-ipam-guacd` systemd サービスとして動き、**127.0.0.1:4822 だけで待ち受けます**。guacd 自体には認証がないため、
  他のインターフェースで開いてはいけません。サービスは権限のない動的ユーザーで動きます。
- `upgrade` のたびに jt-ipam の版に対応するビルドへ更新されます。ダウンロードしたファイルは `scripts/guacd/SHA256SUMS` と照合します。
- 資格情報はサーバーが guacd に渡し、ブラウザーには届きません。SSH のホスト鍵も初回確認後に固定され、guacd はその鍵と一致しなければなりません。
- guacd 経由の SSH では端末をサーバー側で描画します。コピーと貼り付けは Ctrl+Shift+C/Ctrl+Shift+V（Mac は ⌘C/⌘V）。
  日本語・中国語の入力メソッドも使えます。
- `jt-ipam.sh doctor` と「管理 → システムチェック」で、guacd が動いているか、guacd を選んだプロトコルがサポートされているかを確認できます。

ライセンス：guacd は Apache-2.0 です。各パッケージには LICENSE、NOTICE、正確なソースとビルドスクリプトを記した SOURCE ファイルが含まれます。
これらは jt-ipam がビルドしたもので、Apache の公式リリースではありません。
