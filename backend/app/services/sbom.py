"""軟體物料清單（SBOM，CycloneDX 1.5 JSON）。

兩個用途，同一份程式：
- 站台自己產生：系統設定下載（`GET /system/sbom`）、`jt-ipam.sh sbom`。後端相依只寫下限，安裝時 pip 會裝
  當下的版本，所以**從實際安裝的 Python 環境讀**，不從 pyproject 抄；前端讀 pnpm-lock.yaml（版本鎖定）。
- 發版時 CI 在推送版本 tag 時產生，附在 GitHub Release（.github/workflows/sbom.yml）。

前端只列打包進畫面的套件：pnpm-lock 裡 `dependencies` 的遞移閉包；`devDependencies`（建置、測試工具）不是出貨內容。

    python -m app.services.sbom [前端目錄] > jt-ipam.sbom.cdx.json
"""
from __future__ import annotations

import base64
import json
import re
import sys
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

APP_DIST = "jt-ipam-backend"
ROOT = Path(__file__).resolve().parents[3]

#: trove 分類 → SPDX（只列常見的；其餘用名稱）
_CLASSIFIER_SPDX = {
    "MIT License": "MIT", "BSD License": "BSD-3-Clause", "Apache Software License": "Apache-2.0",
    "ISC License (ISCL)": "ISC", "Python Software Foundation License": "PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "GNU Lesser General Public License v3 or later (LGPLv3+)": "LGPL-3.0-or-later",
    "GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
    "GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    "The Unlicense (Unlicense)": "Unlicense",
}


def _norm(name: str) -> str:
    """PEP 503 的套件名稱正規化（purl 的 pypi 名稱也這樣寫）。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def _py_license(md: Any) -> list[dict[str, Any]]:
    expr = (md.get("License-Expression") or "").strip()
    if expr:
        return [{"expression": expr}]
    ids = [_CLASSIFIER_SPDX[c.split(" :: ")[-1]] for c in md.get_all("Classifier") or []
           if c.startswith("License ::") and c.split(" :: ")[-1] in _CLASSIFIER_SPDX]
    if ids:
        return [{"license": {"id": i}} for i in dict.fromkeys(ids)]
    text = (md.get("License") or "").strip()
    if text and "\n" not in text and len(text) <= 80:     # 有些套件把整份授權全文塞在這欄
        return [{"license": {"name": text}}]
    return []


def python_components() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """實際安裝的 Python 套件，以及從 jt-ipam 往下走的相依關係（含 extras，例如 fastapi[standard]）。"""
    from packaging.requirements import InvalidRequirement, Requirement

    dists = {}
    for d in metadata.distributions():
        name = d.metadata.get("Name")
        if name and _norm(name) not in dists:
            dists[_norm(name)] = d

    def ref(key: str) -> str:
        return f"pkg:pypi/{key}@{dists[key].version}"

    edges: dict[str, set[str]] = {}
    seen: set[tuple[str, frozenset[str]]] = set()
    queue: list[tuple[str, frozenset[str]]] = [(_norm(APP_DIST), frozenset())]
    while queue:
        key, extras = queue.pop()
        if (key, extras) in seen or key not in dists:
            continue
        seen.add((key, extras))
        for raw in dists[key].requires or []:
            try:
                req = Requirement(raw)
            except InvalidRequirement:          # 不合規的 metadata 不擋整份清單
                continue
            if req.marker is not None and not any(
                    req.marker.evaluate({"extra": e}) for e in (extras or {""})):
                continue
            dep = _norm(req.name)
            if dep not in dists:
                continue
            edges.setdefault(key, set()).add(dep)
            queue.append((dep, frozenset(req.extras)))

    comps = []
    for key, d in sorted(dists.items()):
        if key == _norm(APP_DIST):
            continue
        c: dict[str, Any] = {"type": "library", "name": d.metadata["Name"], "version": d.version,
                             "purl": ref(key), "bom-ref": ref(key)}
        lic = _py_license(d.metadata)
        if lic:
            c["licenses"] = lic
        comps.append(c)
    deps = [{"ref": ref(k) if k != _norm(APP_DIST) else "", "dependsOn": sorted(ref(v) for v in vs)}
            for k, vs in sorted(edges.items())]
    return comps, deps


def _npm_purl(name: str, version: str) -> str:
    return f"pkg:npm/{name.replace('@', '%40', 1) if name.startswith('@') else name}@{version}"


def _base(version: str) -> str:
    """`3.5.43(typescript@5.9.3)` → `3.5.43`（pnpm 在版本後面附上 peer 相依）。"""
    return version.split("(", 1)[0]


def _npm_license(frontend: Path, name: str, version: str) -> list[dict[str, Any]]:
    store = frontend / "node_modules" / ".pnpm"
    for pj in store.glob(f"{name.replace('/', '+')}@{version}*/node_modules/{name}/package.json"):
        try:
            lic = json.loads(pj.read_text(encoding="utf-8")).get("license")
        except (OSError, ValueError):
            continue
        if isinstance(lic, str) and lic:
            # npm 的 license 欄是自由文字（也有 "SEE LICENSE IN …"），放 id 的話 schema 嚴格驗證會整份不過
            return [{"expression": lic}]
    return []


def _sri_hex(integrity: str | None) -> list[dict[str, str]]:
    if not integrity or not integrity.startswith("sha512-"):
        return []
    try:
        return [{"alg": "SHA-512", "content": base64.b64decode(integrity[7:]).hex()}]
    except ValueError:
        return []


def frontend_components(frontend: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """打包進畫面的 npm 套件（pnpm-lock 的 dependencies 遞移閉包）。"""
    import yaml

    lock = yaml.safe_load((frontend / "pnpm-lock.yaml").read_text(encoding="utf-8"))
    importer = (lock.get("importers") or {}).get(".") or {}
    snapshots = lock.get("snapshots") or {}
    packages = lock.get("packages") or {}

    def usable(v: Any) -> bool:
        return isinstance(v, str) and not v.startswith(("link:", "file:"))

    todo = [(n, str(v["version"])) for n, v in (importer.get("dependencies") or {}).items()
            if usable(v.get("version"))]
    found: dict[tuple[str, str], set[tuple[str, str]]] = {}
    roots = {(n, _base(v)) for n, v in todo}
    while todo:
        name, ver = todo.pop()
        node = (name, _base(ver))
        if node in found:
            continue
        snap = snapshots.get(f"{name}@{ver}") or snapshots.get(f"{name}@{_base(ver)}") or {}
        kids = [(n, str(v)) for n, v in {**(snap.get("dependencies") or {}),
                                         **(snap.get("optionalDependencies") or {})}.items() if usable(v)]
        found[node] = {(n, _base(v)) for n, v in kids}
        todo.extend(kids)

    comps = []
    for name, ver in sorted(found):
        purl = _npm_purl(name, ver)
        c: dict[str, Any] = {"type": "library", "name": name, "version": ver, "purl": purl, "bom-ref": purl}
        lic = _npm_license(frontend, name, ver)
        if lic:
            c["licenses"] = lic
        h = _sri_hex(((packages.get(f"{name}@{ver}") or {}).get("resolution") or {}).get("integrity"))
        if h:
            c["hashes"] = h
        c["properties"] = [{"name": "jt-ipam:location", "value": "frontend (web UI bundle)"}]
        comps.append(c)
    deps = [{"ref": _npm_purl(*n), "dependsOn": sorted(_npm_purl(*k) for k in kids)}
            for n, kids in sorted(found.items()) if kids]
    deps.append({"ref": "", "dependsOn": sorted(_npm_purl(*r) for r in roots)})
    return comps, deps


def _merge_root(deps: Iterable[dict[str, Any]], root: str) -> list[dict[str, Any]]:
    """兩邊各自以 "" 代表 jt-ipam 本身，合併成一筆。"""
    top: set[str] = set()
    out = []
    for d in deps:
        if d["ref"] == "":
            top |= set(d["dependsOn"])
        else:
            out.append(d)
    return [{"ref": root, "dependsOn": sorted(top)}, *out]


def build(*, frontend_dir: Path | None = None) -> dict[str, Any]:
    from app.version import __version__

    frontend = frontend_dir or ROOT / "frontend"
    py, py_deps = python_components()
    js, js_deps = frontend_components(frontend) if (frontend / "pnpm-lock.yaml").exists() else ([], [])
    root = f"jt-ipam@{__version__}"
    return {
        "bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{uuid.uuid4()}", "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "tools": {"components": [{"type": "application", "name": "jt-ipam sbom", "version": __version__}]},
            "component": {"type": "application", "name": "jt-ipam", "version": __version__,
                          "licenses": [{"license": {"id": "AGPL-3.0-or-later"}}], "bom-ref": root,
                          "purl": f"pkg:github/jasoncheng7115/jt-ipam@v{__version__}"},
            "properties": [
                {"name": "jt-ipam:python", "value": f"{sys.version_info.major}.{sys.version_info.minor}"},
                {"name": "jt-ipam:scope", "value": "Python packages installed in the backend environment and the "
                                                   "npm packages bundled into the web UI (pnpm-lock.yaml)"},
            ],
        },
        "components": py + js,
        "dependencies": _merge_root([*py_deps, *js_deps], root),
    }


def filename() -> str:
    from app.version import __version__
    return f"jt-ipam-{__version__}.sbom.cdx.json"


if __name__ == "__main__":
    json.dump(build(frontend_dir=Path(sys.argv[1]) if len(sys.argv) > 1 else None), sys.stdout, indent=1)
    sys.stdout.write("\n")
