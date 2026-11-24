#!/bin/bash
# 可丟棄的 Kubernetes 測試靶（tests/test_k8s_real.py、e2e/k8s.spec.ts）：
#   jtipam-k3s   k3s 最新版（單一容器），裝 MetalLB，IPAddressPool 192.0.2.200-192.0.2.220；內建的 Traefik 當
#                Ingress 控制器（真的會把 MetalLB 發的位址寫回 Ingress）
#   jtipam-kind  kind（上游 Kubernetes，舊一個 minor），裝上游的 CRD 與物件：Cilium LB 集區、Gateway API（Gateway、
#                HTTPRoute、GRPCRoute）、Calico IPPool、Whereabouts IPPool、OpenShift Route、OVN-Kubernetes EgressIP、
#                Cilium egress gateway、Antrea Egress / ExternalIPPool、Multus NetworkAttachmentDefinition。
#                沒有控制器的 status 由這支補上
# 兩個叢集都建唯讀的 ServiceAccount jt-ipam-ro（manifest 與管理頁提供給客戶的同一份），印出 API 位址、CA 與 token 的檔案位置。
#
#   bash scripts/k8s-test-target/run.sh
#   docker rm -f -v jtipam-k3s; kind delete cluster --name jtipam     # 用完收掉
#   （-v 不可省：k3s 映像宣告了 /var/lib/rancher/k3s 等 volume，不加會留下每個 0.6～1.2G 的匿名 volume；
#    2026-11-24 開發機硬碟滿時清出 6 個、約 5.9G）
set -euo pipefail
cd "$(dirname "$0")"
OUT="${K8S_TARGET_OUT:-/tmp/jtipam-k8s}"
mkdir -p "$OUT"
KIND_NODE_IMAGE="${KIND_NODE_IMAGE:-kindest/node:v1.32.8}"

# ── k3s ──
docker rm -f -v jtipam-k3s >/dev/null 2>&1 || true
docker run -d --privileged --name jtipam-k3s rancher/k3s:latest server --disable servicelb >/dev/null
K3S_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' jtipam-k3s)
k3s() { docker exec -i jtipam-k3s kubectl "$@"; }
for _ in $(seq 1 60); do k3s get nodes 2>/dev/null | grep -q " Ready" && break; sleep 3; done
k3s apply -f https://raw.githubusercontent.com/metallb/metallb/v0.15.2/config/manifests/metallb-native.yaml >/dev/null
k3s -n metallb-system wait --for=condition=Available deploy/controller --timeout=180s >/dev/null
sleep 10
k3s apply -f - >/dev/null <<'YAML'
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata: {name: lan-pool, namespace: metallb-system}
spec: {addresses: ["192.0.2.200-192.0.2.220", "198.51.100.64/30"]}
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata: {name: lan, namespace: metallb-system}
YAML
k3s apply -f - < fixtures.yaml >/dev/null
k3s apply -f - < ../../deploy/k8s/jt-ipam-readonly.yaml >/dev/null
for _ in $(seq 1 30); do k3s -n jt-demo get svc web -o jsonpath='{.status.loadBalancer.ingress[0].ip}' | grep -q . && break; sleep 2; done
for _ in $(seq 1 60); do k3s -n kube-system get deploy traefik >/dev/null 2>&1 && break; sleep 3; done
k3s -n kube-system wait --for=condition=Available deploy/traefik --timeout=240s >/dev/null
k3s apply -f - < fixtures-k3s-ingress.yaml >/dev/null
for _ in $(seq 1 60); do k3s -n jt-demo get ingress shop -o jsonpath='{.status.loadBalancer.ingress[0].ip}' | grep -q . && break; sleep 2; done
k3s apply -f - < readonly-phase1.yaml >/dev/null
sleep 3
k3s -n kube-system get secret jt-ipam-ro1-token -o jsonpath='{.data.token}' | base64 -d > "$OUT/k3s-phase1.token"
k3s -n kube-system get secret jt-ipam-ro-token -o jsonpath='{.data.token}' | base64 -d > "$OUT/k3s.token"
k3s -n kube-system get secret jt-ipam-ro-token -o jsonpath='{.data.ca\.crt}' | base64 -d > "$OUT/k3s.ca"
echo "https://$K3S_IP:6443" > "$OUT/k3s.url"

# ── kind ──
if ! command -v kind >/dev/null; then
  curl -fsSLo /usr/local/bin/kind https://github.com/kubernetes-sigs/kind/releases/download/v0.30.0/kind-linux-amd64
  chmod +x /usr/local/bin/kind
fi
kind delete cluster --name jtipam >/dev/null 2>&1 || true
kind create cluster --name jtipam --image "$KIND_NODE_IMAGE" --wait 120s >/dev/null
KIND_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' jtipam-control-plane)
kc() { docker exec -i jtipam-control-plane kubectl --kubeconfig /etc/kubernetes/admin.conf "$@"; }
kc apply -f https://raw.githubusercontent.com/cilium/cilium/v1.18.2/pkg/k8s/apis/cilium.io/client/crds/v2/ciliumloadbalancerippools.yaml >/dev/null
sleep 3
kc apply -f - >/dev/null <<'YAML'
apiVersion: cilium.io/v2
kind: CiliumLoadBalancerIPPool
metadata: {name: cilium-pool}
spec:
  blocks:
    - cidr: 203.0.113.0/28
    - start: 203.0.113.100
      stop: 203.0.113.110
YAML
kc apply -f - < fixtures.yaml >/dev/null
kc apply --server-side -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.3.0/standard-install.yaml >/dev/null
kc apply -f https://raw.githubusercontent.com/projectcalico/calico/v3.30.3/libcalico-go/config/crd/crd.projectcalico.org_ippools.yaml >/dev/null
kc apply -f https://raw.githubusercontent.com/k8snetworkplumbingwg/whereabouts/master/doc/crds/whereabouts.cni.cncf.io_ippools.yaml >/dev/null
kc apply -f https://raw.githubusercontent.com/openshift/api/master/route/v1/zz_generated.crd-manifests/routes.crd.yaml >/dev/null
kc apply -f https://raw.githubusercontent.com/ovn-org/ovn-kubernetes/master/helm/ovn-kubernetes/crds/k8s.ovn.org_egressips.yaml >/dev/null
kc apply -f https://raw.githubusercontent.com/cilium/cilium/v1.18.2/pkg/k8s/apis/cilium.io/client/crds/v2/ciliumegressgatewaypolicies.yaml >/dev/null
kc apply -f https://raw.githubusercontent.com/antrea-io/antrea/main/build/charts/antrea/crds/egress.yaml >/dev/null
kc apply -f https://raw.githubusercontent.com/antrea-io/antrea/main/build/charts/antrea/crds/externalippool.yaml >/dev/null
kc apply -f https://raw.githubusercontent.com/k8snetworkplumbingwg/network-attachment-definition-client/master/artifacts/networks-crd.yaml >/dev/null
kc wait --for=condition=Established crd --all --timeout=60s >/dev/null
# Antrea 的 CRD 用 Antrea 自己的轉換 webhook（這裡沒有 Antrea）：改成直接以 v1beta2 儲存、不轉換
for crd in egresses externalippools; do
  kc patch crd $crd.crd.antrea.io --type=json -p '[{"op":"replace","path":"/spec/conversion","value":{"strategy":"None"}},{"op":"replace","path":"/spec/versions/0/storage","value":false},{"op":"replace","path":"/spec/versions/1/storage","value":true}]' >/dev/null
done
kc apply -f - < fixtures-phase2.yaml >/dev/null
kc apply -f - < fixtures-phase3.yaml >/dev/null
kc patch egresses.v1beta2.crd.antrea.io web-egress --subresource=status --type=merge \
  -p '{"status":{"egressNode":"jtipam-control-plane","egressIPs":["203.0.113.154"]}}' >/dev/null
kc -n infra patch gateway gw --subresource=status --type=merge \
  -p '{"status":{"addresses":[{"type":"IPAddress","value":"203.0.113.201"},{"type":"Hostname","value":"lb.example.net"}]}}' >/dev/null
kc -n openshift-ingress patch svc router-default --subresource=status --type=merge \
  -p '{"status":{"loadBalancer":{"ingress":[{"ip":"203.0.113.202"}]}}}' >/dev/null
kc -n openshift-console patch route console --subresource=status --type=merge \
  -p '{"status":{"ingress":[{"host":"console.apps.example.com","routerName":"default","routerCanonicalHostname":"router-default.apps.example.com","conditions":[{"type":"Admitted","status":"True"}]}]}}' >/dev/null
kc apply -f - < ../../deploy/k8s/jt-ipam-readonly.yaml >/dev/null
sleep 3
kc -n kube-system get secret jt-ipam-ro-token -o jsonpath='{.data.token}' | base64 -d > "$OUT/kind.token"
kc -n kube-system get secret jt-ipam-ro-token -o jsonpath='{.data.ca\.crt}' | base64 -d > "$OUT/kind.ca"
echo "https://$KIND_IP:6443" > "$OUT/kind.url"
echo "k3s  $(cat "$OUT/k3s.url")   kind $(cat "$OUT/kind.url")   files in $OUT"
echo "JTIPAM_TEST_K8S=$OUT OUTBOUND_ALLOW_CIDRS=$K3S_IP/32,$KIND_IP/32 pytest tests/test_k8s_real.py"
