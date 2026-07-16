"""全系統匯出／匯入（跨機搬移）。

把整台 jt-ipam 的設定與資料序列化成一份版本化、密碼保護的匯出檔，
可在另一台 jt-ipam 匯入（保留 UUID → 外鍵與機密 AAD 皆自動成立）。

模組：
- crypto    封套加解密（scrypt KDF + AES-256-GCM，用使用者密碼保護整包）
- registry  以 ORM metadata 推導的「相依序」匯出表清單 + 分類
- secrets   機密欄位編解碼（匯出解密→明文；匯入以目標金鑰重加密）
- exporter  組出 inner payload
- importer  套用 inner payload（merge / replace，含 dry-run）
"""
