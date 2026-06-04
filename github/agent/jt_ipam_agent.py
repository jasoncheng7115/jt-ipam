#!/usr/bin/env python3
"""jt-ipam scan agent (push model, standard library only).

Runs inside the target network segment and connects OUT to the jt-ipam server:
  1. GET  {SERVER}/api/v1/scan-agents/poll    -> subnets to scan (+ server agent sha)
  2. ICMP ping sweep each subnet, fill MAC from the local ARP table
  3. POST {SERVER}/api/v1/scan-agents/report  -> send results back

Auth: every request carries header  X-Agent-Key: <enrollment key>  (server compares sha256).

Auto-update: each poll returns the server's agent.py sha256. If it differs from this
running copy, the agent downloads the new agent.py, overwrites itself and re-executes.

Environment variables:
  JT_IPAM_URL        e.g. https://192.0.2.10      (required)
  JT_IPAM_AGENT_KEY  enrollment key from the agent page (required)
  JT_IPAM_INTERVAL   seconds between rounds, default 300
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
import ssl
import subprocess
import sys
import time
import urllib.request

AGENT_VERSION = "1.1.0"
SERVER = os.environ.get("JT_IPAM_URL", "").rstrip("/")
KEY = os.environ.get("JT_IPAM_AGENT_KEY", "")
INTERVAL = int(os.environ.get("JT_IPAM_INTERVAL", "300"))
INSECURE = os.environ.get("JT_IPAM_INSECURE", "") in ("1", "true", "yes")
MAX_HOSTS = int(os.environ.get("JT_IPAM_MAX_HOSTS", "1024"))
AUTO_UPDATE = os.environ.get("JT_IPAM_AUTO_UPDATE", "1") not in ("0", "false", "no")
PING_WORKERS = 128
AGENT_PATH = os.path.realpath(__file__)


def _ctx() -> ssl.SSLContext | None:
    if not SERVER.startswith("https"):
        return None
    ctx = ssl.create_default_context()
    if INSECURE:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _req(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{SERVER}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-Agent-Key", KEY)
    req.add_header("X-Agent-Version", AGENT_VERSION)
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


def _ping(ip: str) -> bool:
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
        )
        return r.returncode == 0
    except Exception:
        return False


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


def _hosts(cidr: str) -> list[str]:
    net = ipaddress.ip_network(cidr, strict=False)
    if not isinstance(net, ipaddress.IPv4Network):
        return []   # this build scans IPv4 only
    hosts = [str(h) for h in net.hosts()]
    if len(hosts) > MAX_HOSTS:
        print(f"  subnet {cidr} too large ({len(hosts)} hosts) -> scanning first {MAX_HOSTS}", flush=True)
        hosts = hosts[:MAX_HOSTS]
    return hosts


def scan_once() -> None:
    poll = _req("GET", "/api/v1/scan-agents/poll")
    _maybe_self_update(poll.get("agent_sha"))
    subnets = poll.get("subnets") or []
    print(f"[poll] agent={poll.get('agent')} subnets={len(subnets)}", flush=True)
    results: list[dict] = []
    for s in subnets:
        cidr = s.get("cidr")
        if not cidr:
            continue
        hosts = _hosts(cidr)
        alive: list[str] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=PING_WORKERS) as ex:
            for ip, ok in zip(hosts, ex.map(_ping, hosts)):
                if ok:
                    alive.append(ip)
        arp = _arp_table()
        for ip in alive:
            results.append({"ip": ip, "alive": True, "mac": arp.get(ip)})
        print(f"  {cidr}: {len(alive)}/{len(hosts)} alive", flush=True)
    if results:
        r = _req("POST", "/api/v1/scan-agents/report", {"results": results})
        print(f"[report] sent={len(results)} updated={r.get('updated')}", flush=True)
    else:
        print("[report] nothing alive", flush=True)


def main() -> int:
    if not SERVER or not KEY:
        print("ERROR: JT_IPAM_URL and JT_IPAM_AGENT_KEY environment variables are required",
              file=sys.stderr)
        return 2
    print(f"jt-ipam agent -> {SERVER}  interval={INTERVAL}s insecure={INSECURE} "
          f"auto_update={AUTO_UPDATE}", flush=True)
    while True:
        try:
            scan_once()
        except Exception as exc:  # noqa: BLE001 — stay resilient, retry next round
            print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    raise SystemExit(main())
