"""SAML 整合：設定載入 / metadata 端點 / 未設定錯誤路徑。

這些測試不需 IdP，只跑 SP-side 的 settings 組裝與 metadata XML 產出。
"""

from __future__ import annotations

import pytest


@pytest.fixture
def saml_off_settings(monkeypatch):  # type: ignore[no-untyped-def]
    """強制 saml_enabled=False 看到的回應。"""
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_saml_config_fields_default():
    from app.core.config import get_settings
    get_settings.cache_clear()
    s = get_settings()
    assert s.saml_enabled is False
    assert s.saml_admin_groups == []
    assert s.saml_attr_username == "uid"
    assert s.saml_want_assertions_signed is True


async def test_saml_disabled_returns_503():
    from httpx import ASGITransport, AsyncClient

    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/v1/auth/saml/metadata")
        assert r.status_code == 503
        r2 = await c.get("/api/v1/auth/saml/login")
        assert r2.status_code == 503


async def test_saml_metadata_xml_when_configured(monkeypatch):  # type: ignore[no-untyped-def]
    """saml_enabled=True 但沒給 IdP metadata → 503 / NotConfigured。"""
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setenv("SAML_ENABLED", "true")
    from app.core.config import get_settings
    get_settings.cache_clear()
    try:
        from app.main import create_app
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/v1/auth/saml/metadata")
            # 沒設 IdP metadata URL/XML → 503
            assert r.status_code == 503
            assert "metadata" in r.text.lower() or "saml" in r.text.lower()
    finally:
        monkeypatch.delenv("SAML_ENABLED", raising=False)
        get_settings.cache_clear()


async def test_saml_metadata_with_inline_idp_xml(monkeypatch):  # type: ignore[no-untyped-def]
    """貼上 IdP metadata XML → SP metadata 應產出。"""
    from httpx import ASGITransport, AsyncClient

    # 從 OneLogin demo IdP metadata（單一 cert）— 純 SP-side 測試足夠
    idp_xml = '''<?xml version="1.0"?>
<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="https://idp.example.com/saml">
  <IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <KeyDescriptor use="signing">
      <KeyInfo xmlns="http://www.w3.org/2000/09/xmldsig#">
        <X509Data>
          <X509Certificate>MIIDpDCCAoygAwIBAgIGAVwYDgKVMA0GCSqGSIb3DQEBCwUAMIGSMQswCQYDVQQGEwJVUzETMBEG
A1UECAwKQ2FsaWZvcm5pYTEWMBQGA1UEBwwNU2FuIEZyYW5jaXNjbzENMAsGA1UECgwET2t0YTEU
MBIGA1UECwwLU1NPUHJvdmlkZXIxEzARBgNVBAMMCmRldi04OTU0NTAxHDAaBgkqhkiG9w0BCQEW
DWluZm9Ab2t0YS5jb20wHhcNMTcwNTA0MTAxNDM3WhcNMjcwNTA0MTAxNTM3WjCBkjELMAkGA1UE
BhMCVVMxEzARBgNVBAgMCkNhbGlmb3JuaWExFjAUBgNVBAcMDVNhbiBGcmFuY2lzY28xDTALBgNV
BAoMBE9rdGExFDASBgNVBAsMC1NTT1Byb3ZpZGVyMRMwEQYDVQQDDApkZXYtODk1NDUwMRwwGgYJ
KoZIhvcNAQkBFg1pbmZvQG9rdGEuY29tMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA
lNjvmfyxq0DTUqPKHzdNnaWO4bD9q1AcvszRn6BcHEsrFOxz7pFY0wn+m0TwqvTBSp9ZkmjeJg5R
8ZTPXBxzlF8IWSwQ7g8zMaMP44ekc9CDOVJJjm2GpPYn/kDJxGCkdjgpEgU2JvRY7n1KNRkKL5sk
vG4CV98Qg1/SQMkXt9nhNJZS8sM0i7Zp/MxnETUYLyOMOdRC9lQs2W8Wh5vWiWlQ6c2g//Hv6IXc
EWRVvsOWvOh8pFXkNrK2k1A5Nw0Smz4kK5qZqAMjGm4aR4Ag3vZ4KDRtfuRWtrAzM7cZ1IXVk3do
oBsyYMlmtqCB7PLJYlcvLN+FQIQfzOJTYJzfGwIDAQABMA0GCSqGSIb3DQEBCwUAA4IBAQAxYHKZ
5p9kT+6WqSGzCiCh4xnqKOhNLcb0W3/QHeFfV8Q6w5DHH4qpw7vrQ5aBKTVL/o6rH1kP8jFbN6KP
+FbJ8z5nRXdKxhWKhLeFW0xZrMuMKtRWJYFfpIB2L7BJq9Rvbt9w4K/mJZFqGpFdDRZMQg9KZzlX
b0F6XlCVJ4r/4Q1jJZJqNm1RvDk5Kv8q0bXJPqgsHpQJQ/Rx5yqe3CdM7L/ihfpSI4jM4mDp8aJV
N6kT9XpFbJqx5W5z1+P93yNDRQeFuZVOIQOuN7BQIqWvN1dZDc1oZPJnRkRwCXiUcJ8/iGNLvLED
M0F98BkQ6Ej3AALmYgPC0Pq0AmRQwHy</X509Certificate>
        </X509Data>
      </KeyInfo>
    </KeyDescriptor>
    <SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://idp.example.com/saml/sso"/>
  </IDPSSODescriptor>
</EntityDescriptor>'''
    monkeypatch.setenv("SAML_ENABLED", "true")
    monkeypatch.setenv("SAML_IDP_METADATA_XML", idp_xml)
    from app.core.config import get_settings
    get_settings.cache_clear()
    # idp metadata 解析 cache
    from app.services import saml as saml_service
    saml_service._idp_cache.clear()
    try:
        from app.main import create_app
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/v1/auth/saml/metadata")
            assert r.status_code == 200, r.text[:200]
            assert r.headers["content-type"].startswith("application/samlmetadata+xml")
            xml = r.text
            assert "EntityDescriptor" in xml
            assert "SPSSODescriptor" in xml
            assert "AssertionConsumerService" in xml
    finally:
        monkeypatch.delenv("SAML_ENABLED", raising=False)
        monkeypatch.delenv("SAML_IDP_METADATA_XML", raising=False)
        get_settings.cache_clear()
        saml_service._idp_cache.clear()
