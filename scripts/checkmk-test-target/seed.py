"""建 Checkmk 測試資料：5 台主機（含只有 DNS 名稱、IPv6、額外位址）＋唯讀的 guest 自動化帳號 jt-ipam-ro。"""
import base64
import json
import sys
import urllib.request

B = f"{sys.argv[1]}/check_mk/api/1.0"
AUTH = "Basic " + base64.b64encode(b"cmkadmin:Cmk-Admin-2026!").decode()
def call(method, path, body=None, headers=None):
    h = {"Authorization": AUTH, "Accept": "application/json", "Content-Type": "application/json", **(headers or {})}
    r = urllib.request.Request(B + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
    try:
        with urllib.request.urlopen(r) as resp:
            t = resp.read().decode(); return resp.status, (json.loads(t) if t else None), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400], e.headers
print(call("POST", "/domain-types/folder_config/collections/all", {"name": "dc", "title": "Datacenter", "parent": "/"})[:2])
hosts = [
  ("web01", "/dc", {"ipaddress": "127.0.0.1", "alias": "Web server 01", "labels": {"env": "prod"}}),
  ("db01", "/dc", {"ipaddress": "192.0.2.21"}),
  ("sw-core", "/", {"ipaddress": "192.0.2.1", "additional_ipv4addresses": ["192.0.2.254"], "tag_agent": "no-agent", "tag_snmp_ds": "snmp-v2"}),
  ("dnsonly.example.net", "/", {}),
  ("v6host", "/", {"ipaddress": "2001:db8::10", "tag_address_family": "ip-v6-only"}),
]
for name, folder, attrs in hosts:
    print(name, call("POST", "/domain-types/host_config/collections/all", {"host_name": name, "folder": folder, "attributes": attrs})[0])
st, body, hd = call("GET", "/domain-types/activation_run/collections/pending_changes")
etag = hd.get("ETag")
print("user", call("POST", "/domain-types/user_config/collections/all", {
    "username": "jt-ipam-ro", "fullname": "jt-ipam read-only", "roles": ["guest"],
    "auth_option": {"auth_type": "automation", "secret": "RoSecret-2026-abcdef"}})[0])
print("activate", call("POST", "/domain-types/activation_run/actions/activate-changes/invoke", {"redirect": False, "sites": ["cmk"], "force_foreign_changes": True}, {"If-Match": etag or "*"})[:2])
