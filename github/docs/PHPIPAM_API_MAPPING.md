# phpIPAM v1.7 API 相容層對照表

> English: [PHPIPAM_API_MAPPING.en.md](PHPIPAM_API_MAPPING.en.md)

> 目標：phpIPAM 老腳本零改動可遷移；路徑首碼 `/api/phpipam/<app_id>/`
>
> Phase 1 完成「Sections / Subnets / Addresses / VLANs / VRFs / Devices / Tools / User」這 8 大類。其餘類別（Folder/Locations/L2Domains/Circuits/Tags）Phase 2 補齊。
>
> 安全考量：
> - 認證 token 採 phpIPAM 機制（`POST /user/`）；token 在 jt-ipam 內部存為 `api_tokens` 並加密 hash（A02 / A07）
> - 所有 endpoint 套 RBAC 檢查；Section/Subnet 權限與現代 API 共用（A01）
> - 輸出格式包裝為 phpIPAM 風格 `{success, data, message, time}`，但內部仍走 Pydantic 驗證（A03）

---

## 一、認證

| phpIPAM endpoint | jt-ipam endpoint | 說明 |
|---|---|---|
| `POST /api/<app_id>/user/` | `POST /api/phpipam/<app_id>/user/` | 取得 token |
| `DELETE /api/<app_id>/user/` | `DELETE /api/phpipam/<app_id>/user/` | 撤銷 token |
| `PATCH /api/<app_id>/user/` | `PATCH /api/phpipam/<app_id>/user/` | 延長 token |
| `GET /api/<app_id>/user/` | `GET /api/phpipam/<app_id>/user/` | 取得 token 資訊 |

**安全增強**：token TTL 必填，最長 1 年；建立時不寫 plaintext。

---

## 二、Sections（區段）

| phpIPAM | jt-ipam |
|---|---|
| `GET    /sections/` | `GET /api/phpipam/<app>/sections/` |
| `GET    /sections/{id}/` | 同 |
| `GET    /sections/{name}/` | 同（by name） |
| `GET    /sections/{id}/subnets/` | 同 |
| `POST   /sections/` | 同 |
| `PATCH  /sections/{id}/` | 同 |
| `DELETE /sections/{id}/` | 同 |

**對應內部模型**：`Section`。Phase 1 必做。

---

## 三、Subnets（子網）

| phpIPAM | jt-ipam |
|---|---|
| `GET /subnets/{id}/` | 同 |
| `GET /subnets/cidr/{subnet}/` | 同 |
| `GET /subnets/{id}/usage/` | 計算使用率 |
| `GET /subnets/{id}/first_free/` | 第一個空閒 IP |
| `GET /subnets/{id}/slaves/` | 子層 subnets |
| `GET /subnets/{id}/slaves_recursive/` | 遞迴 |
| `GET /subnets/{id}/addresses/` | 該網段所有 IP |
| `GET /subnets/{id}/first_subnet/{mask}/` | 第一個空閒子網 |
| `GET /subnets/{id}/all_subnets/{mask}/` | 列出可用切割 |
| `GET /subnets/{id}/search/{ip}/` | 在網段內搜尋 |
| `POST /subnets/` | 建立 |
| `POST /subnets/{id}/first_subnet/{mask}/` | 自動切下一段 |
| `POST /subnets/{id}/resize/` | 縮放 |
| `POST /subnets/{id}/split/` | 切割 |
| `PATCH /subnets/{id}/` | 更新 |
| `PATCH /subnets/{id}/resize/` | 同上 |
| `PATCH /subnets/{id}/split/` | 同上 |
| `DELETE /subnets/{id}/` | 刪除 |
| `DELETE /subnets/{id}/truncate/` | 清空 IP |
| `DELETE /subnets/{id}/permissions/` | 重設權限 |

**對應內部模型**：`Subnet`。

---

## 四、Addresses（IP）

| phpIPAM | jt-ipam |
|---|---|
| `GET /addresses/{id}/` | 同 |
| `GET /addresses/{ip}/{subnetId}/` | by IP + subnet |
| `GET /addresses/search/{ip}/` | 全域搜尋 |
| `GET /addresses/search_hostname/{hostname}/` | by hostname |
| `GET /addresses/first_free/{subnetId}/` | 第一空閒 |
| `GET /addresses/custom_fields/` | 自訂欄位定義 |
| `GET /addresses/tags/` | 狀態標籤 |
| `GET /addresses/tags/{id}/addresses/` | 某狀態的所有 IP |
| `POST /addresses/` | 建立 |
| `POST /addresses/first_free/` | 配發第一個空閒 |
| `PATCH /addresses/{id}/` | 更新 |
| `DELETE /addresses/{id}/` | 刪除 |
| `DELETE /addresses/{ip}/{subnetId}/` | by IP+subnet 刪除 |

**對應內部模型**：`IPAddress`。注意 phpIPAM 把「狀態」叫 tag。

---

## 五、VLANs

| phpIPAM | jt-ipam |
|---|---|
| `GET /vlans/` | 同 |
| `GET /vlans/{id}/` | 同 |
| `GET /vlans/{id}/subnets/` | 同 |
| `GET /vlans/{id}/subnets/{section}/` | 同 |
| `GET /vlans/search/{number}/` | by number |
| `POST /vlans/` | 同 |
| `PATCH /vlans/{id}/` | 同 |
| `DELETE /vlans/{id}/` | 同 |

**對應內部模型**：`VLAN` + `VLANDomain`。

---

## 六、VRFs

| phpIPAM | jt-ipam |
|---|---|
| `GET /vrf/` | 同 |
| `GET /vrf/{id}/` | 同 |
| `GET /vrf/{id}/subnets/` | 同 |
| `POST /vrf/` | 同 |
| `PATCH /vrf/{id}/` | 同 |
| `DELETE /vrf/{id}/` | 同 |

**對應內部模型**：`VRF`。

---

## 七、Devices

| phpIPAM | jt-ipam |
|---|---|
| `GET /devices/` | 同 |
| `GET /devices/{id}/` | 同 |
| `GET /devices/{id}/subnets/` | 同 |
| `GET /devices/{id}/addresses/` | 同 |
| `POST /devices/` | 同 |
| `PATCH /devices/{id}/` | 同 |
| `DELETE /devices/{id}/` | 同 |

**對應內部模型**：`Device`。

### Device Types

| phpIPAM | jt-ipam |
|---|---|
| `GET /tools/device_types/` | 同 |
| `POST /tools/device_types/` | 同 |
| `PATCH /tools/device_types/{id}/` | 同 |
| `DELETE /tools/device_types/{id}/` | 同 |

---

## 八、Tools（雜項）

涵蓋：tags、nameservers、scan_agents、locations、racks、custom_fields、users、groups。

| phpIPAM endpoint | 對應內部 |
|---|---|
| `/tools/tags/` | IPAddress.state 列舉 |
| `/tools/locations/` | Location |
| `/tools/racks/` | Rack |
| `/tools/nameservers/` | DNSServer (Phase 2) |
| `/tools/scanagents/` | ScanAgent (Phase 1) |
| `/tools/custom_fields/{object}/` | CustomFieldDefinition |
| `/tools/users/` | User |
| `/tools/groups/` | Group |

---

## 九、回應格式

phpIPAM 標準格式：

```json
{
  "code": 200,
  "success": true,
  "data": [...] or {...},
  "message": "...",
  "time": 0.012
}
```

jt-ipam 在 `/api/phpipam/` 路徑下統一包裝；`/api/v1/` 走標準 OpenAPI 格式。

---

## 十、不相容 / 需要注意之處

| phpIPAM 行為 | jt-ipam 處理 |
|---|---|
| 數字 ID（auto increment） | jt-ipam 內部 UUID；對外 phpIPAM 端會額外發給一組 numeric `legacy_id`（單調遞增 bigint），維持相容性 |
| 部分欄位用 `0/1` 表示 bool | 包裝層自動轉換 |
| `null` 與 `""` 混用 | jt-ipam 內部嚴格區分，輸出時統一為 phpIPAM 風格 |
| 子網 cidr 用 `subnet/mask` 兩欄 | jt-ipam 用單一 `cidr`；輸出時拆出 |
| 權限欄 `permissions` 是 base64 序列化字串 | jt-ipam 解析後重新轉換 |

---

## 十一、Phase 切割

| Phase | 涵蓋 |
|---|---|
| **Phase 1** | user, sections, subnets, addresses, vlans, vrf, devices, tools(基本) |
| **Phase 2** | folder, l2domains, circuits, locations, prefixes, scanagents 完整 |
| **Phase 3** | 進階：自訂欄位多型別、batch endpoints |
