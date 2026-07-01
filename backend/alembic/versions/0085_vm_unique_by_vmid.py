"""virtual_machines unique by (cluster, vmid) instead of (cluster, name)  — issue #8

Revision ID: 0085_vm_unique_by_vmid
Revises: 0084_ip_vnc_enabled
Create Date: 2026-06-24

Proxmox 允許同一叢集內多台 VM 同名（VMID 不同）。原本唯一鍵 (cluster_id, name) 會讓「名稱相同、
VMID 不同」的 VM 匯入時撞 vm_cluster_name_uq 而失敗（GitHub issue #8）。sync 早就以 (cluster_id,
legacy_vmid) 為對映鍵，故把唯一鍵改成 (cluster_id, legacy_vmid)。
legacy_vmid 可為 NULL（非 Proxmox 來源的 VM）；Postgres 視 NULL 為相異，故多筆 NULL 不衝突。
"""

from __future__ import annotations

from alembic import op

revision: str = "0085_vm_unique_by_vmid"
down_revision: str | None = "0084_ip_vnc_enabled"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint("vm_cluster_name_uq", "virtual_machines", type_="unique")
    op.create_unique_constraint(
        "vm_cluster_vmid_uq", "virtual_machines", ["cluster_id", "legacy_vmid"]
    )


def downgrade() -> None:
    op.drop_constraint("vm_cluster_vmid_uq", "virtual_machines", type_="unique")
    op.create_unique_constraint(
        "vm_cluster_name_uq", "virtual_machines", ["cluster_id", "name"]
    )
