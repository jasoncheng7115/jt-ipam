"""Checkmk 整合：對真的 Checkmk 跑（2.4 Raw、2.5 Community）。

沒有設定測試靶時整支略過。發版前要跑（TEST_CHECKLIST「Checkmk 真機」）：

    bash scripts/checkmk-test-target/run.sh       # 起 2.4 與 2.5 兩個容器、建測試主機與唯讀自動化帳號，印出下一行
    JTIPAM_TEST_CHECKMK=http://<容器 IP>:5000/cmk,… OUTBOUND_ALLOW_CIDRS=<容器 IP>/32,… pytest tests/test_checkmk_real.py

測試帳號 jt-ipam-ro 是內建 guest 角色的自動化帳號，就是文件建議客戶用的那種。
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.models.checkmk import CheckmkInstance
from app.services import checkmk as svc

pytestmark = pytest.mark.anyio
SITES = [s for s in (os.environ.get("JTIPAM_TEST_CHECKMK") or "").split(",") if s]
needs = pytest.mark.skipif(not SITES, reason="JTIPAM_TEST_CHECKMK not set")


@pytest.fixture(autouse=True)
def _allow_test_target(monkeypatch):
    monkeypatch.setattr("app.core.net_guard.check_target", lambda *a, **k: None)


def _inst(url: str, secret: str = "RoSecret-2026-abcdef", user: str = "jt-ipam-ro") -> CheckmkInstance:
    inst = CheckmkInstance(id=uuid.uuid4(), name="t", api_url=url, api_user=user, verify_tls=False)
    inst.api_secret_enc, inst.api_secret_nonce = svc.encrypt_api_secret(inst.id, secret)
    return inst


@needs
@pytest.mark.parametrize("url", SITES)
async def test_guest_automation_user_reads_every_host(url: str) -> None:
    hc = await svc.healthcheck(_inst(url))
    assert hc["hosts_readable"] is True
    assert hc["host_count"] == 5
    assert hc["version"].startswith(("2.4", "2.5"))
    hosts = {h["name"]: h for h in await svc.list_hosts(_inst(url))}
    assert svc.host_addresses(hosts["web01"]) == ("127.0.0.1", ["127.0.0.1"])
    assert svc.host_addresses(hosts["sw-core"]) == ("192.0.2.1", ["192.0.2.1", "192.0.2.254"])
    assert svc.host_addresses(hosts["dnsonly.example.net"])[0] is None      # 0.0.0.0 佔位
    assert svc._state(hosts["web01"]) == "up"
    assert svc._folder(hosts["web01"]) == "/dc"


@needs
@pytest.mark.parametrize("url", SITES)
async def test_wrong_secret_and_wrong_site(url: str) -> None:
    with pytest.raises(svc.CheckmkError) as e:
        await svc.healthcheck(_inst(url, secret="wrong"))
    assert e.value.code == "cmk_auth"
    with pytest.raises(svc.CheckmkError) as e:
        await svc.healthcheck(_inst(url.rsplit("/", 1)[0] + "/nosite"))
    assert e.value.code == "cmk_no_site"
