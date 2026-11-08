"""推送引擎：載入目前版本 → 交給 driver 推上去並換上 → 連過去確認對方出示的憑證就是這一版 → 不是就換回去。

確認的規則（推錯憑證可能讓管理介面連不上，所以這段寧可保守）：
- 推之前先連一次確認位址（verify_endpoint，預設是 api_url 的主機與埠）。
- 推完輪詢最多 VERIFY_TRIES 次：出示的指紋等於新版 → ok。
- 推之前連得到、推完一直不是新版（或連不到）→ 呼叫 driver 的 rollback 換回原本的憑證 → rolled_back。
- 推之前就連不到（例如 SSL VPN 埠只開在外網）→ 沒辦法確認，不換回 → unverified。
- 只匯入、不換上（driver 回 rollback=None）→ unverified（設備上有了，但還沒有服務在用）。
- 只寫進候選設定、等管理員自己套用（Palo Alto commit=false）→ staged，不驗證也不撤回。
- 一次換好幾個服務（FortiGate 管理介面＋SSL VPN）→ 每個都確認；有一個沒換上就整次換回。
- 換回去之後再連一次，確認出示的回到原本那張；沒回去 → error（certpush_rollback_unconfirmed），要人去看。
- 同一個目標同時只推一次（手動與排程撞在一起時，後來的直接回 busy，不動設備也不改紀錄）。
每次推送都留稽核（不含私鑰），失敗發 cert.push_failed 通知，成功發 cert.deployed。
2026-11-03 外部審查後補上：多位址確認、換回後確認、staged、互斥。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import ssl
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.safe_http import UnsafeOutboundURL, safe_request, transport_detail
from app.core.security import decrypt_secret, encrypt_secret
from app.models.cert_push_target import CertPushTarget
from app.models.certificate import Certificate, CertVersion
from app.models.encrypted_secret import EncryptedSecret
from app.services.cert_push.base import Bundle, PushError, Steps
from app.services.cert_push.drivers import DRIVERS

VERIFY_TRIES = 10
VERIFY_INTERVAL_S = 3.0
#: 換回去之後確認幾次
ROLLBACK_TRIES = 6
#: 自動推送失敗後，隔多久才再試（避免每輪同步都去敲設備）
RETRY_AFTER = timedelta(hours=1)
SECRET_TYPE = "cert_push_target"  # noqa: S105 -- encrypted_secrets 的物件類型名稱，不是密碼


def _aad(target_id: Any) -> bytes:
    return f"cert_push_target:{target_id}:secret".encode()


async def save_secret(session: AsyncSession, target_id: Any, secret: dict[str, str]) -> None:
    enc, nonce = encrypt_secret(json.dumps(secret), aad=_aad(target_id))
    row = (await session.execute(select(EncryptedSecret).where(
        EncryptedSecret.object_type == SECRET_TYPE, EncryptedSecret.object_id == target_id,
        EncryptedSecret.field == "secret"))).scalar_one_or_none()
    if row is None:
        session.add(EncryptedSecret(object_type=SECRET_TYPE, object_id=target_id, field="secret",
                                    ciphertext=enc, nonce=nonce))
    else:
        row.ciphertext, row.nonce = enc, nonce


async def load_secret(session: AsyncSession, target_id: Any) -> dict[str, str]:
    row = (await session.execute(select(EncryptedSecret).where(
        EncryptedSecret.object_type == SECRET_TYPE, EncryptedSecret.object_id == target_id,
        EncryptedSecret.field == "secret"))).scalar_one_or_none()
    if row is None:
        return {}
    return json.loads(decrypt_secret(row.ciphertext, row.nonce, aad=_aad(target_id)).decode())


async def delete_secret(session: AsyncSession, target_id: Any) -> None:
    from sqlalchemy import delete
    await session.execute(delete(EncryptedSecret).where(
        EncryptedSecret.object_type == SECRET_TYPE, EncryptedSecret.object_id == target_id))


def _auth_headers(kind: str, secret: dict[str, str]) -> dict[str, str]:
    if kind == "fortigate":
        return {"Authorization": f"Bearer {secret.get('api_key', '')}"}
    if kind == "opnsense":
        import base64
        raw = f"{secret.get('api_key', '')}:{secret.get('api_secret', '')}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
    if kind == "paloalto":
        return {"X-PAN-KEY": secret.get("api_key", "")}
    if kind == "pfsense":
        return {"X-API-Key": secret.get("api_key", "")}
    return {}


def make_http(target: CertPushTarget, secret: dict[str, str]):
    """driver 用的 Http：所有請求都走 safe_request（SSRF 檢查、轉址不帶認證到別台）。"""
    base = target.api_url.rstrip("/")
    auth = _auth_headers(target.kind, secret)

    async def call(method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None,
                   content: bytes | None = None, headers: dict[str, str] | None = None,
                   timeout: float = 30.0) -> httpx.Response:
        try:
            return await safe_request(method, base + path, headers={**auth, **(headers or {})}, params=params,
                                      json=json, content=content, timeout=timeout, verify=target.verify_tls,
                                      follow_redirects=False)
        except UnsafeOutboundURL as exc:
            raise PushError("connect", "certpush_ssrf", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise PushError("connect", "certpush_transport", transport_detail(exc)) from exc
    return call


def lock_key(target_id: Any) -> int:
    """PostgreSQL advisory lock 的鍵（有號 64 位元）：同一個目標同時只推一次。"""
    h = hashlib.sha256(f"cert_push:{target_id}".encode()).digest()
    return int.from_bytes(h[:8], "big", signed=True)


def _custom_endpoint(target: CertPushTarget) -> tuple[str, int] | None:
    raw = (target.verify_endpoint or "").strip()
    if not raw:
        return None
    u = urlsplit(raw if "://" in raw else f"https://{raw}")
    return (u.hostname or ""), (u.port or 443)


def verify_address(target: CertPushTarget) -> tuple[str, int]:
    raw = (target.verify_endpoint or "").strip()
    if raw:
        u = urlsplit(raw if "://" in raw else f"https://{raw}")
    else:
        u = urlsplit(target.api_url)
    return (u.hostname or ""), (u.port or 443)


#: 最近一次連不到的原因（DNS 解析不到、被拒絕、逾時、被對外連線規則擋下…），步驟明細寫出來。
#: 2026-11-07：確認位址的 DNS 沒設，畫面只寫 unreachable，看不出是哪裡的問題
_PROBE_ERRORS: dict[tuple[str, int], str] = {}


def probe_error(host: str, port: int) -> str:
    return _PROBE_ERRORS.get((host, port), "")


def _why(host: str, port: int, reason: str) -> None:
    if len(_PROBE_ERRORS) > 500:
        _PROBE_ERRORS.clear()
    _PROBE_ERRORS[(host, port)] = reason[:200]


async def presented_fingerprint(host: str, port: int, timeout: float = 8.0) -> str | None:
    """連過去讀對方出示的憑證，回 SHA-256 指紋（小寫十六進位，與 CertVersion 相同）；連不到回 None，
    原因記在 probe_error()。只讀不驗（自簽、名稱不符照樣讀得到）—— 這裡要知道的是「它現在用的是哪一張」，
    不是「它可不可信」。"""
    # 先解析並檢查位址（core/net_guard），再連「檢查過的那個位址」：不然 DNS 在檢查與連線之間換掉就繞過了
    from app.core import net_guard
    _PROBE_ERRORS.pop((host, port), None)
    try:
        addrs = await net_guard.acheck_target(host, port)
    except UnsafeOutboundURL as exc:
        _why(host, port, str(exc))
        return None
    if not addrs:
        _why(host, port, f"no address for {host}")
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(str(addrs[0]), port, ssl=ctx, server_hostname=host), timeout)
    except TimeoutError:
        _why(host, port, f"no answer from {addrs[0]}:{port} within {timeout:g}s")
        return None
    except ssl.SSLError as exc:
        _why(host, port, f"TLS handshake failed: {exc.reason or exc}")
        return None
    except OSError as exc:
        _why(host, port, f"{addrs[0]}:{port}: {exc.strerror or exc}")
        return None
    try:
        der = w.get_extra_info("ssl_object").getpeercert(binary_form=True)
    finally:
        w.close()
    return hashlib.sha256(der).hexdigest() if der else None


def _unreachable(host: str, port: int) -> str:
    why = probe_error(host, port)
    return f"unreachable ({why})" if why else "unreachable"


async def _current(session: AsyncSession, cert_id: Any) -> CertVersion | None:
    return (await session.execute(select(CertVersion).where(
        CertVersion.certificate_id == cert_id, CertVersion.is_current.is_(True)))).scalar_one_or_none()


async def _bundle(session: AsyncSession, cert: Certificate, ver: CertVersion) -> Bundle:
    from app.services.cert_fetch import _key_aad
    key = decrypt_secret(ver.key_enc, ver.key_nonce, aad=_key_aad(cert.id, ver.fingerprint_sha256)).decode()
    return Bundle(cert_name=cert.name, fingerprint=ver.fingerprint_sha256, cert_pem=ver.cert_pem,
                  chain_pem=ver.chain_pem or "", key_pem=key)


async def push_target(session: AsyncSession, target: CertPushTarget, *, actor_user_id: Any = None,
                      request_id: str | None = None, trigger: str = "manual",
                      probe=presented_fingerprint, progress: Any = None) -> dict[str, Any]:
    """推一個目標並記錄結果。回 {status, fingerprint, steps, error}。呼叫端負責 commit。

    progress(items, stage)：每完成一步、每換一個階段就叫一次（背景作業拿來即時顯示做到哪裡）。"""
    steps = Steps(listener=progress)
    status, error, fp = "error", None, None
    from sqlalchemy import text
    got = (await session.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": lock_key(target.id)})).scalar()
    if not got:
        # 另一個推送（手動或排程）正在推同一台：不碰設備、不改紀錄
        return {"status": "busy", "fingerprint": None, "error": "certpush_busy", "steps": []}
    cert = await session.get(Certificate, target.certificate_id)
    ver = await _current(session, target.certificate_id) if cert else None
    try:
        if cert is None or ver is None:
            raise PushError("load", "certpush_no_version")
        fp = ver.fingerprint_sha256
        bundle = await _bundle(session, cert, ver)
        driver_cls = DRIVERS.get(target.kind)
        if driver_cls is None:
            raise PushError("load", "certpush_unknown_kind", target.kind)
        driver = driver_cls(make_http(target, await load_secret(session, target.id)), target.options or {})
        api = urlsplit(target.api_url)
        api_ep = ((api.hostname or ""), (api.port or 443))
        custom = _custom_endpoint(target)
        if hasattr(driver, "verify_endpoints"):
            eps = driver.verify_endpoints(api_ep, custom)
            skipped = driver.unverifiable(custom)
        else:
            eps, skipped = [("service", *verify_address(target))], []
        verify = (target.options or {}).get("verify", True) is not False
        soft = (driver.soft_verify_endpoints(api_ep, custom)
                if verify and not eps and hasattr(driver, "soft_verify_endpoints") else [])
        before: dict[str, str | None] = {}
        if verify and eps:
            steps.stage("probe_before")
        for label, host, port in (eps if verify else []):
            before[label] = await probe(host, port)
            if before[label]:
                steps.ok("probe_before", f"{label} {host}:{port} reachable")
            else:
                # 連不到不打勾，寫出原因（2026-11-07 截圖：「✓ 推之前先連一次（… unreachable）」）
                steps.warn("probe_before", "certpush_probe_unreachable",
                           f"{label} {host}:{port} {_unreachable(host, port)}")
        steps.stage("push")
        rollback = await driver.push(bundle, steps)
        steps.stage("verify")
        if getattr(driver, "staged", False):
            status = "staged"
        elif rollback is None or not verify or not eps:
            # 沒確認到的要講清楚為什麼，不可以打勾（以前全部 steps.ok，狀態寫未確認、明細卻全是綠勾）
            status = "unverified"
            if not verify:
                steps.warn("verify", "certpush_verify_disabled", "verify turned off for this target")
            elif soft and rollback is not None:
                # 軟性確認（見 OPNsenseDriver.soft_verify_endpoints）：對得上算確認，對不上只警示、不換回
                good = True
                for label, host, port in soft:
                    seen = None
                    for i in range(VERIFY_TRIES):
                        seen = await probe(host, port)
                        if seen == fp:
                            break
                        if i < VERIFY_TRIES - 1:
                            await asyncio.sleep(VERIFY_INTERVAL_S)
                    if seen == fp:
                        steps.ok("verify", f"{label} {host}:{port} presents {fp[:16]}")
                    else:
                        steps.warn("verify", "certpush_verify_webgui_old",
                                   f"{label} {host}:{port} presents {(seen or 'nothing')[:16]}; not rolled back")
                        good = False
                status = "ok" if good else "unverified"
            else:
                if rollback is None and eps:
                    steps.warn("verify", "certpush_verify_no_rollback", "nothing to roll back to; not verified")
                for s in skipped or ([] if eps else ["service"]):
                    steps.warn("verify", "certpush_verify_no_address", f"{s}: no verify address; not verified")
        else:
            failed, unknown = [], []
            for label, host, port in eps:
                seen = None
                for i in range(VERIFY_TRIES):
                    seen = await probe(host, port)
                    if seen == fp:
                        break
                    if i < VERIFY_TRIES - 1:
                        await asyncio.sleep(VERIFY_INTERVAL_S)
                if seen == fp:
                    steps.ok("verify", f"{label} {host}:{port} presents {fp[:16]}")
                elif before.get(label) is None and seen is None:
                    why = probe_error(host, port)
                    steps.warn("verify", "certpush_verify_unreachable",
                               f"{label} {host}:{port} unreachable before and after"
                               + (f" ({why})" if why else "") + "; not verified")
                    unknown.append(label)
                else:
                    steps.fail("verify", "certpush_verify_failed",
                               f"{label} {host}:{port} presents {(seen or 'nothing')[:16]}")
                    failed.append(label)
            for s in skipped:
                steps.warn("verify", "certpush_verify_no_address", f"{s}: no verify address; not verified")
            if failed:
                steps.stage("rollback")
                try:
                    await rollback()
                    steps.ok("rollback")
                except PushError as exc:
                    steps.fail("rollback", exc.code, exc.detail)
                    status, error = "error", "certpush_rollback_failed"
                else:
                    # 換回去之後確認真的回到原本那張（以前只看 API 有沒有回成功）
                    back = await _confirm_restored(eps, before, probe, steps)
                    status, error = ("rolled_back", "certpush_verify_failed") if back \
                        else ("error", "certpush_rollback_unconfirmed")
            elif unknown or skipped:
                status = "unverified"
            else:
                status = "ok"
    except PushError as exc:
        steps.fail(exc.step, exc.code, exc.detail)
        status, error = "error", exc.code

    target.last_push_at = datetime.now(UTC)
    target.last_status = status
    target.last_error = error
    target.last_steps = steps.items
    if status in ("ok", "unverified", "staged"):
        target.last_fingerprint = fp            # 自動推送不再重推同一版；畫面另依狀態寫「未確認 / 待 commit」
    await _audit_and_notify(session, target, cert, status, fp, error, actor_user_id, request_id, trigger)
    return {"status": status, "fingerprint": fp, "error": error, "steps": steps.items}


async def _confirm_restored(eps: list[tuple[str, str, int]], before: dict[str, str | None], probe: Any,
                            steps: Steps) -> bool:
    """換回去之後：推之前連得到的位址，要再出示原本那張才算換回。"""
    ok = True
    for label, host, port in eps:
        want = before.get(label)
        if want is None:
            continue
        seen = None
        for i in range(ROLLBACK_TRIES):
            seen = await probe(host, port)
            if seen == want:
                break
            if i < ROLLBACK_TRIES - 1:
                await asyncio.sleep(VERIFY_INTERVAL_S)
        if seen == want:
            steps.ok("rollback_verify", f"{label} {host}:{port} presents the original {want[:16]}")
        else:
            steps.fail("rollback_verify", "certpush_rollback_unconfirmed",
                       f"{label} {host}:{port} presents {(seen or 'nothing')[:16]}")
            ok = False
    return ok


async def _audit_and_notify(session: AsyncSession, target: CertPushTarget, cert: Certificate | None, status: str,
                            fp: str | None, error: str | None, actor_user_id: Any, request_id: str | None,
                            trigger: str) -> None:
    from app.core.audit import append_audit
    from app.services.notification import notify_admins_event
    await append_audit(
        session, actor_user_id=str(actor_user_id) if actor_user_id else None, actor_ip=None,
        actor_user_agent=f"cert-push/{trigger}", object_type="cert_push_target", object_id=str(target.id),
        action="cert_push", diff={"target": target.name, "kind": target.kind, "certificate": cert.name if cert else None,
                                  "fingerprint": fp, "status": status, "error": error},
        request_id=request_id)
    if status in ("error", "rolled_back"):
        await notify_admins_event(
            session, event="cert.push_failed", severity="warning",
            title=f"憑證推送失敗：{target.name}", body=f"{cert.name if cert else ''} → {target.kind} {target.api_url}（{error}）",
            link="/certificates", title_key="notif.cert_push_failed",
            params={"target": target.name, "certificate": cert.name if cert else "", "error": error or ""})
    elif status == "ok":
        await notify_admins_event(
            session, event="cert.deployed", severity="info",
            title=f"憑證已推送：{target.name}", body=f"{cert.name if cert else ''} → {target.kind} {target.api_url}",
            link="/certificates", title_key="notif.cert_pushed",
            params={"target": target.name, "certificate": cert.name if cert else ""})


async def due_targets(session: AsyncSession, now: datetime | None = None) -> list[CertPushTarget]:
    """自動推送：目標上的版本不是目前版本；上次失敗的隔 RETRY_AFTER 才再試。"""
    now = now or datetime.now(UTC)
    rows = (await session.execute(select(CertPushTarget, CertVersion.fingerprint_sha256).join(
        CertVersion, (CertVersion.certificate_id == CertPushTarget.certificate_id) & CertVersion.is_current.is_(True)
    ).where(CertPushTarget.enabled.is_(True), CertPushTarget.auto_push.is_(True)))).all()
    out = []
    for t, fp in rows:
        if t.last_fingerprint == fp:
            continue
        if t.last_status in ("error", "rolled_back") and t.last_push_at and now - t.last_push_at < RETRY_AFTER:
            continue
        out.append(t)
    return out
