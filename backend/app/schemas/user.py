"""User schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import StringConstraints

from app.schemas.base import StrictModel


class UserMe(StrictModel):
    """`/me` 回傳的精簡資料；不洩漏 password_hash / totp_secret 等。"""

    id: uuid.UUID
    username: str
    # 不用 EmailStr — 內部部署常用 .local / .lan TLD（reserved domains）。
    # 可以是空的：email 從 2026-10-22 起是選用的，以前這裡還要求至少 3 個字元，沒填 email 的帳號一登入就 500
    email: Annotated[str, StringConstraints(max_length=255)]
    display_name: str | None
    auth_provider: str
    is_active: bool
    is_admin: bool
    # 此帳號是否已啟用 TOTP 雙因素驗證（前端在「個人設定 → 安全」顯示狀態、決定顯示啟用/停用鈕）
    totp_enabled: bool = False
    # 管理員的 MFA 政策要求這個帳號一定要開（不能停用 TOTP）
    mfa_required_by_policy: bool = False
    # 還沒用過的復原碼組數（0 而且已啟用 TOTP → 提醒重新產生）
    recovery_codes_remaining: int = 0
    # 非管理員是否對任何物件類型有可見範圍；前端用來隱藏零權限看不到的選單
    has_visibility: bool = True
    # 是否具「全域讀取」（管理員或任一類型有萬用授權）；前端用來隱藏全域基礎設施選單
    has_global_read: bool = True
    # 是否擁有任一 write/admin 授權；前端用來反灰「新增/編輯/刪除」按鈕（純唯讀→False）
    can_edit: bool = True
    # 獨立的「連線管理權限」：可對可檢視且已啟用 SSH/RDP 的 IP 開連線
    can_ssh: bool = False
    # 後端是否裝了 RDP/VNC 選用相依（aardwolf）；前端用來隱藏對應入口
    rdp_supported: bool = False
    vnc_supported: bool = False
    bmc_supported: bool = False   # 後端是否具備 IPMI 工具鏈（BMC SOL 主控台）
    ai_enabled: bool = False   # 全域 LLM/AI 是否啟用（前端據此顯示/隱藏 AI 對話小工具）
    # 資料庫結構落後於程式（只給 admin）。落後時畫面會到處 500，而系統其實早就知道 ——
    # 這個旗標讓管理員在踩到之前就看得見（客戶回報過：儀表板正常、裝置清單 500）。
    schema_behind: bool = False
    last_login_at: datetime | None
    created_at: datetime
