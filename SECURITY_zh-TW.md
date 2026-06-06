# 安全政策

安全是 jt-ipam 的 day-one 需求。每個模組與每個 pull request 都會依據
[`docs/SECURITY.md`](docs/SECURITY.md) 所記錄的 **OWASP Top 10:2025** 清單逐項檢核。

> 英文版見 [SECURITY.md](SECURITY.md)。

## 支援版本

最新發布的 `0.4.x` 系列會收到安全修補。較舊的系列不再維護 — 請升級。

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

若不確定某件事是否屬安全問題,請私下回報,我們會協助分級判斷。
