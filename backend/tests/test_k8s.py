"""Kubernetes 整合（2026-11-18）：kubeconfig、解析、同步對應、位址集區轉位址範圍、衝突、權限。

回應的形狀照 k3s 與 kind 實機抓下來的；真的打叢集的在 tests/test_k8s_real.py。
"""
from __future__ import annotations

import base64
import contextlib
import uuid

from typing import Any

import pytest
from sqlalchemy import select

from app.models.address import IPAddress
from app.models.ip_range import IPRange
from app.models.k8s import K8sCidr, K8sCluster, K8sIPPool, K8sNode, K8sServiceIP
from app.models.section import Section
from app.models.subnet import Subnet
from app.services import k8s as svc


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


KUBECONFIG = f"""apiVersion: v1
kind: Config
current-context: prod
clusters:
- name: prod
  cluster: {{server: "https://192.0.2.10:6443/", certificate-authority-data: {_b64("CA-PEM")}}}
- name: lab
  cluster: {{server: "https://192.0.2.20:6443", insecure-skip-tls-verify: true}}
users:
- name: ro
  user: {{token: "tok-123"}}
- name: admin
  user: {{client-certificate-data: {_b64("CERT")}, client-key-data: {_b64("KEY")}}}
contexts:
- name: prod
  context: {{cluster: prod, user: ro}}
- name: lab
  context: {{cluster: lab, user: admin}}
"""


def test_kubeconfig_token_and_client_cert() -> None:
    assert svc.parse_kubeconfig(KUBECONFIG) == {
        "api_url": "https://192.0.2.10:6443", "ca_cert": "CA-PEM", "verify_tls": True,
        "auth_method": "token", "token": "tok-123"}
    lab = svc.parse_kubeconfig(KUBECONFIG, "lab")
    assert (lab["auth_method"], lab["client_cert"], lab["client_key"], lab["verify_tls"]) == \
        ("client_cert", "CERT", "KEY", False)


@pytest.mark.parametrize(("user", "code"), [
    ('{exec: {command: aws, args: [eks, get-token]}}', "k8s_kubeconfig_exec"),
    ('{auth-provider: {name: gcp}}', "k8s_kubeconfig_exec"),
    ('{client-certificate: /home/x/cert.pem, client-key: /home/x/key.pem}', "k8s_kubeconfig_files"),
    ('{username: admin}', "k8s_kubeconfig_no_credentials"),
])
def test_kubeconfig_that_cannot_work_on_the_server(user: str, code: str) -> None:
    kc = f"""clusters: [{{name: c, cluster: {{server: "https://192.0.2.1:6443"}}}}]
users: [{{name: u, user: {user}}}]
contexts: [{{name: x, context: {{cluster: c, user: u}}}}]
current-context: x"""
    with pytest.raises(svc.K8sError) as e:
        svc.parse_kubeconfig(kc)
    assert e.value.code == code
    with pytest.raises(svc.K8sError):
        svc.parse_kubeconfig("not: [a kubeconfig")


NODE = {
    "metadata": {"name": "worker-1", "labels": {"node-role.kubernetes.io/worker": "", "kubernetes.io/os": "linux"}},
    "spec": {"podCIDR": "10.42.1.0/24", "podCIDRs": ["10.42.1.0/24"], "providerID": "openstack:///3f1c6b0e-aaaa-bbbb-cccc-1234567890ab"},
    "status": {"addresses": [{"type": "Hostname", "address": "worker-1"}, {"type": "ExternalIP", "address": "203.0.113.9"},
                             {"type": "InternalIP", "address": "198.51.100.21"}],
               "conditions": [{"type": "Ready", "status": "True", "lastHeartbeatTime": "2026-11-18T03:00:00Z"}],
               "nodeInfo": {"kubeletVersion": "v1.34.1+k3s1", "osImage": "Ubuntu 24.04.3 LTS", "kernelVersion": "6.8.0",
                            "containerRuntimeVersion": "containerd://2.1.4"}},
}
SVC_LB = {"metadata": {"name": "web", "namespace": "shop"},
          "spec": {"type": "LoadBalancer", "ports": [{"name": "http", "port": 80, "protocol": "TCP"}]},
          "status": {"loadBalancer": {"ingress": [{"ip": "198.51.100.200", "ipMode": "VIP"}]}}}
SVC_EXT = {"metadata": {"name": "legacy", "namespace": "shop"},
           "spec": {"type": "ClusterIP", "externalIPs": ["198.51.100.50", "not-an-ip"], "ports": [{"port": 443}]}}
SVC_PENDING = {"metadata": {"name": "pending", "namespace": "shop"}, "spec": {"type": "LoadBalancer"}, "status": {}}
METALLB = {"metadata": {"name": "lan", "namespace": "metallb-system"},
           "spec": {"addresses": ["198.51.100.200-198.51.100.210", "198.51.100.240/30"], "autoAssign": False}}
CILIUM = {"metadata": {"name": "cil"}, "spec": {"blocks": [{"cidr": "203.0.113.0/28"}, {"start": "203.0.113.100", "stop": "203.0.113.110"}],
                                               "disabled": True}}
SVC_CIDR = {"metadata": {"name": "kubernetes"}, "spec": {"cidrs": ["10.43.0.0/16"]}}


def test_parsing() -> None:
    n = svc.parse_node(NODE)
    assert (n["ip"], n["roles"], n["ready"], n["kubelet_version"], n["pod_cidrs"]) == \
        ("198.51.100.21", ["worker"], True, "v1.34.1+k3s1", ["10.42.1.0/24"])
    assert svc.parse_service_ips(SVC_LB)[0] | {} == {"namespace": "shop", "name": "web", "kind": "loadbalancer",
                                                      "ip": "198.51.100.200", "ports": [{"name": "http", "port": 80, "protocol": "TCP"}]}
    assert [r["ip"] for r in svc.parse_service_ips(SVC_EXT)] == ["198.51.100.50"]
    assert svc.parse_service_ips(SVC_PENDING) == []
    m = svc.parse_pool("metallb", METALLB)
    assert m["blocks"] == [{"start": "198.51.100.200", "end": "198.51.100.210"},
                           {"start": "198.51.100.240", "end": "198.51.100.243"}]
    assert m["auto_assign"] is False
    c = svc.parse_pool("cilium", CILIUM)
    assert c["blocks"][1] == {"start": "203.0.113.100", "end": "203.0.113.110"}
    assert c["disabled"] is True
    assert svc._provider_key("openstack:///3F1C6B0E-AAAA-BBBB-CCCC-1234567890AB") == "3f1c6b0e-aaaa-bbbb-cccc-1234567890ab"
    assert svc._provider_key("proxmox://pve1/105") == "105"
    assert svc._provider_key("k3s://worker-1") == "worker-1"


def _fake_api(monkeypatch, nodes: list[Any], services: list[Any], metallb: list[Any] | None,
              cilium: list[Any] | None, cidrs: list[Any] | None, extra: dict[str, Any] | None = None) -> None:
    """extra：路徑片段 → 清單、None（沒裝）或 "forbidden"（唯讀帳號是第一階段的舊版，沒有這條權限）。"""
    @contextlib.asynccontextmanager
    async def client(_c: Any, timeout: float = 30.0):  # noqa: ARG001
        yield object()

    async def get(_client: Any, path: str, *_a: Any, **_k: Any) -> Any:
        assert path == "/version"
        return {"gitVersion": "v1.34.1"}

    async def lst(_client: Any, path: str, **_k: Any) -> list[Any]:
        return {"/api/v1/nodes": nodes, "/api/v1/services": services}[path]

    async def first(_client: Any, paths: tuple[str, ...]) -> list[Any] | None:
        for frag, val in (extra or {}).items():
            if frag in paths[0]:
                if val == "forbidden":
                    raise svc.K8sError("403", code="k8s_forbidden", path=paths[0])
                return val
        if "metallb" in paths[0]:
            return metallb
        if "cilium" in paths[0]:
            return cilium
        if "servicecidrs" in paths[0]:
            return cidrs
        return None
    monkeypatch.setattr(svc, "_client", client)
    monkeypatch.setattr(svc, "_get", get)
    monkeypatch.setattr(svc, "_list", lst)
    monkeypatch.setattr(svc, "_list_first", first)


async def _site(session) -> tuple[K8sCluster, Subnet, list[IPAddress]]:
    sec = Section(name=f"k8s-{uuid.uuid4().hex[:6]}")
    session.add(sec)
    await session.flush()
    sub = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    session.add(sub)
    await session.flush()
    ips = [IPAddress(subnet_id=sub.id, ip="198.51.100.21", state="used"),
           IPAddress(subnet_id=sub.id, ip="198.51.100.200", state="used"),
           IPAddress(subnet_id=sub.id, ip="198.51.100.205", state="used", hostname="printer")]
    session.add_all(ips)
    c = K8sCluster(name=f"prod-{uuid.uuid4().hex[:6]}", api_url="https://192.0.2.10:6443", auth_method="token",
                   scope_subnet_ids=[str(sub.id)])
    session.add(c)
    await session.flush()
    return c, sub, ips


@pytest.mark.anyio
async def test_sync_maps_nodes_services_pools_and_networks(db_session, monkeypatch) -> None:
    from app.models.virt import VirtCluster, VirtualMachine
    c, sub, ips = await _site(db_session)
    vc = VirtCluster(name=f"os-{uuid.uuid4().hex[:4]}", type="openstack")
    db_session.add(vc)
    await db_session.flush()
    vm = VirtualMachine(cluster_id=vc.id, name="worker-1", external_id="3f1c6b0e-aaaa-bbbb-cccc-1234567890ab")
    db_session.add(vm)
    await db_session.flush()
    _fake_api(monkeypatch, [NODE], [SVC_LB, SVC_EXT, SVC_PENDING], [METALLB], [CILIUM], [SVC_CIDR])

    out = await svc.sync_cluster(db_session, c)
    await db_session.flush()
    # service_ips：LB＋externalIPs，再加上 API 位址本身（https://192.0.2.10:6443）
    assert out["nodes"] == 1 and out["nodes_linked"] == 1 and out["service_ips"] == 3 and out["pools"] == 2
    node = (await db_session.execute(select(K8sNode).where(K8sNode.cluster_id == c.id))).scalar_one()
    assert node.jt_ipam_address_id == ips[0].id
    assert node.virtual_machine_id == vm.id, "providerID 連到 OpenStack 的 VM"
    await db_session.refresh(ips[0])
    assert ips[0].last_seen_k8s is not None, "Ready 的節點是上線證據"
    svcs = {(s.kind, str(s.ip).split("/")[0]): s for s in (await db_session.execute(
        select(K8sServiceIP).where(K8sServiceIP.cluster_id == c.id))).scalars()}
    assert svcs[("loadbalancer", "198.51.100.200")].jt_ipam_address_id == ips[1].id
    assert svcs[("external", "198.51.100.50")].jt_ipam_address_id is None
    cidrs = {(r.kind, str(r.cidr)) for r in (await db_session.execute(
        select(K8sCidr).where(K8sCidr.cluster_id == c.id))).scalars()}
    assert cidrs == {("pod", "10.42.1.0/24"), ("service", "10.43.0.0/16")}
    assert c.server_version == "v1.34.1"

    # MetalLB 的兩段都在 198.51.100.0/24 → 建成用途＝保留的位址範圍；Cilium 那段不在任何子網路 → 不建
    ranges = (await db_session.execute(select(IPRange).where(IPRange.source_origin == f"k8s:{c.id}"))).scalars().all()
    assert sorted((str(r.start_ip).split("/")[0], str(r.end_ip).split("/")[0], r.purpose) for r in ranges) == [
        ("198.51.100.200", "198.51.100.210", "reserved"), ("198.51.100.240", "198.51.100.243", "reserved")]
    # DHCP 自動範圍的對帳不可以把它們刪掉（以前會刪掉所有整合建的範圍）
    from app.services.ip_ranges import sync_auto_dhcp_ranges
    await sync_auto_dhcp_ranges(db_session)
    assert len((await db_session.execute(select(IPRange).where(IPRange.source_origin == f"k8s:{c.id}"))).all()) == 2

    # 集區不見了 → 範圍跟著移除；節點不見了 → 鏡像刪除
    _fake_api(monkeypatch, [], [], [], None, None)
    await svc.sync_cluster(db_session, c)
    await db_session.flush()
    assert (await db_session.execute(select(IPRange).where(IPRange.source_origin == f"k8s:{c.id}"))).all() == []
    assert (await db_session.execute(select(K8sNode).where(K8sNode.cluster_id == c.id))).all() == []
    assert (await db_session.execute(select(K8sIPPool).where(K8sIPPool.cluster_id == c.id))).all() == []


@pytest.mark.anyio
async def test_not_ready_node_is_not_evidence_and_ranges_can_be_off(db_session, monkeypatch) -> None:
    c, _sub, ips = await _site(db_session)
    c.create_ip_ranges = False
    down = {**NODE, "status": {**NODE["status"], "conditions": [{"type": "Ready", "status": "False"}]}}
    _fake_api(monkeypatch, [down], [], [METALLB], None, None)
    await svc.sync_cluster(db_session, c)
    await db_session.refresh(ips[0])
    assert ips[0].last_seen_k8s is None
    assert (await db_session.execute(select(IPRange).where(IPRange.source_origin == f"k8s:{c.id}"))).all() == []


@pytest.mark.anyio
async def test_conflicts(db_session, monkeypatch) -> None:
    c, sub, ips = await _site(db_session)
    db_session.add(IPRange(subnet_id=sub.id, start_ip="198.51.100.48", end_ip="198.51.100.60", purpose="dhcp", name="dhcp-a"))
    sec = (await db_session.get(Section, sub.section_id))
    pod_overlap = Subnet(section_id=sec.id, cidr="10.42.0.0/16")
    db_session.add(pod_overlap)
    await db_session.flush()
    _fake_api(monkeypatch, [NODE], [SVC_LB, SVC_EXT], [METALLB], None, None)
    await svc.sync_cluster(db_session, c)
    await db_session.flush()
    found = {(x["kind"], x["ip"]) for x in await svc.conflicts(db_session, cluster_id=c.id)}
    assert ("unregistered", "198.51.100.50") in found           # externalIPs 佔用了沒登記的位址
    assert ("in_dhcp", "198.51.100.50") in found                # 而且在 DHCP 範圍裡
    assert ("pool_used", "198.51.100.200-198.51.100.210") in found   # .205 是 IPAM 登記使用中的印表機
    assert ("cidr_overlap", "10.42.1.0/24") in found
    assert ("unregistered", "198.51.100.200") not in found      # LB 位址 IPAM 有登記
    used = next(x for x in await svc.conflicts(db_session, cluster_id=c.id) if x["kind"] == "pool_used")
    assert used["detail"] == "198.51.100.205", "K8s 自己在用的 .200 不算"
    # 異常偵測的類別用同一份
    from app.services.anomaly import detect_k8s_conflicts
    assert {(x["kind"], x["ip"]) for x in await detect_k8s_conflicts(db_session)} >= found


@pytest.mark.anyio
async def test_secrets_are_write_only_and_kubeconfig_fills_the_connection(client, auth_headers, db_session) -> None:
    r = await client.post("/api/v1/k8s", headers=auth_headers, json={"name": f"kc-{uuid.uuid4().hex[:6]}", "kubeconfig": KUBECONFIG})
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["api_url"], body["auth_method"], body["has_token"], body["has_ca_cert"]) == \
        ("https://192.0.2.10:6443", "token", True, True)
    assert "tok-123" not in r.text
    c = await db_session.get(K8sCluster, uuid.UUID(body["id"]))
    assert svc._secret(c, "token") == "tok-123"
    r = await client.patch(f"/api/v1/k8s/{body['id']}", headers=auth_headers, json={"description": "x"})
    assert r.status_code == 200
    await db_session.refresh(c)
    assert svc._secret(c, "token") == "tok-123", "留空＝不變更"
    for bad, code in (({"api_url": "http://192.0.2.1:6443", "token": "t"}, "k8s_url_https"),
                      ({"api_url": "https://192.0.2.1:6443"}, "k8s_need_token"),
                      ({"kubeconfig": "clusters: []"}, "k8s_kubeconfig_invalid")):
        r = await client.post("/api/v1/k8s", headers=auth_headers, json={"name": f"bad-{uuid.uuid4().hex[:6]}", **bad})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == code
    r = await client.get("/api/v1/k8s/readonly-manifest", headers=auth_headers)
    assert r.status_code == 200
    assert "verbs: [get, list]" in r.text
    assert "create" not in r.text and "delete" not in r.text and "update" not in r.text
    assert (await client.delete(f"/api/v1/k8s/{body['id']}", headers=auth_headers)).status_code == 204


@pytest.mark.anyio
async def test_by_ip_needs_read_on_that_ip(client, auth_headers, db_session, monkeypatch) -> None:
    c, _sub, ips = await _site(db_session)
    _fake_api(monkeypatch, [NODE], [SVC_LB], None, None, None)
    await svc.sync_cluster(db_session, c)
    await db_session.commit()
    r = await client.get(f"/api/v1/k8s/by-ip/{ips[0].id}", headers=auth_headers)
    assert r.status_code == 200
    assert [n["name"] for n in r.json()["nodes"]] == ["worker-1"]
    r = await client.get(f"/api/v1/k8s/by-ip/{ips[1].id}", headers=auth_headers)
    assert [(s["namespace"], s["name"], s["kind"]) for s in r.json()["services"]] == [("shop", "web", "loadbalancer")]
    from app.core.security import hash_password
    from app.models.user import User
    from app.services.auth import issue_access_token
    u = User(username=f"nobody-{uuid.uuid4().hex[:6]}", email="", password_hash=hash_password("TestPassword2026!"),
             auth_provider="local", is_active=True, is_admin=False)
    db_session.add(u)
    await db_session.commit()
    r = await client.get(f"/api/v1/k8s/by-ip/{ips[0].id}", headers={"Authorization": f"Bearer {issue_access_token(u)}"})
    assert r.status_code == 404


# ─────────────────── 第二階段（2026-11-19）：主機名稱、Calico、Whereabouts、可路由的 Pod 網段 ───────────────────

INGRESS = {"metadata": {"name": "shop", "namespace": "shop"},
           "spec": {"tls": [{"hosts": ["shop.example.com"]}],
                    "rules": [{"host": "Shop.Example.com"}, {"host": "api.example.com"}, {"host": "*.dev.example.com"},
                              {"http": {"paths": []}}]},
           "status": {"loadBalancer": {"ingress": [{"ip": "198.51.100.200"}]}}}
INGRESS_PENDING = {"metadata": {"name": "new", "namespace": "shop"}, "spec": {"rules": [{"host": "new.example.com"}]},
                   "status": {"loadBalancer": {}}}
GATEWAY = {"metadata": {"name": "gw", "namespace": "infra"},
           "spec": {"gatewayClassName": "envoy", "listeners": [
               {"name": "https", "hostname": "*.apps.example.com", "port": 443, "protocol": "HTTPS"},
               {"name": "http", "port": 80, "protocol": "HTTP"}]},
           "status": {"addresses": [{"type": "IPAddress", "value": "198.51.100.201"},
                                    {"type": "Hostname", "value": "lb.example.net"}]}}
HTTPROUTE = {"metadata": {"name": "blog", "namespace": "web"},
             "spec": {"parentRefs": [{"name": "gw", "namespace": "infra"}], "hostnames": ["blog.apps.example.com"]}}
GRPCROUTE = {"metadata": {"name": "rpc", "namespace": "infra"},
             "spec": {"parentRefs": [{"name": "gw"}], "hostnames": ["rpc.apps.example.com"]}}
OCP_ROUTE = {"metadata": {"name": "console", "namespace": "openshift-console"},
             "spec": {"host": "console.apps.example.com", "tls": {"termination": "reencrypt"}},
             "status": {"ingress": [{"host": "console.apps.example.com", "routerName": "default",
                                     "routerCanonicalHostname": "router-default.apps.example.com"}]}}
SVC_ROUTER = {"metadata": {"name": "router-default", "namespace": "openshift-ingress"},
              "spec": {"type": "LoadBalancer", "ports": [{"port": 443, "protocol": "TCP"}]},
              "status": {"loadBalancer": {"ingress": [{"ip": "198.51.100.202"}]}}}
CALICO = [
    {"metadata": {"name": "default-ipv4-ippool"},
     "spec": {"cidr": "10.244.0.0/16", "natOutgoing": True, "ipipMode": "Never", "vxlanMode": "CrossSubnet",
              "allowedUses": ["Workload", "Tunnel"]}},
    {"metadata": {"name": "routable"},
     "spec": {"cidr": "10.80.0.0/16", "natOutgoing": False, "ipipMode": "Never", "vxlanMode": "Never"}},
    {"metadata": {"name": "lb"}, "spec": {"cidr": "198.51.100.224/28", "allowedUses": ["LoadBalancer"]}},
]
# Whereabouts：allocations 的 key 是相對 range 裡寫的那個位址的位移（whereabouts 用 net.ParseCIDR 的 IP）
WHEREABOUTS = {"metadata": {"name": "198.51.100.96-27", "namespace": "kube-system"},
               "spec": {"range": "198.51.100.96/27",
                        "allocations": {"1": {"id": "c1", "podref": "shop/db-0", "ifname": "net1"},
                                        "3": {"id": "c2", "podref": "shop/db-1"}}}}

PHASE2 = {"/ingresses": [INGRESS, INGRESS_PENDING], "/gateways": [GATEWAY], "/httproutes": [HTTPROUTE],
          "/grpcroutes": [GRPCROUTE], "route.openshift.io": [OCP_ROUTE], "projectcalico": CALICO,
          "whereabouts": [WHEREABOUTS]}


def test_phase2_parsing() -> None:
    rows = svc.parse_ingress(INGRESS)
    assert [(r["host"], r["tls"], r["ips"]) for r in rows] == [
        ("shop.example.com", True, ["198.51.100.200"]), ("api.example.com", False, ["198.51.100.200"]),
        ("*.dev.example.com", False, ["198.51.100.200"])]
    assert [(r["host"], r["ips"]) for r in svc.parse_ingress(INGRESS_PENDING)] == [("new.example.com", [])]
    gw = svc.parse_gateway(GATEWAY)
    assert gw["ips"] == ["198.51.100.201"], "Hostname 型的位址不是 IP，不收"
    assert [(r["host"], r["tls"]) for r in gw["hostnames"]] == [("*.apps.example.com", True)]
    assert svc.parse_route_refs(HTTPROUTE) == {"hosts": ["blog.apps.example.com"], "parents": [("infra", "gw")]}
    assert svc.parse_route_refs(GRPCROUTE)["parents"] == [("infra", "gw")], "沒寫 namespace＝跟 route 同一個"
    assert svc.parse_openshift_route(OCP_ROUTE) == {"host": "console.apps.example.com", "tls": True,
                                                    "routers": ["default"]}
    cal = [svc.parse_calico_pool(p) for p in CALICO]
    assert (cal[0]["use"], cal[0]["routable"], cal[0]["encapsulation"]) == ("pod", False, "VXLAN")
    assert (cal[1]["use"], cal[1]["routable"], cal[1]["encapsulation"]) == ("pod", True, None)
    assert cal[2]["use"] == "loadbalancer"
    assert cal[2]["blocks"] == [{"start": "198.51.100.224", "end": "198.51.100.239"}]
    wa = svc.parse_whereabouts(WHEREABOUTS)
    assert wa["pool"]["blocks"] == [{"start": "198.51.100.96", "end": "198.51.100.127"}]
    assert wa["pods"] == [{"namespace": "shop", "name": "db-0", "ip": "198.51.100.97"},
                          {"namespace": "shop", "name": "db-1", "ip": "198.51.100.99"}]


async def _phase2_site(db_session):  # type: ignore[no-untyped-def]
    c, sub, ips = await _site(db_session)
    sec = await db_session.get(Section, sub.section_id)
    routable_sub = Subnet(section_id=sec.id, cidr="10.80.0.0/16")
    db_session.add(routable_sub)
    await db_session.flush()
    return c, sub, ips


@pytest.mark.anyio
async def test_sync_hostnames_calico_whereabouts(db_session, monkeypatch) -> None:
    from app.models.k8s import K8sHostname
    c, sub, ips = await _phase2_site(db_session)
    _fake_api(monkeypatch, [NODE], [SVC_LB, SVC_ROUTER], [METALLB], None, None, PHASE2)
    out = await svc.sync_cluster(db_session, c)
    await db_session.flush()
    assert out["missing_permissions"] == []
    hn = {(r.kind, r.host, str(r.ip).split("/")[0] if r.ip else None): r for r in (await db_session.execute(
        select(K8sHostname).where(K8sHostname.cluster_id == c.id))).scalars()}
    assert set(hn) == {
        ("ingress", "shop.example.com", "198.51.100.200"), ("ingress", "api.example.com", "198.51.100.200"),
        ("ingress", "*.dev.example.com", "198.51.100.200"), ("ingress", "new.example.com", None),
        ("gateway", "*.apps.example.com", "198.51.100.201"), ("httproute", "blog.apps.example.com", "198.51.100.201"),
        ("grpcroute", "rpc.apps.example.com", "198.51.100.201"),
        ("route", "console.apps.example.com", "198.51.100.202")}
    assert hn[("ingress", "shop.example.com", "198.51.100.200")].jt_ipam_address_id == ips[1].id
    assert hn[("ingress", "shop.example.com", "198.51.100.200")].tls is True
    pools = {(p.provider, p.name): p for p in (await db_session.execute(
        select(K8sIPPool).where(K8sIPPool.cluster_id == c.id))).scalars()}
    assert pools[("calico", "lb")].use == "loadbalancer"
    assert pools[("whereabouts", "198.51.100.96-27")].use == "pod"
    assert ("calico", "default-ipv4-ippool") not in pools, "Workload 集區是 Pod 網段，不是位址集區"
    cidrs = {str(r.cidr): r for r in (await db_session.execute(select(K8sCidr).where(K8sCidr.cluster_id == c.id))).scalars()}
    assert (cidrs["10.80.0.0/16"].routable, cidrs["10.80.0.0/16"].source) == (True, "calico:routable")
    assert (cidrs["10.244.0.0/16"].routable, cidrs["10.244.0.0/16"].encapsulation) == (False, "VXLAN")
    assert cidrs["10.42.1.0/24"].routable is None, "節點的 podCIDR 看不出能不能路由"
    pods = [(s.namespace, s.name, str(s.ip).split("/")[0]) for s in (await db_session.execute(
        select(K8sServiceIP).where(K8sServiceIP.cluster_id == c.id, K8sServiceIP.kind == "pod"))).scalars()]
    assert sorted(pods) == [("shop", "db-0", "198.51.100.97"), ("shop", "db-1", "198.51.100.99")]
    # Whereabouts 與 Calico LB 集區也建成位址範圍；說明寫明是哪一種
    ranges = {(str(r.start_ip).split("/")[0], str(r.end_ip).split("/")[0]): r for r in (await db_session.execute(
        select(IPRange).where(IPRange.source_origin == f"k8s:{c.id}"))).scalars()}
    assert ("198.51.100.96", "198.51.100.127") in ranges and ("198.51.100.224", "198.51.100.239") in ranges
    assert "Pod" in ranges[("198.51.100.96", "198.51.100.127")].description

    # 都不見了 → 全部收回
    _fake_api(monkeypatch, [], [], None, None, None, {})
    await svc.sync_cluster(db_session, c)
    await db_session.flush()
    assert (await db_session.execute(select(K8sHostname).where(K8sHostname.cluster_id == c.id))).all() == []


@pytest.mark.anyio
async def test_old_readonly_account_still_syncs(db_session, monkeypatch) -> None:
    """客戶套的是第一階段的唯讀帳號（沒有 ingresses、routes 等權限）：同步照跑，缺的權限列出來，不算失敗。"""
    c, _sub, _ips = await _site(db_session)
    _fake_api(monkeypatch, [NODE], [SVC_LB], [METALLB], None, None,
              {"/ingresses": "forbidden", "route.openshift.io": "forbidden", "/gateways": None})
    out = await svc.sync_cluster(db_session, c)
    assert out["nodes"] == 1
    want = ["ingresses.networking.k8s.io", "routes.route.openshift.io"]     # ClusterRole 裡要補的那一條
    assert out["missing_permissions"] == want
    assert c.missing_permissions == want
    _fake_api(monkeypatch, [NODE], [SVC_LB], [METALLB], None, None, {})
    await svc.sync_cluster(db_session, c)
    assert c.missing_permissions is None, "補上權限後清掉提示"


async def _dns(db_session, zone: str, records: list[tuple[str, str, str]]) -> None:  # type: ignore[no-untyped-def]
    from app.models.dns import DNSRecord, DNSServer, DNSZone
    srv = DNSServer(name=f"dns-{uuid.uuid4().hex[:6]}", type="bind9")
    db_session.add(srv)
    await db_session.flush()
    z = DNSZone(server_id=srv.id, name=zone, type="forward")
    db_session.add(z)
    await db_session.flush()
    for name, rtype, value in records:
        db_session.add(DNSRecord(zone_id=z.id, name=name, type=rtype, value=value))
    await db_session.flush()


@pytest.mark.anyio
async def test_dns_mismatch_and_hostname_dns_state(db_session, monkeypatch) -> None:
    c, _sub, _ips = await _phase2_site(db_session)
    await _dns(db_session, "example.com", [("shop", "A", "198.51.100.77"), ("api.example.com.", "A", "198.51.100.200"),
                                           ("www", "CNAME", "shop.example.com.")])
    _fake_api(monkeypatch, [NODE], [SVC_LB, SVC_ROUTER], [METALLB], None, None, PHASE2)
    await svc.sync_cluster(db_session, c)
    await db_session.flush()
    found = [x for x in await svc.conflicts(db_session, cluster_id=c.id) if x["kind"] == "dns_mismatch"]
    assert [(x["ip"], x["what"], x["detail"]) for x in found] == [
        ("198.51.100.200", "shop.example.com", "198.51.100.77")]
    state = {r["host"]: r["dns_state"] for r in await svc.hostnames(db_session, c.id)}
    assert state["shop.example.com"] == "mismatch"
    assert state["api.example.com"] == "match"
    assert state["blog.apps.example.com"] == "none"           # IPAM 同步的 DNS 裡沒有這個名稱
    assert state["*.dev.example.com"] == "wildcard"
    assert state["new.example.com"] == "no_ip"


@pytest.mark.anyio
async def test_routable_pod_networks_map_to_subnets_instead_of_conflicting(db_session, monkeypatch) -> None:
    c, sub, _ips = await _phase2_site(db_session)
    sec = await db_session.get(Section, sub.section_id)
    db_session.add(Subnet(section_id=sec.id, cidr="10.42.0.0/16"))
    await db_session.flush()
    _fake_api(monkeypatch, [NODE], [], None, None, None, {"projectcalico": CALICO})
    await svc.sync_cluster(db_session, c)
    await db_session.flush()
    overlap = {x["ip"] for x in await svc.conflicts(db_session, cluster_id=c.id) if x["kind"] == "cidr_overlap"}
    assert "10.80.0.0/16" not in overlap, "Calico 不 NAT、不封裝的集區是刻意路由到實體網路"
    assert "10.42.1.0/24" in overlap
    mapped = {r["cidr"]: r for r in await svc.cidr_rows(db_session, c.id)}
    assert [s["cidr"] for s in mapped["10.80.0.0/16"]["subnets"]] == ["10.80.0.0/16"]
    # 整個叢集標成「Pod 網段可直接路由」（例如 Cilium native routing）→ 節點的 podCIDR 也不算衝突
    c.pod_network_routable = True
    await db_session.flush()
    overlap = {x["ip"] for x in await svc.conflicts(db_session, cluster_id=c.id) if x["kind"] == "cidr_overlap"}
    assert "10.42.1.0/24" not in overlap
    assert mapped["10.42.1.0/24"]["routable"] is None and mapped["10.42.1.0/24"]["effective_routable"] is False
    again = {r["cidr"]: r for r in await svc.cidr_rows(db_session, c.id)}
    assert again["10.42.1.0/24"]["effective_routable"] is True
    assert [s["cidr"] for s in again["10.42.1.0/24"]["subnets"]] == ["10.42.0.0/16"]
    assert again["10.244.0.0/16"]["effective_routable"] is False, "Calico 明講 NAT＋封裝的，叢集設定蓋不過去"


@pytest.mark.anyio
async def test_by_ip_shows_hostnames_pods_and_routable_pod_network(client, auth_headers, db_session, monkeypatch) -> None:
    c, sub, ips = await _phase2_site(db_session)
    pod_ip = IPAddress(subnet_id=sub.id, ip="198.51.100.97", state="used")
    sec = await db_session.get(Section, sub.section_id)
    routable = (await db_session.execute(select(Subnet).where(Subnet.cidr == "10.80.0.0/16",
                                                              Subnet.section_id == sec.id))).scalar_one()
    in_pod_net = IPAddress(subnet_id=routable.id, ip="10.80.3.4", state="used")
    db_session.add_all([pod_ip, in_pod_net])
    await db_session.flush()
    c.scope_subnet_ids = None
    _fake_api(monkeypatch, [NODE], [SVC_LB, SVC_ROUTER], [METALLB], None, None, PHASE2)
    await svc.sync_cluster(db_session, c)
    await db_session.commit()
    body = (await client.get(f"/api/v1/k8s/by-ip/{ips[1].id}", headers=auth_headers)).json()
    assert sorted(h["host"] for h in body["hostnames"]) == ["*.dev.example.com", "api.example.com", "shop.example.com"]
    body = (await client.get(f"/api/v1/k8s/by-ip/{pod_ip.id}", headers=auth_headers)).json()
    assert [(s["kind"], s["namespace"], s["name"]) for s in body["services"]] == [("pod", "shop", "db-0")]
    body = (await client.get(f"/api/v1/k8s/by-ip/{in_pod_net.id}", headers=auth_headers)).json()
    assert [p["cidr"] for p in body["pod_networks"]] == ["10.80.0.0/16"]
    r = await client.get(f"/api/v1/k8s/{c.id}/hostnames", headers=auth_headers)
    assert r.status_code == 200 and len(r.json()["items"]) == 8


@pytest.mark.anyio
async def test_ingress_hosts_feed_the_fqdn_view(db_session, monkeypatch) -> None:
    """對外開放服務的 FQDN 檢視：Ingress／Gateway 的名稱也算「指向這個 IP 的名稱」（萬用字元不算）。"""
    from app.services.fw_lookup import _attach_fqdns
    c, _sub, _ips = await _phase2_site(db_session)
    _fake_api(monkeypatch, [NODE], [SVC_LB], [METALLB], None, None, {"/ingresses": [INGRESS]})
    await svc.sync_cluster(db_session, c)
    await db_session.flush()
    items = [{"identity": {"ip": "198.51.100.200"}}]
    await _attach_fqdns(db_session, items)
    assert items[0]["identity"]["fqdns"] == ["api.example.com", "shop.example.com"]


@pytest.mark.anyio
async def test_ai_tool_list_k8s(db_session, admin_user, monkeypatch) -> None:
    from app.mcp.tools import GLOBAL_READ_TOOLS, TOOLS
    c, _sub, _ips = await _phase2_site(db_session)
    _fake_api(monkeypatch, [NODE], [SVC_LB, SVC_ROUTER], [METALLB], None, None, PHASE2)
    await svc.sync_cluster(db_session, c)
    await db_session.flush()
    assert "list_k8s" in GLOBAL_READ_TOOLS
    out = await TOOLS["list_k8s"]["fn"](db_session, user=admin_user, ip="198.51.100.200")
    assert [n["name"] for n in out["service_ips"]["items"]] == ["web"]
    assert {h["host"] for h in out["hostnames"]["items"]} == {"shop.example.com", "api.example.com", "*.dev.example.com"}
    assert [p["name"] for p in out["pools"]["items"]] == ["lan"], "位址落在 MetalLB 集區裡"
    out = await TOOLS["list_k8s"]["fn"](db_session, user=admin_user, host="apps.example.com")
    assert {h["host"] for h in out["hostnames"]["items"]} == {"*.apps.example.com", "blog.apps.example.com",
                                                              "rpc.apps.example.com", "console.apps.example.com"}
    out = await TOOLS["list_k8s"]["fn"](db_session, user=admin_user, subnet_cidr="198.51.100.192/28")
    assert sorted(s["ip"] for s in out["service_ips"]["items"]) == ["198.51.100.200", "198.51.100.202"]
    assert [p["name"] for p in out["pools"]["items"]] == ["lan"]
    assert out["nodes"]["items"] == [], "節點 198.51.100.21 不在這個網段"
    out = await TOOLS["list_k8s"]["fn"](db_session, user=admin_user, cluster=c.name)
    assert out["clusters"][0]["name"] == c.name and out["clusters"][0]["nodes"] == 1
    assert out["nodes"]["total"] == 1 and "truncated" in out["nodes"]
    # 單一 IP 的詳細資料也帶 Kubernetes（跟 IP 詳細頁的卡片同一層權限）
    detail = await TOOLS["get_ip_detail"]["fn"](db_session, user=admin_user, ip="198.51.100.200")
    assert [s["name"] for s in detail["kubernetes"]["services"]] == ["web"]
    assert "shop.example.com" in [h["host"] for h in detail["kubernetes"]["hostnames"]]


# ─────────────────── 第三階段（2026-11-19）：Egress IP、API 伺服器位址、Multus 網段 ───────────────────

OVN_EGRESS = {"metadata": {"name": "egress-prod"},
              "spec": {"egressIPs": ["198.51.100.150", "198.51.100.151"], "namespaceSelector": {}},
              "status": {"items": [{"node": "worker-1", "egressIP": "198.51.100.150"}]}}
CILIUM_EGRESS = {"metadata": {"name": "to-internet"},
                 "spec": {"egressGateway": {"nodeSelector": {}, "egressIP": "198.51.100.152"},
                          "egressGateways": [{"nodeSelector": {}, "egressIP": "198.51.100.153"}],
                          "destinationCIDRs": ["0.0.0.0/0"]}}
CILIUM_EGRESS_IFACE = {"metadata": {"name": "by-interface"},
                       "spec": {"egressGateway": {"nodeSelector": {}, "interface": "eth1"}}}
ANTREA_EGRESS = {"metadata": {"name": "web-egress"}, "spec": {"egressIP": "198.51.100.154"},
                 "status": {"egressNode": "worker-1", "egressIP": "198.51.100.154"}}
ANTREA_POOL = {"metadata": {"name": "external"},
               "spec": {"ipRanges": [{"start": "198.51.100.160", "end": "198.51.100.169"}, {"cidr": "198.51.100.176/29"}],
                        "subnetInfo": {"gateway": "198.51.100.1", "prefixLength": 24}}}
NAD_HOSTLOCAL = {"metadata": {"name": "storage", "namespace": "shop"},
                 "spec": {"config": '{"cniVersion": "0.3.1", "type": "macvlan", "master": "eth1", "ipam": {"type": "host-local",'
                                    ' "ranges": [[{"subnet": "198.51.100.64/28", "rangeStart": "198.51.100.66",'
                                    ' "rangeEnd": "198.51.100.78", "gateway": "198.51.100.65"}]]}}'}}
NAD_STATIC = {"metadata": {"name": "legacy-db", "namespace": "shop"},
              "spec": {"config": '{"cniVersion": "0.3.1", "name": "legacy", "plugins": [{"type": "macvlan", "ipam": '
                                 '{"type": "static", "addresses": [{"address": "198.51.100.90/24"}]}}]}'}}
NAD_WHEREABOUTS = {"metadata": {"name": "wa", "namespace": "shop"},
                   "spec": {"config": '{"type": "macvlan", "ipam": {"type": "whereabouts", "range": "198.51.100.96/27"}}'}}
NAD_BROKEN = {"metadata": {"name": "broken", "namespace": "shop"}, "spec": {"config": "not json"}}

PHASE3 = {"k8s.ovn.org": [OVN_EGRESS], "ciliumegressgatewaypolicies": [CILIUM_EGRESS, CILIUM_EGRESS_IFACE],
          "/egresses": [ANTREA_EGRESS], "externalippools": [ANTREA_POOL],
          "network-attachment-definitions": [NAD_HOSTLOCAL, NAD_STATIC, NAD_WHEREABOUTS, NAD_BROKEN]}


def test_phase3_parsing() -> None:
    assert svc.parse_egress("ovn", OVN_EGRESS) == [
        {"name": "egress-prod", "ip": "198.51.100.150", "node": "worker-1"},
        {"name": "egress-prod", "ip": "198.51.100.151", "node": None}]
    assert [e["ip"] for e in svc.parse_egress("cilium", CILIUM_EGRESS)] == ["198.51.100.152", "198.51.100.153"]
    assert svc.parse_egress("cilium", CILIUM_EGRESS_IFACE) == [], "只寫介面的：位址在節點上，看不到"
    assert svc.parse_egress("antrea", ANTREA_EGRESS) == [{"name": "web-egress", "ip": "198.51.100.154", "node": "worker-1"}]
    v1beta2 = {"metadata": {"name": "dual"}, "spec": {"egressIPs": ["198.51.100.155", "2001:db8::55"]},
               "status": {"egressNode": "worker-2", "egressIPs": ["198.51.100.155", "2001:db8::55"]}}
    assert svc.parse_egress("antrea", v1beta2) == [{"name": "dual", "ip": "198.51.100.155", "node": "worker-2"},
                                                   {"name": "dual", "ip": "2001:db8::55", "node": "worker-2"}]
    p = svc.parse_pool("antrea", ANTREA_POOL)
    assert (p["use"], p["blocks"]) == ("external", [{"start": "198.51.100.160", "end": "198.51.100.169"},
                                                    {"start": "198.51.100.176", "end": "198.51.100.183"}])
    hl = svc.parse_nad(NAD_HOSTLOCAL)
    assert hl["pool"]["blocks"] == [{"start": "198.51.100.66", "end": "198.51.100.78"}]
    assert (hl["pool"]["provider"], hl["pool"]["name"], hl["pool"]["use"]) == ("multus", "shop/storage", "pod")
    st = svc.parse_nad(NAD_STATIC)
    assert st["pool"] is None
    assert st["addresses"] == [{"namespace": "shop", "name": "legacy-db", "ip": "198.51.100.90"}]
    assert svc.parse_nad(NAD_WHEREABOUTS) == {"pool": None, "addresses": []}, "Whereabouts 由它自己的 IPPool 處理"
    assert svc.parse_nad(NAD_BROKEN) == {"pool": None, "addresses": []}


@pytest.mark.anyio
async def test_sync_egress_apiserver_and_multus(db_session, monkeypatch) -> None:
    c, sub, ips = await _site(db_session)
    c.api_url = "https://198.51.100.10:6443"
    _fake_api(monkeypatch, [NODE], [], None, None, None, PHASE3)
    out = await svc.sync_cluster(db_session, c)
    await db_session.flush()
    assert out["missing_permissions"] == []
    rows = {(r.kind, r.name, str(r.ip).split("/")[0]): r for r in (await db_session.execute(
        select(K8sServiceIP).where(K8sServiceIP.cluster_id == c.id))).scalars()}
    assert ("egress", "egress-prod", "198.51.100.150") in rows
    assert rows[("egress", "egress-prod", "198.51.100.150")].node == "worker-1"
    assert {("egress", "to-internet", "198.51.100.152"), ("egress", "to-internet", "198.51.100.153"),
            ("egress", "web-egress", "198.51.100.154")} <= set(rows)
    assert ("apiserver", "kubernetes", "198.51.100.10") in rows, "API 位址本身就是 IP"
    assert ("pod", "legacy-db", "198.51.100.90") in rows
    pools = {(p.provider, p.name): p for p in (await db_session.execute(
        select(K8sIPPool).where(K8sIPPool.cluster_id == c.id))).scalars()}
    assert pools[("antrea", "external")].use == "external"
    assert pools[("multus", "shop/storage")].blocks == [{"start": "198.51.100.66", "end": "198.51.100.78"}]
    found = {(x["kind"], x["ip"]) for x in await svc.conflicts(db_session, cluster_id=c.id)}
    assert ("unregistered", "198.51.100.150") in found, "Egress IP 沒登記在 IPAM 也算"

    # API 位址是主機名稱 → 不記（不猜它解析到哪裡）
    c.api_url = "https://k8s.example.com:6443"
    await svc.sync_cluster(db_session, c)
    await db_session.flush()
    kinds = {r.kind for r in (await db_session.execute(select(K8sServiceIP).where(K8sServiceIP.cluster_id == c.id))).scalars()}
    assert "apiserver" not in kinds


@pytest.mark.anyio
async def test_a_broken_optional_resource_does_not_stop_the_sync(db_session, monkeypatch) -> None:
    """選用的 CRD 讀失敗（例如 Antrea 的轉換 webhook 不在，回 500）：其他資料照常同步，錯誤寫在叢集上。"""
    c, _sub, _ips = await _site(db_session)
    _fake_api(monkeypatch, [NODE], [SVC_LB], [METALLB], None, None, {})
    fake_first = svc._list_first

    async def first(client, paths):  # type: ignore[no-untyped-def]
        if "crd.antrea.io" in paths[0]:
            raise svc.K8sError("Kubernetes: HTTP 500", code="k8s_http", path=paths[0], status=500, body="conversion webhook")
        return await fake_first(client, paths)
    monkeypatch.setattr(svc, "_list_first", first)
    out = await svc.sync_cluster(db_session, c)
    assert out["nodes"] == 1
    assert [e.split(":")[0] for e in out["optional_errors"]] == ["egresses.crd.antrea.io", "externalippools.crd.antrea.io"]
    assert "egresses.crd.antrea.io" in (c.last_error or "")


# ─────────────────── 第四階段（2026-11-19）：跨叢集衝突、節點沒登記 ───────────────────

@pytest.mark.anyio
async def test_cross_cluster_clashes_and_unregistered_nodes(db_session, monkeypatch) -> None:
    """兩個叢集在同一個網路上：同一個位址、重疊的集區、都可路由而重疊的 Pod 網段，兩邊的衝突頁都要看得到。"""
    a, sub, _ips = await _site(db_session)
    b = K8sCluster(name=f"dr-{uuid.uuid4().hex[:6]}", api_url="https://k8s-dr.example.com:6443", auth_method="token")
    db_session.add(b)
    await db_session.flush()
    routable = [{"metadata": {"name": "flat"}, "spec": {"cidr": "10.90.0.0/16", "natOutgoing": False,
                                                       "ipipMode": "Never", "vxlanMode": "Never"}}]
    _fake_api(monkeypatch, [NODE], [SVC_LB], [METALLB], None, None, {"projectcalico": routable})
    await svc.sync_cluster(db_session, a)
    node_b = {**NODE, "metadata": {**NODE["metadata"], "name": "dr-1"},
              "status": {**NODE["status"], "addresses": [{"type": "InternalIP", "address": "198.51.100.31"}]}}
    lb_b = {**SVC_LB, "metadata": {"name": "web", "namespace": "dr"}}                  # 同一個 198.51.100.200
    pool_b = {"metadata": {"name": "dr-pool", "namespace": "metallb-system"},
              "spec": {"addresses": ["198.51.100.208-198.51.100.215"]}}                 # 跟 A 的 .200-.210 重疊
    routable_b = [{"metadata": {"name": "flat-b"}, "spec": {"cidr": "10.90.128.0/17", "natOutgoing": False,
                                                           "ipipMode": "Never", "vxlanMode": "Never"}}]
    _fake_api(monkeypatch, [node_b], [lb_b], [pool_b], None, None, {"projectcalico": routable_b})
    await svc.sync_cluster(db_session, b)
    await db_session.flush()

    def kinds(rows):  # type: ignore[no-untyped-def]
        return {(x["kind"], x["ip"]) for x in rows}
    in_a = await svc.conflicts(db_session, cluster_id=a.id)
    in_b = await svc.conflicts(db_session, cluster_id=b.id)
    for rows in (in_a, in_b):
        assert ("cross_ip", "198.51.100.200") in kinds(rows)
        assert ("cross_pool", "198.51.100.208-198.51.100.210") in kinds(rows)
        assert ("cross_cidr", "10.90.128.0/17") in kinds(rows)
    x = next(r for r in in_a if r["kind"] == "cross_ip")
    assert b.name in x["detail"], "寫出撞到哪個叢集的哪個物件"
    # B 的節點 198.51.100.31 在 IPAM 的子網路裡卻沒有紀錄；A 的節點有
    assert ("node_unregistered", "198.51.100.31") in kinds(in_b)
    assert not [r for r in in_a if r["kind"] == "node_unregistered"]
    # 異常偵測（不分叢集）同一個衝突只列一次
    allx = [r for r in await svc.conflicts(db_session) if r["kind"] == "cross_ip"]
    assert len(allx) == 1
