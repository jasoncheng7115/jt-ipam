"""Docker 映像的後端以非特權帳號執行（內部原始碼掃描：Dockerfile Default User Privilege）。

以前 backend 階段沒有 USER，uvicorn、同步排程都是 root：程式一旦被打穿，攻擊者在容器內就是 root。
systemd 安裝本來就用專用帳號 jtipam；Docker 版照做。
"""
from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "deploy" / "docker" / "Dockerfile"


def _stage(name: str) -> list[str]:
    lines = DOCKERFILE.read_text().splitlines()
    out: list[str] = []
    inside = False
    for ln in lines:
        m = re.match(r"^FROM\s+\S+\s+AS\s+(\S+)", ln, re.I)
        if m:
            inside = m.group(1) == name
            continue
        if inside:
            out.append(ln)
    return out


def test_backend_stage_drops_root_before_running() -> None:
    stage = _stage("backend")
    users = [i for i, ln in enumerate(stage) if re.match(r"^USER\s+", ln)]
    assert users, "backend 階段沒有 USER，容器會以 root 執行"
    last_user = stage[users[-1]].split()[1]
    assert last_user not in ("root", "0", "0:0")
    # USER 要在 ENTRYPOINT / CMD 之前，且之後不再有 RUN（否則會以為已降權但其實還在 root 下做事）
    entry = next(i for i, ln in enumerate(stage) if ln.startswith(("ENTRYPOINT", "CMD")))
    assert users[-1] < entry
    assert not any(ln.startswith("RUN") for ln in stage[users[-1]:])


def test_backend_stage_prepares_the_writable_data_dir() -> None:
    # 執行期會寫 /var/lib/jt-ipam（上傳、稽核錨定、GeoIP）；非 root 帳號要有這個目錄的擁有權
    text = "\n".join(_stage("backend"))
    assert re.search(r"install\s+-d\b[^\n]*-o\s+jtipam[^\n]*/var/lib/jt-ipam", text)


def test_frontend_stage_installs_from_the_lockfile_with_pnpm() -> None:
    # 前端是 pnpm 管理（package.json 的 packageManager、pnpm.overrides）：用 npm install 會讀不懂
    # 而直接失敗，且不照 pnpm-lock.yaml 鎖定版本。照 scripts/jt-ipam.sh 的作法用 pnpm --frozen-lockfile。
    text = "\n".join(_stage("frontend"))
    assert "pnpm-lock.yaml" in text
    assert re.search(r"pnpm\s+install\s+--frozen-lockfile", text)
    assert not re.search(r"\bnpm\s+install\b", text)
