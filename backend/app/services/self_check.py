"""系統自我診斷 —— 後端看得到的那一半（`scripts/jt-ipam.sh doctor` 是另一半）。

由來（2026-09-05 客戶回報）：儀表板顯示 55 台裝置，點進裝置清單卻是
「Internal Server Error」＋空白清單。原因是**資料庫結構落後於程式**（升級時 alembic
沒跑完）：儀表板那個數字是 `count(*)`，不需要讀任何欄位；清單是 `select(Device)`，
會列出每一個欄位 —— 少一欄就整支 500。

真正的問題不是那個 500，而是**系統其實查得出原因卻沒有講**。啟動時就知道結構落後了，
卻讓使用者一頁一頁踩 500 再自己去猜。所以這裡做兩件事：

1. `schema_state()` 在啟動時跑一次，落後就用 error 記下來，並讓管理員在畫面上看得到。
2. `run_checks()` 給管理頁的「系統診斷」用：把後端查得到的狀況一次列出來，
   每一項都附「該怎麼修」，而不是只說「有問題」。

**刻意不呼叫 `scripts/jt-ipam.sh`**：後端是以非特權帳號跑的，去 shell out 一個需要 root
的腳本只會得到一份不可靠的結果，還多開一個執行外部指令的面。系統層的檢查（systemd、
nginx、備份檔、掃描代理）仍然要用 CLI 版的 doctor —— 這一頁會明講哪些是它看不到的。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger("self_check")

Status = Literal["ok", "warn", "bad"]


@dataclass
class Check:
    key: str
    title: str
    status: Status
    detail: str = ""
    #: 該怎麼修（指令或動作）。**每個非 ok 的項目都要有** —— 只說「壞了」等於沒說。
    fix: str = ""
    #: i18n：有 key 就由前端用 `t(key, params)` 依當前語言渲染，沒有才退回上面的字串。
    #: 這一頁的讀者是登入中的使用者，語言是他自己的偏好 —— 後端沒有「當前語言」可言，
    #: 所以句子不能在這裡組好。與通知、錯誤訊息同一套作法。
    title_key: str = ""
    detail_key: str = ""
    fix_key: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    generated_at: str = ""

    @property
    def bad(self) -> int:
        return sum(1 for c in self.checks if c.status == "bad")

    @property
    def warn(self) -> int:
        return sum(1 for c in self.checks if c.status == "warn")

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "bad": self.bad, "warn": self.warn,
            "ok": sum(1 for c in self.checks if c.status == "ok"),
            "checks": [vars(c) for c in self.checks],
        }

    def as_text(self) -> str:
        """可下載的純文字報告（貼進工單用）。"""
        icon = {"ok": "[ OK ]", "warn": "[WARN]", "bad": "[FAIL]"}
        lines = [f"jt-ipam self-check — {self.generated_at}", ""]
        for c in self.checks:
            lines.append(f"{icon[c.status]} {c.title}")
            if c.detail:
                lines.append(f"        {c.detail}")
            if c.fix and c.status != "ok":
                lines.append(f"        → {c.fix}")
        lines += ["", f"{self.bad} failed, {self.warn} warnings, "
                      f"{sum(1 for c in self.checks if c.status == 'ok')} ok", ""]
        lines.append("Note: system-level checks (systemd units, nginx, backups, scan agent)")
        lines.append("are not visible from the backend. Run on the server:")
        lines.append("  sudo bash /opt/jt-ipam/scripts/jt-ipam.sh doctor")
        return "\n".join(lines)


# ─────────────────── 資料庫結構 ───────────────────
def _alembic_heads() -> set[str]:
    """程式這一份原始碼的 migration head（不碰資料庫）。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parent.parent.parent      # backend/
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    return set(ScriptDirectory.from_config(cfg).get_heads())


async def schema_state(session: AsyncSession) -> dict[str, Any]:
    """資料庫結構有沒有跟上程式。

    回 `{"current": ..., "head": ..., "behind": bool, "error": str|None}`。
    讀不出來時 `behind` 是 **False**（不確定就不要嚇人），但 `error` 會帶原因。
    """
    out: dict[str, Any] = {"current": None, "head": None, "behind": False, "error": None}
    try:
        out["head"] = ", ".join(sorted(_alembic_heads())) or None
    except Exception as exc:
        out["error"] = f"cannot read migration scripts: {exc}"
        return out
    try:
        rows = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
        out["current"] = ", ".join(sorted(str(r) for r in rows)) or None
    except Exception as exc:
        out["error"] = f"cannot read alembic_version: {exc}"
        return out
    out["behind"] = bool(out["head"]) and out["current"] != out["head"]
    return out


async def warn_if_schema_behind(session: AsyncSession) -> bool:
    """啟動時呼叫：結構落後就用 error 記一筆很明顯的日誌。回傳「是否落後」。"""
    state = await schema_state(session)
    if state["behind"]:
        log.error(
            "database_schema_behind",
            current=state["current"], head=state["head"],
            fix="sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade",
            note="pages that read full rows will fail with 500 until this is fixed",
        )
    elif state["error"]:
        log.warning("schema_check_failed", error=state["error"])
    return bool(state["behind"])


def _frontend_check() -> Check:
    """前端建置是否存在、版本是否與後端相同。

    拆成同步函式而不是寫在 `run_checks()` 裡：那是個 async 函式，在裡面直接用
    pathlib 會被 lint 擋（在事件迴圈裡做阻塞 I/O）。這幾個檔案很小，同步讀沒問題。
    """
    import json

    from app.version import __version__ as backend_ver
    try:
        dist = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"
        if not (dist / "index.html").exists():
            return Check("frontend", "前端建置", "bad", "找不到 dist/index.html",
                         "sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade",
        title_key="doctor.c_frontend", detail_key="doctor.c_frontend_missing", fix_key="doctor.f_run_upgrade")
        vfile = dist / "version.json"
        if not vfile.exists():
            return Check("frontend", "前端建置", "warn", "沒有 dist/version.json", "重新建置前端",
        title_key="doctor.c_frontend", detail_key="doctor.c_frontend_no_version", fix_key="doctor.f_rebuild_frontend")
        fev = json.loads(vfile.read_text(encoding="utf-8")).get("version")
        if fev != backend_ver:
            return Check("frontend", "前端與後端版本不一致", "warn",
                         f"前端 {fev}、後端 {backend_ver}",
                         "cd /opt/jt-ipam/frontend && npm run build（或重跑 upgrade）",
        title_key="doctor.c_frontend_mismatch", detail_key="doctor.c_frontend_mismatch_d", fix_key="doctor.f_rebuild_or_upgrade", params={"frontend": fev, "backend": backend_ver})
        return Check("frontend", "前端建置", "ok", f"與後端相同（{fev}）",
        title_key="doctor.c_frontend", detail_key="doctor.c_frontend_ok", params={"version": fev})
    except Exception as exc:
        return Check("frontend", "前端建置", "warn", str(exc)[:200],
        title_key="doctor.c_frontend", detail_key="doctor.d_raw", params={"detail": str(exc)[:200]})


# ─────────────────── 各項檢查 ───────────────────
async def run_checks(session: AsyncSession) -> Report:
    """後端查得到的所有檢查。任何一項自己壞掉都不可以讓整份報告失敗。"""
    rep = Report(generated_at=datetime.now(UTC).isoformat(timespec="seconds"))

    # 1) 資料庫結構 —— 放第一個：它是「畫面到處 500」最常見的原因
    state = await schema_state(session)
    if state["error"]:
        rep.checks.append(Check(
            "schema", "資料庫結構版本", "warn", state["error"],
            "確認後端能連到資料庫，且 alembic 目錄完整",
        title_key="doctor.c_schema_ver", detail_key="doctor.d_raw", fix_key="doctor.f_schema_probe", params={"detail": str(state["error"])}))
    elif state["behind"]:
        rep.checks.append(Check(
            "schema", "資料庫結構落後於程式", "bad",
            f"資料庫在 {state['current']}，程式需要 {state['head']}",
            "sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade"
            "（或 alembic upgrade head 後重啟後端）",
        title_key="doctor.c_schema_behind", detail_key="doctor.c_schema_behind_d", fix_key="doctor.f_upgrade_or_alembic", params={"current": str(state["current"]), "head": str(state["head"])}))
    else:
        rep.checks.append(Check(
            "schema", "資料庫結構", "ok", f"已在最新版本（{state['current']}）",
        title_key="doctor.c_schema", detail_key="doctor.c_schema_ok", params={"current": str(state["current"])}))

    # 2) 資料庫連線與擴充
    try:
        ver = (await session.execute(text("SHOW server_version"))).scalar_one()
        exts = set((await session.execute(
            text("SELECT extname FROM pg_extension"))).scalars().all())
        missing = {"vector", "pg_trgm"} - exts
        if missing:
            rep.checks.append(Check(
                "db_ext", "PostgreSQL 擴充", "bad",
                f"缺少：{', '.join(sorted(missing))}（PostgreSQL {ver}）",
                "psql -d <db> -c 'CREATE EXTENSION IF NOT EXISTS vector; "
                "CREATE EXTENSION IF NOT EXISTS pg_trgm;'",
        title_key="doctor.c_pg_ext", detail_key="doctor.c_pg_ext_missing", fix_key="doctor.f_create_extension", params={"missing": ", ".join(sorted(missing)), "version": str(ver)}))
        else:
            rep.checks.append(Check("db_ext", "PostgreSQL", "ok",
                                    f"{ver}，vector / pg_trgm 都在",
        title_key="doctor.c_pg", detail_key="doctor.c_pg_ok", params={"version": str(ver)}))
    except Exception as exc:
        rep.checks.append(Check("db_ext", "PostgreSQL", "bad", str(exc)[:200],
                                "systemctl status postgresql",
        title_key="doctor.c_pg", detail_key="doctor.d_raw", fix_key="doctor.f_pg_status", params={"detail": str(exc)[:200]}))

    # 3) 前端建置版本要與後端一致 —— 不一致＝使用者在跑舊的 JS bundle，
    #    這是「存檔沒生效／功能怪怪的」最常見的假故障來源
    rep.checks.append(_frontend_check())

    # 4) 排程同步 —— 只看「有沒有在跑」，實際內容看整合各自的最後錯誤
    try:
        from sqlalchemy import func, select

        from app.models.background_task import BackgroundTask
        from app.services.background_tasks import NOT_SYNC_TIMER_KINDS
        # 只看 jt-ipam-sync 排程寫的列：代理回報、探測與資料庫更新各有自己的節奏，
        # 拿它們當「排程還活著」會把停擺的排程遮掉
        last = (await session.execute(
            select(func.max(BackgroundTask.queued_at)).where(
                BackgroundTask.trigger == "scheduled",
                BackgroundTask.kind.not_in(NOT_SYNC_TIMER_KINDS)))).scalar_one_or_none()
        if last is None:
            # 一個排程同步都沒有過（多半是還沒設定任何整合）：沒有東西可以判斷排程，維持舊的看法（任何作業都算），
            # 不要讓沒設定整合的站台多一個警告
            last = (await session.execute(select(func.max(BackgroundTask.queued_at)))).scalar_one_or_none()
        if last is None:
            rep.checks.append(Check("sync", "背景作業", "warn", "從來沒有背景作業記錄",
                                    "systemctl status jt-ipam-sync.timer",
        title_key="doctor.c_jobs", detail_key="doctor.c_jobs_never", fix_key="doctor.f_sync_timer"))
        else:
            age_h = (datetime.now(UTC) - last).total_seconds() / 3600
            if age_h > 24:
                rep.checks.append(Check(
                    "sync", "背景作業停擺", "warn",
                    f"最後一筆是 {age_h:.0f} 小時前（{last.astimezone():%Y-%m-%d %H:%M}）",
                    "systemctl status jt-ipam-sync.timer；journalctl -u jt-ipam-sync -n 60",
        title_key="doctor.c_jobs_stalled", detail_key="doctor.c_jobs_stalled_d", fix_key="doctor.f_sync_timer_logs", params={"hours": f"{age_h:.0f}", "last": last.isoformat()}))
            else:
                rep.checks.append(Check("sync", "背景作業", "ok",
                                        f"最後一筆 {last.astimezone():%Y-%m-%d %H:%M}",
        title_key="doctor.c_jobs", detail_key="doctor.c_jobs_ok", params={"last": last.isoformat()}))
    except Exception as exc:
        rep.checks.append(Check("sync", "背景作業", "warn", str(exc)[:200],
        title_key="doctor.c_jobs", detail_key="doctor.d_raw", params={"detail": str(exc)[:200]}))

    # 5) 整合的最後錯誤 —— 一次看完，不用逐頁點
    try:
        rep.checks.append(await _integration_errors(session))
    except Exception as exc:
        rep.checks.append(Check("integrations", "整合狀態", "warn", str(exc)[:200],
        title_key="doctor.c_integrations", detail_key="doctor.d_raw", params={"detail": str(exc)[:200]}))

    # 6) 磁碟空間（資料庫與備份都吃這裡）
    try:
        usage = shutil.disk_usage("/")
        free_pct = usage.free / usage.total * 100
        detail = f"根目錄剩餘 {usage.free / 2**30:.1f} GiB（{free_pct:.0f}%）"
        if free_pct < 5:
            rep.checks.append(Check("disk", "磁碟空間不足", "bad", detail, "清理或擴充磁碟",
        title_key="doctor.c_disk_full", detail_key="doctor.d_raw", fix_key="doctor.f_free_disk", params={"detail": detail}))
        elif free_pct < 15:
            rep.checks.append(Check("disk", "磁碟空間偏低", "warn", detail, "留意成長趨勢",
        title_key="doctor.c_disk_low", detail_key="doctor.d_raw", fix_key="doctor.f_watch_growth", params={"detail": detail}))
        else:
            rep.checks.append(Check("disk", "磁碟空間", "ok", detail,
        title_key="doctor.c_disk", detail_key="doctor.d_raw", params={"detail": detail}))
    except Exception as exc:
        rep.checks.append(Check("disk", "磁碟空間", "warn", str(exc)[:200],
        title_key="doctor.c_disk", detail_key="doctor.d_raw", params={"detail": str(exc)[:200]}))

    # 7) ICMP 能力（LXC 常見）：掃描代理要用得到
    try:
        from app.services.netdiag import icmp_socket_available
        if icmp_socket_available():
            rep.checks.append(Check("icmp", "ICMP 探測", "ok", "非特權 ICMP socket 可用",
        title_key="doctor.c_icmp", detail_key="doctor.c_icmp_ok"))
        else:
            rep.checks.append(Check(
                "icmp", "ICMP 探測", "warn",
                "非特權 ICMP socket 不可用（容器內常見；外部 ping 執行檔仍可能可用）",
                "LXC 請以 systemd drop-in 加 AmbientCapabilities=CAP_NET_RAW",
        title_key="doctor.c_icmp", detail_key="doctor.c_icmp_unavailable", fix_key="doctor.f_cap_net_raw"))
    except Exception as exc:
        # 不可以靜默跳過：檢查「不見了」跟「通過了」在畫面上長得一樣
        rep.checks.append(Check("icmp", "ICMP 探測", "warn", f"檢查本身失敗：{exc}"[:200],
        title_key="doctor.c_icmp", detail_key="doctor.d_check_failed", params={"detail": str(exc)[:200]}))

    # 8) 資料健檢：哪些列會讓清單頁讀不出來（客戶回報的那一類）
    try:
        rows = await data_health(session)
        bad_tables = [r for r in rows if r.get("bad_count")]
        errored = [r for r in rows if r.get("error")]
        if bad_tables:
            detail = "；".join(
                f"{r['table']} {r['bad_count']} 筆（例：{r['bad'][0]['label']} — {r['bad'][0]['why']}）"
                for r in bad_tables[:3])
            rep.checks.append(Check(
                "data", "有資料無法在清單頁顯示", "bad", detail[:600],
                "這是程式的欄位限制比資料庫嚴造成的。請把這段訊息回報給我們；"
                "先自行處理的話，把上面那幾筆的該欄位改成合規值即可",
        title_key="doctor.c_data_unreadable", detail_key="doctor.d_raw", fix_key="doctor.f_data_unreadable", params={"detail": detail[:600]}))
        elif errored:
            _errored_text = "；".join(f"{r['table']}：{r['error']}" for r in errored)[:300]
            rep.checks.append(Check("data", "資料健檢", "warn", _errored_text,
        title_key="doctor.c_data", detail_key="doctor.d_raw", params={"detail": _errored_text}))
        else:
            truncated = [r["table"] for r in rows if r.get("truncated")]
            note = f"（只檢查了前 {DATA_SCAN_LIMIT} 筆：{'、'.join(truncated)}）" if truncated else ""
            rep.checks.append(Check("data", "資料健檢", "ok",
                                    f"清單頁的資料都讀得出來{note}",
        title_key="doctor.c_data", detail_key="doctor.c_data_ok", params={"note": note}))
    except Exception as exc:
        rep.checks.append(Check("data", "資料健檢", "warn", f"檢查本身失敗：{exc}"[:200],
        title_key="doctor.c_data", detail_key="doctor.d_check_failed", params={"detail": str(exc)[:200]}))

    # 9) 環境提示：正式環境卻開著 debug
    try:
        from app.core.config import get_settings
        st = get_settings()
        if st.app_debug:
            rep.checks.append(Check("debug", "偵錯模式開啟中", "warn",
                                    f"APP_ENV={st.app_env}", "正式環境請關閉 APP_DEBUG",
        title_key="doctor.c_debug_on", detail_key="doctor.c_env_d", fix_key="doctor.f_disable_debug", params={"env": str(st.app_env)}))
        else:
            rep.checks.append(Check("debug", "執行模式", "ok", f"APP_ENV={st.app_env}",
        title_key="doctor.c_env", detail_key="doctor.c_env_d", params={"env": str(st.app_env)}))
    except Exception as exc:
        rep.checks.append(Check("debug", "執行模式", "warn", f"檢查本身失敗：{exc}"[:200],
        title_key="doctor.c_env", detail_key="doctor.d_check_failed", params={"detail": str(exc)[:200]}))

    # 10) guacd（必要元件，RDP／VNC 的預設引擎）：一定要連得到；選了它的協定外掛都要在
    try:
        rep_guacd = await _guacd_check(session)
        if rep_guacd is not None:
            rep.checks.append(rep_guacd)
    except Exception as exc:
        rep.checks.append(Check("guacd", "guacd 主控台引擎", "warn", f"檢查本身失敗：{exc}"[:200],
                                title_key="doctor.c_guacd", detail_key="doctor.d_check_failed",
                                params={"detail": str(exc)[:200]}))

    # 11) Recog 指紋庫（選用，探測用）：沒裝、或每週的更新檢查一直失敗
    try:
        rep.checks.append(await _recog_check(session))
    except Exception as exc:
        rep.checks.append(Check("recog", "Recog 指紋庫（選用）", "warn", f"檢查本身失敗：{exc}"[:200],
                                title_key="doctor.c_recog", detail_key="doctor.d_check_failed",
                                params={"detail": str(exc)[:200]}))

    # 12) 每日備份：pg_dump 被擋住時備份曾經靜靜失敗了六週（2026-10-08 發現）；bad 會經系統告警通知管理員
    try:
        chk = await _backup_check()
        if chk is not None:
            rep.checks.append(chk)
    except Exception as exc:
        rep.checks.append(Check("backup", "每日備份", "warn", f"檢查本身失敗：{exc}"[:200],
                                title_key="doctor.c_backup", detail_key="doctor.d_check_failed",
                                params={"detail": str(exc)[:200]}))

    # 12b) 備份有沒有加密：沒加密的備份檔裡有資料庫，也有解密它的金鑰
    try:
        chk = await _backup_encryption_check(session)
        if chk is not None:
            rep.checks.append(chk)
    except Exception as exc:
        rep.checks.append(Check("backup_encryption", "備份加密", "warn", f"檢查本身失敗：{exc}"[:200],
                                title_key="doctor.c_backup_enc", detail_key="doctor.d_check_failed",
                                params={"detail": str(exc)[:200]}))

    # 13) 稽核表的擁有者：應用程式帳號擁有的話可以停用「只能新增」的保護（2026-10-09 起由安裝／升級交出去）
    try:
        rep.checks.append(await _audit_owner_check(session))
    except Exception as exc:
        rep.checks.append(Check("audit_owner", "稽核表的保護", "warn", f"檢查本身失敗：{exc}"[:200],
                                title_key="doctor.c_audit_owner", detail_key="doctor.d_check_failed",
                                params={"detail": str(exc)[:200]}))

    return rep


BACKUP_DIR = Path(os.environ.get("JTIPAM_BACKUP_DIR", "/var/backups/jt-ipam"))
#: 每天 03:30 跑一次；超過這麼久沒有成功的備份就是停擺了（多留一天給排程漂移與手動停機）
BACKUP_STALE_HOURS = 48
_BACKUP_LOGS = "journalctl -u jt-ipam-backup -n 30"
_BACKUP_TIMER_FIX = "sudo systemctl enable --now jt-ipam-backup.timer"


def _parse_kv(text_: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (text_ or "").splitlines():
        k, sep, v = line.partition("=")
        if sep:
            out[k.strip()] = v.strip()
    return out


def _iso(v: str | None) -> datetime | None:
    try:
        d = datetime.fromisoformat(v) if v else None
    except ValueError:
        return None
    return d if d is None or d.tzinfo else d.replace(tzinfo=UTC)


def backup_check_from(status_text: str | None, unit: dict[str, str], timer: dict[str, str], *,
                      now: datetime) -> Check | None:
    """依備份腳本的狀態檔（`last-run`）與 systemd 的 unit／timer 狀態判斷。

    沒有這個 timer（開發環境、不是用安裝腳本裝的）就不檢查。狀態檔是新版腳本才寫的：
    沒有它（升級前的舊腳本）就退回看 systemd 的最後結果。
    """
    import re
    if timer.get("LoadState", "not-found") == "not-found":
        return None
    title = "每日備份"
    st = _parse_kv(status_text) if status_text else {}
    if st.get("status") == "fail":
        err = st.get("error", "")
        m = re.search(r"permission denied for table ([A-Za-z0-9_.]+)", err)
        cmd = (f"sudo -u postgres psql -d jt_ipam -c 'ALTER TABLE public.{m.group(1).split('.')[-1]} "
               f"OWNER TO jt_ipam'") if m else _BACKUP_LOGS
        last = st.get("last_success_at", "")
        return Check("backup", "每日備份失敗", "bad",
                     f"{st.get('finished_at', '')} 失敗：{err or '—'}；最後一次成功：{last or '從來沒有'}", cmd,
                     title_key="doctor.c_backup_failed", detail_key="doctor.d_backup_failed",
                     fix_key="doctor.f_backup_owner" if m else "doctor.f_backup_logs",
                     params={"at": st.get("finished_at", ""), "error": err or "—", "last": last or "—",
                             "cmd": cmd})
    if timer.get("UnitFileState") not in ("enabled", "static", "linked") or timer.get("ActiveState") != "active":
        state = f"{timer.get('UnitFileState', '?')}/{timer.get('ActiveState', '?')}"
        return Check("backup", "每日備份沒有排程", "warn", f"jt-ipam-backup.timer：{state}", _BACKUP_TIMER_FIX,
                     title_key="doctor.c_backup_no_timer", detail_key="doctor.d_backup_no_timer",
                     fix_key="doctor.f_backup_timer", params={"state": state})
    if st.get("status") == "ok":
        last_s = st.get("last_success_at") or st.get("finished_at", "")
        last = _iso(last_s)
        if last is not None and now - last > timedelta(hours=BACKUP_STALE_HOURS):
            hours = int((now - last).total_seconds() // 3600)
            return Check("backup", "每日備份停擺", "bad", f"最後一次成功是 {hours} 小時前（{last_s}）",
                         "systemctl list-timers jt-ipam-backup.timer；" + _BACKUP_LOGS,
                         title_key="doctor.c_backup_stale", detail_key="doctor.d_backup_stale",
                         fix_key="doctor.f_backup_logs", params={"hours": str(hours), "last": last_s})
        return Check("backup", title, "ok", f"最後一次成功：{last_s}（{st.get('dump_size', '')}）",
                     title_key="doctor.c_backup", detail_key="doctor.d_backup_ok",
                     params={"at": last_s, "size": st.get("dump_size", "") or "—"})
    # 沒有狀態檔：舊版腳本，看 systemd
    result = unit.get("Result", "")
    ended = unit.get("ExecMainExitTimestamp", "")
    if result and result != "success":
        return Check("backup", "每日備份失敗", "bad", f"備份服務最後一次執行失敗（systemd：{result}，{ended}）",
                     _BACKUP_LOGS, title_key="doctor.c_backup_failed", detail_key="doctor.d_backup_unit_failed",
                     fix_key="doctor.f_backup_logs", params={"result": result, "at": ended})
    if not ended:
        return Check("backup", title, "warn", "還沒有執行過備份", "sudo systemctl start jt-ipam-backup.service",
                     title_key="doctor.c_backup", detail_key="doctor.d_backup_never", fix_key="doctor.f_backup_run")
    return Check("backup", title, "ok", f"備份服務最後一次執行成功（{ended}）",
                 title_key="doctor.c_backup", detail_key="doctor.d_backup_unit_ok", params={"at": ended})


async def _systemctl_show(unit: str, props: tuple[str, ...]) -> dict[str, str]:
    import asyncio
    exe = shutil.which("systemctl")
    if exe is None:
        return {"LoadState": "not-found"}
    proc = await asyncio.create_subprocess_exec(
        exe, "show", unit, *[f"--property={p}" for p in props],
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    except TimeoutError:
        proc.kill()
        return {"LoadState": "not-found"}
    return _parse_kv(out.decode("utf-8", "replace"))


def backup_encryption_check_from(configured: bool, status_text: str | None) -> Check:
    """備份有沒有加密：沒設密碼 → warn；設了但最後一次備份沒加密（加密失敗或舊腳本）→ warn。"""
    st = _parse_kv(status_text) if status_text else {}
    if not configured:
        return Check("backup_encryption", "備份加密", "warn",
                     "沒有設定備份加密密碼：備份檔裡同時有資料庫與解密金鑰（backend.env），拿到備份檔就等於拿到所有機密",
                     "管理 → 系統設定 → 每日備份加密", title_key="doctor.c_backup_enc",
                     detail_key="doctor.d_backup_enc_off", fix_key="doctor.f_backup_enc_off")
    if st.get("status") == "ok" and st.get("encrypted") != "1":
        return Check("backup_encryption", "備份加密", "warn",
                     "已設定備份加密，但最後一次備份沒有加密（升級前的備份腳本，或加密失敗）", _BACKUP_LOGS,
                     title_key="doctor.c_backup_enc", detail_key="doctor.d_backup_enc_missed",
                     fix_key="doctor.f_backup_logs")
    return Check("backup_encryption", "備份加密", "ok", "每日備份以設定的密碼加密（.jtbak）",
                 title_key="doctor.c_backup_enc", detail_key="doctor.d_backup_enc_ok")


async def _backup_encryption_check(session: AsyncSession) -> Check | None:
    timer = await _systemctl_show("jt-ipam-backup.timer", ("LoadState",))
    if timer.get("LoadState", "not-found") == "not-found":
        return None          # 沒有每日備份（開發環境）就不講加密
    from app.services.system_config import get_backup_encryption
    configured = (await get_backup_encryption(session))["enabled"]
    try:
        status_text: str | None = (BACKUP_DIR / "last-run").read_text(encoding="utf-8")
    except OSError:
        status_text = None
    return backup_encryption_check_from(configured, status_text)


_AUDIT_HARDEN_FIX = "sudo bash /opt/jt-ipam/scripts/jt-ipam.sh harden-audit"


async def _audit_owner_check(session: AsyncSession) -> Check:
    """稽核表是不是交給了不能登入的角色（jt_ipam_audit_owner）。

    表的擁有者可以停用「只能新增」的觸發器；應用程式帳號擁有它的話，拿到 backend.env 的人就能
    停用保護後改寫記錄。安裝與升級會自動處理，還原備份或外部資料庫時可能沒做到。"""
    from sqlalchemy import text
    row = (await session.execute(text(
        "SELECT tableowner, current_user FROM pg_tables WHERE tablename = 'audit_logs'"))).first()
    owner, me = (row[0], row[1]) if row else ("", "")
    if owner and owner != me:
        return Check("audit_owner", "稽核表的保護", "ok",
                     f"稽核表由 {owner} 擁有，應用程式帳號只能讀取與新增",
                     title_key="doctor.c_audit_owner", detail_key="doctor.d_audit_owner_ok",
                     params={"owner": owner})
    return Check("audit_owner", "稽核表的保護", "warn",
                 f"稽核表由應用程式帳號（{me}）擁有：它可以停用只能新增的保護", _AUDIT_HARDEN_FIX,
                 title_key="doctor.c_audit_owner", detail_key="doctor.d_audit_owner_app",
                 fix_key="doctor.f_audit_owner", params={"owner": me, "cmd": _AUDIT_HARDEN_FIX})


async def _backup_check() -> Check | None:
    timer = await _systemctl_show("jt-ipam-backup.timer", ("LoadState", "UnitFileState", "ActiveState"))
    unit = await _systemctl_show("jt-ipam-backup.service", ("Result", "ExecMainExitTimestamp"))
    try:
        status_text: str | None = (BACKUP_DIR / "last-run").read_text(encoding="utf-8")
    except OSError:
        status_text = None
    return backup_check_from(status_text, unit, timer, now=datetime.now(UTC))


# 每週檢查一次：連續三週都沒有成功，就不是「偶爾連不到 GitHub」了
_RECOG_STALE_DAYS = 21
_RECOG_FIX = "sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; source /etc/jt-ipam/backend.env; set +a; .venv/bin/python -m app.cli.recog update'"


async def _recog_check(session: AsyncSession) -> Check:
    from app.services import recog
    st = await recog.status(session)
    if not st["installed"]:
        return Check("recog", "Recog 指紋庫（選用）", "warn",
                     "尚未安裝：探測照常運作，但少了由 banner／網頁標題／憑證認出設備與 OS 的比對"
                     + (f"（上次錯誤：{st['error']}）" if st.get("error") else ""),
                     _RECOG_FIX, title_key="doctor.c_recog", detail_key="doctor.d_recog_missing",
                     fix_key="doctor.f_recog", params={"error": st.get("error") or "", "cmd": _RECOG_FIX})
    last_ok = st.get("last_ok_at") or st.get("updated_at")
    try:
        age = (datetime.now(UTC) - datetime.fromisoformat(last_ok)).days if last_ok else None
    except ValueError:
        age = None
    if age is not None and age > _RECOG_STALE_DAYS:
        return Check("recog", "Recog 指紋庫（選用）", "warn",
                     f"版本 {st['release']}；已經 {age} 天沒有成功檢查更新：{st.get('error') or '排程沒有執行'}",
                     "確認這台主機連得到 github.com，並檢查 sudo systemctl status jt-ipam-recog-refresh.timer",
                     title_key="doctor.c_recog", detail_key="doctor.d_recog_stale", fix_key="doctor.f_recog_stale",
                     params={"release": st["release"], "days": age, "error": st.get("error") or ""})
    return Check("recog", "Recog 指紋庫（選用）", "ok", f"版本 {st['release']}，{st['fingerprints']} 條指紋",
                 title_key="doctor.c_recog", detail_key="doctor.d_recog_ok",
                 params={"release": st["release"], "count": st["fingerprints"]})


async def _guacd_check(session: AsyncSession) -> Check | None:
    """guacd 是**必要元件**（RDP／VNC 的預設引擎，2026-09-27 起必裝）：一定要在跑、選了它的協定外掛都要在。

    連不到時設定為 guacd 的連線會暫時退回內建引擎（services/console_engine.py），但那是備援不是正常狀態，
    所以列成失敗；aardwolf 改為選用，缺了不列（GitHub issue #42：Python 3.14 上 aardwolf 會當掉）。
    """
    from app.services import guacd as guac
    from app.services.system_config import get_rdp_engine, get_ssh_engine, get_vnc_engine
    used = [p for p, e in (("rdp", await get_rdp_engine(session)), ("vnc", await get_vnc_engine(session)),
                           ("ssh", await get_ssh_engine(session))) if e == "guacd"]
    st = await guac.probe(use_cache=False)
    names = "、".join(p.upper() for p in used)
    fix = "sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade"
    if not st["ok"]:
        reason = st.get("error") or ""
        if used:
            return Check("guacd", "guacd 主控台引擎", "bad",
                         f"guacd 是必要元件，但連不到它（{st['address']}）：{reason}；{names} 目前暫時改用內建引擎",
                         fix, title_key="doctor.c_guacd", detail_key="doctor.d_guacd_down",
                         fix_key="doctor.f_guacd",
                         params={"protocols": names, "address": st["address"], "reason": reason})
        return Check("guacd", "guacd 主控台引擎", "bad",
                     f"guacd 是必要元件，但連不到它（{st['address']}）：{reason}", fix,
                     title_key="doctor.c_guacd", detail_key="doctor.d_guacd_down_idle", fix_key="doctor.f_guacd",
                     params={"address": st["address"], "reason": reason})
    missing = [p for p in used if not st["protocols"].get(p)]
    if missing:
        m = "、".join(p.upper() for p in missing)
        return Check("guacd", "guacd 主控台引擎", "bad",
                     f"{m} 設定為 guacd，但 guacd 沒有這些協定的支援；目前連線暫時改用內建引擎", fix,
                     title_key="doctor.c_guacd", detail_key="doctor.d_guacd_missing", fix_key="doctor.f_guacd",
                     params={"protocols": m})
    if not used:
        return Check("guacd", "guacd 主控台引擎", "ok", f"服務正常，目前沒有協定使用它（{st['address']}）",
                     title_key="doctor.c_guacd", detail_key="doctor.d_guacd_idle",
                     params={"address": st["address"]})
    return Check("guacd", "guacd 主控台引擎", "ok", f"{names} 使用 guacd，服務正常（{st['address']}）",
                 title_key="doctor.c_guacd", detail_key="doctor.d_guacd_ok",
                 params={"protocols": names, "address": st["address"]})


async def _integration_errors(session: AsyncSession) -> Check:
    """把各整合的 `last_error` 掃一遍。有錯的列出名字，沒錯就一句話帶過。"""
    from sqlalchemy import select

    from app.models.adguard import AdGuardInstance
    from app.models.checkmk import CheckmkInstance
    from app.models.checkpoint import CheckPointServer
    from app.models.checkpoint_gaia import CheckPointGaiaTarget
    from app.models.firewall import OPNsenseFirewall
    from app.models.fortigate import FortiGateFirewall
    from app.models.librenms import LibreNMSInstance
    from app.models.mikrotik import MikroTikRouter
    from app.models.paloalto import PaloAltoFirewall
    from app.models.pfsense import PfSenseFirewall
    from app.models.virt import ProxmoxInstance
    from app.models.wazuh import WazuhInstance
    from app.models.zabbix import ZabbixInstance

    failing: list[str] = []
    total = 0
    for model, label in (
        (OPNsenseFirewall, "OPNsense"), (PfSenseFirewall, "pfSense"),
        (FortiGateFirewall, "FortiGate"), (PaloAltoFirewall, "Palo Alto"),
        (CheckPointServer, "Check Point"), (CheckPointGaiaTarget, "Check Point Gaia"),
        (MikroTikRouter, "MikroTik"), (LibreNMSInstance, "LibreNMS"),
        (ZabbixInstance, "Zabbix"), (CheckmkInstance, "Checkmk"), (WazuhInstance, "Wazuh"),
        (AdGuardInstance, "AdGuard"), (ProxmoxInstance, "Proxmox"),
    ):
        for obj in (await session.execute(select(model))).scalars().all():
            total += 1
            if getattr(obj, "last_error", None):
                failing.append(f"{label}／{getattr(obj, 'name', '?')}")
    if not total:
        return Check("integrations", "整合狀態", "ok", "尚未設定任何整合",
        title_key="doctor.c_integrations", detail_key="doctor.c_integrations_none")
    if failing:
        return Check("integrations", "整合有錯誤", "warn",
                     f"{len(failing)} / {total} 個實例有最後錯誤：{'、'.join(failing[:8])}",
                     "到各整合設定頁看「最後錯誤」與「測試連線」",
        title_key="doctor.c_integrations_bad", detail_key="doctor.c_integrations_bad_d", fix_key="doctor.f_integration_pages", params={"failing": len(failing), "total": total, "names": "、".join(failing[:8])})
    return Check("integrations", "整合狀態", "ok", f"{total} 個實例都沒有最後錯誤",
        title_key="doctor.c_integrations", detail_key="doctor.c_integrations_ok", params={"total": total})


# ─────────────────── 資料健檢 ───────────────────
#: 「清單頁讀得出來嗎」要檢查的表。每一項是（畫面上的名稱, ORM, 讀取用 schema, 顯示欄位）。
#:
#: 為什麼需要這個：讀取用的 schema 繼承了**寫入用**的約束（型別允許清單、長度上限、
#: 數值範圍），但整合同步進來的資料不走表單 —— LibreNMS／Proxmox 給的 vendor／model
#: 可以很長，type 也可能不在我們的清單裡。一列不合規就會讓**整頁 500**，而儀表板的
#: count(*) 不讀欄位所以照樣正常（2026-09-05 客戶回報的就是這個組合）。
DATA_TABLES: tuple[tuple[str, str, str, str], ...] = (
    ("裝置", "app.models.device:Device", "app.schemas.device:DeviceRead", "name"),
    ("IP 位址", "app.models.address:IPAddress", "app.schemas.address:IPAddressRead", "ip"),
    ("子網路", "app.models.subnet:Subnet", "app.schemas.subnet:SubnetRead", "cidr"),
    ("區段", "app.models.section:Section", "app.schemas.section:SectionRead", "name"),
    ("機櫃", "app.models.location:Rack", "app.schemas.location:RackRead", "name"),
    ("機房 / 地點", "app.models.location:Location", "app.schemas.location:LocationRead", "name"),
    ("單位", "app.models.customer:Customer", "app.schemas.customer:CustomerRead", "name"),
)

#: 一次最多檢查幾列。這一頁是人按下去才跑的，不能在大型環境上把資料庫拖住；
#: 掃不完時會**明講掃到哪裡**，而不是回一個看起來全綠的結果。
DATA_SCAN_LIMIT = 5000


def _load(path: str) -> Any:
    module, name = path.split(":")
    import importlib
    return getattr(importlib.import_module(module), name)


def _why(exc: Any, row: Any) -> str:
    """把 Pydantic 的錯誤縮成一句人看得懂的話。

    一定要帶**實際值**（或長度）：只說「string too long」的話，看的人還是得自己去
    翻資料庫才知道哪裡要改 —— 那就等於沒有把診斷做完。
    """
    out: list[str] = []
    for err in getattr(exc, "errors", lambda: [])()[:3]:
        field = ".".join(str(x) for x in err.get("loc", ())) or "?"
        msg = err.get("msg", "")
        value = getattr(row, field.split(".")[0], None)
        if isinstance(value, str) and len(value) > 60:
            shown = f"（長度 {len(value)}，開頭：{value[:40]}…）"
        elif value is None:
            shown = "（目前是空值）"
        else:
            shown = f"（目前值：{value}）"
        out.append(f"{field}：{msg}{shown}")
    return "；".join(out) or str(exc)[:200]


async def data_health(session: AsyncSession) -> list[dict[str, Any]]:
    """逐表檢查「這些列在清單頁讀得出來嗎」，回傳讀不出來的那些。

    直接拿**正式在用的讀取 schema** 去驗，所以這裡通過就等於清單頁不會因為資料而爆；
    自己另寫一套規則去猜，遲早會跟真正的 schema 走鐘。
    """
    from sqlalchemy import func, select

    out: list[dict[str, Any]] = []
    for label, model_path, schema_path, name_field in DATA_TABLES:
        try:
            model, schema = _load(model_path), _load(schema_path)
        except Exception as exc:                  # 表或 schema 不在（舊版）→ 跳過但要講
            out.append({"table": label, "error": f"無法載入：{exc}"[:200]})
            continue
        try:
            total = int(await session.scalar(select(func.count()).select_from(model)) or 0)
            rows = (await session.execute(
                select(model).limit(DATA_SCAN_LIMIT))).scalars().all()
        except Exception as exc:
            out.append({"table": label, "error": str(exc)[:200]})
            continue

        bad: list[dict[str, Any]] = []
        for row in rows:
            try:
                schema.model_validate(row)
            except Exception as exc:              # 就是這一列會讓清單頁 500
                bad.append({
                    "id": str(getattr(row, "id", "")),
                    "label": str(getattr(row, name_field, "") or "")[:80],
                    "why": _why(exc, row),
                })
        if bad or total > len(rows):
            out.append({
                "table": label, "checked": len(rows), "total": total,
                "bad": bad[:20], "bad_count": len(bad),
                "truncated": total > len(rows),
            })
    return out


def env_file_hint() -> str:
    return os.environ.get("JTIPAM_ENV_FILE", "/etc/jt-ipam/backend.env")
