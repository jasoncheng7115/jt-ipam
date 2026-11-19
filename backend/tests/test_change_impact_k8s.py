"""IP 變更評估 × Kubernetes（2026-11-19；新整合都要支援評估）。

- 節點 IP：kubelet 憑證、etcd 都綁 IP → 高
- Service／Whereabouts Pod 佔用的位址：要一起改 Service 或集區
- Ingress／Gateway／Route 的名稱指向這個位址：DNS 要跟著改；同一叢集同一位址的名稱合成一筆
- 落在位址集區裡：可能已經或之後會被發出去
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.models.k8s import K8sCluster, K8sHostname, K8sIPPool, K8sNode, K8sServiceIP
from app.services.change_impact.model import RULES

from tests.test_change_impact_engine import _ip, _net, _rules, _run


async def _cluster(db):  # type: ignore[no-untyped-def]
    c = K8sCluster(name=f"k8s-{uuid.uuid4().hex[:4]}", api_url="https://192.0.2.10:6443", auth_method="token",
                   last_sync_at=datetime.now(UTC))
    db.add(c)
    await db.flush()
    return c


async def test_node_service_hostnames_and_pool(db_session, admin_user) -> None:
    sub = await _net(db_session)
    lb = await _ip(db_session, sub, "198.51.100.200")
    node_ip = await _ip(db_session, sub, "198.51.100.21")
    c = await _cluster(db_session)
    db_session.add_all([
        K8sNode(cluster_id=c.id, name="worker-1", ip="198.51.100.21", jt_ipam_address_id=node_ip.id, ready=True),
        K8sServiceIP(cluster_id=c.id, namespace="ingress", name="traefik", kind="loadbalancer", ip="198.51.100.200",
                     jt_ipam_address_id=lb.id),
        K8sIPPool(cluster_id=c.id, provider="metallb", name="lan", use="loadbalancer",
                  blocks=[{"start": "198.51.100.200", "end": "198.51.100.210"}]),
        *[K8sHostname(cluster_id=c.id, kind="ingress", namespace="shop", name="shop", host=h, ip="198.51.100.200",
                      jt_ipam_address_id=lb.id) for h in ("shop.example.com", "api.example.com", "a.example.com",
                                                          "b.example.com")],
    ])
    res = await _run(db_session, admin_user, lb, "198.51.100.230")
    rules = _rules(res)
    assert "virt.k8s_service_ip" in rules
    assert "virt.k8s_pool" in rules
    assert rules.count("virt.k8s_hostname") == 1, "四個名稱合成一筆"
    f = next(f for f in res.findings if f.rule_id == "virt.k8s_hostname")
    assert f.params["count"] == 4
    assert f.params["hosts"] == "a.example.com, api.example.com, b.example.com, shop.example.com"
    assert RULES["virt.k8s_hostname"].impact == "change_required"
    res = await _run(db_session, admin_user, node_ip, "198.51.100.22")
    f = next(f for f in res.findings if f.rule_id == "virt.k8s_node")
    assert RULES[f.rule_id].severity == "high"


async def test_apiserver_address_is_high(db_session, admin_user) -> None:
    sub = await _net(db_session)
    vip = await _ip(db_session, sub, "198.51.100.10")
    c = await _cluster(db_session)
    db_session.add(K8sServiceIP(cluster_id=c.id, namespace="default", name="kubernetes", kind="apiserver",
                                ip="198.51.100.10", jt_ipam_address_id=vip.id))
    res = await _run(db_session, admin_user, vip, "198.51.100.11")
    f = next(f for f in res.findings if f.rule_id == "virt.k8s_apiserver")
    assert RULES[f.rule_id].severity == "high"
    assert "virt.k8s_service_ip" not in _rules(res), "API 位址只出一筆，不再算成一般 Service 位址"
