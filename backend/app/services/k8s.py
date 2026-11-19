"""Kubernetes 同步（kube-apiserver REST，全程唯讀）。

對 IPAM 有意義的是會佔用或撞到實體網段的位址（Pod IP 是叢集內部、隨時在變的，不收）：
1. 節點 IP（InternalIP／ExternalIP）→ 對應既有 IP、主機名稱來源、節點 Ready 當上線證據、providerID 連到既有 VM
2. Service 佔用的位址：LoadBalancer 發到的 IP、spec.externalIPs → 標出哪個 namespace/service 在用
3. LB 位址集區（MetalLB IPAddressPool、Cilium CiliumLoadBalancerIPPool）→ 建成「位址範圍（集區）」（用途＝保留）
4. 叢集網段（節點 podCIDR、Calico Workload 集區、ServiceCIDR）→ 跟實體子網路重疊要提醒；可直接路由的
   （Calico 不 NAT 不封裝、或叢集標成可路由）是刻意的對應，改成顯示對應到哪個子網路
5. 網域名稱（第二階段）：Ingress、Gateway API（Gateway listener、HTTPRoute、GRPCRoute）、OpenShift Route
   → 名稱指向哪個位址、跟 IPAM 同步的 DNS 記錄對不對得上；也進「對外開放服務」的 FQDN 檢視
6. Multus 次要網卡：Whereabouts 集區＋發給 Pod 的位址；NetworkAttachmentDefinition 寫死的 host-local 網段與 static 位址
7. Egress IP（第三階段）：OVN-Kubernetes EgressIP、Cilium egress gateway、Antrea Egress 與 ExternalIPPool
   → Pod 對外連線的來源位址（防火牆規則常綁它）；API 位址本身是 IP 時也記下來（改了它所有 kubeconfig 都要改）

第二階段的資源都是「選用」：沒裝（404）略過；唯讀帳號還是第一階段的版本（403）也照樣同步，
讀不到的資源記在 `missing_permissions`，畫面提示重新套用 manifest。

認證：ServiceAccount token（`Authorization: Bearer`）或 kubeconfig 裡的用戶端憑證；叢集 CA 有填就只信任它。
kubeconfig 用 exec／auth-provider 的雲端驗證（EKS、GKE…）在伺服器端跑不了，明確拒絕。
清單一律分頁（limit／continue），大叢集也不會一次撈爆。
"""

from __future__ import annotations

import base64
import contextlib
import ipaddress
import logging
import os
import re
import ssl
import tempfile
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_secret, encrypt_secret
from app.core.ui_error import UiError
from app.models.k8s import K8sCidr, K8sCluster, K8sHostname, K8sIPPool, K8sNode, K8sServiceIP
from app.services.ip_autocreate import match_existing_many

_log = logging.getLogger(__name__)
PAGE = 500
#: 選用的資源：叢集沒裝（404）就略過，不算同步失敗
POOL_SOURCES = (
    ("metallb", ("/apis/metallb.io/v1beta1/ipaddresspools",)),
    ("cilium", ("/apis/cilium.io/v2/ciliumloadbalancerippools", "/apis/cilium.io/v2alpha1/ciliumloadbalancerippools")),
)
SERVICE_CIDR_PATHS = ("/apis/networking.k8s.io/v1/servicecidrs", "/apis/networking.k8s.io/v1beta1/servicecidrs")
_GW = "/apis/gateway.networking.k8s.io"
#: 第二階段：名稱與次要網卡（key 也是測試連線的檢查項目）
PHASE2_SOURCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ingresses", ("/apis/networking.k8s.io/v1/ingresses",)),
    ("gateways", (f"{_GW}/v1/gateways", f"{_GW}/v1beta1/gateways")),
    ("httproutes", (f"{_GW}/v1/httproutes", f"{_GW}/v1beta1/httproutes")),
    ("grpcroutes", (f"{_GW}/v1/grpcroutes", f"{_GW}/v1alpha2/grpcroutes")),
    ("routes", ("/apis/route.openshift.io/v1/routes",)),
    # Calico：CRD（crd.projectcalico.org）一定有；裝了 Calico API server 才有 projectcalico.org/v3
    ("calico", ("/apis/crd.projectcalico.org/v1/ippools", "/apis/projectcalico.org/v3/ippools")),
    ("whereabouts", ("/apis/whereabouts.cni.cncf.io/v1alpha1/ippools",)),
    # 第三階段：Egress IP 與 Multus 的網段設定
    ("ovn_egress", ("/apis/k8s.ovn.org/v1/egressips",)),
    ("cilium_egress", ("/apis/cilium.io/v2/ciliumegressgatewaypolicies",
                       "/apis/cilium.io/v2alpha1/ciliumegressgatewaypolicies")),
    ("antrea_egress", ("/apis/crd.antrea.io/v1beta2/egresses", "/apis/crd.antrea.io/v1beta1/egresses")),
    ("antrea_pools", ("/apis/crd.antrea.io/v1beta2/externalippools", "/apis/crd.antrea.io/v1beta1/externalippools")),
    ("nads", ("/apis/k8s.cni.cncf.io/v1/network-attachment-definitions",)),
)


class K8sError(UiError, RuntimeError):
    pass


class _NotInstalled(Exception):
    """選用的 API（CRD）不存在。"""


# ─────────────────── 機密 ───────────────────

def _aad(cluster_id: Any, field: str) -> bytes:
    return f"k8s_cluster:{cluster_id}:{field}".encode()


def encrypt_field(cluster_id: Any, field: str, raw: str) -> tuple[bytes, bytes]:
    return encrypt_secret(raw, aad=_aad(cluster_id, field))


def _secret(c: K8sCluster, field: str) -> str | None:
    enc, nonce = getattr(c, f"{field}_enc", None), getattr(c, f"{field}_nonce", None)
    if not enc or not nonce:
        return None
    return decrypt_secret(enc, nonce, aad=_aad(c.id, field)).decode("utf-8")


# ─────────────────── kubeconfig ───────────────────

def _b64(v: Any) -> str | None:
    if not v:
        return None
    try:
        return base64.b64decode(str(v)).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise K8sError("kubeconfig 裡的憑證資料不是有效的 base64", code="k8s_kubeconfig_invalid") from exc


def parse_kubeconfig(text: str, context: str | None = None) -> dict[str, Any]:
    """從貼上的 kubeconfig 取出 API 位址、CA、token 或用戶端憑證（只接受內嵌的資料）。"""
    import yaml
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise K8sError("kubeconfig 不是有效的 YAML", code="k8s_kubeconfig_invalid") from exc
    if not isinstance(doc, dict) or not doc.get("clusters"):
        raise K8sError("這不是 kubeconfig（找不到 clusters）", code="k8s_kubeconfig_invalid")

    def named(kind: str, name: str | None) -> dict[str, Any]:
        items = doc.get(kind) or []
        for it in items:
            if isinstance(it, dict) and (name is None or it.get("name") == name):
                return it.get(kind[:-1]) or {}
        return {}

    ctx_name = context or doc.get("current-context")
    ctx = named("contexts", ctx_name) if ctx_name else named("contexts", None)
    cluster = named("clusters", ctx.get("cluster")) if ctx else named("clusters", None)
    user = named("users", ctx.get("user")) if ctx else named("users", None)
    if not cluster.get("server"):
        raise K8sError("kubeconfig 裡沒有 API 位址（server）", code="k8s_kubeconfig_invalid")
    if user.get("exec") or user.get("auth-provider"):
        raise K8sError("這個 kubeconfig 用外部程式取得驗證（exec／auth-provider，例如 EKS、GKE），伺服器端無法執行；"
                       "請改用唯讀 ServiceAccount 的 token", code="k8s_kubeconfig_exec")
    if any(user.get(k) for k in ("tokenFile", "client-certificate", "client-key")) or cluster.get("certificate-authority"):
        raise K8sError("kubeconfig 指向本機的檔案，請改用內嵌的資料（*-data）或唯讀 ServiceAccount 的 token",
                       code="k8s_kubeconfig_files")
    out: dict[str, Any] = {
        "api_url": str(cluster["server"]).rstrip("/"),
        "ca_cert": _b64(cluster.get("certificate-authority-data")),
        "verify_tls": not bool(cluster.get("insecure-skip-tls-verify")),
    }
    if user.get("token"):
        out.update(auth_method="token", token=str(user["token"]))
    elif user.get("client-certificate-data") and user.get("client-key-data"):
        out.update(auth_method="client_cert", client_cert=_b64(user["client-certificate-data"]),
                   client_key=_b64(user["client-key-data"]))
    else:
        raise K8sError("kubeconfig 裡沒有 token 或用戶端憑證", code="k8s_kubeconfig_no_credentials")
    return out


# ─────────────────── HTTP ───────────────────

def _ssl_context(c: K8sCluster) -> ssl.SSLContext | bool:
    """叢集 CA（有填就只信任它）＋用戶端憑證（ssl 只吃檔案：寫進 0600 的暫存檔、載入後立刻刪）。"""
    key = _secret(c, "client_key") if c.auth_method == "client_cert" else None
    if not key and c.verify_tls and not (c.ca_cert or "").strip():
        return True
    ctx = ssl.create_default_context(cadata=c.ca_cert) if (c.verify_tls and (c.ca_cert or "").strip()) \
        else ssl.create_default_context()
    if not c.verify_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if key and c.client_cert:
        fd, path = tempfile.mkstemp(prefix="jtipam-k8s-", suffix=".pem")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(c.client_cert.strip() + "\n" + key.strip() + "\n")
            ctx.load_cert_chain(path)
        except ssl.SSLError as exc:
            raise K8sError("用戶端憑證或私鑰無法載入", code="k8s_client_cert_invalid", reason=str(exc)[:200]) from exc
        finally:
            with contextlib.suppress(OSError):
                os.unlink(path)
    return ctx


@contextlib.asynccontextmanager
async def _client(c: K8sCluster, timeout: float = 30.0) -> AsyncIterator[httpx.AsyncClient]:
    """出站一律走 GuardedTransport（連線當下檢查位址）、不跟轉址、不信任環境變數。測試會換掉這一支。"""
    from app.core.safe_http import GuardedTransport
    headers = {"Accept": "application/json"}
    if c.auth_method == "token":
        token = _secret(c, "token")
        if not token:
            raise K8sError("沒有設定 token", code="k8s_no_credentials")
        headers["Authorization"] = f"Bearer {token.strip()}"
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False, headers=headers,
                                 base_url=c.api_url.rstrip("/"),
                                 transport=GuardedTransport(verify=_ssl_context(c))) as client:
        yield client


def _transport_error(exc: BaseException) -> K8sError:
    from app.core.safe_http import UnsafeOutboundURL, transport_detail
    reason = transport_detail(exc)
    if isinstance(exc, UnsafeOutboundURL):
        return K8sError(f"blocked: {exc}", code="k8s_unreachable", reason=str(exc)[:200])
    if re.search(r"CERTIFICATE_VERIFY_FAILED|certificate verify failed|SSL", f"{reason} {exc.__cause__ or ''}"):
        return K8sError(f"TLS verification failed: {reason}", code="k8s_tls_failed", reason=reason)
    return K8sError(f"connection failed: {reason}", code="k8s_unreachable", reason=reason)


async def _get(client: httpx.AsyncClient, path: str, params: dict[str, Any] | None = None,
               *, optional: bool = False) -> Any:
    from app.core.safe_http import UnsafeOutboundURL
    try:
        resp = await client.get(path, params=params)
    except (httpx.HTTPError, UnsafeOutboundURL, ssl.SSLError) as exc:   # 連線／TLS／逾時、SSRF 守門擋下
        raise _transport_error(exc) from exc
    if resp.status_code == 404 and optional:
        raise _NotInstalled(path)
    if resp.status_code == 401:
        raise K8sError("token 或用戶端憑證無效（401）", code="k8s_auth")
    if resp.status_code == 403:
        raise K8sError(f"沒有權限讀取 {path}（403），請套用 jt-ipam 的唯讀 ClusterRole",
                       code="k8s_forbidden", path=path)
    if resp.status_code != 200:
        raise K8sError(f"Kubernetes {path}: HTTP {resp.status_code}", code="k8s_http", path=path,
                       status=resp.status_code, body=" ".join(resp.text[:200].split()))
    try:
        return resp.json()
    except ValueError as exc:
        raise K8sError(f"Kubernetes {path}: 回應不是 JSON", code="k8s_not_json", path=path,
                       ctype=resp.headers.get("content-type", "?")) from exc


async def _list(client: httpx.AsyncClient, path: str, *, optional: bool = False) -> list[dict[str, Any]]:
    """分頁讀完整份清單（limit／continue）。"""
    items: list[dict[str, Any]] = []
    cont: str | None = None
    while True:
        params: dict[str, Any] = {"limit": PAGE}
        if cont:
            params["continue"] = cont
        body = await _get(client, path, params, optional=optional)
        items.extend(i for i in (body.get("items") or []) if isinstance(i, dict))
        cont = (body.get("metadata") or {}).get("continue")
        if not cont:
            return items


async def _installed(client: httpx.AsyncClient, path: str) -> bool:
    """RBAC 擋在前面：沒權限時，沒裝的資源也回 403。改問 discovery（`/apis/<group>/<version>`，
    system:discovery 讓每個已驗證的帳號都讀得到）這個資源到底存不存在。問不到就當作存在（保守：照樣提示缺權限）。"""
    parts = path.strip("/").split("/")
    if parts[0] != "apis" or len(parts) < 4:
        return True
    try:
        body = await _get(client, f"/apis/{parts[1]}/{parts[2]}", optional=True)
    except _NotInstalled:
        return False
    except K8sError:
        return True
    return any((r or {}).get("name") == parts[3] for r in (body or {}).get("resources") or [])


async def _list_first(client: httpx.AsyncClient, paths: tuple[str, ...]) -> list[dict[str, Any]] | None:
    """依序試幾個 API 版本；全都沒裝回 None。某個版本沒權限（403）先試下一個，都不行才丟出那個 403
    （真的沒裝的不算，見 _installed）。"""
    forbidden: K8sError | None = None
    for p in paths:
        try:
            return await _list(client, p, optional=True)
        except _NotInstalled:
            continue
        except K8sError as exc:
            if exc.code != "k8s_forbidden":
                raise
            if await _installed(client, p):
                forbidden = forbidden or exc
    if forbidden is not None:
        raise forbidden
    return None


def resource_label(path: str) -> str:
    """`/apis/<group>/<version>/<resource>` → `<resource>.<group>`（ClusterRole 裡要補的那一條）；核心 API 只有資源名稱。"""
    parts = path.strip("/").split("/")
    if parts[0] == "apis" and len(parts) >= 4:
        return f"{parts[3]}.{parts[1]}"
    return parts[-1]


async def _list_soft(client: httpx.AsyncClient, paths: tuple[str, ...], missing: list[str],
                     failed: list[str] | None = None) -> list[dict[str, Any]] | None:
    """選用資源：沒裝回 None；沒權限（唯讀帳號還是舊版）也回 None，並記下缺哪一條權限，同步照跑。
    API 回其他錯誤（例如 CRD 的轉換 webhook 掛了，回 500）也只略過這一項，記在 failed，不拖垮整個同步。"""
    try:
        return await _list_first(client, paths)
    except K8sError as exc:
        label = resource_label(paths[0])
        if exc.code == "k8s_forbidden":
            if label not in missing:
                missing.append(label)
            return None
        if exc.code in ("k8s_http", "k8s_not_json") and failed is not None:
            _log.warning("k8s optional resource %s unreadable: %s", label, exc)
            failed.append(f"{label}: {exc}"[:300])
            return None
        raise


# ─────────────────── 解析 ───────────────────

def _valid_ip(v: Any) -> str | None:
    try:
        return str(ipaddress.ip_address(str(v).strip()))
    except ValueError:
        return None


def parse_node(n: dict[str, Any]) -> dict[str, Any]:
    meta, spec, status = n.get("metadata") or {}, n.get("spec") or {}, n.get("status") or {}
    addrs = [{"type": a.get("type"), "address": a.get("address")} for a in (status.get("addresses") or [])
             if isinstance(a, dict) and a.get("address")]
    ips = [(a["type"], _valid_ip(a["address"])) for a in addrs]
    primary = next((ip for t, ip in ips if t == "InternalIP" and ip), None) or \
        next((ip for t, ip in ips if t == "ExternalIP" and ip), None)
    ready_cond = next((c for c in (status.get("conditions") or []) if c.get("type") == "Ready"), {})
    info = status.get("nodeInfo") or {}
    roles = sorted(k.split("/", 1)[1] for k in (meta.get("labels") or {})
                   if k.startswith("node-role.kubernetes.io/") and k.split("/", 1)[1])
    hb = ready_cond.get("lastHeartbeatTime")
    return {
        "name": str(meta.get("name") or ""), "ip": primary, "addresses": addrs, "roles": roles or None,
        "ready": (ready_cond.get("status") == "True") if ready_cond else None,
        "unschedulable": bool(spec.get("unschedulable")),
        "kubelet_version": (info.get("kubeletVersion") or "")[:64] or None,
        "os_image": (info.get("osImage") or "")[:255] or None,
        "kernel": (info.get("kernelVersion") or "")[:128] or None,
        "runtime": (info.get("containerRuntimeVersion") or "")[:128] or None,
        "provider_id": (spec.get("providerID") or "")[:512] or None,
        "pod_cidrs": spec.get("podCIDRs") or ([spec["podCIDR"]] if spec.get("podCIDR") else None),
        "last_heartbeat": datetime.fromisoformat(hb.replace("Z", "+00:00")) if isinstance(hb, str) and hb else None,
    }


def parse_service_ips(s: dict[str, Any]) -> list[dict[str, Any]]:
    meta, spec, status = s.get("metadata") or {}, s.get("spec") or {}, s.get("status") or {}
    ports = [{"name": p.get("name"), "port": p.get("port"), "protocol": p.get("protocol")}
             for p in (spec.get("ports") or []) if isinstance(p, dict)]
    out: list[dict[str, Any]] = []
    if spec.get("type") == "LoadBalancer":
        for ing in (status.get("loadBalancer") or {}).get("ingress") or []:
            ip = _valid_ip((ing or {}).get("ip"))
            if ip:
                out.append({"kind": "loadbalancer", "ip": ip})
    for raw in spec.get("externalIPs") or []:
        ip = _valid_ip(raw)
        if ip:
            out.append({"kind": "external", "ip": ip})
    base = {"namespace": str(meta.get("namespace") or ""), "name": str(meta.get("name") or ""), "ports": ports or None}
    return [{**base, **o} for o in out]


def _block(raw: Any) -> dict[str, str] | None:
    """`192.0.2.10-192.0.2.20`、CIDR、或 {cidr}／{start, stop} → {"start", "end"}。"""
    if isinstance(raw, dict):
        if raw.get("cidr"):
            raw = raw["cidr"]
        elif raw.get("start"):
            a, b = _valid_ip(raw.get("start")), _valid_ip(raw.get("stop") or raw.get("end") or raw.get("start"))
            return {"start": a, "end": b} if a and b else None
        else:
            return None
    s = str(raw).strip()
    if "-" in s and "/" not in s:
        a, _, b = s.partition("-")
        a2, b2 = _valid_ip(a), _valid_ip(b)
        return {"start": a2, "end": b2} if a2 and b2 else None
    try:
        net = ipaddress.ip_network(s, strict=False)
    except ValueError:
        return None
    return {"start": str(net[0]), "end": str(net[-1])}


def parse_pool(provider: str, p: dict[str, Any]) -> dict[str, Any]:
    meta, spec = p.get("metadata") or {}, p.get("spec") or {}
    raw = {"metallb": spec.get("addresses"), "antrea": spec.get("ipRanges")}.get(provider) \
        or spec.get("blocks") or spec.get("cidrs")
    blocks = [b for b in (_block(x) for x in (raw or [])) if b]
    return {"provider": provider, "name": str(meta.get("name") or ""), "namespace": meta.get("namespace"),
            "blocks": blocks, "auto_assign": spec.get("autoAssign", True) is not False,
            "disabled": bool(spec.get("disabled")),
            # Antrea 的 ExternalIPPool 同時給 LoadBalancer 與 Egress 用
            "use": "external" if provider == "antrea" else "loadbalancer"}


def parse_egress(provider: str, e: dict[str, Any]) -> list[dict[str, Any]]:
    """Egress 物件 → [{name, ip, node}]。只寫網卡、沒寫位址的（Cilium 的 interface）看不到位址，不收。"""
    meta, spec, status = e.get("metadata") or {}, e.get("spec") or {}, e.get("status") or {}
    name = str(meta.get("name") or "")
    node_of: dict[str, str] = {}
    raw: list[Any] = []
    if provider == "ovn":
        raw = list(spec.get("egressIPs") or [])
        node_of = {str(i.get("egressIP")): str(i.get("node")) for i in status.get("items") or []
                   if isinstance(i, dict) and i.get("node")}
    elif provider == "cilium":
        gws = [spec.get("egressGateway"), *(spec.get("egressGateways") or [])]
        raw = [g.get("egressIP") for g in gws if isinstance(g, dict)]
    elif provider == "antrea":
        # v1beta1 是單一的 egressIP，v1beta2 改成 egressIPs 清單（spec 與 status 都是）
        held = [status.get("egressIP"), *(status.get("egressIPs") or [])]
        raw = [spec.get("egressIP"), *(spec.get("egressIPs") or []), *held]
        if status.get("egressNode"):
            node_of = {str(ip): str(status["egressNode"]) for ip in held if ip}
    out: list[dict[str, Any]] = []
    for r in raw:
        ip = _valid_ip(r) if r else None
        if ip and ip not in [o["ip"] for o in out]:
            out.append({"name": name, "ip": ip, "node": node_of.get(ip)})
    return out


def parse_nad(n: dict[str, Any]) -> dict[str, Any]:
    """NetworkAttachmentDefinition 的 CNI 設定（JSON 字串；單一外掛或 plugins 串）：
    host-local 的 ranges（或舊寫法 subnet＋rangeStart／rangeEnd）→ 集區；static 的 addresses → 固定位址。
    Whereabouts 由它自己的 IPPool 處理；DHCP 型的位址由外部 DHCP 發，這裡不管。"""
    import json
    meta = n.get("metadata") or {}
    ns, name = str(meta.get("namespace") or ""), str(meta.get("name") or "")
    empty: dict[str, Any] = {"pool": None, "addresses": []}
    try:
        cfg = json.loads(str((n.get("spec") or {}).get("config") or ""))
    except ValueError:
        return empty
    if not isinstance(cfg, dict):
        return empty
    ipams = [p.get("ipam") for p in [cfg, *(cfg.get("plugins") or [])] if isinstance(p, dict)]
    blocks: list[dict[str, str]] = []
    addrs: list[dict[str, Any]] = []
    for ipam in (i for i in ipams if isinstance(i, dict)):
        if ipam.get("type") == "host-local":
            sets = ipam.get("ranges") or [[{"subnet": ipam.get("subnet"), "rangeStart": ipam.get("rangeStart"),
                                            "rangeEnd": ipam.get("rangeEnd")}]]
            for r in (r for rs in sets for r in (rs or []) if isinstance(r, dict)):
                try:
                    net = ipaddress.ip_network(str(r.get("subnet") or "").strip(), strict=False)
                except ValueError:
                    continue
                lo = _valid_ip(r.get("rangeStart")) or str(net[1] if net.num_addresses > 2 else net[0])
                hi = _valid_ip(r.get("rangeEnd")) or str(net[-2] if net.num_addresses > 2 else net[-1])
                blocks.append({"start": lo, "end": hi})
        elif ipam.get("type") == "static":
            for a in ipam.get("addresses") or []:
                with contextlib.suppress(ValueError, TypeError):
                    addrs.append({"namespace": ns, "name": name,
                                  "ip": str(ipaddress.ip_interface(str(a.get("address")).strip()).ip)})
    pool = {"provider": "multus", "name": f"{ns}/{name}"[:253], "namespace": ns or None, "blocks": blocks,
            "auto_assign": True, "disabled": False, "use": "pod"} if blocks else None
    return {"pool": pool, "addresses": addrs}


def apiserver_ip(api_url: str | None) -> str | None:
    """API 位址的主機是 IP 才算（主機名稱不猜它解析到哪裡）。"""
    from urllib.parse import urlsplit
    try:
        host = urlsplit(api_url or "").hostname
    except ValueError:
        return None
    return _valid_ip(host) if host else None


def _host(v: Any) -> str | None:
    h = str(v or "").strip().rstrip(".").lower()
    return h[:253] or None


def _lb_ips(status: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for ing in (status.get("loadBalancer") or {}).get("ingress") or []:
        ip = _valid_ip((ing or {}).get("ip"))
        if ip and ip not in out:
            out.append(ip)
    return out


def parse_ingress(i: dict[str, Any]) -> list[dict[str, Any]]:
    """Ingress → 每個名稱一列：名稱來自 rules[].host 與 tls[].hosts，位址是 status 裡 LB 發的 IP（還沒發就是空的）。"""
    spec, status = i.get("spec") or {}, i.get("status") or {}
    tls_hosts = {h for t in (spec.get("tls") or []) for h in (_host(x) for x in (t or {}).get("hosts") or []) if h}
    hosts: list[str] = []
    for h in [_host((r or {}).get("host")) for r in spec.get("rules") or []] + sorted(tls_hosts):
        if h and h not in hosts:
            hosts.append(h)
    ips = _lb_ips(status)
    return [{"host": h, "tls": h in tls_hosts, "ips": ips} for h in hosts]


def parse_gateway(g: dict[str, Any]) -> dict[str, Any]:
    """Gateway → 位址（status.addresses 裡 IPAddress 型的）與 listener 的名稱（HTTPS／TLS 算 TLS）。"""
    spec, status = g.get("spec") or {}, g.get("status") or {}
    ips: list[str] = []
    for a in status.get("addresses") or []:
        if (a or {}).get("type", "IPAddress") in ("IPAddress", None):
            ip = _valid_ip(a.get("value"))
            if ip and ip not in ips:
                ips.append(ip)
    hostnames: list[dict[str, Any]] = []
    for ls in spec.get("listeners") or []:
        h = _host((ls or {}).get("hostname"))
        if h and h not in [x["host"] for x in hostnames]:
            hostnames.append({"host": h, "tls": str(ls.get("protocol") or "").upper() in ("HTTPS", "TLS")})
    return {"ips": ips, "hostnames": hostnames}


def parse_route_refs(r: dict[str, Any]) -> dict[str, Any]:
    """HTTPRoute／GRPCRoute → 名稱與掛在哪些 Gateway 上（沒寫 namespace＝跟 route 同一個）。"""
    meta, spec = r.get("metadata") or {}, r.get("spec") or {}
    ns = str(meta.get("namespace") or "")
    hosts = [h for h in (_host(x) for x in spec.get("hostnames") or []) if h]
    parents = [(str(p.get("namespace") or ns), str(p.get("name"))) for p in spec.get("parentRefs") or []
               if isinstance(p, dict) and p.get("name") and (p.get("kind") or "Gateway") == "Gateway"]
    return {"hosts": list(dict.fromkeys(hosts)), "parents": list(dict.fromkeys(parents))}


def parse_openshift_route(r: dict[str, Any]) -> dict[str, Any]:
    """OpenShift Route → 名稱與接手的 router（位址要從 openshift-ingress 裡 router-<名稱> 的 Service 找）。"""
    spec, status = r.get("spec") or {}, r.get("status") or {}
    routers = [str(i.get("routerName")) for i in status.get("ingress") or [] if (i or {}).get("routerName")]
    return {"host": _host(spec.get("host")), "tls": bool(spec.get("tls")), "routers": list(dict.fromkeys(routers))}


def _mode_on(v: Any) -> bool:
    return str(v or "").strip().lower() not in ("", "never", "false", "off")


def parse_calico_pool(p: dict[str, Any]) -> dict[str, Any]:
    """Calico IPPool：allowedUses 有 LoadBalancer 的是 LB 位址集區，其他是 Pod 網段。
    不 NAT 也不封裝（ipipMode／vxlanMode 都是 Never）＝Pod 位址直接路由到實體網路（BGP 發佈）。"""
    meta, spec = p.get("metadata") or {}, p.get("spec") or {}
    uses = spec.get("allowedUses") or ["Workload", "Tunnel"]
    encap = "IPIP" if _mode_on(spec.get("ipipMode")) else ("VXLAN" if _mode_on(spec.get("vxlanMode")) else None)
    try:
        cidr = str(ipaddress.ip_network(str(spec.get("cidr") or "").strip(), strict=False))
    except ValueError:
        cidr = None
    blk = _block(cidr) if cidr else None
    return {"provider": "calico", "name": str(meta.get("name") or ""), "namespace": None, "cidr": cidr,
            "use": "loadbalancer" if "LoadBalancer" in uses else "pod",
            "routable": not bool(spec.get("natOutgoing")) and encap is None, "encapsulation": encap,
            "blocks": [blk] if blk else [], "auto_assign": True, "disabled": bool(spec.get("disabled"))}


def parse_whereabouts(p: dict[str, Any]) -> dict[str, Any]:
    """Whereabouts IPPool（Multus 次要網卡）：range 是實體網段上的集區；allocations 的 key 是位移，
    基準是 range 裡寫的那個位址（whereabouts 用 net.ParseCIDR 取的 IP，不一定是網路位址）。"""
    meta, spec = p.get("metadata") or {}, p.get("spec") or {}
    name = str(meta.get("name") or "")
    pool = {"provider": "whereabouts", "name": name, "namespace": meta.get("namespace"), "blocks": [],
            "auto_assign": True, "disabled": False, "use": "pod"}
    try:
        iface = ipaddress.ip_interface(str(spec.get("range") or "").strip())
    except ValueError:
        return {"pool": pool, "pods": []}
    pool["blocks"] = [{"start": str(iface.network[0]), "end": str(iface.network[-1])}]
    pods: list[dict[str, Any]] = []
    for off, a in (spec.get("allocations") or {}).items():
        try:
            ip = iface.ip + int(off)
        except (ValueError, TypeError, ipaddress.AddressValueError):
            continue
        ns, _, pod = str((a or {}).get("podref") or "").partition("/")
        if not pod:
            ns, pod = "", ns
        if pod and ip in iface.network:
            pods.append({"namespace": ns, "name": pod, "ip": str(ip)})
    pods.sort(key=lambda x: int(ipaddress.ip_address(x["ip"])))
    return {"pool": pool, "pods": pods}


def collect_hostnames(raw: dict[str, list[dict[str, Any]]], svc_ips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """各來源的名稱 → [{kind, namespace, name, host, tls, ip}]（一個名稱有幾個位址就幾列，沒有位址的 ip=None）。"""
    out: list[dict[str, Any]] = []

    def add(kind: str, meta: dict[str, Any], host: str, tls: bool, ips: list[str]) -> None:
        base = {"kind": kind, "namespace": str(meta.get("namespace") or "")[:253],
                "name": str(meta.get("name") or "")[:253], "host": host, "tls": tls}
        for ip in ips or [None]:
            out.append({**base, "ip": ip})

    for i in raw.get("ingresses") or []:
        for r in parse_ingress(i):
            add("ingress", i.get("metadata") or {}, r["host"], r["tls"], r["ips"])
    gw_ips: dict[tuple[str, str], list[str]] = {}
    for g in raw.get("gateways") or []:
        meta = g.get("metadata") or {}
        pg = parse_gateway(g)
        gw_ips[(str(meta.get("namespace") or ""), str(meta.get("name") or ""))] = pg["ips"]
        for h in pg["hostnames"]:
            add("gateway", meta, h["host"], h["tls"], pg["ips"])
    for kind in ("httproutes", "grpcroutes"):
        for r in raw.get(kind) or []:
            ref = parse_route_refs(r)
            ips = list(dict.fromkeys(ip for p in ref["parents"] for ip in gw_ips.get(p, [])))
            for h in ref["hosts"]:
                add(kind[:-1], r.get("metadata") or {}, h, False, ips)
    routers: dict[str, list[str]] = {}
    for s in svc_ips:
        if s["namespace"] == "openshift-ingress" and s["name"].startswith("router-"):
            routers.setdefault(s["name"][len("router-"):], []).append(s["ip"])
    for r in raw.get("routes") or []:
        pr = parse_openshift_route(r)
        if pr["host"]:
            ips = list(dict.fromkeys(ip for name in pr["routers"] for ip in routers.get(name, [])))
            add("route", r.get("metadata") or {}, pr["host"], pr["tls"], ips)
    seen: set[tuple[Any, ...]] = set()
    uniq = []
    for h in out:
        k = (h["kind"], h["namespace"], h["name"], h["host"], h["ip"])
        if k not in seen:
            seen.add(k)
            uniq.append(h)
    return uniq


# ─────────────────── 測試連線 ───────────────────

async def healthcheck(c: K8sCluster) -> dict[str, Any]:
    """版本＋逐項看讀不讀得到（節點、Service、集區、Service 網段），方便現場判斷少了哪條權限。"""
    async with _client(c, timeout=15.0) as client:
        ver = await _get(client, "/version")
        out: dict[str, Any] = {"version": (ver or {}).get("gitVersion"), "checks": []}

        async def check(key: str, paths: tuple[str, ...], optional: bool) -> None:
            try:
                if optional:
                    items = await _list_first(client, paths)
                    out["checks"].append({"key": key, "ok": None if items is None else True,
                                          "count": None if items is None else len(items)})
                else:
                    out["checks"].append({"key": key, "ok": True, "count": len(await _list(client, paths[0]))})
            except K8sError as exc:
                out["checks"].append({"key": key, "ok": False, "code": exc.code, "error": str(exc)})
        await check("nodes", ("/api/v1/nodes",), False)
        await check("services", ("/api/v1/services",), False)
        for provider, paths in POOL_SOURCES:
            await check(provider, paths, True)
        await check("servicecidrs", SERVICE_CIDR_PATHS, True)
        for key, paths in PHASE2_SOURCES:
            await check(key, paths, True)
    return out


# ─────────────────── 同步 ───────────────────

def _scope_ids(c: K8sCluster) -> set[Any]:
    out: set[Any] = set()
    for s in (c.scope_subnet_ids or []):
        with contextlib.suppress(ValueError, TypeError):
            out.add(uuid.UUID(str(s)))
    return out


def _provider_key(provider_id: str | None) -> str | None:
    """`openstack:///<uuid>`、`openstack://region/<uuid>`、`vsphere://<uuid>`、`proxmox://<叢集>/<vmid>` → 最後一段。"""
    if not provider_id or "://" not in provider_id:
        return None
    tail = provider_id.split("://", 1)[1].rstrip("/").rsplit("/", 1)[-1]
    return tail.lower() or None


async def _link_vms(session: AsyncSession, nodes: list[dict[str, Any]]) -> dict[str, uuid.UUID]:
    """節點 → 既有 VM：先用 providerID 對 VM 的外部識別碼（Nova server id、vSphere UUID、Proxmox VMID），
    對不到再用主要 IP 對 VM 的主要 IP。都要唯一才算。"""
    from app.models.virt import VirtualMachine
    keys = {n["name"]: _provider_key(n.get("provider_id")) for n in nodes}
    out: dict[str, uuid.UUID] = {}
    wanted = {k for k in keys.values() if k}
    if wanted:
        by_ext: dict[str, list[uuid.UUID]] = {}
        for vid, ext, vmid in (await session.execute(
                select(VirtualMachine.id, VirtualMachine.external_id, VirtualMachine.legacy_vmid))).all():
            for k in {str(ext).lower() if ext else None, str(vmid) if vmid is not None else None} - {None}:
                if k in wanted:
                    by_ext.setdefault(k, []).append(vid)
        for name, k in keys.items():
            hits = by_ext.get(k or "", [])
            if len(hits) == 1:
                out[name] = hits[0]
    addr_ids = {n["name"]: n.get("_addr_id") for n in nodes if n["name"] not in out and n.get("_addr_id")}
    if addr_ids:
        from app.core.sqlin import in_values
        by_ip: dict[uuid.UUID, list[uuid.UUID]] = {}
        for vid, ipid in (await session.execute(select(VirtualMachine.id, VirtualMachine.primary_ip_id).where(
                in_values(VirtualMachine.primary_ip_id, list(set(addr_ids.values())))))).all():
            by_ip.setdefault(ipid, []).append(vid)
        for name, ipid in addr_ids.items():
            hits = by_ip.get(ipid, [])
            if len(hits) == 1:
                out[name] = hits[0]
    return out


async def sync_cluster(session: AsyncSession, c: K8sCluster) -> dict[str, Any]:
    """讀節點、Service、集區、網段，更新鏡像表並對應 IPAM。**只標既有 IP、不新建**。"""
    async with _client(c, timeout=60.0) as client:
        ver = await _get(client, "/version")
        nodes_raw = await _list(client, "/api/v1/nodes")
        svcs_raw = await _list(client, "/api/v1/services")
        missing: list[str] = []
        failed: list[str] = []
        pools_raw: list[tuple[str, dict[str, Any]]] = []
        for provider, paths in POOL_SOURCES:
            for p in await _list_soft(client, paths, missing, failed) or []:
                pools_raw.append((provider, p))
        svc_cidrs_raw = await _list_soft(client, SERVICE_CIDR_PATHS, missing, failed) or []
        raw2 = {key: await _list_soft(client, paths, missing, failed) or [] for key, paths in PHASE2_SOURCES}

    now = datetime.now(UTC)
    scope = _scope_ids(c)
    nodes = [n for n in (parse_node(x) for x in nodes_raw) if n["name"]]
    svc_ips = [r for s in svcs_raw for r in parse_service_ips(s)]
    hostnames_found = collect_hostnames(raw2, svc_ips)
    pools = [p for p in (parse_pool(pv, x) for pv, x in pools_raw) if p["name"]]
    calico = [p for p in (parse_calico_pool(x) for x in raw2["calico"]) if p["name"] and p["cidr"]]
    pools += [p for p in calico if p["use"] == "loadbalancer"]
    for w in (parse_whereabouts(x) for x in raw2["whereabouts"]):
        if w["pool"]["name"] and w["pool"]["blocks"]:
            pools.append(w["pool"])
            svc_ips += [{"kind": "pod", "ports": None, **pod} for pod in w["pods"]]
    pools += [p for p in (parse_pool("antrea", x) for x in raw2["antrea_pools"]) if p["name"] and p["blocks"]]
    for nad in (parse_nad(x) for x in raw2["nads"]):
        if nad["pool"]:
            pools.append(nad["pool"])
        svc_ips += [{"kind": "pod", "ports": None, **a} for a in nad["addresses"]]
    for provider, key in (("ovn", "ovn_egress"), ("cilium", "cilium_egress"), ("antrea", "antrea_egress")):
        for x in raw2[key]:
            svc_ips += [{"kind": "egress", "namespace": "", "ports": None, **e} for e in parse_egress(provider, x)]
    api_ip = apiserver_ip(c.api_url)
    if api_ip:
        svc_ips.append({"kind": "apiserver", "namespace": "default", "name": "kubernetes", "ip": api_ip,
                        "ports": None})
    # 同一個位址從兩處讀到（例如 Antrea 的 spec 與 status）只留一筆
    uniq: dict[tuple[Any, ...], dict[str, Any]] = {}
    for s in svc_ips:
        uniq.setdefault((s["namespace"], s["name"], s["kind"], s["ip"]), s)
    svc_ips = list(uniq.values())

    matches = await match_existing_many(session, {n["ip"] for n in nodes if n["ip"]} | {r["ip"] for r in svc_ips}
                                        | {h["ip"] for h in hostnames_found if h["ip"]}, scope)
    from app.services.hostname_reports import HostnameRun, enabled_peers
    hn_run = HostnameRun(session, source="k8s", origin=f"k8s:{c.id}", peers=await enabled_peers(session, K8sCluster))

    linked = 0
    for n in nodes:
        ipa, _amb = matches.get(n["ip"], (None, False)) if n["ip"] else (None, False)
        n["_addr_id"] = ipa.id if ipa is not None else None
        if ipa is not None:
            linked += 1
            hn_run.report(ipa, n["name"])
            # 上線判定：Ready＝kubelet 心跳正常（節點控制器 40 秒沒心跳就改成 NotReady），是此刻可達的證據
            if n["ready"]:
                ipa.last_seen_k8s = now
    vm_of = await _link_vms(session, nodes)
    await hn_run.finish(complete=True)

    # ── 節點 ──
    have_nodes = {r.name: r for r in (await session.execute(
        select(K8sNode).where(K8sNode.cluster_id == c.id))).scalars()}
    for n in nodes:
        values = {k: v for k, v in n.items() if not k.startswith("_") and k != "name"}
        values.update(jt_ipam_address_id=n["_addr_id"], virtual_machine_id=vm_of.get(n["name"]), synced_at=now)
        row = have_nodes.pop(n["name"], None)
        if row is None:
            session.add(K8sNode(cluster_id=c.id, name=n["name"][:253], **values))
        else:
            for k, v in values.items():
                setattr(row, k, v)
    for row in have_nodes.values():
        await session.delete(row)

    # ── Service 佔用的位址 ──
    have_svc = {(r.namespace, r.name, r.kind, str(r.ip)): r for r in (await session.execute(
        select(K8sServiceIP).where(K8sServiceIP.cluster_id == c.id))).scalars()}
    for s in svc_ips:
        ipa, _amb = matches.get(s["ip"], (None, False))
        key = (s["namespace"], s["name"], s["kind"], s["ip"])
        row = have_svc.pop(key, None)
        if row is None:
            session.add(K8sServiceIP(cluster_id=c.id, namespace=s["namespace"][:253], name=s["name"][:253],
                                     kind=s["kind"], ip=s["ip"], ports=s["ports"], node=(s.get("node") or None),
                                     jt_ipam_address_id=ipa.id if ipa is not None else None, synced_at=now))
        else:
            row.ports, row.node, row.synced_at = s["ports"], s.get("node") or None, now
            row.jt_ipam_address_id = ipa.id if ipa is not None else None
    for row in have_svc.values():
        await session.delete(row)

    # ── 集區 ──
    have_pools = {(r.provider, r.name): r for r in (await session.execute(
        select(K8sIPPool).where(K8sIPPool.cluster_id == c.id))).scalars()}
    for p in pools:
        row = have_pools.pop((p["provider"], p["name"]), None)
        vals = {"namespace": p["namespace"], "blocks": p["blocks"], "auto_assign": p["auto_assign"],
                "disabled": p["disabled"], "use": p["use"], "synced_at": now}
        if row is None:
            session.add(K8sIPPool(cluster_id=c.id, provider=p["provider"], name=p["name"][:253], **vals))
        else:
            for k, v in vals.items():
                setattr(row, k, v)
    for row in have_pools.values():
        await session.delete(row)

    # ── 叢集網段（Calico 的 Workload 集區優先：它帶得出能不能路由）──
    want_cidrs: dict[tuple[str, str], dict[str, Any]] = {}
    for p in calico:
        if p["use"] == "pod":
            want_cidrs.setdefault(("pod", p["cidr"]), {"source": f"calico:{p['name']}", "routable": p["routable"],
                                                       "encapsulation": p["encapsulation"]})
    for n in nodes:
        for raw in n.get("pod_cidrs") or []:
            with contextlib.suppress(ValueError):
                want_cidrs.setdefault(("pod", str(ipaddress.ip_network(str(raw), strict=False))),
                                      {"source": n["name"], "routable": None, "encapsulation": None})
    for sc in svc_cidrs_raw:
        for raw in (sc.get("spec") or {}).get("cidrs") or []:
            with contextlib.suppress(ValueError):
                want_cidrs.setdefault(("service", str(ipaddress.ip_network(str(raw), strict=False))),
                                      {"source": str((sc.get("metadata") or {}).get("name") or ""),
                                       "routable": None, "encapsulation": None})
    have_cidrs = {(r.kind, str(r.cidr)): r for r in (await session.execute(
        select(K8sCidr).where(K8sCidr.cluster_id == c.id))).scalars()}
    for (kind, cidr), w in want_cidrs.items():
        vals = {"source": w["source"][:253] or None, "routable": w["routable"], "encapsulation": w["encapsulation"],
                "synced_at": now}
        row = have_cidrs.pop((kind, cidr), None)
        if row is None:
            session.add(K8sCidr(cluster_id=c.id, kind=kind, cidr=cidr, **vals))
        else:
            for k, v in vals.items():
                setattr(row, k, v)
    for row in have_cidrs.values():
        await session.delete(row)

    # ── 網域名稱 ──
    have_hn = {(r.kind, r.namespace, r.name, r.host, str(r.ip).split("/")[0] if r.ip else None): r
               for r in (await session.execute(select(K8sHostname).where(K8sHostname.cluster_id == c.id))).scalars()}
    for h in hostnames_found:
        ipa, _amb = matches.get(h["ip"], (None, False)) if h["ip"] else (None, False)
        row = have_hn.pop((h["kind"], h["namespace"], h["name"], h["host"], h["ip"]), None)
        if row is None:
            session.add(K8sHostname(cluster_id=c.id, kind=h["kind"], namespace=h["namespace"], name=h["name"],
                                    host=h["host"], ip=h["ip"], tls=h["tls"],
                                    jt_ipam_address_id=ipa.id if ipa is not None else None, synced_at=now))
        else:
            row.tls, row.synced_at = h["tls"], now
            row.jt_ipam_address_id = ipa.id if ipa is not None else None
    for row in have_hn.values():
        await session.delete(row)
    await session.flush()

    ranges = await sync_pool_ranges(session, c, pools if c.create_ip_ranges else [])
    c.server_version = ((ver or {}).get("gitVersion") or "")[:64] or None
    c.last_sync_at = now
    # 選用資源讀失敗：其他資料照常更新，但錯誤要看得到（叢集頁的錯誤欄）
    c.last_error = ("; ".join(failed))[:2000] or None
    c.missing_permissions = sorted(missing) or None
    return {"nodes": len(nodes), "nodes_linked": linked, "service_ips": len(svc_ips), "pools": len(pools),
            "cidrs": len(want_cidrs), "hostnames": len(hostnames_found), "missing_permissions": sorted(missing),
            "optional_errors": failed,
            **{f"ranges_{k}": v for k, v in ranges.items()}}


_RANGE_DESC = {"loadbalancer": "Kubernetes LoadBalancer 位址集區（唯讀，跟著叢集同步）",
               "pod": "Kubernetes Pod 次要網卡位址集區（Whereabouts，唯讀，跟著叢集同步）"}


def pool_label(c: K8sCluster, pool: dict[str, Any]) -> str:
    return f"K8s {c.name} · {pool['name']}"[:64]


async def sync_pool_ranges(session: AsyncSession, c: K8sCluster, pools: list[dict[str, Any]]) -> dict[str, int]:
    """LB 位址集區與 Whereabouts 集區 → 子網路的「位址範圍（集區）」（用途＝保留，來源 `k8s:<叢集>`）。

    規則同 DHCP 發放範圍（services/ip_ranges.sync_auto_dhcp_ranges）：整段落在**唯一**一個最精確的子網路裡才建；
    跟既有範圍重疊不建（範圍不可以重疊）；集區不見了、或關掉「建立位址範圍」就移除。手動建立的範圍一律不動。
    """
    from app.models.ip_range import IPRange
    from app.models.subnet import Subnet

    origin = f"k8s:{c.id}"[:64]
    out = {"created": 0, "removed": 0, "skipped_overlap": 0, "skipped_ambiguous": 0}
    nets: list[tuple[Any, uuid.UUID]] = []
    for sid, cidr in (await session.execute(select(Subnet.id, Subnet.cidr))).all():
        with contextlib.suppress(ValueError):
            nets.append((ipaddress.ip_network(str(cidr), strict=False), sid))
    desired: dict[tuple[uuid.UUID, str, str], str] = {}
    desc: dict[tuple[uuid.UUID, str, str], str] = {}
    for p in pools:
        for b in p["blocks"]:
            a, z = ipaddress.ip_address(b["start"]), ipaddress.ip_address(b["end"])
            if a.version != z.version or int(a) > int(z):
                continue
            hits = [(n, sid) for n, sid in nets if a in n and z in n]
            if not hits:
                continue
            best = max(n.prefixlen for n, _ in hits)
            top = [sid for n, sid in hits if n.prefixlen == best]
            if len(top) != 1:
                out["skipped_ambiguous"] += 1
                continue
            desired.setdefault((top[0], str(a), str(z)), pool_label(c, p))
            desc.setdefault((top[0], str(a), str(z)), _RANGE_DESC.get(p.get("use") or "loadbalancer", ""))

    for r in (await session.execute(select(IPRange).where(IPRange.source_origin == origin))).scalars().all():
        key = (r.subnet_id, str(r.start_ip).split("/")[0], str(r.end_ip).split("/")[0])
        if key in desired:
            r.name = desired.pop(key)
            r.description = desc.get(key, r.description)
        else:
            await session.delete(r)
            out["removed"] += 1
    await session.flush()
    for (subnet_id, start, end), label in desired.items():
        lo, hi = int(ipaddress.ip_address(start)), int(ipaddress.ip_address(end))
        clash = False
        for o in (await session.execute(select(IPRange).where(IPRange.subnet_id == subnet_id))).scalars().all():
            if lo <= int(ipaddress.ip_address(str(o.end_ip).split("/")[0])) and \
                    int(ipaddress.ip_address(str(o.start_ip).split("/")[0])) <= hi:
                clash = True
                break
        if clash:
            out["skipped_overlap"] += 1
            continue
        session.add(IPRange(subnet_id=subnet_id, start_ip=start, end_ip=end, purpose="reserved", name=label,
                            description=desc.get((subnet_id, start, end), _RANGE_DESC["loadbalancer"]),
                            source_origin=origin))
        await session.flush()
        out["created"] += 1
    return out


# ─────────────────── 衝突 ───────────────────

def _int(ip: str) -> int:
    return int(ipaddress.ip_address(str(ip).split("/")[0]))


async def dns_answers(session: AsyncSession, hosts: set[str]) -> dict[str, set[str]]:
    """IPAM 同步進來的 DNS 記錄裡，這些名稱解析到哪些位址（A／AAAA，CNAME 往下追三層）。查不到的名稱不在結果裡。

    名稱用同步時正規化好的 name_norm；舊資料還沒正規化的（NULL）當場補算。不做即時 DNS 查詢（同 FQDN 檢視）。
    """
    from sqlalchemy import or_

    from app.core.sqlin import in_values
    from app.models.dns import DNSRecord, DNSZone
    from app.services.dns_compare import normalize_name, normalize_value
    if not hosts:
        return {}
    addr: dict[str, set[str]] = {}
    cname: dict[str, str] = {}
    wanted, done = set(hosts), set()
    null_rows: list[Any] | None = None
    for _ in range(4):
        todo = wanted - done
        if not todo:
            break
        done |= todo
        rows = list((await session.execute(
            select(DNSRecord.name, DNSRecord.type, DNSRecord.value, DNSRecord.name_norm, DNSZone.name)
            .join(DNSZone, DNSZone.id == DNSRecord.zone_id)
            .where(DNSRecord.type.in_(("A", "AAAA", "CNAME")), in_values(DNSRecord.name_norm, sorted(todo))))).all())
        if null_rows is None:
            null_rows = list((await session.execute(
                select(DNSRecord.name, DNSRecord.type, DNSRecord.value, DNSRecord.name_norm, DNSZone.name)
                .join(DNSZone, DNSZone.id == DNSRecord.zone_id)
                .where(DNSRecord.type.in_(("A", "AAAA", "CNAME")),
                       or_(DNSRecord.name_norm.is_(None), DNSRecord.name_norm == "")))).all())
        for name, rtype, value, norm, zone in rows + null_rows:
            fq = norm or normalize_name(name, zone)
            if fq not in todo:
                continue
            v = normalize_value(rtype, value)
            if rtype == "CNAME":
                cname.setdefault(fq, v)
                wanted.add(v)
            elif _valid_ip(v):
                addr.setdefault(fq, set()).add(v)

    def resolve(h: str, depth: int = 0) -> set[str] | None:
        if h in addr:
            return addr[h]
        if h in cname and depth < 3:
            return resolve(cname[h], depth + 1)
        return None
    out: dict[str, set[str]] = {}
    for h in hosts:
        r = resolve(h)
        if r is not None:
            out[h] = r
    return out


def _dns_state(host: str, k8s_ips: set[str], dns: set[str] | None) -> str:
    if host.startswith("*."):
        return "wildcard"
    if not k8s_ips:
        return "no_ip"
    if dns is None:
        return "none"
    return "match" if dns & k8s_ips else "mismatch"


async def hostnames(session: AsyncSession, cluster_id: uuid.UUID) -> list[dict[str, Any]]:
    """叢集的網域名稱，附上 IPAM 同步的 DNS 怎麼解析（match／mismatch／none／wildcard／no_ip）。"""
    rows = (await session.execute(select(K8sHostname).where(K8sHostname.cluster_id == cluster_id)
                                  .order_by(K8sHostname.host, K8sHostname.kind, K8sHostname.name))).scalars().all()
    ips_of: dict[str, set[str]] = {}
    for r in rows:
        if r.ip:
            ips_of.setdefault(r.host, set()).add(str(r.ip).split("/")[0])
    dns = await dns_answers(session, {r.host for r in rows if not r.host.startswith("*.")})
    return [{
        "id": str(r.id), "kind": r.kind, "namespace": r.namespace, "name": r.name, "host": r.host,
        "ip": str(r.ip).split("/")[0] if r.ip else None, "tls": r.tls,
        "ip_address_id": str(r.jt_ipam_address_id) if r.jt_ipam_address_id else None,
        "dns_ips": sorted(dns.get(r.host, set()), key=_int),
        "dns_state": _dns_state(r.host, ips_of.get(r.host, set()), dns.get(r.host)),
        "synced_at": r.synced_at.isoformat() if r.synced_at else None,
    } for r in rows]


def effective_routable(row: K8sCidr, cluster_routable: bool) -> bool:
    """Calico 說得出來的照它；看不出來的 Pod 網段照叢集設定；Service 網段一律不可路由。"""
    if row.routable is not None:
        return bool(row.routable)
    return row.kind == "pod" and cluster_routable


async def _subnets(session: AsyncSession) -> list[tuple[Any, str, uuid.UUID]]:
    from app.models.subnet import Subnet
    out: list[tuple[Any, str, uuid.UUID]] = []
    for sid, cidr in (await session.execute(select(Subnet.id, Subnet.cidr).where(Subnet.archived_at.is_(None)))).all():
        with contextlib.suppress(ValueError):
            out.append((ipaddress.ip_network(str(cidr), strict=False), str(cidr), sid))
    return out


async def cidr_rows(session: AsyncSession, cluster_id: uuid.UUID) -> list[dict[str, Any]]:
    """叢集網段＋重疊的子網路：可路由的是「對應到哪個子網路」，不可路由的就是衝突。"""
    c = await session.get(K8sCluster, cluster_id)
    rows = (await session.execute(select(K8sCidr).where(K8sCidr.cluster_id == cluster_id)
                                  .order_by(K8sCidr.kind, K8sCidr.cidr))).scalars().all()
    subnets = await _subnets(session)
    out = []
    for r in rows:
        net = ipaddress.ip_network(str(r.cidr), strict=False)
        out.append({
            "id": str(r.id), "kind": r.kind, "cidr": str(r.cidr), "source": r.source, "routable": r.routable,
            "encapsulation": r.encapsulation,
            "effective_routable": effective_routable(r, bool(c and c.pod_network_routable)),
            "subnets": [{"id": str(sid), "cidr": label} for n, label, sid in subnets
                        if n.version == net.version and n.overlaps(net)],
            "synced_at": r.synced_at.isoformat() if r.synced_at else None,
        })
    return out


async def conflicts(session: AsyncSession, *, cluster_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    """K8s 佔用的位址跟 IPAM 的規劃打架的地方（異常偵測「Kubernetes 位址衝突」與叢集頁共用）。

    - unregistered：Service／Pod 佔用的位址落在 IPAM 管的子網路裡，IPAM 卻沒有登記（繞過 IPAM 直接拿走位址）
    - in_dhcp：佔用的位址在 DHCP 範圍裡（DHCP 隨時可能把它發給別台）
    - pool_dhcp：位址集區跟 DHCP 範圍重疊
    - pool_used：位址集區裡有 IPAM 登記為使用中、而且不是 K8s 自己在用的位址（MetalLB 可能把它發出去）
    - cidr_overlap：Pod／Service 網段跟 IPAM 的子網路重疊（路由會打架）；可直接路由的 Pod 網段是刻意對應，不算
    - dns_mismatch：Ingress／Gateway／Route 的名稱在 IPAM 同步的 DNS 裡指向別的位址（名稱連不到這個服務）
    - node_unregistered：節點的位址落在 IPAM 管的子網路裡，IPAM 卻沒有登記
    - cross_ip／cross_pool／cross_cidr：兩個叢集用了同一個位址、位址集區重疊、可路由的 Pod 網段重疊
      （各自看都沒問題，放在同一個網路上才會撞）。看單一叢集時列出跟它有關的，detail 寫另一邊；不分叢集時每對只列一次
    """
    from app.models.address import IPAddress
    from app.models.ip_range import IPRange

    clusters = {cid: (name, routable) for cid, name, routable in (await session.execute(
        select(K8sCluster.id, K8sCluster.name, K8sCluster.pod_network_routable))).all()}
    names = {cid: v[0] for cid, v in clusters.items()}

    def only(model: Any) -> list[Any]:
        return [] if cluster_id is None else [model.cluster_id == cluster_id]
    svc = (await session.execute(select(K8sServiceIP).where(*only(K8sServiceIP)))).scalars().all()
    pools = (await session.execute(select(K8sIPPool).where(*only(K8sIPPool)))).scalars().all()
    cidrs = (await session.execute(select(K8sCidr).where(*only(K8sCidr)))).scalars().all()
    hns = (await session.execute(select(K8sHostname).where(*only(K8sHostname), K8sHostname.ip.is_not(None))))\
        .scalars().all()
    subnets = [(n, label) for n, label, _sid in await _subnets(session)]
    dhcp = [(_int(r.start_ip), _int(r.end_ip), r.name) for r in (await session.execute(
        select(IPRange).where(IPRange.purpose == "dhcp"))).scalars().all()]

    out: list[dict[str, Any]] = []
    k8s_ips = {str(s.ip).split("/")[0] for s in svc}
    for s in svc:
        ip = str(s.ip).split("/")[0]
        what = f"{s.namespace}/{s.name}"
        base = {"cluster": names.get(s.cluster_id), "cluster_id": str(s.cluster_id), "ip": ip, "what": what,
                "ip_address_id": str(s.jt_ipam_address_id) if s.jt_ipam_address_id else None}
        if s.jt_ipam_address_id is None:
            addr = ipaddress.ip_address(ip)
            hit = [c for n, c in subnets if addr in n]
            if hit:
                out.append({**base, "kind": "unregistered", "detail": hit[0]})
        v = _int(ip)
        for lo, hi, label in dhcp:
            if lo <= v <= hi:
                out.append({**base, "kind": "in_dhcp", "detail": label})
                break
    used = [(str(h), str(i)) for h, i in (await session.execute(
        select(func.host(IPAddress.ip), IPAddress.id).where(IPAddress.state.in_(("used", "reserved"))))).all()]
    for p in pools:
        if p.disabled:
            continue
        for b in p.blocks or []:
            lo, hi = _int(b["start"]), _int(b["end"])
            base = {"cluster": names.get(p.cluster_id), "cluster_id": str(p.cluster_id),
                    "ip": f"{b['start']}-{b['end']}", "what": f"{p.provider}:{p.name}"}
            for dlo, dhi, label in dhcp:
                if lo <= dhi and dlo <= hi:
                    out.append({**base, "kind": "pool_dhcp", "detail": label})
            taken = [h for h, _i in used if h not in k8s_ips and lo <= _int(h) <= hi]
            if taken:
                out.append({**base, "kind": "pool_used", "detail": "、".join(sorted(taken, key=_int)[:20]),
                            "count": len(taken)})
    for c in cidrs:
        if effective_routable(c, bool(clusters.get(c.cluster_id, (None, False))[1])):
            continue
        net = ipaddress.ip_network(str(c.cidr), strict=False)
        for n, label in subnets:
            if n.version == net.version and n.overlaps(net):
                out.append({"cluster": names.get(c.cluster_id), "cluster_id": str(c.cluster_id), "ip": str(net),
                            "what": f"{c.kind}:{c.source or ''}".rstrip(":"), "kind": "cidr_overlap", "detail": label})
    nodes = (await session.execute(select(K8sNode).where(*only(K8sNode), K8sNode.ip.is_not(None),
                                                         K8sNode.jt_ipam_address_id.is_(None)))).scalars().all()
    for nd in nodes:
        addr = ipaddress.ip_address(str(nd.ip).split("/")[0])
        hit = [c for n, c in subnets if addr in n]
        if hit:
            out.append({"cluster": names.get(nd.cluster_id), "cluster_id": str(nd.cluster_id), "ip": str(addr),
                        "what": nd.name, "kind": "node_unregistered", "detail": hit[0]})
    if len(clusters) > 1:
        out += await _cross_cluster(session, clusters, cluster_id)
    by_host: dict[tuple[Any, str], list[Any]] = {}
    for h in hns:
        if not h.host.startswith("*."):
            by_host.setdefault((h.cluster_id, h.host), []).append(h)
    dns = await dns_answers(session, {host for _cid, host in by_host})
    for (cid, host), rows in sorted(by_host.items(), key=lambda kv: kv[0][1]):
        ips = {str(r.ip).split("/")[0] for r in rows}
        if _dns_state(host, ips, dns.get(host)) != "mismatch":
            continue
        first = min(rows, key=lambda r: _int(str(r.ip)))
        out.append({"cluster": names.get(cid), "cluster_id": str(cid), "ip": str(first.ip).split("/")[0],
                    "what": host, "kind": "dns_mismatch", "detail": "、".join(sorted(dns[host], key=_int)),
                    "ip_address_id": str(first.jt_ipam_address_id) if first.jt_ipam_address_id else None})
    return out


async def _cross_cluster(session: AsyncSession, clusters: dict[Any, tuple[str, bool]],
                         cluster_id: uuid.UUID | None) -> list[dict[str, Any]]:
    """兩個叢集之間的衝突（見 conflicts）。"""
    names = {cid: v[0] for cid, v in clusters.items()}
    out: list[dict[str, Any]] = []

    def emit(kind: str, ip: str, s1: tuple[Any, str], s2: tuple[Any, str]) -> None:
        if cluster_id is None:
            me, other = sorted((s1, s2), key=lambda s: (names.get(s[0]) or "", s[1]))
        elif s1[0] == cluster_id:
            me, other = s1, s2
        elif s2[0] == cluster_id:
            me, other = s2, s1
        else:
            return
        out.append({"cluster": names.get(me[0]), "cluster_id": str(me[0]), "ip": ip, "what": me[1],
                    "kind": kind, "detail": f"{names.get(other[0])}: {other[1]}"})

    def pairs(items: list[tuple[Any, str]]) -> list[tuple[tuple[Any, str], tuple[Any, str]]]:
        return [(x, y) for i, x in enumerate(items) for y in items[i + 1:] if x[0] != y[0]]

    by_ip: dict[str, list[tuple[Any, str]]] = {}
    for s in (await session.execute(select(K8sServiceIP))).scalars().all():
        by_ip.setdefault(str(s.ip).split("/")[0], []).append(
            (s.cluster_id, f"{s.namespace}/{s.name}" if s.namespace else s.name))
    for n in (await session.execute(select(K8sNode).where(K8sNode.ip.is_not(None)))).scalars().all():
        by_ip.setdefault(str(n.ip).split("/")[0], []).append((n.cluster_id, f"node {n.name}"))
    for ip, items in sorted(by_ip.items(), key=lambda kv: _int(kv[0])):
        seen: set[tuple[Any, Any]] = set()
        for x, y in pairs(items):
            if (x[0], y[0]) not in seen:          # 同一對叢集、同一個位址只算一次
                seen.add((x[0], y[0]))
                emit("cross_ip", ip, x, y)
    blocks: list[tuple[Any, str, int, int, int]] = []
    for p in (await session.execute(select(K8sIPPool).where(K8sIPPool.disabled.is_(False)))).scalars().all():
        for b in p.blocks or []:
            lo, hi = ipaddress.ip_address(b["start"]), ipaddress.ip_address(b["end"])
            blocks.append((p.cluster_id, f"{p.provider}:{p.name}", lo.version, int(lo), int(hi)))
    for i, x in enumerate(blocks):
        for y in blocks[i + 1:]:
            if x[0] != y[0] and x[2] == y[2] and x[3] <= y[4] and y[3] <= x[4]:
                lo, hi = max(x[3], y[3]), min(x[4], y[4])
                rng = f"{ipaddress.ip_address(lo)}-{ipaddress.ip_address(hi)}"
                emit("cross_pool", rng, (x[0], x[1]), (y[0], y[1]))
    nets: list[tuple[Any, str, Any]] = []
    for r in (await session.execute(select(K8sCidr).where(K8sCidr.kind == "pod"))).scalars().all():
        if effective_routable(r, bool(clusters.get(r.cluster_id, (None, False))[1])):
            nets.append((r.cluster_id, f"pod:{r.source or ''}".rstrip(":"), ipaddress.ip_network(str(r.cidr), strict=False)))
    for i, x in enumerate(nets):
        for y in nets[i + 1:]:
            if x[0] != y[0] and x[2].version == y[2].version and x[2].overlaps(y[2]):
                small = x[2] if x[2].prefixlen >= y[2].prefixlen else y[2]
                emit("cross_cidr", str(small), (x[0], x[1]), (y[0], y[1]))
    return out


# ─────────────────── 單一 IP ───────────────────

async def by_ip(session: AsyncSession, ip_id: uuid.UUID) -> dict[str, Any]:
    """IP 詳細頁的卡片與 AI 的 get_ip_detail 共用（呼叫端要先確認對這筆 IP 有讀取權）。"""
    from app.models.address import IPAddress

    def iso(v: Any) -> str | None:
        return v.isoformat() if v else None
    clusters = {cid: (name, routable) for cid, name, routable in (await session.execute(
        select(K8sCluster.id, K8sCluster.name, K8sCluster.pod_network_routable))).all()}
    names = {cid: v[0] for cid, v in clusters.items()}
    nodes = (await session.execute(select(K8sNode).where(K8sNode.jt_ipam_address_id == ip_id))).scalars().all()
    svcs = (await session.execute(select(K8sServiceIP).where(K8sServiceIP.jt_ipam_address_id == ip_id))).scalars().all()
    hns = (await session.execute(select(K8sHostname).where(K8sHostname.jt_ipam_address_id == ip_id)
                                 .order_by(K8sHostname.host))).scalars().all()
    pod_networks: list[dict[str, Any]] = []
    ip_text = await session.scalar(select(func.host(IPAddress.ip)).where(IPAddress.id == ip_id))
    if ip_text and not nodes and not svcs:
        addr = ipaddress.ip_address(str(ip_text))
        for r in (await session.execute(select(K8sCidr).where(K8sCidr.kind == "pod"))).scalars().all():
            net = ipaddress.ip_network(str(r.cidr), strict=False)
            if net.version == addr.version and addr in net \
                    and effective_routable(r, bool(clusters.get(r.cluster_id, (None, False))[1])):
                pod_networks.append({"cluster": names.get(r.cluster_id), "cluster_id": str(r.cluster_id),
                                     "cidr": str(net), "source": r.source})
    return {
        "nodes": [{"cluster": names.get(n.cluster_id), "cluster_id": str(n.cluster_id), "name": n.name,
                   "roles": n.roles, "ready": n.ready, "kubelet_version": n.kubelet_version,
                   "synced_at": iso(n.synced_at)} for n in nodes],
        "services": [{"cluster": names.get(s.cluster_id), "cluster_id": str(s.cluster_id), "namespace": s.namespace,
                      "name": s.name, "kind": s.kind, "ports": s.ports, "node": s.node,
                      "synced_at": iso(s.synced_at)}
                     for s in svcs],
        "hostnames": [{"cluster": names.get(h.cluster_id), "cluster_id": str(h.cluster_id), "kind": h.kind,
                       "namespace": h.namespace, "name": h.name, "host": h.host, "tls": h.tls,
                       "synced_at": iso(h.synced_at)} for h in hns],
        "pod_networks": pod_networks,
    }
