"""FDB（交換器 MAC 位址表）推導的存取層與骨幹連線。

FDB 回答的是「哪個 MAC 出現在哪台交換器的哪個埠」。從這裡推連線有兩個古典陷阱，
測試就是圍著它們寫的：

1. **上行埠（trunk）不是端點**：一個埠上掛著幾十上百個 MAC，代表那是通往其他交換器的
   路，不是「這些機器都插在這個埠上」。把它當存取埠畫，整台交換器會被畫成掛在另一台的
   某個埠底下 —— 拓樸圖會錯得很有自信。
2. **同一個 MAC 對到多台裝置就不能猜**：重疊網段下同一個 MAC 可能出現在多筆 IP 記錄。
   寧可不畫，也不要畫一條「看起來很確定」的錯邊（同 Proxmox MAC 後援的原則）。

FDB 屬證據契約的 `learned` 層：它說的是「曾經學到這個對應」，不是「現在活著」，
所以這裡推出來的邊一律標 `via=fdb`，不參與任何上線判定。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.models.address import IPAddress
from app.models.device import Device
from app.models.librenms import FDBEntry, LibreNMSDevice, LibreNMSInstance
from app.models.section import Section
from app.models.subnet import Subnet
from app.services.topology import UPLINK_MAC_THRESHOLD, build_topology


async def _device(session, name: str, type_: str = "switch") -> Device:
    d = Device(name=name, type=type_)
    session.add(d)
    await session.flush()
    return d


async def _subnet(session, cidr: str = "198.51.100.0/24") -> Subnet:
    sec = Section(name=f"sec-{cidr}")
    session.add(sec)
    await session.flush()
    sn = Subnet(cidr=cidr, section_id=sec.id)
    session.add(sn)
    await session.flush()
    return sn


async def _ip(session, subnet, ip: str, mac: str, device=None) -> IPAddress:
    row = IPAddress(subnet_id=subnet.id, ip=ip, mac=mac,
                    device_id=device.id if device else None)
    session.add(row)
    await session.flush()
    return row


async def _ln_device(session, device: Device) -> LibreNMSDevice:
    """FDB 裡的「交換器」是 LibreNMS 的裝置，要經 jt_ipam_device_id 才對得回圖上的節點。

    這層轉換不是形式：實機上 FDB 只有兩台交換器在報，兩台都有連結；沒連結的交換器
    在圖上根本沒有節點可以連。
    """
    inst = (await session.execute(select(LibreNMSInstance).limit(1))).scalars().first()
    if inst is None:
        inst = LibreNMSInstance(name="ln", api_url="https://librenms.example.com",
                                api_token_enc=b"x", api_token_nonce=b"y")
        session.add(inst)
        await session.flush()
    seq = int(await session.scalar(
        select(func.coalesce(func.max(LibreNMSDevice.legacy_device_id), 0))) or 0) + 1
    ln = LibreNMSDevice(instance_id=inst.id, legacy_device_id=seq,
                        hostname=device.name, jt_ipam_device_id=device.id)
    session.add(ln)
    await session.flush()
    return ln


async def _fdb(session, switch: Device, port: str, mac: str, vlan: int | None = None) -> None:
    # 查資料庫而不是用模組級快取：測試之間會 TRUNCATE，快取住的 row 下一個測試就是幽靈。
    ln = (await session.execute(
        select(LibreNMSDevice).where(LibreNMSDevice.jt_ipam_device_id == switch.id)
    )).scalars().first()
    if ln is None:
        ln = await _ln_device(session, switch)
    session.add(FDBEntry(mac=mac, port_name=port, device_id=ln.id, vlan_id_num=vlan,
                         source="librenms", last_seen_at=datetime.now(UTC)))
    await session.flush()


def _l2(graph, kind: str = "l2") -> list[dict]:
    return [e["data"] for e in graph["edges"] if e["data"].get("kind") == kind]


async def test_access_port_links_device_to_switch_with_port_label(db_session):
    sw = await _device(db_session, "sw-core")
    host = await _device(db_session, "srv-1", "server")
    sn = await _subnet(db_session)
    await _ip(db_session, sn, "198.51.100.10", "aa:bb:cc:00:00:01", host)
    await _fdb(db_session, sw, "GigabitEthernet1/0/5", "aa:bb:cc:00:00:01", vlan=20)

    g = await build_topology(db_session, include_l3=False, include_vpn=False,
                             include_wireless=False)
    edges = _l2(g)
    assert len(edges) == 1
    e = edges[0]
    assert {e["source"], e["target"]} == {str(host.id), str(sw.id)}
    assert e["label"] == "GigabitEthernet1/0/5"
    assert e["via"] == "fdb"
    assert e["vlan"] == 20
    assert e["direct"] is True, "埠上只有這一個 MAC → 就是直接插在上面"


async def test_trunk_port_does_not_produce_endpoint_edges(db_session):
    """MAC 數超過門檻的埠是上行，不可以把上面的每個 MAC 都畫成插在這個埠上。"""
    sw = await _device(db_session, "sw-core")
    sn = await _subnet(db_session)
    hosts = []
    for i in range(UPLINK_MAC_THRESHOLD + 2):
        h = await _device(db_session, f"srv-{i}", "server")
        mac = f"aa:bb:cc:00:01:{i:02x}"
        await _ip(db_session, sn, f"198.51.100.{20 + i}", mac, h)
        await _fdb(db_session, sw, "Port-channel1", mac)
        hosts.append(h)

    g = await build_topology(db_session, include_l3=False, include_vpn=False,
                             include_wireless=False)
    assert _l2(g) == [], "上行埠不該產生存取層邊"


async def test_one_sided_sighting_is_not_enough_for_a_backbone_link(db_session):
    """只有 A 在自己的上行埠看到 B（B 沒回報 FDB）→ 不畫。

    單邊看得到只代表「B 在那個方向的某處」，不代表直連 —— 中間可能還隔著別台。
    無從證實就不畫，同「同 MAC 對到多台就不猜」的原則。
    """
    a = await _device(db_session, "sw-a")
    b = await _device(db_session, "sw-b")
    sn = await _subnet(db_session)
    b_mac = "aa:bb:cc:00:02:01"
    await _ip(db_session, sn, "198.51.100.2", b_mac, b)
    await _fdb(db_session, a, "Te1/1/1", b_mac)
    for i in range(UPLINK_MAC_THRESHOLD + 1):
        await _fdb(db_session, a, "Te1/1/1", f"aa:bb:cc:00:03:{i:02x}")

    g = await build_topology(db_session, include_l3=False, include_vpn=False,
                             include_wireless=False)
    assert _l2(g, "l2_uplink") == []


async def test_chain_does_not_make_the_two_ends_look_adjacent(db_session):
    """A—B—C 串接：A 與 C 互相看得到，但不可以畫成 A—C 直連。

    這是 FDB 推拓樸最容易產生的假邊：C 的 MAC 當然會出現在 A 朝 B 的那個埠上。
    擋掉它的是「兩個埠背後的 MAC 集合不可相交」—— 兩邊都含有 B 與 B 底下的機器。
    """
    a = await _device(db_session, "sw-a")
    b = await _device(db_session, "sw-b")
    c = await _device(db_session, "sw-c")
    sn = await _subnet(db_session)
    macs = {}
    for i, sw in enumerate((a, b, c)):
        macs[sw.name] = f"aa:bb:cc:00:0b:{i:02x}"
        await _ip(db_session, sn, f"198.51.100.{30 + i}", macs[sw.name], sw)
    # B 底下掛著自己的機器，A 與 C 都會透過 B 學到它們
    b_hosts = [f"aa:bb:cc:00:0c:{i:02x}" for i in range(UPLINK_MAC_THRESHOLD + 1)]
    for m in b_hosts:
        await _fdb(db_session, b, "Gi1/0/10", m)

    # A 朝 B 的埠：看得到 B、C 與 B 底下的機器
    for m in [macs["sw-b"], macs["sw-c"], *b_hosts]:
        await _fdb(db_session, a, "Te1/1/1", m)
    # C 朝 B 的埠：看得到 B、A 與 B 底下的機器
    for m in [macs["sw-b"], macs["sw-a"], *b_hosts]:
        await _fdb(db_session, c, "Te1/1/9", m)
    # B 的兩個上行埠：朝 A 那個只看得到 A、朝 C 那個只看得到 C
    await _fdb(db_session, b, "Te1/1/2", macs["sw-a"])
    await _fdb(db_session, b, "Te1/1/3", macs["sw-c"])

    g = await build_topology(db_session, include_l3=False, include_vpn=False,
                             include_wireless=False)
    pairs = {frozenset((e["source"], e["target"]))
             for e in _l2(g, "l2_uplink") + _l2(g)}
    assert frozenset((str(a.id), str(c.id))) not in pairs, "串接兩端不可畫成直連"
    assert frozenset((str(a.id), str(b.id))) in pairs
    assert frozenset((str(b.id), str(c.id))) in pairs


async def test_mutual_sighting_makes_exactly_one_backbone_edge(db_session):
    """兩台交換器都在自己的上行埠看到對方、且兩埠背後不重疊 → 一條線，標出兩端埠名。"""
    a = await _device(db_session, "sw-a")
    b = await _device(db_session, "sw-b")
    sn = await _subnet(db_session)
    a_mac, b_mac = "aa:bb:cc:00:04:01", "aa:bb:cc:00:04:02"
    await _ip(db_session, sn, "198.51.100.3", a_mac, a)
    await _ip(db_session, sn, "198.51.100.4", b_mac, b)
    # A 朝 B 的埠上除了 B 還有 B 底下的機器；B 朝 A 的埠上除了 A 還有 A 底下的機器。
    # 兩邊背後的機器不重疊 —— 這正是「直連」該有的樣子。
    await _fdb(db_session, a, "Te1/1/1", b_mac)
    for i in range(UPLINK_MAC_THRESHOLD + 1):
        await _fdb(db_session, a, "Te1/1/1", f"aa:bb:cc:00:05:{i:02x}")
    await _fdb(db_session, b, "Te1/1/2", a_mac)
    for i in range(UPLINK_MAC_THRESHOLD + 1):
        await _fdb(db_session, b, "Te1/1/2", f"aa:bb:cc:00:06:{i:02x}")

    g = await build_topology(db_session, include_l3=False, include_vpn=False,
                             include_wireless=False)
    ups = _l2(g, "l2_uplink")
    assert len(ups) == 1, "兩邊都看到對方，仍然只該有一條骨幹線"
    assert {ups[0]["source"], ups[0]["target"]} == {str(a.id), str(b.id)}
    # port 是 source 那端的埠、peer_port 是 target 那端的；哪一台當 source 由 id 排序
    # 決定，測試不該假設是哪一台。
    by_end = {ups[0]["source"]: ups[0]["port"], ups[0]["target"]: ups[0]["peer_port"]}
    assert by_end[str(a.id)] == "Te1/1/1" and by_end[str(b.id)] == "Te1/1/2"


async def test_port_with_several_devices_is_not_claimed_as_direct(db_session):
    """一個埠後面掛著好幾台機器 → 它們在這個埠「後面」，但不見得直接插在上面。

    可能底下接了一台笨集線器，也可能那是一台跑著多個虛擬機的主機。實機上就有這種埠，
    而且同一批機器還會同時出現在另一台交換器的另一個埠上 —— 兩邊都畫成直連就是說謊。
    """
    sw = await _device(db_session, "sw-core")
    sn = await _subnet(db_session)
    for i in range(3):
        h = await _device(db_session, f"srv-{i}", "server")
        mac = f"aa:bb:cc:00:0d:{i:02x}"
        await _ip(db_session, sn, f"198.51.100.{40 + i}", mac, h)
        await _fdb(db_session, sw, "Gi1/0/7", mac)

    g = await build_topology(db_session, include_l3=False, include_vpn=False,
                             include_wireless=False)
    edges = _l2(g)
    assert len(edges) == 3, "三台都該畫（它們確實在這個埠後面）"
    assert all(e["direct"] is False for e in edges)
    assert all(e["port_mac_count"] == 3 for e in edges)


async def test_device_behind_a_busy_port_is_placed_when_that_port_is_the_inner_one(db_session):
    """AP／虛擬化主機／下游小交換器的埠上有幾十個 MAC，但那就是它插的地方。

    只看「MAC 數超過門檻＝上行埠」會把這些全部判成查不出位置 —— 實機上六台網路設備
    就是這樣全部落到「查不出接在哪台交換器」。真正分得出來的是**包含關係**：
    離裝置最近的那個埠，它背後的 MAC 集合會是外層埠的子集。實機驗證成立
    （某 AP 的埠 35 個 MAC，是上行埠 144 個 MAC 的子集）。
    """
    core = await _device(db_session, "sw-core")
    edge = await _device(db_session, "sw-edge")
    ap = await _device(db_session, "ap-1", "ap")
    sn = await _subnet(db_session)
    ap_mac = "aa:bb:cc:00:20:01"
    await _ip(db_session, sn, "198.51.100.60", ap_mac, ap)

    # sw-edge 的 eth1/0/7：AP 自己 + 它的無線用戶端（超過門檻，但這就是 AP 插的埠）
    clients = [f"aa:bb:cc:00:21:{i:02x}" for i in range(UPLINK_MAC_THRESHOLD + 3)]
    for m in [ap_mac, *clients]:
        await _fdb(db_session, edge, "eth1/0/7", m)
    # sw-core 的上行埠：看得到上面全部，再加上別的東西（外層）
    for m in [ap_mac, *clients, "aa:bb:cc:00:22:01", "aa:bb:cc:00:22:02"]:
        await _fdb(db_session, core, "Eth1/1/1", m)

    g = await build_topology(db_session, include_l3=False, include_vpn=False,
                             include_wireless=False)
    mine = [e for e in _l2(g) if e["source"] == str(ap.id)]
    assert len(mine) == 1, "應該挑出離它最近的那個埠，而不是兩個都畫或都不畫"
    assert mine[0]["target"] == str(edge.id)
    assert mine[0]["port"] == "eth1/0/7"
    assert mine[0]["direct"] is False, "埠上還有別的機器 → 是「在此埠後面」不是直連"


async def test_device_seen_only_on_one_busy_port_stays_unplaced(db_session):
    """只在一個大埠上看到，沒有內外可比 —— 它可能在那個上行埠後面的任何地方，不猜。"""
    core = await _device(db_session, "sw-core")
    host = await _device(db_session, "srv-x", "server")
    sn = await _subnet(db_session)
    mac = "aa:bb:cc:00:23:01"
    await _ip(db_session, sn, "198.51.100.61", mac, host)
    for m in [mac] + [f"aa:bb:cc:00:24:{i:02x}" for i in range(UPLINK_MAC_THRESHOLD + 2)]:
        await _fdb(db_session, core, "Te1/1/1", m)

    g = await build_topology(db_session, include_l3=False, include_vpn=False,
                             include_wireless=False)
    assert [e for e in _l2(g) if e["source"] == str(host.id)] == []


async def test_two_busy_ports_that_do_not_contain_each_other_are_not_guessed(db_session):
    """在多個大埠上看到、彼此又不是包含關係（實機上路由器就是這樣）→ 不猜。"""
    a = await _device(db_session, "sw-a")
    b = await _device(db_session, "sw-b")
    rtr = await _device(db_session, "rtr-1", "router")
    sn = await _subnet(db_session)
    mac = "aa:bb:cc:00:25:01"
    await _ip(db_session, sn, "198.51.100.62", mac, rtr)
    for i in range(UPLINK_MAC_THRESHOLD + 2):
        await _fdb(db_session, a, "Gi1/0/10", f"aa:bb:cc:00:26:{i:02x}")
        await _fdb(db_session, b, "Gi1/0/20", f"aa:bb:cc:00:27:{i:02x}")
    await _fdb(db_session, a, "Gi1/0/10", mac)
    await _fdb(db_session, b, "Gi1/0/20", mac)

    g = await build_topology(db_session, include_l3=False, include_vpn=False,
                             include_wireless=False)
    assert [e for e in _l2(g) if e["source"] == str(rtr.id)] == []


async def test_ambiguous_mac_is_not_guessed(db_session):
    """同一個 MAC 對到兩台不同裝置（重疊網段）→ 不畫，不猜。"""
    sw = await _device(db_session, "sw-core")
    h1 = await _device(db_session, "srv-1", "server")
    h2 = await _device(db_session, "srv-2", "server")
    s1 = await _subnet(db_session, "198.51.100.0/24")
    s2 = await _subnet(db_session, "203.0.113.0/24")
    mac = "aa:bb:cc:00:07:01"
    await _ip(db_session, s1, "198.51.100.11", mac, h1)
    await _ip(db_session, s2, "203.0.113.11", mac, h2)
    await _fdb(db_session, sw, "Gi1/0/9", mac)

    g = await build_topology(db_session, include_l3=False, include_vpn=False,
                             include_wireless=False)
    assert _l2(g) == []


async def test_switch_seeing_its_own_mac_makes_no_self_loop(db_session):
    sw = await _device(db_session, "sw-core")
    sn = await _subnet(db_session)
    mac = "aa:bb:cc:00:08:01"
    await _ip(db_session, sn, "198.51.100.5", mac, sw)
    await _fdb(db_session, sw, "Vlan1", mac)

    g = await build_topology(db_session, include_l3=False, include_vpn=False,
                             include_wireless=False)
    assert _l2(g) == [] and _l2(g, "l2_uplink") == []


async def test_include_fdb_false_draws_nothing(db_session):
    sw = await _device(db_session, "sw-core")
    host = await _device(db_session, "srv-1", "server")
    sn = await _subnet(db_session)
    await _ip(db_session, sn, "198.51.100.12", "aa:bb:cc:00:09:01", host)
    await _fdb(db_session, sw, "Gi1/0/1", "aa:bb:cc:00:09:01")

    g = await build_topology(db_session, include_l3=False, include_vpn=False,
                             include_wireless=False, include_fdb=False)
    assert _l2(g) == []


async def test_edge_skipped_when_one_end_is_filtered_out(db_session):
    """被子網路篩掉的裝置不在圖上 → 不可以留一條連到不存在節點的邊。"""
    sw = await _device(db_session, "sw-core")
    host = await _device(db_session, "srv-1", "server")
    keep = await _subnet(db_session, "198.51.100.0/24")
    other = await _subnet(db_session, "203.0.113.0/24")
    await _ip(db_session, other, "203.0.113.20", "aa:bb:cc:00:0a:01", host)
    await _ip(db_session, keep, "198.51.100.1", "aa:bb:cc:00:0a:02", sw)
    await _fdb(db_session, sw, "Gi1/0/2", "aa:bb:cc:00:0a:01")

    g = await build_topology(db_session, include_l3=False, include_vpn=False,
                             include_wireless=False, subnet_ids=[keep.id])
    node_ids = {n["data"]["id"] for n in g["nodes"]}
    for e in g["edges"]:
        assert e["data"]["source"] in node_ids and e["data"]["target"] in node_ids


async def _two_switch_uplink(session):
    """兩台交換器互接：sw-a 的 Eth1/1/1 朝 sw-b、sw-b 的 eth1/0/25 朝 sw-a（兩邊都看得到對方自己的 MAC）。"""
    a = await _device(session, "sw-a")
    b = await _device(session, "sw-b")
    sn = await _subnet(session)
    await _ip(session, sn, "198.51.100.2", "aa:bb:cc:00:a0:01", a)
    await _ip(session, sn, "198.51.100.3", "aa:bb:cc:00:b0:01", b)
    await _fdb(session, a, "Eth1/1/1", "aa:bb:cc:00:b0:01")
    await _fdb(session, b, "eth1/0/25", "aa:bb:cc:00:a0:01")
    return a, b, sn


async def test_dual_homed_host_gets_one_edge_per_switch_with_its_nic_mac(db_session):
    """虛擬化主機兩張網卡各接一台交換器（實機 10-23：一台主機的 LAN 卡在一台交換器、
    儲存網路卡在另一台）。每張網卡也會從交換器之間的上聯埠被對面學到；上聯埠
    （看得到別台交換器自己 MAC 的埠）不是端點，剩下的那個埠即使有好幾個 MAC（主機上的 VM）
    也是它插的地方。兩條線都要畫，並帶各自的網卡 MAC 讓前端認得出是雙接。"""
    a, b, sn = await _two_switch_uplink(db_session)
    host = await _device(db_session, "pve-1", "server")
    lan, stor = "aa:bb:cc:00:c0:01", "aa:bb:cc:00:c0:02"
    await _ip(db_session, sn, "198.51.100.110", lan, host)
    # 儲存網路那張卡沒有 IP 記錄，只在裝置埠上（LibreNMS 匯入的介面 MAC）
    from app.models.physical import DevicePort
    db_session.add(DevicePort(device_id=host.id, name="enp23s0d1", mac_address=stor))
    await db_session.flush()
    vms = [f"aa:bb:cc:00:c1:{i:02x}" for i in range(UPLINK_MAC_THRESHOLD + 6)]
    # LAN 卡：接在 sw-b 的 eth1/0/10（埠上還有 VM 的 MAC），也從 sw-a 的上聯 Eth1/1/1 學到
    # 兩張表各自老化，上聯埠那份不一定是存取埠那份的超集合（實機就是這樣，包含關係判不出內外）
    for m in [lan, *vms]:
        await _fdb(db_session, b, "eth1/0/10", m)
    for m in [lan, *vms[:-2]]:
        await _fdb(db_session, a, "Eth1/1/1", m)
    # 儲存卡：直接接在 sw-a 的 Eth1/4，也從 sw-b 的上聯 eth1/0/25 學到
    await _fdb(db_session, a, "Eth1/4", stor)
    await _fdb(db_session, b, "eth1/0/25", stor)

    g = await build_topology(db_session, include_l3=False, include_vpn=False, include_wireless=False)
    mine = sorted((e for e in _l2(g) if e["source"] == str(host.id)), key=lambda e: e["port"])
    assert [(e["target"], e["port"]) for e in mine] == [(str(a.id), "Eth1/4"), (str(b.id), "eth1/0/10")]
    assert [e["mac"] for e in mine] == [stor, lan]
    assert [e["direct"] for e in mine] == [True, False]
    assert [e["nic"] for e in mine] == ["enp23s0d1", None]


async def test_busy_port_alone_still_unplaced_without_an_uplink_to_rule_out(db_session):
    """只排除「看得到別台交換器」的上聯埠：沒有上聯可排除時，單一熱鬧埠照舊不猜。"""
    a, _b, sn = await _two_switch_uplink(db_session)
    host = await _device(db_session, "srv-y", "server")
    mac = "aa:bb:cc:00:d0:01"
    await _ip(db_session, sn, "198.51.100.120", mac, host)
    for m in [mac] + [f"aa:bb:cc:00:d1:{i:02x}" for i in range(UPLINK_MAC_THRESHOLD + 2)]:
        await _fdb(db_session, a, "Te1/1/9", m)
    g = await build_topology(db_session, include_l3=False, include_vpn=False, include_wireless=False)
    assert [e for e in _l2(g) if e["source"] == str(host.id)] == []


async def test_bonded_host_on_two_switches_gets_both_edges(db_session):
    """網卡綁定（bond / LACP）跨兩台交換器：兩張卡共用一個 MAC（實機 11-07：檔案伺服器、日誌伺服器）。
    同一個 MAC 在兩台交換器各出現在一個「只有它」的埠上 —— 上聯埠不會只有一個 MAC，
    所以這是插在兩邊，兩條線都要畫，並標 bond 讓前端認得出是雙接。"""
    a, b, sn = await _two_switch_uplink(db_session)
    host = await _device(db_session, "nas-b", "server")
    mac = "aa:bb:cc:00:e0:01"
    await _ip(db_session, sn, "198.51.100.130", mac, host)
    await _fdb(db_session, a, "Gi1/0/5", mac)
    await _fdb(db_session, b, "Gi1/0/7", mac)

    g = await build_topology(db_session, include_l3=False, include_vpn=False, include_wireless=False)
    mine = sorted((e for e in _l2(g) if e["source"] == str(host.id)), key=lambda e: e["port"])
    assert [(e["target"], e["port"]) for e in mine] == [(str(a.id), "Gi1/0/5"), (str(b.id), "Gi1/0/7")]
    assert all(e["bond"] for e in mine)
    assert all(e["direct"] for e in mine)


async def test_same_mac_on_a_shared_port_elsewhere_is_not_a_bond(db_session):
    """另一邊的埠還有別的 MAC（底下接集線器、或其實是朝外的路）→ 不算綁定，照舊只畫最乾淨的那條。"""
    a, b, sn = await _two_switch_uplink(db_session)
    host = await _device(db_session, "srv-z", "server")
    mac = "aa:bb:cc:00:e1:01"
    await _ip(db_session, sn, "198.51.100.131", mac, host)
    await _fdb(db_session, a, "Gi1/0/6", mac)
    for m in [mac, "aa:bb:cc:00:e1:02", "aa:bb:cc:00:e1:03"]:
        await _fdb(db_session, b, "Gi1/0/8", m)

    g = await build_topology(db_session, include_l3=False, include_vpn=False, include_wireless=False)
    mine = [e for e in _l2(g) if e["source"] == str(host.id)]
    assert [(e["target"], e["port"]) for e in mine] == [(str(a.id), "Gi1/0/6")]
    assert not mine[0].get("bond")
