"""各家設備的憑證推送 driver。只透過 Http 跟設備說話（base.Http），換上之前一定先記下原本的設定，
回傳的 rollback 會把設備恢復成原狀。

FortiGate（FortiOS 7.x REST，API 權杖要有 system 與 vpn 的寫入權限）
  options: {"bind": ["admin", "sslvpn"], "vdom": "root"}
  1. 讀目前管理介面（system global admin-server-cert）與 SSL VPN（vpn.ssl settings servercert）的憑證
  2. POST /api/v2/monitor/vpn-certificate/local/import（type=regular，憑證含中繼鏈、私鑰都是 PEM 的 base64，scope=global）
     名稱帶指紋前 8 碼：同一版重推會撞名 → 當成已經匯入
  3. 依 bind 改 admin-server-cert／servercert；bind 為空＝只匯入，交給管理員自己選
  舊的 jtipam- 憑證不自動刪（換回去要用；FortiGate 刪憑證前必須沒有任何設定引用它）

OPNsense（23.7 以後的 Trust API，API 金鑰的權限要能寫 System: Certificates 與重啟服務）
  options: {"cert_uuid": "<要替換的既有憑證>", "restart_webgui": true}
  直接替換一張既有憑證的內容（POST /api/trust/cert/set/{uuid}，action=import）：refid 不變，
  網頁管理介面、OpenVPN、HAProxy…凡是引用這張的都跟著換，不必改各服務的設定。
  crt_payload 只能放一張（上游 CertController 數到不是 1 張就拒，2026-11-03 外部審查）→ 中繼 CA 先匯進
  信任庫（/api/trust/ca/add，action=existing，已經有同一張就不重複），存檔時 OPNsense 自己把憑證連到它。
  Trust API 存檔不會重啟服務 → restart_webgui 時 POST /api/core/service/restart/webgui：回 4xx/5xx 是失敗
  （權限不足、路徑錯）；重啟當下連線被切斷是預期的，由引擎連過去確認。

Palo Alto（PAN-OS XML API；API 金鑰的角色要能匯入憑證、改設定、commit）
  options: {"profile": "<SSL/TLS 服務設定檔名稱>", "commit": true}
  1. 私鑰用隨機密碼加密成 PKCS#8 後與憑證一起匯入（type=import&category=keypair&format=pem；PAN-OS 要求私鑰加密）
     中繼憑證另外匯入（category=certificate），名稱同前綴加 -ca1、-ca2…
  2. 讀設定檔目前的憑證 → 改成新的（/config/shared/ssl-tls-service-profile/entry[@name=…]/certificate）
  3. commit，輪詢工作直到 FIN；結果不是 OK 就失敗（換回去也要 commit）
  設定檔被 GlobalProtect 入口、管理介面等引用，換掉設定檔的憑證＝它們一起換。
  commit 會套用整份候選設定（2026-11-03 外部審查）→ 改之前先查有沒有別人待提交的變更，有就不動；
  options.commit_admin 有填時用 partial commit 只套用那個管理員的變更。commit 失敗時把候選設定換回原本的
  （不然下次別人 commit 會意外套上）；逾時代表結果不知道，不亂動、回報工作編號。
  commit=false＝只寫進候選設定、等管理員自己 commit：狀態是「已寫入待 commit」，引擎不驗證也不撤回。

pfSense（pfSense-pkg-RESTAPI v2；X-API-Key 的權限要能建立憑證與改 webConfigurator）
  options: {"bind_webgui": true}
  1. 憑證清單裡已有同名（jtipam-…-指紋）就沿用，否則 POST /api/v2/system/certificate 建一張
  2. bind_webgui 時讀 /api/v2/system/webgui/settings 的 sslcertref → PATCH 成新憑證的 refid
     改了會重啟 webConfigurator，這個請求可能被斷線（設定其實已經改了）→ 不當失敗，交給引擎連過去確認，
     確認不到照樣換回（2026-11-03 外部審查）。
"""
from __future__ import annotations

import asyncio
import base64
import re
import secrets
from typing import Any
from xml.sax.saxutils import escape as _xml_escape

from defusedxml import ElementTree as DefusedET

from app.services.cert_push.base import Bundle, Http, PushError, Rollback, Steps, b64

#: Palo Alto commit 工作最多輪詢幾次、間隔幾秒（約 4 分鐘）
COMMIT_POLLS = 120
COMMIT_POLL_S = 2.0


def _disconnected(exc: PushError) -> bool:
    """服務重啟時連線被切斷（預期中）。只認連線層的錯誤，不包含設備回的 4xx/5xx 與 SSRF 擋下。"""
    return exc.code == "certpush_transport"


def _body(resp: Any) -> dict[str, Any]:
    try:
        data = resp.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


class _ExplicitVerify:
    """換的憑證不一定是管理介面在用（Palo Alto 的設定檔可能給 GlobalProtect、OPNsense 的憑證可能給 HAProxy）：
    只確認使用者填的「確認位址」；沒填就標未確認、不換回 —— 以前拿 API 網址去確認，管理介面本來就不會換，
    會被誤判失敗而換回（使用者 11-03 同意）。換的是管理介面的憑證時，確認位址填 API 網址的主機:埠。"""

    def verify_endpoints(self, api: tuple[str, int], custom: tuple[str, int] | None) -> list[tuple[str, str, int]]:
        return [("service", custom[0], custom[1])] if custom else []

    def unverifiable(self, custom: tuple[str, int] | None) -> list[str]:
        return [] if custom else ["service"]


class FortiGateDriver:
    kind = "fortigate"

    def __init__(self, http: Http, options: dict[str, Any]) -> None:
        self.http, self.opt = http, options or {}

    def _ok(self, step: str, resp: Any) -> dict[str, Any]:
        body = _body(resp)
        if resp.status_code >= 400 or body.get("status") == "error":
            raise PushError(step, "certpush_device_error", resp.text)
        return body

    async def push(self, bundle: Bundle, steps: Steps) -> Rollback | None:
        bind = [b for b in (self.opt.get("bind") or []) if b in ("admin", "sslvpn")]
        vdom = str(self.opt.get("vdom") or "root")
        name = bundle.device_name(35)
        old: dict[str, str] = {}
        if "admin" in bind:
            r = self._ok("read_admin", await self.http("GET", "/api/v2/cmdb/system/global"))
            old["admin"] = str((r.get("results") or {}).get("admin-server-cert") or "")
        if "sslvpn" in bind:
            r = self._ok("read_sslvpn", await self.http("GET", "/api/v2/cmdb/vpn.ssl/settings", params={"vdom": vdom}))
            old["sslvpn"] = str((r.get("results") or {}).get("servercert") or "")
        steps.ok("read", ", ".join(f"{k}={v}" for k, v in old.items()))

        resp = await self.http("POST", "/api/v2/monitor/vpn-certificate/local/import", json={
            "type": "regular", "certname": name, "scope": "global",
            "file_content": b64(bundle.fullchain), "key_file_content": b64(bundle.key_pem), "password": "",
        })
        body = _body(resp)
        if resp.status_code >= 400 or body.get("status") == "error":
            if body.get("error") == -5 or "exist" in resp.text.lower():     # 同一版已經匯入過
                steps.ok("import", f"{name} (already present)")
            else:
                raise PushError("import", "certpush_device_error", resp.text)
        else:
            steps.ok("import", name)

        done: list[str] = []

        async def rollback() -> None:
            # 換回去也要看回應（以前沒檢查：設備回 500 也當已換回，2026-11-03 外部審查）
            for what in reversed(done):
                if what == "admin":
                    self._ok("rollback", await self.http("PUT", "/api/v2/cmdb/system/global",
                                                         json={"admin-server-cert": old["admin"]}))
                else:
                    self._ok("rollback", await self.http("PUT", "/api/v2/cmdb/vpn.ssl/settings", params={"vdom": vdom},
                                                         json={"servercert": old["sslvpn"]}))

        try:
            if "admin" in bind:
                self._ok("bind_admin", await self.http("PUT", "/api/v2/cmdb/system/global",
                                                       json={"admin-server-cert": name}))
                done.append("admin")
                steps.ok("bind_admin", name)
            if "sslvpn" in bind:
                self._ok("bind_sslvpn", await self.http("PUT", "/api/v2/cmdb/vpn.ssl/settings", params={"vdom": vdom},
                                                        json={"servercert": name}))
                done.append("sslvpn")
                steps.ok("bind_sslvpn", name)
        except PushError:
            await rollback()                         # 綁到一半：已經換掉的先換回去
            raise
        return rollback if done else None

    def verify_endpoints(self, api: tuple[str, int], custom: tuple[str, int] | None) -> list[tuple[str, str, int]]:
        """要確認哪些位址（2026-11-03 外部審查：以前只驗一個）。管理介面看 API 網址；SSL VPN 看「確認位址」，
        沒填就沒辦法確認 —— 不可以拿管理介面判斷（它本來就不會換，會被誤判成失敗而換回）。
        只換管理介面又填了確認位址時，照舊用確認位址。"""
        bind = [b for b in (self.opt.get("bind") or []) if b in ("admin", "sslvpn")]
        out: list[tuple[str, str, int]] = []
        if "admin" in bind:
            h, port = custom if (custom and "sslvpn" not in bind) else api
            out.append(("admin", h, port))
        if "sslvpn" in bind and custom:
            out.append(("sslvpn", custom[0], custom[1]))
        return out

    def unverifiable(self, custom: tuple[str, int] | None) -> list[str]:
        bind = self.opt.get("bind") or []
        return ["sslvpn"] if "sslvpn" in bind and not custom else []


def _parse_cert(raw: Any) -> dict[str, str | None]:
    """憑證本體 → CN、簽發者 CN、有效期間（ISO）。PEM 或 base64 包著的 PEM；失敗回空。"""
    import base64
    import binascii

    from cryptography import x509
    from cryptography.x509.oid import NameOID
    t = str(raw or "").strip()
    if not t:
        return {}
    if "BEGIN CERTIFICATE" not in t:
        try:
            t = base64.b64decode(t, validate=True).decode("utf-8", "replace")
        except (binascii.Error, ValueError):
            return {}
    try:
        c = x509.load_pem_x509_certificate(t.encode())
    except ValueError:
        return {}

    def cn(name: Any) -> str | None:
        v = name.get_attributes_for_oid(NameOID.COMMON_NAME)
        return str(v[0].value) if v else None
    return {"subject": cn(c.subject), "issuer": cn(c.issuer),
            "valid_from": c.not_valid_before_utc.isoformat(), "valid_to": c.not_valid_after_utc.isoformat()}


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes")


def _when(v: Any) -> str | None:
    """OPNsense 的有效日期：版本不同可能是 epoch 秒數或「YYYY-MM-DD hh:mm:ss」，一律轉 ISO（UTC）。"""
    from datetime import UTC, datetime
    t = str(v or "").strip()
    if not t:
        return None
    if t.isdigit():
        return datetime.fromtimestamp(int(t), UTC).isoformat()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).replace(tzinfo=UTC).isoformat()
        except ValueError:
            continue
    return None


class OPNsenseDriver(_ExplicitVerify):
    kind = "opnsense"

    def __init__(self, http: Http, options: dict[str, Any]) -> None:
        self.http, self.opt = http, options or {}

    async def list_device_certs(self) -> list[dict[str, Any]]:
        """給畫面選「要替換哪一張」：連同使用中、使用者憑證、簽發者、有效期間（使用者 10-23），使用中的排前面。"""
        resp = await self.http("GET", "/api/trust/cert/search")
        if resp.status_code >= 400:
            raise PushError("list", "certpush_device_error", resp.text)
        rows = []
        for r in _body(resp).get("rows") or []:
            if not r.get("uuid"):
                continue
            rows.append({"id": str(r.get("uuid")), "name": str(r.get("descr") or ""),
                         "subject": str(r.get("commonname") or ""),
                         "in_use": _truthy(r.get("in_use")), "is_user": _truthy(r.get("is_user")),
                         "issuer": str(r.get("caref") or "") or None,
                         "valid_from": _when(r.get("valid_from")), "valid_to": _when(r.get("valid_to"))})
        rows.sort(key=lambda x: not x["in_use"])          # 穩定排序：使用中在前，其餘照設備的順序
        return rows

    async def _set(self, uuid: str, descr: str, crt: str, prv: str, step: str) -> None:
        resp = await self.http("POST", f"/api/trust/cert/set/{uuid}", json={"cert": {
            "action": "import", "descr": descr, "crt_payload": crt, "prv_payload": prv}})
        body = _body(resp)
        if resp.status_code >= 400 or body.get("result") != "saved":
            raise PushError(step, "certpush_device_error", str(body.get("validations") or resp.text))

    async def _restart(self, steps: Steps, step: str = "restart_webgui") -> None:
        try:
            resp = await self.http("POST", "/api/core/service/restart/webgui", timeout=20.0)
        except PushError as exc:
            if not _disconnected(exc):
                raise
            steps.ok(step, "connection closed during restart; verifying")
            return
        if resp.status_code >= 400:
            raise PushError(step, "certpush_device_error", resp.text)
        steps.ok(step, str(resp.status_code))

    async def _ensure_cas(self, chain_pem: str, steps: Steps) -> None:
        """中繼 CA 匯進信任庫；同一張已經在了（不管誰匯的、叫什麼名字）就不重複。"""
        pems = _split_pems(chain_pem)
        if not pems:
            return
        from cryptography import x509
        have: set[bytes] = set()
        rows = _body(await self.http("GET", "/api/trust/ca/search")).get("rows") or []
        for r in rows[:200]:
            ca = _body(await self.http("GET", f"/api/trust/ca/get/{r.get('uuid')}")).get("ca") or {}
            try:
                have.add(x509.load_pem_x509_certificate(base64.b64decode(str(ca.get("crt") or "")))
                         .public_bytes(serialization_der()))
            except ValueError:
                pass                                 # 讀不出來的 CA（空的、格式不對）不算已有，照樣匯入新的
        for pem in pems:
            c = x509.load_pem_x509_certificate(pem.encode())
            der = c.public_bytes(serialization_der())
            if der in have:
                continue
            import hashlib
            descr = f"jtipam-ca-{hashlib.sha256(der).hexdigest()[:8]}"
            resp = await self.http("POST", "/api/trust/ca/add", json={"ca": {
                "action": "existing", "descr": descr, "crt_payload": pem.strip() + "\n"}})
            body = _body(resp)
            if resp.status_code >= 400 or body.get("result") != "saved":
                raise PushError("import_ca", "certpush_device_error", str(body.get("validations") or resp.text))
            have.add(der)
            steps.ok("import_ca", descr)

    async def push(self, bundle: Bundle, steps: Steps) -> Rollback | None:
        uuid = str(self.opt.get("cert_uuid") or "").strip()
        if not uuid:
            raise PushError("read", "certpush_opnsense_no_target")
        resp = await self.http("GET", f"/api/trust/cert/get/{uuid}")
        cur = _body(resp).get("cert")
        if resp.status_code >= 400 or not isinstance(cur, dict):
            raise PushError("read", "certpush_opnsense_cert_missing", resp.text)
        descr = str(cur.get("descr") or bundle.cert_name)

        def _dec(v: Any) -> str:
            try:
                return base64.b64decode(str(v or "")).decode()
            except Exception:
                return ""
        old_crt, old_prv = _dec(cur.get("crt")), _dec(cur.get("prv"))
        steps.ok("read", descr)

        await self._ensure_cas(bundle.chain_pem, steps)
        await self._set(uuid, descr, bundle.cert_pem.strip() + "\n", bundle.key_pem, "replace")
        steps.ok("replace", descr)
        restart = self.opt.get("restart_webgui", True) is not False
        if restart:
            await self._restart(steps)

        async def rollback() -> None:
            if not (old_crt and old_prv):
                raise PushError("rollback", "certpush_no_rollback_data")
            first = (_split_pems(old_crt) or [old_crt])[0]   # 舊版本若曾存成整串鏈，也只送第一張
            await self._set(uuid, descr, first.strip() + "\n", old_prv, "rollback")
            if restart:
                await self._restart(Steps(), "rollback")
        return rollback


def _split_pems(text: str) -> list[str]:
    return re.findall(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", text or "", re.S)


def serialization_der():  # type: ignore[no-untyped-def]
    from cryptography.hazmat.primitives.serialization import Encoding
    return Encoding.DER


def _encrypt_key(key_pem: str, passphrase: str) -> str:
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.BestAvailableEncryption(passphrase.encode())).decode()


class PaloAltoDriver(_ExplicitVerify):
    kind = "paloalto"

    async def _xml(self, step: str, params: dict[str, Any], *, content: bytes | None = None,
                   headers: dict[str, str] | None = None, method: str = "GET") -> Any:
        resp = await self.http(method, "/api/", params=params, content=content, headers=headers)
        try:
            root = DefusedET.fromstring(resp.text)
        except Exception as exc:
            raise PushError(step, "certpush_device_error", resp.text) from exc
        if resp.status_code >= 400 or root.get("status") != "success":
            msg = " ".join(t.strip() for t in root.itertext() if t.strip())
            raise PushError(step, "certpush_device_error", msg or resp.text)
        return root

    async def _import(self, step: str, name: str, category: str, pem: str, passphrase: str | None = None) -> None:
        boundary = "jtipam" + secrets.token_hex(12)
        content = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}.pem\"\r\n"
                   f"Content-Type: application/x-pem-file\r\n\r\n{pem}\r\n--{boundary}--\r\n").encode()
        params: dict[str, Any] = {"type": "import", "category": category, "certificate-name": name, "format": "pem"}
        if passphrase:
            params["passphrase"] = passphrase
        await self._xml(step, params, content=content, method="POST",
                        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})

    def __init__(self, http: Http, options: dict[str, Any]) -> None:
        self.http, self.opt = http, options or {}
        self.staged = False                          # commit=false：只寫進候選設定（引擎看這個決定狀態）

    async def _pending(self) -> bool:
        r = await self._xml("precheck", {"type": "op", "cmd": "<check><pending-changes></pending-changes></check>"})
        return (r.findtext(".//result") or "").strip().lower() == "yes"

    async def _commit(self, step: str) -> None:
        admin = str(self.opt.get("commit_admin") or "").strip()
        cmd = (f"<commit><partial><admin><member>{_xml_escape(admin)}</member></admin></partial></commit>"
               if admin else "<commit></commit>")
        root = await self._xml(step, {"type": "commit", "cmd": cmd})
        job = root.findtext(".//job")
        if not job:                                  # 「沒有可以 commit 的變更」
            return
        for _ in range(COMMIT_POLLS):
            r = await self._xml(step, {"type": "op", "cmd": f"<show><jobs><id>{int(job)}</id></jobs></show>"})
            if (r.findtext(".//job/status") or "") == "FIN":
                if (r.findtext(".//job/result") or "") != "OK":
                    lines = " ".join((e.text or "").strip() for e in r.iter("line"))
                    raise PushError(step, "certpush_commit_failed", lines)
                return
            await asyncio.sleep(COMMIT_POLL_S)
        raise PushError(step, "certpush_commit_timeout", f"job {int(job)}")

    async def push(self, bundle: Bundle, steps: Steps) -> Rollback | None:
        profile = str(self.opt.get("profile") or "").strip()
        if not profile:
            raise PushError("read", "certpush_paloalto_no_profile")
        xpath = f"/config/shared/ssl-tls-service-profile/entry[@name='{profile}']"
        root = await self._xml("read", {"type": "config", "action": "get", "xpath": f"{xpath}/certificate"})
        old = (root.findtext(".//certificate") or "").strip()
        steps.ok("read", old)
        commit = self.opt.get("commit", True) is not False
        if commit and await self._pending():
            # commit 會連別人還沒準備好的變更一起套用：有待提交的變更就什麼都不動
            raise PushError("precheck", "certpush_paloalto_pending_changes")

        async def set_cert(step: str, value: str) -> None:
            await self._xml(step, {"type": "config", "action": "set", "xpath": xpath,
                                   "element": f"<certificate>{_xml_escape(value)}</certificate>"})

        name = bundle.device_name(31)
        for i, ca in enumerate(_split_pems(bundle.chain_pem), 1):
            await self._import("import_chain", f"{name[:27]}-ca{i}", "certificate", ca + "\n")
        passphrase = secrets.token_urlsafe(24)
        await self._import("import", name, "keypair",
                           bundle.cert_pem.strip() + "\n" + _encrypt_key(bundle.key_pem, passphrase), passphrase)
        steps.ok("import", name)

        async def rollback() -> None:
            if not old:
                raise PushError("rollback", "certpush_no_rollback_data")
            await set_cert("rollback", old)
            if commit:
                await self._commit("rollback")

        await set_cert("set_profile", name)
        steps.ok("set_profile", profile)
        if not commit:
            self.staged = True                       # 只寫進候選設定，等管理員自己 commit
            steps.ok("staged", "candidate only; commit on the firewall to apply")
            return rollback
        try:
            await self._commit("commit")
        except PushError as exc:
            if exc.code == "certpush_commit_failed" and old:
                # commit 沒成功，但候選設定已經指向新憑證：換回去，下次別人 commit 才不會意外套上
                try:
                    await set_cert("revert_candidate", old)
                    steps.ok("revert_candidate", old)
                except PushError as rexc:
                    steps.fail("revert_candidate", rexc.code, rexc.detail)
            raise
        steps.ok("commit")
        return rollback


class PfSenseDriver:
    kind = "pfsense"

    def __init__(self, http: Http, options: dict[str, Any]) -> None:
        self.http, self.opt = http, options or {}

    def _ok(self, step: str, resp: Any) -> dict[str, Any]:
        body = _body(resp)
        if resp.status_code >= 400 or (body.get("status") not in (None, "ok")):
            raise PushError(step, "certpush_device_error", str(body.get("message") or resp.text))
        return body

    async def list_device_certs(self) -> list[dict[str, Any]]:
        """設備上的憑證（唯讀）：哪張是網頁管理介面正在用的、CN、簽發者、有效期間（使用者 10-23）。
        pfSense 的 API 不給有效日，從 crt 解析（PEM，或 base64 包著的 PEM）；解析不了就不顯示日期。"""
        rows = self._ok("list", await self.http("GET", "/api/v2/system/certificates")).get("data") or []
        cur = self._ok("list", await self.http("GET", "/api/v2/system/webgui/settings")).get("data") or {}
        used = str(cur.get("sslcertref") or "")
        out = []
        for r in rows:
            if not isinstance(r, dict) or not r.get("refid"):
                continue
            info = _parse_cert(r.get("crt"))
            out.append({"id": str(r["refid"]), "name": str(r.get("descr") or ""), "subject": info.get("subject") or "",
                        "in_use": str(r["refid"]) == used, "is_user": str(r.get("type") or "") == "user",
                        "issuer": info.get("issuer"), "valid_from": info.get("valid_from"), "valid_to": info.get("valid_to")})
        out.sort(key=lambda x: not x["in_use"])
        return out

    async def push(self, bundle: Bundle, steps: Steps) -> Rollback | None:
        name = bundle.device_name(40)
        rows = self._ok("read", await self.http("GET", "/api/v2/system/certificates")).get("data") or []
        refid = next((str(r.get("refid")) for r in rows if isinstance(r, dict) and r.get("descr") == name), "")
        if refid:
            steps.ok("import", f"{name} (already present)")
        else:
            data = self._ok("import", await self.http("POST", "/api/v2/system/certificate", json={
                "descr": name, "crt": bundle.fullchain, "prv": bundle.key_pem})).get("data") or {}
            refid = str(data.get("refid") or "")
            if not refid:
                raise PushError("import", "certpush_device_error", "no refid in response")
            steps.ok("import", name)

        if not self.opt.get("bind_webgui", True):
            return None
        cur = self._ok("read_webgui", await self.http("GET", "/api/v2/system/webgui/settings")).get("data") or {}
        old = str(cur.get("sslcertref") or "")

        async def patch(step: str, ref: str, s: Steps) -> None:
            try:
                resp = await self.http("PATCH", "/api/v2/system/webgui/settings", json={"sslcertref": ref})
            except PushError as exc:
                if not _disconnected(exc):
                    raise
                # 改了會重啟 webConfigurator，請求被斷線不代表沒改：交給引擎連過去確認
                s.ok(step, f"{ref} (connection dropped during webConfigurator restart; verifying)")
                return
            self._ok(step, resp)
            s.ok(step, ref)

        async def rollback() -> None:
            if not old:
                raise PushError("rollback", "certpush_no_rollback_data")
            await patch("rollback", old, Steps())

        await patch("bind_webgui", refid, steps)
        return rollback


DRIVERS: dict[str, type] = {"fortigate": FortiGateDriver, "opnsense": OPNsenseDriver,
                            "paloalto": PaloAltoDriver, "pfsense": PfSenseDriver}
