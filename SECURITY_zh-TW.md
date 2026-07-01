# 安全政策

安全是 jt-ipam 的 day-one 需求。每個模組與每個 pull request 都會依據
[`docs/SECURITY.md`](docs/SECURITY.md) 所記錄的 **OWASP Top 10:2025** 清單逐項檢核。

> 英文版見 [SECURITY.md](SECURITY.md)。

## 支援版本

最新發布的 `0.5.x` 系列會收到安全修補。較舊的系列不再維護 — 請升級。

## 回報漏洞

**請勿為安全問題開公開 issue。**

請透過以下任一管道私下回報:

- GitHub:開立[私密安全通報（private security advisory）](https://github.com/jasoncheng7115/jt-ipam/security/advisories/new)
- Email:儲存庫個人檔案上列出的維護者信箱

請附上受影響版本、重現步驟與影響範圍。我們會在數個工作日內回覆,並與您協調修補與
揭露的時程。

## 重點範圍

- 強制 TLS(nginx 反向代理或 uvicorn 自簽)。
- 機密(DNS 憑證、SNMP、API token)在應用層加密;密碼使用 argon2id;以 TOTP 做 MFA。
- 所有對外整合 URL 走 SSRF 白名單(封鎖 metadata / link-local)。
- 稽核事件以 SHA-256 串鏈。

## 已接受風險（已記錄，附補償控制）

以下是已知的掃描器發現，但移除它們的代價不成比例（例如要換掉整個 UI 框架），在此記錄以供稽核透明。
每一項在實務上都已被周邊控制中和。

### CSP `style-src 'unsafe-inline'`（ZAP 規則 10055 評為 *Medium*）

- **為何無法移除：** 前端是 Vue 3 + Naive UI。Vue 的 `v-show`、動態 `:style` 綁定、Naive UI 浮動元件的定位都會
  產生 inline `style="…"` **屬性**。CSP **無法**用 nonce 或 hash 放行 inline style *屬性*（nonce／hash 只對
  `<style>` 區塊有效），而一旦加了 nonce，瀏覽器就會**忽略** `'unsafe-inline'` → 全站 `v-show`／`:style` 會壞。
  這是 CSP 層級的先天限制，所有主流 Vue／React 元件庫（MUI、Angular Material…）皆然。
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
（`deploy/zap-baseline.conf`）—— 即零新增 High／Medium／Low。

若不確定某件事是否屬安全問題,請私下回報,我們會協助分級判斷。
