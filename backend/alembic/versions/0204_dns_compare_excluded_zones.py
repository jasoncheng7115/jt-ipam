"""dns_compare_groups.excluded_zones：比對群組裡不比對的 zone；dns_compare_group_diffs.confirmed_at：差異確認的時間

2026-10-11 真實環境驗證（PowerDNS 主要＋BIND9、Technitium 次要）：Technitium 另外放了一個沒有複寫的 zone，
整組就永遠「不一致」。主從之間只複寫部分 zone、或某台另外放自己的 zone 很常見（UCS 與只放 AD zone 的 Windows DC），
所以群組要能設定不比對的 zone（正規化後的名稱清單；空清單＝全部都比）。

同一次驗證也看到：三台依序拉取，第一台一拉完就比對，另外兩台用的還是舊資料，寬限 0 分鐘就當場告警
「另外兩台沒有」（其中一台其實有）。改成用資料的時間判斷：沒有的那台要在差異出現（再加寬限時間）之後重新拉取過、
仍然沒有才算確認，確認的時間記在 confirmed_at（以前用「比對當下距離第一次看到多久」算）。

Revision ID: 0204_dns_compare_excluded_zones
Revises: 0203_dns_compare_groups
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0204_dns_compare_excluded_zones"
down_revision: str | None = "0203_dns_compare_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dns_compare_groups", sa.Column(
        "excluded_zones", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("dns_compare_group_diffs", sa.Column("confirmed_at", sa.DateTime(timezone=True)))
    # 既有差異下一次比對時依新規則重算（現在還沒有確認時間＝待確認，不會補發通知）


def downgrade() -> None:
    op.drop_column("dns_compare_group_diffs", "confirmed_at")
    op.drop_column("dns_compare_groups", "excluded_zones")
