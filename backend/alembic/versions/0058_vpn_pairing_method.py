"""vpn_tunnels.pairing_method — 對接判定方式（公鑰可靠 / IPsec 端點 best-effort）

Revision ID: 0058_vpn_pairing_method
Revises: 0057_device_rack_face
Create Date: 2026-06-02

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0058_vpn_pairing_method"
down_revision: str | None = "0057_device_rack_face"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vpn_tunnels", sa.Column("pairing_method", sa.String(24), nullable=True))


def downgrade() -> None:
    op.drop_column("vpn_tunnels", "pairing_method")
