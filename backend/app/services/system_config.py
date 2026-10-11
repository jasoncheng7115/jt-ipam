"""讀 system_settings table（admin UI 設定）+ env 預設值合併。

對 ai.py 之類消費者：呼叫 get_llm_config(session) → 拿到完整 dict，
DB 有設就用 DB，否則用 env。

有簡單 60s in-process cache 避免每次 LLM call 都 hit DB；改寫時主動 bump 版本。
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.models.system_setting import SystemSetting
from app.services.schedule import MIN_INTERVAL_MINUTES

LLM_KEY = "llm"
_TTL_SEC = 60.0
MAX_AUDIT_TIMES = 12          # 一天排 12 次已經遠超需要，再多只是誤設


def normalize_times(raw: Any) -> list[str]:
    """把使用者/DB 給的排程時刻整理成乾淨的 "HH:MM" 清單（去重、排序、去掉不合法的）。

    無法解析的項目**直接丟掉而不是報錯**：這是排程設定，一個打錯的字不該讓整組時刻
    連同還能用的那些一起失效。
    """
    out: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        text = str(item).strip()
        if ":" not in text:
            continue
        hh, _, mm = text.partition(":")
        try:
            h, m = int(hh), int(mm)
        except ValueError:
            continue
        if 0 <= h <= 23 and 0 <= m <= 59:
            out.add(f"{h:02d}:{m:02d}")
    return sorted(out)[:MAX_AUDIT_TIMES]


def normalize_weekdays(raw: Any) -> list[int]:
    """整理「每週的哪幾天」：1=週一 … 7=週日（ISO），去重、排序、丟掉範圍外的。

    跟 normalize_times 同樣的原則：一個壞值不該讓整組設定失效。
    """
    out: set[int] = set()
    for item in raw if isinstance(raw, list) else []:
        try:
            d = int(item)
        except (ValueError, TypeError):
            continue
        if 1 <= d <= 7:
            out.add(d)
    return sorted(out)


@dataclass
class LLMConfig:
    enabled: bool
    url: str
    embedding_model: str
    chat_model: str
    timeout: float
    # 對話模型的上下文長度（Ollama num_ctx）。None＝沿用模型／Ollama 預設（通常 4096）。
    # 工具多、注入資料量大的對話容易超過預設而被截斷，可在此調高（耗更多記憶體/VRAM）。
    num_ctx: int | None = None
    # 供應商：ollama（原生 API）或 openai（OpenAI 相容端點，可接 ChatGPT / vLLM /
    # LM Studio / OpenRouter…）。**預設 ollama** —— 接雲端等於把網段、主機名稱、拓樸
    # 送到外部服務，那是使用者要明確選擇的事，不是升版就自動改變的行為。
    provider: str = "ollama"
    # 嵌入模型的位址。留空＝沿用 url（對話模型那一台）—— 兩種模型常常是分開部署的，
    # 位址自然不同（GitHub issue #33）。供應商、金鑰與逾時仍共用；需要連到**不同供應商**
    # 的嵌入服務是另一件事，目前不支援。
    embedding_base_url: str | None = None
    api_key: str | None = None      # 明文（已解密）；僅供 openai 相容端點的 Bearer
    # 對外提供 MCP（讓其它系統以 HTTP 呼叫 /api/mcp）：預設關閉，打開才接受外部 MCP 呼叫。
    mcp_external_enabled: bool = False
    mcp_api_key: str | None = None          # 明文（已解密）；僅程序內使用，不外傳
    mcp_principal_user_id: str | None = None  # MCP 金鑰所代表的管理員身份（唯讀，僅供 RBAC 可見範圍）
    # MCP 金鑰到期時間（輪替時選天數；0199 起每把都有，到期前由 credential_expiry 通知管理員）
    mcp_api_key_expires_at: datetime | None = None
    # AI 巡檢：定期讓模型檢視 IPAM 資料找可疑之處。預設關閉 —— 它會把資料送給 LLM，
    # 該不該做是使用者的決定，不是升版就自動開始跑的事。
    ai_audit_enabled: bool = False
    # 每天在這些時刻各跑一次（"HH:MM"，伺服器本地時區）。用時刻而不是「每 N 小時」：
    # 巡檢要排在離峰跑，間隔式排程會隨著每次執行時間漂移，最後跑在什麼時候沒人說得準。
    ai_audit_times: list[str] = field(default_factory=lambda: ["03:30"])
    # 排程的「哪幾天」：daily＝每天（預設，維持既有安裝的行為）／
    # weekly＝每週的指定幾天（ai_audit_weekdays，1=週一 … 7=週日）／
    # monthly＝每月的指定某一天（ai_audit_month_day；設 31 遇到短月會落在該月最後一天，
    # 不是整個月都不跑 —— 那種安靜地不執行最難查）
    ai_audit_frequency: str = "daily"
    ai_audit_weekdays: list[int] = field(default_factory=lambda: [1])
    ai_audit_month_day: int = 1
    # 巡檢用的模型。留空＝沿用對話模型 —— 巡檢是長提示詞的批次工作，適合的模型
    # 不一定跟互動對話同一個（可以換更大的、或反過來換更省的）。
    ai_audit_model: str | None = None
    # 巡檢用的上下文長度。留空＝沿用對話模型的設定。開大一點可以一批塞更多資料
    # （批次少、跑得快），代價是更多記憶體／VRAM。
    ai_audit_num_ctx: int | None = None
    # AI 判讀（未授權 IP 判讀／IP 調查／防火牆規則異動解讀）用的模型與上下文長度。
    # 留空＝沿用對話模型。對話要快（互動、會叫工具），判讀要一次讀完一大包證據再下結論
    # —— 適合的模型不一定同一個。
    ai_interpret_model: str | None = None
    ai_interpret_num_ctx: int | None = None
    # AI 對話允不允許模型先思考。預設允許（畫面顯示「思考中」，與以前相同）；關掉時每一輪都送關閉思考的
    # 參數 —— 接會思考的模型、尤其經過 LiteLLM 這類閘道時，回答快很多。巡檢與判讀一律關閉、不看這個
    chat_thinking: bool = True


_MCP_AAD = b"llm:mcp_api_key"
# LLM 供應商金鑰用自己的 AAD：兩把金鑰用途不同，密文不該能互換位置使用。
_LLM_KEY_AAD = b"llm:api_key"


def _enc(plain: str, aad: bytes) -> str:
    import base64 as _b64

    from app.core.security import encrypt_secret
    ct, nonce = encrypt_secret(plain, aad=aad)
    return "v1:" + _b64.b64encode(nonce).decode() + ":" + _b64.b64encode(ct).decode()


def _dec(blob: str, aad: bytes) -> str | None:
    import base64 as _b64

    from app.core.security import decrypt_secret
    try:
        _ver, b_nonce, b_ct = blob.split(":", 2)
        return decrypt_secret(
            _b64.b64decode(b_ct), _b64.b64decode(b_nonce), aad=aad,
        ).decode("utf-8")
    except Exception:
        return None


def _enc_mcp(plain: str) -> str:
    return _enc(plain, _MCP_AAD)


def _dec_mcp(blob: str) -> str | None:
    return _dec(blob, _MCP_AAD)


_cache: dict[str, tuple[float, LLMConfig]] = {}


def _bust() -> None:
    _cache.pop(LLM_KEY, None)


async def get_llm_config(session: AsyncSession) -> LLMConfig:
    now = time.monotonic()
    cached = _cache.get(LLM_KEY)
    if cached and now - cached[0] < _TTL_SEC:
        return cached[1]

    s = get_settings()
    # env 預設
    cfg = LLMConfig(
        enabled=s.ollama_enabled,
        url=s.ollama_url,
        embedding_model=s.ollama_embedding_model,
        chat_model=s.ollama_chat_model,
        timeout=s.ollama_timeout,
    )
    row = await session.get(SystemSetting, LLM_KEY)
    if row and isinstance(row.value, dict):
        v = row.value
        if "enabled" in v and isinstance(v["enabled"], bool):
            cfg.enabled = v["enabled"]
        if v.get("url"):
            cfg.url = str(v["url"])
        if v.get("embedding_model"):
            cfg.embedding_model = str(v["embedding_model"])
        if v.get("embedding_base_url"):
            cfg.embedding_base_url = str(v["embedding_base_url"])
        if v.get("chat_model"):
            cfg.chat_model = str(v["chat_model"])
        if v.get("provider") in ("ollama", "openai"):
            cfg.provider = str(v["provider"])
        if v.get("api_key_enc"):
            cfg.api_key = _dec(str(v["api_key_enc"]), _LLM_KEY_AAD)
        elif v.get("api_key"):
            # 舊資料（v0.5.148 之前短暫存過明文）：讀得回來，但下次存檔就會換成密文
            cfg.api_key = str(v["api_key"])
        if v.get("timeout") is not None:
            try:
                cfg.timeout = float(v["timeout"])
            except (ValueError, TypeError):
                pass
        if v.get("num_ctx") is not None:
            try:
                n = int(v["num_ctx"])
                cfg.num_ctx = n if n > 0 else None
            except (ValueError, TypeError):
                pass
        if isinstance(v.get("mcp_external_enabled"), bool):
            cfg.mcp_external_enabled = v["mcp_external_enabled"]
        if v.get("mcp_api_key_enc"):
            cfg.mcp_api_key = _dec_mcp(str(v["mcp_api_key_enc"]))
        if v.get("mcp_principal_user_id"):
            cfg.mcp_principal_user_id = str(v["mcp_principal_user_id"])
        if v.get("mcp_api_key_expires_at"):
            try:
                cfg.mcp_api_key_expires_at = datetime.fromisoformat(str(v["mcp_api_key_expires_at"]))
            except ValueError:
                cfg.mcp_api_key_expires_at = None
        if isinstance(v.get("ai_audit_enabled"), bool):
            cfg.ai_audit_enabled = v["ai_audit_enabled"]
        if v.get("ai_audit_model"):
            cfg.ai_audit_model = str(v["ai_audit_model"]).strip() or None
        if v.get("ai_audit_num_ctx") is not None:
            try:
                n = int(v["ai_audit_num_ctx"])
                cfg.ai_audit_num_ctx = n if n > 0 else None
            except (ValueError, TypeError):
                pass
        if isinstance(v.get("chat_thinking"), bool):
            cfg.chat_thinking = v["chat_thinking"]
        if v.get("ai_interpret_model"):
            cfg.ai_interpret_model = str(v["ai_interpret_model"]).strip() or None
        if v.get("ai_interpret_num_ctx") is not None:
            try:
                n = int(v["ai_interpret_num_ctx"])
                cfg.ai_interpret_num_ctx = n if n > 0 else None
            except (ValueError, TypeError):
                pass
        if isinstance(v.get("ai_audit_times"), list):
            times = normalize_times(v["ai_audit_times"])
            if times:
                cfg.ai_audit_times = times
        if str(v.get("ai_audit_frequency", "")) in ("daily", "weekly", "monthly"):
            cfg.ai_audit_frequency = str(v["ai_audit_frequency"])
        if isinstance(v.get("ai_audit_weekdays"), list):
            days = normalize_weekdays(v["ai_audit_weekdays"])
            if days:
                cfg.ai_audit_weekdays = days
        if v.get("ai_audit_month_day") is not None:
            try:
                d = int(v["ai_audit_month_day"])
                if 1 <= d <= 31:
                    cfg.ai_audit_month_day = d
            except (ValueError, TypeError):
                pass

    _cache[LLM_KEY] = (now, cfg)
    return cfg


async def set_llm_config(
    session: AsyncSession,
    *,
    enabled: bool | None = None,
    url: str | None = None,
    embedding_model: str | None = None,
    embedding_base_url: str | None = None,
    chat_model: str | None = None,
    timeout: float | None = None,
    num_ctx: int | None = None,
    mcp_external_enabled: bool | None = None,
    ai_audit_enabled: bool | None = None,
    ai_audit_times: list[str] | None = None,
    ai_audit_frequency: str | None = None,
    ai_audit_weekdays: list[int] | None = None,
    ai_audit_month_day: int | None = None,
    ai_audit_model: str | None = None,
    ai_audit_num_ctx: int | None = None,
    ai_interpret_model: str | None = None,
    ai_interpret_num_ctx: int | None = None,
    chat_thinking: bool | None = None,
    provider: str | None = None,
    api_key: str | None = None,
    updated_by_user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    row = await session.get(SystemSetting, LLM_KEY)
    if row is None:
        row = SystemSetting(key=LLM_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    current: dict[str, Any] = dict(row.value or {})
    if enabled is not None: current["enabled"] = bool(enabled)
    if url is not None: current["url"] = str(url).strip().rstrip("/")
    if embedding_model is not None: current["embedding_model"] = embedding_model.strip()
    # 空字串＝清掉，回到「沿用對話模型的位址」
    if embedding_base_url is not None:
        current["embedding_base_url"] = str(embedding_base_url).strip().rstrip("/")
    if chat_model is not None: current["chat_model"] = chat_model.strip()
    if ai_audit_enabled is not None: current["ai_audit_enabled"] = bool(ai_audit_enabled)
    # 空字串＝清掉，回去沿用對話模型（不是「存一個空模型名」）
    if ai_audit_model is not None: current["ai_audit_model"] = ai_audit_model.strip() or None
    # 0 ＝清掉，回去沿用對話模型的上下文長度
    if ai_audit_num_ctx is not None:
        current["ai_audit_num_ctx"] = int(ai_audit_num_ctx) if int(ai_audit_num_ctx) > 0 else None
    if chat_thinking is not None:
        current["chat_thinking"] = bool(chat_thinking)
    # 判讀：同上，空字串／0 ＝清掉，回去沿用對話模型
    if ai_interpret_model is not None:
        current["ai_interpret_model"] = ai_interpret_model.strip() or None
    if ai_interpret_num_ctx is not None:
        current["ai_interpret_num_ctx"] = (int(ai_interpret_num_ctx)
                                           if int(ai_interpret_num_ctx) > 0 else None)
    if ai_audit_times is not None:
        times = normalize_times(ai_audit_times)
        # 一個時刻都排不出來就不要存 —— 存成空清單等於安靜地把排程關掉，
        # 但畫面上開關還是開著
        if times:
            current["ai_audit_times"] = times
    if ai_audit_frequency in ("daily", "weekly", "monthly"):
        current["ai_audit_frequency"] = ai_audit_frequency
    if ai_audit_weekdays is not None:
        days = normalize_weekdays(ai_audit_weekdays)
        # 一天都排不出來就不要存：空清單＝週排程永遠不會觸發，但畫面上開關還開著
        if days:
            current["ai_audit_weekdays"] = days
    if ai_audit_month_day is not None and 1 <= int(ai_audit_month_day) <= 31:
        current["ai_audit_month_day"] = int(ai_audit_month_day)
    if provider in ("ollama", "openai"): current["provider"] = provider
    # 空字串＝清掉金鑰（本地 vLLM／LM Studio 多半不需要）；沒帶這個欄位就不動它，
    # 才不會因為改別的設定而把金鑰洗掉
    if api_key is not None:
        key = api_key.strip()
        current.pop("api_key", None)          # 順手清掉可能存在的舊明文欄位
        current["api_key_enc"] = _enc(key, _LLM_KEY_AAD) if key else None
    if timeout is not None: current["timeout"] = float(timeout)
    if num_ctx is not None: current["num_ctx"] = int(num_ctx) if int(num_ctx) > 0 else None
    if mcp_external_enabled is not None: current["mcp_external_enabled"] = bool(mcp_external_enabled)
    row.value = current
    row.updated_by = updated_by_user_id
    # JSONB 變更 SQLAlchemy 對 dict in-place 不會偵測 — flag_modified 保險
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    _bust()
    return current


#: 對外 MCP 金鑰的預設有效天數（輪替時可選 1–365）
MCP_KEY_DEFAULT_DAYS = 90


def mcp_key_valid(cfg: LLMConfig, token: str) -> bool:
    """這把是不是目前有效的對外 MCP 金鑰（常數時間比對＋未過期）。"""
    import secrets

    if not (cfg.mcp_api_key and cfg.mcp_principal_user_id and token):
        return False
    if not secrets.compare_digest(token.encode(), cfg.mcp_api_key.encode()):
        return False
    return cfg.mcp_api_key_expires_at is None or cfg.mcp_api_key_expires_at > datetime.now(UTC)


async def rotate_mcp_api_key(
    session: AsyncSession,
    *,
    principal_user_id: uuid.UUID,
    updated_by_user_id: uuid.UUID | None = None,
    expires_in_days: int = MCP_KEY_DEFAULT_DAYS,
) -> str:
    """產生一把新的對外 MCP 金鑰（唯讀），加密保存並綁定代表身份；回傳明文（僅此一次完整顯示）。

    金鑰會過期（2026-10-09 起）：以前不會，一把外流的金鑰可以永遠用下去。"""
    import secrets
    from datetime import timedelta

    key = "jtmcp_" + secrets.token_urlsafe(32)
    row = await session.get(SystemSetting, LLM_KEY)
    if row is None:
        row = SystemSetting(key=LLM_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    current: dict[str, Any] = dict(row.value or {})
    current["mcp_api_key_enc"] = _enc_mcp(key)
    current["mcp_principal_user_id"] = str(principal_user_id)
    current["mcp_api_key_expires_at"] = (datetime.now(UTC) + timedelta(days=expires_in_days)).isoformat()
    row.value = current
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    _bust()
    return key


# ─────────────────── AI 巡檢的上次執行時間 ───────────────────
AI_AUDIT_KEY = "ai_audit_state"


async def get_ai_audit_last_run(session: AsyncSession) -> datetime | None:
    """上次巡檢執行完成的時間（不論有沒有產生發現）。

    這個必須獨立記錄，**不能拿最後一筆發現的時間當作「上次執行時間」**：一次乾淨的
    巡檢什麼都不會寫，於是排程會誤判成「從沒跑過」，每一輪同步（約 5 分鐘）就再打
    一次 LLM —— 環境越乾淨，模型被打得越兇。
    """
    row = await session.get(SystemSetting, AI_AUDIT_KEY)
    if row and isinstance(row.value, dict):
        v = row.value.get("last_run_at")
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v)
            except ValueError:
                return None
    return None


async def set_ai_audit_last_run(session: AsyncSession, *, at: datetime) -> None:
    row = await session.get(SystemSetting, AI_AUDIT_KEY)
    if row is None:
        row = SystemSetting(key=AI_AUDIT_KEY, value={})
        session.add(row)
    row.value = {**(row.value or {}), "last_run_at": at.isoformat()}
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()


# ─────────────────── 異常偵測（排程）───────────────────
ANOMALY_KEY = "anomaly"


@dataclass
class AnomalyConfig:
    """異常偵測的排程設定。

    **預設關閉**：排程會發通知給所有管理員，升級不該讓任何站台突然開始發信。
    時刻／頻率的形狀刻意與巡檢排程一致（同一個 `services/schedule.due` 判斷），
    使用者在兩個地方看到的是同一套語意。
    """

    schedule_enabled: bool = False
    times: list[str] = field(default_factory=lambda: ["04:00"])
    frequency: str = "daily"          # daily / weekly / monthly / interval
    weekdays: list[int] = field(default_factory=lambda: [1])   # 1=週一 … 7=週日
    month_day: int = 1
    # 「每隔 N 分鐘」用。下限由 schedule.MIN_INTERVAL_MINUTES 決定（timer 週期）——
    # 設得比 timer 還密不會更即時，只會讓人以為設定沒生效。
    interval_minutes: int = 60


async def get_anomaly_config(session: AsyncSession) -> AnomalyConfig:
    cfg = AnomalyConfig()
    row = await session.get(SystemSetting, ANOMALY_KEY)
    v = row.value if row and isinstance(row.value, dict) else {}
    if isinstance(v.get("schedule_enabled"), bool):
        cfg.schedule_enabled = v["schedule_enabled"]
    times = normalize_times(v.get("times"))
    if times:
        cfg.times = times
    if str(v.get("frequency", "")) in ("daily", "weekly", "monthly", "interval"):
        cfg.frequency = str(v["frequency"])
    if v.get("interval_minutes") is not None:
        try:
            cfg.interval_minutes = max(int(v["interval_minutes"]), MIN_INTERVAL_MINUTES)
        except (TypeError, ValueError):
            pass
    days = normalize_weekdays(v.get("weekdays"))
    if days:
        cfg.weekdays = days
    if v.get("month_day") is not None:
        try:
            d = int(v["month_day"])
            if 1 <= d <= 31:
                cfg.month_day = d
        except (TypeError, ValueError):
            pass
    return cfg


async def set_anomaly_config(
    session: AsyncSession, *,
    schedule_enabled: bool | None = None,
    times: list[str] | None = None,
    frequency: str | None = None,
    weekdays: list[int] | None = None,
    month_day: int | None = None,
    interval_minutes: int | None = None,
) -> AnomalyConfig:
    row = await session.get(SystemSetting, ANOMALY_KEY)
    if row is None:
        row = SystemSetting(key=ANOMALY_KEY, value={})
        session.add(row)
    v = dict(row.value or {})
    if schedule_enabled is not None:
        v["schedule_enabled"] = bool(schedule_enabled)
    if times is not None:
        t = normalize_times(times)
        if t:
            v["times"] = t
    if frequency in ("daily", "weekly", "monthly", "interval"):
        v["frequency"] = frequency
    if interval_minutes is not None:
        v["interval_minutes"] = max(int(interval_minutes), MIN_INTERVAL_MINUTES)
    if weekdays is not None:
        d = normalize_weekdays(weekdays)
        if d:
            v["weekdays"] = d
    if month_day is not None and 1 <= int(month_day) <= 31:
        v["month_day"] = int(month_day)
    row.value = v
    flag_modified(row, "value")
    await session.flush()
    return await get_anomaly_config(session)


async def get_anomaly_last_run(session: AsyncSession) -> datetime | None:
    """上次排程執行的時間。與巡檢同理：不能從「最後一筆發現」回推 ——
    一次乾淨的偵測什麼都不會留下，那會被判成從沒跑過而每輪重跑。"""
    row = await session.get(SystemSetting, ANOMALY_KEY)
    if row and isinstance(row.value, dict):
        v = row.value.get("last_run_at")
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v)
            except ValueError:
                return None
    return None


async def set_anomaly_last_run(session: AsyncSession, *, at: datetime) -> None:
    row = await session.get(SystemSetting, ANOMALY_KEY)
    if row is None:
        row = SystemSetting(key=ANOMALY_KEY, value={})
        session.add(row)
    row.value = {**(row.value or {}), "last_run_at": at.isoformat()}
    flag_modified(row, "value")
    await session.flush()


async def get_anomaly_seen(session: AsyncSession) -> dict[str, list[str]]:
    """上次通知過的發現指紋，逐類別一份。用來只通知「新的」。"""
    row = await session.get(SystemSetting, ANOMALY_KEY)
    v = row.value if row and isinstance(row.value, dict) else {}
    seen = v.get("seen")
    return seen if isinstance(seen, dict) else {}


async def set_anomaly_seen(session: AsyncSession, seen: dict[str, list[str]]) -> None:
    row = await session.get(SystemSetting, ANOMALY_KEY)
    if row is None:
        row = SystemSetting(key=ANOMALY_KEY, value={})
        session.add(row)
    row.value = {**(row.value or {}), "seen": seen}
    flag_modified(row, "value")
    await session.flush()


#: 上一次偵測的結果（手動或排程）。另開一個鍵：結果可能有幾百 KB，不要跟設定擠在一起，
#: 每次讀排程設定都得把它整份讀出來。
ANOMALY_REPORT_KEY = "anomaly_report"


async def set_anomaly_report(session: AsyncSession, report: dict[str, Any], *, trigger: str) -> None:
    """保存這次的結果：進頁面先顯示上次的結果，不用每次都重跑（點去探測再返回，結果曾被清空）。"""
    from fastapi.encoders import jsonable_encoder

    value = {"at": datetime.now(UTC).isoformat(), "trigger": trigger, "report": jsonable_encoder(report)}
    row = await session.get(SystemSetting, ANOMALY_REPORT_KEY)
    if row is None:
        session.add(SystemSetting(key=ANOMALY_REPORT_KEY, value=value))
    else:
        row.value = value
        flag_modified(row, "value")
    await session.flush()


async def get_anomaly_report(session: AsyncSession) -> dict[str, Any] | None:
    row = await session.get(SystemSetting, ANOMALY_REPORT_KEY)
    v = row.value if row and isinstance(row.value, dict) else None
    return v if v and isinstance(v.get("report"), dict) else None


# ─────────────────── AI chat 歷程保留設定 ───────────────────
AI_CHAT_KEY = "ai_chat"
_DEFAULT_RETENTION_DAYS = 90


async def get_ai_chat_retention_days(session: AsyncSession) -> int:
    """AI chat 歷程保留天數；0 = 永久保留。預設 90 天。"""
    row = await session.get(SystemSetting, AI_CHAT_KEY)
    if row and isinstance(row.value, dict):
        v = row.value.get("retention_days")
        if isinstance(v, int) and v >= 0:
            return v
    return _DEFAULT_RETENTION_DAYS


async def set_ai_chat_retention_days(
    session: AsyncSession, *, days: int, updated_by_user_id: uuid.UUID | None = None,
) -> int:
    days = max(0, int(days))
    row = await session.get(SystemSetting, AI_CHAT_KEY)
    if row is None:
        row = SystemSetting(key=AI_CHAT_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    current = dict(row.value or {})
    current["retention_days"] = days
    row.value = current
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    return days


# ─────────────────── 連線管理資安設定（console security）───────────────────
CONSOLE_SECURITY_KEY = "console_security"


async def get_rdp_clipboard_paste(session: AsyncSession) -> bool:
    """是否允許 RDP 控制端把文字貼到被控端（剪貼簿單向重導）。預設關閉（deny by default）。"""
    row = await session.get(SystemSetting, CONSOLE_SECURITY_KEY)
    if row and isinstance(row.value, dict):
        return bool(row.value.get("rdp_clipboard_paste", False))
    return False


async def set_rdp_clipboard_paste(
    session: AsyncSession, *, enabled: bool, updated_by_user_id: uuid.UUID | None = None,
) -> bool:
    row = await session.get(SystemSetting, CONSOLE_SECURITY_KEY)
    if row is None:
        row = SystemSetting(key=CONSOLE_SECURITY_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    current = dict(row.value or {})
    current["rdp_clipboard_paste"] = bool(enabled)
    row.value = current
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    return bool(enabled)


async def get_console_relay_enabled(session: AsyncSession) -> bool:
    """允許主控台經由掃描代理中繼（issue #24 階段二）。預設關閉。

    兩道網頁開關之一：這裡（系統）與逐台代理的「允許中繼」，都開才會中繼（代理主機不必設定，可用 JT_IPAM_RELAY=0 否決）。
    """
    row = await session.get(SystemSetting, CONSOLE_SECURITY_KEY)
    if row and isinstance(row.value, dict):
        return bool(row.value.get("console_relay", False))
    return False


async def set_console_relay_enabled(
    session: AsyncSession, *, enabled: bool, updated_by_user_id: uuid.UUID | None = None,
) -> bool:
    row = await session.get(SystemSetting, CONSOLE_SECURITY_KEY)
    if row is None:
        row = SystemSetting(key=CONSOLE_SECURITY_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    current = dict(row.value or {})
    current["console_relay"] = bool(enabled)
    row.value = current
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    return bool(enabled)


# RDP 主控台的連線引擎。
#
# `aardwolf` 是純 Python、零外部行程，一路以來的預設。它的限制在 asyauth 0.0.23：
# NTLM 的 MIC 沒有實作（原始碼裡是一行 TODO）。伺服器的 CHALLENGE 只要帶
# `MsvAvTimestamp`，MS-NLMP 就要求用戶端回 MIC，而 FreeRDP 的伺服器端會強制檢查 ——
# gnome-remote-desktop 用的正是它。實測（2026-09-17，Ubuntu 24 + GNOME 遠端登入）：
# 同一台、同一組帳密，FreeRDP 認證成功，aardwolf 回 STATUS_LOGON_FAILURE。
#
# `freerdp` 則相容性站在業界標準那邊，代價是要外部行程與虛擬顯示。
#
# `guacd`（2026-09-25 起）：Apache Guacamole 的伺服器端，預編檔由 scripts/guacd/ 提供、
# 以 jt-ipam-guacd 服務跑在本機。
#
# **RDP 與 VNC 的預設是 guacd**（2026-09-27 使用者指示；已安裝的站台由遷移 0158 強制改過來，
# 安裝／升級腳本預設會裝 guacd）。guacd 沒在跑時實際連線退回內建引擎（services/console_engine.py），
# 不會因為某個 OS 還沒有預編檔就整個連不上。SSH 預設仍是內建。有測試釘住這些預設值。
RDP_ENGINES: tuple[str, ...] = ("aardwolf", "freerdp", "guacd")
_RDP_ENGINE_DEFAULT = "guacd"
#: VNC／SSH：`builtin` 是一路以來的實作（VNC 走 aardwolf、SSH 走 asyncssh＋xterm.js）
VNC_ENGINES: tuple[str, ...] = ("builtin", "guacd")
SSH_ENGINES: tuple[str, ...] = ("builtin", "guacd")
_VNC_ENGINE_DEFAULT = "guacd"


async def get_rdp_engine(session: AsyncSession) -> str:
    row = await session.get(SystemSetting, CONSOLE_SECURITY_KEY)
    if row and isinstance(row.value, dict):
        engine = row.value.get("rdp_engine")
        # 認不得的值當成預設，不要讓一筆壞設定把整個主控台變成連不上
        if engine in RDP_ENGINES:
            return str(engine)
    return _RDP_ENGINE_DEFAULT


async def set_rdp_engine(
    session: AsyncSession, *, engine: str, updated_by_user_id: uuid.UUID | None = None,
) -> str:
    if engine not in RDP_ENGINES:
        raise ValueError(f"unknown rdp engine: {engine!r}")
    row = await session.get(SystemSetting, CONSOLE_SECURITY_KEY)
    if row is None:
        row = SystemSetting(key=CONSOLE_SECURITY_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    # 這把 key 底下還有剪貼簿設定 —— 要合併，不能整包換掉
    current = dict(row.value or {})
    current["rdp_engine"] = engine
    row.value = current
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    return engine


async def _get_engine(session: AsyncSession, key: str, allowed: tuple[str, ...], default: str) -> str:
    row = await session.get(SystemSetting, CONSOLE_SECURITY_KEY)
    if row and isinstance(row.value, dict):
        engine = row.value.get(key)
        if engine in allowed:
            return str(engine)
    return default


async def _set_engine(session: AsyncSession, key: str, engine: str, allowed: tuple[str, ...],
                      updated_by_user_id: uuid.UUID | None) -> str:
    if engine not in allowed:
        raise ValueError(f"unknown {key}: {engine!r}")
    row = await session.get(SystemSetting, CONSOLE_SECURITY_KEY)
    if row is None:
        row = SystemSetting(key=CONSOLE_SECURITY_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    current = dict(row.value or {})        # 同一把 key 底下還有別的設定，要合併
    current[key] = engine
    row.value = current
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    return engine


async def get_vnc_engine(session: AsyncSession) -> str:
    return await _get_engine(session, "vnc_engine", VNC_ENGINES, _VNC_ENGINE_DEFAULT)


async def set_vnc_engine(session: AsyncSession, *, engine: str,
                         updated_by_user_id: uuid.UUID | None = None) -> str:
    return await _set_engine(session, "vnc_engine", engine, VNC_ENGINES, updated_by_user_id)


async def get_ssh_engine(session: AsyncSession) -> str:
    return await _get_engine(session, "ssh_engine", SSH_ENGINES, "builtin")


async def set_ssh_engine(session: AsyncSession, *, engine: str,
                         updated_by_user_id: uuid.UUID | None = None) -> str:
    return await _set_engine(session, "ssh_engine", engine, SSH_ENGINES, updated_by_user_id)


# SFTP 單檔上下傳上限（MB）。預設 100 MB —— 這個功能的本意是設定檔、憑證、紀錄片段；
# 管理者可以放大（例如要搬 ISO）。上界是防打錯字（多打三個 0），不是能力限制：
# 後端逐塊串流、不會整個檔案放進記憶體；大檔下載在瀏覽器端改成直接寫入磁碟（SftpBrowser）。
SFTP_MAX_FILE_MB_DEFAULT = 100
SFTP_MAX_FILE_MB_LIMIT = 102_400          # 100 GB


async def get_sftp_max_file_mb(session: AsyncSession) -> int:
    row = await session.get(SystemSetting, CONSOLE_SECURITY_KEY)
    if row and isinstance(row.value, dict):
        v = row.value.get("sftp_max_file_mb")
        # 壞掉的值（字串、越界）當成預設 —— 不要讓一筆壞設定把 SFTP 整個變成不能用
        if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= SFTP_MAX_FILE_MB_LIMIT:
            return v
    return SFTP_MAX_FILE_MB_DEFAULT


async def set_sftp_max_file_mb(session: AsyncSession, *, mb: int,
                               updated_by_user_id: uuid.UUID | None = None) -> int:
    if not (1 <= int(mb) <= SFTP_MAX_FILE_MB_LIMIT):
        raise ValueError(f"sftp_max_file_mb out of range: {mb!r}")
    row = await session.get(SystemSetting, CONSOLE_SECURITY_KEY)
    if row is None:
        row = SystemSetting(key=CONSOLE_SECURITY_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    current = dict(row.value or {})        # 同一把 key 底下還有剪貼簿與引擎，要合併
    current["sftp_max_file_mb"] = int(mb)
    row.value = current
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    return int(mb)


# ─────────────────── 介面顯示設定（UI display）───────────────────
UI_DISPLAY_KEY = "ui_display"
_DEFAULT_CHANGE_LOG_DIM_DAYS = 30


async def get_change_log_dim_days(session: AsyncSession) -> int:
    """異動記錄超過幾天的項目以淡色顯示；0 = 不淡化。預設 30 天。"""
    row = await session.get(SystemSetting, UI_DISPLAY_KEY)
    if row and isinstance(row.value, dict):
        v = row.value.get("change_log_dim_days")
        if isinstance(v, int) and v >= 0:
            return v
    return _DEFAULT_CHANGE_LOG_DIM_DAYS


async def set_change_log_dim_days(
    session: AsyncSession, *, days: int, updated_by_user_id: uuid.UUID | None = None,
) -> int:
    days = max(0, min(3650, int(days)))
    row = await session.get(SystemSetting, UI_DISPLAY_KEY)
    if row is None:
        row = SystemSetting(key=UI_DISPLAY_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    current = dict(row.value or {})
    current["change_log_dim_days"] = days
    row.value = current
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    return days


# ─────────────────── Graylog DSV 查表（lookup table adapter）───────────────────

GRAYLOG_DSV_KEY = "graylog_dsv"


DEVICE_PORTS_KEY = "device_ports"

# 從整合來源匯入裝置連接埠時，符合這些樣式的視為「偽介面」而略過／清除：Windows 端點的
# NDIS 過濾器、WAN Miniport、通道等（LibreNMS 從 ifIndex 產生 ethernet_N / wireless_N /
# ppp_N…）。實體交換器與 Linux 埠名不會長這樣。管理者可在系統設定調整這份清單。
DEFAULT_PORT_IGNORE_PATTERNS = [
    r"^ethernet_\d+$",
    r"^wireless_\d+$",
    r"^ppp_\d+$",
    r"^tunnel_\d+$",
    r"^loopback_\d+$",
    r"^isatap_\d+$",
    r"^teredo_\d+$",
    # Docker／Podman 容器的 veth：每起一個容器多一個、停掉就消失（實機一台累積 41 個）
    r"^veth[0-9a-f]+$",
]


def _clean_port_patterns(raw: Any) -> list[str]:
    """整理埠過濾樣式：去空白、丟掉無法編譯的正則（比照 normalize_times，一個壞值不該讓整組失效）。"""
    out: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        p = str(item).strip()
        if not p:
            continue
        try:
            re.compile(p)
        except re.error:
            continue
        if p not in out:
            out.append(p)
    return out


async def get_device_port_filter(session: AsyncSession) -> dict[str, Any]:
    """裝置連接埠匯入的偽介面過濾設定：filter_pseudo（總開關）＋ ignore_patterns（樣式清單）。"""
    row = await session.get(SystemSetting, DEVICE_PORTS_KEY)
    v = dict(row.value) if (row and isinstance(row.value, dict)) else {}
    pats = _clean_port_patterns(v.get("ignore_patterns"))
    return {
        "filter_pseudo": bool(v.get("filter_pseudo", True)),
        "ignore_patterns": pats or list(DEFAULT_PORT_IGNORE_PATTERNS),
    }


async def set_device_port_filter(
    session: AsyncSession, *, filter_pseudo: bool, ignore_patterns: Any,
    updated_by_user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    from sqlalchemy.orm.attributes import flag_modified

    pats = _clean_port_patterns(ignore_patterns) or list(DEFAULT_PORT_IGNORE_PATTERNS)
    row = await session.get(SystemSetting, DEVICE_PORTS_KEY)
    if row is None:
        row = SystemSetting(key=DEVICE_PORTS_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    cur = dict(row.value or {})
    cur["filter_pseudo"] = bool(filter_pseudo)
    cur["ignore_patterns"] = pats
    row.value = cur
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.commit()
    return {"filter_pseudo": bool(filter_pseudo), "ignore_patterns": pats}


RACK_EMBED_KEY = "rack_embed"


# ── 公開端點的權杖（機櫃嵌入圖、Graylog DSV）──
# 2026-10-09 起：權杖加密存放並綁定用途（以前明文存在設定 JSON 裡）、會到期（預設一年，
# 到期前由 credential_expiry 通知管理員）。管理區的設定頁不再直接回權杖，要按「顯示」
# （另一個端點，留稽核）才看得到。
RACK_TOKEN_AAD = b"setting:rack_embed:token"
DSV_TOKEN_AAD = b"setting:graylog_dsv:token"
PUBLIC_TOKEN_DEFAULT_DAYS = 365
PUBLIC_TOKEN_DAYS = (30, 90, 180, 365)


def _public_token(v: dict[str, Any], aad: bytes) -> tuple[str, datetime | None]:
    """(權杖明文, 到期時間)。沒有權杖回 ("", None)；0202 之前的明文欄位也讀得到。"""
    tok = ""
    if v.get("token_enc"):
        tok = _dec(str(v["token_enc"]), aad) or ""
    elif v.get("token"):
        tok = str(v["token"])
    exp = None
    if v.get("token_expires_at"):
        try:
            exp = datetime.fromisoformat(str(v["token_expires_at"]))
        except ValueError:
            exp = None
    return tok, exp


def _new_public_token(cur: dict[str, Any], aad: bytes, days: int, nbytes: int) -> None:
    import secrets as _secrets
    from datetime import timedelta

    cur["token_enc"] = _enc(_secrets.token_urlsafe(nbytes), aad)
    cur.pop("token", None)
    cur["token_expires_at"] = (datetime.now(UTC) + timedelta(days=days)).isoformat()


def public_token_problem(cfg: dict[str, Any], supplied: str) -> str | None:
    """公開端點的權杖檢查：None＝通過；否則 "invalid" 或 "expired"（常數時間比對）。"""
    import hmac

    expected = str(cfg.get("token") or "")
    if not expected:
        return "invalid"
    try:
        a = (supplied or "").encode("utf-8", "surrogatepass")
        b = expected.encode("utf-8", "surrogatepass")
    except (UnicodeEncodeError, AttributeError):
        return "invalid"
    if not hmac.compare_digest(a, b):
        return "invalid"
    exp = cfg.get("token_expires_at")
    if isinstance(exp, datetime) and exp <= datetime.now(UTC):
        return "expired"
    return None


async def get_rack_embed(session: AsyncSession) -> dict[str, Any]:
    """機櫃示意圖對外嵌入設定：enabled / token（明文，僅程序內用）/ token_set / token_expires_at。

    與 Graylog DSV 同一個模式（單一 token + 逐物件 expose 開關），刻意不共用同一把
    token：撤銷嵌入網址時不該把 Graylog 的查表一起打掉。
    """
    row = await session.get(SystemSetting, RACK_EMBED_KEY)
    v = dict(row.value) if (row and isinstance(row.value, dict)) else {}
    tok, exp = _public_token(v, RACK_TOKEN_AAD)
    return {"enabled": bool(v.get("enabled", False)), "token": tok, "token_set": bool(tok),
            "token_expires_at": exp}


async def set_rack_embed(
    session: AsyncSession, *, enabled: bool, regenerate_token: bool = False,
    token_days: int = PUBLIC_TOKEN_DEFAULT_DAYS, updated_by_user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    from sqlalchemy.orm.attributes import flag_modified

    row = await session.get(SystemSetting, RACK_EMBED_KEY)
    if row is None:
        row = SystemSetting(key=RACK_EMBED_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    cur = dict(row.value or {})
    cur["enabled"] = bool(enabled)
    if regenerate_token or not (cur.get("token_enc") or cur.get("token")):
        _new_public_token(cur, RACK_TOKEN_AAD, token_days, 32)
    row.value = cur
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.flush()
    return await get_rack_embed(session)


async def get_graylog_dsv(session: AsyncSession) -> dict[str, Any]:
    """Graylog DSV 查表設定：enabled / token（明文，僅程序內用）/ token_set / token_expires_at /
    fmt(csv|tsv) / path(URL slug) / allow_plain_http / allowed_sources。

    - allow_plain_http：明文 8088 埠要在這裡明確打開才服務（新安裝預設關；0202 升級時
      已經在用 DSV 的站台保留原本的行為）
    - allowed_sources：只接受這些網段來的查表請求（空＝不限制）
    """
    row = await session.get(SystemSetting, GRAYLOG_DSV_KEY)
    v = dict(row.value) if (row and isinstance(row.value, dict)) else {}
    tok, exp = _public_token(v, DSV_TOKEN_AAD)
    srcs = v.get("allowed_sources")
    return {
        "enabled": bool(v.get("enabled", False)),
        "token": tok,
        "token_set": bool(tok),
        "token_expires_at": exp,
        "fmt": v.get("fmt") if v.get("fmt") in ("csv", "tsv") else "csv",
        "path": str(v.get("path") or "ip-fqdn"),
        "allow_plain_http": bool(v.get("allow_plain_http", False)),
        "allowed_sources": [str(x) for x in srcs] if isinstance(srcs, list) else [],
    }


def clean_cidrs(items: list[str] | None) -> list[str]:
    """允許的來源：每行一個位址或網段；不合法的丟 ValueError（不要靜靜略過，否則以為限制了其實沒有）。"""
    import ipaddress as _ip

    out: list[str] = []
    for raw in items or []:
        raw = str(raw).strip()
        if not raw:
            continue
        out.append(str(_ip.ip_network(raw, strict=False)))
    return out[:200]


async def set_graylog_dsv(
    session: AsyncSession, *, enabled: bool, fmt: str, path: str,
    regenerate_token: bool = False, token_days: int = PUBLIC_TOKEN_DEFAULT_DAYS,
    allow_plain_http: bool | None = None, allowed_sources: list[str] | None = None,
    updated_by_user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    import re

    from sqlalchemy.orm.attributes import flag_modified

    row = await session.get(SystemSetting, GRAYLOG_DSV_KEY)
    if row is None:
        row = SystemSetting(key=GRAYLOG_DSV_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    cur = dict(row.value or {})
    cur["enabled"] = bool(enabled)
    cur["fmt"] = fmt if fmt in ("csv", "tsv") else "csv"
    # path 限英數 / 連字號 / 底線，避免亂跑路由
    slug = re.sub(r"[^A-Za-z0-9_-]", "", path or "").strip("-") or "ip-fqdn"
    cur["path"] = slug[:48]
    if allow_plain_http is not None:
        cur["allow_plain_http"] = bool(allow_plain_http)
    if allowed_sources is not None:
        cur["allowed_sources"] = clean_cidrs(allowed_sources)
    if regenerate_token or not (cur.get("token_enc") or cur.get("token")):
        _new_public_token(cur, DSV_TOKEN_AAD, token_days, 24)
    row.value = cur
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.commit()
    return await get_graylog_dsv(session)


# ─────────────────── LDAP / AD（管理區設定，DB 覆蓋 env）───────────────────
import base64  # noqa: E402

from app.core.security import decrypt_secret, encrypt_secret  # noqa: E402

LDAP_KEY = "ldap"
_LDAP_AAD = b"ldap:bind_password"


@dataclass
class LdapConfig:
    enabled: bool
    server: str | None
    port: int
    use_ssl: bool
    use_starttls: bool
    bind_dn: str | None
    bind_password: str | None   # 明文（已解密）；僅在 process 內使用，不外傳
    search_base: str | None
    user_filter: str
    attr_email: str
    attr_display_name: str
    attr_member_of: str
    admin_groups: list[str]
    timeout: float
    default_group_id: str | None = None   # 自動建立帳號時加入的群組（預設角色）


def _enc_pw(pw: str) -> str:
    ct, nonce = encrypt_secret(pw, aad=_LDAP_AAD)
    return "v1:" + base64.b64encode(nonce).decode() + ":" + base64.b64encode(ct).decode()


def _dec_pw(blob: str) -> str | None:
    try:
        _ver, b_nonce, b_ct = blob.split(":", 2)
        return decrypt_secret(
            base64.b64decode(b_ct), base64.b64decode(b_nonce), aad=_LDAP_AAD
        ).decode("utf-8")
    except Exception:
        return None


async def get_ldap_config(session: AsyncSession) -> LdapConfig:
    """合併 env 預設 + DB 覆蓋。DB 沒設就完全等同舊的 env 行為。"""
    s = get_settings()
    cfg = LdapConfig(
        enabled=s.ldap_enabled,
        server=s.ldap_server,
        port=s.ldap_port,
        use_ssl=s.ldap_use_ssl,
        use_starttls=s.ldap_use_starttls,
        bind_dn=s.ldap_bind_dn,
        bind_password=s.ldap_bind_password.get_secret_value() if s.ldap_bind_password else None,
        search_base=s.ldap_search_base,
        user_filter=s.ldap_user_filter,
        attr_email=s.ldap_attr_email,
        attr_display_name=s.ldap_attr_display_name,
        attr_member_of=s.ldap_attr_member_of,
        admin_groups=list(s.ldap_admin_groups),
        timeout=s.ldap_timeout,
    )
    row = await session.get(SystemSetting, LDAP_KEY)
    if row and isinstance(row.value, dict):
        v = row.value
        for k in ("server", "bind_dn", "search_base", "user_filter",
                  "attr_email", "attr_display_name", "attr_member_of"):
            if isinstance(v.get(k), str) and v[k] != "":
                setattr(cfg, k, v[k])
        for k in ("enabled", "use_ssl", "use_starttls"):
            if isinstance(v.get(k), bool):
                setattr(cfg, k, v[k])
        if isinstance(v.get("port"), int):
            cfg.port = v["port"]
        if isinstance(v.get("admin_groups"), list):
            cfg.admin_groups = [str(x) for x in v["admin_groups"]]
        if isinstance(v.get("default_group_id"), str) and v["default_group_id"]:
            cfg.default_group_id = v["default_group_id"]
        if isinstance(v.get("bind_password_enc"), str) and v["bind_password_enc"]:
            pw = _dec_pw(v["bind_password_enc"])
            if pw is not None:
                cfg.bind_password = pw
    return cfg


_LDAP_SCALARS = ("enabled", "server", "port", "use_ssl", "use_starttls", "bind_dn",
                 "search_base", "user_filter", "attr_email", "attr_display_name",
                 "attr_member_of", "admin_groups", "default_group_id")


async def set_ldap_config(
    session: AsyncSession, *, data: dict[str, Any], updated_by_user_id: uuid.UUID
) -> dict[str, Any]:
    """寫入 DB。bind_password：給非空字串才更新；給空字串清除；不給則保留原值。"""
    from sqlalchemy.orm.attributes import flag_modified

    row = await session.get(SystemSetting, LDAP_KEY)
    if row is None:
        row = SystemSetting(key=LDAP_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    val: dict[str, Any] = dict(row.value or {})
    for k in _LDAP_SCALARS:
        if k in data:
            val[k] = data[k]
    if "bind_password" in data:
        pw = data["bind_password"]
        if pw:
            val["bind_password_enc"] = _enc_pw(str(pw))
        elif pw == "":
            val.pop("bind_password_enc", None)
    row.value = val
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.commit()
    return val


# ─────────────────── SSO：OIDC（OpenID Connect）───────────────────
OIDC_KEY = "oidc"
_OIDC_AAD = b"oidc:client_secret"


def _enc_oidc(s: str) -> str:
    ct, nonce = encrypt_secret(s, aad=_OIDC_AAD)
    return "v1:" + base64.b64encode(nonce).decode() + ":" + base64.b64encode(ct).decode()


def _dec_oidc(blob: str) -> str | None:
    try:
        _ver, b_nonce, b_ct = blob.split(":", 2)
        return decrypt_secret(base64.b64decode(b_ct), base64.b64decode(b_nonce),
                              aad=_OIDC_AAD).decode("utf-8")
    except Exception:
        return None


@dataclass
class OidcConfig:
    enabled: bool
    issuer: str | None
    client_id: str | None
    client_secret: str | None   # 明文（已解密），僅 process 內用
    redirect_uri: str | None
    scope: str
    groups_claim: str
    username_claim: str
    admin_groups: list[str]
    default_group_id: str | None = None


async def get_oidc_config(session: AsyncSession) -> OidcConfig:
    s = get_settings()
    cfg = OidcConfig(
        enabled=s.oidc_enabled,
        issuer=s.oidc_issuer,
        client_id=s.oidc_client_id,
        client_secret=s.oidc_client_secret.get_secret_value() if s.oidc_client_secret else None,
        redirect_uri=s.oidc_redirect_uri,
        scope=s.oidc_scope,
        groups_claim=s.oidc_groups_claim,
        username_claim=s.oidc_username_claim,
        admin_groups=list(s.oidc_admin_groups),
    )
    row = await session.get(SystemSetting, OIDC_KEY)
    if row and isinstance(row.value, dict):
        v = row.value
        for k in ("issuer", "client_id", "redirect_uri", "scope",
                  "groups_claim", "username_claim"):
            if isinstance(v.get(k), str) and v[k] != "":
                setattr(cfg, k, v[k])
        if isinstance(v.get("enabled"), bool):
            cfg.enabled = v["enabled"]
        if isinstance(v.get("admin_groups"), list):
            cfg.admin_groups = [str(x) for x in v["admin_groups"]]
        if isinstance(v.get("default_group_id"), str) and v["default_group_id"]:
            cfg.default_group_id = v["default_group_id"]
        if isinstance(v.get("client_secret_enc"), str) and v["client_secret_enc"]:
            sec = _dec_oidc(v["client_secret_enc"])
            if sec is not None:
                cfg.client_secret = sec
    return cfg


_OIDC_SCALARS = ("enabled", "issuer", "client_id", "redirect_uri", "scope",
                 "groups_claim", "username_claim", "admin_groups", "default_group_id")


async def set_oidc_config(
    session: AsyncSession, *, data: dict[str, Any], updated_by_user_id: uuid.UUID
) -> dict[str, Any]:
    from sqlalchemy.orm.attributes import flag_modified
    row = await session.get(SystemSetting, OIDC_KEY)
    if row is None:
        row = SystemSetting(key=OIDC_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    val: dict[str, Any] = dict(row.value or {})
    for k in _OIDC_SCALARS:
        if k in data:
            val[k] = data[k]
    if "client_secret" in data:
        sec = data["client_secret"]
        if sec:
            val["client_secret_enc"] = _enc_oidc(str(sec))
        elif sec == "":
            val.pop("client_secret_enc", None)
    row.value = val
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.commit()
    return val


# ─────────────────── SSO：SAML 2.0 ───────────────────
SAML_KEY = "saml"
_SAML_AAD = b"saml:sp_private_key"


def _enc_saml(s: str) -> str:
    ct, nonce = encrypt_secret(s, aad=_SAML_AAD)
    return "v1:" + base64.b64encode(nonce).decode() + ":" + base64.b64encode(ct).decode()


def _dec_saml(blob: str) -> str | None:
    try:
        _ver, b_nonce, b_ct = blob.split(":", 2)
        return decrypt_secret(base64.b64decode(b_ct), base64.b64decode(b_nonce),
                              aad=_SAML_AAD).decode("utf-8")
    except Exception:
        return None


@dataclass
class SamlConfig:
    enabled: bool
    idp_metadata_url: str | None
    idp_metadata_xml: str | None
    sp_entity_id: str | None
    sp_acs_url: str | None
    sp_sls_url: str | None
    sp_x509_cert: str | None
    sp_private_key: str | None   # 明文（已解密），僅 process 內用
    want_assertions_signed: bool
    want_assertions_encrypted: bool
    want_name_id_encrypted: bool
    authn_requests_signed: bool
    attr_username: str
    attr_email: str
    attr_displayname: str
    attr_groups: str
    admin_groups: list[str]
    default_group_id: str | None = None


async def get_saml_config(session: AsyncSession) -> SamlConfig:
    """env 為預設、DB(system_settings.saml) 覆寫；無 DB row → 行為與舊版讀 env 完全相同。"""
    s = get_settings()
    cfg = SamlConfig(
        enabled=s.saml_enabled,
        idp_metadata_url=s.saml_idp_metadata_url or s.saml_metadata_url,
        idp_metadata_xml=s.saml_idp_metadata_xml,
        sp_entity_id=s.saml_sp_entity_id or s.saml_entity_id,
        sp_acs_url=s.saml_sp_acs_url or s.saml_acs_url,
        sp_sls_url=s.saml_sp_sls_url,
        sp_x509_cert=s.saml_sp_x509_cert,
        sp_private_key=s.saml_sp_private_key.get_secret_value() if s.saml_sp_private_key else None,
        want_assertions_signed=s.saml_want_assertions_signed,
        want_assertions_encrypted=s.saml_want_assertions_encrypted,
        want_name_id_encrypted=s.saml_want_name_id_encrypted,
        authn_requests_signed=s.saml_authn_requests_signed,
        attr_username=s.saml_attr_username,
        attr_email=s.saml_attr_email,
        attr_displayname=s.saml_attr_displayname,
        attr_groups=s.saml_attr_groups,
        admin_groups=list(s.saml_admin_groups),
    )
    row = await session.get(SystemSetting, SAML_KEY)
    if row and isinstance(row.value, dict):
        v = row.value
        for k in ("idp_metadata_url", "idp_metadata_xml", "sp_entity_id", "sp_acs_url",
                  "sp_sls_url", "sp_x509_cert", "attr_username", "attr_email",
                  "attr_displayname", "attr_groups"):
            if isinstance(v.get(k), str) and v[k] != "":
                setattr(cfg, k, v[k])
        for bk in ("enabled", "want_assertions_signed", "want_assertions_encrypted",
                   "want_name_id_encrypted", "authn_requests_signed"):
            if isinstance(v.get(bk), bool):
                setattr(cfg, bk, v[bk])
        if isinstance(v.get("admin_groups"), list):
            cfg.admin_groups = [str(x) for x in v["admin_groups"]]
        if isinstance(v.get("default_group_id"), str) and v["default_group_id"]:
            cfg.default_group_id = v["default_group_id"]
        if isinstance(v.get("sp_private_key_enc"), str) and v["sp_private_key_enc"]:
            pk = _dec_saml(v["sp_private_key_enc"])
            if pk is not None:
                cfg.sp_private_key = pk
    return cfg


_SAML_SCALARS = ("enabled", "idp_metadata_url", "idp_metadata_xml", "sp_entity_id",
                 "sp_acs_url", "sp_sls_url", "sp_x509_cert", "want_assertions_signed",
                 "want_assertions_encrypted", "want_name_id_encrypted",
                 "authn_requests_signed", "attr_username", "attr_email",
                 "attr_displayname", "attr_groups", "admin_groups", "default_group_id")


async def set_saml_config(
    session: AsyncSession, *, data: dict[str, Any], updated_by_user_id: uuid.UUID
) -> dict[str, Any]:
    from sqlalchemy.orm.attributes import flag_modified
    row = await session.get(SystemSetting, SAML_KEY)
    if row is None:
        row = SystemSetting(key=SAML_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    val: dict[str, Any] = dict(row.value or {})
    for k in _SAML_SCALARS:
        if k in data:
            val[k] = data[k]
    if "sp_private_key" in data:
        pk = data["sp_private_key"]
        if pk:
            val["sp_private_key_enc"] = _enc_saml(str(pk))
        elif pk == "":
            val.pop("sp_private_key_enc", None)
    row.value = val
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.commit()
    return val


# ─────────────────── 稽核轉送到 Graylog（syslog / CEF / GELF）───────────────────
AUDIT_FWD_KEY = "audit_forward"


@dataclass
class AuditForwardConfig:
    enabled: bool
    host: str | None
    port: int
    protocol: str   # tcp | udp
    fmt: str        # gelf | syslog | cef


_af_cache: dict[str, tuple[float, AuditForwardConfig]] = {}


async def get_audit_forward(session: AsyncSession) -> AuditForwardConfig:
    now = time.monotonic()
    c = _af_cache.get(AUDIT_FWD_KEY)
    if c and now - c[0] < _TTL_SEC:
        return c[1]
    cfg = AuditForwardConfig(enabled=False, host=None, port=12201, protocol="udp", fmt="gelf")
    row = await session.get(SystemSetting, AUDIT_FWD_KEY)
    if row and isinstance(row.value, dict):
        v = row.value
        if isinstance(v.get("enabled"), bool):
            cfg.enabled = v["enabled"]
        if isinstance(v.get("host"), str):
            cfg.host = v["host"] or None
        if isinstance(v.get("port"), int):
            cfg.port = v["port"]
        if v.get("protocol") in ("tcp", "udp"):
            cfg.protocol = v["protocol"]
        if v.get("fmt") in ("gelf", "syslog", "cef"):
            cfg.fmt = v["fmt"]
    _af_cache[AUDIT_FWD_KEY] = (now, cfg)
    return cfg


async def set_audit_forward(
    session: AsyncSession, *, data: dict[str, Any], updated_by_user_id: uuid.UUID
) -> AuditForwardConfig:
    from sqlalchemy.orm.attributes import flag_modified

    row = await session.get(SystemSetting, AUDIT_FWD_KEY)
    if row is None:
        row = SystemSetting(key=AUDIT_FWD_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    val = dict(row.value or {})
    for k in ("enabled", "host", "port", "protocol", "fmt"):
        if k in data:
            val[k] = data[k]
    row.value = val
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.commit()
    _af_cache.pop(AUDIT_FWD_KEY, None)
    return await get_audit_forward(session)


# ─────────────────── 通知發送管道（Email 已實作；其餘開發中）───────────────────
NOTIFY_CH_KEY = "notification_channels"
_NOTIFY_AAD = b"notification:smtp_password"
_ncfg_cache: dict[str, tuple[float, dict[str, Any]]] = {}

# 規劃支援的管道；available=False 者前端顯示但反灰（開發中）
NOTIFY_CHANNELS: tuple[tuple[str, bool], ...] = (
    ("email", True),
    ("telegram", True),
    ("slack", True),
    ("teams", True),
    ("nextcloud", True),
    ("zulip", True),
    ("webhook", True),
)

# 通知管道設定欄位（除 email/smtp 外的 webhook 類管道）
_NOTIFY_BOOL_KEYS = (
    "email_enabled", "telegram_enabled", "slack_enabled",
    "teams_enabled", "nextcloud_enabled", "zulip_enabled", "webhook_enabled",
)
_NOTIFY_STR_KEYS = (
    "smtp_host", "smtp_username", "smtp_from",
    "telegram_chat_id", "nextcloud_url", "nextcloud_token",
    "zulip_site", "zulip_bot_email", "zulip_stream", "zulip_topic",
)
# 明文設定欄位 -> 加密儲存欄位（get 會反向解密回明文欄位）
_NOTIFY_SECRETS = {
    "smtp_password": "smtp_password_enc",
    "telegram_token": "telegram_token_enc",
    "slack_webhook": "slack_webhook_enc",
    "teams_webhook": "teams_webhook_enc",
    "nextcloud_secret": "nextcloud_secret_enc",
    "zulip_api_key": "zulip_api_key_enc",
    "webhook_url": "webhook_url_enc",      # 通用 webhook（URL 可能含密鑰，一律加密）
    "webhook_token": "webhook_token_enc",  # 選填 Bearer token
}


def _enc_smtp(pw: str) -> str:
    ct, nonce = encrypt_secret(pw, aad=_NOTIFY_AAD)
    return "v1:" + base64.b64encode(nonce).decode() + ":" + base64.b64encode(ct).decode()


def _dec_smtp(blob: str) -> str | None:
    try:
        _ver, b_nonce, b_ct = blob.split(":", 2)
        return decrypt_secret(
            base64.b64decode(b_ct), base64.b64decode(b_nonce), aad=_NOTIFY_AAD
        ).decode("utf-8")
    except Exception:
        return None


def _default_notify() -> dict[str, Any]:
    cfg: dict[str, Any] = dict.fromkeys(_NOTIFY_BOOL_KEYS, False)
    cfg.update({"smtp_port": 587, "smtp_tls": "starttls"})  # none/starttls/tls
    for k in _NOTIFY_STR_KEYS:
        cfg[k] = None
    for enc in _NOTIFY_SECRETS.values():
        cfg[enc] = None
    return cfg


async def get_notification_channels(session: AsyncSession) -> dict[str, Any]:
    """回傳通知管道設定（含解密後的各管道密鑰；僅後端 send 用，API 層會遮蔽）。"""
    now = time.monotonic()
    c = _ncfg_cache.get(NOTIFY_CH_KEY)
    if c and now - c[0] < _TTL_SEC:
        return dict(c[1])
    cfg = _default_notify()
    row = await session.get(SystemSetting, NOTIFY_CH_KEY)
    if row and isinstance(row.value, dict):
        v = row.value
        for k in _NOTIFY_BOOL_KEYS:
            if isinstance(v.get(k), bool):
                cfg[k] = v[k]
        if isinstance(v.get("smtp_port"), int):
            cfg["smtp_port"] = v["smtp_port"]
        if v.get("smtp_tls") in ("none", "starttls", "tls"):
            cfg["smtp_tls"] = v["smtp_tls"]
        for k in (*_NOTIFY_STR_KEYS, *_NOTIFY_SECRETS.values()):
            if isinstance(v.get(k), str) and v[k]:
                cfg[k] = v[k]
    # 解密每個密鑰到對應明文欄位（smtp_password / telegram_token / slack_webhook / …）
    for plain, enc in _NOTIFY_SECRETS.items():
        cfg[plain] = _dec_smtp(cfg[enc]) if cfg.get(enc) else None
    _ncfg_cache[NOTIFY_CH_KEY] = (now, dict(cfg))
    return cfg


async def set_notification_channels(
    session: AsyncSession, *, data: dict[str, Any], updated_by_user_id: uuid.UUID,
) -> dict[str, Any]:
    from sqlalchemy.orm.attributes import flag_modified
    row = await session.get(SystemSetting, NOTIFY_CH_KEY)
    if row is None:
        row = SystemSetting(key=NOTIFY_CH_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    val = dict(row.value or {})
    for k in (*_NOTIFY_BOOL_KEYS, "smtp_port", "smtp_tls", *_NOTIFY_STR_KEYS):
        if k in data:
            val[k] = data[k]
    # 密鑰：給了非空字串才更新（空字串/未給 = 保留原本）；明確傳 null/"" 清除
    for plain, enc in _NOTIFY_SECRETS.items():
        if plain in data:
            secret = data[plain]
            if secret:
                val[enc] = _enc_smtp(str(secret))
            elif secret == "" or secret is None:
                val.pop(enc, None)
    row.value = val
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.commit()
    _ncfg_cache.pop(NOTIFY_CH_KEY, None)
    return await get_notification_channels(session)


# ─────────────────── 通知矩陣（哪些事件、走哪些管道）───────────────────
NOTIFY_MATRIX_KEY = "notification_matrix"
# 可通知事件登錄（矩陣的列）：(key, 預設站內, 預設 email)。新增事件只要在這裡加一列。
# 異常偵測逐類別的通知事件。
#
# 原本只有一列 `anomaly.detected`：十種發現要嘛全通知、要嘛全不通知。但這十種的份量
# 差很多 ——「非法 DHCP 伺服器」要立刻處理，「失聯 IP」比較像每週整理一次的清單。
# 混在一起的下場是使用者為了不被吵而整類關掉，真正要緊的那幾種也一起消失。
#
# 舊的 `anomaly.detected` 不再出現在設定頁（一列講不出作用的總開關只會讓人猜誰說了算），
# 但**仍然是升級時的預設來源**：已經把異常通知的 Email 打開的站台，升級後十類都還開著。
ANOMALY_EVENTS: tuple[str, ...] = (
    "anomaly.ip_conflicts",
    "anomaly.arp_flux",
    "anomaly.l2_subnet_bleed",
    "anomaly.mac_drifts",
    "anomaly.ghost_ips",
    "anomaly.unauthorized_ips",
    "anomaly.rogue_dhcp",
    "anomaly.external_exposure",
    "anomaly.dangling_dns",
    "anomaly.duplicate_ip_records",
    "anomaly.suspicious_changes",
    "anomaly.fw_rule_rot",
    "anomaly.mac_flapping",
    "anomaly.identity_changes",
)
LEGACY_ANOMALY_EVENT = "anomaly.detected"

NOTIFY_EVENTS: tuple[tuple[str, bool, bool], ...] = (
    ("ip_request.created", True, True),    # 審核者：有新 IP 申請待審
    ("ip_request.approved", True, True),   # 申請人：申請已核准（含配發 IP）
    ("ip_request.rejected", True, True),   # 申請人：申請已拒絕
    ("cert.expiring", True, False),        # 憑證即將到期 / 已過期
    # API 權杖、對外 MCP 金鑰、DSV／機櫃嵌入權杖即將到期（14／7／1 天與當天各一次）
    ("credential.expiring", True, False),
    ("cert.deployed", True, False),        # 代理成功部署新憑證
    ("cert.drift", True, False),           # 憑證飄移（某代理未套到最新版）
    *((ev, True, False) for ev in ANOMALY_EVENTS),   # 異常偵測（逐類別）
    ("firewall.rules_changed", True, False),  # 防火牆規則有異動
    # 「東西壞了卻沒人知道」三類。只在開始與恢復時發（見 services/state_alert）。
    ("integration.sync_failed", True, False),  # 整合同步失敗／恢復
    ("dns.compare_mismatch", True, False),        # DNS 比對群組各台的紀錄不一樣（超過寬限時間才發；services/dns_compare）
    ("dns.compare_resolved", True, False),        # DNS 比對群組恢復一致
    ("agent.offline", True, False),            # 掃描／憑證代理失聯／恢復
    ("agent.overloaded", True, False),         # 掃描代理負載過重（連續 3 輪）／恢復
    ("identify.done", True, False),            # 自己發起的 IP 探測完成／失敗（只通知發起人）
    ("system.health", True, False),            # 系統檢查未通過／恢復
    ("dhcp.pool_exhausted", True, False),      # DHCP 集區快用完／回到門檻以下
    ("jump_host.key_changed", True, True),     # 跳板主機金鑰改變（資安事件，預設連 Email 都開）
    ("cert.fetch_failed", True, False),        # 憑證來源抓取失敗／恢復
    # 這兩個原本直接推通知、設定頁上沒有對應的列 —— 使用者收得到卻關不掉
    ("audit.chain_broken", True, True),        # 稽核鏈驗證失敗（資安事件）
    ("ip.stale", True, False),                 # 失聯 IP 提醒
    # 權限變更是低頻高影響 → 預設連 Email 都開；暴力破解只在「多個帳號同時被鎖」時發
    ("security.privilege_changed", True, True),
    # 已經換掉的更新權杖又被使用（疑似盜用；工作階段已自動撤銷）
    ("security.session_reuse", True, True),
    ("security.brute_force", True, False),
    # RustDesk 客戶端回報的告警（密碼一分鐘錯 6 次、累計 30 次、允許清單違規…）；同一台同一類 10 分鐘內只發一次
    ("rustdesk.alarm", True, False),
    # IP 變更評估：送審通知審核人（跟 IP 申請一樣預設連 Email）、覆核結果通知建立者
    ("change_impact.submitted", True, True),
    ("change_impact.reviewed", True, False),
)


def _default_matrix() -> dict[str, dict[str, bool]]:
    return {k: {"in_app": ia, "email": em} for k, ia, em in NOTIFY_EVENTS}


async def get_notification_matrix(session: AsyncSession) -> dict[str, dict[str, bool]]:
    """回傳通知矩陣 {event: {in_app, email}}，未設定的事件用預設值補齊。"""
    out = _default_matrix()
    row = await session.get(SystemSetting, NOTIFY_MATRIX_KEY)
    stored = row.value if row and isinstance(row.value, dict) else {}

    # 升級路徑：舊資料只有一列 anomaly.detected。逐類別的設定還沒存在時，
    # 沿用那一列的值 —— 否則已經打開 Email 的站台會在升級當下被靜靜關掉，
    # 而畫面上看起來只是「預設值」。第一次儲存之後就以逐類別的設定為準。
    legacy = stored.get(LEGACY_ANOMALY_EVENT)
    if isinstance(legacy, dict):
        for ev in ANOMALY_EVENTS:
            if ev in stored:
                continue
            if isinstance(legacy.get("in_app"), bool):
                out[ev]["in_app"] = legacy["in_app"]
            if isinstance(legacy.get("email"), bool):
                out[ev]["email"] = legacy["email"]

    for k, v in stored.items():
        if k in out and isinstance(v, dict):
            if isinstance(v.get("in_app"), bool):
                out[k]["in_app"] = v["in_app"]
            if isinstance(v.get("email"), bool):
                out[k]["email"] = v["email"]
    return out


async def set_notification_matrix(
    session: AsyncSession, *, data: dict[str, Any], updated_by_user_id: uuid.UUID,
) -> dict[str, dict[str, bool]]:
    from sqlalchemy.orm.attributes import flag_modified
    row = await session.get(SystemSetting, NOTIFY_MATRIX_KEY)
    if row is None:
        row = SystemSetting(key=NOTIFY_MATRIX_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    defaults = _default_matrix()
    val: dict[str, dict[str, bool]] = {}
    for k, dflt in defaults.items():
        v = data.get(k) if isinstance(data, dict) else None
        ia = bool(v["in_app"]) if isinstance(v, dict) and isinstance(v.get("in_app"), bool) else dflt["in_app"]
        em = bool(v["email"]) if isinstance(v, dict) and isinstance(v.get("email"), bool) else dflt["email"]
        val[k] = {"in_app": ia, "email": em}
    row.value = val
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.commit()
    return await get_notification_matrix(session)


# ─────────────────── 依 MAC 自動掛裝置（ip_device_autolink）───────────────────
AUTOLINK_KEY = "ip_device_autolink"


#: 哪些證據可以用來判定「上線」。ARP 預設不勾 —— 它證明的是「某個 MAC↔IP 對應被學到過」，
#: 不是機器現在活著；而且 LibreNMS 的 ARP API 連時間都不回，來源設備的快取不老化就會
#: 永遠看起來「剛剛才看到」（實機上讓一台關機的 VM 顯示 52 天全綠）。
ONLINE_GRACE_KEY = "online_grace_minutes"


async def get_liveness_config(session: AsyncSession) -> dict[str, Any]:
    """上線判定：閾值（分鐘）＋哪些來源算數。"""
    from app.services.evidence import LIVENESS_SOURCES, default_liveness_sources
    row = await session.get(SystemSetting, ONLINE_GRACE_KEY)
    v = row.value if row and isinstance(row.value, dict) else {}
    try:
        minutes = int(v.get("minutes") or 30)
    except (TypeError, ValueError):
        minutes = 30
    raw = v.get("sources")
    if isinstance(raw, list):
        sources = [str(x) for x in raw if str(x) in LIVENESS_SOURCES]
    else:
        sources = default_liveness_sources()
    return {"minutes": min(43200, max(1, minutes)), "sources": sources}


async def get_autolink_config(session: AsyncSession) -> dict[str, Any]:
    """是否讓每輪同步依網卡 MAC 把 IP 掛回所屬裝置，以及限定的子網路範圍。

    **預設關閉（deny by default）。** 升級之後突然多出一個每 5 分鐘自動改資料的背景
    作業，本身就是不該發生的事 —— 使用者沒要求過。範圍留空＝全部子網路，比照本專案
    其他整合的 `scope_subnet_ids` 慣例（重疊網段下要能把範圍收到確定乾淨的網段）。
    """
    row = await session.get(SystemSetting, AUTOLINK_KEY)
    v = row.value if row and isinstance(row.value, dict) else {}
    scope = v.get("scope_subnet_ids")
    return {
        "enabled": bool(v.get("enabled", False)),
        "scope_subnet_ids": [str(x) for x in scope] if isinstance(scope, list) and scope else None,
    }


async def set_autolink_config(
    session: AsyncSession, *, enabled: bool | None = None,
    scope_subnet_ids: list[str] | None = None,
    updated_by_user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    row = await session.get(SystemSetting, AUTOLINK_KEY)
    if row is None:
        row = SystemSetting(key=AUTOLINK_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    current = dict(row.value or {})
    if enabled is not None:
        current["enabled"] = bool(enabled)
    if scope_subnet_ids is not None:
        # 空清單＝清掉範圍限制（回到全部子網路），不是「一個都不含」
        current["scope_subnet_ids"] = [str(x) for x in scope_subnet_ids] or None
    row.value = current
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    return await get_autolink_config(session)


# ── 憑證到期通知的全域預設天數 ───────────────────────────────
CERT_EXPIRY_KEY = "cert_expiry_alert"
CERT_EXPIRY_DEFAULT_DAYS = 21


async def get_cert_expiry_days(session: AsyncSession) -> int:
    """到期前幾天開始通知的**全域預設**。逐張憑證可以各自覆寫。"""
    from app.models.system_setting import SystemSetting

    row = await session.get(SystemSetting, CERT_EXPIRY_KEY)
    val = row.value if row and isinstance(row.value, dict) else {}
    try:
        days = int(val.get("days", CERT_EXPIRY_DEFAULT_DAYS))
    except (TypeError, ValueError):
        days = CERT_EXPIRY_DEFAULT_DAYS
    return max(1, min(365, days))


async def set_cert_expiry_days(
    session: AsyncSession, *, days: int, updated_by_user_id: Any = None,
) -> int:
    from app.models.system_setting import SystemSetting

    clean = max(1, min(365, int(days)))
    row = await session.get(SystemSetting, CERT_EXPIRY_KEY)
    if row is None:
        row = SystemSetting(key=CERT_EXPIRY_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    row.value = {"days": clean}
    row.updated_by = updated_by_user_id
    return clean


# ─────────────────── 每日備份加密（2026-10-09）───────────────────
# 密碼加密存在這裡（綁定用途）；每日備份（root 的 jt-ipam-backup.sh）透過 app.cli.backup_encrypt
# 取出來把整包備份加密成 .jtbak（格式見 services/backup_crypt）。沒設定就維持原本的明文目錄，
# 系統診斷會提醒。
BACKUP_ENC_KEY = "backup_encryption"
BACKUP_PASS_AAD = b"setting:backup_encryption:passphrase"


async def get_backup_encryption(session: AsyncSession) -> dict[str, Any]:
    """{enabled, set_at, passphrase（明文，僅程序內用）}。"""
    row = await session.get(SystemSetting, BACKUP_ENC_KEY)
    v = dict(row.value) if (row and isinstance(row.value, dict)) else {}
    pw = _dec(str(v["passphrase_enc"]), BACKUP_PASS_AAD) if v.get("passphrase_enc") else None
    return {"enabled": bool(pw), "set_at": v.get("set_at"), "passphrase": pw}


async def set_backup_encryption(session: AsyncSession, passphrase: str | None, *,
                                updated_by: uuid.UUID | None) -> None:
    """設定（或用 None 移除）備份加密密碼。"""
    row = await session.get(SystemSetting, BACKUP_ENC_KEY)
    value: dict[str, Any] = {}
    if passphrase:
        value = {"passphrase_enc": _enc(passphrase, BACKUP_PASS_AAD), "set_at": datetime.now(UTC).isoformat()}
    if row is None:
        session.add(SystemSetting(key=BACKUP_ENC_KEY, value=value, updated_by=updated_by))
    else:
        row.value = value
        row.updated_by = updated_by
        flag_modified(row, "value")
    await session.flush()
