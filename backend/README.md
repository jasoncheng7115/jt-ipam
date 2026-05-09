# jt-ipam backend

FastAPI + SQLAlchemy 2.0（async）+ PostgreSQL 16 + Redis 7。

## 開發

```bash
# 依賴（建議使用 uv）
uv sync --extra dev

# 跑遷移
uv run alembic upgrade head

# 啟動 dev server
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 測試 / lint / 型別 / 安全
uv run pytest
uv run ruff check .
uv run mypy app
uv run bandit -r app
uv run pip-audit
```

## 結構

```
backend/
├── app/
│   ├── main.py              # FastAPI app + middleware
│   ├── core/
│   │   ├── config.py        # pydantic-settings
│   │   ├── db.py            # async engine / session
│   │   ├── security.py      # argon2 / JWT / encryption
│   │   ├── audit.py         # SHA-256 異動鏈
│   │   ├── safe_http.py     # SSRF allowlist (A10)
│   │   ├── rate_limit.py    # 限流
│   │   └── middleware.py    # security headers / request id / logging
│   ├── models/              # SQLAlchemy 2.0 ORM
│   ├── schemas/             # Pydantic v2 I/O
│   ├── api/
│   │   ├── v1/              # 現代 REST API
│   │   └── phpipam/         # phpIPAM v1.7 相容層
│   ├── services/            # 業務邏輯
│   └── utils/
├── alembic/                 # 遷移
└── tests/
```
