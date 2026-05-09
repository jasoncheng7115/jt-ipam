"""SQLAlchemy 2.0 ORM models."""

from app.models.address import IPAddress
from app.models.advanced import (
    ASN,
    Circuit,
    CircuitType,
    Contact,
    ContactAssignment,
    ContactGroup,
    ContactRole,
    Provider,
    Tenant,
    TenantGroup,
    WirelessLink,
    WirelessSSID,
)
from app.models.audit import AuditLog
from app.models.base import Base
from app.models.custom_field import CustomFieldDefinition
from app.models.device import Device
from app.models.dns import DNSRecord, DNSServer, DNSZone
from app.models.encrypted_secret import EncryptedSecret
from app.models.firewall import OPNsenseAliasMapping, OPNsenseFirewall
from app.models.ip_request import IPRequest, IPRequestEvent
from app.models.librenms import ARPEntry, FDBEntry, LibreNMSDevice, LibreNMSInstance
from app.models.location import Location, Rack
from app.models.migration_mapping import PhpIPAMMigrationMapping
from app.models.nat import NATTranslation
from app.models.notification import Notification, WebhookSubscription
from app.models.permission import Permission
from app.models.physical import (
    Cable,
    CableTermination,
    PowerFeed,
    PowerOutlet,
    PowerPanel,
    VPNTunnel,
)
from app.models.scan_agent import ScanAgent
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.user import APIToken, Group, User, UserGroupMember, UserPreference
from app.models.virt import (
    ProxmoxInstance,
    VirtCluster,
    VirtualMachine,
    VMInterface,
)
from app.models.vlan import VLAN, VLANDomain
from app.models.vrf import VRF

__all__ = [
    "APIToken",
    "ARPEntry",
    "ASN",
    "AuditLog",
    "Base",
    "Cable",
    "CableTermination",
    "Circuit",
    "CircuitType",
    "Contact",
    "ContactAssignment",
    "ContactGroup",
    "ContactRole",
    "CustomFieldDefinition",
    "DNSRecord",
    "DNSServer",
    "DNSZone",
    "Device",
    "EncryptedSecret",
    "FDBEntry",
    "Group",
    "IPAddress",
    "IPRequest",
    "IPRequestEvent",
    "LibreNMSDevice",
    "LibreNMSInstance",
    "Location",
    "NATTranslation",
    "Notification",
    "OPNsenseAliasMapping",
    "OPNsenseFirewall",
    "Permission",
    "PhpIPAMMigrationMapping",
    "PowerFeed",
    "PowerOutlet",
    "PowerPanel",
    "Provider",
    "ProxmoxInstance",
    "Rack",
    "ScanAgent",
    "Section",
    "Subnet",
    "Tenant",
    "TenantGroup",
    "User",
    "UserGroupMember",
    "UserPreference",
    "VLAN",
    "VLANDomain",
    "VMInterface",
    "VPNTunnel",
    "VRF",
    "VirtCluster",
    "VirtualMachine",
    "WebhookSubscription",
    "WirelessLink",
    "WirelessSSID",
]
