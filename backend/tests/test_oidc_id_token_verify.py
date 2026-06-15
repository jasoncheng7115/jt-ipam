"""OIDC ID Token 簽章驗證測試（OWASP A07）。

對應修補：sso.py 不再 base64 解開 ID Token 就信任其 claims（groups → admin 提權）；
改用 oidc_service.verify_id_token() 以 provider JWKS 驗簽 + 檢 aud/iss/nonce。
"""

from __future__ import annotations

import json
import time

import jwt
import pytest
from app.services import oidc as oidc_svc
from cryptography.hazmat.primitives.asymmetric import rsa

KID = "test-key-1"
AUD = "jt-ipam-client"
ISS = "https://idp.example.com"


class _Cfg:
    client_id = AUD


def _keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(priv.public_key()))
    jwk.update({"kid": KID, "alg": "RS256", "use": "sig"})
    return priv, {"keys": [jwk]}


def _sign(priv, claims: dict, kid: str = KID) -> str:
    return jwt.encode(claims, priv, algorithm="RS256", headers={"kid": kid})


def _patch(monkeypatch, jwks):
    async def fake_discover(cfg):
        return oidc_svc.OIDCDiscovery(
            authorization_endpoint=f"{ISS}/auth", token_endpoint=f"{ISS}/token",
            userinfo_endpoint=f"{ISS}/userinfo", jwks_uri=f"{ISS}/jwks", issuer=ISS,
        )

    class _Resp:
        status_code = 200

        def json(self):
            return jwks

    async def fake_safe_request(method, url, **kw):
        return _Resp()

    monkeypatch.setattr(oidc_svc, "discover", fake_discover)
    monkeypatch.setattr(oidc_svc, "safe_request", fake_safe_request)


def _claims(**extra):
    now = int(time.time())
    base = {"sub": "u1", "aud": AUD, "iss": ISS, "iat": now, "exp": now + 600}
    base.update(extra)
    return base


async def test_valid_id_token_returns_claims(monkeypatch):
    priv, jwks = _keypair()
    _patch(monkeypatch, jwks)
    token = _sign(priv, _claims(groups=["admins"], nonce="n1"))
    claims = await oidc_svc.verify_id_token(_Cfg(), token, nonce="n1")
    assert claims["groups"] == ["admins"]
    assert claims["sub"] == "u1"


async def test_token_signed_by_wrong_key_rejected(monkeypatch):
    _, jwks = _keypair()            # 對外公告的 JWKS
    attacker_priv, _ = _keypair()  # 攻擊者自己的私鑰（不在 JWKS 裡）
    _patch(monkeypatch, jwks)
    forged = _sign(attacker_priv, _claims(groups=["admins"]))
    with pytest.raises(oidc_svc.OIDCError):
        await oidc_svc.verify_id_token(_Cfg(), forged)


async def test_wrong_audience_rejected(monkeypatch):
    priv, jwks = _keypair()
    _patch(monkeypatch, jwks)
    token = _sign(priv, _claims(aud="some-other-client"))
    with pytest.raises(oidc_svc.OIDCError):
        await oidc_svc.verify_id_token(_Cfg(), token)


async def test_nonce_mismatch_rejected(monkeypatch):
    priv, jwks = _keypair()
    _patch(monkeypatch, jwks)
    token = _sign(priv, _claims(nonce="real"))
    with pytest.raises(oidc_svc.OIDCError):
        await oidc_svc.verify_id_token(_Cfg(), token, nonce="attacker")


async def test_unknown_kid_rejected(monkeypatch):
    priv, jwks = _keypair()
    _patch(monkeypatch, jwks)
    token = _sign(priv, _claims(), kid="nonexistent-kid")
    with pytest.raises(oidc_svc.OIDCError):
        await oidc_svc.verify_id_token(_Cfg(), token)
