"""系統匯出／匯入測試。

純單元（無 DB）：crypto 封套、secrets 各表徵 round-trip、registry 完整性、向下相容 coercion。
DB-backed（需 JTIPAM_TEST_DATABASE_URL）：export→import 全流程、merge 冪等、replace、dry-run 不落地。
"""

from __future__ import annotations

import secrets as _rng
import uuid

import pytest
from app.core.security import decrypt_secret, encrypt_secret
from app.services.system_transfer import crypto, importer, registry, secrets


# ─────────────────────────── 純單元 ───────────────────────────
def test_crypto_roundtrip_and_wrong_passphrase():
    inner = {"tables": {"customers": [{"id": "x", "name": "n"}]}, "central_secrets": []}
    env = crypto.seal(inner, "s3cret-pass", metadata={"app_version": "1.2.3", "scope": ["core"]}, rng=_rng)
    assert env["format"] == crypto.FORMAT
    assert crypto.read_metadata(env)["app_version"] == "1.2.3"
    assert crypto.open_envelope(env, "s3cret-pass") == inner
    with pytest.raises(crypto.TransferCryptoError):
        crypto.open_envelope(env, "wrong-pass")


def test_crypto_rejects_newer_format():
    env = crypto.seal({"tables": {}}, "pw12345678", metadata={}, rng=_rng)
    env["format_version"] = 999
    with pytest.raises(crypto.TransferCryptoError):
        crypto.open_envelope(env, "pw12345678")


def test_registry_complete_and_ordered():
    assert registry.validate_registry() == [], "有資料表未分類"
    names = registry.tables_for_scope(registry.DEFAULT_SCOPE)
    # 相依序：customers 應在 subnets 之前、subnets 在 ip_addresses 之前
    assert names.index("customers") < names.index("subnets") < names.index("ip_addresses")
    # 中央機密表在 core/integrations 被選時要納入
    assert registry.ENCRYPTED_SECRETS_TABLE in names
    assert registry.ENCRYPTED_SECRETS_TABLE not in registry.tables_for_scope(["oui"])


def test_column_secret_roundtrip_readable_by_app_service():
    from app.services.librenms import _aad
    iid = "11111111-1111-1111-1111-111111111111"
    enc, nonce = encrypt_secret("tok-abc", aad=_aad(iid))
    row = {"id": iid, "api_token_enc": enc, "api_token_nonce": nonce}
    sec = secrets.strip_column_secrets("librenms_instances", row)
    assert sec == {"api_token": "tok-abc"}
    assert "api_token_enc" not in row  # 密文已移除
    # 匯入端重加密
    imp = {"id": iid}
    secrets.apply_column_secrets("librenms_instances", imp, sec)
    back = decrypt_secret(imp["api_token_enc"], imp["api_token_nonce"], aad=_aad(iid)).decode()
    assert back == "tok-abc"


def test_settings_v1_blob_roundtrip():
    from app.services.system_config import _dec_smtp, _enc_smtp
    val = {"smtp_password_enc": _enc_smtp("hunter2"), "smtp_host": "mail"}
    out = secrets.transform_settings_out("notification_channels", val)
    assert out["smtp_password_enc"] == {"__plain__": "hunter2"}
    back = secrets.transform_settings_in("notification_channels", out)
    assert _dec_smtp(back["smtp_password_enc"]) == "hunter2"
    assert back["smtp_host"] == "mail"


def test_settings_phpipam_b64pair_roundtrip():
    import base64
    enc, nonce = encrypt_secret("ssh-key-pem")
    val = {"key_enc": base64.b64encode(enc).decode(), "key_nonce": base64.b64encode(nonce).decode(), "host": "h"}
    out = secrets.transform_settings_out("phpipam_migration", val)
    assert out["key_enc"] == {"__plain__": "ssh-key-pem"}
    assert "key_nonce" not in out
    back = secrets.transform_settings_in("phpipam_migration", out)
    got = decrypt_secret(base64.b64decode(back["key_enc"]), base64.b64decode(back["key_nonce"])).decode()
    assert got == "ssh-key-pem"


def test_central_secret_roundtrip():
    oid = uuid.uuid4()
    aad = secrets.central_secret_aad("dns_server", oid, "api_key")
    ct, nonce = encrypt_secret("dns-key", aad=aad)
    row = {"object_type": "dns_server", "object_id": oid, "field": "api_key",
           "key_id": "primary", "ciphertext": ct, "nonce": nonce}
    entry = secrets.export_central_row(row)
    assert entry["plaintext"] == "dns-key"
    built = secrets.import_central_row(entry)
    back = decrypt_secret(built["ciphertext"], built["nonce"], aad=aad).decode()
    assert back == "dns-key"


def test_json_safe_normalizes_nested_uuid_array():
    # array 欄位（如 opnsense_firewalls.scope_subnet_ids = uuid[]）元素是 UUID 物件，
    # 巢狀值也要轉成 JSON-native，讓匯出檔能乾淨 round-trip（不靠 json.dumps default=str）。
    import json

    from app.services.system_transfer import exporter
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    val = exporter._json_safe([u1, {"nested": u2}])
    assert val == [str(u1), {"nested": str(u2)}]
    assert json.loads(json.dumps(val)) == val  # 純 JSON-native，round-trip 相等


def test_coerce_backward_compat_drops_unknown_columns():
    table = registry.table_by_name("customers")
    row = {"id": str(uuid.uuid4()), "name": "acme", "some_future_column": "ignored"}
    out = importer._coerce(table, row)
    assert "some_future_column" not in out       # 未知欄位被丟棄（新版匯出→舊實例）
    assert isinstance(out["id"], uuid.UUID)       # UUID 欄位轉回 uuid
    assert out["name"] == "acme"


# ─────────────────────────── DB-backed ───────────────────────────
pytestmark_db = pytest.mark.usefixtures("db_session")


async def _seed(db_session):
    """建立一組跨相依關係的資料 + 一個含加密機密的整合。回 (ids, plaintext_token)。"""
    from app.models.address import IPAddress
    from app.models.customer import Customer
    from app.models.librenms import LibreNMSInstance
    from app.models.section import Section
    from app.models.subnet import Subnet
    from app.services.librenms import _aad

    cust = Customer(name="Acme")
    db_session.add(cust)
    await db_session.flush()
    sect = Section(name="HQ", customer_id=cust.id)
    db_session.add(sect)
    await db_session.flush()
    sub = Subnet(section_id=sect.id, cidr="10.9.8.0/24", customer_id=cust.id)
    db_session.add(sub)
    await db_session.flush()
    ipa = IPAddress(subnet_id=sub.id, ip="10.9.8.5", hostname="host-a")
    db_session.add(ipa)

    lid = uuid.uuid4()
    enc, nonce = encrypt_secret("librenms-token-xyz", aad=_aad(lid))
    inst = LibreNMSInstance(id=lid, name="mon", api_url="https://nms.local",
                            api_token_enc=enc, api_token_nonce=nonce)
    db_session.add(inst)
    await db_session.commit()
    return {"cust": cust.id, "sub": sub.id, "ip": ipa.id, "librenms": lid}, "librenms-token-xyz"


async def test_export_import_roundtrip_replace(db_session):
    from app.models.address import IPAddress
    from app.models.librenms import LibreNMSInstance
    from app.services.librenms import _aad
    from app.services.system_transfer import exporter
    from sqlalchemy import func, select

    ids, token = await _seed(db_session)
    inner = await exporter.build_export(db_session, ["core", "integrations"])
    assert inner["counts"]["customers"] >= 1
    # librenms 機密應以明文出現在 __secrets__、密文欄位不外流
    li_row = inner["tables"]["librenms_instances"][0]
    assert li_row["__secrets__"]["api_token"] == token
    assert "api_token_enc" not in li_row

    # replace 匯入回同一 DB（保留 UUID）
    report = await importer.apply_import(db_session, inner, mode="replace", dry_run=False)
    assert report["tables"]["customers"]["errored"] == 0
    assert report["tables"]["ip_addresses"]["errored"] == 0

    # 資料與 UUID 保留、機密以目標金鑰重加密後可解
    got_ip = (await db_session.execute(
        select(IPAddress).where(IPAddress.id == ids["ip"]))).scalar_one()
    assert str(got_ip.ip) == "10.9.8.5"
    inst = (await db_session.execute(
        select(LibreNMSInstance).where(LibreNMSInstance.id == ids["librenms"]))).scalar_one()
    assert decrypt_secret(inst.api_token_enc, inst.api_token_nonce, aad=_aad(ids["librenms"])).decode() == token
    # 沒有重複
    n = (await db_session.execute(select(func.count()).select_from(IPAddress))).scalar_one()
    assert n == 1


async def test_merge_idempotent(db_session):
    from app.models.customer import Customer
    from app.services.system_transfer import exporter
    from sqlalchemy import func, select

    await _seed(db_session)
    inner = await exporter.build_export(db_session, ["core", "integrations"])
    await importer.apply_import(db_session, inner, mode="merge", dry_run=False)
    r2 = await importer.apply_import(db_session, inner, mode="merge", dry_run=False)
    # 第二輪應全為 update，且客戶列數不變
    assert r2["tables"]["customers"]["inserted"] == 0
    assert r2["tables"]["customers"]["updated"] >= 1
    n = (await db_session.execute(select(func.count()).select_from(Customer))).scalar_one()
    assert n == 1


async def test_dry_run_does_not_persist(db_session):
    from app.models.customer import Customer
    from sqlalchemy import func, select

    await _seed(db_session)  # commits 1 customer
    new_id = str(uuid.uuid4())
    inner = {"tables": {"customers": [{"id": new_id, "name": "Ghost"}]}, "central_secrets": []}
    report = await importer.apply_import(db_session, inner, mode="merge", dry_run=True)
    assert report["tables"]["customers"]["inserted"] == 1
    # rollback → 幽靈客戶不存在
    n = (await db_session.execute(
        select(func.count()).select_from(Customer).where(Customer.id == uuid.UUID(new_id)))).scalar_one()
    assert n == 0


async def test_central_secret_db_import(db_session):
    # 中央 encrypted_secrets：匯出包帶明文 → 匯入以目標金鑰重加密後可解回
    from app.models.encrypted_secret import EncryptedSecret
    from sqlalchemy import select
    oid = uuid.uuid4()
    inner = {"tables": {}, "central_secrets": [
        {"object_type": "dns_server", "object_id": str(oid), "field": "api_key",
         "key_id": "primary", "plaintext": "top-secret-key"},
    ]}
    report = await importer.apply_import(db_session, inner, mode="merge", dry_run=False)
    assert report["central_secrets"]["inserted"] == 1
    row = (await db_session.execute(
        select(EncryptedSecret).where(EncryptedSecret.object_id == oid))).scalar_one()
    aad = secrets.central_secret_aad("dns_server", oid, "api_key")
    assert decrypt_secret(row.ciphertext, row.nonce, aad=aad).decode() == "top-secret-key"


async def test_backward_compat_unknown_table_skipped(db_session):
    # 舊/新版差異：匯出包含本機沒有的資料表 → 略過不炸
    inner = {"tables": {"future_feature_table": [{"id": "1"}],
                        "customers": [{"id": str(uuid.uuid4()), "name": "Compat"}]},
             "central_secrets": []}
    report = await importer.apply_import(db_session, inner, mode="merge", dry_run=False)
    assert "future_feature_table" not in report["tables"]
    assert report["tables"]["customers"]["inserted"] == 1
