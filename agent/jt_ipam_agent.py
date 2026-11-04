#!/usr/bin/env python3
"""jt-ipam scan agent (push model, standard library only).

Runs inside the target network segment and connects OUT to the jt-ipam server:
  1. GET  {SERVER}/api/v1/scan-agents/poll    -> subnets to scan (+ server agent sha)
  2. Run the requested probes per subnet (icmp / tcp / arp / rdns / os / ports ...)
  3. POST {SERVER}/api/v1/scan-agents/report  -> send results back

Auth: every request carries header  X-Agent-Key: <enrollment key>  (server compares sha256).

Capability self-report: each poll also carries header  X-Agent-Probes  listing the
probe keys this host can actually perform (depends on tools/permissions available).

Auto-update: each poll returns the server's agent.py sha256. If it differs from this
running copy, the agent downloads the new agent.py and agent.py.sig, and only installs it (then
re-executes) when the signature verifies against the jt-ipam signing key built into this file.

排程（重點）：輕量探測（icmp/tcp/arp/rdns）跟著 fast loop（interval_seconds）每輪跑；
重量探測（os/ports，可能上 nmap）只在距離上次執行超過 intervals[probe] 秒時才跑。
每個探測的「上次執行時間」存在記憶體裡，依此節流。

Environment variables:
  JT_IPAM_URL        e.g. https://ipam.example.com    (required)
  JT_IPAM_AGENT_KEY  enrollment key from the agent page (required)
  JT_IPAM_INTERVAL   fallback fast-loop seconds if server omits interval_seconds, default 300
  JT_IPAM_PIN        sha256//<base64> of the jt-ipam server's public key (the install command shown in
                     jt-ipam sets it): talk only to that server, also with a self-signed certificate
  JT_IPAM_INSECURE   =1 to skip TLS verification entirely (not recommended; use JT_IPAM_PIN)
  JT_IPAM_MAX_HOSTS  max hosts scanned per subnet per cycle, default 4096; larger subnets are
                     scanned in rotating chunks (a /16 takes 16 cycles to cover once). From 1.18.0 the
                     server sends a plan for large subnets: known addresses every cycle, the rest
                     discovered a slice per cycle
  JT_IPAM_AUTO_UPDATE =0 to disable self-update (default on)
  JT_IPAM_DHCPD_CONF    dhcpd.conf path when this host runs isc-dhcp-server and an "ISC DHCP"
                        source in jt-ipam points at this agent (default: first existing of
                        /etc/dhcp/dhcpd.conf, /etc/dhcpd.conf, /usr/local/etc/dhcpd.conf)
  JT_IPAM_DHCPD_LEASES  dhcpd.leases path (default: first existing of /var/lib/dhcp/dhcpd.leases,
                        /var/lib/dhcpd/dhcpd.leases, /var/db/dhcpd.leases)
                        Only parsed ranges / fixed addresses / active leases are sent -- never the
                        file contents (dhcpd.conf often holds DDNS/OMAPI keys). The server cannot
                        change these paths.
  SNMP collection (1.19.0) needs the net-snmp client tools (apt install snmp / dnf install net-snmp-utils /
  apk add net-snmp-tools). It reads only the network devices the jt-ipam server assigns to this agent, with
  the credentials the server sends (kept in memory, handed to net-snmp via a private 0600 snmp.conf, never on
  the command line), read-only, OIDs from a fixed list in this file. Nothing to set here.
  Console relay (SSH/SFTP/RDP/VNC from the browser into the subnets this agent scans) is switched on
  and configured in the jt-ipam web UI (system settings + the agent page); nothing to set here. The
  agent relays only what the server currently allows. Optional local limits for the host owner:
  JT_IPAM_RELAY         =0 refuses all relaying on this host, whatever the server says
  JT_IPAM_RELAY_PORTS   only relay to these ports (narrows the list set in the web UI)
  JT_IPAM_RELAY_MAX     at most this many concurrent sessions (narrows the web UI setting)
  JT_IPAM_RELAY_CIDRS   comma separated: only relay into these networks, whatever the server says
                        (a compromised server still cannot reach past them, and cannot push a
                        new agent either: updates must carry the jt-ipam signature)
"""
from __future__ import annotations

import base64
import bisect
import concurrent.futures
import hashlib
import http.client
import hmac
import ipaddress
import json
import os
import re
import selectors
import shutil
import socket
import ssl
import struct
import subprocess
import threading
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

AGENT_VERSION = "1.21.0"
SERVER = os.environ.get("JT_IPAM_URL", "").rstrip("/")
KEY = os.environ.get("JT_IPAM_AGENT_KEY", "")
INTERVAL = int(os.environ.get("JT_IPAM_INTERVAL", "300"))
INSECURE = os.environ.get("JT_IPAM_INSECURE", "") in ("1", "true", "yes")
MAX_HOSTS = int(os.environ.get("JT_IPAM_MAX_HOSTS", "4096"))
AUTO_UPDATE = os.environ.get("JT_IPAM_AUTO_UPDATE", "1") not in ("0", "false", "no")
PING_WORKERS = 128
AGENT_PATH = os.path.realpath(__file__)

# 所有已知探測鍵；server 沒指定 probes 時，向下相容用 icmp。
ALL_PROBES = ("icmp", "tcp", "arp", "rdns", "netbios", "mdns", "dhcp", "os", "ports")
DEFAULT_PROBES = ("icmp",)

# tcp 探測掃的常見埠（也作為 alive 判定依據）
TCP_PROBE_PORTS = (22, 80, 443, 445, 3389, 8006)
# 定期 OS 偵測另外看的埠（只在沒開「連接埠」探測時補上）：RTSP 554／8554（攝影機）、9100／631／515（印表機）、
# 5060（VoIP）、5000／5001（NAS 管理頁）、8080／8443（設備網頁）、37777／34567（常見 NVR／DVR）
OS_PROBE_KIND_PORTS = (554, 8554, 9100, 631, 515, 5060, 5000, 5001, 8080, 8443, 37777, 34567)
TCP_PROBE_TIMEOUT = 1.0

# 每個探測在記憶體裡的「上次執行時間」：key = (subnet_id, probe) -> epoch seconds。
# 用 subnet 粒度節流（同一輪同子網的所有 host 一起跑某探測或一起跳過）。
_last_run: dict[tuple, float] = {}

# scan_once 把 server 回的 fast loop 寫在這，給 main 的 sleep 用（單元素 list 當可變參照）。
_CURRENT_FAST: list[int] = [0]


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


def _check_pin(sock: ssl.SSLSocket) -> None:
    if not PIN:
        return
    try:
        got = _spki_pin(sock.getpeercert(binary_form=True) or b"")
    except (IndexError, ValueError):
        got = ""
    if not hmac.compare_digest(got.encode(), PIN.encode()):
        sock.close()
        raise ssl.SSLError("server certificate does not match JT_IPAM_PIN "
                           "(not the jt-ipam server this agent was installed for)")


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self) -> None:
        super().connect()
        _check_pin(self.sock)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):  # type: ignore[no-untyped-def]
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)


def _urlopen(req: urllib.request.Request, timeout: float):  # type: ignore[no-untyped-def]
    """連回伺服器的 HTTP(S) 都走這裡：釘選時在送出請求（與金鑰）之前比對公鑰。"""
    if PIN and SERVER.startswith("https"):
        return urllib.request.build_opener(_PinnedHTTPSHandler(context=_ctx())).open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout, context=_ctx())  # noqa: S310


def _verify_signature(data: bytes, sig_b64: str) -> bool:
    """RSA PKCS#1 v1.5 / SHA-256 驗章，純 Python：不靠 openssl，也就不會因為主機沒裝而跳過。"""
    try:
        sig = base64.b64decode(sig_b64.strip(), validate=True)
    except (ValueError, TypeError):
        return False
    k = (AGENT_SIGNING_N.bit_length() + 7) // 8
    s = int.from_bytes(sig, "big")
    if len(sig) != k or s >= AGENT_SIGNING_N:
        return False
    t = _SHA256_DIGEST_INFO + hashlib.sha256(data).digest()
    want = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(pow(s, AGENT_SIGNING_E, AGENT_SIGNING_N).to_bytes(k, "big"), want)


def _capabilities() -> list[str]:
    """回報本機實際能做的探測（送 X-Agent-Probes header 給 server）。

    icmp/tcp/rdns 一律支援；arp 需 neigh 表可讀；os/ports 需 PATH 上有 nmap；
    netbios 需 nmblookup/nbtscan、mdns 需 avahi-resolve（有才回報能力、才會實際查名）。
    snmp（1.19.0）：有 net-snmp 用戶端工具（snmpbulkwalk／snmpget）才回報。SNMP 不是逐子網路的探測：
    只對伺服器指派的網路設備、用伺服器下發的憑證做唯讀讀取（見下方 SNMP collection 一節）。
    """
    caps = ["icmp", "tcp", "rdns"]
    try:
        if _arp_table():
            caps.append("arp")
    except Exception:
        pass
    if shutil.which("nmap"):
        caps.extend(["os", "ports"])
    # NetBIOS / mDNS：有對應工具才回報能力（_netbios / _mdns 實際查名）
    if shutil.which("nmblookup") or shutil.which("nbtscan"):
        caps.append("netbios")
    if shutil.which("avahi-resolve"):
        caps.append("mdns")
    # DHCP discovery needs to bind UDP/68 (privileged) — report the capability only if we can.
    if _can_bind_dhcp_port():
        caps.append("dhcp")
    # 去重並維持 ALL_PROBES 順序
    seen = set(caps)
    out = [p for p in ALL_PROBES if p in seen]
    if _snmp_available():
        out.append("snmp")      # 不在 ALL_PROBES：子網路的探測清單裡沒有它（伺服器另外指派收集目標）
    return out


# 探測相依的外部工具（name, version-args）。server 端另有 probes/package 對照表（顯示用）。
_DEP_TOOLS = (
    ("python3", ["--version"]),
    ("ping", ["-V"]),
    ("ip", ["-V"]),
    ("nmap", ["--version"]),
    ("nmblookup", ["-V"]),
    ("nbtscan", []),         # 無可靠的版本旗標
    ("avahi-resolve", []),   # 無可靠的版本旗標
    ("snmpbulkwalk", ["--version"]),   # net-snmp 用戶端工具（apt: snmp）→ SNMP 收集（1.19.0）
)


def _tool_version(path: str, args: list) -> str:
    if not args:
        return ""
    try:
        r = subprocess.run([path, *args], capture_output=True, text=True, timeout=4)
        txt = (r.stdout or "") + (r.stderr or "")
        m = re.search(r"(\d+\.\d+(?:\.\d+)?)", txt)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _tools_header() -> str:
    """相依工具盤點 → 緊湊字串 `name|installed(1/0)|version` 以分號相接（送 X-Agent-Tools）。"""
    parts = []
    for name, vargs in _DEP_TOOLS:
        path = shutil.which(name)
        ver = _tool_version(path, vargs) if path else ""
        parts.append(f"{name}|{1 if path else 0}|{ver}")
    return ";".join(parts)


def _req(method: str, path: str, body: dict | None = None,
         extra_headers: dict | None = None, timeout: float = 30) -> dict:
    url = f"{SERVER}{path}"
    data = json.dumps(body).encode() if body is not None else None
    # S310 is suppressed here and below: the scheme is validated once at startup
    # (main() rejects anything that is not http/https), and SERVER comes from the
    # agent's own config file, not from anything the network says.
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


def _children_of(pid: int, proc: str = "/proc") -> list:
    """PIDs whose parent is `pid` (Linux /proc). The name in /proc/<pid>/stat may itself contain
    ')' and spaces, so the fields are read after the LAST ')'."""
    out = []
    try:
        entries = os.listdir(proc)
    except OSError:
        return out
    for name in entries:
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc, name, "stat"), encoding="utf-8", errors="replace") as f:
                fields = f.read().rsplit(")", 1)[1].split()
            if int(fields[1]) == pid:
                out.append(int(name))
        except (OSError, IndexError, ValueError):
            continue
    return out


# Self-update re-executes in place (os.execv keeps the PID), so probes the old program had
# running become children nobody waits for: when they finish they stay as zombies (seen in
# production: 8 nmap zombies left by an update). Children that already exist when this
# program starts can only be such leftovers; reap exactly those, never waitpid(-1), which
# would steal the exit status of a subprocess call that is still waiting for its child.
_INHERITED: list = _children_of(os.getpid())


def _reap_inherited() -> None:
    for pid in list(_INHERITED):
        try:
            done, _status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            done = pid
        except OSError:
            continue
        if done:
            _INHERITED.remove(pid)


def _maybe_self_update(server_sha: str | None) -> None:
    """If the server's agent.py differs from this copy, update self and re-exec."""
    if not AUTO_UPDATE or not server_sha:
        return
    if server_sha == _self_sha():
        return
    print("[update] server agent differs from local; downloading new version", flush=True)
    try:
        new = _get_bytes("/api/v1/scan-agents/agent.py")
        if hashlib.sha256(new).hexdigest() != server_sha:
            print("[update] downloaded sha mismatch; skip this round", flush=True)
            return
        # 只裝 jt-ipam 簽章金鑰簽過的版本：sha256 是同一台伺服器給的，假冒或被入侵的伺服器照樣算得出來
        sig = _get_bytes("/api/v1/scan-agents/agent.py.sig").decode("ascii", "replace")
        if not _verify_signature(new, sig):
            print("[update] SIGNATURE CHECK FAILED; keeping the current agent", file=sys.stderr, flush=True)
            return
        tmp = AGENT_PATH + ".new"
        with open(tmp, "wb") as f:
            f.write(new)
        os.chmod(tmp, 0o755)
        os.replace(tmp, AGENT_PATH)
        print("[update] updated; re-executing new agent", flush=True)
        os.execv(sys.executable, [sys.executable, AGENT_PATH])
    except Exception as exc:  # noqa: BLE001 — never let update crash the agent
        print(f"[update] failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# 探測實作（每個都必須對單一 host 的失敗保持容忍，絕不可讓 loop 崩掉）           #
# --------------------------------------------------------------------------- #

def _ping(ip: str) -> bool:
    """icmp 探測：回 True 表示有回應。"""
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
        )
        return r.returncode == 0
    except Exception:
        return False


def _tcp_scan(ip: str) -> list[int]:
    """tcp 探測：嘗試連線常見埠，回傳成功連上的埠清單。"""
    open_ports: list[int] = []
    for port in TCP_PROBE_PORTS:
        try:
            with socket.create_connection((ip, port), timeout=TCP_PROBE_TIMEOUT):
                open_ports.append(port)
        except Exception:
            continue
    return open_ports


def _rdns(ip: str) -> tuple[str | None, bool]:
    """rdns 探測：反查 hostname → (名稱, DNS 是否明確說沒有)。

    只有 DNS 明確回答「沒有這筆 PTR」（HOST_NOT_FOUND＝NXDOMAIN、NO_DATA）才算沒有；伺服器會拿它
    清掉舊名。逾時、DNS 連不上、TRY_AGAIN 都不算 —— 那種時候清掉，全部名稱會跟著 DNS 故障一起消失。
    """
    try:
        host, _, _ = socket.gethostbyaddr(ip)
        return (host or None), False
    except socket.herror as exc:
        return None, exc.errno in (1, 4)      # HOST_NOT_FOUND / NO_DATA
    except Exception:
        return None, False


def _netbios(ip: str) -> str | None:
    """NetBIOS 名稱探測：nmblookup -A <ip> 取 <00> UNIQUE（非 <GROUP>）工作站名；或 nbtscan -q。"""
    nmb = shutil.which("nmblookup")
    if nmb:
        try:
            r = subprocess.run([nmb, "-A", ip], capture_output=True, text=True, timeout=5)
            for line in r.stdout.splitlines():
                # 例： "    DESKTOP-ABC   <00> -         B <ACTIVE>"  → UNIQUE 機名（要）
                #      "    WORKGROUP     <00> - <GROUP> B <ACTIVE>"  → 群組（略過）
                if "<00>" in line and "<GROUP>" not in line:
                    name = line.split()[0].strip()
                    if name and name != ip:
                        return name
        except Exception:
            pass
    nbt = shutil.which("nbtscan")
    if nbt:
        try:
            r = subprocess.run([nbt, "-q", ip], capture_output=True, text=True, timeout=5)
            for line in r.stdout.splitlines():
                parts = line.split()
                # 例： "192.168.1.10   WORKGROUP\\DESKTOP-ABC   SHARING ..."
                if parts and parts[0] == ip and len(parts) >= 2:
                    nm = parts[1]
                    if "\\" in nm:
                        nm = nm.split("\\", 1)[1]
                    if nm and nm != "<unknown>":
                        return nm
        except Exception:
            pass
    return None


# ─────────────────── DHCP server discovery ───────────────────
# Sends one standard DHCPDISCOVER broadcast and collects every DHCPOFFER that comes
# back. Anything answering is handing out addresses on this segment; the server then
# compares that against the addresses marked as DHCP servers in IPAM, and whatever is
# left over is a rogue DHCP server.
#
# We do NOT accept the offered lease (no REQUEST is sent), so nothing is consumed.
# Some servers do reserve the offered address for a short while — that is inherent to
# asking the question at all, and is why this probe is off by default.

DHCP_MAGIC = b"\x63\x82\x53\x63"


def _can_bind_dhcp_port() -> bool:
    """Can we bind UDP/68? DHCP replies are sent there; without it we would miss them."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", 68))
        s.close()
        return True
    except OSError:
        return False


def _dhcp_packet(xid: bytes, mac: bytes) -> bytes:
    """Build a minimal DHCPDISCOVER (RFC 2131)."""
    pkt = b"".join((
        b"\x01",                     # op: BOOTREQUEST
        b"\x01",                     # htype: ethernet
        b"\x06",                     # hlen
        b"\x00",                     # hops
        xid,
        b"\x00\x00",                 # secs
        b"\x80\x00",                 # flags: broadcast
        b"\x00" * 4 * 4,             # ciaddr / yiaddr / siaddr / giaddr
        mac + b"\x00" * 10,          # chaddr (16 bytes)
        b"\x00" * 64,                # sname
        b"\x00" * 128,               # file
        DHCP_MAGIC,
        b"\x35\x01\x01",            # option 53: DHCPDISCOVER
        b"\x37\x03\x01\x03\x06",    # option 55: request subnet/router/dns
        b"\xff",                     # end
    ))
    return pkt


def _dhcp_parse(data: bytes) -> dict | None:
    """Pull the interesting bits out of a DHCP reply. Returns None if it is not one."""
    if len(data) < 240 or data[236:240] != DHCP_MAGIC:
        return None
    out = {"offered_ip": socket.inet_ntoa(data[16:20]),
           "relay": socket.inet_ntoa(data[24:28]),
           "xid": data[4:8]}
    i = 240
    while i < len(data):
        opt = data[i]
        if opt == 255:                # end
            break
        if opt == 0:                  # pad
            i += 1
            continue
        if i + 1 >= len(data):
            break
        ln = data[i + 1]
        val = data[i + 2:i + 2 + ln]
        if len(val) < ln:
            # Truncated option: stop here. Before 1.14.1 this raised (IndexError / OSError from
            # inet_ntoa) and ended the whole listening window, so any host on the segment could
            # hide a rogue DHCP server behind one malformed reply.
            break
        if opt == 53 and ln == 1:
            out["msg_type"] = val[0]
        elif opt == 54 and ln == 4:   # server identifier
            out["server_id"] = socket.inet_ntoa(val)
        elif opt == 1 and ln == 4:
            out["netmask"] = socket.inet_ntoa(val)
        elif opt == 3 and ln >= 4:
            out["router"] = socket.inet_ntoa(val[:4])
        i += 2 + ln
    return out


def _dhcp_discover(wait: float = 4.0) -> list:
    """Broadcast one DHCPDISCOVER, return every distinct server that answered.

    Listens for the whole window rather than stopping at the first reply — the entire
    point is to find the SECOND server answering on a segment that should only have one.
    """
    mac = os.urandom(6)
    mac = bytes([(mac[0] | 0x02) & 0xFE]) + mac[1:]   # locally administered, unicast
    xid = os.urandom(4)
    found = {}
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", 68))
        sock.settimeout(0.5)
        sock.sendto(_dhcp_packet(xid, mac), ("255.255.255.255", 67))
        deadline = time.time() + wait
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            info = _dhcp_parse(data)
            # Only our own transaction, and only offers (2 = DHCPOFFER)
            if not info or info.get("xid") != xid or info.get("msg_type") != 2:
                continue
            server_ip = info.get("server_id") or addr[0]
            if server_ip in found:
                continue
            found[server_ip] = {
                "server_ip": server_ip,
                "from_ip": addr[0],
                "offered_ip": info.get("offered_ip"),
                "router": info.get("router"),
                "netmask": info.get("netmask"),
                "via_relay": info.get("relay") not in (None, "0.0.0.0"),
            }
    except OSError as exc:
        print(f"[dhcp] discovery failed: {exc}", file=sys.stderr, flush=True)
    finally:
        if sock is not None:
            sock.close()
    return list(found.values())


def _mdns(ip: str) -> str | None:
    """mDNS 名稱探測：avahi-resolve -a <ip> 取 .local 主機名。"""
    av = shutil.which("avahi-resolve")
    if av:
        try:
            r = subprocess.run([av, "-a", ip], capture_output=True, text=True, timeout=5)
            out = (r.stdout or "").strip()
            if out:
                # 例： "192.168.1.10\tdesktop.local"
                parts = out.split()
                if len(parts) >= 2:
                    name = parts[-1].strip().rstrip(".")
                    if name and name != ip:
                        return name
        except Exception:
            pass
    return None


_ARP_RE = re.compile(r"^(\d+\.\d+\.\d+\.\d+)\s+\S+\s+\S+\s+([0-9a-f:]{17})", re.I)


def _arp_table() -> dict[str, str]:
    """Read ip->mac from `ip neigh` / /proc/net/arp."""
    out: dict[str, str] = {}
    try:
        r = subprocess.run(["ip", "neigh"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0].count(".") == 3 and ":" in parts[4]:
                out[parts[0]] = parts[4].lower()
    except Exception:
        pass
    if not out:
        try:
            with open("/proc/net/arp") as f:
                for line in f.readlines()[1:]:
                    c = line.split()
                    if len(c) >= 4 and c[3] != "00:00:00:00:00:00":
                        out[c[0]] = c[3].lower()
        except Exception:
            pass
    return out


# OS 推導：積極猜測（-O --osscan-guess）對裝置/BMC 常自信地誤判（HP NAS、OpenWrt…），
# 故只接受「通用 OS」猜測、排除裝置型號；有 banner/服務資訊時一律優先採信。
_OS_DEVICEY = re.compile(
    r"NAS|printer|\bWAP\b|router|webcam|camera|storage|switch|VoIP|media device|"
    r"game console|specialized|OpenWrt|P2000|firewall|access point|broadband|"
    r"bridge|load balancer|embedded", re.I)
_OS_OSY = re.compile(r"Linux|Windows|BSD|macOS|Mac OS|Solaris|Unix|Android|VMware|ESXi", re.I)


def _derive_os(text: str):
    """從 nmap -sV / smb-os-discovery / -O 輸出推出最可靠的 OS 字串。

    優先序（可靠 → 猜測）：SMB 探得的 Windows 版本 > SSH banner 發行版 > 綜合 Service Info OS
    > -O 精確匹配 > -O 系列 > 積極猜測（僅限通用 OS、排除裝置型號）。
    banner/服務資訊比 TCP/IP 堆疊指紋準得多，故一律優先；純猜測若是裝置型號寧回 None（顯示未知）。
    """
    m = re.search(r"smb-os-discovery:.*?\n\|\s*OS:\s*([^\n(]+)", text, re.S)
    if m:
        return m.group(1).strip()[:200]
    m = re.search(r"\d+/tcp\s+open\s+ssh\s+OpenSSH\s+\S+\s+(Debian|Ubuntu|Raspbian|FreeBSD)\b", text, re.I)
    if m:
        return m.group(1)
    m = re.search(r"Service Info:.*?\bOSs?:\s*([^;\n]+)", text)
    if m:
        return m.group(1).strip()[:200]
    m = re.search(r"OS details:\s*(.+)", text)
    if m:
        return m.group(1).strip()[:200]
    m = re.search(r"Running:\s*(.+)", text)
    if m:
        return m.group(1).strip()[:200]
    g = re.search(r"(?:Aggressive )?OS guesses:\s*(.+)", text)
    if g:
        top = g.group(1).split(",")[0].strip()[:200]
        if _OS_OSY.search(top) and not _OS_DEVICEY.search(top):
            return top
    return None


#: banner 腳本只跑在「伺服器先說話」的服務埠（1.18.1；客戶 2026-10-12：Brother、Ricoh 被掃描後印出亂碼）。
#: nmap 的 banner 對所有 TCP 埠連線、不理會 nmap 自己為印表機設的 Exclude 9100-9107；明文連上等不到對方先說話，
#: 就改用 TLS 再連一次（comm.tryssl）—— 印表機的 RAW 列印埠把 TLS ClientHello 當成列印工作印出來。
#: 伺服器只拿 banner 比對 SSH／FTP／SMTP／POP3／IMAP／Telnet，這些服務明文就讀得到 banner（不會退回 TLS），
#: 限定在這些埠不少任何判讀資料；其他埠送 TLS 只有壞處（印表機、序列設備伺服器、工控設備）。
#: FTP 21／2121、SSH 22／2222、Telnet 23／2323、SMTP 25／465／587、POP3 110／995、IMAP 143／993
BANNER_PORTS = "21-23,25,110,143,465,587,993,995,2121,2222,2323"
#: --script-args 用逗號分隔參數：值裡有逗號要整段加引號
BANNER_SCRIPT_ARGS = f'banner.ports="{BANNER_PORTS}"'

#: 定期 OS 偵測額外跑的腳本：都只是讀服務自己送出來的東西（banner、網頁標題、伺服器標頭、憑證），
#: 伺服器拿去比對 Recog 指紋庫（1.14.0 起）。與 IP 探測相比少了 ssh-hostkey、rdp-ntlm-info
_OS_SCRIPTS = "smb-os-discovery,banner,http-title,http-server-header,ssl-cert"
_OS_MAX_PORTS = 32
_OS_MAX_TEXT = 300


def _compact_nmap(parsed: dict) -> dict:
    """定期 OS 偵測要送回伺服器判讀的部分：埠數與每段文字都有上限，一台通常幾 KB。"""
    def cut(d) -> dict:  # noqa: ANN001
        return {k: (v or "")[:_OS_MAX_TEXT] for k, v in (d or {}).items()}
    ports = [{**p, "scripts": cut(p.get("scripts"))} for p in (parsed.get("ports") or [])[:_OS_MAX_PORTS]]
    return {"ports": ports, "os": (parsed.get("os") or [])[:3], "closed": int(parsed.get("closed") or 0),
            "host_scripts": cut(parsed.get("host_scripts")), "mac_vendor": parsed.get("mac_vendor"),
            "hostnames": (parsed.get("hostnames") or [])[:5]}


def _nmap_os_ports(ip: str, want_os: bool, want_ports: bool) -> dict:
    """os / ports 重量探測：有 nmap 才跑，回 {os_guess?, open_ports?, nmap?}。

    os 偵測需 root（-O），失敗就只回 ports；任何錯誤都回空 dict（略過）。
    `nmap`（1.14.0 起）：結構化結果，伺服器用 IP 探測同一套判讀（含 Recog）推 OS 與設備類型；
    `os_guess` 照舊附上（舊伺服器只認這個）。
    """
    result: dict = {}
    if not shutil.which("nmap"):
        return result
    if want_ports:
        port_args = ["--top-ports", "100"]
    else:
        # 沒開「連接埠」探測時只看幾個埠：OS 偵測至少要看得到最能說明設備類型的那幾個
        #（攝影機的 RTSP、印表機、VoIP、NAS、NVR），否則只剩 TCP/IP 指紋，攝影機會被判成一般主機
        ports = sorted(set(TCP_PROBE_PORTS) | set(OS_PROBE_KIND_PORTS)) if want_os else list(TCP_PROBE_PORTS)
        port_args = ["-p", ",".join(str(p) for p in ports)]
    try:
        v6 = ["-6"] if ipaddress.ip_address(ip).version == 6 else []     # 沒有 -6，IPv6 位址一定失敗
    except ValueError:
        v6 = []
    args = ["nmap", "-Pn", "-T4", "--host-timeout", "90s" if want_os else "30s", *v6, *port_args]
    xml_path = None
    if want_os:
        # -sV（服務/banner 偵測）+ smb-os-discovery 遠比純 TCP/IP 堆疊指紋（-O）可靠：
        # 裝置/BMC 用 -O 常被自信地誤判。_derive_os 綜合這些訊號、優先採信 banner。
        # --version-light：預設強度會對認不得的服務一連試幾十種探針、每種等好幾秒，一個慢服務（PVE 的 8006）
        # 就能拖過單台時限，nmap 會把這台的結果整筆丟掉（2026-10-04 正式環境）；常見服務輕量模式就認得
        args += ["-sV", "--version-light", "-O", "--osscan-guess",
                 "--script", _OS_SCRIPTS, "--script-args", BANNER_SCRIPT_ARGS, "--script-timeout", "15s"]
        fd, xml_path = tempfile.mkstemp(prefix="jtipam-nmap-", suffix=".xml")
        os.close(fd)
        args += ["-oX", xml_path]
    args.append(ip)

    def _run(cmd: list) -> tuple:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120 if want_os else 60)
        parsed: dict = {}
        if xml_path:
            try:
                with open(xml_path, encoding="utf-8", errors="replace") as fh:
                    parsed = _parse_nmap_xml(fh.read())
            except OSError:
                pass
        return r.stdout or "", parsed

    def _has_data(parsed: dict) -> bool:
        return bool(parsed.get("ports") or parsed.get("os") or parsed.get("host_scripts"))

    try:
        text, parsed = _run(args)
        if want_os and not _has_data(parsed):
            # 還是拖過單台時限（或腳本卡住）：nmap 不留這台的任何結果。退回只做 OS 指紋（十幾秒），
            # 至少有 OS 與設備類型可判讀，不要整筆白跑
            text2, parsed2 = _run(["nmap", "-Pn", "-T4", "--host-timeout", "60s", *v6, *port_args,
                                   "-O", "--osscan-guess", "-oX", xml_path, ip])
            if _has_data(parsed2):
                text, parsed = text2, parsed2
        if _has_data(parsed):
            result["nmap"] = _compact_nmap(parsed)
        if want_ports:
            ports: list[int] = []
            for line in text.splitlines():
                m = re.match(r"^(\d+)/tcp\s+open", line.strip())
                if m:
                    ports.append(int(m.group(1)))
            if ports:
                result["open_ports"] = ports
        if want_os:
            og = _derive_os(text)
            if og:
                result["os_guess"] = og
    except Exception:
        return {}
    finally:
        if xml_path:
            try:
                os.unlink(xml_path)
            except OSError:
                pass
    return result


_chunk_pos: dict[str, int] = {}


# ── 獨立的 ISC DHCP Server（isc-dhcp-server）──────────────────────────────────
# 代理裝在 DHCP 主機上時，讀本機的 dhcpd.conf／dhcpd.leases，解析後只回報結構化資料
# （範圍、固定分配、租約）。ISC dhcpd 沒有能列出全部租約的 API（OMAPI 只能逐筆查），只能讀檔。
#
# 安全界線：
# - 檔案路徑只能在這台主機設定（環境變數），伺服器端改不了 —— 不然代理就成了「讀任意檔案」的管道
# - dhcpd.conf 裡常有 DDNS／OMAPI 金鑰：只取 subnet/range/host，key/zone/failover 等區塊一律不碰，
#   回報裡不會出現檔案原文
DHCPD_CONF_CANDIDATES = ("/etc/dhcp/dhcpd.conf", "/etc/dhcpd.conf", "/usr/local/etc/dhcpd.conf")
DHCPD_LEASES_CANDIDATES = ("/var/lib/dhcp/dhcpd.leases", "/var/lib/dhcpd/dhcpd.leases",
                           "/var/db/dhcpd.leases", "/var/db/dhcpd/dhcpd.leases")
DHCPD_MAX_FILE = 256 * 1024 * 1024       # 租約檔是日誌，dhcpd 每小時重寫一次；超過這麼大就不讀
DHCPD_MAX_INCLUDES = 50
DHCPD_MAX_POOLS = 5000
DHCPD_MAX_RESERVATIONS = 20000
DHCPD_MAX_LEASES = 50000
_DHCPD_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|#[^\n]*|[{};,]|[^\s{};,"#]+')


def _dhcpd_env_path(env: str, candidates: tuple) -> str:
    v = os.environ.get(env, "").strip()
    if v:
        return v
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


def _dhcpd_tree(text: str) -> list:
    """dhcpd 設定／租約檔的語法樹：[(字詞串, 子節點或 None)]。字串去掉引號、註解略過。"""
    root: list = []
    stack = [root]
    cur: list = []
    for m in _DHCPD_TOKEN.finditer(text):
        tok = m.group(0)
        if tok.startswith("#"):
            continue
        if tok == ";":
            if cur:
                stack[-1].append((cur, None))
            cur = []
        elif tok == "{":
            node: tuple = (cur, [])
            stack[-1].append(node)
            stack.append(node[1])
            cur = []
        elif tok == "}":
            if cur:
                stack[-1].append((cur, None))
            cur = []
            if len(stack) > 1:
                stack.pop()
        elif tok.startswith('"'):
            cur.append(tok[1:-1])
        else:
            cur.append(tok)
    return root


def _dhcpd_ip4(v: str) -> str | None:
    try:
        a = ipaddress.ip_address(v)
    except ValueError:
        return None
    return str(a) if a.version == 4 else None


def _dhcpd_mac(v: str) -> str | None:
    hexs = "".join(ch for ch in str(v).lower() if ch in "0123456789abcdef")
    return ":".join(hexs[i:i + 2] for i in range(0, 12, 2)) if len(hexs) == 12 else None


def _dhcpd_host(label: str, kids: list) -> list:
    """host 區塊 → 固定分配（一個 fixed-address 一筆；寫主機名稱的要靠 DNS 才知道位址，不收）。"""
    mac = name = ddns = None
    addrs: list = []
    for words, sub in kids:
        if sub is not None or not words:
            continue
        k = words[0].lower()
        if k == "hardware" and len(words) >= 3:
            mac = _dhcpd_mac(words[2])
        elif k == "fixed-address":
            addrs = [w for w in words[1:] if w != ","]
        elif k == "option" and len(words) >= 3 and words[1].lower() == "host-name":
            name = words[2]
        elif k == "ddns-hostname" and len(words) >= 2:
            ddns = words[1]
    out = []
    for a in addrs:
        ip = _dhcpd_ip4(a)
        if ip:
            out.append({"ip": ip, "mac": mac, "hostname": name or ddns or label})
    return out


def _dhcpd_parse_conf(text: str, load_include=None) -> dict:  # noqa: ANN001
    """dhcpd.conf → {"pools": [...], "reservations": [...]}（只看 IPv4 的 subnet／range／host）。"""
    pools: list = []
    reservations: list = []
    seen_includes: set = set()

    def add_range(subnet: str | None, words: list) -> None:
        ips = [w for w in words if w.lower() != "dynamic-bootp"]
        start = _dhcpd_ip4(ips[0]) if ips else None
        end = _dhcpd_ip4(ips[1]) if len(ips) > 1 else start
        if not start or not end or subnet is None:
            return
        if ipaddress.ip_address(start) > ipaddress.ip_address(end):
            start, end = end, start
        if len(pools) < DHCPD_MAX_POOLS:
            pools.append({"subnet": subnet, "start": start, "end": end})

    def walk(nodes: list, subnet: str | None, depth: int) -> None:
        for words, kids in nodes:
            if not words:
                if kids:
                    walk(kids, subnet, depth)
                continue
            key = words[0].lower()
            if kids is None:
                if key == "range":
                    add_range(subnet, words[1:])
                elif key == "include" and len(words) >= 2 and load_include is not None:
                    path = words[1]
                    if depth < 5 and path not in seen_includes and len(seen_includes) < DHCPD_MAX_INCLUDES:
                        seen_includes.add(path)
                        inc = load_include(path)
                        if inc:
                            walk(_dhcpd_tree(inc), subnet, depth + 1)
                continue
            if key == "subnet" and len(words) >= 4 and words[2].lower() == "netmask":
                try:
                    net = ipaddress.ip_network(f"{words[1]}/{words[3]}", strict=False)
                except ValueError:
                    continue
                walk(kids, str(net), depth)
            elif key == "host" and len(words) >= 2:
                for r in _dhcpd_host(words[1], kids):
                    if len(reservations) < DHCPD_MAX_RESERVATIONS:
                        reservations.append(r)
            elif key in ("shared-network", "group", "pool"):
                walk(kids, subnet, depth)
            # 其他區塊（key、zone、failover、class、on commit…）不碰

    walk(_dhcpd_tree(text), None, 0)
    return {"pools": pools, "reservations": reservations}


def _dhcpd_time(words: list) -> tuple[bool, datetime | None]:
    """租約檔的時間：`4 2026/09/24 13:02:03`（UTC）、`epoch 1695520923`、`never`。
    回傳 (是否永不到期, 時間)。"""
    if not words:
        return False, None
    if words[0].lower() == "never":
        return True, None
    try:
        if words[0].lower() == "epoch" and len(words) >= 2:
            return False, datetime.fromtimestamp(int(words[1]), tz=timezone.utc)
        if len(words) >= 3:
            return False, datetime.strptime(f"{words[1]} {words[2]}", "%Y/%m/%d %H:%M:%S").replace(
                tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        return False, None
    return False, None


def _dhcpd_parse_leases(text: str, now: datetime | None = None) -> dict:
    """dhcpd.leases → {"leases": [...目前有效的], "reservations": [...OMAPI 動態新增的 host]}。

    檔案是日誌：同一個位址後面的記錄蓋掉前面的。只有最後一筆是 `binding state active`、
    而且還沒到期（或永不到期）的才算。
    """
    now = now or datetime.now(timezone.utc)
    latest: dict = {}
    hosts: dict = {}
    for words, kids in _dhcpd_tree(text):
        if kids is None or not words:
            continue
        key = words[0].lower()
        if key == "lease" and len(words) >= 2:
            ip = _dhcpd_ip4(words[1])
            if not ip:
                continue
            info = {"state": None, "mac": None, "hostname": None, "never": False, "ends": None}
            for w, sub in kids:
                if sub is not None or not w:
                    continue
                k = w[0].lower()
                if k == "binding" and len(w) >= 3 and w[1].lower() == "state":
                    info["state"] = w[2].lower()
                elif k == "ends":
                    info["never"], info["ends"] = _dhcpd_time(w[1:])
                elif k == "hardware" and len(w) >= 3:
                    info["mac"] = _dhcpd_mac(w[2])
                elif k == "client-hostname" and len(w) >= 2:
                    info["hostname"] = w[1]
            latest.pop(ip, None)          # 維持「最後一次出現」的順序
            latest[ip] = info
        elif key == "host" and len(words) >= 2:
            flags = {w[0].lower() for w, sub in kids if sub is None and w}
            if "deleted" in flags:
                hosts.pop(words[1], None)
            else:
                hosts[words[1]] = _dhcpd_host(words[1], kids)
    leases = []
    for ip, info in latest.items():
        if info["state"] != "active":
            continue
        if not info["never"] and (info["ends"] is None or info["ends"] <= now):
            continue
        if len(leases) >= DHCPD_MAX_LEASES:
            break
        leases.append({"ip": ip, "mac": info["mac"], "hostname": info["hostname"],
                       "ends": info["ends"].isoformat() if info["ends"] else None})
    leases.sort(key=lambda x: ipaddress.ip_address(x["ip"]))
    reservations = [r for rs in hosts.values() for r in rs][:DHCPD_MAX_RESERVATIONS]
    return {"leases": leases, "reservations": reservations}


def _dhcpd_read(path: str) -> tuple[str | None, dict]:
    st = {"path": path, "ok": False, "error": None, "size": None, "mtime": None}
    try:
        info = os.stat(path)
        st["size"], st["mtime"] = info.st_size, int(info.st_mtime)
        if info.st_size > DHCPD_MAX_FILE:
            st["error"] = f"file too large ({info.st_size} bytes)"
            return None, st
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        st["ok"] = True
        return text, st
    except OSError as exc:
        st["error"] = f"{type(exc).__name__}: {exc.strerror or exc}"
        return None, st


def _dhcpd_collect(conf_path: str, leases_path: str, now: datetime | None = None) -> dict:
    """讀本機兩個檔、解析，回報結構化結果與檔案狀態（讀不到就說讀不到，不當成「沒有資料」）。"""
    base = os.path.dirname(conf_path)

    def load_include(p: str) -> str | None:
        full = p if os.path.isabs(p) else os.path.join(base, p)
        if not os.path.isfile(full):
            return None
        text, _st = _dhcpd_read(full)
        return text

    conf_text, conf_st = _dhcpd_read(conf_path)
    leases_text, leases_st = _dhcpd_read(leases_path)
    conf = _dhcpd_parse_conf(conf_text, load_include) if conf_text is not None else {
        "pools": [], "reservations": []}
    lease = _dhcpd_parse_leases(leases_text, now) if leases_text is not None else {
        "leases": [], "reservations": []}
    return {
        "pools": conf["pools"],
        "reservations": (conf["reservations"] + lease["reservations"])[:DHCPD_MAX_RESERVATIONS],
        "leases": lease["leases"],
        "files": {"conf": conf_st, "leases": leases_st},
    }


_DHCPD_STATE = {"last": 0.0, "running": False}
_DHCPD_LOCK = threading.Lock()


def _dhcpd_report_once(source_id: str) -> None:
    """讀本機 dhcpd 檔、回報（在背景執行緒跑：租約檔大時解析要幾秒，不能拖到上線偵測）。"""
    try:
        conf = _dhcpd_env_path("JT_IPAM_DHCPD_CONF", DHCPD_CONF_CANDIDATES)
        leases = _dhcpd_env_path("JT_IPAM_DHCPD_LEASES", DHCPD_LEASES_CANDIDATES)
        data = _dhcpd_collect(conf, leases)
        data["source_id"] = source_id
        r = _req("POST", "/api/v1/scan-agents/dhcpd-report", data, timeout=120)
        print(f"[dhcpd] pools={len(data['pools'])} reservations={len(data['reservations'])} "
              f"leases={len(data['leases'])} conf_ok={data['files']['conf']['ok']} "
              f"leases_ok={data['files']['leases']['ok']} -> {r.get('status', 'ok')}", flush=True)
    except Exception as exc:  # noqa: BLE001 — 下一輪再試
        print(f"[dhcpd] report failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    finally:
        with _DHCPD_LOCK:
            _DHCPD_STATE["running"] = False


def _dhcpd_maybe_report(cfg, now: float) -> None:  # noqa: ANN001
    """poll 回應帶了 `dhcpd`（伺服器上有 ISC DHCP 來源指到這台代理）才讀檔；沒有就什麼都不做。"""
    if not isinstance(cfg, dict) or not cfg.get("source_id"):
        return
    interval = max(60, int(cfg.get("interval_seconds") or 300))
    with _DHCPD_LOCK:
        if _DHCPD_STATE["running"] or now - _DHCPD_STATE["last"] < interval:
            return
        _DHCPD_STATE["running"], _DHCPD_STATE["last"] = True, now
    threading.Thread(target=_dhcpd_report_once, args=(str(cfg["source_id"]),),
                     name="jt-ipam-dhcpd", daemon=True).start()


def _subnet_chunk(subnet_id: str, cidr: str) -> tuple[list[str], int, dict]:
    """這輪要掃的位址、子網路總位址數、{chunk, rounds}。

    比單輪上限（MAX_HOSTS）大的子網路分段輪替：每輪掃下一段，掃完一遍從頭開始。以前是
    永遠只掃前 1024 個，後面的位址永遠不會被看到，畫面上也沒有任何提示。
    不為了算總數把整個大網段展開（/8 有一千六百萬個位址）。
    """
    net = ipaddress.ip_network(cidr, strict=False)
    if not isinstance(net, ipaddress.IPv4Network):
        return [], 0, {"chunk": 1, "rounds": 1}   # this build scans IPv4 only
    total = max(net.num_addresses - (2 if net.prefixlen < 31 else 0), 0)
    if total <= MAX_HOSTS:
        return [str(h) for h in net.hosts()], total, {"chunk": 1, "rounds": 1}
    rounds = -(-total // MAX_HOSTS)
    start = _chunk_pos.get(subnet_id, 0)
    if start >= total:
        start = 0
    first = int(net.network_address) + (1 if net.prefixlen < 31 else 0) + start
    count = min(MAX_HOSTS, total - start)
    hosts = [str(ipaddress.IPv4Address(first + i)) for i in range(count)]
    _chunk_pos[subnet_id] = start + count if start + count < total else 0
    return hosts, total, {"chunk": start // MAX_HOSTS + 1, "rounds": rounds}


def _parse_runs(raw: list, lo: int, hi: int) -> list[tuple[int, int]]:
    """伺服器給的 [[起, 迄], …]（字串）→ 整數區段，裁到子網路可用範圍內、排序。壞的略過。"""
    out: list[tuple[int, int]] = []
    for item in raw or []:
        try:
            a = int(ipaddress.IPv4Address(str(item[0])))
            b = int(ipaddress.IPv4Address(str(item[1])))
        except (ValueError, TypeError, IndexError):
            continue
        a, b = max(a, lo), min(b, hi)
        if a <= b:
            out.append((a, b))
    return sorted(out)


def _runs_slice(runs: list[tuple[int, int]], start: int, count: int) -> list[int]:
    """區段串起來後，從第 start 個開始取 count 個（不展開整個清單：區段可能是一整段 /16）。"""
    out: list[int] = []
    for a, b in runs:
        n = b - a + 1
        if start >= n:
            start -= n
            continue
        take = min(n - start, count - len(out))
        out.extend(range(a + start, a + start + take))
        start = 0
        if len(out) >= count:
            break
    return out


def _in_runs(runs: list[tuple[int, int]], starts: list[int], n: int) -> bool:
    i = bisect.bisect_right(starts, n) - 1
    return i >= 0 and runs[i][0] <= n <= runs[i][1]


def _plan_hosts(subnet_id: str, cidr: str, plan: dict | None) -> tuple[list[str], int, dict]:
    """這輪要掃的位址（客戶 2026-10-11：10.0.0.0/8 讓掃描炸掉，伺服器 1.18.0 起會給大子網路掃描計畫）。

    沒有計畫＝照舊全掃（_subnet_chunk，太大就分段輪替）。有計畫（mode=targets）：
    - 已知位址每一輪都掃（超過單輪上限就輪替）→ 回報的 rounds 是已知位址繞一圈的輪數，伺服器只拿它判斷超載
    - discover：其餘位址每輪往後看一段（per_cycle 個），已知的不重複探測；繞完一圈從頭開始
    - discover.near（伺服器 2026-10-20 起）：已經有設備的 /24 裡其餘的位址，每輪先掃 near_per_cycle 個、繞完從頭。
      /8 的全域探索要約 57 天一圈，新接的設備最可能出現在已經有設備的網段（客戶的印表機五、六小時沒被發現）
    """
    if not plan or plan.get("mode") != "targets":
        return _subnet_chunk(subnet_id, cidr)
    net = ipaddress.ip_network(cidr, strict=False)
    if not isinstance(net, ipaddress.IPv4Network):
        return [], 0, {"chunk": 1, "rounds": 1}
    edge = 1 if net.prefixlen < 31 else 0
    lo = int(net.network_address) + edge
    total = max(net.num_addresses - 2 * edge, 0)
    hi = lo + total - 1
    runs = _parse_runs(plan.get("known") or [], lo, hi)
    known_total = sum(b - a + 1 for a, b in runs)
    if known_total <= MAX_HOSTS:
        k_start, k_rounds = 0, 1
    else:
        k_rounds = -(-known_total // MAX_HOSTS)
        k_start = _chunk_pos.get(f"{subnet_id}:k", 0)
        if k_start >= known_total:
            k_start = 0
        nxt = k_start + MAX_HOSTS
        _chunk_pos[f"{subnet_id}:k"] = nxt if nxt < known_total else 0
    picked = _runs_slice(runs, k_start, min(MAX_HOSTS, known_total - k_start))
    hosts = [str(ipaddress.IPv4Address(n)) for n in picked]
    info: dict = {"mode": "targets", "known": known_total, "chunk": k_start // MAX_HOSTS + 1, "rounds": k_rounds}
    disc = plan.get("discover")
    if disc and total:
        try:
            per = int(disc.get("per_cycle") or 0)
        except (TypeError, ValueError):
            per = 0
        per = max(1, min(per or 1, MAX_HOSTS))
        near = _parse_runs(disc.get("near") or [], lo, hi)
        near_total = sum(b - a + 1 for a, b in near)
        if near_total:
            try:
                n_per = int(disc.get("near_per_cycle") or 0)
            except (TypeError, ValueError):
                n_per = 0
            n_per = max(1, min(n_per or 1, MAX_HOSTS))
            n_start = _chunk_pos.get(f"{subnet_id}:n", 0)
            if n_start >= near_total:
                n_start = 0
            k_starts = [a for a, _ in runs]
            for n in _runs_slice(near, n_start, min(n_per, near_total - n_start)):
                if not _in_runs(runs, k_starts, n):
                    hosts.append(str(ipaddress.IPv4Address(n)))
            nxt_n = n_start + n_per
            _chunk_pos[f"{subnet_id}:n"] = nxt_n if nxt_n < near_total else 0
            info.update(near=near_total, near_chunk=n_start // n_per + 1, near_rounds=-(-near_total // n_per))
        d_start = _chunk_pos.get(f"{subnet_id}:d", 0)
        if d_start >= total:
            d_start = 0
        starts = [a for a, _ in runs]
        for n in range(lo + d_start, lo + min(d_start + per, total)):
            if not _in_runs(runs, starts, n):
                hosts.append(str(ipaddress.IPv4Address(n)))
        nxt = d_start + per
        _chunk_pos[f"{subnet_id}:d"] = nxt if nxt < total else 0
        info.update(discover_chunk=d_start // per + 1, discover_rounds=-(-total // per))
    return hosts, total, info


def _due(subnet_id, probe: str, intervals: dict, fast: int, now: float) -> bool:
    """這個探測本輪是否該對此子網執行（依 per-probe cadence 節流）。

    輕量探測 cadence 預設等於 fast loop；重量探測（os/ports）用 intervals 指定的大週期。
    第一次（沒有 last_run 記錄）一律執行。
    """
    cadence = int(intervals.get(probe, fast) or fast)
    key = (subnet_id, probe)
    last = _last_run.get(key)
    if last is None:
        return True
    return (now - last) >= cadence


# 上線偵測（每輪都要跑完、立刻回報）與重量探測（對在線主機逐台查，丟給背景慢慢跑）分開。
# 以前全部串在一起、整輪跑完才回報：OS 指紋那一輪跑一個小時，這一小時內所有子網路的上線狀態
# 都沒有更新，連自動更新都卡住（正式環境 2026-09-28 實際發生）。
LIGHT_PROBES = ("icmp", "tcp", "arp", "dhcp")
HEAVY_WORKERS = int(os.environ.get("JT_IPAM_HEAVY_WORKERS", "16"))   # 背景同時查幾台
NMAP_WORKERS = int(os.environ.get("JT_IPAM_NMAP_WORKERS", "4"))      # 其中同時跑 nmap 的上限


def _split_probes(due: list[str]) -> tuple[list[str], list[str]]:
    """本輪到期的探測分成「上線偵測」與「重量探測」兩組（保持原本順序）。"""
    return [p for p in due if p in LIGHT_PROBES], [p for p in due if p not in LIGHT_PROBES]


class _HeavyQueue:
    """背景重量探測的待辦：同一台主機只排一次（還沒跑到又被排一次就合併探測項目）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, tuple[str, list[str]]] = {}

    def submit(self, subnet_id: str, ip: str, probes: list[str]) -> None:
        with self._lock:
            if ip in self._items:
                sid, old = self._items[ip]
                self._items[ip] = (sid, old + [p for p in probes if p not in old])
            else:
                self._items[ip] = (subnet_id, list(probes))

    def take(self, n: int) -> list[tuple[str, list[str]]]:
        with self._lock:
            keys = list(self._items)[:n]
            return [(k, self._items.pop(k)[1]) for k in keys]

    def pending(self) -> int:
        with self._lock:
            return len(self._items)


_HEAVY = _HeavyQueue()
_HEAVY_STATS: dict[str, float] = {"done": 0, "last_batch_s": 0.0}


NAME_PROBES = ("rdns", "netbios", "mdns")
HEAVY_FLUSH_S = 20.0     # 查完的結果最多等多久就先回報


def _heavy_names(ip: str, probes: list[str]) -> dict:
    """名稱查詢（反解／NetBIOS／mDNS）：幾秒內完成。"""
    item: dict = {"ip": ip, "alive": True, "liveness": False}
    probes_run: list[str] = []
    if "rdns" in probes:
        probes_run.append("rdns")
        rd, no_ptr = _rdns(ip)
        if rd:
            item["rdns"] = rd
        elif no_ptr:
            item["rdns"] = ""     # DNS 明確說沒有 → 伺服器清掉舊名（1.8.1 起）
    if "netbios" in probes:
        probes_run.append("netbios")
        nb = _netbios(ip)
        if nb:
            item["netbios"] = nb
    if "mdns" in probes:
        probes_run.append("mdns")
        md = _mdns(ip)
        if md:
            item["mdns"] = md
    item["probes_run"] = probes_run
    return item


def _heavy_nmap(ip: str, probes: list[str]) -> dict:
    """OS 指紋／連接埠（nmap）：每台可能要一分多鐘。"""
    want_os, want_ports = "os" in probes, "ports" in probes
    item: dict = {"ip": ip, "alive": True, "liveness": False}
    np = _nmap_os_ports(ip, want_os, want_ports)
    item["probes_run"] = [p for p in ("os", "ports") if p in probes]
    if np.get("os_guess"):
        item["os_guess"] = np["os_guess"]
    if np.get("open_ports"):
        item["open_ports"] = sorted(set(np["open_ports"]))
    if np.get("nmap"):
        item["nmap"] = np["nmap"]
    return item


def _heavy_probe_host(ip: str, probes: list[str]) -> dict:
    """對一台在線主機跑完所有重量探測，合成一筆。結果**不是上線證據**（liveness=False）：
    反解是 DNS 回答的，不是主機本身；排進佇列到真正跑到之間也可能隔了幾分鐘。"""
    item = _heavy_names(ip, probes)
    if "os" in probes or "ports" in probes:
        np = _heavy_nmap(ip, probes)
        item["probes_run"] = item["probes_run"] + np.pop("probes_run")
        item.update({k: v for k, v in np.items() if k in ("os_guess", "open_ports", "nmap")})
    return item


def _heavy_drain(q: _HeavyQueue, batch: int = 50) -> int:
    """把佇列裡的主機跑完。名稱查詢與 nmap 各用自己的工作池（nmap 很慢，不能讓它佔住名稱查詢），
    查完的結果累積到一批、或最多等 HEAVY_FLUSH_S 秒就先回報。回傳處理了幾台。"""
    done = 0
    buf: list[dict] = []
    last_flush = time.time()

    def flush() -> None:
        nonlocal buf, last_flush
        # 伺服器重啟（部署）的那幾秒會回 502：背景結果是花好幾分鐘跑出來的，丟了要等下一個週期
        # 才會重跑，所以隔幾秒再送，最多三次
        for attempt in range(3):
            if not buf:
                break
            try:
                _req("POST", "/api/v1/scan-agents/report", {"results": buf})
                break
            except Exception as exc:  # noqa: BLE001
                print(f"[heavy] report failed ({attempt + 1}/3): {type(exc).__name__}: {exc}",
                      file=sys.stderr, flush=True)
                if attempt < 2:
                    time.sleep(10 * (attempt + 1))
        buf = []
        last_flush = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, HEAVY_WORKERS)) as names_pool, \
            concurrent.futures.ThreadPoolExecutor(max_workers=max(1, NMAP_WORKERS)) as nmap_pool:
        while True:
            work = q.take(batch)
            if not work:
                break
            started = time.time()
            futs = []
            for ip, probes in work:
                if any(p in NAME_PROBES for p in probes):
                    futs.append(names_pool.submit(_heavy_names, ip, probes))
                if "os" in probes or "ports" in probes:
                    futs.append(nmap_pool.submit(_heavy_nmap, ip, probes))
            pending = set(futs)
            while pending:
                finished, pending = concurrent.futures.wait(pending, timeout=1.0)
                for f in finished:
                    try:
                        buf.append(f.result())
                    except Exception as exc:  # noqa: BLE001 -- 一台失敗不影響其他台
                        print(f"[heavy] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                    if len(buf) >= batch:
                        flush()
                if buf and time.time() - last_flush >= HEAVY_FLUSH_S:
                    flush()
            flush()
            done += len(work)
            _HEAVY_STATS["done"] += len(work)
            _HEAVY_STATS["last_batch_s"] = round(time.time() - started, 1)
            print(f"[heavy] {len(work)} hosts in {_HEAVY_STATS['last_batch_s']}s, "
                  f"{q.pending()} still queued", flush=True)
    return done


def _heavy_loop() -> None:
    """背景執行緒：有待辦就跑，沒有就等。任何錯誤都不能讓它停掉（也不能影響上線偵測）。"""
    while True:
        try:
            if _HEAVY.pending():
                _heavy_drain(_HEAVY)
            else:
                time.sleep(2)
        except Exception as exc:  # noqa: BLE001
            print(f"[heavy] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            time.sleep(10)


def scan_once() -> None:
    _reap_inherited()
    cycle_started = time.time()
    caps = _capabilities()
    poll = _req("GET", "/api/v1/scan-agents/poll",
                extra_headers={"X-Agent-Probes": ",".join(caps),
                               "X-Agent-Tools": _tools_header(),
                               "X-Agent-Relay": _relay_header()})
    _relay_set_assigned(poll.get("relay_cidrs") or [], poll.get("relay_ports") or [], poll.get("relay_max") or 0)
    _maybe_self_update(poll.get("agent_sha"))
    subnets = poll.get("subnets") or []
    fast = int(poll.get("interval_seconds") or INTERVAL)
    _CURRENT_FAST[0] = fast  # 給 main 的 sleep 用
    intervals = poll.get("intervals") or {}
    ip_overrides = poll.get("ip_overrides") or {}
    # 「立刻執行一次」：server 要求強制本輪全跑 → 清掉各探測的上次執行時間（全部判定到期）
    if poll.get("force_scan"):
        _last_run.clear()
        print("[poll] force_scan: running all probes now", flush=True)
    print(f"[poll] agent={poll.get('agent')} subnets={len(subnets)} "
          f"fast={fast}s caps={','.join(caps)}", flush=True)
    # 獨立 ISC DHCP Server：伺服器指派了才讀本機 dhcpd 檔（背景執行，不佔這一輪的時間）
    _dhcpd_maybe_report(poll.get("dhcpd"), time.time())
    # SNMP 收集：伺服器指派給這台代理的網路設備（憑證只放記憶體；背景執行緒依各區段間隔讀取）
    _snmp_set_assigned(poll.get("snmp") or [], poll.get("snmp_budget") or {})

    cap_set = set(caps)
    now = time.time()
    subnet_stats: list[dict] = []

    for s in subnets:
        cidr = s.get("cidr")
        if not cidr:
            continue
        sub_started = time.time()
        subnet_id = s.get("subnet_id")
        # server 要求的探測；缺/空 -> 向下相容用 icmp。再交集本機能力。
        requested = s.get("probes") or list(DEFAULT_PROBES)
        requested = [p for p in requested if p in ALL_PROBES]
        if not requested:
            requested = list(DEFAULT_PROBES)
        # 本輪實際對此子網要跑哪些探測（能力 ∩ 請求 ∩ cadence-due）
        due = [p for p in requested
               if p in cap_set and _due(subnet_id, p, intervals, fast, now)]
        # 標記這些探測本輪已跑（即使後面某 host 失敗，cadence 仍以本輪為準）
        for p in due:
            _last_run[(subnet_id, p)] = now

        if not due:
            print(f"  {cidr}: no probe due this cycle", flush=True)
            continue
        light, heavy = _split_probes(due)

        # DHCP 偵測是「對整個網段問一次」，不是逐台問 —— 它找的是誰在發 IP。
        dhcp_servers: list[dict] = []
        if "dhcp" in light:
            offers = _dhcp_discover()
            # 先問完再讀 arp 表：剛跟我們講過話的主機這時才會在表裡
            neigh = _arp_table() if offers else {}
            for srv in offers:
                srv["subnet_cidr"] = cidr
                srv["mac"] = neigh.get(srv["server_ip"])
                dhcp_servers.append(srv)
            print(f"  {cidr}: dhcp offers={len(dhcp_servers)}", flush=True)

        hosts, total_hosts, chunk = _plan_hosts(str(subnet_id or cidr), cidr, s.get("plan"))
        if chunk.get("mode") == "targets":
            print(f"  subnet {cidr}: {chunk['known']} known addresses"
                  + (f" (part {chunk['chunk']}/{chunk['rounds']})" if chunk["rounds"] > 1 else "")
                  + (f", discovering part {chunk['discover_chunk']}/{chunk['discover_rounds']}"
                     if chunk.get("discover_rounds") else ""), flush=True)
        elif chunk["rounds"] > 1 and hosts:
            print(f"  subnet {cidr} has {total_hosts} hosts -> scanning part {chunk['chunk']}/{chunk['rounds']} "
                  f"({hosts[0]}-{hosts[-1]})", flush=True)
        # arp 表用於 arp 探測，也順手在其他探測時補 mac。
        arp = _arp_table()

        icmp_alive: dict[str, bool] = {}
        if "icmp" in light:
            with concurrent.futures.ThreadPoolExecutor(max_workers=PING_WORKERS) as ex:
                for ip, ok in zip(hosts, ex.map(_ping, hosts)):
                    icmp_alive[ip] = bool(ok)

        tcp_ports: dict[str, list[int]] = {}
        if "tcp" in light:
            with concurrent.futures.ThreadPoolExecutor(max_workers=PING_WORKERS) as ex:
                for ip, ports in zip(hosts, ex.map(_tcp_scan, hosts)):
                    if ports:
                        tcp_ports[ip] = ports

        results: list[dict] = []
        for ip in hosts:
            # 套用 per-IP override：略過指定探測
            skip = set(ip_overrides.get(ip) or [])
            host_light = [p for p in light if p not in skip]
            host_heavy = [p for p in heavy if p not in skip]
            if not host_light and not host_heavy:
                continue

            alive = False
            item: dict = {"ip": ip}
            probes_run: list[str] = []
            if "icmp" in host_light:
                probes_run.append("icmp")
                if icmp_alive.get(ip):
                    alive = True
            if "tcp" in host_light:
                probes_run.append("tcp")
                ports = tcp_ports.get(ip) or []
                if ports:
                    alive = True
                    item["open_ports"] = sorted(set(ports))
            mac = arp.get(ip)
            if "arp" in host_light:
                probes_run.append("arp")
                if mac:
                    alive = True  # 在 neigh 表代表本子網有回應過
            if mac:
                item["mac"] = mac   # 即使沒跑 arp 探測，arp 表剛好有資料也順手補 mac
            # SNMP 不在逐主機探測裡：不對網段每台主機試社群字串（v2c 是明文，等於送給每一台）。
            # 只對伺服器指派的網路設備、用下發的憑證唯讀讀取（_snmp_loop，見 SNMP collection 一節）
            if not alive:
                continue
            item["alive"] = True
            item["probes_run"] = probes_run
            results.append(item)
            # 重量探測只對在線主機、丟給背景跑，不佔住這一輪
            if host_heavy:
                _HEAVY.submit(subnet_id, ip, host_heavy)

        # 每個子網路做完就回報，不等其他子網路（大子網路或慢的子網路不會拖住別人）
        if results or dhcp_servers:
            payload: dict = {"results": results}
            if dhcp_servers:
                payload["dhcp_servers"] = dhcp_servers
            r = _req("POST", "/api/v1/scan-agents/report", payload)
            print(f"  {cidr}: probes={'+'.join(light) or '-'} alive={len(results)}/{len(hosts)} "
                  f"queued={'+'.join(heavy) or '-'} updated={r.get('updated')}", flush=True)
        subnet_stats.append({"cidr": cidr, "hosts": len(hosts), "total_hosts": total_hosts,
                             "alive": len(results), "truncated": False, **chunk,
                             "duration_s": round(time.time() - sub_started, 1)})

    # 整輪統計：負載顯示與超載通知用（耗時 ÷ 週期、背景待辦量）
    _req("POST", "/api/v1/scan-agents/report", {"results": [], "cycle": {
        "duration_s": round(time.time() - cycle_started, 1),
        "interval_s": fast,
        "subnets": subnet_stats,
        "heavy_backlog": _HEAVY.pending(),
        "heavy_done": int(_HEAVY_STATS["done"]),
        "heavy_last_batch_s": _HEAVY_STATS["last_batch_s"],
        "agent_version": AGENT_VERSION,
    }})


# ─────────────────── SNMP collection (server-assigned targets only, 1.19.0) ───────────────────
# Principle (replaces the old "never run probes that need credentials" rule, 2026-10-15):
#   * only the targets the server assigns to THIS agent (network devices the admin picked), never a sweep:
#     a v2c community travels in clear text, so trying it against every host would hand it to every host
#   * only the credentials the server sends for that target; kept in memory only, handed to net-snmp through
#     a private 0600 snmp.conf in a fresh 0700 temp directory (never on argv, so `ps` cannot show them),
#     deleted right after each command
#   * read-only GET / GETNEXT / GETBULK of the OIDs hard-coded below (SNMP_OIDS); nothing else is ever asked
#   * gentle on the device: one walk at a time per agent, a pause between walks, small max-repetitions,
#     a time limit per walk, a row cap per walk and a time budget per device
# net-snmp's command line tools are used because the agent is standard-library only and SNMPv3 privacy
# needs AES; the tools exist only when the "snmp" package is installed (capability reported as such).
SNMP_OIDS = {
    # SNMPv2-MIB system group
    "sysDescr": "1.3.6.1.2.1.1.1.0", "sysObjectID": "1.3.6.1.2.1.1.2.0",
    "sysUpTime": "1.3.6.1.2.1.1.3.0", "sysName": "1.3.6.1.2.1.1.5.0",
    # IF-MIB
    "ifDescr": "1.3.6.1.2.1.2.2.1.2", "ifType": "1.3.6.1.2.1.2.2.1.3", "ifPhysAddress": "1.3.6.1.2.1.2.2.1.6",
    "ifAdminStatus": "1.3.6.1.2.1.2.2.1.7", "ifOperStatus": "1.3.6.1.2.1.2.2.1.8",
    "ifName": "1.3.6.1.2.1.31.1.1.1.1", "ifAlias": "1.3.6.1.2.1.31.1.1.1.18",
    # IP-MIB: neighbour (ARP / ND) table, new and legacy
    "ipNetToPhysicalPhysAddress": "1.3.6.1.2.1.4.35.1.4", "ipNetToPhysicalType": "1.3.6.1.2.1.4.35.1.6",
    "ipNetToPhysicalState": "1.3.6.1.2.1.4.35.1.7",
    "ipNetToMediaPhysAddress": "1.3.6.1.2.1.4.22.1.2", "ipNetToMediaType": "1.3.6.1.2.1.4.22.1.4",
    # IP-MIB: the device's own addresses, new and legacy
    "ipAddressIfIndex": "1.3.6.1.2.1.4.34.1.3", "ipAddressType": "1.3.6.1.2.1.4.34.1.4",
    "ipAdEntIfIndex": "1.3.6.1.2.1.4.20.1.2",
    # Q-BRIDGE-MIB / BRIDGE-MIB: MAC address table
    "dot1qTpFdbPort": "1.3.6.1.2.1.17.7.1.2.2.1.2", "dot1qVlanFdbId": "1.3.6.1.2.1.17.7.1.4.2.1.3",
    "dot1dTpFdbPort": "1.3.6.1.2.1.17.4.3.1.2", "dot1dBasePortIfIndex": "1.3.6.1.2.1.17.1.4.1.2",
    # LLDP-MIB
    "lldpLocPortIdSubtype": "1.0.8802.1.1.2.1.3.7.1.2", "lldpLocPortId": "1.0.8802.1.1.2.1.3.7.1.3",
    "lldpLocPortDesc": "1.0.8802.1.1.2.1.3.7.1.4",
    "lldpRemChassisIdSubtype": "1.0.8802.1.1.2.1.4.1.1.4", "lldpRemChassisId": "1.0.8802.1.1.2.1.4.1.1.5",
    "lldpRemPortIdSubtype": "1.0.8802.1.1.2.1.4.1.1.6", "lldpRemPortId": "1.0.8802.1.1.2.1.4.1.1.7",
    "lldpRemPortDesc": "1.0.8802.1.1.2.1.4.1.1.8", "lldpRemSysName": "1.0.8802.1.1.2.1.4.1.1.9",
    "lldpRemSysDesc": "1.0.8802.1.1.2.1.4.1.1.10", "lldpRemManAddrIfSubtype": "1.0.8802.1.1.2.1.4.2.1.3",
    # CISCO-CDP-MIB cdpCacheTable
    "cdpCacheAddressType": "1.3.6.1.4.1.9.9.23.1.2.1.1.3", "cdpCacheAddress": "1.3.6.1.4.1.9.9.23.1.2.1.1.4",
    "cdpCacheDeviceId": "1.3.6.1.4.1.9.9.23.1.2.1.1.6", "cdpCacheDevicePort": "1.3.6.1.4.1.9.9.23.1.2.1.1.7",
    "cdpCachePlatform": "1.3.6.1.4.1.9.9.23.1.2.1.1.8",
    # CISCO-VTP-MIB vtpVlanState (which VLANs exist, for the per-VLAN MAC table on Cisco)
    "vtpVlanState": "1.3.6.1.4.1.9.9.46.1.3.1.1.2",
}
_SNMP_ALLOWED = frozenset(SNMP_OIDS.values())
SNMP_SECTIONS = ("arp", "fdb", "neighbors", "interfaces")
SNMP_MIN_INTERVAL = 60
_SNMP_AUTH = {"MD5": "MD5", "SHA": "SHA", "SHA-224": "SHA-224", "SHA-256": "SHA-256",
              "SHA-384": "SHA-384", "SHA-512": "SHA-512"}
_SNMP_PRIV = {"DES": "DES", "AES-128": "AES", "AES-192": "AES-192", "AES-256": "AES-256"}
_SNMP_LEVELS = ("authPriv", "authNoPriv")
# Defaults and hard ceilings for the server-sent budget (the server cannot make us hammer a device)
_SNMP_BUDGET_LIMITS = {"target_seconds": (300, 30, 600), "walk_seconds": (120, 5, 300),
                       "max_rows": (150000, 100, 200000), "max_repetitions": (10, 1, 25),
                       "pause_ms": (200, 0, 5000), "max_vlans": (64, 0, 128)}
_SNMP_ABSENT = ("No Such Object", "No Such Instance", "No more variables left")
_SNMP_REPORT_SPLIT = 12 * 1024 * 1024      # nginx accepts 16 MB; above this, one request per section
_SNMP_STATE: dict = {"targets": [], "budget": {}}
_SNMP_LAST: dict[tuple, float] = {}
_SNMP_STATE_LOCK = threading.Lock()
_SNMP_DEV_LOCKS: dict[str, threading.Lock] = {}


def _snmp_available() -> bool:
    return bool(shutil.which("snmpbulkwalk") and shutil.which("snmpget"))


def _snmp_budget(raw) -> dict:  # noqa: ANN001
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for k, (default, lo, hi) in _SNMP_BUDGET_LIMITS.items():
        try:
            v = int(raw.get(k, default))
        except (TypeError, ValueError):
            v = default
        out[k] = max(lo, min(v, hi))
    return out


def _snmp_clean_str(v, limit: int = 128, spaces: bool = True) -> str | None:  # noqa: ANN001
    """A credential value we are willing to write into snmp.conf: no control characters at all
    (a newline would let the sender add arbitrary net-snmp directives), bounded length."""
    if not isinstance(v, str) or not v or len(v) > limit:
        return None
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in v):
        return None
    if not spaces and " " in v:
        return None
    return v


def _snmp_cred_ok(cred) -> dict | None:  # noqa: ANN001
    """Validate a server-sent credential ourselves (never trust the server's validation)."""
    if not isinstance(cred, dict):
        return None
    out = {"credential_id": str(cred.get("credential_id") or "")[:64]}
    try:
        out["timeout_s"] = max(1, min(int(cred.get("timeout_s") or 5), 30))
        out["retries"] = max(0, min(int(cred.get("retries") or 0), 5))
    except (TypeError, ValueError):
        return None
    if cred.get("version") == "v2c":
        community = _snmp_clean_str(cred.get("community"))
        if not community:
            return None
        out.update(version="v2c", community=community)
        return out
    if cred.get("version") != "v3":
        return None
    user = _snmp_clean_str(cred.get("username"), 64, spaces=False)
    level = cred.get("security_level")
    auth = _SNMP_AUTH.get(cred.get("auth_protocol"))
    apass = _snmp_clean_str(cred.get("auth_passphrase"))
    if not user or level not in _SNMP_LEVELS or not auth or not apass:
        return None
    out.update(version="v3", username=user, security_level=level, auth_protocol=auth, auth_passphrase=apass)
    if level == "authPriv":
        priv = _SNMP_PRIV.get(cred.get("priv_protocol"))
        ppass = _snmp_clean_str(cred.get("priv_passphrase"))
        if not priv or not ppass:
            return None
        out.update(priv_protocol=priv, priv_passphrase=ppass)
    return out


def _snmp_target_ok(spec) -> dict | None:  # noqa: ANN001
    """A server-sent target: an IP literal (no host names, no multicast), a port, known sections."""
    if not isinstance(spec, dict):
        return None
    try:
        ip = ipaddress.ip_address(str(spec.get("ip") or "").strip())
        port = int(spec.get("port") or 161)
    except (TypeError, ValueError):
        return None
    if ip.is_multicast or ip.is_unspecified or not 1 <= port <= 65535:
        return None
    sections = {}
    for k, v in (spec.get("sections") or {}).items() if isinstance(spec.get("sections"), dict) else []:
        if k in SNMP_SECTIONS:
            try:
                sections[k] = max(SNMP_MIN_INTERVAL, min(int(v), 86400))
            except (TypeError, ValueError):
                continue
    return {"target_id": str(spec.get("target_id") or "")[:64], "ip": str(ip), "port": port,
            "sections": sections}


def _snmp_conf_value(v: str) -> str:
    """net-snmp reads the rest of the line as the value unless it starts with a double quote, and cuts a line
    at '#' (comment) outside quotes; quote (with backslash escapes) when the value starts with a quote, has
    surrounding blanks or contains '#' or a backslash."""
    if v.startswith('"') or v != v.strip() or "#" in v or "\\" in v:
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return v


def _snmp_conf_text(cred: dict, community_suffix: str = "") -> str:
    lines = ["mibs :", "hexOutputLength 0"]
    if cred["version"] == "v2c":
        lines += ["defVersion 2c", "defCommunity " + _snmp_conf_value(cred["community"] + community_suffix)]
    else:
        lines += ["defVersion 3", "defSecurityName " + _snmp_conf_value(cred["username"]),
                  "defSecurityLevel " + cred["security_level"], "defAuthType " + cred["auth_protocol"],
                  "defAuthPassphrase " + _snmp_conf_value(cred["auth_passphrase"])]
        if cred["security_level"] == "authPriv":
            lines += ["defPrivType " + cred["priv_protocol"],
                      "defPrivPassphrase " + _snmp_conf_value(cred["priv_passphrase"])]
    return "\n".join(lines) + "\n"


def _snmp_parse(text: str) -> list[tuple[str, str]]:
    """`-On -Oq` output -> [(oid, raw value)]. A line that does not start with '.' continues the previous
    value (old net-snmp versions wrap long hex strings even with hexOutputLength 0)."""
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        if line.startswith("."):
            oid, _, val = line.partition(" ")
            out.append((oid[1:], val.strip()))
        elif out and line.strip():
            oid, val = out[-1]
            out[-1] = (oid, (val + " " + line.strip()).strip())
    return out


def _snmp_absent(val: str) -> bool:
    return any(val.startswith(m) for m in _SNMP_ABSENT)


def _snmp_bytes(val: str) -> bytes | None:
    """`"4C 69 6E "` (hex, from -Ox) -> bytes. Plain quoted text (some agents' empty strings) -> its bytes."""
    s = val.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    parts = s.split()
    if parts and all(len(p) == 2 and all(c in "0123456789abcdefABCDEF" for c in p) for p in parts):
        return bytes(int(p, 16) for p in parts)
    return s.encode("utf-8", "replace") if s else b""


def _snmp_text(val: str, limit: int = 255, first_line: bool = False) -> str | None:
    b = _snmp_bytes(val)
    if b is None:
        return None
    t = b.decode("utf-8", "replace").replace("\x00", "").strip()
    if first_line:
        t = (t.splitlines() or [""])[0]
    # line breaks / tabs become spaces (Cisco's sysDescr is several lines); other control characters go
    t = " ".join("".join(c if c >= " " else (" " if c in "\r\n\t" else "") for c in t).split())
    return t[:limit] or None


def _snmp_mac(val) -> str | None:  # noqa: ANN001
    b = _snmp_bytes(val) if isinstance(val, str) else val
    if not b or len(b) != 6:
        return None
    return ":".join(f"{x:02x}" for x in b)


def _snmp_int(val: str) -> int | None:
    try:
        return int(str(val).strip().split()[0])
    except (ValueError, IndexError):
        return None


def _snmp_suffix(oid: str, col: str) -> list[int] | None:
    if not oid.startswith(col + "."):
        return None
    try:
        return [int(x) for x in oid[len(col) + 1:].split(".")]
    except ValueError:
        return None


def _snmp_inet(parts: list[int]) -> tuple[str | None, list[int]]:
    """InetAddressType.InetAddress (length-prefixed) at the start of an index -> (ip, rest)."""
    if len(parts) < 2:
        return None, []
    typ, ln = parts[0], parts[1]
    raw = parts[2:2 + ln]
    try:
        if typ in (1, 3) and ln in (4, 8) and len(raw) == ln:       # ipv4 / ipv4z (zone index dropped)
            return str(ipaddress.IPv4Address(bytes(raw[:4]))), parts[2 + ln:]
        if typ in (2, 4) and ln in (16, 20) and len(raw) == ln:     # ipv6 / ipv6z
            return ipaddress.IPv6Address(bytes(raw[:16])).compressed, parts[2 + ln:]
    except (ValueError, TypeError):
        pass
    return None, parts[2 + ln:]


class _SnmpSession:
    """One device, one credential, one run: runs net-snmp commands with the budget applied."""

    def __init__(self, ip: str, port: int, cred: dict, budget: dict) -> None:
        self.ip, self.port, self.cred, self.budget = ip, port, cred, budget
        self.deadline = time.monotonic() + budget["target_seconds"]
        self._ifnames: dict | None = None
        self.sys_object_id = ""

    def host(self) -> str:
        return f"udp6:[{self.ip}]:{self.port}" if ":" in self.ip else f"udp:{self.ip}:{self.port}"

    def run(self, tool: str, oids: list, *, context: str | None = None, community_suffix: str = "",
            limit_rows: int | None = None) -> tuple[list[tuple[str, str]], str, str | None]:
        """-> (varbinds, status ok|partial|error|absent, error). Never raises."""
        if tool not in ("snmpget", "snmpbulkwalk") or not oids or any(o not in _SNMP_ALLOWED for o in oids):
            return [], "error", "OID not in the allowlist"
        if tool == "snmpbulkwalk" and len(oids) != 1:
            return [], "error", "one subtree per walk"
        remaining = self.deadline - time.monotonic()
        if remaining <= 1:
            return [], "error", "device time budget used up"
        max_rows = min(limit_rows or self.budget["max_rows"], self.budget["max_rows"])
        argv = [tool, "-On", "-Oq", "-Ox", "-Oe", "-Ot", "-OU",
                "-t", str(self.cred["timeout_s"]), "-r", str(self.cred["retries"])]
        if tool == "snmpbulkwalk":
            argv.append(f"-Cr{self.budget['max_repetitions']}")
        if context:
            argv += ["-n", context]          # SNMPv3 context name (vlan-N): not a secret
        argv += [self.host(), *oids]
        tmp = tempfile.mkdtemp(prefix="jt-snmp-")       # 0700
        try:
            fd = os.open(os.path.join(tmp, "snmp.conf"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(_snmp_conf_text(self.cred, community_suffix))
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "SNMPCONFPATH": tmp,
                   "SNMP_PERSISTENT_DIR": tmp, "MIBDIRS": tmp, "HOME": tmp, "LC_ALL": "C"}
            return self._exec(argv, env, min(self.budget["walk_seconds"], remaining), max_rows)
        except Exception as exc:  # noqa: BLE001
            return [], "error", f"{type(exc).__name__}: {exc}"[:300]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            time.sleep(self.budget["pause_ms"] / 1000.0)

    @staticmethod
    def _exec(argv: list, env: dict, limit_s: float, max_rows: int) -> tuple[list, str, str | None]:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,  # noqa: S603
                                stdin=subprocess.DEVNULL)
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ, "out")
        sel.register(proc.stderr, selectors.EVENT_READ, "err")
        out, err = bytearray(), bytearray()
        lines, cut, open_streams = 0, None, 2
        end = time.monotonic() + limit_s
        try:
            while open_streams:
                left = end - time.monotonic()
                if left <= 0:
                    cut = "timeout"
                    break
                for key, _ev in sel.select(timeout=min(left, 1.0)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        sel.unregister(key.fileobj)
                        open_streams -= 1
                        continue
                    if key.data == "out":
                        lines += chunk.count(b"\n")
                        out += chunk
                    elif len(err) < 4096:
                        err += chunk
                if lines > max_rows:
                    cut = "rows"
                    break
        finally:
            sel.close()
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            for s in (proc.stdout, proc.stderr):
                try:
                    s.close()
                except Exception:  # noqa: BLE001
                    pass
        rows = _snmp_parse(out.decode("utf-8", "replace"))
        real = [(o, v) for o, v in rows if not _snmp_absent(v)]
        # net-snmp notes that it created cert_indexes inside our temp dir: not an error
        msg = " ".join(ln.strip() for ln in err.decode("utf-8", "replace").splitlines()
                       if ln.strip() and not ln.startswith("Created directory"))
        if cut == "rows":
            return real[:max_rows], "partial", "row limit reached"
        if cut == "timeout":
            return real, ("partial" if real else "error"), "timed out" if real else (msg or "timed out")[:300]
        if proc.returncode != 0:
            return real, ("partial" if real else "error"), (msg or f"exit {proc.returncode}")[:300]
        if not real:
            return [], "absent", None
        return real, "ok", None

    def walk(self, name: str, **kw) -> tuple[list[tuple[list[int], str]], str, str | None]:  # noqa: ANN003
        col = SNMP_OIDS[name]
        rows, st, err = self.run("snmpbulkwalk", [col], **kw)
        out = []
        for oid, val in rows:
            idx = _snmp_suffix(oid, col)
            if idx is not None:
                out.append((idx, val))
        return out, st, err

    def system(self) -> tuple[dict | None, str | None]:
        names = ("sysDescr", "sysObjectID", "sysUpTime", "sysName")
        rows, st, err = self.run("snmpget", [SNMP_OIDS[n] for n in names])
        if st == "error":
            return None, err or "no response"
        got = dict(rows)
        oid = got.get(SNMP_OIDS["sysObjectID"], "")
        self.sys_object_id = oid.lstrip(".") if oid.startswith(".") else oid
        return {"sys_descr": _snmp_text(got.get(SNMP_OIDS["sysDescr"], ""), 1000),
                "sys_object_id": self.sys_object_id or None,
                "sys_uptime": _snmp_int(got.get(SNMP_OIDS["sysUpTime"], "")),
                "sys_name": _snmp_text(got.get(SNMP_OIDS["sysName"], ""))}, None

    def ifnames(self) -> dict:
        """ifIndex -> short interface name (ifName, else ifDescr); read once per run."""
        if self._ifnames is None:
            names: dict = {}
            for col in ("ifName", "ifDescr"):
                rows, _st, _err = self.walk(col)
                for idx, val in rows:
                    t = _snmp_text(val)
                    if t and idx and idx[0] not in names:
                        names[idx[0]] = t
                if names:
                    break
            self._ifnames = names
        return self._ifnames


def _snmp_worst(statuses: list) -> str:
    """Combine walk statuses: an error next to usable rows is partial; ok beside absent is still ok."""
    useful = any(x in ("ok", "partial") for x in statuses)
    if "error" in statuses:
        return "partial" if useful else "error"
    if "partial" in statuses:
        return "partial"
    return "ok" if "ok" in statuses else "absent"


def _snmp_arp(s: _SnmpSession) -> dict:
    """ARP / ND: ipNetToPhysicalTable (has a state) with the legacy ipNetToMediaTable as fallback."""
    rows, st, err = s.walk("ipNetToPhysicalPhysAddress")
    out: list = []
    if rows:
        state = {tuple(i): _snmp_int(v) for i, v in s.walk("ipNetToPhysicalState")[0]}
        typ = {tuple(i): _snmp_int(v) for i, v in s.walk("ipNetToPhysicalType")[0]}
        for idx, val in rows:
            ip, _rest = _snmp_inet(idx[1:]) if idx else (None, [])
            mac = _snmp_mac(val)
            if ip and mac:
                out.append([ip, mac, idx[0], state.get(tuple(idx)), typ.get(tuple(idx))])
        return {"status": st, "error": err, "table": "physical", "rows": out, "truncated": st == "partial"}
    if st == "error":
        return {"status": "error", "error": err, "rows": []}
    rows, st, err = s.walk("ipNetToMediaPhysAddress")
    typ = {tuple(i): _snmp_int(v) for i, v in s.walk("ipNetToMediaType")[0]} if rows else {}
    for idx, val in rows:
        if len(idx) != 5:
            continue
        mac = _snmp_mac(val)
        if mac:
            out.append([".".join(str(x) for x in idx[1:]), mac, idx[0], None, typ.get(tuple(idx))])
    return {"status": st, "error": err, "table": "media", "rows": out, "truncated": st == "partial"}


def _snmp_fdb_walk(s: _SnmpSession, vlan: int | None = None, *, context: str | None = None,
                   suffix: str = "") -> tuple[list, str, str | None]:
    """One BRIDGE-MIB pass (optionally inside a Cisco VLAN context) -> [[mac, port, vlan]]."""
    names = s.ifnames()
    bp = {i[0]: _snmp_int(v) for i, v in s.walk("dot1dBasePortIfIndex", context=context,
                                                community_suffix=suffix)[0] if i}
    rows, st, err = s.walk("dot1dTpFdbPort", context=context, community_suffix=suffix)
    out = []
    for idx, val in rows:
        port = _snmp_int(val)
        if len(idx) != 6 or not port:                 # port 0 = the switch's own address
            continue
        ifx = bp.get(port)
        name = names.get(ifx) if ifx is not None else None
        out.append([_snmp_mac(bytes(idx)), name or f"port {port}", vlan])
    return out, st, err


def _snmp_fdb(s: _SnmpSession) -> dict:
    """MAC table: Q-BRIDGE (per FDB id / VLAN) first, then BRIDGE-MIB; Cisco keeps one BRIDGE-MIB instance
    per VLAN (community@vlan for v2c, context vlan-N for v3), read only for VLANs that exist (vtpVlanState)."""
    names = s.ifnames()
    rows, st, err = s.walk("dot1qTpFdbPort")
    if rows:
        bp = {i[0]: _snmp_int(v) for i, v in s.walk("dot1dBasePortIfIndex")[0] if i}
        fid_vlan: dict = {}
        for idx, val in s.walk("dot1qVlanFdbId")[0]:
            fid = _snmp_int(val)
            if fid is not None and idx:
                fid_vlan.setdefault(fid, set()).add(idx[-1])
        out = []
        for idx, val in rows:
            port = _snmp_int(val)
            if len(idx) != 7 or not port:
                continue
            vl = fid_vlan.get(idx[0])
            vlan = next(iter(vl)) if vl and len(vl) == 1 else (idx[0] if 0 < idx[0] < 4095 else None)
            ifx = bp.get(port)
            out.append([_snmp_mac(bytes(idx[1:])), (names.get(ifx) if ifx is not None else None) or f"port {port}",
                        vlan])
        return {"status": st, "error": err, "rows": out, "truncated": st == "partial"}
    if st == "error":
        return {"status": "error", "error": err, "rows": []}
    if s.sys_object_id.startswith("1.3.6.1.4.1.9."):
        vlans = sorted({i[-1] for i, v in s.walk("vtpVlanState")[0] if i and _snmp_int(v) == 1
                        and not 1002 <= i[-1] <= 1005})
        if vlans:
            cap = s.budget["max_vlans"]
            out, statuses, errs = [], [], []
            for vl in vlans[:cap]:
                if s.cred["version"] == "v3":
                    r, st2, e2 = _snmp_fdb_walk(s, vl, context=f"vlan-{vl}")
                else:
                    r, st2, e2 = _snmp_fdb_walk(s, vl, suffix=f"@{vl}")
                out += r
                statuses.append(st2)
                if e2:
                    errs.append(f"vlan {vl}: {e2}")
            status = _snmp_worst(statuses)
            truncated = len(vlans) > cap or status == "partial"
            if len(vlans) > cap:
                errs.append(f"only the first {cap} of {len(vlans)} VLANs were read")
                status = "partial" if status in ("ok", "absent") else status
            return {"status": status, "error": "; ".join(errs)[:300] or None, "rows": out, "truncated": truncated}
    out, st, err = _snmp_fdb_walk(s)
    return {"status": st, "error": err, "rows": out, "truncated": st == "partial"}


def _snmp_interfaces(s: _SnmpSession) -> dict:
    cols = {}
    statuses = []
    for name in ("ifDescr", "ifName", "ifAlias", "ifType", "ifPhysAddress", "ifAdminStatus", "ifOperStatus"):
        rows, st, _err = s.walk(name)
        cols[name] = {i[0]: v for i, v in rows if i}
        if name == "ifDescr":
            statuses.append(st)
            if st == "error":
                return {"status": "error", "error": _err, "rows": []}
    out = []
    for ifx in sorted(set(cols["ifDescr"]) | set(cols["ifName"])):
        out.append({"if_index": ifx, "name": _snmp_text(cols["ifName"].get(ifx, "")),
                    "descr": _snmp_text(cols["ifDescr"].get(ifx, "")),
                    "alias": _snmp_text(cols["ifAlias"].get(ifx, ""), 500),
                    "type": _snmp_int(cols["ifType"].get(ifx, "")),
                    "mac": _snmp_mac(cols["ifPhysAddress"].get(ifx, "")),
                    "admin": _snmp_int(cols["ifAdminStatus"].get(ifx, "")),
                    "oper": _snmp_int(cols["ifOperStatus"].get(ifx, ""))})
    addrs = []
    rows, st, _err = s.walk("ipAddressIfIndex")
    if rows:
        kinds = {tuple(i): _snmp_int(v) for i, v in s.walk("ipAddressType")[0]}
        for idx, val in rows:
            ip, _rest = _snmp_inet(idx)
            if ip and kinds.get(tuple(idx), 1) == 1:          # unicast only (Linux lists broadcasts too)
                addrs.append({"ip": ip, "if_index": _snmp_int(val)})
    else:
        for idx, val in s.walk("ipAdEntIfIndex")[0]:
            if len(idx) == 4:
                addrs.append({"ip": ".".join(str(x) for x in idx), "if_index": _snmp_int(val)})
    s._ifnames = {k: (_snmp_text(cols["ifName"].get(k, "")) or _snmp_text(cols["ifDescr"].get(k, "")))
                  for k in set(cols["ifDescr"]) | set(cols["ifName"])}
    return {"status": statuses[0], "rows": out, "addresses": addrs, "truncated": statuses[0] == "partial"}


def _snmp_lldp_id(val: str, is_mac: bool) -> str | None:
    """LLDP port / chassis id: a MAC subtype becomes aa:bb:..; everything else stays text."""
    if is_mac:
        return _snmp_mac(val) or _snmp_text(val)
    return _snmp_text(val)


def _snmp_neighbors(s: _SnmpSession) -> dict:
    out = []
    statuses = []
    errs = []
    # ── LLDP ──
    names, st, err = s.walk("lldpRemSysName")
    chassis, st2, _e = s.walk("lldpRemChassisId")
    statuses += [st if names else st2]
    if err and st == "error":
        errs.append(f"lldp: {err}")
    keys = {tuple(i[1:3]) for i, _v in names + chassis if len(i) >= 3}
    if keys:
        def col(name: str) -> dict:
            return {tuple(i[1:3]): v for i, v in s.walk(name)[0] if len(i) >= 3}
        sysname = {tuple(i[1:3]): v for i, v in names if len(i) >= 3}
        chas = {tuple(i[1:3]): v for i, v in chassis if len(i) >= 3}
        chas_sub = {k: _snmp_int(v) for k, v in col("lldpRemChassisIdSubtype").items()}
        port_id, port_sub, port_desc = col("lldpRemPortId"), col("lldpRemPortIdSubtype"), col("lldpRemPortDesc")
        sysdesc = col("lldpRemSysDesc")
        mgmt: dict = {}
        # index: timeMark.localPort.remIndex.addrFamily(1 ipv4, 2 ipv6).len.address -- prefer IPv4
        for idx, _v in s.walk("lldpRemManAddrIfSubtype")[0]:
            if len(idx) >= 6:
                ip, _rest = _snmp_inet(idx[3:])
                if ip and ((idx[1], idx[2]) not in mgmt or ":" in mgmt[(idx[1], idx[2])]):
                    mgmt[(idx[1], idx[2])] = ip
        loc_id = {i[0]: v for i, v in s.walk("lldpLocPortId")[0] if i}
        loc_sub = {i[0]: _snmp_int(v) for i, v in s.walk("lldpLocPortIdSubtype")[0] if i}
        loc_desc = {i[0]: v for i, v in s.walk("lldpLocPortDesc")[0] if i}
        ifn = s.ifnames()
        for key in sorted(keys):
            lp = key[0]
            local = (_snmp_text(loc_id.get(lp, "")) if loc_sub.get(lp) in (5, 7) else None) \
                or _snmp_text(loc_desc.get(lp, "")) or ifn.get(lp) or str(lp)
            # port id subtypes: 3 macAddress, 5 interfaceName, 7 local; chassis id subtype 4 = macAddress
            psub = _snmp_int(port_sub.get(key, ""))
            rport = (_snmp_text(port_id.get(key, "")) if psub in (5, 7) else None) \
                or _snmp_text(port_desc.get(key, "")) or _snmp_lldp_id(port_id.get(key, ""), psub == 3)
            csub = chas_sub.get(key)
            cid = _snmp_lldp_id(chas.get(key, ""), csub == 4)
            out.append({"protocol": "lldp", "local_port": local, "remote_sysname": _snmp_text(sysname.get(key, "")),
                        "remote_port": rport, "remote_chassis_id": cid,
                        "remote_mac": cid if csub == 4 else None, "remote_mgmt_address": mgmt.get(key),
                        "remote_platform": _snmp_text(sysdesc.get(key, ""), 255, first_line=True)})
    # ── CDP ──
    dev, st3, err3 = s.walk("cdpCacheDeviceId")
    statuses.append(st3)
    if err3 and st3 == "error":
        errs.append(f"cdp: {err3}")
    if dev:
        def ccol(name: str) -> dict:
            return {tuple(i[:2]): v for i, v in s.walk(name)[0] if len(i) >= 2}
        port, plat, atype, addr = (ccol("cdpCacheDevicePort"), ccol("cdpCachePlatform"),
                                   ccol("cdpCacheAddressType"), ccol("cdpCacheAddress"))
        ifn = s.ifnames()
        for idx, val in dev:
            if len(idx) < 2:
                continue
            key = tuple(idx[:2])
            ip = None
            b = _snmp_bytes(addr.get(key, ""))
            if _snmp_int(atype.get(key, "")) == 1 and b and len(b) == 4:
                ip = str(ipaddress.IPv4Address(b))
            out.append({"protocol": "cdp", "local_port": ifn.get(idx[0]) or str(idx[0]),
                        "remote_sysname": _snmp_text(val), "remote_port": _snmp_text(port.get(key, "")),
                        "remote_chassis_id": _snmp_text(val), "remote_mac": None, "remote_mgmt_address": ip,
                        "remote_platform": _snmp_text(plat.get(key, ""))})
    status = _snmp_worst(statuses)
    return {"status": status, "error": "; ".join(errs)[:300] or None, "rows": out}


_SNMP_COLLECTORS = {"interfaces": _snmp_interfaces, "arp": _snmp_arp, "fdb": _snmp_fdb,
                    "neighbors": _snmp_neighbors}
# interfaces first: it also fills the ifIndex -> name map the MAC table and CDP use
_SNMP_ORDER = ("interfaces", "arp", "fdb", "neighbors")


def _snmp_dev_lock(ip: str) -> threading.Lock:
    """Never two runs against the same device at once (scheduled collection vs. a test from the web UI)."""
    with _SNMP_STATE_LOCK:
        return _SNMP_DEV_LOCKS.setdefault(ip, threading.Lock())


def _snmp_collect(spec: dict, cred: dict, sections: list, budget: dict) -> dict:
    """Run the given sections against one device -> report payload (without target_id / job_id)."""
    started = time.monotonic()
    with _snmp_dev_lock(spec["ip"]):
        s = _SnmpSession(spec["ip"], spec["port"], cred, budget)
        system, err = s.system()
        payload: dict = {"credential_id": cred.get("credential_id") or None}
        if system is None:
            payload.update(error=err or "no response", error_code="snmp_unreachable", sections={})
        else:
            payload["system"] = system
            out = {}
            for name in _SNMP_ORDER:
                if name not in sections:
                    continue
                t0 = time.monotonic()
                if time.monotonic() >= s.deadline:
                    out[name] = {"status": "error", "error": "device time budget used up", "rows": []}
                    continue
                try:
                    out[name] = _SNMP_COLLECTORS[name](s)
                except Exception as exc:  # noqa: BLE001
                    out[name] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300], "rows": []}
                out[name]["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
            payload["sections"] = out
    payload["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return payload


def _snmp_report(payload: dict) -> dict:
    """POST to the server; one request per section when the whole thing would be too large."""
    body = json.dumps(payload)
    if len(body) <= _SNMP_REPORT_SPLIT or len(payload.get("sections") or {}) <= 1:
        return _req("POST", "/api/v1/scan-agents/snmp-report", payload, timeout=120)
    last: dict = {}
    for name, sec in payload["sections"].items():
        part = {k: v for k, v in payload.items() if k != "sections"}
        part["sections"] = {name: sec}
        last = _req("POST", "/api/v1/scan-agents/snmp-report", part, timeout=120)
    return last


def _snmp_set_assigned(targets, budget) -> None:  # noqa: ANN001
    """From each poll: what the server currently assigns to this agent (credentials stay in memory only)."""
    clean = []
    for t in targets if isinstance(targets, list) else []:
        spec, cred = _snmp_target_ok(t), _snmp_cred_ok((t or {}).get("credential") if isinstance(t, dict) else None)
        if spec and cred and spec["target_id"] and spec["sections"]:
            spec["credential"] = cred
            clean.append(spec)
    with _SNMP_STATE_LOCK:
        _SNMP_STATE["targets"] = clean
        _SNMP_STATE["budget"] = _snmp_budget(budget)
        keep = {t["target_id"] for t in clean}
        for k in [k for k in _SNMP_LAST if k[0] not in keep]:
            del _SNMP_LAST[k]


def _snmp_due(now: float) -> list:
    with _SNMP_STATE_LOCK:
        targets, budget = list(_SNMP_STATE["targets"]), dict(_SNMP_STATE["budget"])
        due = []
        for t in targets:
            names = [n for n, iv in t["sections"].items() if now - _SNMP_LAST.get((t["target_id"], n), 0) >= iv]
            if names:
                for n in names:            # marked at start: a failing device is retried next interval, not every 10 s
                    _SNMP_LAST[(t["target_id"], n)] = now
                due.append((t, names))
    return [(t, names, budget) for t, names in due]


def _snmp_loop() -> None:
    """Background thread: one device at a time, only sections whose interval has passed."""
    while True:
        try:
            due = _snmp_due(time.time())
            if not due:
                time.sleep(10)
                continue
            for spec, names, budget in due:
                payload = _snmp_collect(spec, spec["credential"], names, budget)
                payload["target_id"] = spec["target_id"]
                r = _snmp_report(payload)
                counts = ",".join(f"{k}={v.get('status')}/{len(v.get('rows') or [])}"
                                  for k, v in (payload.get("sections") or {}).items())
                print(f"[snmp] {spec['ip']} {counts or payload.get('error')} -> {r.get('result', r.get('status'))}",
                      flush=True)
        except Exception as exc:  # noqa: BLE001 -- never let SNMP take the agent down
            print(f"[snmp] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            time.sleep(30)


def _snmp_job(kind: str, params: dict) -> tuple[object, str | None]:
    """snmp_test / snmp_collect jobs (validated here, not only on the server)."""
    if not _snmp_available():
        return None, "net-snmp client tools are not installed on this agent (apt install snmp)"
    spec = _snmp_target_ok(params)
    if spec is None:
        return None, "invalid snmp target"
    budget = _snmp_budget(params.get("budget"))
    if kind == "snmp_collect":
        cred = _snmp_cred_ok(params.get("credential"))
        if cred is None:
            return None, "invalid snmp credential"
        payload = _snmp_collect(spec, cred, list(spec["sections"]), budget)
        payload.update(target_id=spec["target_id"], job_id=str(params.get("_job_id") or "") or None)
        r = _snmp_report(payload)
        summary = {k: {"status": v.get("status"), "rows": len(v.get("rows") or [])}
                   for k, v in (payload.get("sections") or {}).items()}
        return {"result": r.get("result"), "sections": summary}, payload.get("error")
    creds = [c for c in (_snmp_cred_ok(c) for c in (params.get("credentials") or [])[:5]) if c]
    if not creds:
        return None, "invalid snmp credential"
    items = []
    for cred in creds:
        p = _snmp_collect(spec, cred, list(spec["sections"]), budget)
        secs = {k: {"status": v.get("status"), "rows": len(v.get("rows") or []), "error": v.get("error"),
                    "elapsed_ms": v.get("elapsed_ms"), "table": v.get("table"), "truncated": v.get("truncated")}
                for k, v in (p.get("sections") or {}).items()}
        items.append({"credential_id": cred.get("credential_id"), "ok": p.get("system") is not None,
                      "system": p.get("system"), "error": p.get("error"), "elapsed_ms": p.get("elapsed_ms"),
                      "sections": secs})
    return {"items": items}, None


# ─────────────────── On-demand probe jobs (Tools page) ───────────────────
# The server cannot reach us: we only dial out. So on-demand probes arrive through a
# long-poll queue -- we ask for work, run it locally, and post the result back.
#
# SECURITY: we validate every job ourselves and never pass anything to a shell.
# The server is not trusted to have validated for us: if the server is compromised,
# this check is the last thing standing between it and arbitrary probing of the
# customer network. Only these read-only probe kinds are ever executed; identify runs a
# fixed, read-only script list defined in this file (see _IDENTIFY_SCRIPTS).
_JOB_KINDS = ("ping", "tcp", "traceroute", "rdns", "identify", "snmp_test", "snmp_collect")
# snmp_test / snmp_collect: one server-assigned device, read-only, OIDs from SNMP_OIDS only (see _snmp_job)
_SNMP_JOB_KINDS = ("snmp_test", "snmp_collect")
_JOB_MAX_TARGETS = 64
_JOB_MAX_PORTS = 64
_HOSTNAME_OK = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
                          r"(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$")

def _job_valid_target(t: str) -> str | None:
    s = (t or "").strip()
    if not s or len(s) > 253:
        return None
    try:
        ipaddress.ip_address(s)
        return s
    except ValueError:
        pass
    return s if _HOSTNAME_OK.match(s) else None


def _job_run_ping(targets: list[str], count: int, timeout: float) -> list[dict]:
    out = []
    for t in targets:
        argv = ["ping", "-c", str(count), "-W", str(int(max(1, timeout))), t]
        try:
            r = subprocess.run(argv, capture_output=True, text=True,
                               timeout=count * timeout + 5)
            ok = r.returncode == 0
            out.append({"target": t, "alive": ok, "output": (r.stdout or "")[-2000:]})
        except Exception as exc:  # noqa: BLE001
            out.append({"target": t, "alive": False, "error": f"{type(exc).__name__}"})
    return out


def _job_run_tcp(targets: list[str], ports: list[int], timeout: float) -> list[dict]:
    out = []
    for t in targets:
        opened, closed = [], []
        for p in ports:
            try:
                with socket.create_connection((t, p), timeout=timeout):
                    opened.append(p)
            except Exception:  # noqa: BLE001
                closed.append(p)
        out.append({"target": t, "open": opened, "closed": closed})
    return out


def _job_run_traceroute(target: str, max_hops: int) -> dict:
    for tool in ("tracepath", "traceroute"):
        exe = shutil.which(tool)
        if not exe:
            continue
        argv = ([exe, "-m", str(max_hops), target] if tool == "traceroute"
                else [exe, "-m", str(max_hops), target])
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=max_hops * 3 + 10)
            return {"target": target, "tool": tool, "output": (r.stdout or "")[-8000:]}
        except Exception as exc:  # noqa: BLE001
            return {"target": target, "tool": tool, "error": f"{type(exc).__name__}"}
    return {"target": target, "error": "no traceroute tool available (tracepath/traceroute)"}


# identify（IP 詳細頁的「探測」）：只做唯讀、非侵入的識別 —— 服務版本、OS 指紋，加上固定一小組
# 讀取資訊用的 NSE 腳本（banner／HTTP 標題與伺服器標頭／TLS 憑證／SSH 主機金鑰／SMB 與 RDP 的
# 系統資訊）。清單寫死在這裡、不從伺服器收：後端被入侵也改不了代理會跑什麼。
_IDENTIFY_SCRIPTS = ("banner,http-title,http-server-header,ssl-cert,ssh-hostkey,"
                     "smb-os-discovery,rdp-ntlm-info")
_IDENTIFY_TIMEOUT = 300          # 秒；伺服器端的「領走未回報」門檻比這個長
_IDENTIFY_MAX_PORTS = 200
_IDENTIFY_MAX_TEXT = 600         # 每段腳本輸出最多保留幾個字元
_IDENTIFY_TOP_PORTS = 1000
# nmap 的前 1000 個常用埠不含這些，但它們最能說明「這是什麼設備」：PVE／PBS 網頁、WinRM、
# Intel AMT、HPE iLO、MikroTik Winbox／API、Docker／Kubernetes API、常見攝影機與 NVR 管理埠。
# 刻意不放工控協定（Modbus、S7 等）：老舊 PLC 可能被版本探測弄當。
_IDENTIFY_EXTRA_PORTS = (902, 2375, 2376, 5601, 5985, 5986, 6443, 8006, 8007, 8123, 8291, 8554,
                         8728, 8729, 9443, 10050, 10443, 16992, 16993, 17988, 17990, 34567, 37777)
_NMAP_SERVICES_PATHS = ("/usr/share/nmap/nmap-services", "/usr/local/share/nmap/nmap-services",
                        "/opt/homebrew/share/nmap/nmap-services")


def _identify_port_list(path: str | None = None, top: int = _IDENTIFY_TOP_PORTS) -> str | None:
    """前 N 個常用 TCP 埠（依 nmap-services 的頻率）＋補充清單，逗號分隔。

    nmap 的 `-p` 與 `--top-ports` 併用時是取交集而不是聯集，所以自己算。讀不到檔案回 None，
    呼叫端退回 `--top-ports`。
    """
    for p in ((path,) if path else _NMAP_SERVICES_PATHS):
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        ranked: list[tuple[float, int]] = []
        for line in lines:
            parts = line.split()
            if len(parts) < 3 or line.startswith("#") or not parts[1].endswith("/tcp"):
                continue
            try:
                ranked.append((float(parts[2]), int(parts[1][:-4])))
            except ValueError:
                continue
        if not ranked:
            continue
        ranked.sort(key=lambda x: (-x[0], x[1]))
        ports = {port for _, port in ranked[:top]} | set(_IDENTIFY_EXTRA_PORTS)
        return ",".join(str(x) for x in sorted(ports))
    return None


_MAX_SAN = 20


def _cert_data(sc) -> dict:  # noqa: ANN001 -- ElementTree element
    """ssl-cert 的結構化欄位：Subject／Issuer、有效期間、指紋、金鑰、SAN（1.17.4 起多了後四項）。

    文字輸出最多留 600 字，SAN 很長時到期日會被切掉；這裡不受影響。nmap 7.94 不給 SHA-256，
    從 PEM 自己算；PEM 本身不送回伺服器（只要指紋）。"""
    cert: dict = {}
    for part in ("subject", "issuer"):
        table = sc.find(f"table[@key='{part}']")
        if table is not None:
            cert[part] = {e.get("key"): (e.text or "")[:200] for e in table.findall("elem") if e.get("key")}
    val = sc.find("table[@key='validity']")
    if val is not None:
        for key, out_key in (("notBefore", "not_before"), ("notAfter", "not_after")):
            e = val.find(f"elem[@key='{key}']")
            if e is not None and e.text:
                cert[out_key] = e.text[:40]
    sha1 = sc.find("elem[@key='sha1']")
    if sha1 is not None and sha1.text:
        cert["sha1"] = sha1.text.strip().lower()[:64]
    pem = sc.find("elem[@key='pem']")
    if pem is not None and pem.text:
        body = "".join(ln for ln in pem.text.splitlines() if ln and not ln.startswith("-----"))
        try:
            cert["sha256"] = hashlib.sha256(base64.b64decode(body)).hexdigest()
        except Exception:  # noqa: BLE001 -- 指紋算不出來就不給，其餘照常
            pass
    pk = sc.find("table[@key='pubkey']")
    if pk is not None:
        t, b = pk.find("elem[@key='type']"), pk.find("elem[@key='bits']")
        if t is not None and t.text:
            cert["key_type"] = t.text[:20]
        if b is not None and (b.text or "").isdigit():
            cert["key_bits"] = int(b.text)
    for ext in sc.findall("table[@key='extensions']/table"):
        name = ext.find("elem[@key='name']")
        value = ext.find("elem[@key='value']")
        if name is not None and name.text == "X509v3 Subject Alternative Name" and value is not None:
            cert["san"] = [x.strip()[:200] for x in (value.text or "").split(",") if x.strip()][:_MAX_SAN]
    return cert


def _hostkey_data(sc) -> list:  # noqa: ANN001 -- ElementTree element
    """ssh-hostkey → [{type, bits, sha256}]。nmap 預設只給 MD5；OpenSSH 現在顯示的是 SHA256，
    從金鑰本體自己算，畫面才對得上 `ssh-keygen -lf`。金鑰本體不送回伺服器。"""
    keys = []
    for tb in sc.findall("table"):
        g = {e.get("key"): (e.text or "") for e in tb.findall("elem") if e.get("key")}
        if not g.get("key"):
            continue
        try:
            fp = "SHA256:" + base64.b64encode(hashlib.sha256(base64.b64decode(g["key"])).digest()).decode().rstrip("=")
        except Exception:  # noqa: BLE001
            continue
        keys.append({"type": g.get("type", "")[:40], "bits": int(g["bits"]) if g.get("bits", "").isdigit() else None,
                     "sha256": fp})
    return keys[:8]


def _script_data(port) -> dict:  # noqa: ANN001 -- ElementTree element
    """腳本的結構化輸出裡伺服器要用的部分：ssl-cert（Subject／Issuer 各欄位、有效期間、指紋、金鑰、SAN）
    與 ssh-hostkey（SHA256 指紋）。"""
    out: dict = {}
    for sc in port.findall("script"):
        if sc.get("id") == "ssl-cert":
            cert = _cert_data(sc)
            if cert:
                out["ssl-cert"] = cert
        elif sc.get("id") == "ssh-hostkey":
            keys = _hostkey_data(sc)
            if keys:
                out["ssh-hostkey"] = keys
    return out


def _scripts_of(el) -> tuple[dict, list]:  # noqa: ANN001 -- ElementTree element
    """{腳本 id: 輸出（最多 _IDENTIFY_MAX_TEXT 字）}，以及被截斷的腳本 id（畫面要標出來）。"""
    texts, cut = {}, []
    for sc in el.findall("script"):
        sid = sc.get("id")
        if not sid:
            continue
        full = (sc.get("output") or "").strip()
        texts[sid] = full[:_IDENTIFY_MAX_TEXT]
        if len(full) > _IDENTIFY_MAX_TEXT:
            cut.append(sid)
    return texts, cut


def _parse_nmap_xml(text: str) -> dict:
    """nmap -oX 輸出 → {hostnames, mac, mac_vendor, ports[開著的], os[], host_scripts{}}。解析失敗回空結構。

    host_scripts：主機層腳本（smb-os-discovery 不掛在任何埠下，以前整段被丟掉）。
    ports[].script_data：ssl-cert 的完整 Subject／Issuer —— 文字輸出只有 CN／O／ST／C，
    伺服器端比對 Recog 的設備預設憑證要 OU、L 這些欄位。
    ports[].method／conf／devicetype（1.17.2 起）：nmap 怎麼認出這個服務。method="table" ＝沒有探針比中、
    服務名稱只是照埠號表寫的（9100 寫 jetdirect、554 寫 rtsp），伺服器不可以當成認出了服務；devicetype 是
    nmap-service-probes 的 d/ 欄位（這個服務通常跑在哪種設備上）。舊伺服器不認得這幾個欄位，照樣忽略。
    1.17.4 起：filtered（被過濾的埠數）、distance（跳數）、uptime（nmap 推估的開機時間）、host_found
    （輸出裡有沒有這台：沒有＝nmap 失敗或略過，不是「沒回應」）、timedout（單台時限到了、結果被丟掉）、
    ports[].truncated／host_scripts_truncated（輸出被截斷的腳本）。
    1.18.2 起：hosts_down（nmap 統計裡判定不在的主機數）：-Pn 時區網內的主機探測當下沒回 ARP，nmap 也會判定
    不在、輸出裡沒有這台 —— 那是「沒回應」，不是 nmap 失敗（2026-10-12 只回 ARP、時有時無的 VM）。

    XML 是本機剛跑完的 nmap 產生的（不是從網路收來的文件），用標準函式庫解析即可。
    """
    # closed：回了 RST 的埠數 —— 有這個就代表主機活著，只是那些埠沒開；全部 filtered 又沒有 MAC 回應＝沒回應
    out: dict = {"hostnames": [], "mac": None, "mac_vendor": None, "ports": [], "os": [], "closed": 0,
                 "filtered": 0, "host_scripts": {}, "host_scripts_truncated": [], "distance": None, "uptime": None,
                 "host_found": False, "timedout": False, "hosts_down": None}
    try:
        root = ET.fromstring(text)  # noqa: S314 -- local nmap output, not untrusted input
    except Exception:  # noqa: BLE001
        return out
    hs = root.find("runstats/hosts")
    if hs is not None and str(hs.get("down") or "").isdigit():
        out["hosts_down"] = int(hs.get("down"))
    host = root.find("host")
    if host is None:
        return out
    out["host_found"] = True
    out["timedout"] = host.get("timedout") == "true"
    dist = host.find("distance")
    if dist is not None and (dist.get("value") or "").isdigit():
        out["distance"] = int(dist.get("value"))
    up = host.find("uptime")
    if up is not None and (up.get("seconds") or "").isdigit():
        out["uptime"] = {"seconds": int(up.get("seconds")), "lastboot": (up.get("lastboot") or "")[:60] or None}
    for a in host.findall("address"):
        if a.get("addrtype") == "mac":
            out["mac"], out["mac_vendor"] = a.get("addr"), a.get("vendor")
    out["hostnames"] = [h.get("name") for h in host.findall("hostnames/hostname") if h.get("name")]
    for ex in host.findall("ports/extraports"):
        if ex.get("state") in ("closed", "filtered"):
            out[ex.get("state")] += int(ex.get("count") or 0)
    for port in host.findall("ports/port"):
        st = port.find("state")
        if st is not None and st.get("state") in ("closed", "filtered"):
            out[st.get("state")] += 1
        if st is None or st.get("state") != "open":
            continue
        svc = port.find("service")
        g = (lambda k: (svc.get(k) if svc is not None else None) or "")
        scripts, cut = _scripts_of(port)
        out["ports"].append({
            "port": int(port.get("portid") or 0), "proto": port.get("protocol") or "tcp",
            "state": "open", "service": g("name"), "product": g("product"), "version": g("version"),
            "extrainfo": g("extrainfo"), "tunnel": g("tunnel"), "ostype": g("ostype"),
            "method": g("method"), "conf": g("conf"), "devicetype": g("devicetype"),
            "scripts": scripts, "truncated": cut,
            "script_data": _script_data(port),
        })
        if len(out["ports"]) >= _IDENTIFY_MAX_PORTS:
            break
    hs = host.find("hostscript")
    if hs is not None:
        out["host_scripts"], out["host_scripts_truncated"] = _scripts_of(hs)
    for m in host.findall("os/osmatch")[:5]:
        cls = m.find("osclass")
        out["os"].append({
            "name": m.get("name"), "accuracy": int(m.get("accuracy") or 0),
            "type": cls.get("type") if cls is not None else None,
            "vendor": cls.get("vendor") if cls is not None else None,
            "family": cls.get("osfamily") if cls is not None else None,
        })
    return out


def _job_run_identify(target: str, progress=None) -> dict:  # noqa: ANN001
    """對單一 IP 做識別：名稱查詢（反解／NetBIOS／mDNS）＋ nmap 服務版本與 OS 指紋。

    `progress`：每個階段開始時呼叫一次（{"stage": ...}），讓畫面看得到現在在做什麼。
    """
    started = time.time()

    def stage(name: str) -> None:
        if progress:
            try:
                progress({"stage": name, "elapsed": round(time.time() - started, 1)})
            except Exception:  # noqa: BLE001 -- 進度回報失敗不可以讓探測本身失敗
                pass

    stage("names")
    names = {"rdns": _rdns(target)[0], "netbios": _netbios(target), "mdns": _mdns(target)}
    if not shutil.which("nmap"):
        return {"target": target, "names": names, "nmap": {"available": False}}
    stage("scan")
    port_list = _identify_port_list()
    ports = ["-p", port_list] if port_list else ["--top-ports", str(_IDENTIFY_TOP_PORTS)]
    argv = ["nmap", "-Pn", "-sV", "--version-intensity", "5", *ports, "-T4",
            "--host-timeout", f"{_IDENTIFY_TIMEOUT - 30}s", "--script", _IDENTIFY_SCRIPTS,
            "--script-args", BANNER_SCRIPT_ARGS]     # banner 不碰列印埠，見 BANNER_PORTS
    # IPv6 要 -6，不然 nmap 把位址當成解析不了的主機名稱（以前 IPv6 的探測一定失敗）
    if ipaddress.ip_address(target).version == 6:
        argv.append("-6")
    os_scan = hasattr(os, "geteuid") and os.geteuid() == 0
    if os_scan:
        argv += ["-O", "--osscan-guess"]      # OS 指紋要 raw socket（root）；沒有就只做服務版本
    argv += ["-oX", "-", target]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=_IDENTIFY_TIMEOUT)
        parsed = _parse_nmap_xml(r.stdout or "")
        nmap = {"available": True, **parsed, "exit": r.returncode, "os_scan": os_scan,
                "stderr": (r.stderr or "")[-_IDENTIFY_MAX_TEXT:] or None}
    except subprocess.TimeoutExpired:
        nmap = {"available": True, "os_scan": os_scan, "error": f"nmap timed out after {_IDENTIFY_TIMEOUT}s"}
    except Exception as exc:  # noqa: BLE001
        nmap = {"available": True, "os_scan": os_scan, "error": f"{type(exc).__name__}: {exc}"[:_IDENTIFY_MAX_TEXT]}
    return {"target": target, "names": names, "nmap": nmap, "elapsed": round(time.time() - started, 1)}


def _job_execute(kind: str, params: dict, progress=None) -> tuple[object, str | None]:  # noqa: ANN001
    """Run one job. Returns (result, error). Never raises."""
    if kind not in _JOB_KINDS:
        return None, f"unsupported probe: {kind}"
    if kind in _SNMP_JOB_KINDS:
        try:
            return _snmp_job(kind, params if isinstance(params, dict) else {})
        except Exception as exc:  # noqa: BLE001
            return None, f"{type(exc).__name__}: {exc}"
    raw = params.get("targets") or []
    items = raw if isinstance(raw, list) else re.split(r"[\s,]+", str(raw))
    targets = [x for x in (_job_valid_target(t) for t in items if t) if x]
    if not targets:
        return None, "no valid target"
    if len(targets) > _JOB_MAX_TARGETS:
        return None, f"too many targets (max {_JOB_MAX_TARGETS})"
    try:
        if kind == "ping":
            count = max(1, min(int(params.get("count") or 3), 10))
            timeout = max(0.5, min(float(params.get("timeout") or 2.0), 10.0))
            return _job_run_ping(targets, count, timeout), None
        if kind == "tcp":
            ports = [int(p) for p in (params.get("ports") or []) if 1 <= int(p) <= 65535]
            if not ports:
                return None, "no valid port"
            if len(ports) > _JOB_MAX_PORTS:
                return None, f"too many ports (max {_JOB_MAX_PORTS})"
            timeout = max(0.2, min(float(params.get("timeout") or 1.5), 10.0))
            return _job_run_tcp(targets, ports, timeout), None
        if kind == "traceroute":
            hops = max(1, min(int(params.get("max_hops") or 20), 30))
            return _job_run_traceroute(targets[0], hops), None
        if kind == "identify":
            # 代理自己也要擋：只接受單一 IP（不收主機名稱、不收多個目標）
            if len(targets) != 1:
                return None, "identify takes exactly one target"
            try:
                ipaddress.ip_address(targets[0])
            except ValueError:
                return None, "identify takes an IP address"
            return _job_run_identify(targets[0], progress), None
        return [{"target": t, "hostname": _rdns(t)[0]} for t in targets], None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def _job_runs_in_background(kind: str) -> bool:
    """identify 要跑好幾分鐘；放在工作佇列的執行緒上跑，這段期間其他工具探測會排不到
    （待辦兩分鐘沒被領就作廢）。其他種類幾秒內就結束，照順序跑即可。"""
    return kind == "identify" or kind in _SNMP_JOB_KINDS


def _job_run_and_report(job: dict) -> None:
    jid, kind = job.get("id"), str(job.get("kind"))

    def progress(p: dict) -> None:
        _req("POST", f"/api/v1/scan-agents/jobs/{jid}/progress", {"progress": p})

    params = dict(job.get("params") or {}) if isinstance(job.get("params"), dict) else {}
    if kind in _SNMP_JOB_KINDS:
        params["_job_id"] = jid       # the collection report carries it, so the server fills the right record
    result, error = _job_execute(kind, params,
                                 progress=progress if kind == "identify" else None)
    try:
        _req("POST", f"/api/v1/scan-agents/jobs/{jid}/result", {"result": result, "error": error})
    except Exception as exc:  # noqa: BLE001
        print(f"[jobs] report failed: {type(exc).__name__}", file=sys.stderr, flush=True)


# ─────────────────── Console relay (issue #24 phase 2) ───────────────────
# The server cannot reach into this network; the agent dials out. For each console session the server
# hands out a `relay_open` job; the agent checks the target against ITS OWN rules, connects to it, then
# opens one outbound WebSocket per session to /api/v1/scan-agents/relay/<sid>/ws and moves bytes.
#
# Security: the server hands out the scope (assigned subnets, ports, session limit) only while the
# admin has relay switched on in the web UI; without a scope nothing is relayed. The target must be
# inside that scope (and inside the optional local pins). Loopback, link-local and multicast are always
# refused. JT_IPAM_RELAY=0 on this host refuses everything. A single-use ticket ties the WebSocket to
# this job.


def _relay_env_on(value) -> bool:
    """On unless the host owner explicitly says no: whether to relay is decided in the web UI."""
    return (value or "").strip().lower() not in ("0", "false", "no", "off")


RELAY_ENABLED = _relay_env_on(os.environ.get("JT_IPAM_RELAY"))
RELAY_PORTS_SPEC = os.environ.get("JT_IPAM_RELAY_PORTS", "")
_max_env = (os.environ.get("JT_IPAM_RELAY_MAX") or "").strip()
RELAY_MAX_LOCAL = max(1, min(int(_max_env), 64)) if _max_env.isdigit() else None
RELAY_CIDRS_SPEC = os.environ.get("JT_IPAM_RELAY_CIDRS", "")
RELAY_FRAME_MAX = 64 * 1024          # largest frame we accept from the server
RELAY_CHUNK = 64 * 1024              # largest frame we send
RELAY_CONNECT_TIMEOUT = 10.0
RELAY_KEEPALIVE = 25.0               # idle seconds before an application-level keepalive frame
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_RELAY_ASSIGNED: list = []           # networks the server says this agent is assigned to
_RELAY_PORTS: set = set()            # ports allowed in the web UI (from the server)
_RELAY_MAX = [0]                     # session limit set in the web UI (from the server)
_RELAY_ACTIVE = 0
_RELAY_LOCK = threading.Lock()
_TOKEN_OK = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_SID_OK = re.compile(r"^[0-9a-f]{32}$")


def _parse_ports(spec: str) -> set:
    out: set = set()
    for tok in (spec or "").split(","):
        a, _, b = tok.strip().partition("-")
        if not a.isdigit() or (b and not b.isdigit()):
            continue
        lo, hi = int(a), int(b or a)
        if 1 <= lo <= hi <= 65535 and hi - lo < 256:
            out.update(range(lo, hi + 1))
    return out


def _parse_nets(spec) -> list:
    items = spec if isinstance(spec, list) else str(spec or "").split(",")
    nets = []
    for c in items:
        try:
            nets.append(ipaddress.ip_network(str(c).strip(), strict=False))
        except ValueError:
            continue
    return nets


RELAY_PORTS_LOCAL = _parse_ports(RELAY_PORTS_SPEC) if RELAY_PORTS_SPEC.strip() else None
RELAY_PINNED = _parse_nets(RELAY_CIDRS_SPEC) if RELAY_CIDRS_SPEC.strip() else None


def _relay_header() -> str:
    """Capabilities for X-Agent-Relay. Sent even when off, so the server can say *why* it cannot relay.
    ports / max are the host owner's local limits (empty / 0 = none, the web UI decides)."""
    ports = ",".join(str(p) for p in sorted(RELAY_PORTS_LOCAL or ()))
    return (f"enabled={1 if RELAY_ENABLED else 0};ports={ports};max={RELAY_MAX_LOCAL or 0};"
            f"pinned={1 if RELAY_PINNED is not None else 0}")


def _relay_set_assigned(cidrs: list, ports=None, max_sessions=None) -> None:
    """The scope the server currently allows (poll, or fresh in each relay job). Empty = relay nothing."""
    with _RELAY_LOCK:
        _RELAY_ASSIGNED[:] = _parse_nets(cidrs)
        if ports is not None:
            _RELAY_PORTS.clear()
            _RELAY_PORTS.update(int(p) for p in ports if str(p).isdigit() and 1 <= int(p) <= 65535)
        if max_sessions is not None:
            try:
                _RELAY_MAX[0] = max(0, min(int(max_sessions), 64))
            except (TypeError, ValueError):
                _RELAY_MAX[0] = 0


def _relay_limit() -> int:
    lim = _RELAY_MAX[0]
    return min(lim, RELAY_MAX_LOCAL) if RELAY_MAX_LOCAL else lim


def _relay_acquire() -> bool:
    global _RELAY_ACTIVE
    with _RELAY_LOCK:
        if _RELAY_ACTIVE >= _relay_limit():
            return False
        _RELAY_ACTIVE += 1
        return True


def _relay_release() -> None:
    global _RELAY_ACTIVE
    with _RELAY_LOCK:
        _RELAY_ACTIVE = max(0, _RELAY_ACTIVE - 1)


def _relay_target_ok(target: str, port: int) -> str | None:
    """None when allowed, else an error token. The agent decides for itself -- it does not take the
    server's word for the target."""
    if not RELAY_ENABLED:
        return "relay_disabled"
    with _RELAY_LOCK:
        ports = set(_RELAY_PORTS)
    if port not in ports or (RELAY_PORTS_LOCAL is not None and port not in RELAY_PORTS_LOCAL):
        return "relay_port_not_allowed"
    try:
        ip = ipaddress.ip_address(target)
    except ValueError:
        return "relay_target_not_allowed"
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return "relay_target_not_allowed"
    with _RELAY_LOCK:
        assigned = list(_RELAY_ASSIGNED)
    if not any(ip in n for n in assigned if n.version == ip.version):
        return "relay_target_not_allowed"
    if RELAY_PINNED is not None and not any(ip in n for n in RELAY_PINNED if n.version == ip.version):
        return "relay_target_not_allowed"
    return None


class _WSError(Exception):
    pass


def _ws_mask(payload: bytes, key: bytes) -> bytes:
    """XOR with the 4-byte key. Big-int XOR: fast enough for 64 KiB frames in pure Python."""
    n = len(payload)
    if not n:
        return b""
    m = (key * (n // 4 + 1))[:n]
    return (int.from_bytes(payload, "big") ^ int.from_bytes(m, "big")).to_bytes(n, "big")


def _ws_encode(opcode: int, payload: bytes) -> bytes:
    """One client frame (FIN set, masked as RFC 6455 requires of clients)."""
    n = len(payload)
    b1 = 0x80 | opcode
    if n < 126:
        head = struct.pack("!BB", b1, 0x80 | n)
    elif n < 65536:
        head = struct.pack("!BBH", b1, 0x80 | 126, n)
    else:
        head = struct.pack("!BBQ", b1, 0x80 | 127, n)
    key = os.urandom(4)
    return head + key + _ws_mask(payload, key)


def _ws_decode(buf: bytearray):
    """Take one complete server frame off the front of `buf` -> (opcode, payload), or None if more
    bytes are needed. Only the subset we use: no fragmentation, no masked server frames, no
    unknown opcodes, frames up to RELAY_FRAME_MAX. Anything else closes the session."""
    if len(buf) < 2:
        return None
    b1, b2 = buf[0], buf[1]
    fin, opcode, masked, n = b1 & 0x80, b1 & 0x0F, b2 & 0x80, b2 & 0x7F
    i = 2
    if n == 126:
        if len(buf) < 4:
            return None
        n = struct.unpack("!H", bytes(buf[2:4]))[0]
        i = 4
    elif n == 127:
        if len(buf) < 10:
            return None
        n = struct.unpack("!Q", bytes(buf[2:10]))[0]
        i = 10
    if masked:
        raise _WSError("masked frame from server")
    if not fin or opcode == 0:
        raise _WSError("fragmented frame")
    if opcode not in (0x1, 0x2, 0x8, 0x9, 0xA):
        raise _WSError(f"unexpected opcode {opcode}")
    if n > RELAY_FRAME_MAX:
        raise _WSError(f"frame too large ({n})")
    if len(buf) < i + n:
        return None
    payload = bytes(buf[i:i + n])
    del buf[:i + n]
    return opcode, payload


class _WS:
    def __init__(self, sock, rest: bytes = b"") -> None:
        self.sock = sock
        self.buf = bytearray(rest)
        self.closed = False

    def send(self, opcode: int, payload: bytes) -> None:
        self.sock.sendall(_ws_encode(opcode, payload))

    def read_frames(self) -> list:
        """Read what is available (the selector said readable) and return every complete frame."""
        data = self.sock.recv(65536)
        if not data:
            self.closed = True
            return []
        self.buf += data
        pending = getattr(self.sock, "pending", None)
        while pending is not None and pending():
            more = self.sock.recv(65536)
            if not more:
                break
            self.buf += more
        return self.buffered_frames()

    def buffered_frames(self) -> list:
        """Complete frames already in the buffer (e.g. one that arrived with the handshake reply)."""
        frames = []
        while True:
            f = _ws_decode(self.buf)
            if f is None:
                return frames
            frames.append(f)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self.send(0x8, struct.pack("!H", 1000))
            except OSError:
                pass
        try:
            self.sock.close()
        except OSError:
            pass


def _ws_connect(path: str, headers: dict, timeout: float = RELAY_CONNECT_TIMEOUT) -> _WS:
    """Open a WebSocket to the jt-ipam server (same URL, TLS settings and CA as the rest of the agent)."""
    u = urllib.parse.urlsplit(SERVER)
    host = u.hostname or ""
    tls = u.scheme == "https"
    port = u.port or (443 if tls else 80)
    raw = socket.create_connection((host, port), timeout=timeout)
    try:
        sock = _ctx().wrap_socket(raw, server_hostname=host) if tls else raw
        if tls:
            _check_pin(sock)              # 主控台中繼一樣只跟釘選的伺服器講話
        key = base64.b64encode(os.urandom(16)).decode()
        hosthdr = f"[{host}]" if ":" in host else host
        if u.port:
            hosthdr += f":{u.port}"
        lines = [f"GET {u.path.rstrip('/')}{path} HTTP/1.1", f"Host: {hosthdr}", "Upgrade: websocket",
                 "Connection: Upgrade", f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13",
                 f"X-Agent-Key: {KEY}", f"X-Agent-Version: {AGENT_VERSION}", f"X-Agent-Tls: {_tls_mode()}"]
        lines += [f"{k}: {v}" for k, v in headers.items()]
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = sock.recv(4096)
            if not chunk:
                raise _WSError("server closed during handshake")
            resp += chunk
            if len(resp) > 16384:
                raise _WSError("handshake response too large")
        head, _, rest = resp.partition(b"\r\n\r\n")
        status, *hdr_lines = head.decode("latin-1").split("\r\n")
        parts = status.split(" ", 2)
        if len(parts) < 2 or parts[1] != "101":
            raise _WSError(f"handshake refused: {status[:80]}")
        hdrs = {}
        for line in hdr_lines:
            k, _, v = line.partition(":")
            hdrs[k.strip().lower()] = v.strip()
        want = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()  # noqa: S324 -- RFC 6455 handshake
        if hdrs.get("sec-websocket-accept") != want:
            raise _WSError("bad Sec-WebSocket-Accept")
        sock.settimeout(120)
        return _WS(sock, rest)
    except BaseException:
        raw.close()
        raise


def _relay_report(jid, error=None) -> None:
    try:
        _req("POST", f"/api/v1/scan-agents/jobs/{jid}/result",
             {"result": None if error else {"relay": "open"}, "error": error})
    except Exception as exc:  # noqa: BLE001
        print(f"[relay] report failed: {type(exc).__name__}", file=sys.stderr, flush=True)


def _relay_pump(tsock, ws: _WS) -> str:
    """Move bytes until either side closes. Single thread, one selector over both sockets."""
    sel = selectors.DefaultSelector()
    sel.register(tsock, selectors.EVENT_READ, "target")
    sel.register(ws.sock, selectors.EVENT_READ, "ws")
    last_sent = time.monotonic()

    def handle(frames: list):
        for opcode, payload in frames:
            if opcode == 0x2:
                tsock.sendall(payload)
            elif opcode == 0x9:
                ws.send(0xA, payload)
            elif opcode == 0x8:
                return "server_closed"
            # 0x1 (server keepalive) and 0xA (pong) need nothing
        return None

    try:
        early = handle(ws.buffered_frames())
        if early:
            return early
        while True:
            wait = max(0.5, RELAY_KEEPALIVE - (time.monotonic() - last_sent))
            events = sel.select(wait)
            if not events and time.monotonic() - last_sent >= RELAY_KEEPALIVE:
                ws.send(0x1, b"ka")
                last_sent = time.monotonic()
            for key, _ in events:
                if key.data == "target":
                    data = tsock.recv(RELAY_CHUNK)
                    if not data:
                        return "target_closed"
                    ws.send(0x2, data)
                    last_sent = time.monotonic()
                    continue
                done = handle(ws.read_frames())
                if done or ws.closed:
                    return done or "server_closed"
    finally:
        sel.close()


def _relay_session(job: dict) -> None:
    """One console session. Never raises: relaying must not disturb scanning or probes."""
    jid = job.get("id")
    params = job.get("params") or {}
    target, sid, ticket = str(params.get("target") or ""), str(params.get("sid") or ""), str(params.get("ticket") or "")
    try:
        port = int(params.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    if not _SID_OK.match(sid) or not _TOKEN_OK.match(ticket):
        _relay_report(jid, "relay_ws_failed: malformed job")
        return
    # The job carries the scope the server allows right now: no waiting for the next poll after an
    # admin switches relay on in the web UI
    scope = params.get("scope")
    if isinstance(scope, dict):
        _relay_set_assigned(scope.get("cidrs") or [], scope.get("ports") or [], scope.get("max") or 0)
    err = _relay_target_ok(target, port)
    if err:
        _relay_report(jid, f"{err}: {target}:{port}")
        return
    if not _relay_acquire():
        _relay_report(jid, f"relay_busy: {_relay_limit()} sessions in use")
        return
    tsock = ws = None
    reason = "error"
    started = time.monotonic()
    try:
        try:
            tsock = socket.create_connection((target, port), timeout=RELAY_CONNECT_TIMEOUT)
            tsock.settimeout(None)
        except OSError as exc:
            _relay_report(jid, f"relay_connect_failed: {exc.strerror or exc}")
            return
        try:
            ws = _ws_connect(f"/api/v1/scan-agents/relay/{sid}/ws", {"X-Relay-Ticket": ticket})
        except Exception as exc:  # noqa: BLE001
            _relay_report(jid, f"relay_ws_failed: {type(exc).__name__}: {exc}")
            return
        _relay_report(jid)
        print(f"[relay] open {sid[:8]} -> {target}:{port}", flush=True)
        reason = _relay_pump(tsock, ws)
    except Exception as exc:  # noqa: BLE001
        reason = f"{type(exc).__name__}: {exc}"
    finally:
        for s in (ws, tsock):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        _relay_release()
        if ws is not None:
            print(f"[relay] closed {sid[:8]} after {time.monotonic() - started:.0f}s ({reason})", flush=True)


def _jobs_loop() -> None:
    """Long-poll for on-demand probes. Runs in its own thread.

    Wrapped so that any failure here can never take the scanning loop down with it --
    the agent's primary job is scanning; this is an extra.
    """
    while True:
        try:
            resp = _req("GET", "/api/v1/scan-agents/jobs?wait=25") or {}
            for job in resp.get("jobs") or []:
                if str(job.get("kind")) == "relay_open":
                    # Console relay: long-lived, one thread per session (never blocks probes or scanning)
                    threading.Thread(target=_relay_session, args=(job,), name="jt-ipam-relay",
                                     daemon=True).start()
                    continue
                if _job_runs_in_background(str(job.get("kind"))):
                    threading.Thread(target=_job_run_and_report, args=(job,), daemon=True).start()
                else:
                    _job_run_and_report(job)
        except Exception as exc:  # noqa: BLE001
            print(f"[jobs] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            time.sleep(10)      # back off; do not hammer a server that is down


def main() -> int:
    if not SERVER or not KEY:
        print("ERROR: JT_IPAM_URL and JT_IPAM_AGENT_KEY environment variables are required",
              file=sys.stderr)
        return 2
    # Refuse anything that is not http(s). urllib happily opens file:, ftp: and
    # friends, so a mistyped JT_IPAM_URL would otherwise turn every poll into a
    # local file read -- with the agent key attached to it.
    if not SERVER.startswith(("https://", "http://")):
        print(f"ERROR: JT_IPAM_URL must start with https:// (or http://), got: {SERVER}",
              file=sys.stderr)
        return 2
    print(f"jt-ipam agent v{AGENT_VERSION} -> {SERVER}  fallback_interval={INTERVAL}s "
          f"tls={_tls_mode()} auto_update={AUTO_UPDATE}", flush=True)
    # fast loop 由 server 的 interval_seconds 決定；poll 失敗時退回 env INTERVAL。
    sleep_for = INTERVAL
    # On-demand probe jobs run in a separate thread: scanning must not wait on them,
    # and a failure there must not stop scanning.
    threading.Thread(target=_jobs_loop, name="jt-ipam-jobs", daemon=True).start()
    # 重量探測（反解／NetBIOS／mDNS／OS 指紋）在背景跑，上線偵測每輪都能準時做完、立刻回報
    threading.Thread(target=_heavy_loop, name="jt-ipam-heavy", daemon=True).start()
    # SNMP 收集（1.19.0）：跟掃描週期分開，各區段依自己的間隔；沒有指派目標時什麼都不做
    threading.Thread(target=_snmp_loop, name="jt-ipam-snmp", daemon=True).start()
    while True:
        try:
            scan_once()
            # scan_once 內已用 server 回的 interval_seconds；這裡再讀一次當下次 sleep。
        except Exception as exc:  # noqa: BLE001 — stay resilient, retry next round
            print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        # 取最近一次 poll 拿到的 fast loop（存在模組層好讓 sleep 跟上）
        sleep_for = _CURRENT_FAST[0] or INTERVAL
        time.sleep(sleep_for)


if __name__ == "__main__":
    raise SystemExit(main())
