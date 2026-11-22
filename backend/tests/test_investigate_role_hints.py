"""判讀之前，先把「這台在扮演什麼角色」算出來給模型。

實機回報：一台反向代理主機有 20 筆 A 記錄指向它，AI 判讀把這件事講成「DNS 記錄與
主機名稱來源之間存在顯著的矛盾」。**那不是模型的錯** —— 我們送過去的只是一串域名，
沒有任何訊號說「多個域名指向同一個位址對反向代理是正常的」，而提示詞又特別要求它
「指出矛盾」。於是它照做了，只是指錯了地方。

要修的是我們這端：把可以從事實算出來的角色訊號先算好，並在提示詞裡寫明哪些樣態
是常態、不要當成矛盾。
"""
from __future__ import annotations

import pytest
from app.services.investigate import infer_role_hints


def _dossier(**kw):
    base = {"found": True, "ip": "192.0.2.10", "hostname": "proxy-01",
            "dns": [], "nat": [], "firewall": [], "arp": [], "hostname_sources": []}
    base.update(kw)
    return base


def test_many_names_on_one_address_is_recognised_as_a_shared_entry_point():
    d = _dossier(dns=[{"rtype": "A", "name": f"n{i}.example.com"} for i in range(20)])
    hints = infer_role_hints(d)
    assert any("reverse proxy" in h.lower() or "shared" in h.lower() for h in hints)
    assert any("20" in h for h in hints)


def test_a_single_name_is_not_called_a_reverse_proxy():
    """一個域名就是一台普通主機 —— 不能因為有 DNS 記錄就亂貼標籤。"""
    assert not any("proxy" in h.lower()
                   for h in infer_role_hints(_dossier(dns=[{"rtype": "A", "name": "a.example.com"}])))


def test_web_ports_forwarded_from_outside_is_flagged():
    d = _dossier(nat=[{"port": 443, "protocol": "tcp"}, {"port": 80, "protocol": "tcp"}])
    assert any("80" in h or "443" in h for h in infer_role_hints(d))


def test_hints_are_facts_not_verdicts():
    """訊號只描述觀察到的樣態，不下「安全 / 不安全」或「設定錯誤」的判斷。"""
    d = _dossier(dns=[{"rtype": "A", "name": f"n{i}.example.com"} for i in range(30)],
                 nat=[{"port": 443}])
    joined = " ".join(infer_role_hints(d)).lower()
    for word in ("insecure", "misconfigur", "should ", "risk"):
        assert word not in joined


def test_no_signal_means_no_hint():
    assert infer_role_hints(_dossier()) == []


def test_the_prompt_tells_the_model_not_to_report_normal_patterns_as_contradictions():
    """光算出訊號還不夠 —— 提示詞本來就寫著「特別指出矛盾」，得同時告訴它哪些不是。"""
    from app.api.v1.endpoints.investigate import _prompt
    d = _dossier(dns=[{"rtype": "A", "name": f"n{i}.example.com"} for i in range(20)])
    zh = _prompt(d, "zh-TW")
    assert "角色訊號" in zh
    assert "反向代理" in zh
    assert "不要當成矛盾" in zh
    en = _prompt(d, "en-US")
    assert "Role signals" in en and "reverse proxy" in en.lower()


def test_a_plain_host_gets_no_role_signal_block():
    from app.api.v1.endpoints.investigate import _prompt
    assert "角色訊號" not in _prompt(_dossier(), "zh-TW")


def test_dhcp_lease_with_a_random_mac_means_changes_are_expected():
    """2026-10-05：調查多了 DHCP 與網卡的事實。有租約、MAC 又是隨機的 → 位址換人、MAC 換掉都是常態。"""
    d = _dossier(dhcp={"in_lease": True, "reserved": False, "in_pool": True},
                 identity={"mac_random": True})
    hints = infer_role_hints(d)
    assert any("randomized" in h and "DHCP" in h for h in hints)
    joined = " ".join(hints).lower()
    for word in ("insecure", "misconfigur", "risk"):
        assert word not in joined


def test_pool_without_reservation_is_a_normal_dhcp_range():
    d = _dossier(dhcp={"in_lease": False, "reserved": False, "in_pool": True})
    assert any("DHCP pool" in h for h in infer_role_hints(d))


def test_a_reserved_address_gets_no_dhcp_hint():
    """固定分配的位址不會換人：不可以替「換了 MAC」找理由。"""
    d = _dossier(dhcp={"in_lease": True, "reserved": True, "in_pool": True,
                       "reservations": [{"mac": "00:11:22:33:44:55"}]})
    assert not any("DHCP" in h for h in infer_role_hints(d))


def test_the_prompt_explains_hostname_spelling_and_uses_the_compact_view():
    """提示詞要說明主機名稱的寫法差異不算矛盾，送出的檔案是精簡版（沒有內部識別碼）。"""
    from app.api.v1.endpoints.investigate import _prompt
    d = _dossier(address={"ip_address_id": "8b0f4a8e-2f43-4a51-9d9c-3f0a6b1b2c3d", "hostname": "h"},
                 conflicts=[{"code": "names", "params": {"n": 2}}])
    zh = _prompt(d, "zh-TW")
    assert "conflicts" in zh
    assert "結尾的點" in zh
    assert "8b0f4a8e-2f43-4a51-9d9c-3f0a6b1b2c3d" not in zh
    assert "trailing dot" in _prompt(d, "en-US")


@pytest.mark.anyio
async def test_check_ip_exposure_reads_the_dossier_keys(monkeypatch):
    """check_ip_exposure 以前讀 `firewall`／`hostname`（檔案裡沒有這兩個鍵），放行規則與主機名稱永遠是空的。"""
    from app.mcp import tools

    async def fake(_s, *, user, ip):
        return {"found": True, "ip": ip, "address": {"hostname": "web01"},
                "nat": [{"port": 443}],
                "firewall_rules": [{"action": "pass", "port": "22"}, {"action": "block", "port": "23"}]}

    monkeypatch.setattr("app.services.investigate.collect_dossier", fake)
    out = await tools.check_ip_exposure(None, None, "192.0.2.10")  # type: ignore[arg-type]
    assert out["hostname"] == "web01"
    assert [r["port"] for r in out["firewall_allow_rules"]] == ["22"]
    assert out["open_ports"] == ["22", "443"]
