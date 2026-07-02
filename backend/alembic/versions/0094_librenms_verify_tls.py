"""librenms_instances.verify_tls — per-instance TLS verification toggle

LibreNMS 常以自簽憑證跑（CN/SAN 可能是主機名稱而非 IP）。原本後端一律驗證憑證，
自簽或主機名稱不符就 transport ConnectError。新增 verify_tls（預設 true）：關閉時
httpx verify=False，接受自簽/名稱不符（等同 Wazuh 的 Verify TLS 開關）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0094_librenms_verify_tls"
down_revision: str | None = "0093_notification_i18n"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "librenms_instances",
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    op.drop_column("librenms_instances", "verify_tls")
