"""jt-ipam RustDesk agent (dedicated, standard library only).

Installed on the host running RustDesk Server (open source; hbbs/hbbr). It connects OUT to jt-ipam with
the agent key of ONE "RustDesk" server configured in the jt-ipam web UI (each server has its own key):

  1. POST {SERVER}/api/v1/rustdesk/agent/poll         every few seconds: report this agent's state, get the
                                                     settings + "sync now" / "test" requests
  2. POST {SERVER}/api/v1/rustdesk/agent/report       every report interval (or on "sync now"): the device IDs
                                                     read from the hbbs database + online state
  3. POST {SERVER}/api/v1/rustdesk/agent/events       RustDesk client reports received on TCP 21114 (below)
  4. POST {SERVER}/api/v1/rustdesk/agent/test-result  results of a "test" requested in the web UI
  5. POST {SERVER}/api/v1/rustdesk/agent/delete-result results of "delete old registrations" (below)

Auth: every request carries header  X-Agent-Key: <agent key>  (the server compares sha256).

What is read on this host (fixed here; the jt-ipam server cannot change it):
  - the hbbs working directory (official deb: /var/lib/rustdesk-server), database opened read-only;
    only id / created_at / the registered IP of each device. pk / uuid / guid are not sent.
  - id_ed25519.pub (the public key, for the "connect with RustDesk" links). The private key file
    id_ed25519 is never opened.
  - `hbbs --version`
  - online state: an OnlineRequest to hbbs on this host's own address (hbbs treats loopback as its
    admin console, so 127.0.0.1 is not used)

Deleting old registrations (agent 1.2.0): hbbs keeps every ID that ever registered. When BOTH sides allow it, an
admin can delete old ones from the jt-ipam web UI:
  - jt-ipam: "Allow deleting old registrations" switched on for this server (off by default), and
  - this host: the installer re-run with --allow-delete (JT_RD_ALLOW_DELETE=1 in the config file; systemd then
    makes the hbbs directory writable for the agent and hides the private key file id_ed25519 from it).
The poll reply then carries the IDs to delete. Right before deleting, the agent asks hbbs again whether each ID is
online (OnlineRequest) and skips the online ones; it deletes only rows of the `peer` table by id, in one
transaction, and touches no other table or file. Without --allow-delete every request is answered "failed" with
the reason, and the database stays read-only.

RustDesk client reports: when receiving client reports is switched on for this server in the jt-ipam web
UI, the agent listens on TCP 21114 (port set in the web UI). RustDesk clients that only have the ID server
configured send their heartbeat, system info (host name, OS, user) and connection / file transfer / alarm
audits there. Each report must carry the same ID and uuid as the hbbs database before it is forwarded;
the uuid is only compared on this host and is never forwarded, stored or logged. Replies never contain
anything that disconnects a session or changes client settings. Switched off: the port is not opened.

Auto-update: each poll returns the server's agent.py sha256. If it differs from this running copy, the
agent downloads the new agent.py and agent.py.sig, and only installs it (then re-executes) when the
signature verifies against the jt-ipam signing key built into this file.

Environment variables (/etc/jt-ipam-rustdesk-agent.env):
  JT_IPAM_URL           e.g. https://ipam.example.com    (required)
  JT_IPAM_AGENT_KEY     agent key of this RustDesk server, shown in jt-ipam (required)
  JT_IPAM_PIN           sha256//<base64> of the jt-ipam server's public key (the install command shown in
                        jt-ipam sets it): talk only to that server, also with a self-signed certificate
  JT_IPAM_INSECURE      =1 to skip TLS verification entirely (not recommended; use JT_IPAM_PIN)
  JT_IPAM_AUTO_PIN      =0 to keep the settings above as they are. By default an agent that does not verify
                        jt-ipam switches to verifying it by itself and follows a certificate change signed by
                        this jt-ipam (what it learned is kept in jt-ipam-trust next to this program)
  JT_IPAM_AUTO_UPDATE   =0 to disable self-update (default on)
  JT_IPAM_RUSTDESK_DIR  hbbs working directory when it is not the official deb's /var/lib/rustdesk-server
  JT_RD_ALLOW_DELETE    =1 to allow deleting old registrations (written by the installer's --allow-delete)
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import hmac
import http.server
import ipaddress
import itertools
import json
import os
import re
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

AGENT_VERSION = "1.4.0"
SERVER = os.environ.get("JT_IPAM_URL", "").rstrip("/")
KEY = os.environ.get("JT_IPAM_AGENT_KEY", "")
INSECURE = os.environ.get("JT_IPAM_INSECURE", "") in ("1", "true", "yes")
AUTO_UPDATE = os.environ.get("JT_IPAM_AUTO_UPDATE", "1") not in ("0", "false", "no")
AGENT_PATH = os.path.realpath(__file__)
POLL_SECONDS_DEFAULT = 10
POLL_SECONDS_RANGE = (5, 300)
API_BASE = "/api/v1/rustdesk/agent"


# ── 伺服器身分（2026-11-04）──────────────────────────────────────────────────
# 安裝指令帶 jt-ipam 憑證公鑰的指紋（JT_IPAM_PIN=sha256//<base64>，跟 curl --pinnedpubkey 同格式）：代理只跟
# 出示這把公鑰的伺服器講話，握手完、送出金鑰之前就比對。沒有指紋時用系統 CA 驗證；JT_IPAM_INSECURE 才完全不驗。
# 以前的安裝指令一律 INSECURE：能攔截網路的人可以假冒 jt-ipam，拿走金鑰、推一支以 root 執行的「新版代理」。
# 這一段在掃描代理與 RustDesk 代理裡一字不差（tests/test_python_agent_trust.py 比對）。
PIN = os.environ.get("JT_IPAM_PIN", "").strip()
# 自我更新只裝用 jt-ipam 簽章金鑰簽過的版本（RSA-3072、PKCS#1 v1.5、SHA-256；私鑰不在程式庫裡）
AGENT_SIGNING_N = int.from_bytes(base64.b64decode(
    "58yoM59293eHgMFKcrE6nWfpIWVQqHr7SJIhyXmCjaAQVoqwCY2VNqinJIMGGDE2wAiKKjxm8xSi+0Cx89/fF/hNAs59s+T9m8q4pV7f39QW/AeREcMj+1qk3Y+f4XbarAh2kd82f9eNx4mQ7P+1AIpyFTfPRsX/lzcNVZ8L9PcwgOA08qydmiuJ0yLNR987OMaWuc2wRIXjcYmwjYLXrpqxDC+jCccY2Ni25nCrJzJIoqGbhWJP5Ddu3lq/gD37DiNoqmleNYiDeEAM1/w7LtplmMF0Pn4QsMvcRzgORMwYoBFrT09ouC3iKiIcZNlDnlg9dBvpE1QmyY2EwLVxS7Y+FmMPyfgTvKQyT2s63+3KrXhR3nOESdfolZdbjW09dEybI6TPSRjAlQKl7cuIKyJeySlxeFu4ARscP6HcmoBNVpqLZP2epoDz0mORCeGG9U2K4yDDHZwrmI+U+/BvO7LX+hO8YXbYeONCuztgg1MiwFyWdPk+xg0/jC651a2N"), "big")
AGENT_SIGNING_E = 65537
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _tls_mode() -> str:
    """回報給伺服器（X-Agent-Tls）：pinned / ca / insecure。http 根本沒加密，算 insecure。"""
    if not SERVER.startswith("https"):
        return "insecure"
    return "pinned" if PIN else ("insecure" if INSECURE else "ca")


def _ctx() -> ssl.SSLContext | None:
    if not SERVER.startswith("https"):
        return None
    ctx = ssl.create_default_context()
    # 代理連回伺服器一律 TLS 1.2 以上（CodeQL #43）。Python 3.10 起預設就是，明寫出來不靠預設值
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if PIN or INSECURE:
        # 釘選時身分由 _check_pin 比對公鑰（自簽憑證、用 IP 連都可以）；只有 INSECURE 是真的不驗
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _der_tlv(der: bytes, i: int) -> tuple[int, int, int]:
    """DER 的一個 TLV → (tag, 內容起點, 結尾)。"""
    tag, n = der[i], der[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(der[i:i + k], "big")
        i += k
    return tag, i, i + n


def _spki_pin(der: bytes) -> str:
    """憑證 DER → sha256//<base64>（SubjectPublicKeyInfo 的 SHA-256）。標準函式庫取不出公鑰，自己走 X.509 結構。"""
    _, i, _ = _der_tlv(der, 0)                 # Certificate
    _, i, _ = _der_tlv(der, i)                 # TBSCertificate 的內容
    tag, _, end = _der_tlv(der, i)
    if tag == 0xA0:                            # [0] version（v1 憑證沒有這欄）
        i = end
    for _ in range(5):                         # serialNumber, signature, issuer, validity, subject
        i = _der_tlv(der, i)[2]
    end = _der_tlv(der, i)[2]                  # subjectPublicKeyInfo 整段
    return "sha256//" + base64.b64encode(hashlib.sha256(der[i:end]).digest()).decode()


def _check_pin(sock: ssl.SSLSocket, pin: str | None = None) -> None:
    want = PIN if pin is None else pin                 # pin：還沒採用、先試連一次的候選公鑰
    if not want:
        return
    try:
        got = _spki_pin(sock.getpeercert(binary_form=True) or b"")
    except (IndexError, ValueError):
        got = ""
    if not hmac.compare_digest(got.encode(), want.encode()):
        sock.close()
        raise ssl.SSLError("server certificate does not match JT_IPAM_PIN "
                           "(not the jt-ipam server this agent was installed for)")


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    pin: str | None = None

    def connect(self) -> None:
        super().connect()
        _check_pin(self.sock, self.pin)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):  # type: ignore[no-untyped-def]
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)


def _urlopen(req: urllib.request.Request, timeout: float):  # type: ignore[no-untyped-def]
    """連回伺服器的 HTTP(S) 都走這裡：釘選時在送出請求（與金鑰）之前比對公鑰。"""
    if PIN and SERVER.startswith("https"):
        return urllib.request.build_opener(_PinnedHTTPSHandler(context=_ctx())).open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout, context=_ctx())  # noqa: S310


def _verify_signature(data: bytes, sig_b64: str) -> bool:
    """自我更新的簽章：jt-ipam 的程式簽章金鑰（內建在這支程式裡）。"""
    return _rsa_verify(AGENT_SIGNING_N, AGENT_SIGNING_E, data, sig_b64)


# ── 不必重裝就改成驗證 jt-ipam（2026-11-07）──────────────────────────────────────
# 1.0.20 以前裝的代理不驗證伺服器。與其要客戶逐台重跑安裝指令，代理自己來（JT_IPAM_AUTO_PIN，預設開）：
# 抓 jt-ipam 的信任資料（目前憑證的公鑰，用這台 jt-ipam 自己的身分金鑰簽章；抓的時候不帶代理金鑰），
# 先試一般 CA 驗證、不行就釘選公鑰，**用新設定真的連得上才換**。同時記下身分金鑰：之後 jt-ipam 換憑證，
# 新的公鑰要有那把身分金鑰的簽章、而且用它真的連得上，才跟著換。
# 學到的存在程式旁邊的 jt-ipam-trust（RustDesk 代理只有程式目錄可寫），優先於環境變數；安裝程式會清掉。
AUTO_PIN = os.environ.get("JT_IPAM_AUTO_PIN", "true").strip().lower() not in ("0", "false", "no", "off")
TRUST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jt-ipam-trust")
TRUST_IDENTITY: tuple[int, int] | None = None          # 身分公鑰 (n, e)
_PIN_FORMAT = re.compile(r"sha256//[A-Za-z0-9+/]{43}=")
_TRUST_NEXT = {"maintain": 0.0, "recover": 0.0}


def _rsa_verify(n: int, e: int, data: bytes, sig_b64: str) -> bool:
    """RSA PKCS#1 v1.5 / SHA-256 驗章，純 Python：不靠 openssl，也就不會因為主機沒裝而跳過。"""
    try:
        sig = base64.b64decode(sig_b64.strip(), validate=True)
    except (ValueError, TypeError):
        return False
    k = (n.bit_length() + 7) // 8
    s = int.from_bytes(sig, "big")
    if len(sig) != k or s >= n:
        return False
    t = _SHA256_DIGEST_INFO + hashlib.sha256(data).digest()
    if k < len(t) + 11:
        return False
    want = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(pow(s, e, n).to_bytes(k, "big"), want)


def _trust_load() -> None:
    global PIN, INSECURE, TRUST_IDENTITY
    try:
        with open(TRUST_FILE, encoding="utf-8") as f:
            kv = dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)
    except OSError:
        return
    mode, pin = kv.get("TRUST_MODE"), kv.get("TRUST_PIN", "")
    if mode == "pinned" and _PIN_FORMAT.fullmatch(pin):
        PIN, INSECURE = pin, False
    elif mode == "ca":
        PIN, INSECURE = "", False
    else:
        return
    try:
        TRUST_IDENTITY = (int.from_bytes(base64.b64decode(kv["TRUST_N"], validate=True), "big"),
                          int.from_bytes(base64.b64decode(kv["TRUST_E"], validate=True), "big"))
    except (KeyError, ValueError):
        TRUST_IDENTITY = None


def _trust_save(mode: str, pin: str, n_b64: str, e_b64: str) -> bool:
    tmp = TRUST_FILE + ".new"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"TRUST_MODE={mode}\nTRUST_PIN={pin}\nTRUST_N={n_b64}\nTRUST_E={e_b64}\n")
        os.replace(tmp, TRUST_FILE)
    except OSError as exc:
        print(f"[trust] cannot save {TRUST_FILE}: {exc}", file=sys.stderr, flush=True)
        return False
    _trust_load()
    return True


def _trust_get(how: str, pin: str = "") -> bytes | None:
    """jt-ipam 的信任資料；how = insecure / ca / pin。不帶代理金鑰（還不確定對面是誰）。"""
    req = urllib.request.Request(f"{SERVER}/api/v1/agent-trust", method="GET")  # noqa: S310
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if how != "ca":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        if how == "pin":
            conn = type("_TrustPinned", (_PinnedHTTPSConnection,), {"pin": pin})
            handler = _PinnedHTTPSHandler(context=ctx)
            handler.https_open = lambda r: handler.do_open(conn, r, context=ctx)  # type: ignore[method-assign]
            resp = urllib.request.build_opener(handler).open(req, timeout=20)
        else:
            resp = urllib.request.urlopen(req, timeout=20, context=ctx)  # noqa: S310
        with resp:
            return resp.read(65536)
    except (OSError, ValueError):
        return None


def _trust_parse(raw: bytes | None) -> dict[str, str] | None:
    """一行一個 key=value，最後是 sig=（簽的是它之前的所有位元組）；格式不對 → None。"""
    try:
        text = (raw or b"").decode("ascii")
        i = text.index("\nsig=")
        f = dict(line.split("=", 1) for line in text[:i].split("\n") if "=" in line)
        f["_body"], f["_sig"] = text[:i + 1], text[i + 5:].strip()
        f["_n"] = str(int.from_bytes(base64.b64decode(f["identity_n"], validate=True), "big"))
        f["_e"] = str(int.from_bytes(base64.b64decode(f["identity_e"], validate=True), "big"))
        if f.get("jt-ipam-agent-trust") != "1" or not _PIN_FORMAT.fullmatch(f.get("spki_pin", "")):
            return None
        return f
    except (ValueError, KeyError, UnicodeDecodeError):
        return None


def _trust_maintain() -> None:
    """記下 jt-ipam 的身分（一次）；不驗證的代理也趁這時改成驗證。失敗一小時後再試，絕不丟例外。"""
    if not SERVER.startswith("https://") or not AUTO_PIN or TRUST_IDENTITY is not None:
        return
    now = time.monotonic()
    if now < _TRUST_NEXT["maintain"]:
        return
    _TRUST_NEXT["maintain"] = now + 3600
    try:
        mode = _tls_mode()
        f = _trust_parse(_trust_get("pin", PIN) if mode == "pinned" else _trust_get(mode))
        if f is None or not _rsa_verify(int(f["_n"]), int(f["_e"]), f["_body"].encode(), f["_sig"]):
            return
        n_b64, e_b64, pin = f["identity_n"], f["identity_e"], f["spki_pin"]
        if mode == "pinned":
            if pin == PIN:
                _trust_save("pinned", pin, n_b64, e_b64)
        elif mode == "ca":
            _trust_save("ca", "", n_b64, e_b64)
        elif f.get("auto_pin") != "1":
            pass                                         # jt-ipam 關掉了自動改成驗證
        elif _trust_get("ca") is not None:
            if _trust_save("ca", "", n_b64, e_b64):
                print("[trust] now verifying jt-ipam's certificate (certificate authorities)", flush=True)
        elif _trust_get("pin", pin) is not None:
            if _trust_save("pinned", pin, n_b64, e_b64):
                print(f"[trust] now only talking to the jt-ipam server with public key {pin}", flush=True)
    except Exception as exc:  # noqa: BLE001 — 這只是加強，不能讓代理停下來
        print(f"[trust] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


def _trust_cert_error(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(exc, ssl.SSLError) or isinstance(reason, ssl.SSLError)


def _trust_recover() -> bool:
    """驗證過的連線因為憑證失敗：jt-ipam 換了憑證、而且有記下的身分簽章 → 跟著換（True＝換了，馬上重試）。"""
    if TRUST_IDENTITY is None or _tls_mode() == "insecure":
        return False
    now = time.monotonic()
    if now < _TRUST_NEXT["recover"]:
        return False
    _TRUST_NEXT["recover"] = now + 300
    try:
        f = _trust_parse(_trust_get("insecure"))
        if f is None or (int(f["_n"]), int(f["_e"])) != TRUST_IDENTITY:
            return False
        if not _rsa_verify(*TRUST_IDENTITY, f["_body"].encode(), f["_sig"]):
            print("[trust] the server's certificate changed but the change is not signed by this jt-ipam; "
                  "not following it", file=sys.stderr, flush=True)
            return False
        pin = f["spki_pin"]
        if pin == PIN or int(f.get("not_after", "0")) <= time.time() or _trust_get("pin", pin) is None:
            return False
        if _trust_save("pinned", pin, f["identity_n"], f["identity_e"]):
            print(f"[trust] jt-ipam changed its certificate; following it (signed by this jt-ipam): {pin}", flush=True)
            return True
    except Exception as exc:  # noqa: BLE001
        print(f"[trust] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    return False


_trust_load()


def _req(method: str, path: str, body: dict | None = None,
         extra_headers: dict | None = None, timeout: float = 30) -> dict:
    url = f"{SERVER}{path}"
    data = json.dumps(body).encode() if body is not None else None
    # S310: the scheme is validated once at startup (main() rejects anything that is not http/https),
    # and SERVER comes from this host's own config file, not from anything the network says.
    req = urllib.request.Request(url, data=data, method=method)  # noqa: S310
    req.add_header("X-Agent-Key", KEY)
    req.add_header("X-Agent-Version", AGENT_VERSION)
    req.add_header("X-Agent-Tls", _tls_mode())
    if extra_headers:
        for k, v in extra_headers.items():
            req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with _urlopen(req, timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def _get_bytes(path: str) -> bytes:
    req = urllib.request.Request(f"{SERVER}{path}", method="GET")  # noqa: S310
    req.add_header("X-Agent-Key", KEY)
    req.add_header("X-Agent-Version", AGENT_VERSION)
    req.add_header("X-Agent-Tls", _tls_mode())
    with _urlopen(req, 30) as resp:
        return resp.read()


def _self_sha() -> str:
    try:
        with open(AGENT_PATH, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def _maybe_self_update(server_sha: str | None) -> None:
    """If the server's agent.py differs from this copy, update self and re-exec."""
    if not AUTO_UPDATE or not server_sha or server_sha == _self_sha():
        return
    print("[update] server agent differs from local; downloading new version", flush=True)
    try:
        new = _get_bytes(f"{API_BASE}/agent.py")
        if hashlib.sha256(new).hexdigest() != server_sha:
            print("[update] downloaded sha mismatch; skip this round", flush=True)
            return
        # 只裝 jt-ipam 簽章金鑰簽過的版本：sha256 是同一台伺服器給的，假冒或被入侵的伺服器照樣算得出來
        sig = _get_bytes(f"{API_BASE}/agent.py.sig").decode("ascii", "replace")
        if not _verify_signature(new, sig):
            print("[update] SIGNATURE CHECK FAILED; keeping the current agent", file=sys.stderr, flush=True)
            return
        tmp = AGENT_PATH + ".new"
        with open(tmp, "wb") as f:
            f.write(new)
        os.chmod(tmp, 0o755)
        os.replace(tmp, AGENT_PATH)
        print("[update] updated; re-executing new agent", flush=True)
        _rdapi_stop()                       # 先放掉 21114，新程式才綁得上
        os.execv(sys.executable, [sys.executable, AGENT_PATH])
    except Exception as exc:  # noqa: BLE001 — never let update crash the agent
        print(f"[update] failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


# ── RustDesk Server（開源版）──────────────────────────────────────────────────
# 讀 hbbs 的資料庫（已註冊的 ID、首次註冊時間、登記 IP）並查線上狀態，
# 只回報解析後的結果。開源版沒有管理 API（那是 Pro 版的功能），能用的只有 SQLite 與 OnlineRequest。
#
# 安全界線：
# - 只讀固定位置（官方 deb 的工作目錄；可用 JT_IPAM_RUSTDESK_DIR 在這台主機改），伺服器端改不了
# - peer 表只取 id／created_at／info.ip；pk、uuid、guid 不讀；私鑰檔 id_ed25519 絕不開啟，
#   只讀公鑰 id_ed25519.pub（給 jt-ipam 組「以 RustDesk 連線」的網址）
# - 資料庫以唯讀模式開啟；唯一的例外是「刪除舊註冊」（見下方 _rustdesk_delete_peers：兩邊都允許才寫）
RUSTDESK_DIR_CANDIDATES = ("/var/lib/rustdesk-server",)
RUSTDESK_MAX_PEERS = 50000
RUSTDESK_ONLINE_BATCH = 1000
_RUSTDESK_ID = re.compile(r"^[A-Za-z0-9_-]{3,100}$")
_RUSTDESK_KEY = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def _rustdesk_dir() -> str:
    v = os.environ.get("JT_IPAM_RUSTDESK_DIR", "").strip()
    if v:
        return v
    for c in RUSTDESK_DIR_CANDIDATES:
        if os.path.isdir(c):
            return c
    return RUSTDESK_DIR_CANDIDATES[0]


def _rustdesk_ip(value) -> str | None:  # noqa: ANN001
    try:
        ip = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped                 # hbbs 聽雙堆疊：IPv4 客戶端記成 ::ffff:a.b.c.d
    return str(ip)


def _rustdesk_db_open(db_path: str):  # noqa: ANN202 — sqlite3 在用到時才載入
    """hbbs 的資料庫一律以唯讀模式開啟（URI mode=ro），不會改到 hbbs 的檔案。"""
    import sqlite3
    return sqlite3.connect(f"file:{urllib.parse.quote(db_path)}?mode=ro", uri=True, timeout=5)


def _rustdesk_read_peers(db_path: str) -> tuple[dict, list]:
    """hbbs 的 peer 表 → [{id, created_at, ip}]。讀不到要回 ok=False 與原因（伺服器據此不清資料）。"""
    st: dict = {"path": db_path, "ok": False, "error": None, "truncated": False}
    if not os.path.isfile(db_path):
        st["error"] = "not found"
        return st, []
    try:
        con = _rustdesk_db_open(db_path)
        try:
            rows = con.execute("SELECT id, created_at, info FROM peer LIMIT ?",
                               (RUSTDESK_MAX_PEERS + 1,)).fetchall()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 — 原因原文帶回去，畫面上才看得出是權限還是格式
        st["error"] = f"{type(exc).__name__}: {exc}"
        return st, []
    if len(rows) > RUSTDESK_MAX_PEERS:
        rows, st["truncated"] = rows[:RUSTDESK_MAX_PEERS], True
    peers = []
    for pid, created, info in rows:
        pid = str(pid or "")
        if not _RUSTDESK_ID.match(pid):
            continue
        ip = None
        try:
            ip = _rustdesk_ip((json.loads(info or "{}") or {}).get("ip"))
        except (ValueError, TypeError, AttributeError):
            pass
        peers.append({"id": pid, "created_at": str(created) if created else None, "ip": ip})
    st["ok"] = True
    return st, peers


def _rustdesk_public_key(data_dir: str) -> str | None:
    try:
        with open(os.path.join(data_dir, "id_ed25519.pub"), encoding="ascii") as f:
            key = f.read(200).strip()
    except (OSError, UnicodeDecodeError):
        return None
    return key if _RUSTDESK_KEY.match(key) else None


def _rustdesk_online_port(data_dir: str) -> int:
    """OnlineRequest 走 hbbs 的「主埠 − 1」（預設 21116 → 21115）。主埠改過的話寫在工作目錄的 .env。"""
    port = 21116
    try:
        with open(os.path.join(data_dir, ".env"), encoding="utf-8") as f:
            for line in f:
                k, _, v = line.partition("=")
                if k.strip().upper() == "PORT" and v.strip().isdigit():
                    port = int(v.strip())
    except OSError:
        pass
    return port - 1 if 3 <= port <= 65535 else 21115


def _rustdesk_local_ip() -> str:
    """這台主機對外的位址。hbbs 把來自 loopback 的連線當成文字管理指令，查線上狀態不能連 127.0.0.1。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))         # UDP connect 只選路由，不送任何封包
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def _pb_varint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _pb_field(num: int, payload: bytes) -> bytes:
    return _pb_varint(num << 3 | 2) + _pb_varint(len(payload)) + payload


def _pb_read_varint(b: bytes, i: int) -> tuple[int, int]:
    shift = n = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        shift += 7
        if not c & 0x80:
            return n, i


def _hbb_frame(data: bytes) -> bytes:
    """hbb_common 的 BytesCodec：長度左移 2 位、低 2 位是標頭長度減一，little-endian。"""
    n = len(data)
    if n <= 0x3F:
        return bytes([n << 2]) + data
    if n <= 0x3FFF:
        return (n << 2 | 1).to_bytes(2, "little") + data
    if n <= 0x3FFFFF:
        return (n << 2 | 2).to_bytes(4, "little")[:3] + data
    return (n << 2 | 3).to_bytes(4, "little") + data


def _rustdesk_online_batch(ids: list, host: str, port: int) -> dict:
    req = _pb_field(1, b"jt-ipam") + b"".join(_pb_field(2, x.encode()) for x in ids)
    with socket.create_connection((host, port), timeout=5) as s:
        s.settimeout(10)
        s.sendall(_hbb_frame(_pb_field(23, req)))        # RendezvousMessage.online_request = 23
        buf = b""
        while len(buf) < 1 or len(buf) < (buf[0] & 3) + 1:
            chunk = s.recv(65536)
            if not chunk:
                raise ConnectionError("hbbs closed the connection")
            buf += chunk
        hl = (buf[0] & 3) + 1
        n = int.from_bytes(buf[:hl], "little") >> 2
        while len(buf) < hl + n:
            chunk = s.recv(65536)
            if not chunk:
                raise ConnectionError("hbbs closed the connection")
            buf += chunk
    body = buf[hl:hl + n]
    tag, i = _pb_read_varint(body, 0)
    if tag != (24 << 3 | 2):                              # online_response = 24
        raise ValueError(f"unexpected reply field {tag >> 3}")
    ln, i = _pb_read_varint(body, i)
    inner, states = body[i:i + ln], b""
    j = 0
    while j < len(inner):
        t, j = _pb_read_varint(inner, j)
        l2, j = _pb_read_varint(inner, j)
        if t == (1 << 3 | 2):
            states = inner[j:j + l2]
        j += l2
    return {x: bool(k // 8 < len(states) and states[k // 8] >> (7 - k % 8) & 1)
            for k, x in enumerate(ids)}


def _rustdesk_online(ids: list, host: str, port: int) -> tuple[dict, str | None]:
    """每個 ID 是否在線（hbbs：30 秒內註冊過）。失敗回 ({}, 原因)。"""
    out: dict = {}
    try:
        for k in range(0, len(ids), RUSTDESK_ONLINE_BATCH):
            out.update(_rustdesk_online_batch(ids[k:k + RUSTDESK_ONLINE_BATCH], host, port))
    except Exception as exc:  # noqa: BLE001
        return {}, f"{type(exc).__name__}: {exc}"
    return out, None


def _rustdesk_version() -> str | None:
    exe = shutil.which("hbbs") or "/usr/bin/hbbs"
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+\.\d+\.\d+[\w.-]*)", r.stdout or "")
    return m.group(1)[:32] if m else None


def _rustdesk_collect(data_dir: str) -> dict:
    db_st, peers = _rustdesk_read_peers(os.path.join(data_dir, "db_v2.sqlite3"))
    online, err = ({}, None)
    if peers:
        host = _rustdesk_local_ip()
        online, err = (_rustdesk_online([p["id"] for p in peers], host, _rustdesk_online_port(data_dir))
                       if host else ({}, "no non-loopback address"))
    for p in peers:
        p["online"] = online.get(p["id"]) if err is None else None
    return {
        "version": _rustdesk_version(),
        "public_key": _rustdesk_public_key(data_dir),
        "files": {"db": db_st},
        "online_ok": err is None,
        "online_error": err,
        "peers": peers,
    }


# ── 刪除舊註冊（1.2.0）────────────────────────────────────────────────────────
# hbbs 永遠留著每一個註冊過的 ID。兩邊都允許時，管理員可以在 jt-ipam 選舊的 ID 請代理刪掉：
# - jt-ipam：這台伺服器開了「允許刪除舊註冊」（預設關）→ 輪詢回應才會帶 peer_deletes
# - 這台主機：以 --allow-delete 重新安裝（設定檔有 JT_RD_ALLOW_DELETE=1；systemd 才讓 hbbs 目錄可寫，
#   私鑰檔 id_ed25519 對代理完全不可見）
#
# 界線：
# - 刪之前用 OnlineRequest 再問一次 hbbs：jt-ipam 看到的是上次回報時的狀態，這段時間可能剛好上線；上線中的不刪，
#   查不到線上狀態就整批不刪（不確定就不動）
# - 只刪 peer 表裡 id 相符的那一列，同一個交易；不碰其他表、不碰任何檔案
# - 讀寫用另一個開啟函式（_rustdesk_db_open_rw）：讀取一律走唯讀的 _rustdesk_db_open
RUSTDESK_DELETE_BATCH = 200
RUSTDESK_DELETE_BUSY_SECONDS = 10.0       # hbbs 正在寫入時最多等這麼久（SQLite busy timeout）
RUSTDESK_DELETE_UNSENT_MAX = 2000
_DELETE_NOT_ENABLED = "not enabled on this host (re-run the installer with --allow-delete)"


def _rustdesk_allow_delete() -> bool:
    """這台主機的管理員有沒有以 --allow-delete 安裝（每次都讀環境變數，測試可以切換）。"""
    return os.environ.get("JT_RD_ALLOW_DELETE", "").strip().lower() in ("1", "true", "yes")


def _rustdesk_delete_capability(data_dir: str) -> tuple[bool, str | None]:
    """代理現在寫不寫得了 hbbs 的資料庫：(可以嗎, 不行的原因)。資料庫檔與它的目錄都要可寫
    （SQLite 要在同一個目錄建立日誌檔）。systemd 唯讀掛載時 access() 回 EROFS，這裡就看得出來。"""
    if not _rustdesk_allow_delete():
        return False, _DELETE_NOT_ENABLED
    db = os.path.join(data_dir, "db_v2.sqlite3")
    if not os.path.isfile(db):
        return False, f"{db}: not found"
    if not os.access(db, os.W_OK):
        return False, f"{db}: read-only for the agent"
    if not os.access(data_dir, os.W_OK):
        return False, f"{data_dir}: directory read-only for the agent (SQLite needs it for its journal)"
    return True, None


def _rustdesk_db_open_rw(db_path: str):  # noqa: ANN202 — sqlite3 在用到時才載入
    """只給「刪除舊註冊」用：讀寫模式（mode=rw，檔案不存在不會建立），hbbs 正在寫入時等它最多
    RUSTDESK_DELETE_BUSY_SECONDS 秒。isolation_level=None：交易由呼叫端明確 BEGIN／COMMIT。"""
    import sqlite3
    return sqlite3.connect(f"file:{urllib.parse.quote(db_path)}?mode=rw", uri=True,
                           timeout=RUSTDESK_DELETE_BUSY_SECONDS, isolation_level=None)


def _rustdesk_delete_peers(data_dir: str, ids: list) -> dict:
    """刪掉這些 ID 的註冊 → {ID: (結果, 說明)}；結果是 deleted／skipped_online／not_found／failed。"""
    ok, why = _rustdesk_delete_capability(data_dir)
    if not ok:
        return {i: ("failed", why) for i in ids}
    host = _rustdesk_local_ip()
    online, err = (_rustdesk_online(ids, host, _rustdesk_online_port(data_dir)) if host
                   else ({}, "no non-loopback address"))
    if err is not None:
        return {i: ("failed", f"online check failed, nothing deleted: {err}") for i in ids}
    out: dict = {i: ("skipped_online", None) for i in ids if online.get(i)}
    todo = [i for i in ids if not online.get(i)]
    if not todo:
        return out
    try:
        con = _rustdesk_db_open_rw(os.path.join(data_dir, "db_v2.sqlite3"))
        try:
            con.execute("BEGIN IMMEDIATE")          # 先拿寫入鎖（hbbs 在寫就等 busy timeout）
            try:
                res = {i: ("deleted", None) if con.execute("DELETE FROM peer WHERE id = ?", (i,)).rowcount
                       else ("not_found", None) for i in todo}
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 — 原因原文帶回去（鎖住、權限、格式）；交易還原，一筆都沒刪
        why = f"{type(exc).__name__}: {exc}"[:500]
        return {**out, **{i: ("failed", why) for i in todo}}
    _rdapi_forget(todo)
    return {**out, **res}


_DELETE_STATE = {"running": False}
_DELETE_LOCK = threading.Lock()
_DELETE_UNSENT: dict = {}           # req_id → 結果（已經做了但沒送到；下次輪詢再送，不重做）


def _rustdesk_delete_jobs(cfg) -> list:  # noqa: ANN001
    """輪詢回應的 peer_deletes → [(req_id, ID)]；格式不對的略過（伺服器給的內容也要自己驗一次）。"""
    jobs = cfg.get("peer_deletes") if isinstance(cfg, dict) else None
    out: list = []
    for j in jobs if isinstance(jobs, list) else []:
        if not isinstance(j, dict):
            continue
        rid, pid = j.get("req_id"), j.get("rustdesk_id")
        if isinstance(rid, str) and 0 < len(rid) <= 64 and isinstance(pid, str) and _RUSTDESK_ID.match(pid):
            out.append((rid, pid))
    return out[:RUSTDESK_DELETE_BATCH]


def _rustdesk_delete_once(source_id: str, jobs: list) -> None:
    try:
        with _DELETE_LOCK:
            done = {rid: _DELETE_UNSENT[rid] for rid, _pid in jobs if rid in _DELETE_UNSENT}
        todo = [(rid, pid) for rid, pid in jobs if rid not in done]
        if todo:
            res = _rustdesk_delete_peers(_rustdesk_dir(), list(dict.fromkeys(pid for _rid, pid in todo)))
            for rid, pid in todo:
                st, detail = res.get(pid, ("failed", "no result"))
                done[rid] = {"req_id": rid, "status": st, "detail": str(detail)[:500] if detail else None}
        results = [done[rid] for rid, _pid in jobs if rid in done]
        try:
            r = _req("POST", f"{API_BASE}/delete-result", {"source_id": source_id, "results": results}, timeout=30)
        except Exception as exc:  # noqa: BLE001 — 結果留著下次輪詢再送；不重做（已經刪掉的再刪會變成「找不到」）
            with _DELETE_LOCK:
                if len(_DELETE_UNSENT) + len(results) > RUSTDESK_DELETE_UNSENT_MAX:
                    _DELETE_UNSENT.clear()
                _DELETE_UNSENT.update({x["req_id"]: x for x in results})
            print(f"[delete] result not delivered, kept for the next poll: {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)
            return
        with _DELETE_LOCK:
            for x in results:
                _DELETE_UNSENT.pop(x["req_id"], None)
        counts: dict = {}
        for x in results:
            counts[x["status"]] = counts.get(x["status"], 0) + 1
        print(f"[delete] {counts} -> {r.get('status', 'ok')}", flush=True)
    except Exception as exc:  # noqa: BLE001 — 下一輪再試
        print(f"[delete] failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    finally:
        with _DELETE_LOCK:
            _DELETE_STATE["running"] = False


def _rustdesk_maybe_delete(cfg) -> None:  # noqa: ANN001
    """輪詢回應帶了刪除工作就在背景做（不拖慢輪詢）；正在做就等下一輪（結果送到前伺服器每次都會再帶）。"""
    jobs = _rustdesk_delete_jobs(cfg)
    if not jobs or not cfg.get("source_id"):
        return
    with _DELETE_LOCK:
        if _DELETE_STATE["running"]:
            return
        _DELETE_STATE["running"] = True
    threading.Thread(target=_rustdesk_delete_once, args=(str(cfg["source_id"]), jobs),
                     name="jt-ipam-rustdesk-delete", daemon=True).start()


_RUSTDESK_STATE = {"last": 0.0, "running": False}
_RUSTDESK_LOCK = threading.Lock()


def _rustdesk_report_once(source_id: str) -> None:
    try:
        data = _rustdesk_collect(_rustdesk_dir())
        data["source_id"] = source_id
        r = _req("POST", f"{API_BASE}/report", data, timeout=120)
        print(f"[rustdesk] peers={len(data['peers'])} db_ok={data['files']['db']['ok']} "
              f"online_ok={data['online_ok']} -> {r.get('status', 'ok')}", flush=True)
    except Exception as exc:  # noqa: BLE001 — 下一輪再試
        print(f"[rustdesk] report failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    finally:
        with _RUSTDESK_LOCK:
            _RUSTDESK_STATE["running"] = False


def _rustdesk_maybe_report(cfg, now: float, force: bool = False) -> None:  # noqa: ANN001
    """照回報間隔讀一次（背景執行）；網頁上按「立即同步」時 force=True 不等間隔。正在讀就不重疊。"""
    if not isinstance(cfg, dict) or not cfg.get("source_id"):
        return
    interval = max(60, int(cfg.get("interval_seconds") or 300))
    with _RUSTDESK_LOCK:
        if _RUSTDESK_STATE["running"] or (not force and now - _RUSTDESK_STATE["last"] < interval):
            return
        _RUSTDESK_STATE["running"], _RUSTDESK_STATE["last"] = True, now
    threading.Thread(target=_rustdesk_report_once, args=(str(cfg["source_id"]),),
                     name="jt-ipam-rustdesk", daemon=True).start()


# ── RustDesk 客戶端回報的接收端（API 伺服器子集，docs/SPEC_RUSTDESK_API_zh-TW.md）──────────
# 客戶端只設了 ID 伺服器、沒填 API 伺服器時，會把心跳、系統資訊、連線／檔案傳輸稽核與告警 POST 到
# http://<ID 伺服器>:21114。代理在 RustDesk 主機上聽這個埠，驗證後排入佇列、定時轉給 jt-ipam。
#
# 界線：
# - 只有 jt-ipam 指派了 RustDesk 伺服器、且在網頁上開啟接收時才聽埠；停用就完全不聽
#   （讓客戶端照舊連不上，而不是收下丟掉）
# - 客戶端不帶憑證，誰都能宣稱自己是某個 ID：帶 uuid 的請求要與 hbbs 資料庫裡同一個 ID 的 uuid 完全相同
#   才接受。uuid 只在本機比對：不轉送、不寫日誌、不存（快取只留雜湊）
# - 回應絕不含 disconnect／modified_at／strategy（會中斷連線、改客戶端設定）：心跳一律回 {}
# - 本文上限 64 KB、10 秒內讀完、每個來源 IP 每秒 20 個請求；JSON 壞掉或欄位型別不對回 400
# - 轉送的每個欄位都先截斷、清掉 NUL 與孤立的代理字元：一筆 jt-ipam 收不下的資料會讓整批一直被拒
RDAPI_DEFAULT_PORT = 21114
RDAPI_LISTEN_HOST = "0.0.0.0"  # noqa: S104 — 要收得到內網所有客戶端；只有網頁上開啟時才聽
RDAPI_MAX_BODY = 64 * 1024
RDAPI_SOCKET_TIMEOUT = 10.0
RDAPI_RATE_PER_SEC = 20
RDAPI_MAX_CONN = 256               # 同時處理中的連線（慢速連線不可以把執行緒吃光）
RDAPI_MAX_CONN_PER_IP = 16
RDAPI_VERIFY_TTL = 60.0            # 驗證結果的快取（避免每個心跳都開資料庫）
RDAPI_MISS_TTL = 15.0              # 「資料庫沒有這個 ID」的快取短一點：新註冊的裝置很快就認得
RDAPI_CACHE_MAX = 20000
RDAPI_QUEUE_CAP = 5000
RDAPI_FLUSH_SECONDS = 5.0
RDAPI_POLL_SECONDS = 0.5            # HTTP 伺服器迴圈的週期（看門狗、停止的反應時間）
RDAPI_FLUSH_BATCH = 200
RDAPI_NONCE_TTL = 300.0
RDAPI_NONCE_MAX = 200000
RDAPI_EVENTS_PATH = f"{API_BASE}/events"
_RDAPI_PATHS = {
    "/api/heartbeat": "heartbeat",
    "/api/sysinfo": "sysinfo",
    "/api/audit/conn": "conn",
    "/api/audit/file": "file",
    "/api/audit/alarm": "alarm",
}
_RDAPI_DROP_KEYS = ("unverified", "rate_limited", "bad_request", "queue_overflow")
_RDAPI_ALARM_INFO_KEYS = ("ip", "id", "name", "message", "conn_type")
_RDAPI_TEXT = "text/plain; charset=utf-8"
_RDAPI_JSON = "application/json"
# 轉送前的字串長度（規格 §6.5；規格沒列的比照 jt-ipam 端欄位長度）
_RD_LIM_ID = 100
_RD_LIM_NAME = 255
_RD_LIM_PATH = 1024
_RD_LIM_INFO = 512
_RD_LIM_IP = 64
_RD_LIM_NONCE = 64
_RD_LIM_VERSION = 64
_RD_LIM_REF = 100
_RD_LIM_NOTE = 2048
_RD_I64 = (-(1 << 63), (1 << 63) - 1)
_RD_SESSION = (-(1 << 63), (1 << 64) - 1)       # session_id 是 64 位元，可能是無號的

_RDAPI: dict = {"server": None, "thread": None, "port": None, "source_id": None,
                "fwd": None, "fwd_stop": None}
_RDAPI_LIFE = threading.Lock()     # 啟動／停止
_RDAPI_LOCK = threading.Lock()     # 佇列、計數、去重、限流、快取（只做記憶體操作，不可在持有時做 I/O）
_RDAPI_QUEUE: dict = {}            # 依到達順序；心跳的鍵是 ("hb", ID)，同一個 ID 只留最後一筆
_RDAPI_SEQ = itertools.count()
_RDAPI_DROPPED: dict = dict.fromkeys(_RDAPI_DROP_KEYS, 0)
_RDAPI_NONCES: dict = {}           # nonce → 到期時間（monotonic），依時間先後排
_RDAPI_RATE: dict = {}             # 來源 IP → [這一秒的起點, 次數]
_RDAPI_CACHE: dict = {}            # ID → (到期時間, 資料庫有沒有這個 ID, uuid 的 sha256)
_RDAPI_WARNED: dict = {}           # 日誌節流：鍵 → 上次印出的時間
_RDAPI_WAKE = threading.Event()    # 佇列累積到 RDAPI_FLUSH_BATCH 筆時叫醒轉送執行緒
_RDAPI_BIND_ERROR: list = [None]   # 最近一次綁不上埠的原因（成功或停用時清掉）；輪詢時回報給 jt-ipam


class _RdBad(ValueError):
    """欄位缺少或型別不對（回 400）。"""


def _rdapi_warn(key: str, msg: str, every: float = 60.0) -> None:
    """同一類問題每分鐘最多印一次（客戶端每幾秒就送一次，不可以把日誌灌爆）。訊息裡絕不放 uuid。"""
    now = time.monotonic()
    with _RDAPI_LOCK:
        last = _RDAPI_WARNED.get(key)
        if last is not None and now - last < every:
            return
        _RDAPI_WARNED[key] = now
    print(msg, file=sys.stderr, flush=True)


def _rdapi_count(key: str) -> None:
    with _RDAPI_LOCK:
        _RDAPI_DROPPED[key] = _RDAPI_DROPPED.get(key, 0) + 1


def _rd_is_int(v, lim: tuple = _RD_I64) -> bool:  # noqa: ANN001
    return isinstance(v, int) and not isinstance(v, bool) and lim[0] <= v <= lim[1]


def _rd_field(d: dict, key: str, kind: str, required: bool = False, lim: tuple = _RD_I64):  # noqa: ANN202
    """取一個欄位並檢查型別。不存在（或 null）時：必要欄位丟 _RdBad，選用欄位回 None。"""
    v = d.get(key)
    if v is None:
        if required:
            raise _RdBad(key)
        return None
    ok = (isinstance(v, str) if kind == "str" else
          isinstance(v, bool) if kind == "bool" else
          _rd_is_int(v, lim))
    if not ok:
        raise _RdBad(key)
    return v


def _rd_text(v, limit: int) -> str | None:  # noqa: ANN001
    """截斷並清掉 NUL 與孤立的代理字元（jt-ipam 端存不進去，整批會一直被拒）。"""
    if not isinstance(v, str):
        return None
    return v[:limit].replace("\x00", "").encode("utf-8", "replace").decode("utf-8")


def _rd_json_obj(v) -> dict | None:  # noqa: ANN001
    """檔案傳輸／告警的 info 是一段 JSON 文字，要再解析一次；不是物件就當作沒有。"""
    if not isinstance(v, str):
        return None
    try:
        out = json.loads(v)
    except (ValueError, RecursionError):
        return None
    return out if isinstance(out, dict) else None


def _rdapi_lookup(rid: str) -> tuple[bool, bytes | None] | None:
    """hbbs 資料庫有沒有這個 ID、它的 uuid 的 sha256（快取 RDAPI_VERIFY_TTL 秒）。
    讀不到資料庫回 None 且不快取 —— 驗證一律當作不通過，不可以變成「全部放行」。"""
    now = time.monotonic()
    with _RDAPI_LOCK:
        hit = _RDAPI_CACHE.get(rid)
        if hit is not None and hit[0] > now:
            return hit[1], hit[2]
    db_path = os.path.join(_rustdesk_dir(), "db_v2.sqlite3")
    if not os.path.isfile(db_path):
        _rdapi_warn("db", f"[rustdesk-api] hbbs database not found: {db_path}")
        return None
    try:
        con = _rustdesk_db_open(db_path)
        try:
            row = con.execute("SELECT uuid FROM peer WHERE id = ? LIMIT 1", (rid,)).fetchone()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 — 原因原文印出來（sqlite 的訊息不含查詢參數）
        _rdapi_warn("db", f"[rustdesk-api] hbbs database unreadable: {type(exc).__name__}: {exc}")
        return None
    raw = row[0] if row else None
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    elif isinstance(raw, str):
        raw = raw.encode("utf-8")
    digest = hashlib.sha256(raw).digest() if isinstance(raw, (bytes, bytearray)) and raw else None
    found = row is not None
    with _RDAPI_LOCK:
        if len(_RDAPI_CACHE) >= RDAPI_CACHE_MAX:
            for k in [k for k, v in _RDAPI_CACHE.items() if v[0] <= now]:
                del _RDAPI_CACHE[k]
            if len(_RDAPI_CACHE) >= RDAPI_CACHE_MAX:
                _RDAPI_CACHE.clear()
        _RDAPI_CACHE[rid] = (now + (RDAPI_VERIFY_TTL if found else RDAPI_MISS_TTL), found, digest)
    return found, digest


def _rdapi_forget(ids: list) -> None:
    """刪掉註冊後清掉這些 ID 的驗證快取（否則快取到期前還會接受它們的回報）。"""
    with _RDAPI_LOCK:
        for rid in ids:
            _RDAPI_CACHE.pop(rid, None)


def _rdapi_known(rid: str) -> bool:
    """ID 格式合格且存在於 hbbs 資料庫（給沒有 uuid 的備註用）。"""
    if not _RUSTDESK_ID.match(rid):
        return False
    hit = _rdapi_lookup(rid)
    return bool(hit and hit[0])


def _rdapi_verify(rid: str, uuid_b64: str) -> bool:
    """uuid（標準 base64、含補位）解碼後與 hbbs 資料庫同一個 ID 的 uuid 原始位元組完全相同才通過。
    ID 格式不合（jt-ipam 收不下、hbbs 的裝置清單也不會有）一律不通過。"""
    if not _RUSTDESK_ID.match(rid):
        return False
    try:
        raw = base64.b64decode(uuid_b64, validate=True)
    except ValueError:                  # binascii.Error 是 ValueError 的子類別；非 ASCII 也是 ValueError
        return False
    if not raw:
        return False
    hit = _rdapi_lookup(rid)
    if not hit or not hit[0] or hit[1] is None:
        return False
    return hmac.compare_digest(hashlib.sha256(raw).digest(), hit[1])


def _rdapi_rate_ok(ip: str) -> bool:
    """同一個來源 IP 每秒最多 RDAPI_RATE_PER_SEC 個請求（被擋的也算，持續灌就持續擋）。"""
    now = time.monotonic()
    with _RDAPI_LOCK:
        w = _RDAPI_RATE.get(ip)
        if w is None or now - w[0] >= 1.0:
            w = _RDAPI_RATE[ip] = [now, 0]
        w[1] += 1
        return w[1] <= RDAPI_RATE_PER_SEC


def _rdapi_housekeeping(now: float) -> None:
    """清掉過期的限流視窗、nonce、驗證快取（由 HTTP 伺服器的迴圈每幾秒呼叫一次）。"""
    with _RDAPI_LOCK:
        for k in [k for k, w in _RDAPI_RATE.items() if now - w[0] >= 2.0]:
            del _RDAPI_RATE[k]
        while _RDAPI_NONCES:
            k = next(iter(_RDAPI_NONCES))
            if _RDAPI_NONCES[k] > now:
                break
            del _RDAPI_NONCES[k]
        for k in [k for k, v in _RDAPI_CACHE.items() if v[0] <= now]:
            del _RDAPI_CACHE[k]


def _rdapi_enqueue(ev: dict) -> None:
    """排入轉送佇列。心跳同一個 ID 只留最後一筆；超過 RDAPI_QUEUE_CAP 丟最舊的並計數。"""
    with _RDAPI_LOCK:
        if ev.get("kind") == "heartbeat":
            key: tuple = ("hb", ev.get("id"))
            _RDAPI_QUEUE.pop(key, None)
        else:
            key = ("ev", next(_RDAPI_SEQ))
        while _RDAPI_QUEUE and len(_RDAPI_QUEUE) >= max(1, RDAPI_QUEUE_CAP):
            del _RDAPI_QUEUE[next(iter(_RDAPI_QUEUE))]
            _RDAPI_DROPPED["queue_overflow"] += 1
        _RDAPI_QUEUE[key] = ev
        full = len(_RDAPI_QUEUE) >= RDAPI_FLUSH_BATCH
    if full:
        _RDAPI_WAKE.set()


def _rdapi_enqueue_once(ev: dict) -> bool:
    """稽核以 nonce 去重（客戶端重送時沿用同一個 nonce），記憶體保留 RDAPI_NONCE_TTL 秒。"""
    nonce = ev["nonce"]
    now = time.monotonic()
    with _RDAPI_LOCK:
        exp = _RDAPI_NONCES.get(nonce)
        if exp is not None and exp > now:
            return False
        _RDAPI_NONCES.pop(nonce, None)              # 重新插入到最後，維持依時間先後
        while _RDAPI_NONCES and len(_RDAPI_NONCES) >= RDAPI_NONCE_MAX:
            del _RDAPI_NONCES[next(iter(_RDAPI_NONCES))]
        _RDAPI_NONCES[nonce] = now + RDAPI_NONCE_TTL
    _rdapi_enqueue(ev)
    return True


def _rdapi_audit_head(d: dict) -> tuple[str, str, str]:
    """稽核共同的必要欄位：id、uuid、nonce（nonce 是去重的依據，空字串不收）。"""
    rid = _rd_field(d, "id", "str", required=True)
    u = _rd_field(d, "uuid", "str", required=True)
    nonce = _rd_text(_rd_field(d, "nonce", "str", required=True), _RD_LIM_NONCE)
    if not nonce:
        raise _RdBad("nonce")
    return rid, u, nonce


def _rdapi_accept_audit(rid: str, u: str, ev: dict) -> tuple[int, bytes, str | None]:
    """稽核驗證不過也回 200（客戶端才不會一直重送），不轉送、計入 unverified。"""
    if _rdapi_verify(rid, u):
        _rdapi_enqueue_once(ev)
    else:
        _rdapi_count("unverified")
    return 200, b"", None


def _rdapi_heartbeat(d: dict, base: dict) -> tuple[int, bytes, str | None]:
    rid = _rd_field(d, "id", "str", required=True)
    u = _rd_field(d, "uuid", "str", required=True)
    ver = _rd_field(d, "ver", "int")
    _rd_field(d, "modified_at", "int")
    conns = d.get("conns")
    if conns is not None and not (isinstance(conns, list) and all(_rd_is_int(c) for c in conns)):
        raise _RdBad("conns")
    if _rdapi_verify(rid, u):
        _rdapi_enqueue({**base, "id": _rd_text(rid, _RD_LIM_ID), "ver": ver, "conns": len(conns or ())})
    else:
        _rdapi_count("unverified")      # 驗證失敗也回 {}：不洩漏「這個 ID 存不存在」
    return 200, b"{}", _RDAPI_JSON


def _rdapi_sysinfo(d: dict, base: dict) -> tuple[int, bytes, str | None]:
    rid = _rd_field(d, "id", "str", required=True)
    u = _rd_field(d, "uuid", "str", required=True)
    v = {k: _rd_field(d, k, "str") for k in ("version", "hostname", "username", "os", "cpu", "memory")}
    if not _rdapi_verify(rid, u):
        _rdapi_count("unverified")
        return 200, b"ID_NOT_FOUND", _RDAPI_TEXT
    # preset-* 等其他鍵（管理員預先寫進客戶端的選項）一律不轉送
    _rdapi_enqueue({**base, "id": _rd_text(rid, _RD_LIM_ID),
                    "hostname": _rd_text(v["hostname"], _RD_LIM_NAME),
                    "username": _rd_text(v["username"], _RD_LIM_NAME),
                    "os": _rd_text(v["os"], _RD_LIM_NAME),
                    "cpu": _rd_text(v["cpu"], _RD_LIM_NAME),
                    "memory": _rd_text(v["memory"], _RD_LIM_NAME),
                    "version": _rd_text(v["version"], _RD_LIM_VERSION)})
    return 200, b"SYSINFO_UPDATED", _RDAPI_TEXT


def _rdapi_conn(d: dict, base: dict) -> tuple[int, bytes, str | None]:
    if "note" in d and d.get("uuid") is None:
        # 控制端的備註：沒有 uuid 無法驗證，只在 ID 存在於 hbbs 時接受，標示未驗證
        rid = _rd_field(d, "id", "str", required=True)
        sid = _rd_field(d, "session_id", "int", required=True, lim=_RD_SESSION)
        note = _rd_field(d, "note", "str", required=True)
        if not _rdapi_known(rid):
            _rdapi_count("unverified")
            return 200, b"", None
        _rdapi_enqueue({**base, "kind": "note", "id": _rd_text(rid, _RD_LIM_ID), "session_id": str(sid),
                        "note": _rd_text(note, _RD_LIM_NOTE), "verified": False})
        return 200, b"", None
    rid, u, nonce = _rdapi_audit_head(d)
    conn_id = _rd_field(d, "conn_id", "int", required=True)
    sid = _rd_field(d, "session_id", "int", required=True, lim=_RD_SESSION)
    action = _rd_field(d, "action", "str")
    ev = {**base, "id": _rd_text(rid, _RD_LIM_ID), "nonce": nonce, "conn_id": conn_id,
          "session_id": str(sid), "action": None, "ip": None, "peer_id": None, "peer_name": None,
          "type": None, "primary_auth": None, "two_factor": None, "conn_audit_ref": None}
    if action is None:
        # 第二種（驗證通過）沒有 action；peer 是 [對方 ID, 對方名稱]
        peer = d.get("peer")
        if not (isinstance(peer, list) and len(peer) == 2 and all(isinstance(x, str) for x in peer)):
            raise _RdBad("peer")
        ev.update(action="auth", peer_id=_rd_text(peer[0], _RD_LIM_ID),
                  peer_name=_rd_text(peer[1], _RD_LIM_NAME), type=_rd_field(d, "type", "int"),
                  primary_auth=_rd_field(d, "primary_auth", "int"),
                  two_factor=_rd_field(d, "two_factor", "int"))
    elif action == "new":
        ev.update(action="new", ip=_rd_text(_rd_field(d, "ip", "str"), _RD_LIM_IP),
                  conn_audit_ref=_rd_text(_rd_field(d, "conn_audit_ref", "str"), _RD_LIM_REF))
    elif action == "close":
        ev["action"] = "close"
    else:
        raise _RdBad("action")
    return _rdapi_accept_audit(rid, u, ev)


def _rdapi_file(d: dict, base: dict) -> tuple[int, bytes, str | None]:
    rid, u, nonce = _rdapi_audit_head(d)
    conn_id = _rd_field(d, "conn_id", "int")
    peer_id = _rd_field(d, "peer_id", "str")
    typ = _rd_field(d, "type", "int")
    path = _rd_field(d, "path", "str")
    is_file = _rd_field(d, "is_file", "bool")
    info = _rd_json_obj(_rd_field(d, "info", "str")) or {}
    files = info.get("files")
    out_files = None
    if isinstance(files, list):
        out_files = [[_rd_text(f[0], _RD_LIM_INFO), f[1]] for f in files
                     if isinstance(f, list) and len(f) == 2 and isinstance(f[0], str) and _rd_is_int(f[1])][:10]
    num = info.get("num")
    ev = {**base, "id": _rd_text(rid, _RD_LIM_ID), "nonce": nonce, "conn_id": conn_id,
          "peer_id": _rd_text(peer_id, _RD_LIM_ID), "type": typ, "path": _rd_text(path, _RD_LIM_PATH),
          "is_file": is_file, "ip": _rd_text(info.get("ip"), _RD_LIM_IP),
          "peer_name": _rd_text(info.get("name"), _RD_LIM_NAME),
          "num": num if _rd_is_int(num) else None, "files": out_files}
    return _rdapi_accept_audit(rid, u, ev)


def _rdapi_alarm(d: dict, base: dict) -> tuple[int, bytes, str | None]:
    rid, u, nonce = _rdapi_audit_head(d)
    typ = _rd_field(d, "typ", "int", required=True)
    conn_id = _rd_field(d, "conn_id", "int")
    ref = _rd_field(d, "conn_audit_ref", "str")
    info = _rd_json_obj(_rd_field(d, "info", "str")) or {}
    out: dict = {}
    for k in _RDAPI_ALARM_INFO_KEYS:                # 只轉送文件列出的鍵；巢狀物件、浮點數不轉送
        v = info.get(k)
        if isinstance(v, str):
            out[k] = _rd_text(v, _RD_LIM_INFO)
        elif isinstance(v, bool) or _rd_is_int(v):
            out[k] = v
    ev = {**base, "id": _rd_text(rid, _RD_LIM_ID), "nonce": nonce, "conn_id": conn_id, "typ": typ,
          "info": out, "conn_audit_ref": _rd_text(ref, _RD_LIM_REF)}
    return _rdapi_accept_audit(rid, u, ev)


_RDAPI_KINDS = {"heartbeat": _rdapi_heartbeat, "sysinfo": _rdapi_sysinfo, "conn": _rdapi_conn,
                "file": _rdapi_file, "alarm": _rdapi_alarm}


def _rdapi_process(kind: str, d: dict, src_ip: str) -> tuple[int, bytes, str | None]:
    """一個已解析的請求 → (狀態碼, 本文, Content-Type)。欄位不對丟 _RdBad。"""
    base = {"kind": kind, "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "src_ip": src_ip}
    return _RDAPI_KINDS[kind](d, base)


def _rdapi_request(h) -> tuple[int | None, bytes, str | None]:  # noqa: ANN001 — _RdapiHandler
    """處理一個請求。回 (None, …) 表示連線已經不能用（沒送完、逾時），不回應直接關閉。"""
    ip = str(h.client_address[0]) if h.client_address else ""
    allowed = _rdapi_rate_ok(ip)
    kind = _RDAPI_PATHS.get(h.path.split("?", 1)[0])
    # 本文長度：沒有 Content-Length 當作空本文；分塊傳送、多個不一致、不是數字 → 400；超過上限 → 413
    err = None
    n = 0
    lens = [x.strip() for x in (h.headers.get_all("Content-Length") or [])]
    if (h.headers.get("Transfer-Encoding") or len(set(lens)) > 1
            or (lens and not re.fullmatch(r"[0-9]{1,12}", lens[0]))):
        err = 400
    elif lens:
        n = int(lens[0])
        if n > RDAPI_MAX_BODY:
            err = 413
    # 先讀完本文再回應：沒讀的資料留在接收緩衝區，關閉時會變成 RST，客戶端可能收不到 429／404，
    # 而把它當成連線錯誤一直重送。讀取受 10 秒時限約束（伺服器端的看門狗會切斷超時的連線）
    body = b""
    if err is None and n:
        try:
            body = h.rfile.read(n)
        except OSError:
            return None, b"", None
        if len(body) < n:
            return None, b"", None
    h.server.rd_done_reading(h.request)
    if not allowed:
        _rdapi_count("rate_limited")
        return 429, b"", None
    if h.command != "POST" or kind is None:
        return 404, b"", None
    if err is not None:
        _rdapi_count("bad_request")
        return err, b"", None
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        data = None
    if not isinstance(data, dict):
        _rdapi_count("bad_request")
        return 400, b"", None
    try:
        return _rdapi_process(kind, data, ip)
    except _RdBad:
        _rdapi_count("bad_request")
        return 400, b"", None


class _RdapiHandler(http.server.BaseHTTPRequestHandler):
    """RustDesk 客戶端的回報。只收 _RDAPI_PATHS 的 POST；其他方法、路徑一律 404、空本文。"""

    protocol_version = "HTTP/1.1"       # 狀態列用 1.1；每個回應都帶 Connection: close
    server_version = "jt-ipam-agent"
    sys_version = ""

    def setup(self) -> None:
        self.timeout = RDAPI_SOCKET_TIMEOUT
        super().setup()

    def log_message(self, fmt, *args) -> None:  # noqa: ANN001, ANN002
        """不記每個請求：心跳很頻繁，而且路徑、查詢字串是外部可控的文字。"""

    def send_error(self, code, message=None, explain=None) -> None:  # noqa: ANN001
        # 協定層錯誤（請求列太長、標頭壞掉…）也只回狀態碼，不回 HTML、不回顯對方送來的內容
        self._rd_reply(code, b"", None)

    def __getattr__(self, name: str):  # noqa: ANN204
        # GET／PUT／DELETE…（以及任何奇怪的方法名稱）都走同一個處理：一律 404，而不是預設的 501
        if name.startswith("do_"):
            return self.do_POST
        raise AttributeError(name)

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler 的命名慣例
        try:
            code, body, ctype = _rdapi_request(self)
        except Exception as exc:  # noqa: BLE001 — 只印例外類型：訊息可能帶到請求內容
            _rdapi_warn("handler", f"[rustdesk-api] request failed: {type(exc).__name__}")
            code, body, ctype = 500, b"", None
        if code is None:
            self.close_connection = True
            return
        self._rd_reply(code, body, ctype)

    def _rd_reply(self, code: int, body: bytes, ctype: str | None) -> None:
        self.close_connection = True
        try:
            self.send_response(code)
            if ctype:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)
        except OSError:
            pass


class _RdapiServer(http.server.ThreadingHTTPServer):
    """每個連線一個 daemon 執行緒；同時處理中的連線有上限；看門狗切斷 RDAPI_SOCKET_TIMEOUT 秒內
    還沒送完請求的連線（逐次 recv 的逾時擋不住一次送一個位元組的慢速連線）。"""

    daemon_threads = True
    block_on_close = False              # 停止時不等還在跑的連線（看門狗與 socket 逾時會收掉它們）
    request_queue_size = 64

    def __init__(self, addr: tuple) -> None:
        self.rd_active: dict = {}       # socket → [來源 IP, 讀取期限（讀完後為 None）]
        self.rd_lock = threading.Lock()
        self.rd_next_housekeeping = 0.0
        super().__init__(addr, _RdapiHandler)

    def server_bind(self) -> None:
        # 不用 HTTPServer.server_bind：它會對聽的位址做 getfqdn（反查 DNS，可能卡住）
        socketserver.TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])

    def verify_request(self, request, client_address) -> bool:  # noqa: ANN001
        ip = str(client_address[0]) if client_address else ""
        with self.rd_lock:
            per_ip = sum(1 for v in self.rd_active.values() if v[0] == ip)
            ok = len(self.rd_active) < RDAPI_MAX_CONN and per_ip < RDAPI_MAX_CONN_PER_IP
            if ok:
                self.rd_active[request] = [ip, time.monotonic() + RDAPI_SOCKET_TIMEOUT]
        if not ok:
            _rdapi_count("rate_limited")
        return ok

    def shutdown_request(self, request) -> None:  # noqa: ANN001
        with self.rd_lock:
            self.rd_active.pop(request, None)
        super().shutdown_request(request)

    def rd_done_reading(self, request) -> None:  # noqa: ANN001
        with self.rd_lock:
            v = self.rd_active.get(request)
            if v is not None:
                v[1] = None

    def rd_cut(self, every: bool = False) -> None:
        now = time.monotonic()
        with self.rd_lock:
            late = [s for s, v in self.rd_active.items() if every or (v[1] is not None and v[1] <= now)]
            for s in late:
                self.rd_active[s][1] = None
        for s in late:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def service_actions(self) -> None:
        # serve_forever 每一輪（最多 poll_interval 秒）都會呼叫
        self.rd_cut()
        now = time.monotonic()
        if now >= self.rd_next_housekeeping:
            self.rd_next_housekeeping = now + 5.0
            _rdapi_housekeeping(now)

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001
        exc = sys.exc_info()[1]
        if not isinstance(exc, OSError):    # 對方斷線、重設是常態，不記
            _rdapi_warn("conn", f"[rustdesk-api] connection error: {type(exc).__name__}")


def _rdapi_flush() -> bool:
    """把佇列送給 jt-ipam（每次最多 RDAPI_FLUSH_BATCH 筆，送完為止）。
    失敗保留佇列與計數，下一輪再送；計數只算上次成功送出之後的。"""
    while True:
        sid = _RDAPI.get("source_id")
        if not sid:
            return False
        with _RDAPI_LOCK:
            batch = list(itertools.islice(_RDAPI_QUEUE.items(), max(1, RDAPI_FLUSH_BATCH)))
            dropped = {k: int(_RDAPI_DROPPED.get(k, 0)) for k in _RDAPI_DROP_KEYS}
        if not batch and not any(dropped.values()):
            return True
        payload = {"source_id": str(sid), "dropped": dropped, "events": [ev for _k, ev in batch]}
        try:
            _req("POST", RDAPI_EVENTS_PATH, payload, timeout=30)
        except Exception as exc:  # noqa: BLE001 — 保留佇列，下一輪再送
            _rdapi_warn("forward", f"[rustdesk-api] forward failed, {len(batch)} events kept for the "
                                   f"next try: {type(exc).__name__}: {exc}")
            return False
        with _RDAPI_LOCK:
            for k, ev in batch:
                if _RDAPI_QUEUE.get(k) is ev:       # 送出期間被同 ID 的新心跳取代的，留著下次送
                    del _RDAPI_QUEUE[k]
            for k, c in dropped.items():
                _RDAPI_DROPPED[k] = max(0, _RDAPI_DROPPED.get(k, 0) - c)
            more = bool(_RDAPI_QUEUE)
        if not more or len(batch) < RDAPI_FLUSH_BATCH:
            return True


def _rdapi_forward_loop(stop: threading.Event) -> None:
    """每 RDAPI_FLUSH_SECONDS 秒（或佇列累積到 RDAPI_FLUSH_BATCH 筆時）轉送一次。"""
    while not stop.is_set():
        _RDAPI_WAKE.wait(RDAPI_FLUSH_SECONDS)
        _RDAPI_WAKE.clear()
        if stop.is_set():
            return
        try:
            ok = _rdapi_flush()
        except Exception as exc:  # noqa: BLE001 — 轉送執行緒不可以死
            _rdapi_warn("forward", f"[rustdesk-api] forwarder error: {type(exc).__name__}: {exc}")
            ok = False
        if not ok:
            stop.wait(RDAPI_FLUSH_SECONDS)      # 失敗時不因為佇列滿而連續重試


def _rdapi_stop_server_locked() -> None:
    srv = _RDAPI.get("server")
    if srv is None:
        return
    _RDAPI.update(server=None, thread=None, port=None)
    try:
        srv.shutdown()
    finally:
        srv.server_close()
        srv.rd_cut(every=True)


def _rdapi_start(port: int, host: str | None = None) -> int:
    """在 host:port 聽 RustDesk 客戶端的回報（已經在聽就先關掉舊的）。回傳實際的埠；綁不上丟 OSError。"""
    with _RDAPI_LIFE:
        _rdapi_stop_server_locked()
        srv = _RdapiServer((host or RDAPI_LISTEN_HOST, int(port)))
        t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": RDAPI_POLL_SECONDS},
                             name="jt-ipam-rustdesk-api", daemon=True)
        t.start()
        _RDAPI.update(server=srv, thread=t, port=int(port))
        return int(srv.server_address[1])


def _rdapi_ensure_forwarder() -> None:
    with _RDAPI_LIFE:
        t = _RDAPI.get("fwd")
        if t is not None and t.is_alive():
            return
        stop = threading.Event()
        t = threading.Thread(target=_rdapi_forward_loop, args=(stop,), name="jt-ipam-rustdesk-fwd",
                             daemon=True)
        _RDAPI.update(fwd=t, fwd_stop=stop)
        t.start()


def _rdapi_stop() -> None:
    """停止接收：關掉埠、停掉轉送執行緒、清掉還沒送出的資料。"""
    with _RDAPI_LIFE:
        _rdapi_stop_server_locked()
        stop = _RDAPI.get("fwd_stop")
        if stop is not None:
            stop.set()
            _RDAPI_WAKE.set()
        _RDAPI.update(fwd=None, fwd_stop=None, source_id=None)
    with _RDAPI_LOCK:
        _RDAPI_QUEUE.clear()
        _RDAPI_DROPPED.update(dict.fromkeys(_RDAPI_DROP_KEYS, 0))
        _RDAPI_NONCES.clear()
        _RDAPI_RATE.clear()
        _RDAPI_CACHE.clear()


def _rdapi_apply(cfg) -> None:  # noqa: ANN001
    """依 poll 回應的 `rustdesk` 開關接收端：有指派（source_id）且網頁上開啟（api_listen）→ 聽 api_port
    （預設 21114，埠改了就換）；否則完全不聽。綁不上（埠被占用、權限不足）只記一行，下一輪 poll 再試。"""
    try:
        if not (isinstance(cfg, dict) and cfg.get("source_id") and cfg.get("api_listen")):
            _RDAPI_BIND_ERROR[0] = None
            if _RDAPI.get("server") is not None or _RDAPI.get("fwd") is not None:
                _rdapi_stop()
                print("[rustdesk-api] receiver stopped (not enabled for this agent)", flush=True)
            return
        try:
            port = int(cfg.get("api_port") or RDAPI_DEFAULT_PORT)
        except (TypeError, ValueError):
            port = RDAPI_DEFAULT_PORT
        if not 1 <= port <= 65535:
            port = RDAPI_DEFAULT_PORT
        _RDAPI["source_id"] = str(cfg["source_id"])
        _rdapi_ensure_forwarder()
        if _RDAPI.get("server") is not None and _RDAPI.get("port") == port:
            return
        try:
            bound = _rdapi_start(port)
        except OSError as exc:
            _RDAPI_BIND_ERROR[0] = f"TCP {port}: {type(exc).__name__}: {exc}"[:500]
            _rdapi_warn("bind", f"[rustdesk-api] cannot listen on TCP {port}: {type(exc).__name__}: {exc}; "
                                f"will retry on the next poll")
            return
        _RDAPI_BIND_ERROR[0] = None
        print(f"[rustdesk-api] listening on TCP {bound} for RustDesk client reports", flush=True)
    except Exception as exc:  # noqa: BLE001 — 接收端出錯不可以拖垮輪詢與回報
        print(f"[rustdesk-api] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


def _rdapi_status() -> dict:
    srv = _RDAPI.get("server")
    return {"listening": srv is not None, "port": _RDAPI.get("port") if srv is not None else None,
            "error": _RDAPI_BIND_ERROR[0]}




# ── hbbr／hbbs 日誌：找出 Key 設錯的客戶端 ─────────────────────────────────────────
# RustDesk 客戶端的 Key 設錯時照樣能向 hbbs 註冊（註冊不檢查 Key）、照樣回報，同一區網的直接連線也正常；
# 只有走中繼時被 hbbr 拒絕，而網頁連線一定走中繼。hbbr 的日誌寫得很清楚，所以讀它，
# 只送「哪個 IP 什麼時候被拒／通過、幾次」，不送日誌原文。官方 deb 由 systemd 把輸出附加到
# /var/log/rustdesk-server/{hbbr,hbbs}.log，logrotate 用 copytruncate（同一個檔案被截短）。

RUSTDESK_LOG_DIR_CANDIDATES = ("/var/log/rustdesk-server",)
KEYLOG_BACKFILL_BYTES = 256 * 1024       # 剛啟動時只回頭看最後這麼多，不從幾 GB 的開頭讀起
KEYLOG_MAX_READ = 4 * 1024 * 1024         # 每次輪詢每個檔案最多讀這麼多（其餘下一輪）
KEYLOG_MAX_ENTRIES = 1000                 # 每一輪送出的 IP 筆數上限（失敗、通過各自計）
_KEYLOG_TS = re.compile(r"^\[(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d)(?:\.\d+)? ([+-]\d\d:?\d\d)\]")
_KEYLOG_RULES = (
    ("relay", re.compile(r"Relay authentication failed from (\S+) - invalid key")),
    ("hbbs", re.compile(r"Authentication failed from (\S+) for peer (\S+) - invalid key")),
    ("ok", re.compile(r"New relay request \S+ from (\S+)\s*$")),
    ("ok", re.compile(r"Relayrequest \S+ from (\S+) got paired")),
)
_KEYLOG_TAILS: dict = {}                  # 檔名 → {"ino", "pos", "rest"}
_KEYLOG_FAILS: dict = {}                  # (scope, ip) → {"scope", "ip", "n", "first", "last", "target"}
_KEYLOG_OKS: dict = {}                    # ip → 最後一次通過的時間
_KEYLOG_INFO: dict = {"dir": None, "hbbr": False, "hbbs": False, "error": None, "dropped": 0}


def _rustdesk_log_dir() -> str:
    v = os.environ.get("JT_RD_LOG_DIR", "").strip()
    if v:
        return v
    for c in RUSTDESK_LOG_DIR_CANDIDATES:
        if os.path.isdir(c):
            return c
    return RUSTDESK_LOG_DIR_CANDIDATES[0]


def _keylog_addr(addr: str) -> str | None:
    """`[::ffff:192.0.2.5]:56019` 或 `192.0.2.5:56019` → `192.0.2.5`。"""
    a = addr.strip()
    if a.startswith("["):
        host = a[1:a.find("]")] if "]" in a else ""
    else:
        host = a.rsplit(":", 1)[0] if a.count(":") == 1 else a
    return _rustdesk_ip(host) if host else None


def _keylog_parse(line: str) -> tuple | None:
    """一行日誌 → (種類, IP, 對方 ID 或 None, ISO 時間)；不是要找的就回 None。
    種類：relay＝中繼拒絕（Key 錯）、hbbs＝hbbs 拒絕主動連線（Key 錯）、ok＝中繼通過。"""
    m = _KEYLOG_TS.match(line)
    if not m:
        return None
    off = m.group(3) if ":" in m.group(3) else f"{m.group(3)[:3]}:{m.group(3)[3:]}"
    ts = f"{m.group(1)}T{m.group(2)}{off}"
    for kind, rx in _KEYLOG_RULES:
        r = rx.search(line, m.end())
        if r:
            ip = _keylog_addr(r.group(1))
            if not ip:
                return None
            target = r.group(2)[:100] if kind == "hbbs" else None
            return kind, ip, target, ts
    return None


def _keylog_add(kind: str, ip: str, target, ts: str, n: int = 1, first: str | None = None) -> None:  # noqa: ANN001
    if kind == "ok":
        if ip in _KEYLOG_OKS:
            _KEYLOG_OKS[ip] = max(_KEYLOG_OKS[ip], ts)
        elif len(_KEYLOG_OKS) < KEYLOG_MAX_ENTRIES:
            _KEYLOG_OKS[ip] = ts
        else:
            _KEYLOG_INFO["dropped"] += 1
        return
    key = (kind, ip)
    cur = _KEYLOG_FAILS.get(key)
    if cur is None:
        if len(_KEYLOG_FAILS) >= KEYLOG_MAX_ENTRIES:
            _KEYLOG_INFO["dropped"] += n
            return
        _KEYLOG_FAILS[key] = {"scope": kind, "ip": ip, "n": n, "first": first or ts, "last": ts, "target": target}
        return
    cur["n"] += n
    cur["first"] = min(cur["first"], first or ts)
    if ts >= cur["last"]:
        cur["last"] = ts
        cur["target"] = target or cur["target"]


def _keylog_read(path: str) -> list:
    """讀這個檔案從上次位置之後的完整行。檔案變小（copytruncate）或換了一個（inode 不同）就從頭讀；
    第一次讀只回頭看最後 KEYLOG_BACKFILL_BYTES，被切到的第一行丟掉。"""
    st = os.stat(path)
    t = _KEYLOG_TAILS.get(path)
    if t is None:
        pos = max(0, st.st_size - KEYLOG_BACKFILL_BYTES)
        t = {"ino": st.st_ino, "pos": pos, "rest": b"", "skip_first": pos > 0}
        _KEYLOG_TAILS[path] = t
    elif t["ino"] != st.st_ino or st.st_size < t["pos"]:
        t.update(ino=st.st_ino, pos=0, rest=b"", skip_first=False)
    if st.st_size == t["pos"]:
        return []
    with open(path, "rb") as f:
        f.seek(t["pos"])
        data = f.read(KEYLOG_MAX_READ)
    t["pos"] += len(data)
    buf = t["rest"] + data
    lines = buf.split(b"\n")
    t["rest"] = lines.pop()                  # 最後一段還沒寫完（沒有換行）留到下次
    if len(t["rest"]) > 65536:               # 一直沒有換行的怪檔案不可以讓記憶體一直長
        t["rest"] = b""
    if t.pop("skip_first", False) and lines:
        lines = lines[1:]
    return [ln.decode("utf-8", "replace") for ln in lines]


def _keylog_collect() -> None:
    d = _rustdesk_log_dir()
    _KEYLOG_INFO.update(dir=d, error=None)
    errors = []
    for name in ("hbbr", "hbbs"):
        path = os.path.join(d, f"{name}.log")
        try:
            lines = _keylog_read(path)
            _KEYLOG_INFO[name] = True
        except OSError as exc:
            _KEYLOG_INFO[name] = False
            _KEYLOG_TAILS.pop(path, None)
            errors.append(f"{path}: {type(exc).__name__}: {exc.strerror or exc}")
            continue
        for ln in lines:
            r = _keylog_parse(ln)
            if r:
                _keylog_add(*r)
    if errors:
        _KEYLOG_INFO["error"] = "; ".join(errors)[:500]


def _keylog_take() -> dict:
    """取出累積的結果（送出用）並清空；送失敗用 _keylog_restore 放回去。"""
    out = {"fails": list(_KEYLOG_FAILS.values()), "ok": [{"ip": ip, "last": ts} for ip, ts in _KEYLOG_OKS.items()],
           "dropped": int(_KEYLOG_INFO["dropped"]),
           "logs": {"dir": _KEYLOG_INFO["dir"], "hbbr": bool(_KEYLOG_INFO["hbbr"]),
                    "hbbs": bool(_KEYLOG_INFO["hbbs"]), "error": _KEYLOG_INFO["error"]}}
    _KEYLOG_FAILS.clear()
    _KEYLOG_OKS.clear()
    _KEYLOG_INFO["dropped"] = 0
    return out


def _keylog_restore(kc: dict) -> None:
    for f in kc.get("fails") or []:
        _keylog_add(f["scope"], f["ip"], f.get("target"), f["last"], n=int(f["n"]), first=f["first"])
    for o in kc.get("ok") or []:
        _keylog_add("ok", o["ip"], None, o["last"])
    _KEYLOG_INFO["dropped"] += int(kc.get("dropped") or 0)


# ── 「測試」：在這台主機上逐項檢查（網頁上按「測試」後，下一次輪詢取走 test_id）────────────

def _selftest() -> list:
    """每一項 {key, ok, detail}。detail 給人看（路徑、筆數、錯誤原文），不放 uuid 或金鑰。"""
    checks: list = []
    data_dir = _rustdesk_dir()
    checks.append({"key": "data_dir", "ok": os.path.isdir(data_dir), "detail": data_dir})
    st, peers = _rustdesk_read_peers(os.path.join(data_dir, "db_v2.sqlite3"))
    checks.append({"key": "database", "ok": bool(st.get("ok")),
                   "detail": (f"{st.get('path')}: {len(peers)} IDs" + (" (truncated)" if st.get("truncated") else ""))
                   if st.get("ok") else f"{st.get('path')}: {st.get('error')}"})
    key = _rustdesk_public_key(data_dir)
    checks.append({"key": "public_key", "ok": key is not None,
                   "detail": os.path.join(data_dir, "id_ed25519.pub") + ("" if key else ": not found / unreadable")})
    ver = _rustdesk_version()
    checks.append({"key": "hbbs_version", "ok": ver is not None, "detail": ver or "hbbs --version failed"})
    host, port = _rustdesk_local_ip(), _rustdesk_online_port(data_dir)
    if not host:
        checks.append({"key": "online_query", "ok": False, "detail": "no non-loopback address"})
    else:
        ids = [p["id"] for p in peers[:1]] or ["jt-ipam-selftest"]
        res, err = _rustdesk_online(ids, host, port)
        checks.append({"key": "online_query", "ok": err is None,
                       "detail": f"{host}:{port}" + (f": {err}" if err else "")})
    if _rustdesk_allow_delete():
        can, why = _rustdesk_delete_capability(data_dir)
        checks.append({"key": "peer_delete", "ok": can,
                       "detail": f"writable: {os.path.join(data_dir, 'db_v2.sqlite3')}" if can else why})
    else:
        # 沒開「刪除舊註冊」是預設狀態，不是錯誤
        checks.append({"key": "peer_delete", "ok": True, "detail": "read-only (delete not enabled)"})
    d = _rustdesk_log_dir()
    missing = [n for n in ("hbbr.log", "hbbs.log") if not os.access(os.path.join(d, n), os.R_OK)]
    checks.append({"key": "logs", "ok": not missing,
                   "detail": d + (f": cannot read {', '.join(missing)}" if missing else "")})
    rcv = _rdapi_status()
    if rcv["listening"]:
        ok, detail = True, f"TCP {rcv['port']}"
        try:
            with socket.create_connection(("127.0.0.1", int(rcv["port"])), timeout=3):
                pass
        except OSError as exc:
            ok, detail = False, f"TCP {rcv['port']}: {type(exc).__name__}: {exc}"
        checks.append({"key": "receiver", "ok": ok, "detail": detail})
    elif rcv["error"]:
        checks.append({"key": "receiver", "ok": False, "detail": rcv["error"]})
    else:
        checks.append({"key": "receiver", "ok": True, "detail": "off"})
    for c in checks:
        c["detail"] = str(c["detail"])[:2000]
    return checks


def _run_test(test_id: str) -> None:
    try:
        checks = _selftest()
    except Exception as exc:  # noqa: BLE001 — 測試本身出錯也要回報，畫面才不會一直等
        checks = [{"key": "agent", "ok": False, "detail": f"{type(exc).__name__}: {exc}"[:2000]}]
    try:
        _req("POST", f"{API_BASE}/test-result", {"test_id": test_id, "checks": checks}, timeout=30)
        print(f"[test] {sum(1 for c in checks if c['ok'])}/{len(checks)} checks passed", flush=True)
    except Exception as exc:  # noqa: BLE001 — 下一次輪詢還會再要一次
        print(f"[test] result not delivered: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


# ── 主迴圈 ──────────────────────────────────────────────────────────────────

def _capabilities() -> dict:
    """1.2.0 起：這台主機上代理做得到什麼（畫面據此決定「刪除舊註冊」按鈕能不能按、為什麼不能）。"""
    can, why = _rustdesk_delete_capability(_rustdesk_dir())
    return {"delete": can, "delete_reason": why[:500] if why else None}


def _poll_body(key_checks: dict | None = None) -> dict:
    return {"version": AGENT_VERSION, "hostname": socket.gethostname()[:255],
            "data_dir": _rustdesk_dir()[:1024], "receiver": _rdapi_status(),
            "key_checks": key_checks if key_checks is not None else _keylog_take(),
            "capabilities": _capabilities()}


_TESTS_DONE: dict = {}              # test_id → 時間（同一個測試只跑一次，結果送失敗才會再跑）


def poll_once() -> int:
    """輪詢一次，回傳下次輪詢前要等幾秒。"""
    try:
        _keylog_collect()
    except Exception as exc:  # noqa: BLE001 — 讀日誌出錯不可以拖垮輪詢
        _KEYLOG_INFO["error"] = f"{type(exc).__name__}: {exc}"[:500]
    kc = _keylog_take()
    try:
        cfg = _req("POST", f"{API_BASE}/poll", _poll_body(kc), timeout=30)
    except urllib.error.HTTPError as exc:
        _keylog_restore(kc)                 # 沒送到的下一輪再送
        if exc.code != 422:
            raise
        # 升級空窗：jt-ipam 的程式檔已換新（代理據此更新成這一版），後端還沒重啟，舊的不認得 key_checks／
        # capabilities。不帶它們重送，輪詢照常；後端重啟後下一輪就會帶上
        body = _poll_body({})               # 給空的：不可以再取一次（剛放回去的結果會被取走、丟掉）
        body.pop("key_checks", None)
        body.pop("capabilities", None)
        cfg = _req("POST", f"{API_BASE}/poll", body, timeout=30)
    except Exception:
        _keylog_restore(kc)
        raise
    _maybe_self_update(cfg.get("agent_sha"))
    active = bool(cfg.get("enabled"))
    _rdapi_apply(cfg if active else None)
    test_id = cfg.get("test_id")
    if isinstance(test_id, str) and test_id and test_id not in _TESTS_DONE:
        _TESTS_DONE.clear()
        _TESTS_DONE[test_id] = time.time()
        threading.Thread(target=_run_test, args=(test_id,), name="jt-ipam-rustdesk-test", daemon=True).start()
    if active:
        _rustdesk_maybe_report(cfg, time.time(), force=bool(cfg.get("report_now")))
        _rustdesk_maybe_delete(cfg)
    try:
        sec = int(cfg.get("poll_seconds") or POLL_SECONDS_DEFAULT)
    except (TypeError, ValueError):
        sec = POLL_SECONDS_DEFAULT
    return min(max(sec, POLL_SECONDS_RANGE[0]), POLL_SECONDS_RANGE[1])


def main() -> int:
    if not SERVER or not KEY:
        print("ERROR: JT_IPAM_URL and JT_IPAM_AGENT_KEY environment variables are required", file=sys.stderr)
        return 2
    # urllib also opens file:, ftp: ...; a mistyped JT_IPAM_URL would turn every poll into a local file read
    if not SERVER.startswith(("https://", "http://")):
        print(f"ERROR: JT_IPAM_URL must start with https:// (or http://), got: {SERVER}", file=sys.stderr)
        return 2
    print(f"jt-ipam RustDesk agent v{AGENT_VERSION} -> {SERVER}  data_dir={_rustdesk_dir()} "
          f"tls={_tls_mode()} auto_update={AUTO_UPDATE}", flush=True)
    while True:
        try:
            _trust_maintain()
            wait = poll_once()
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                # 金鑰被換掉、伺服器在 jt-ipam 被刪掉：不再收客戶端回報，慢慢重試
                _rdapi_stop()
                print("[poll] agent key rejected (401): the RustDesk server was deleted in jt-ipam or its agent "
                      "key was replaced. Re-run the install command shown in jt-ipam.", file=sys.stderr, flush=True)
                wait = 60
            else:
                print(f"[poll] HTTP {exc.code}: {exc.reason}", file=sys.stderr, flush=True)
                wait = 30
        except Exception as exc:  # noqa: BLE001 — stay resilient, retry
            print(f"[poll] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            wait = 1 if _trust_cert_error(exc) and _trust_recover() else 30
        time.sleep(wait)


if __name__ == "__main__":
    raise SystemExit(main())
