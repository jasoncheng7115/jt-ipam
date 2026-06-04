"""device.rack_face — 裝置安裝方向（機櫃前面 / 後面）

Revision ID: 0057_device_rack_face
Revises: 0056_opnsense_synced_aliases
Create Date: 2026-06-02

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0057_device_rack_face"
down_revision: str | None = "0056_opnsense_synced_aliases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("rack_face", sa.String(8), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "rack_face")
