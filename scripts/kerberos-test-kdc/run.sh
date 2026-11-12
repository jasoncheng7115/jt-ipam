#!/bin/bash
# 可丟棄的 Kerberos 測試靶：Samba AD 網域控制站（領域 CORP.TEST），給 Hyper-V 的 WinRM Kerberos 驗證用
# （backend/app/services/winrm_kerberos.py）。只在 docker 內部網路，不綁主機的連接埠。
#
#   bash scripts/kerberos-test-kdc/run.sh          # 建映像、起容器、建帳號與 HTTP/hv1.corp.test 服務名稱
#   docker rm -f jtipam-samba-dc                   # 用完收掉
#
# 起好之後：
#   JTIPAM_TEST_KDC=<容器 IP> JTIPAM_TEST_KRB_USER=svc-ipam@CORP.TEST JTIPAM_TEST_KRB_PASS='Svc-Ipam-2026!' \
#   JTIPAM_TEST_KRB_SPN_HOST=hv1.corp.test pytest tests/test_hyperv_kerberos.py
#   JTIPAM_TEST_AD_HOST=<容器 IP> OUTBOUND_ALLOW_CIDRS=<容器 IP>/32 pytest tests/test_ldap_real_directory.py
#
# 注意：samba-tool 建的電腦帳號預設沒有 AES 金鑰（真的 AD 有），所以下面要補 msDS-SupportedEncryptionTypes 並重設密碼，
# 否則拿 HTTP/<主機> 服務票時會是「KDC has no support for encryption type」。
set -euo pipefail
cd "$(dirname "$0")"
docker build -q -t jtipam-samba-dc . >/dev/null
docker rm -f jtipam-samba-dc >/dev/null 2>&1 || true
docker run -d --name jtipam-samba-dc --hostname dc1.corp.test --privileged jtipam-samba-dc >/dev/null
for _ in $(seq 1 60); do
  docker exec jtipam-samba-dc bash -c 'samba-tool user list >/dev/null 2>&1' && break
  sleep 3
done
docker exec jtipam-samba-dc bash -c "
  samba-tool user create svc-ipam 'Svc-Ipam-2026!' >/dev/null
  samba-tool computer create hv1 >/dev/null
  samba-tool spn add HTTP/hv1.corp.test 'hv1\$'
  apt-get update -qq >/dev/null 2>&1; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ldb-tools >/dev/null 2>&1
  printf 'dn: CN=hv1,CN=Computers,DC=corp,DC=test\nchangetype: modify\nreplace: msDS-SupportedEncryptionTypes\nmsDS-SupportedEncryptionTypes: 24\n' > /tmp/enc.ldif
  ldbmodify -H /var/lib/samba/private/sam.ldb /tmp/enc.ldif >/dev/null
  samba-tool user setpassword 'hv1\$' --newpassword='Hv1-Machine-Pass-2026!' >/dev/null
"
# LDAP／AD 登入的真機測試（tests/test_ldap_real_directory.py）：客戶的 AD 允許不加密的簡單綁定，Samba 預設不允許；
# 測試用帳號 user07（顯示名稱有空白、屬於「IPAM Admins」群組，群組名稱也有空白）與 plain08；
# nest09 只在「Net Team」裡，「Net Team」再是「IPAM Admins」的成員（巢狀群組：memberOf 只列直接所屬，客戶 2026-11-12）
docker exec jtipam-samba-dc bash -c "grep -q 'require strong auth' /etc/samba/smb.conf || sed -i '/^\\[global\\]/a\\\\tldap server require strong auth = no' /etc/samba/smb.conf"
docker restart jtipam-samba-dc >/dev/null
for _ in $(seq 1 60); do
  docker exec jtipam-samba-dc bash -c 'samba-tool user list >/dev/null 2>&1' && break
  sleep 3
done
docker exec jtipam-samba-dc bash -c "
  samba-tool user create user07 'User07-Pass-2026!' --given-name=Test --surname=User07 --mail-address=user07@corp.test >/dev/null
  samba-tool group add 'IPAM Admins' >/dev/null
  samba-tool group addmembers 'IPAM Admins' user07 >/dev/null
  samba-tool user create plain08 'Plain08-Pass-2026!' >/dev/null
  samba-tool user create nest09 'Nest09-Pass-2026!' >/dev/null
  samba-tool group add 'Net Team' >/dev/null
  samba-tool group addmembers 'Net Team' nest09 >/dev/null
  samba-tool group addmembers 'IPAM Admins' 'Net Team' >/dev/null
"
echo "KDC: $(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' jtipam-samba-dc)  realm CORP.TEST  user svc-ipam@CORP.TEST"
