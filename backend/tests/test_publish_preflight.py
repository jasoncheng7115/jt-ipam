"""發佈前檢查（scripts/publish/preflight.py，2026-11-22 參考 jt-waf-mgr 的發版流程）。

以前推 GitHub 是「整棵樹疊過去、用排除清單擋、再用 sed 把幾個已知字串改掉」，每次靠人工 grep 才攔到
實驗室主機名稱與真實位址，而且已公開的樹裡還留著好幾個。現在：
- 允許清單（PUBLISH.txt）：沒列的檔案不發佈
- 私有封鎖清單放在專案外，沒有它就不發佈；不改寫內容，攔到就改原始碼
- 金鑰要有內容才算（說明文字只寫開頭那一行不算）、信箱只允許範例與公開的、session 連結、會自動關 issue 的字
- 截圖的中繼資料（PNG 文字區塊、JPEG EXIF）
"""
from __future__ import annotations

import importlib.util
import struct
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def pf(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    deny = tmp_path / "deny.txt"
    deny.write_text("# test\nlab.example.net\n!blog.lab.example.net\nsrv-42\n10.66.\naa:bb:cc:\n", encoding="utf-8")
    monkeypatch.setenv("JT_IPAM_DENYLIST", str(deny))
    spec = importlib.util.spec_from_file_location("preflight", ROOT / "scripts/publish/preflight.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _scan(pf, text, **kw):  # type: ignore[no-untyped-def]
    pf.failures.clear()
    pf.scan_text("f", text, pf.load_denylist(), **kw)
    return list(pf.failures)


def test_denylist_names_prefixes_and_exceptions(pf) -> None:  # type: ignore[no-untyped-def]
    assert _scan(pf, "see https://blog.lab.example.net/post") == []
    assert _scan(pf, "host sso.lab.example.net")
    assert _scan(pf, "Device(name='srv-42')")
    assert _scan(pf, "srv-421 and srv-42x") == [], "整個字才算"
    assert _scan(pf, "gateway 10.66.0.1")
    assert _scan(pf, "mac AA:BB:CC:00:11:22")
    assert _scan(pf, '"mac": "AA-BB-CC-00-11-22"'), "Windows 的寫法"
    assert _scan(pf, '"mac": "AABBCC001122"'), "沒有分隔符號的寫法"
    assert _scan(pf, '"mac": "02AABBCC0011"') == [], "前綴要在開頭"


def test_missing_denylist_refuses(pf, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(pf, "DENYLIST", "/nonexistent/deny.txt")
    assert pf.load_denylist() is None
    with pytest.raises(SystemExit):
        pf.require_denylist()


def test_keys_tokens_mailboxes_sessions(pf) -> None:  # type: ignore[no-untyped-def]
    assert _scan(pf, '"hint": "-----BEGIN OPENSSH PRIVATE KEY-----\\n..."') == [], "只有開頭一行是說明，不是金鑰"
    assert _scan(pf, "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA1234567890abcdefghijklmnopqrstuvwxyzABCD")  # publish:allow (a test of the checks)
    assert _scan(pf, "token jt_live_" + "A" * 43)
    assert _scan(pf, "mail admin@example.com or noreply@users.noreply.github.com") == []
    assert _scan(pf, "contact someone@realcorp.com.tw")  # publish:allow (a test of the checks)
    assert _scan(pf, "Claude-Session: https://claude.ai/code/session_x")  # publish:allow (a test of the checks)


def test_messages_must_not_close_issues(pf) -> None:  # type: ignore[no-untyped-def]
    assert _scan(pf, "fix(nat): save edits\n\nFixes #59", message=True)
    assert _scan(pf, "fix(nat): save edits (GitHub issue #59)", message=True) == []


def test_allowlist(pf) -> None:  # type: ignore[no-untyped-def]
    pats = pf.allowlist((ROOT / "PUBLISH.txt").read_text(encoding="utf-8"))
    assert pf.listed("backend/app/main.py", pats)
    assert pf.listed("docs/INSTALL_ja.md", pats)
    assert pf.listed("docs/shots/zh/a.png", pats)
    assert not pf.listed("docs/SPEC_WARROOM_zh-TW.md", pats), "內部規格不發佈"
    assert not pf.listed("CLAUDE.md", pats)
    assert not pf.listed("notes.txt", pats), "沒列的新檔案預設不發佈"


def _png(chunks):  # type: ignore[no-untyped-def]
    out = b"\x89PNG\r\n\x1a\n"
    for kind, body in chunks:
        out += struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return out


def test_screenshot_metadata(pf) -> None:  # type: ignore[no-untyped-def]
    pf.failures.clear()
    pf.check_blob("a.png", _png([(b"IHDR", b"\0" * 13), (b"IEND", b"")]), pf.load_denylist())
    assert pf.failures == []
    pf.check_blob("b.png", _png([(b"IHDR", b"\0" * 13), (b"tEXt", b"Comment\0srv-42"), (b"IEND", b"")]),
                  pf.load_denylist())
    assert len(pf.failures) == 2, "有文字區塊就擋，內容裡的私有名稱另外列出"
    pf.failures.clear()
    pf.check_blob("c.bin", b"\0\1\2\3", pf.load_denylist())
    assert pf.failures, "看不了內容的二進位檔不發佈"
