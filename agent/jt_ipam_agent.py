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
running copy, the agent downloads the new agent.py, overwrites itself and re-executes.

排程（重點）：輕量探測（icmp/tcp/arp/rdns）跟著 fast loop（interval_seconds）每輪跑；
重量探測（os/ports，可能上 nmap）只在距離上次執行超過 intervals[probe] 秒時才跑。
每個探測的「上次執行時間」存在記憶體裡，依此節流。

Environment variables:
  JT_IPAM_URL        e.g. https://192.0.2.10      (required)
  JT_IPAM_AGENT_KEY  enrollment key from the agent page (required)
  JT_IPAM_INTERVAL   fallback fast-loop seconds if server omits interval_seconds, default 300
  JT_IPAM_INSECURE   =1 to skip TLS verification (self-signed server)
  JT_IPAM_MAX_HOSTS  max hosts scanned per subnet, default 1024 (avoid huge /16)
  JT_IPAM_AUTO_UPDATE =0 to disable self-update (default on)
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.request

AGENT_VERSION = "1.5.0"
SERVER = os.environ.get("JT_IPAM_URL", "").rstrip("/")
KEY = os.environ.get("JT_IPAM_AGENT_KEY", "")
INTERVAL = int(os.environ.get("JT_IPAM_INTERVAL", "300"))
INSECURE = os.environ.get("JT_IPAM_INSECURE", "") in ("1", "true", "yes")
MAX_HOSTS = int(os.environ.get("JT_IPAM_MAX_HOSTS", "1024"))
AUTO_UPDATE = os.environ.get("JT_IPAM_AUTO_UPDATE", "1") not in ("0", "false", "no")
PING_WORKERS = 128
AGENT_PATH = os.path.realpath(__file__)

# 所有已知探測鍵；server 沒指定 probes 時，向下相容用 icmp。
ALL_PROBES = ("icmp", "tcp", "arp", "rdns", "netbios", "mdns", "os", "ports")
DEFAULT_PROBES = ("icmp",)

# tcp 探測掃的常見埠（也作為 alive 判定依據）
TCP_PROBE_PORTS = (22, 80, 443, 445, 3389, 8006)
TCP_PROBE_TIMEOUT = 1.0

# 每個探測在記憶體裡的「上次執行時間」：key = (subnet_id, probe) -> epoch seconds。
# 用 subnet 粒度節流（同一輪同子網的所有 host 一起跑某探測或一起跳過）。
_last_run: dict[tuple, float] = {}

# scan_once 把 server 回的 fast loop 寫在這，給 main 的 sleep 用（單元素 list 當可變參照）。
_CURRENT_FAST: list[int] = [0]


def _ctx() -> ssl.SSLContext | None:
    if not SERVER.startswith("https"):
        return None
    ctx = ssl.create_default_context()
    if INSECURE:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _capabilities() -> list[str]:
    """回報本機實際能做的探測（送 X-Agent-Probes header 給 server）。

    icmp/tcp/rdns 一律支援；arp 需 neigh 表可讀；os/ports 需 PATH 上有 nmap；
    netbios 需 nmblookup/nbtscan、mdns 需 avahi-resolve（有才回報能力、才會實際查名）。
    不做需要憑證/社群字串的探測（如 SNMP）。
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
    # 去重並維持 ALL_PROBES 順序
    seen = set(caps)
    return [p for p in ALL_PROBES if p in seen]


# 探測相依的外部工具（name, version-args）。server 端另有 probes/package 對照表（顯示用）。
_DEP_TOOLS = (
    ("python3", ["--version"]),
    ("ping", ["-V"]),
    ("ip", ["-V"]),
    ("nmap", ["--version"]),
    ("nmblookup", ["-V"]),
    ("nbtscan", []),         # 無可靠的版本旗標
    ("avahi-resolve", []),   # 無可靠的版本旗標
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
         extra_headers: dict | None = None) -> dict:
    url = f"{SERVER}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-Agent-Key", KEY)
    req.add_header("X-Agent-Version", AGENT_VERSION)
    if extra_headers:
        for k, v in extra_headers.items():
            req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30, context=_ctx()) as resp:
        return json.loads(resp.read().decode() or "{}")


def _get_bytes(path: str) -> bytes:
    req = urllib.request.Request(f"{SERVER}{path}", method="GET")
    req.add_header("X-Agent-Key", KEY)
    req.add_header("X-Agent-Version", AGENT_VERSION)
    with urllib.request.urlopen(req, timeout=30, context=_ctx()) as resp:
        return resp.read()


def _self_sha() -> str:
    try:
        with open(AGENT_PATH, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


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


def _rdns(ip: str) -> str | None:
    """rdns 探測：反查 hostname。"""
    try:
        host, _, _ = socket.gethostbyaddr(ip)
        return host or None
    except Exception:
        return None


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


def _nmap_os_ports(ip: str, want_os: bool, want_ports: bool) -> dict:
    """os / ports 重量探測：有 nmap 才跑，回 {os_guess?, open_ports?}。

    os 偵測需 root（-O），失敗就只回 ports；任何錯誤都回空 dict（略過）。
    """
    result: dict = {}
    if not shutil.which("nmap"):
        return result
    args = ["nmap", "-Pn", "-T4", "--host-timeout", "30s"]
    if want_ports:
        args += ["--top-ports", "100"]
    else:
        args += ["-p", ",".join(str(p) for p in TCP_PROBE_PORTS)]
    if want_os:
        args.append("-O")
    args.append(ip)
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, timeout=60,
        )
        text = r.stdout or ""
        if want_ports:
            ports: list[int] = []
            for line in text.splitlines():
                m = re.match(r"^(\d+)/tcp\s+open", line.strip())
                if m:
                    ports.append(int(m.group(1)))
            if ports:
                result["open_ports"] = ports
        if want_os:
            m = re.search(r"OS details:\s*(.+)", text)
            if not m:
                m = re.search(r"Running:\s*(.+)", text)
            if not m:
                m = re.search(r"OS guesses:\s*(.+)", text)
            if m:
                result["os_guess"] = m.group(1).strip()[:200]
    except Exception:
        return {}
    return result


def _hosts(cidr: str) -> list[str]:
    net = ipaddress.ip_network(cidr, strict=False)
    if not isinstance(net, ipaddress.IPv4Network):
        return []   # this build scans IPv4 only
    hosts = [str(h) for h in net.hosts()]
    if len(hosts) > MAX_HOSTS:
        print(f"  subnet {cidr} too large ({len(hosts)} hosts) -> scanning first {MAX_HOSTS}", flush=True)
        hosts = hosts[:MAX_HOSTS]
    return hosts


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


def scan_once() -> None:
    caps = _capabilities()
    poll = _req("GET", "/api/v1/scan-agents/poll",
                extra_headers={"X-Agent-Probes": ",".join(caps),
                               "X-Agent-Tools": _tools_header()})
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

    cap_set = set(caps)
    now = time.time()
    results: list[dict] = []

    for s in subnets:
        cidr = s.get("cidr")
        if not cidr:
            continue
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

        hosts = _hosts(cidr)
        # arp 表用於 arp 探測，也順手在其他探測時補 mac。
        arp = _arp_table()

        # 先用 icmp/tcp/arp 判定哪些 host alive；其餘探測只對 alive host 跑。
        icmp_alive: dict[str, bool] = {}
        if "icmp" in due:
            with concurrent.futures.ThreadPoolExecutor(max_workers=PING_WORKERS) as ex:
                for ip, ok in zip(hosts, ex.map(_ping, hosts)):
                    icmp_alive[ip] = bool(ok)

        tcp_ports: dict[str, list[int]] = {}
        if "tcp" in due:
            with concurrent.futures.ThreadPoolExecutor(max_workers=PING_WORKERS) as ex:
                for ip, ports in zip(hosts, ex.map(_tcp_scan, hosts)):
                    if ports:
                        tcp_ports[ip] = ports

        # 整理每個 host 的本輪結果。
        subnet_alive = 0
        for ip in hosts:
            # 套用 per-IP override：略過指定探測
            skip = set(ip_overrides.get(ip) or [])
            host_probes = [p for p in due if p not in skip]
            if not host_probes:
                continue

            alive = False
            item: dict = {"ip": ip}
            probes_run: list[str] = []

            if "icmp" in host_probes:
                probes_run.append("icmp")
                if icmp_alive.get(ip):
                    alive = True

            if "tcp" in host_probes:
                probes_run.append("tcp")
                ports = tcp_ports.get(ip) or []
                if ports:
                    alive = True
                    item["open_ports"] = sorted(set(ports))

            if "arp" in host_probes:
                probes_run.append("arp")
                mac = arp.get(ip)
                if mac:
                    item["mac"] = mac
                    alive = True  # 在 neigh 表代表本子網有回應過
            else:
                # 即使沒跑 arp 探測，若 arp 表剛好有資料也順手補 mac
                mac = arp.get(ip)
                if mac:
                    item["mac"] = mac

            # rdns / 重量探測只對「目前判定 alive」的 host 跑，省資源。
            if alive and "rdns" in host_probes:
                probes_run.append("rdns")
                rd = _rdns(ip)
                if rd:
                    item["rdns"] = rd

            if alive and ("os" in host_probes or "ports" in host_probes):
                want_os = "os" in host_probes
                want_ports = "ports" in host_probes
                np = _nmap_os_ports(ip, want_os, want_ports)
                if want_os:
                    probes_run.append("os")
                if want_ports:
                    probes_run.append("ports")
                if np.get("os_guess"):
                    item["os_guess"] = np["os_guess"]
                if np.get("open_ports"):
                    merged = set(item.get("open_ports") or []) | set(np["open_ports"])
                    item["open_ports"] = sorted(merged)

            # NetBIOS / mDNS：對 alive host 實際查名（需 nmblookup / avahi-resolve，
            # 能力清單已先用 shutil.which 過濾，沒工具就不會進到 host_probes）。
            if alive and "netbios" in host_probes:
                probes_run.append("netbios")
                nb = _netbios(ip)
                if nb:
                    item["netbios"] = nb
            if alive and "mdns" in host_probes:
                probes_run.append("mdns")
                md = _mdns(ip)
                if md:
                    item["mdns"] = md
            # snmp 仍不實作（需社群字串/憑證，違反「不做需憑證探測」原則）；列到只記錄已嘗試。
            if "snmp" in host_probes:
                probes_run.append("snmp")

            if alive:
                item["alive"] = True
                item["probes_run"] = probes_run
                results.append(item)
                subnet_alive += 1

        summary = "+".join(due)
        print(f"  {cidr}: probes={summary} alive={subnet_alive}/{len(hosts)}", flush=True)

    if results:
        r = _req("POST", "/api/v1/scan-agents/report", {"results": results})
        print(f"[report] sent={len(results)} updated={r.get('updated')}", flush=True)
    else:
        print("[report] nothing to report", flush=True)


def main() -> int:
    if not SERVER or not KEY:
        print("ERROR: JT_IPAM_URL and JT_IPAM_AGENT_KEY environment variables are required",
              file=sys.stderr)
        return 2
    print(f"jt-ipam agent v{AGENT_VERSION} -> {SERVER}  fallback_interval={INTERVAL}s "
          f"insecure={INSECURE} auto_update={AUTO_UPDATE}", flush=True)
    # fast loop 由 server 的 interval_seconds 決定；poll 失敗時退回 env INTERVAL。
    sleep_for = INTERVAL
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
