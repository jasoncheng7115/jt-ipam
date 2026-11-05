# 安全政策

安全是 jt-ipam 的 day-one 需求：每個模組與每個 pull request 都會依 **OWASP Top 10:2025**
逐項檢核，而且每一版發布前都必須通過 OWASP ZAP 基準掃描且零新增發現。

> 英文版見 [SECURITY.md](SECURITY.md)；日文版見 [SECURITY_ja.md](SECURITY_ja.md)。

## 支援版本

只有最新版會收到安全修補，回報前請先升級。發版頻繁，修正會以新版發布，不會回頭補進舊版。
自 `v0.6.8` 起每個版本都會打 tag（`vX.Y.Z`）。

## 回報漏洞

**請勿開公開 issue。**

- GitHub：到 [Security 分頁](https://github.com/jasoncheng7115/jt-ipam/security)按「Report a vulnerability」（私下通報）
- Email：service@jason.tools

請附上受影響版本、重現步驟與影響範圍。我們會在 3 個工作天內回覆；確認的問題以 7 天內釋出修正版、
並發布 GitHub 安全公告（Security Advisory）為目標。

## 安全公告

已發布的公告：https://github.com/jasoncheng7115/jt-ipam/security/advisories

每個已在新版修正、實際可被利用的問題都會發一則公告，在修正版釋出之後公開。純加強與不影響 jt-ipam 的
相依套件升級只寫在版本說明。

## 重點範圍

- 強制 TLS（nginx 反向代理或 uvicorn 自簽）。
- 機密（DNS 憑證、SNMP、API token）在應用層加密，每一筆以 AES-GCM 附加資料綁定用途；密碼使用 argon2id。
- 雙因素驗證（TOTP）可要求管理員或所有人使用，附單次使用的復原碼；同一組驗證碼不能用第二次。
- 伺服器端工作階段：更新用的權杖只放在 HttpOnly、SameSite=Strict 的 cookie，每次換發並偵測重複使用；
  登出、停用、管理員撤銷立即生效，開著的主控台 30 秒內結束。
- 對外連線：HTTP 在連線當下檢查，其他協定在連線前檢查（封鎖雲端中繼資料、link-local 與保留位址；
  主控台另外不連本機）。
- 稽核事件以 SHA-256 串鏈、資料庫層只能新增、外部錨定，匯出與檢視機密也留記錄。
- 每日備份可用密碼加密。
- 對應 ISO/IEC 27001:2022 與 ISO/IEC 42001:2023 的哪些控制項、每一項怎麼驗證，見
  [docs/COMPLIANCE_zh-TW.md](docs/COMPLIANCE_zh-TW.md)。

## 已接受風險（已記錄，附補償控制）

以下是已知的掃描器發現，但移除它們的代價不成比例（例如要換掉整個 UI 框架），在此記錄以供稽核透明。
每一項在實務上都已被周邊控制中和。

### CSP `style-src 'unsafe-inline'`（ZAP 規則 10055 評為 *Medium*）

- **為何無法移除：** 前端是 Vue 3 + Naive UI。Vue 的 `v-show`、動態 `:style` 綁定、Naive UI 浮動元件的定位都會
  產生 inline `style="…"` **屬性**。CSP **無法**用 nonce 或 hash 放行 inline style *屬性*（nonce/hash 只對
  `<style>` 區塊有效），而一旦加了 nonce，瀏覽器就會**忽略** `'unsafe-inline'` → 全站 `v-show`/`:style` 會壞。
  這是 CSP 層級的先天限制，所有主流 Vue/React 元件庫（MUI、Angular Material…）皆然。
- **為何實際風險低（補償控制）：**
  - `script-src 'self'`（script 無 `unsafe-inline`）→ 注入的 CSS **無法執行 JavaScript**。
  - `img-src 'self' data: blob:`、`connect-src 'self'` → 注入的 CSS **無法外洩資料**（常見的 attribute-selector
    + 外部圖片外洩手法被擋；沒有任何外部來源可達）。
  - 輸出由 Vue 自動跳脫，根本沒有注入點。
  - Naive UI 的 config provider 已開 `inline-theme-disabled`，把主題樣式從 inline 屬性移進 `<style>` 區塊，
    縮小 inline 面積。
- **淨效果：** 頂多是樣式被竄改的外觀破壞，**絕不會執行程式或竊取資料**。已記於 `deploy/zap-baseline.conf`。

## 發版關卡

每次發版前都會跑 ZAP 掃描（HTTP 及經對外反向代理），要求**沒有上述基準以外的任何發現**
（`deploy/zap-baseline.conf`），即零新增 High/Medium/Low。

CI 也在每次推送時跑 ZAP baseline 掃描，對象是真的 nginx 設定與正式建置的前端；基準以外的任何警示都會讓 CI 失敗。

若不確定某件事是否屬安全問題,請私下回報,我們會協助分級判斷。
