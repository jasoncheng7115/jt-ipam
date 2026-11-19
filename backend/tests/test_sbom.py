"""SBOM（CycloneDX 1.5）：站台自己產生（系統設定下載、`jt-ipam.sh sbom`）與發版時 CI 附在 GitHub Release（2026-11-16）。

後端的相依套件只寫下限，客戶安裝時 pip 會裝當下的版本 → 站台上的 SBOM 一定要從實際安裝的環境讀，
不可以從 pyproject 抄。前端版本由 pnpm-lock.yaml 鎖定，只列打包進畫面的（dependencies 的遞移閉包），
開發工具（devDependencies）不算出貨內容。
"""
from __future__ import annotations

import json
import re
import uuid
from importlib.metadata import version as dist_version
from pathlib import Path

import pytest
from app.services import sbom

ROOT = Path(__file__).resolve().parents[2]

LOCK = """lockfileVersion: '9.0'
importers:
  .:
    dependencies:
      vue:
        specifier: ^3.5.0
        version: 3.5.43(typescript@5.9.3)
      '@scope/lib':
        specifier: 1.0.0
        version: 1.0.0
    devDependencies:
      vitest:
        specifier: ^4.0.0
        version: 4.1.12
packages:
  vue@3.5.43:
    resolution: {integrity: sha512-AAAA}
  '@vue/shared@3.5.43':
    resolution: {integrity: sha512-BBBB}
  '@scope/lib@1.0.0':
    resolution: {integrity: sha512-CCCC}
  vitest@4.1.12:
    resolution: {integrity: sha512-DDDD}
  tinyspy@4.0.0:
    resolution: {integrity: sha512-EEEE}
snapshots:
  vue@3.5.43(typescript@5.9.3):
    dependencies:
      '@vue/shared': 3.5.43
  '@vue/shared@3.5.43': {}
  '@scope/lib@1.0.0': {}
  vitest@4.1.12:
    dependencies:
      tinyspy: 4.0.0
  tinyspy@4.0.0: {}
"""


def test_frontend_components_are_the_shipped_closure(tmp_path: Path) -> None:
    (tmp_path / "pnpm-lock.yaml").write_text(LOCK)
    nm = tmp_path / "node_modules" / ".pnpm" / "vue@3.5.43_typescript@5.9.3" / "node_modules" / "vue"
    nm.mkdir(parents=True)
    (nm / "package.json").write_text(json.dumps({"name": "vue", "version": "3.5.43", "license": "MIT"}))
    comps, deps = sbom.frontend_components(tmp_path)
    by = {c["name"]: c for c in comps}
    assert set(by) == {"vue", "@vue/shared", "@scope/lib"}, "開發工具與它們的相依不是出貨內容"
    assert by["vue"]["purl"] == "pkg:npm/vue@3.5.43"
    assert by["@scope/lib"]["purl"] == "pkg:npm/%40scope/lib@1.0.0"
    assert by["vue"]["licenses"] == [{"expression": "MIT"}]
    # pnpm 的 integrity（base64）轉成 CycloneDX 要的十六進位
    assert by["vue"]["hashes"] == [{"alg": "SHA-512", "content": "000000"}]
    assert {"ref": "pkg:npm/vue@3.5.43", "dependsOn": ["pkg:npm/%40vue/shared@3.5.43"]} in deps


def test_python_components_come_from_the_installed_environment() -> None:
    comps, deps = sbom.python_components()
    by = {c["name"]: c for c in comps}
    # 版本是實際安裝的，不是 pyproject 的下限
    assert by["fastapi"]["version"] == dist_version("fastapi")
    assert by["fastapi"]["purl"] == f"pkg:pypi/fastapi@{dist_version('fastapi')}"
    assert by["fastapi"]["licenses"]
    assert "jt-ipam-backend" not in by, "自己是 metadata.component，不是相依套件"
    # 相依關係：sqlalchemy[asyncio] 帶進 greenlet；fastapi 依賴 starlette
    edges = {d["ref"]: set(d["dependsOn"]) for d in deps}
    assert any(r.startswith("pkg:pypi/starlette@") for r in edges[by["fastapi"]["bom-ref"]])


def test_whole_bom_is_consistent() -> None:
    bom = sbom.build(frontend_dir=ROOT / "frontend")
    assert bom["bomFormat"] == "CycloneDX"
    assert bom["specVersion"] == "1.5"
    assert re.fullmatch(r"urn:uuid:[0-9a-f-]{36}", bom["serialNumber"])
    uuid.UUID(bom["serialNumber"][9:])
    from app.version import __version__
    meta = bom["metadata"]["component"]
    assert (meta["name"], meta["version"]) == ("jt-ipam", __version__)
    refs = [c["bom-ref"] for c in bom["components"]]
    assert len(refs) == len(set(refs)), "bom-ref 不可重複"
    known = set(refs) | {meta["bom-ref"]}
    for d in bom["dependencies"]:
        assert d["ref"] in known
        assert set(d["dependsOn"]) <= known, d
    names = {c["name"].lower() for c in bom["components"]}
    assert {"fastapi", "sqlalchemy", "vue", "naive-ui"} <= names
    assert "vitest" not in names
    assert "@playwright/test" not in names


def test_passes_the_cyclonedx_1_5_schema() -> None:
    """用官方函式庫對 CycloneDX 1.5 的 JSON schema 做嚴格驗證（dev 相依；沒裝就略過）。"""
    pytest.importorskip("jsonschema")
    validation = pytest.importorskip("cyclonedx.validation.json")
    from cyclonedx.schema import SchemaVersion
    err = validation.JsonStrictValidator(SchemaVersion.V1_5).validate_str(
        json.dumps(sbom.build(frontend_dir=ROOT / "frontend")))
    assert err is None, err


@pytest.mark.anyio
async def test_download_is_admin_only(client, auth_headers, db_session) -> None:
    r = await client.get("/api/v1/system/sbom", headers=auth_headers)
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert ".sbom.cdx.json" in r.headers["content-disposition"]
    assert r.json()["bomFormat"] == "CycloneDX"

    from app.core.security import hash_password
    from app.models.user import User
    from app.services.auth import issue_access_token
    u = User(username=f"ro-{uuid.uuid4().hex[:6]}", email="", password_hash=hash_password("TestPassword2026!"),
             auth_provider="local", is_active=True, is_admin=False)
    db_session.add(u)
    await db_session.commit()
    r = await client.get("/api/v1/system/sbom", headers={"Authorization": f"Bearer {issue_access_token(u)}"})
    assert r.status_code == 403
