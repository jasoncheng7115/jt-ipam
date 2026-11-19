"""Kubernetes 整合：對真的叢集跑（k3s 最新版＋MetalLB、kind 上游舊一個 minor＋Cilium LB 集區）。

沒有設定測試靶時整支略過。發版前要跑（TEST_CHECKLIST「Kubernetes 真機」）：

    bash scripts/k8s-test-target/run.sh       # 起兩個叢集、建唯讀 ServiceAccount，印出下一行
    JTIPAM_TEST_K8S=/tmp/jtipam-k8s OUTBOUND_ALLOW_CIDRS=<兩個 API 的 IP>/32,… pytest tests/test_k8s_real.py

帳號是 deploy/k8s/jt-ipam-readonly.yaml 建的 jt-ipam-ro，就是管理頁給客戶的那一份。
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from app.models.k8s import K8sCluster
from app.services import k8s as svc

pytestmark = pytest.mark.anyio
DIR = Path(os.environ.get("JTIPAM_TEST_K8S", ""))
needs = pytest.mark.skipif(not os.environ.get("JTIPAM_TEST_K8S"), reason="JTIPAM_TEST_K8S not set")
TARGETS = ["k3s", "kind"]


def _cluster(name: str, *, token: str | None = None, ca: bool = True) -> K8sCluster:
    c = K8sCluster(id=uuid.uuid4(), name=name, api_url=(DIR / f"{name}.url").read_text().strip(),
                   ca_cert=(DIR / f"{name}.ca").read_text() if ca else None, verify_tls=True, auth_method="token")
    c.token_enc, c.token_nonce = svc.encrypt_field(c.id, "token", token or (DIR / f"{name}.token").read_text().strip())
    return c


@needs
@pytest.mark.parametrize("name", TARGETS)
async def test_readonly_account_reads_everything_it_needs(name: str) -> None:
    hc = await svc.healthcheck(_cluster(name))
    assert hc["version"].startswith("v1.")
    checks = {c["key"]: c for c in hc["checks"]}
    assert checks["nodes"]["ok"] is True
    assert checks["nodes"]["count"] >= 1
    assert checks["services"]["ok"] is True
    if name == "k3s":
        assert checks["metallb"] == {"key": "metallb", "ok": True, "count": 1}
        assert checks["cilium"]["ok"] is None             # 沒裝 → 不算錯
    else:
        assert checks["cilium"] == {"key": "cilium", "ok": True, "count": 1}
        assert checks["metallb"]["ok"] is None


@needs
@pytest.mark.parametrize("name", TARGETS)
async def test_lists_parse_into_what_ipam_cares_about(name: str) -> None:
    c = _cluster(name)
    async with svc._client(c) as client:
        nodes = [svc.parse_node(n) for n in await svc._list(client, "/api/v1/nodes")]
        svcs = [r for s in await svc._list(client, "/api/v1/services") for r in svc.parse_service_ips(s)]
        pools = [svc.parse_pool(p, x) for p, paths in svc.POOL_SOURCES for x in (await svc._list_first(client, paths) or [])]
    api_ip = (DIR / f"{name}.url").read_text().split("//")[1].split(":")[0]
    assert any(n["ip"] == api_ip and n["ready"] for n in nodes)
    assert all(n["kubelet_version"] for n in nodes)
    assert {"namespace": "jt-demo", "name": "legacy", "kind": "external", "ip": "192.0.2.50",
            "ports": [{"name": "https", "port": 443, "protocol": "TCP"}]} in svcs
    if name == "k3s":
        lb = [s for s in svcs if s["kind"] == "loadbalancer"]
        assert lb and lb[0]["ip"].startswith("192.0.2.2")     # MetalLB 從集區發的
        assert pools[0]["blocks"] == [{"start": "192.0.2.200", "end": "192.0.2.220"},
                                      {"start": "198.51.100.64", "end": "198.51.100.67"}]
    else:
        assert pools[0]["provider"] == "cilium"
        assert pools[0]["blocks"] == [{"start": "203.0.113.0", "end": "203.0.113.15"},
                                      {"start": "203.0.113.100", "end": "203.0.113.110"}]


@needs
@pytest.mark.parametrize("name", TARGETS)
async def test_readonly_account_cannot_write(name: str) -> None:
    """唯讀帳號：寫入一律被拒（403）。"""
    c = _cluster(name)
    async with svc._client(c) as client:
        r = await client.delete("/api/v1/namespaces/jt-demo/services/legacy")
        assert r.status_code == 403
        r = await client.post("/api/v1/namespaces", json={"metadata": {"name": "nope"}})
        assert r.status_code == 403


@needs
async def test_wrong_token_and_wrong_ca() -> None:
    with pytest.raises(svc.K8sError) as e:
        await svc.healthcheck(_cluster("k3s", token="not-a-token"))
    assert e.value.code == "k8s_auth"
    wrong = _cluster("k3s")
    wrong.ca_cert = (DIR / "kind.ca").read_text()            # 別的叢集的 CA
    with pytest.raises(svc.K8sError) as e:
        await svc.healthcheck(wrong)
    assert e.value.code == "k8s_tls_failed"


# ─────────────────── 第二階段（2026-11-19）───────────────────

async def _raw2(c: K8sCluster) -> dict[str, list]:
    missing: list[str] = []
    async with svc._client(c) as client:
        raw = {key: await svc._list_soft(client, paths, missing) or [] for key, paths in svc.PHASE2_SOURCES}
        svcs = [r for s in await svc._list(client, "/api/v1/services") for r in svc.parse_service_ips(s)]
    raw["_svc_ips"] = svcs
    raw["_missing"] = missing
    return raw


@needs
async def test_k3s_traefik_writes_the_metallb_address_into_the_ingress() -> None:
    """真的 Ingress 控制器：k3s 內建 Traefik 的 Service 從 MetalLB 拿位址，再寫回 Ingress 的 status。"""
    raw = await _raw2(_cluster("k3s"))
    assert raw["_missing"] == []
    traefik = next(s["ip"] for s in raw["_svc_ips"] if s["name"] == "traefik" and s["kind"] == "loadbalancer")
    rows = {(h["kind"], h["host"]): h for h in svc.collect_hostnames(raw, raw["_svc_ips"])}
    assert rows[("ingress", "shop.example.com")]["ip"] == traefik
    assert rows[("ingress", "shop.example.com")]["tls"] is True
    assert rows[("ingress", "api.example.com")]["tls"] is False


@needs
async def test_kind_gateway_api_calico_whereabouts_and_openshift_routes() -> None:
    """上游的 CRD（Gateway API v1.3、Calico、Whereabouts、OpenShift Route），物件照客戶會寫的樣子建。"""
    c = _cluster("kind")
    hc = {x["key"]: x for x in (await svc.healthcheck(c))["checks"]}
    for key, n in (("gateways", 1), ("httproutes", 1), ("grpcroutes", 1), ("routes", 1), ("calico", 3),
                   ("whereabouts", 1)):
        assert hc[key] == {"key": key, "ok": True, "count": n}, hc[key]
    assert hc["ingresses"]["ok"] is True
    raw = await _raw2(c)
    got = {(h["kind"], h["namespace"], h["host"], h["ip"], h["tls"]) for h in svc.collect_hostnames(raw, raw["_svc_ips"])}
    assert got == {
        ("gateway", "infra", "*.apps.example.com", "203.0.113.201", True),
        ("httproute", "web", "blog.apps.example.com", "203.0.113.201", False),
        ("grpcroute", "infra", "rpc.apps.example.com", "203.0.113.201", False),
        ("route", "openshift-console", "console.apps.example.com", "203.0.113.202", True),
    }
    cal = {p["name"]: p for p in (svc.parse_calico_pool(x) for x in raw["calico"])}
    assert (cal["default-ipv4-ippool"]["routable"], cal["default-ipv4-ippool"]["encapsulation"]) == (False, "VXLAN")
    assert (cal["routable"]["routable"], cal["routable"]["use"]) == (True, "pod")
    assert (cal["lb"]["use"], cal["lb"]["blocks"]) == ("loadbalancer", [{"start": "203.0.113.224", "end": "203.0.113.239"}])
    wa = svc.parse_whereabouts(raw["whereabouts"][0])
    assert wa["pods"] == [{"namespace": "jt-demo", "name": "db-0", "ip": "203.0.113.97"},
                          {"namespace": "jt-demo", "name": "db-1", "ip": "203.0.113.99"}]


@needs
async def test_phase1_readonly_account_keeps_working() -> None:
    """客戶還沒重新套用 manifest：新資源回 403，同步照跑，列出 ClusterRole 要補的那幾條。"""
    c = _cluster("k3s", token=(DIR / "k3s-phase1.token").read_text().strip())
    raw = await _raw2(c)
    # 沒權限時沒裝的資源也是 403；靠 discovery 分辨，k3s 沒裝的不可以列成缺權限（k3s 有 Gateway API 的 CRD）
    assert set(raw["_missing"]) <= {"ingresses.networking.k8s.io", "gateways.gateway.networking.k8s.io",
                                    "httproutes.gateway.networking.k8s.io", "grpcroutes.gateway.networking.k8s.io"}
    assert raw["_missing"].count("ingresses.networking.k8s.io") == 1
    assert raw["ingresses"] == []
    hc = {x["key"]: x for x in (await svc.healthcheck(c))["checks"]}
    assert hc["nodes"]["ok"] is True
    assert (hc["ingresses"]["ok"], hc["ingresses"]["code"]) == (False, "k8s_forbidden")


@needs
async def test_kind_egress_ips_and_multus() -> None:
    """第三階段：上游 CRD（OVN-Kubernetes EgressIP、Cilium egress gateway、Antrea Egress / ExternalIPPool、Multus NAD）。"""
    c = _cluster("kind")
    hc = {x["key"]: x for x in (await svc.healthcheck(c))["checks"]}
    for key in ("ovn_egress", "cilium_egress", "antrea_egress", "antrea_pools"):
        assert hc[key] == {"key": key, "ok": True, "count": 1}, hc[key]
    assert hc["nads"] == {"key": "nads", "ok": True, "count": 2}
    raw = await _raw2(c)
    eg = {(e["name"], e["ip"], e["node"]) for p, k in (("ovn", "ovn_egress"), ("cilium", "cilium_egress"),
                                                      ("antrea", "antrea_egress")) for x in raw[k]
          for e in svc.parse_egress(p, x)}
    assert eg == {("egress-prod", "203.0.113.150", "jtipam-control-plane"), ("egress-prod", "203.0.113.151", None),
                  ("to-internet", "203.0.113.152", None), ("web-egress", "203.0.113.154", "jtipam-control-plane")}
    pool = svc.parse_pool("antrea", raw["antrea_pools"][0])
    assert pool["blocks"] == [{"start": "203.0.113.160", "end": "203.0.113.169"},
                              {"start": "203.0.113.176", "end": "203.0.113.183"}]
    nads = {n["metadata"]["name"]: svc.parse_nad(n) for n in raw["nads"]}
    assert nads["storage"]["pool"]["blocks"] == [{"start": "203.0.113.66", "end": "203.0.113.78"}]
    assert nads["legacy-db"]["addresses"] == [{"namespace": "jt-demo", "name": "legacy-db", "ip": "203.0.113.90"}]
    assert svc.apiserver_ip(c.api_url) == (DIR / "kind.url").read_text().split("//")[1].split(":")[0]
