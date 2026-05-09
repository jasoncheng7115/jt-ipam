"""SQLAlchemy 2.0 ORM models."""

from app.models.address import IPAddress
from app.models.audit import AuditLog
from app.models.base import Base
from app.models.custom_field import CustomFieldDefinition
from app.models.device import Device
from app.models.encrypted_secret import EncryptedSecret
from app.models.ip_request import IPRequest, IPRequestEvent
from app.models.location import Location, Rack
from app.models.migration_mapping import PhpIPAMMigrationMapping
from app.models.nat import NATTranslation
from app.models.notification import Notification, WebhookSubscription
from app.models.permission import Permission
from app.models.scan_agent import ScanAgent
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.user import APIToken, Group, User, UserGroupMember, UserPreference
from app.models.vlan import VLAN, VLANDomain
from app.models.vrf import VRF

__all__ = [
    "APIToken",
    "AuditLog",
    "Base",
    "CustomFieldDefinition",
    "Device",
    "EncryptedSecret",
    "Group",
    "IPAddress",
    "IPRequest",
    "IPRequestEvent",
    "Location",
    "NATTranslation",
    "Notification",
    "Permission",
    "PhpIPAMMigrationMapping",
    "Rack",
    "ScanAgent",
    "Section",
    "Subnet",
    "User",
    "UserGroupMember",
    "UserPreference",
    "VLAN",
    "VLANDomain",
    "VRF",
    "WebhookSubscription",
]
