"""IP 變更評估：pfSense 同步進來的 NAT port forward 也要比對（2026-11-04，部落格查證時發現）。

pfSense 在「同步規則」打開時會把 NAT port forward 同步進 nat_translations（source_origin=pfsense:<id>），
但變更評估的來源清單沒把 pfSense 算成 NAT 來源：環境裡只有 pfSense 的 NAT、又沒有手動 NAT 時，
整個 NAT 檢查直接跳過，改 IP 時不會提醒「這個位址有 port forward」。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.models.nat import NATTranslation
from app.models.pfsense import PfSenseFirewall
from tests.test_change_impact_engine import _gap_codes, _ip, _net, _run


async def _pfsense(db, *, sync_rules: bool) -> PfSenseFirewall:  # type: ignore[no-untyped-def]
    fw = PfSenseFirewall(name=f"pfs-{uuid.uuid4().hex[:6]}", api_url="https://192.0.2.5", api_key_enc=b"x",
                         api_key_nonce=b"x", sync_rules=sync_rules, last_sync_at=datetime.now(UTC))
    db.add(fw)
    await db.flush()
    return fw


async def test_pfsense_port_forward_is_checked(db_session, admin_user) -> None:
    sub = await _net(db_session)
    root = await _ip(db_session, sub, "198.51.100.10")
    fw = await _pfsense(db_session, sync_rules=True)
    db_session.add(NATTranslation(name="Web", type="port_forward", dst_ip_id=root.id, protocol="tcp", dst_port=443,
                                  source_origin=f"pfsense:{fw.id}"))
    res = await _run(db_session, admin_user, root, "198.51.100.80")
    nat = [f for f in res.findings if f.rule_id == "nat.translation"]
    assert nat, "pfSense 的 port forward 要列出來"
    assert fw.name in nat[0].subject_label
    assert ("nat", "not_configured") not in {(g.category, g.reason_code) for g in res.gaps}


async def test_pfsense_without_rule_sync_is_not_a_nat_source(db_session, admin_user) -> None:
    """NAT 跟著「同步規則」一起同步；沒開就不算這台會提供 NAT（不然會被當成有來源卻沒資料）。"""
    sub = await _net(db_session)
    root = await _ip(db_session, sub, "198.51.100.11")
    await _pfsense(db_session, sync_rules=False)
    res = await _run(db_session, admin_user, root, "198.51.100.81")
    assert "not_configured" in _gap_codes(res)
