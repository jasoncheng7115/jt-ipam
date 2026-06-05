# jt-ipam Plugin Development Guide

> 繁體中文版：[PLUGINS.md](PLUGINS.md)

Third-party packages can extend jt-ipam via `entry_points`, without forking the main repo.

---

## Minimal working plugin

`my_plugin/__init__.py`:

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
    description="Example plugin",
    on_load=_on_load,
)
```

`pyproject.toml`:

```toml
[project]
name = "my-jt-ipam-plugin"
version = "1.0.0"

[project.entry-points."jt_ipam.plugins"]
my_plugin = "my_plugin:plugin"
```

Once installed, jt-ipam loads it on startup:

```bash
cd /opt/jt-ipam/backend
.venv/bin/pip install /path/to/my-jt-ipam-plugin
sudo systemctl restart jt-ipam-backend
```

Verify:

```bash
curl -fsS https://ipam.example.com/api/v1/plugins -H "Authorization: Bearer ..."
# {"count": 1, "plugins": [{"name": "my-plugin", "version": "1.0.0", ...}]}
```

---

## What you can register

`on_load(app)` receives the FastAPI app object, where you can:

- `app.include_router(...)` to add REST endpoints
- `app.middleware(...)` to add middleware
- Start background tasks (`asyncio.create_task`)
- Register GraphQL types (compose after reading `app.state.graphql_schema`)

`on_shutdown(app)` is the matching cleanup hook.

---

## Security notes (OWASP)

- **A01**: plugin endpoints **must** use `Depends(get_current_user)` or
  `Depends(require_admin)`; do not bypass RBAC.
- **A02**: plugin-owned secrets go through `app.models.encrypted_secret.EncryptedSecret`,
  not environment variables or plain DB columns.
- **A09**: write operations should call `app.core.audit.append_audit()` to append to the audit chain.
- **A10**: outbound connections use `app.core.safe_http.safe_request`, never `httpx.get()` directly.
- **A06**: pin dependency versions in the plugin's pyproject; releases follow the SBOM flow.

---

## Limitations (current Phase 4)

- No managed database migrations: a plugin's own tables must be managed with its own
  alembic (recommended: a separate alembic env, kept apart from jt-ipam's main alembic).
- No automatic OpenAPI schema merge: plugin endpoints appear in `/openapi.json`,
  but the GraphQL union schema must be handled manually.
- No hot plugin uninstall; disabling currently means `pip uninstall && systemctl restart`.

These limitations will be improved in Phase 4.x.
