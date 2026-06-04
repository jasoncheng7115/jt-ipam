"""OPNsense extra sync flags (DHCP / ARP / OpenVPN).

Revision ID: 0016_opnsense_extra_sync
Revises: 0015_background_tasks
Create Date: 2026-05-26 17:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_opnsense_extra_sync"
down_revision: str | None = "0015_background_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("opnsense_firewalls",
                  sa.Column("sync_dhcp", sa.Boolean, nullable=False, server_default="false"))
    op.add_column("opnsense_firewalls",
                  sa.Column("sync_arp", sa.Boolean, nullable=False, server_default="false"))
    op.add_column("opnsense_firewalls",
                  sa.Column("sync_openvpn", sa.Boolean, nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("opnsense_firewalls", "sync_openvpn")
    op.drop_column("opnsense_firewalls", "sync_arp")
    op.drop_column("opnsense_firewalls", "sync_dhcp")
