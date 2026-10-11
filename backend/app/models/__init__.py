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
from app.models.ai_finding import AIFinding
from app.models.audit import AuditLog
from app.models.background_task import BackgroundTask
from app.models.base import Base
from app.models.certificate import CertAgent, Certificate, CertVersion
from app.models.change_impact import (
    ChangePlan,
    ChangePlanRevision,
    ChangeTask,
    ImpactAIArtifact,
    ImpactDependencyGroup,
    ImpactDependencyMember,
    ImpactEvidence,
    ImpactFinding,
    ImpactGap,
    ImpactRelation,
    ImpactReview,
    ImpactRun,
    ImpactService,
    ImpactServiceEndpoint,
)
from app.models.checkpoint import (
    CheckPointGateway,
    CheckPointObject,
    CheckPointRule,
    CheckPointServer,
)
from app.models.checkpoint_gaia import CheckPointDhcpSubnet, CheckPointGaiaTarget
from app.models.custom_field import CustomFieldDefinition
from app.models.customer import Customer
from app.models.device import Device
from app.models.dhcp import DHCPPoolRange
from app.models.dhcp_sighting import DHCPSighting
from app.models.dhcp_standalone import IscDhcpServer, KeaDhcpServer
from app.models.dns import DNSRecord, DNSServer, DNSZone
from app.models.dns_compare_group import DNSCompareGroup, DNSCompareGroupDiff
from app.models.encrypted_secret import EncryptedSecret
from app.models.event_rule import EventRule
from app.models.firewall import (
    OPNsenseAliasMapping,
    OPNsenseFirewall,
    OPNsenseRuleLabel,
    OPNsenseSyncedAlias,
)
from app.models.firewall_rule import OPNsenseRule
from app.models.fortigate import (
    FortiGateAddressObject,
    FortiGateFirewall,
    FortiGatePolicy,
)
from app.models.fw_snapshot import FwRuleSnapshot
from app.models.ip_change_log import IPChangeLog
from app.models.ip_cooldown import IPCooldown
from app.models.ip_hostname import IPHostnameObservation
from app.models.ip_liveness import IPLivenessDay
from app.models.ip_range import IPRange
from app.models.ip_request import IPRequest, IPRequestEvent, IPRequestStageApproval
from app.models.isoinsight import IsoInsightLease, IsoInsightSource, IsoInsightSyncRun
from app.models.jump_host import JumpHost
from app.models.librenms import ARPEntry, FDBEntry, LibreNMSDevice, LibreNMSInstance
from app.models.location import Location, Rack
from app.models.migration_mapping import PhpIPAMMigrationMapping
from app.models.mikrotik import MikroTikAddressList, MikroTikNeighbor, MikroTikRouter, MikroTikRule
from app.models.nat import NATTranslation
from app.models.notification import Notification, WebhookSubscription
from app.models.oui import OUIVendor
from app.models.paloalto import (
    PaloAltoAddressObject,
    PaloAltoFirewall,
    PaloAltoPolicy,
)
from app.models.permission import Permission
from app.models.pfsense import PfSenseFirewall, PfSenseSyncedAlias
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
from app.models.pve_firewall import (
    PVEFirewallGroup,
    PVEFirewallIPSet,
    PVEFirewallRule,
    PVEFirewallState,
)
from app.models.recog import RecogDatabase
from app.models.rustdesk import RustDeskAuditEvent, RustDeskPeer, RustDeskPeerDelete, RustDeskServer
from app.models.scan_agent import ScanAgent
from app.models.scan_agent_cycle import ScanAgentCycle
from app.models.section import Section
from app.models.ssh_credential import SSHCredential
from app.models.subnet import Subnet
from app.models.system_setting import SystemSetting
from app.models.technitium import TechnitiumDhcpScope, TechnitiumDhcpServer
from app.models.unmanaged_sighting import UnmanagedSighting
from app.models.user import APIToken, Group, User, UserGroupMember, UserPreference
from app.models.user_session import UserRecoveryCode, UserSession
from app.models.virt import (
    ProxmoxInstance,
    VirtCluster,
    VirtualMachine,
    VMInterface,
)
from app.models.vlan import VLAN, DeviceVLAN, VLANDomain
from app.models.vrf import VRF
from app.models.wazuh import WazuhAgent, WazuhInstance
from app.models.windows_dhcp import WindowsDhcpServer
from app.models.zabbix import ZabbixHost, ZabbixInstance

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
    "ChangePlan",
    "ChangePlanRevision",
    "ChangeTask",
    "Circuit",
    "CircuitType",
    "Contact",
    "ContactAssignment",
    "ContactGroup",
    "ContactRole",
    "CustomFieldDefinition",
    "DHCPPoolRange",
    "DNSCompareGroup",
    "DNSCompareGroupDiff",
    "DNSRecord",
    "DNSServer",
    "DNSZone",
    "Device",
    "DevicePort",
    "DevicePowerPort",
    "DeviceVLAN",
    "EncryptedSecret",
    "EventRule",
    "FDBEntry",
    "Group",
    "IPAddress",
    "IPChangeLog",
    "IPCooldown",
    "IPHostnameObservation",
    "IPLivenessDay",
    "IPRange",
    "IPRequest",
    "IPRequestEvent",
    "IPRequestStageApproval",
    "ImpactAIArtifact",
    "ImpactDependencyGroup",
    "ImpactDependencyMember",
    "ImpactEvidence",
    "ImpactFinding",
    "ImpactGap",
    "ImpactRelation",
    "ImpactReview",
    "ImpactRun",
    "ImpactService",
    "ImpactServiceEndpoint",
    "JumpHost",
    "LibreNMSDevice",
    "LibreNMSInstance",
    "Location",
    "MikroTikAddressList",
    "MikroTikNeighbor",
    "MikroTikRouter",
    "MikroTikRule",
    "NATTranslation",
    "Notification",
    "OPNsenseAliasMapping",
    "OPNsenseFirewall",
    "OPNsenseRuleLabel",
    "OPNsenseSyncedAlias",
    "PVEFirewallGroup",
    "PVEFirewallIPSet",
    "PVEFirewallRule",
    "PVEFirewallState",
    "Permission",
    "PfSenseFirewall",
    "PfSenseSyncedAlias",
    "PhpIPAMMigrationMapping",
    "PowerFeed",
    "PowerOutlet",
    "PowerPanel",
    "Provider",
    "ProxmoxInstance",
    "Rack",
    "SSHCredential",
    "ScanAgent",
    "ScanAgentCycle",
    "Section",
    "Subnet",
    "Tenant",
    "TenantGroup",
    "UnmanagedSighting",
    "User",
    "UserGroupMember",
    "UserPreference",
    "UserRecoveryCode",
    "UserSession",
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
    "ZabbixHost",
    "ZabbixInstance",
]
