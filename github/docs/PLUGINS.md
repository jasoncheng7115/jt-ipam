# jt-ipam Plugin 開發指南

> English: [PLUGINS.en.md](PLUGINS.en.md)

第三方套件可透過 `entry_points` 擴充 jt-ipam，無需 fork 主 repo。

---

## 最小可運作 plugin

`my_plugin/__init__.py`：

```python
from fastapi import APIRouter, Depends

from app.api.v1.dependencies import CurrentUser
from app.plugins import JtIpamPlugin

router = APIRouter(prefix="/api/v1/my-plugin", tags=["my-plugin"])


@router.get("/hello")
async def hello(_user: CurrentUser) -> dict:
    return {"hello": "from my plugin"}


def _on_load(app):
    app.include_router(router)


plugin = JtIpamPlugin(
    name="my-plugin",
    version="1.0.0",
    description="範例 plugin",
    on_load=_on_load,
)
```

`pyproject.toml`：

```toml
[project]
name = "my-jt-ipam-plugin"
version = "1.0.0"

[project.entry-points."jt_ipam.plugins"]
my_plugin = "my_plugin:plugin"
```

安裝後 jt-ipam 啟動即會載入：

```bash
cd /opt/jt-ipam/backend
.venv/bin/pip install /path/to/my-jt-ipam-plugin
sudo systemctl restart jt-ipam-backend
```

驗證：

```bash
curl -fsS https://ipam.example.com/api/v1/plugins -H "Authorization: Bearer ..."
# {"count": 1, "plugins": [{"name": "my-plugin", "version": "1.0.0", ...}]}
```

---

## 可註冊內容

`on_load(app)` 收到 FastAPI app 物件，可：

- `app.include_router(...)` 加 REST endpoint
- `app.middleware(...)` 加 middleware
- 啟動 background task（`asyncio.create_task`）
- 註冊 GraphQL types（取 `app.state.graphql_schema` 後組合）

`on_shutdown(app)` 對應的 cleanup hook。

---

## 安全須知（OWASP）

- **A01**：plugin endpoint **務必**用 `Depends(get_current_user)` 或
  `Depends(require_admin)`；不要繞過 RBAC。
- **A02**：plugin 自有 secret 走 `app.models.encrypted_secret.EncryptedSecret`，
  不要用環境變數或 plain DB 欄位。
- **A09**：寫入操作呼叫 `app.core.audit.append_audit()` 寫稽核鏈。
- **A10**：對外連線用 `app.core.safe_http.safe_request`，不直接 `httpx.get()`。
- **A06**：plugin pyproject 鎖定依賴版本；release 走 SBOM 流程。

---

## 限制（目前 Phase 4）

- 沒有資料庫 migration 託管：plugin 自有資料表需自行管理 alembic（建議放
  自己的 alembic env，與 jt-ipam 主 alembic 分流）。
- 沒有自動 OpenAPI schema 合併：plugin endpoint 會出現在 `/openapi.json` 內，
  但 GraphQL union schema 需要手動處理。
- 沒有 plugin uninstall 熱拔除；目前停用 = `pip uninstall && systemctl restart`。

這些限制將在 Phase 4.x 改善。
