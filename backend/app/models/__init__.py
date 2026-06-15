"""SQLAlchemy 2.0 ORM models."""

from app.models.address import IPAddress
from app.models.adguard import AdGuardInstance
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
from app.models.ai_chat import AIChatConversation, AIChatMessage
from app.models.audit import AuditLog
from app.models.background_task import BackgroundTask
from app.models.base import Base
from app.models.certificate import CertAgent, Certificate, CertVersion
from app.models.custom_field import CustomFieldDefinition
from app.models.customer import Customer
from app.models.device import Device
from app.models.dhcp import DHCPPoolRange
from app.models.dns import DNSRecord, DNSServer, DNSZone
from app.models.encrypted_secret import EncryptedSecret
from app.models.firewall import OPNsenseAliasMapping, OPNsenseFirewall, OPNsenseSyncedAlias
from app.models.firewall_rule import OPNsenseRule
from app.models.ip_change_log import IPChangeLog
from app.models.ip_hostname import IPHostnameObservation
from app.models.ip_request import IPRequest, IPRequestEvent, IPRequestStageApproval
from app.models.librenms import ARPEntry, FDBEntry, LibreNMSDevice, LibreNMSInstance
from app.models.location import Location, Rack
from app.models.migration_mapping import PhpIPAMMigrationMapping
from app.models.nat import NATTranslation
from app.models.notification import Notification, WebhookSubscription
from app.models.oui import OUIVendor
from app.models.permission import Permission
from app.models.physical import (
    Cable,
    CableTermination,
    DevicePort,
    DevicePowerPort,
    PowerFeed,
    PowerOutlet,
    PowerPanel,
    VPNTunnel,
)
from app.models.scan_agent import ScanAgent
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.system_setting import SystemSetting
from app.models.user import APIToken, Group, User, UserGroupMember, UserPreference
from app.models.virt import (
    ProxmoxInstance,
    VirtCluster,
    VirtualMachine,
    VMInterface,
)
from app.models.vlan import VLAN, DeviceVLAN, VLANDomain
from app.models.vrf import VRF
from app.models.wazuh import WazuhAgent, WazuhInstance

__all__ = [
    "ASN",
    "VLAN",
    "VRF",
    "AIChatConversation",
    "AIChatMessage",
    "APIToken",
    "ARPEntry",
    "AuditLog",
    "Base",
    "Cable",
    "CableTermination",
    "CertAgent",
    "CertVersion",
    "Certificate",
    "Circuit",
    "CircuitType",
    "Contact",
    "ContactAssignment",
    "ContactGroup",
    "ContactRole",
    "CustomFieldDefinition",
    "DHCPPoolRange",
    "DNSRecord",
    "DNSServer",
    "DNSZone",
    "Device",
    "DevicePort",
    "DevicePowerPort",
    "DeviceVLAN",
    "EncryptedSecret",
    "FDBEntry",
    "Group",
    "IPAddress",
    "IPChangeLog",
    "IPHostnameObservation",
    "IPRequest",
    "IPRequestEvent",
    "IPRequestStageApproval",
    "LibreNMSDevice",
    "LibreNMSInstance",
    "Location",
    "NATTranslation",
    "Notification",
    "OPNsenseAliasMapping",
    "OPNsenseFirewall",
    "OPNsenseSyncedAlias",
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
    "VLANDomain",
    "VMInterface",
    "VPNTunnel",
    "VirtCluster",
    "VirtualMachine",
    "WazuhAgent",
    "WazuhInstance",
    "WebhookSubscription",
    "WirelessLink",
    "WirelessSSID",
]
