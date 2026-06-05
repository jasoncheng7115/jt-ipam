# phpIPAM v1.7 API Compatibility Layer — Mapping

> 繁體中文版：[PHPIPAM_API_MAPPING.md](PHPIPAM_API_MAPPING.md)

> Goal: existing phpIPAM scripts migrate with zero changes; path prefix `/api/phpipam/<app_id>/`
>
> Phase 1 covers the 8 major categories: Sections / Subnets / Addresses / VLANs / VRFs / Devices / Tools / User. The rest (Folder/Locations/L2Domains/Circuits/Tags) land in Phase 2.
>
> Security considerations:
> - Auth tokens use the phpIPAM mechanism (`POST /user/`); tokens are stored internally in jt-ipam as `api_tokens` with an encrypted hash (A02 / A07)
> - Every endpoint applies RBAC checks; Section/Subnet permissions are shared with the modern API (A01)
> - Output is wrapped in phpIPAM style `{success, data, message, time}`, but internally still goes through Pydantic validation (A03)

---

## 1. Authentication

| phpIPAM endpoint | jt-ipam endpoint | Notes |
|---|---|---|
| `POST /api/<app_id>/user/` | `POST /api/phpipam/<app_id>/user/` | obtain token |
| `DELETE /api/<app_id>/user/` | `DELETE /api/phpipam/<app_id>/user/` | revoke token |
| `PATCH /api/<app_id>/user/` | `PATCH /api/phpipam/<app_id>/user/` | extend token |
| `GET /api/<app_id>/user/` | `GET /api/phpipam/<app_id>/user/` | token info |

**Security hardening**: token TTL is mandatory, max 1 year; no plaintext written on creation.

---

## 2. Sections

| phpIPAM | jt-ipam |
|---|---|
| `GET    /sections/` | `GET /api/phpipam/<app>/sections/` |
| `GET    /sections/{id}/` | same |
| `GET    /sections/{name}/` | same (by name) |
| `GET    /sections/{id}/subnets/` | same |
| `POST   /sections/` | same |
| `PATCH  /sections/{id}/` | same |
| `DELETE /sections/{id}/` | same |

**Maps to internal model**: `Section`. Mandatory in Phase 1.

---

## 3. Subnets

| phpIPAM | jt-ipam |
|---|---|
| `GET /subnets/{id}/` | same |
| `GET /subnets/cidr/{subnet}/` | same |
| `GET /subnets/{id}/usage/` | compute utilization |
| `GET /subnets/{id}/first_free/` | first free IP |
| `GET /subnets/{id}/slaves/` | child subnets |
| `GET /subnets/{id}/slaves_recursive/` | recursive |
| `GET /subnets/{id}/addresses/` | all IPs in the subnet |
| `GET /subnets/{id}/first_subnet/{mask}/` | first free child subnet |
| `GET /subnets/{id}/all_subnets/{mask}/` | list possible splits |
| `GET /subnets/{id}/search/{ip}/` | search within the subnet |
| `POST /subnets/` | create |
| `POST /subnets/{id}/first_subnet/{mask}/` | auto carve next block |
| `POST /subnets/{id}/resize/` | resize |
| `POST /subnets/{id}/split/` | split |
| `PATCH /subnets/{id}/` | update |
| `PATCH /subnets/{id}/resize/` | as above |
| `PATCH /subnets/{id}/split/` | as above |
| `DELETE /subnets/{id}/` | delete |
| `DELETE /subnets/{id}/truncate/` | clear IPs |
| `DELETE /subnets/{id}/permissions/` | reset permissions |

**Maps to internal model**: `Subnet`.

---

## 4. Addresses (IP)

| phpIPAM | jt-ipam |
|---|---|
| `GET /addresses/{id}/` | same |
| `GET /addresses/{ip}/{subnetId}/` | by IP + subnet |
| `GET /addresses/search/{ip}/` | global search |
| `GET /addresses/search_hostname/{hostname}/` | by hostname |
| `GET /addresses/first_free/{subnetId}/` | first free |
| `GET /addresses/custom_fields/` | custom field definitions |
| `GET /addresses/tags/` | status tags |
| `GET /addresses/tags/{id}/addresses/` | all IPs with a given status |
| `POST /addresses/` | create |
| `POST /addresses/first_free/` | allocate first free |
| `PATCH /addresses/{id}/` | update |
| `DELETE /addresses/{id}/` | delete |
| `DELETE /addresses/{ip}/{subnetId}/` | delete by IP+subnet |

**Maps to internal model**: `IPAddress`. Note phpIPAM calls "status" a tag.

---

## 5. VLANs

| phpIPAM | jt-ipam |
|---|---|
| `GET /vlans/` | same |
| `GET /vlans/{id}/` | same |
| `GET /vlans/{id}/subnets/` | same |
| `GET /vlans/{id}/subnets/{section}/` | same |
| `GET /vlans/search/{number}/` | by number |
| `POST /vlans/` | same |
| `PATCH /vlans/{id}/` | same |
| `DELETE /vlans/{id}/` | same |

**Maps to internal model**: `VLAN` + `VLANDomain`.

---

## 6. VRFs

| phpIPAM | jt-ipam |
|---|---|
| `GET /vrf/` | same |
| `GET /vrf/{id}/` | same |
| `GET /vrf/{id}/subnets/` | same |
| `POST /vrf/` | same |
| `PATCH /vrf/{id}/` | same |
| `DELETE /vrf/{id}/` | same |

**Maps to internal model**: `VRF`.

---

## 7. Devices

| phpIPAM | jt-ipam |
|---|---|
| `GET /devices/` | same |
| `GET /devices/{id}/` | same |
| `GET /devices/{id}/subnets/` | same |
| `GET /devices/{id}/addresses/` | same |
| `POST /devices/` | same |
| `PATCH /devices/{id}/` | same |
| `DELETE /devices/{id}/` | same |

**Maps to internal model**: `Device`.

### Device Types

| phpIPAM | jt-ipam |
|---|---|
| `GET /tools/device_types/` | same |
| `POST /tools/device_types/` | same |
| `PATCH /tools/device_types/{id}/` | same |
| `DELETE /tools/device_types/{id}/` | same |

---

## 8. Tools (miscellaneous)

Covers: tags, nameservers, scan_agents, locations, racks, custom_fields, users, groups.

| phpIPAM endpoint | maps internally to |
|---|---|
| `/tools/tags/` | IPAddress.state enum |
| `/tools/locations/` | Location |
| `/tools/racks/` | Rack |
| `/tools/nameservers/` | DNSServer (Phase 2) |
| `/tools/scanagents/` | ScanAgent (Phase 1) |
| `/tools/custom_fields/{object}/` | CustomFieldDefinition |
| `/tools/users/` | User |
| `/tools/groups/` | Group |

---

## 9. Response format

phpIPAM standard format:

```json
{
  "code": 200,
  "success": true,
  "data": [...] or {...},
  "message": "...",
  "time": 0.012
}
```

jt-ipam wraps responses uniformly under `/api/phpipam/`; `/api/v1/` uses the standard OpenAPI format.

---

## 10. Incompatibilities / things to watch

| phpIPAM behavior | jt-ipam handling |
|---|---|
| numeric IDs (auto increment) | jt-ipam uses UUIDs internally; the phpIPAM-facing side additionally issues a numeric `legacy_id` (monotonic bigint) for compatibility |
| some fields use `0/1` for booleans | the wrapper converts automatically |
| mixes `null` and `""` | jt-ipam distinguishes strictly internally, normalizes to phpIPAM style on output |
| subnet cidr uses two columns `subnet/mask` | jt-ipam uses a single `cidr`; split out on output |
| `permissions` field is a base64-serialized string | jt-ipam parses and re-encodes |

---

## 11. Phase breakdown

| Phase | Coverage |
|---|---|
| **Phase 1** | user, sections, subnets, addresses, vlans, vrf, devices, tools (basic) |
| **Phase 2** | folder, l2domains, circuits, locations, prefixes, full scanagents |
| **Phase 3** | advanced: multi-type custom fields, batch endpoints |
