"""OCS Inventory NG 整合的解析與比對規則。

樣本取自 2026-09-18 對官方映像檔 2.10／2.11 的實機探測（見 project_ocs_integration_design）。
這個整合最容易出的錯都是**安靜的**：把虛擬網卡的假 MAC 灌進來、拿過期盤點蓋掉正確值、
把同一 MAC 的多台機器亂配、或替 OCS 自己建出髒 IP —— 所以規則要逐條釘住。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.services import ocs as svc

# ─────────────────── 純函式 ───────────────────


@pytest.mark.parametrize(("text", "expected"), [
    ("2.11.0", (2, 11, 0)), ("2.10", (2, 10)), ("2.12.5", (2, 12, 5)),
    ("", ()), (None, ()), ("garbage", ()),
])
def test_parse_version(text: str | None, expected: tuple[int, ...]) -> None:
    assert svc.parse_version(text) == expected


def test_usable_nics_drops_virtual_and_zero_mac() -> None:
    """VPN／docker0（VIRTUALDEV=1）與全零 MAC 不可以進比對 —— 否則假 MAC 灌一堆進來。

    樣本就是實測那台筆電：有線（Down 但有 MAC，要留）、Wi-Fi（Up）、VPN（虛擬、零 MAC）。
    """
    nics = [
        {"DESCRIPTION": "Ethernet", "MACADDR": "aa:bb:cc:00:00:07", "STATUS": "Down",
         "VIRTUALDEV": 0},
        {"DESCRIPTION": "Wi-Fi", "MACADDR": "aa:bb:cc:00:00:08", "STATUS": "Up",
         "VIRTUALDEV": 0},
        {"DESCRIPTION": "VPN adapter", "MACADDR": "00:00:00:00:00:00", "VIRTUALDEV": 1},
        {"DESCRIPTION": "empty", "MACADDR": "", "VIRTUALDEV": 0},
    ]
    macs = [n["MACADDR"] for n in svc.usable_nics(nics)]
    assert macs == ["aa:bb:cc:00:00:07", "aa:bb:cc:00:00:08"]
    assert svc.usable_nics(None) == []


def test_old_agent_in_lxc_marks_the_only_nic_virtual() -> None:
    """舊版 agent（2.4.2 以前）在 LXC 裡把 eth0 標成 VIRTUALDEV=1 —— 容器的網卡背後是 veth。

    全部照「虛擬就丟」的話，這種容器一張網卡都不剩，jt-ipam 永遠對不上它的 IP（2026-09-25
    實際部署時有兩台容器就是這樣，OCS 收到了、jt-ipam 卻顯示沒盤點過）。
    規則：有非虛擬網卡時照舊只用那些；**全部都是虛擬**時，退回用有 MAC、有 IP 的那幾張。
    """
    lxc = [
        {"DESCRIPTION": "eth0", "MACADDR": "00:00:5e:00:53:31", "VIRTUALDEV": 1,
         "IPADDRESS": "198.51.100.67", "TYPE": "ethernet", "STATUS": "Up"},
        {"DESCRIPTION": "eth1", "MACADDR": "00:00:5e:00:53:32", "VIRTUALDEV": 1,
         "IPADDRESS": "203.0.113.67", "TYPE": "ethernet", "STATUS": "Up"},
        {"DESCRIPTION": "tun0", "MACADDR": "", "VIRTUALDEV": 1, "IPADDRESS": "10.8.0.2"},
    ]
    assert [n["DESCRIPTION"] for n in svc.usable_nics(lxc)] == ["eth0", "eth1"]

    # 筆電：有實體網卡時，虛擬的 docker0 仍然丟掉（原本的行為不變）
    laptop = [
        {"DESCRIPTION": "Wi-Fi", "MACADDR": "aa:bb:cc:00:00:08", "VIRTUALDEV": 0,
         "IPADDRESS": "198.51.100.8"},
        {"DESCRIPTION": "docker0", "MACADDR": "02:42:ac:11:00:01", "VIRTUALDEV": 1,
         "IPADDRESS": "172.17.0.1"},
    ]
    assert [n["DESCRIPTION"] for n in svc.usable_nics(laptop)] == ["Wi-Fi"]


def test_parse_os_prefers_comments_and_maps_family() -> None:
    g, f = svc.parse_os({"OSNAME": "Windows", "OSVERSION": "10.0.19045",
                         "OSCOMMENTS": "Windows 10 Pro"})
    assert g == "Windows 10 Pro" and f == "windows"
    g, f = svc.parse_os({"OSNAME": "Linux", "OSVERSION": "6.8.0",
                         "OSCOMMENTS": "Ubuntu 24.04.1 LTS"})
    assert g == "Ubuntu 24.04.1 LTS" and f == "linux"
    # 沒有 comments 時退回 OSNAME + OSVERSION
    g, f = svc.parse_os({"OSNAME": "Linux", "OSVERSION": "6.8.0", "OSCOMMENTS": ""})
    assert g == "Linux 6.8.0" and f == "linux"
    assert svc.parse_os({}) == (None, None)


def test_repair_text_recovers_any_language() -> None:
    # OCS 代理一律送 UTF-8；亂碼是 OCS 資料庫非 UTF-8（latin-1／cp1252）造成的雙重編碼。
    # 還原對任何語系都適用，因為源頭都是 UTF-8。
    texts = [
        "Microsoft Windows 11 專業版",   # 繁中
        "简体中文 系统 网络管理",          # 简中
        "日本語 版 コンピュータ",          # 日文
        "한국어 버전",                     # 韓文
    ]
    for db_enc in ("latin-1", "cp1252"):
        for text in texts:
            try:
                broken = text.encode("utf-8").decode(db_enc)
            except UnicodeDecodeError:
                continue   # cp1252 有少數未定義位元組；latin-1 一定成立
            assert svc._repair_text(broken) == text, (db_enc, text)
    # 走 parse_os 同一條修復
    g, _ = svc.parse_os({"OSNAME": "Windows",
                         "OSCOMMENTS": "Microsoft Windows 11 專業版".encode("utf-8").decode("latin-1")})
    assert g == "Microsoft Windows 11 專業版"


def test_repair_text_leaves_good_text_untouched() -> None:
    for good in ("Windows 10 Pro", "Ubuntu 24.04.1 LTS", "專業版", "cafe",
                 "日本語", "简体中文", "", None):
        assert svc._repair_text(good) == good


def test_decode_json_handles_non_utf8_body() -> None:
    # 合法 UTF-8：正常解。
    good = '{"OSCOMMENTS": "專業版"}'.encode("utf-8")
    assert svc._decode_json(good)["OSCOMMENTS"] == "專業版"
    # 位元組其實是 latin-1 卻被標成 UTF-8：不可炸、要保留位元組讓 _repair_text 事後還原。
    raw = '{"OSCOMMENTS": "café"}'.encode("latin-1")  # 0xe9 單獨出現＝非法 UTF-8
    obj = svc._decode_json(raw)
    assert "OSCOMMENTS" in obj
    # 真的不是 JSON → 拋 OcsError（帶底層原文）
    import pytest as _pytest
    with _pytest.raises(svc.OcsError):
        svc._decode_json(b"<html>not json</html>")


def test_bios_asset_rejects_placeholder_junk() -> None:
    """主機板沒燒 DMI 時的佔位字串不可以當成序號 —— 那會製造一堆假資產。"""
    real = svc.bios_asset([{"SMANUFACTURER": "Dell Inc.", "SMODEL": "OptiPlex 7090",
                            "SSN": "SN-PC001"}])
    assert real == {"vendor": "Dell Inc.", "model": "OptiPlex 7090", "serial": "SN-PC001"}
    junk = svc.bios_asset([{"SMANUFACTURER": "System manufacturer",
                            "SMODEL": "To be filled by O.E.M.", "SSN": "Default string"}])
    assert junk == {"vendor": None, "model": None, "serial": None}
    assert svc.bios_asset([]) == {"vendor": None, "model": None, "serial": None}
    # 佔位前綴黏著真值時，去掉前綴留真值（實機 "To Be Filled By O.E.M. X570D4I-2T"）
    pre = svc.bios_asset([{"SMODEL": "To Be Filled By O.E.M. X570D4I-2T"}])
    assert pre["model"] == "X570D4I-2T"


def test_ocs_tag_agent_notes_extraction() -> None:
    comp = {
        "hardware": {"USERAGENT": "OCS-NG_unified_unix_agent_v2.10.0"},
        "accountinfo": [{"HARDWARE_ID": 2, "TAG": "ABCD1234"}],
        "itmgmt_comments": [
            {"ID": 1, "ACTION": "ADD_NOTE_BY_USER", "USER_INSERT": "admin",
             "DATE_INSERT": "2026-09-18", "COMMENTS": "第一筆"},
            {"ID": 2, "ACTION": "ADD_NOTE_BY_USER", "USER_INSERT": "admin",
             "DATE_INSERT": "2026-09-19",
             # 亂碼（latin1-over-utf8 的「加入資產編碼」）要被修回來
             "COMMENTS": "加入資產編碼".encode("utf-8").decode("latin-1")},
        ],
    }
    assert svc.ocs_tag_of(comp) == "ABCD1234"
    assert svc.ocs_agent_of(comp["hardware"]) == "OCS-NG_unified_unix_agent_v2.10.0"
    notes = svc.ocs_notes_of(comp)
    assert [n["comment"] for n in notes] == ["加入資產編碼", "第一筆"]   # ID 由大到小
    assert notes[0]["user"] == "admin" and notes[0]["action"] == "ADD_NOTE_BY_USER"
    # 預設 TAG "NA" 視為沒有
    assert svc.ocs_tag_of({"accountinfo": [{"TAG": "NA"}]}) is None
    assert svc.ocs_tag_of({}) is None
    assert svc.ocs_notes_of({}) == []


def test_lastdate_and_staleness() -> None:
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    fresh = svc.lastdate_of({"LASTDATE": "2026-09-18 08:00:00"})
    assert fresh == datetime(2026, 9, 18, 8, 0, tzinfo=UTC)
    assert svc.is_stale(fresh, stale_after_days=30, now=now) is False
    old = svc.lastdate_of({"LASTDATE": "2025-08-01 08:00:00"})
    assert svc.is_stale(old, stale_after_days=30, now=now) is True
    assert svc.is_stale(None, stale_after_days=30, now=now) is True
    assert svc.lastdate_of({"LASTDATE": ""}) is None


def test_dedup_sections_drops_the_duplicate_software_key() -> None:
    """/computer/:id 把軟體放在 `software` 與空字串 key 各一份，只留具名的。"""
    got = svc.dedup_sections({"hardware": {"ID": 1}, "software": [1, 2], "": [1, 2]})
    assert "" not in got and got["software"] == [1, 2]


def test_decide_match_only_matches_never_guesses() -> None:
    """只比不建、多筆不猜（比照 Proxmox）。"""
    a, b = uuid.uuid4(), uuid.uuid4()
    idx = {"aabbcc000001": [a], "aabbcc000002": [b, uuid.uuid4()]}
    assert svc.decide_match("aa:bb:cc:00:00:01", idx) == a       # 唯一 → 配
    assert svc.decide_match("aa:bb:cc:00:00:02", idx) is None     # 多筆 → 不猜
    assert svc.decide_match("de:ad:be:ef:00:00", idx) is None     # 查無 → 不建


def test_ocs_is_a_hostname_source() -> None:
    from app.models.ip_hostname import HOSTNAME_SOURCES
    assert "ocs" in HOSTNAME_SOURCES


def test_ocs_inventory_time_is_not_a_liveness_signal() -> None:
    """last_seen_ocs 是顯示用，**絕不可**進上線判定 —— 盤點時間不代表機器還活著。

    守法：effective_status 的計算（librenms.py）不可以參照 last_seen_ocs。
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "app" / "services" / "librenms.py").read_text()
    # effective_status 那段若引用了 last_seen_ocs，就是把盤點時間當活性訊號了
    assert "last_seen_ocs" not in src


# ─────────────────── DB 比對（只比不建） ───────────────────

async def _mk_ip(session, ip_str: str, mac: str, *, device_id=None):
    """建一個掛在真實 section/subnet 底下的 IP（subnet_id 是 NOT NULL）。"""
    from app.models.address import IPAddress
    from app.models.section import Section
    from app.models.subnet import Subnet
    sec = Section(name="t")
    session.add(sec)
    await session.flush()
    net = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    session.add(net)
    await session.flush()
    obj = IPAddress(subnet_id=net.id, ip=ip_str, mac=mac, device_id=device_id)
    session.add(obj)
    await session.flush()
    return obj


def _server(**kw: Any) -> Any:
    from types import SimpleNamespace
    base = {"id": uuid.uuid4(), "name": "ocs", "source_type": "rest",
            "base_url": "https://192.0.2.10", "sync_bios": True, "stale_after_days": 30, "timezone": "UTC"}
    base.update(kw)
    return SimpleNamespace(**base)


def _computer(name: str, mac: str, *, os_comments: str = "Windows 10 Pro",
              lastdate: str = "2026-09-18 08:00:00", serial: str = "SN-X",
              extra_nics: list[dict] | None = None) -> dict[str, Any]:
    nics = [{"MACADDR": mac, "VIRTUALDEV": 0, "IPADDRESS": "198.51.100.41"}]
    nics += extra_nics or []
    return {
        "hardware": {"NAME": name, "OSNAME": "Windows", "OSVERSION": "10.0.19045",
                     "OSCOMMENTS": os_comments, "LASTDATE": lastdate},
        "networks": nics,
        "bios": [{"SMANUFACTURER": "Dell Inc.", "SMODEL": "OptiPlex 7090", "SSN": serial}],
    }


@pytest.mark.anyio
async def test_apply_matches_existing_ip_by_mac_and_fills_identity(db_session) -> None:
    from app.services.arp_precedence import normalize_mac
    ip = await _mk_ip(db_session, "198.51.100.41", "aa:bb:cc:00:00:41")
    idx = {normalize_mac("aa:bb:cc:00:00:41"): [ip.id]}

    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    res = await svc._apply_computer(
        db_session, _server(), _computer("pc-07", "aa:bb:cc:00:00:41"), idx, now)
    await db_session.flush()

    assert res["matched"] == 1
    await db_session.refresh(ip)
    assert ip.os_ocs == "Windows 10 Pro", "OCS 的 OS 要寫進 os_ocs"
    assert ip.os_guess is None, "不可污染掃描代理的 os_guess 欄位"
    assert ip.last_seen_ocs == datetime(2026, 9, 18, 8, 0, tzinfo=UTC)


@pytest.mark.anyio
async def test_apply_never_creates_an_ip(db_session) -> None:
    """OCS 說有這個 MAC，但我們沒有對應的 IP → 什麼都不建。"""
    from app.models.address import IPAddress
    from sqlalchemy import func, select
    before = (await db_session.execute(select(func.count()).select_from(IPAddress))).scalar()
    res = await svc._apply_computer(
        db_session, _server(), _computer("ghost", "de:ad:be:ef:00:00"), {}, datetime.now(UTC))
    await db_session.flush()
    after = (await db_session.execute(select(func.count()).select_from(IPAddress))).scalar()
    assert res["matched"] == 0 and after == before


@pytest.mark.anyio
async def test_apply_skips_ambiguous_mac(db_session) -> None:
    """同一 MAC 對到兩個 IP（複製 VM／重疊網段）→ 一個都不動。"""
    from app.services.arp_precedence import normalize_mac
    a = await _mk_ip(db_session, "198.51.100.41", "aa:bb:cc:00:00:41")
    b = await _mk_ip(db_session, "198.51.100.42", "aa:bb:cc:00:00:41")
    idx = {normalize_mac("aa:bb:cc:00:00:41"): [a.id, b.id]}
    res = await svc._apply_computer(
        db_session, _server(), _computer("dup", "aa:bb:cc:00:00:41"), idx, datetime.now(UTC))
    await db_session.flush()
    assert res["matched"] == 0
    await db_session.refresh(a)
    assert a.os_guess is None and a.last_seen_ocs is None


@pytest.mark.anyio
async def test_stale_inventory_does_not_stamp_last_seen_or_overwrite_serial(db_session) -> None:
    """一年沒盤點的機器：主機名稱仍記（多源保存），但不 stamp 盤點時間、不動裝置序號。"""
    from app.models.device import Device
    from app.services.arp_precedence import normalize_mac
    dev = Device(name="host-old", serial="REAL-SERIAL")
    db_session.add(dev)
    await db_session.flush()
    ip = await _mk_ip(db_session, "198.51.100.99", "aa:bb:cc:00:00:99", device_id=dev.id)
    idx = {normalize_mac("aa:bb:cc:00:00:99"): [ip.id]}

    await svc._apply_computer(
        db_session, _server(),
        _computer("host-old", "aa:bb:cc:00:00:99", lastdate="2025-08-01 08:00:00",
                  serial="STALE-SERIAL"),
        idx, datetime(2026, 9, 18, 12, 0, tzinfo=UTC))
    await db_session.flush()
    await db_session.refresh(ip)
    await db_session.refresh(dev)
    assert ip.last_seen_ocs is None, "過期盤點不該 stamp 成現在還看得到"
    assert dev.serial == "REAL-SERIAL", "過期盤點不該蓋掉裝置既有序號"


@pytest.mark.anyio
async def test_serial_fills_only_empty_device_fields(db_session) -> None:
    """新鮮盤點會補**空的**裝置欄位，但不覆寫已填的。"""
    from app.models.device import Device
    from app.services.arp_precedence import normalize_mac
    dev = Device(name="pc-07", vendor="Acme")     # vendor 已填、serial/model 空
    db_session.add(dev)
    await db_session.flush()
    ip = await _mk_ip(db_session, "198.51.100.41", "aa:bb:cc:00:00:41", device_id=dev.id)
    idx = {normalize_mac("aa:bb:cc:00:00:41"): [ip.id]}

    await svc._apply_computer(
        db_session, _server(), _computer("pc-07", "aa:bb:cc:00:00:41", serial="SN-PC001"),
        idx, datetime(2026, 9, 18, 12, 0, tzinfo=UTC))
    await db_session.flush()
    await db_session.refresh(dev)
    assert dev.serial == "SN-PC001" and dev.model == "OptiPlex 7090"


@pytest.mark.anyio
async def test_serial_replaces_stored_dmi_placeholder(db_session) -> None:
    """舊版同步可能把 DMI 佔位垃圾（"To Be Filled By O.E.M. …"）存進 Device；新鮮盤點要能覆寫掉它，
    但使用者手填的真值不動。"""
    from app.models.device import Device
    from app.services.arp_precedence import normalize_mac
    dev = Device(name="pc-07", model="To Be Filled By O.E.M. X570D4I-2T",
                 serial="REAL-SN", vendor="Acme")
    db_session.add(dev)
    await db_session.flush()
    ip = await _mk_ip(db_session, "198.51.100.41", "aa:bb:cc:00:00:41", device_id=dev.id)
    idx = {normalize_mac("aa:bb:cc:00:00:41"): [ip.id]}
    comp = _computer("pc-07", "aa:bb:cc:00:00:41", serial="SN-PC001")
    comp["bios"] = [{"SMANUFACTURER": "ASRock", "SMODEL": "X570D4I-2T", "SSN": "SN-PC001"}]
    await svc._apply_computer(db_session, _server(), comp, idx,
                             datetime(2026, 9, 18, 12, 0, tzinfo=UTC))
    await db_session.flush()
    await db_session.refresh(dev)
    assert dev.model == "X570D4I-2T"     # 佔位垃圾被覆寫
    assert dev.serial == "REAL-SN"       # 使用者手填的真值保留
    assert dev.vendor == "Acme", "已填的 vendor 不可被覆寫"


@pytest.mark.anyio
async def test_placeholder_cleared_when_ocs_has_no_real_value(db_session) -> None:
    """存的是佔位垃圾、OCS 也給不出真值時（bios 序號也是佔位）→ 清成空，別繼續顯示垃圾。"""
    from app.models.device import Device
    from app.services.arp_precedence import normalize_mac
    dev = Device(name="pc-14", serial="To Be Filled By O.E.M.")
    db_session.add(dev)
    await db_session.flush()
    ip = await _mk_ip(db_session, "198.51.100.41", "aa:bb:cc:00:00:41", device_id=dev.id)
    idx = {normalize_mac("aa:bb:cc:00:00:41"): [ip.id]}
    comp = _computer("pc-14", "aa:bb:cc:00:00:41")
    comp["bios"] = [{"SSN": "To Be Filled By O.E.M."}]   # OCS 的序號也是佔位
    await svc._apply_computer(db_session, _server(), comp, idx,
                             datetime(2026, 9, 18, 12, 0, tzinfo=UTC))
    await db_session.flush()
    await db_session.refresh(dev)
    assert dev.serial is None


@pytest.mark.anyio
async def test_ocs_hostname_does_not_override_a_manual_one(db_session) -> None:
    """人工指定的主機名稱不被 OCS 默默覆蓋（apply_observation 走多源優先序）。"""
    from app.services import hostname as hn
    from app.services.arp_precedence import normalize_mac
    ip = await _mk_ip(db_session, "198.51.100.41", "aa:bb:cc:00:00:41")
    await hn.apply_observation(db_session, ip=ip, source="manual", hostname="the-real-name")
    await db_session.flush()

    idx = {normalize_mac("aa:bb:cc:00:00:41"): [ip.id]}
    await svc._apply_computer(
        db_session, _server(), _computer("ocs-name", "aa:bb:cc:00:00:41"),
        idx, datetime(2026, 9, 18, 12, 0, tzinfo=UTC))
    await db_session.flush()
    await db_session.refresh(ip)
    assert ip.hostname == "the-real-name", "OCS 不可壓過人工名稱"


# ─────────────────── API（CRUD、密碼永不回傳） ───────────────────

@pytest.mark.anyio
async def test_crud_roundtrip_and_password_never_returned(client, auth_headers) -> None:
    # 建立（不帶密碼 —— OCS 預設無驗證）
    r = await client.post("/api/v1/ocs", headers=auth_headers, json={
        "name": "ocs-a", "base_url": "https://192.0.2.10"})
    assert r.status_code == 201, r.text
    body = r.json()
    sid = body["id"]
    assert body["has_password"] is False
    assert body["sync_software"] is False, "軟體區段預設必須是關的"
    assert "api_password" not in body and "api_password_enc" not in body

    # 設密碼 → has_password 變 True，但明文不回
    r = await client.patch(f"/api/v1/ocs/{sid}", headers=auth_headers,
                           json={"api_username": "svc", "api_password": "s3cret-XYZ"})
    assert r.status_code == 200
    body = r.json()
    assert body["has_password"] is True and body["api_username"] == "svc"
    assert "s3cret-XYZ" not in r.text

    # 清單也不外洩密碼
    r = await client.get("/api/v1/ocs", headers=auth_headers)
    assert r.status_code == 200 and "s3cret-XYZ" not in r.text
    assert any(it["id"] == sid for it in r.json()["items"])

    # 清掉憑證（改回無驗證）
    r = await client.patch(f"/api/v1/ocs/{sid}", headers=auth_headers,
                           json={"clear_credentials": True})
    assert r.status_code == 200 and r.json()["has_password"] is False

    # 刪除
    r = await client.delete(f"/api/v1/ocs/{sid}", headers=auth_headers)
    assert r.status_code == 204


@pytest.mark.anyio
async def test_duplicate_name_is_409(client, auth_headers) -> None:
    p = {"name": "ocs-dup", "base_url": "https://192.0.2.11"}
    assert (await client.post("/api/v1/ocs", headers=auth_headers, json=p)).status_code == 201
    assert (await client.post("/api/v1/ocs", headers=auth_headers, json=p)).status_code == 409


@pytest.mark.anyio
async def test_stored_password_decrypts_back(client, auth_headers, db_session) -> None:
    """AAD 綁 id 的加密要能解回原文（換 id 就解不開，AAD 有守住）。"""
    from app.models.ocs import OcsServer
    from app.services import ocs as svc
    r = await client.post("/api/v1/ocs", headers=auth_headers, json={
        "name": "ocs-crypt", "base_url": "https://192.0.2.12",
        "api_username": "u", "api_password": "round-trip-42"})
    sid = r.json()["id"]
    obj = await db_session.get(OcsServer, uuid.UUID(sid))
    assert svc._auth(obj) == ("u", "round-trip-42")


# ─────────────────── HTTP 呼叫路徑（簽名回歸） ───────────────────

class _FakeResp:
    def __init__(self, status: int, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def content(self) -> bytes:
        import json as _json
        return _json.dumps(self._payload).encode("utf-8")

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=None)


@pytest.mark.anyio
async def test_diagnose_uses_the_real_safe_request_signature(monkeypatch) -> None:
    """回歸：`safe_request` 沒有 `auth=`、client 是關鍵字參數。

    原本 diagnose/sync 用了 `safe_request(client, "GET", url, auth=...)` —— 簽名全錯，
    每次呼叫都丟 TypeError，而 diagnose 又把它吞成「連不到這套 OCS」。單元測試當時只測
    純函式與 `_apply_computer`，完全沒碰 HTTP 路徑，所以沒抓到（2026-09-18 實機才爆）。
    這個測試用假的 safe_request 跑一次 diagnose，記下每次呼叫的參數並檢查簽名。
    """
    from types import SimpleNamespace

    import app.services.ocs as ocs

    calls: list[dict] = []

    class _C:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *e):
            return False

    def fake_client(*a, **k):
        return _C()

    async def fake_request(method, url, *, client=None, headers=None, verify=True, **extra):
        calls.append({"method": method, "url": url, "headers": headers or {},
                      "verify": verify, "extra": extra})
        # listID / lastupdate 都回 200 + 空陣列
        return _FakeResp(200, [])

    monkeypatch.setattr(ocs, "safe_client", fake_client)
    monkeypatch.setattr(ocs, "safe_request", fake_request)

    srv = SimpleNamespace(
        id=uuid.uuid4(), name="ocs", source_type="rest",
        base_url="https://192.0.2.10", verify_tls=False,
        api_username="jtipam", api_password_enc=None, api_password_nonce=None)
    out = await ocs.diagnose(srv)

    assert out["reachable"] is True
    assert calls, "diagnose 沒有真的發出任何請求"
    for c in calls:
        assert c["method"] == "GET"
        assert "auth" not in c["extra"], "又用了 safe_request 不支援的 auth= 參數"
    # 有帶帳號時，帶認證的請求要有 Authorization 標頭
    assert any("Authorization" in c["headers"] for c in calls)


@pytest.mark.anyio
async def test_full_sync_pages_and_applies(monkeypatch, db_session) -> None:
    """端到端（假 HTTP）：沒有 lastupdate → 全量分頁 → 比對既有 IP。"""
    import app.services.ocs as ocs

    ip = await _mk_ip(db_session, "198.51.100.41", "aa:bb:cc:00:00:41")

    # 盤點時間要相對於今天：寫死日期過了 stale_after_days 就變成「過期不採用」（2026-10-19 踩到）
    recent = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    page = {"1": {"hardware": {"NAME": "host-x", "OSNAME": "Linux",
                              "OSCOMMENTS": "Ubuntu 24.04 LTS", "LASTDATE": recent},
                  "networks": [{"MACADDR": "aa:bb:cc:00:00:41", "VIRTUALDEV": 0}],
                  "bios": [{"SSN": "SN-1", "SMODEL": "M", "SMANUFACTURER": "V"}]}}

    class _C2:
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return False

    def fake_client(*a, **k):
        return _C2()

    async def fake_request(method, url, *, client=None, headers=None, verify=True, **extra):
        if "lastupdate" in url:
            return _FakeResp(404, None)                 # 這台沒有增量
        if "start=0" in url:
            return _FakeResp(200, page)
        return _FakeResp(200, {})                       # 第二頁空 → 停

    monkeypatch.setattr(ocs, "safe_client", fake_client)
    monkeypatch.setattr(ocs, "safe_request", fake_request)

    from types import SimpleNamespace
    srv = SimpleNamespace(
        id=uuid.uuid4(), name="ocs", source_type="rest", base_url="https://192.0.2.10",
        verify_tls=False, api_username=None, api_password_enc=None, api_password_nonce=None,
        sync_bios=True, stale_after_days=30, timezone="UTC", last_incremental_epoch=None,
        detected_version=None, last_sync_at=None, last_success_at=None,
        last_error="x", last_cost=None)
    summary = await ocs.sync_instance(db_session, srv)
    await db_session.flush()

    assert summary["computers"] == 1 and summary["matched_ips"] == 1
    assert summary["mode"] == "full"
    await db_session.refresh(ip)
    assert ip.os_ocs == "Ubuntu 24.04 LTS"
    assert ip.last_seen_ocs is not None


@pytest.mark.anyio
async def test_ocs_os_outranks_scanner_guess(db_session) -> None:
    """agent 回報的 OS 要蓋過 nmap 的指紋猜測（Win11 被掃成 XP 的實際情況）。"""
    from app.services import os_precedence
    ip = await _mk_ip(db_session, "198.51.100.54", "aa:bb:cc:00:00:54")
    ip.os_guess = "Microsoft Windows XP SP3 (90%)"   # 掃描代理誤判
    ip.os_ocs = "Microsoft Windows 11"               # OCS agent 回報
    await db_session.flush()
    eff = await os_precedence.effective_os(db_session, ip)
    assert eff["os_source"] == "ocs"
    assert eff["os_guess"] == "Microsoft Windows 11"
    assert eff["os_family"] == "windows"


@pytest.mark.anyio
async def test_scanner_still_wins_when_ocs_has_nothing(db_session) -> None:
    ip = await _mk_ip(db_session, "198.51.100.55", "aa:bb:cc:00:00:55")
    from app.services import os_precedence
    ip.os_guess = "Ubuntu 24.04"
    await db_session.flush()
    eff = await os_precedence.effective_os(db_session, ip)
    assert eff["os_source"] == "scanner" and eff["os_guess"] == "Ubuntu 24.04"


@pytest.mark.anyio
async def test_device_integrations_exposes_ocs_block(client, auth_headers, db_session) -> None:
    """裝置明細的 /integrations 要回一個 ocs 區塊：作業系統／盤點時間＋序號型號廠牌。

    OCS 沒有自己的每台記錄表，是把資料補進 IP（os_ocs / last_seen_ocs / ocs_hw）與裝置
    （serial / model / vendor）。有 IP 被盤點過就算此裝置在 OCS 有資料。
    卡片的製造商／型號／序號顯示 OCS 自己回報的（ocs_hw），不是裝置欄位 —— 裝置欄位
    這裡刻意放別的來源的值（2026-09-27 實機：LibreNMS 填的「windows／Intel x64」）。
    """
    from datetime import UTC, datetime

    from app.models.device import Device
    dev = Device(name="pc-ocs", model="Intel x64", vendor="windows")
    db_session.add(dev)
    await db_session.flush()
    from app.models.ocs import OcsServer
    db_session.add(OcsServer(name="ocs-t", source_type="rest",
                             base_url="https://ocs.example.com", enabled=True))
    ip = await _mk_ip(db_session, "198.51.100.41", "aa:bb:cc:00:00:41", device_id=dev.id)
    ip.os_ocs = "Windows 11 Pro"
    ip.last_seen_ocs = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)
    ip.ocs_id = 42
    ip.ocs_tag = "ASSET-000042"
    ip.ocs_agent = "OCS-NG_unified_unix_agent_v2.10.0"
    ip.ocs_notes = [{"date": "2026-09-19", "user": "admin", "comment": "加入資產編碼",
                     "action": "ADD_NOTE_BY_USER"}]
    ip.ocs_hw = {"system": {"vendor": "Dell Inc.", "model": "OptiPlex", "serial": "SN-OCS-1",
                            "chassis": "Desktop"},
                 "board": {"vendor": None, "model": None, "serial": None}}
    await db_session.commit()

    r = await client.get(f"/api/v1/devices/{dev.id}/integrations", headers=auth_headers)
    assert r.status_code == 200, r.text
    ocs = r.json()["ocs"]
    assert ocs is not None
    assert ocs["os"] == "Windows 11 Pro"
    assert ocs["last_inventory"] is not None
    assert ocs["serial"] == "SN-OCS-1"
    assert ocs["model"] == "OptiPlex"
    assert ocs["vendor"] == "Dell Inc."
    assert ocs["tag"] == "ASSET-000042"
    assert ocs["agent"] == "OCS-NG_unified_unix_agent_v2.10.0"
    assert ocs["notes"][0]["comment"] == "加入資產編碼"
    assert ocs["url"] == "https://ocs.example.com/ocsreports/index.php?function=computer&systemid=42"


@pytest.mark.anyio
async def test_device_integrations_ocs_absent_without_inventory(client, auth_headers, db_session) -> None:
    """沒被 OCS 盤點過的裝置，ocs 區塊為 None（不憑空冒出來）。"""
    from app.models.device import Device
    dev = Device(name="pc-plain")
    db_session.add(dev)
    await db_session.flush()
    await _mk_ip(db_session, "198.51.100.42", "aa:bb:cc:00:00:42", device_id=dev.id)
    await db_session.commit()

    r = await client.get(f"/api/v1/devices/{dev.id}/integrations", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["ocs"] is None


# ─────────────────── AI / MCP 工具 ───────────────────

@pytest.mark.anyio
async def test_mcp_list_ocs_computers(db_session, admin_user) -> None:
    """OCS 盤點資料要問得到，且只列「被 OCS 盤點過」的 IP，並回 scope/count。"""
    from datetime import UTC, datetime, timedelta

    from app.mcp.tools import list_ocs_computers
    ip = await _mk_ip(db_session, "198.51.100.41", "aa:bb:cc:00:00:41")
    ip.ocs_id = 7
    ip.ocs_tag = "ASSET-000114"
    ip.ocs_agent = "OCS-NG_unified_unix_agent_v2.10.0"
    ip.last_seen_ocs = datetime.now(UTC)
    ip.ocs_notes = [{"date": "2026-09-19", "user": "admin", "comment": "編列資產標籤"}]
    await _mk_ip(db_session, "198.51.100.99", "aa:bb:cc:00:00:99")   # 沒被 OCS 盤點過
    await db_session.flush()

    r = await list_ocs_computers(db_session, user=admin_user)
    assert r["scope"] == "all" and r["count"] == 1, r
    got = r["computers"][0]
    assert got["tag"] == "ASSET-000114"
    assert got["agent_version"].startswith("OCS-NG")
    assert got["notes"][0]["comment"] == "編列資產標籤"

    # stale_days：盤點時間很新 → 問「超過 30 天沒盤點」不該列出來
    assert (await list_ocs_computers(db_session, user=admin_user, stale_days=30))["count"] == 0
    ip.last_seen_ocs = datetime.now(UTC) - timedelta(days=60)
    await db_session.flush()
    assert (await list_ocs_computers(db_session, user=admin_user, stale_days=30))["count"] == 1


def test_ocs_tool_is_registered_with_the_right_permission_tier() -> None:
    """整合開了 REST/UI 就要同步開 MCP 工具，且權限分層要跟同類工具與 REST 一致。

    2026-09-30 起是 admin：`/api/v1/ocs/agents` 只給 admin，工具以前放在全域讀取 ——
    網頁打不開的資料在 AI 對話裡問得到（見 test_mcp_tools_match_rest_permissions.py）。"""
    from app.mcp.tools import ADMIN_TOOLS, GLOBAL_READ_TOOLS, MUTATING_TOOLS, TOOLS
    assert "list_ocs_computers" in TOOLS
    assert "list_ocs_computers" in ADMIN_TOOLS          # 與 list_wazuh_agents 同級
    assert "list_wazuh_agents" in ADMIN_TOOLS
    assert "list_ocs_computers" not in MUTATING_TOOLS  # 唯讀
    assert "list_ocs_computers" not in GLOBAL_READ_TOOLS


@pytest.mark.anyio
async def test_get_ip_detail_exposes_ocs_fields(db_session, admin_user) -> None:
    """AI 問某個 IP 時也要看得到 OCS 欄位（標籤／代理版本／盤點時間／備註）。"""
    from datetime import UTC, datetime

    from app.mcp.tools import get_ip_detail
    ip = await _mk_ip(db_session, "198.51.100.41", "aa:bb:cc:00:00:41")
    ip.ocs_id = 7
    ip.ocs_tag = "ASSET-000114"
    ip.ocs_agent = "agent-2.10"
    ip.last_seen_ocs = datetime.now(UTC)
    await db_session.commit()

    d = await get_ip_detail(db_session, user=admin_user, ip="198.51.100.41")
    assert d["ocs_tag"] == "ASSET-000114"
    assert d["ocs_agent"] == "agent-2.10"
    assert d["last_seen_ocs"] is not None


# ── 2026-09-26：new-host 裝了 OCS agent，jt-ipam 卻對到 BMC 的位址、主機本身沒對上 ──

def test_the_bmc_interface_is_not_the_hosts_nic() -> None:
    """Linux agent 會把 IPMI 控制器列成一張 DESCRIPTION=bmc 的網卡：那是另一台設備（BMC）的
    位址與 MAC，把主機的 OS／主機名稱寫到 BMC 的 IP 上是錯的。"""
    nics = [{"DESCRIPTION": "bmc", "MACADDR": "aa:bb:cc:00:0f:3f", "IPADDRESS": "198.51.100.46",
             "VIRTUALDEV": 0},
            {"DESCRIPTION": "eno2", "MACADDR": "aa:bb:cc:00:0d:7d", "IPADDRESS": "198.51.100.139",
             "VIRTUALDEV": 0}]
    assert [n["DESCRIPTION"] for n in svc.usable_nics(nics)] == ["eno2"]


def test_an_ambiguous_mac_is_settled_by_the_ip_the_nic_reports() -> None:
    """同一個 MAC 在兩筆 IP 上：常見是舊的 DHCP 位址（改固定 IP 之前拿的）留著同一個 MAC。
    OCS 回報這張網卡目前的 IP 正好是其中一筆 → MAC 與 IP 都對上，就是它。"""
    new, old = uuid.uuid4(), uuid.uuid4()
    idx = {"aabbcc000d7d": [old, new]}
    addr = {old: "198.51.100.69", new: "198.51.100.139"}
    assert svc.decide_match("aa:bb:cc:00:0d:7d", idx, reported_ip="198.51.100.139", addr_of=addr) == new
    # 回報的 IP 不在候選裡 → 仍然不猜
    assert svc.decide_match("aa:bb:cc:00:0d:7d", idx, reported_ip="198.51.100.7", addr_of=addr) is None
    assert svc.decide_match("aa:bb:cc:00:0d:7d", idx) is None


async def test_ocs_fields_are_cleared_from_an_ip_that_no_longer_matches(monkeypatch, db_session) -> None:
    """上一輪對到（例如 BMC 的位址、或換掉的舊機器），這一輪完整同步沒對到 → OCS 的資料要清掉，
    不然 IP 會一直頂著別台機器的 OS 與盤點編號。"""
    import app.services.ocs as ocs

    host = await _mk_ip(db_session, "198.51.100.139", "aa:bb:cc:00:0d:7d")
    bmc = await _mk_ip(db_session, "198.51.100.46", "aa:bb:cc:00:0f:3f")
    bmc.ocs_id, bmc.os_ocs, bmc.ocs_tag = 53, "Fedora Linux 44", "old"
    bmc.last_seen_ocs = datetime(2026, 9, 26, tzinfo=UTC)
    await db_session.flush()

    page = {"53": {"hardware": {"NAME": "new-host", "OSNAME": "Linux", "OSCOMMENTS": "Fedora Linux 44",
                                "LASTDATE": "2026-09-26 08:00:00"},
                   "networks": [
                       {"DESCRIPTION": "bmc", "MACADDR": "aa:bb:cc:00:0f:3f", "IPADDRESS": "198.51.100.46"},
                       {"DESCRIPTION": "eno2", "MACADDR": "aa:bb:cc:00:0d:7d", "IPADDRESS": "198.51.100.139"}]}}

    class _C2:
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return False

    async def fake_request(method, url, *, client=None, headers=None, verify=True, **extra):
        if "lastupdate" in url:
            return _FakeResp(404, None)
        return _FakeResp(200, page if "start=0" in url else {})

    monkeypatch.setattr(ocs, "safe_client", lambda *a, **k: _C2())
    monkeypatch.setattr(ocs, "safe_request", fake_request)
    from types import SimpleNamespace
    srv = SimpleNamespace(
        id=uuid.uuid4(), name="ocs", source_type="rest", base_url="https://192.0.2.10",
        verify_tls=False, api_username=None, api_password_enc=None, api_password_nonce=None,
        sync_bios=False, stale_after_days=3650, last_incremental_epoch=None, timezone="UTC",
        detected_version=None, last_sync_at=None, last_success_at=None,
        last_error=None, last_cost=None, scope_subnet_ids=None)
    await ocs.sync_instance(db_session, srv)
    await db_session.flush()
    await db_session.refresh(host)
    await db_session.refresh(bmc)
    assert host.ocs_id == 53
    assert host.os_ocs == "Fedora Linux 44"
    assert bmc.ocs_id is None
    assert bmc.os_ocs is None
    assert bmc.ocs_tag is None
    assert bmc.last_seen_ocs is None


async def test_a_stale_inventory_clears_the_ocs_os(db_session) -> None:
    """過期的盤點以前只是「不寫」，舊的 OS 一直留著、繼續蓋過掃描代理的即時結果。"""
    from app.services.arp_precedence import normalize_mac
    ip = await _mk_ip(db_session, "198.51.100.98", "aa:bb:cc:00:00:98")
    ip.os_ocs = "Windows 7"
    await db_session.flush()
    idx = {normalize_mac("aa:bb:cc:00:00:98"): [ip.id]}
    await svc._apply_computer(
        db_session, _server(),
        _computer("host-old", "aa:bb:cc:00:00:98", lastdate="2025-08-01 08:00:00"),
        idx, datetime(2026, 9, 18, 12, 0, tzinfo=UTC))
    await db_session.flush()
    await db_session.refresh(ip)
    assert ip.os_ocs is None
