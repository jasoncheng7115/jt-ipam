# Changelog

All notable changes to this project are documented here. The format is loosely
based on [Keep a Changelog](https://keepachangelog.com/); versions track
`frontend/package.json` / `backend/app/version.py`.

## [0.5.83] — 2026-07-02

### Changed
- Notification settings: the **notification matrix** card now sits above all the per-channel settings (right under the intro), so the “which event → which channel” overview comes first instead of being sandwiched between Email and the other channels.


## [0.5.82] — 2026-07-02

### Added
- **Generic Webhook notification channel** — POSTs `{app, subject, text}` JSON to a custom URL (optional Bearer token via Authorization header); config form + Test button like the other channels. For n8n / custom endpoints / anything not covered by the built-in channels.


## [0.5.81] — 2026-07-02

### Fixed
- **Notification channels — send concurrently** — enabled webhook channels now fire in parallel (asyncio.gather) instead of sequentially, so worst-case latency is one channel's timeout, not the sum (avoids stalling IP-request/sync flows when several channels are slow).
- **Teams webhook — support the new Workflows webhooks** — falls back to an Adaptive Card payload when the legacy `{"text"}` (O365 connector) form is rejected, so both legacy connectors and current Workflows incoming webhooks work.


## [0.5.80] — 2026-07-02

### Added
- **LibreNMS integration: Verify TLS toggle** (migration 0094) — like Wazuh. Turn it off to connect when LibreNMS uses a self-signed cert or the hostname doesn't match (e.g. connecting by IP); the API client then uses `verify=False`. Fixes `transport: ConnectError` on self-signed LibreNMS without hacking the venv's certifi bundle (which upgrades would wipe). Default on.


## [0.5.79] — 2026-07-02

### Changed
- Notification wording: 站內通知 → 通知 (drop the 站內 prefix per Taiwan usage).


## [0.5.78] — 2026-07-02

### Changed
- **Notification wording: 鈴鐺 → 站內通知** (Taiwan usage) in the notification-settings copy and matrix column; the intro now lists all supported channels (Email + Telegram/Slack/Teams/Nextcloud/Zulip) instead of “in development”.
- docs: TEST_CHECKLIST spot-checks for the recent features; graylog DSV docstring uses RFC 5737 example IPs.


## [0.5.77] — 2026-07-01

### Added
- **Notification channels: Telegram, Slack, Microsoft Teams, Nextcloud Talk, Zulip** — all implemented (previously grayed “coming soon”). Each has a config form (encrypted tokens/webhooks) + a Test button on the notification-settings page; enabled channels receive every alert the matrix fires (IP requests, anomalies, certificate expiry/drift/deploy, stale-IP reminders) alongside Email/in-app. Admin-configured outbound (same trust model as SMTP).


## [0.5.76] — 2026-07-01

### Changed
- **In-app notifications now follow the UI language** — notifications store an i18n key + params (migration 0093); the bell and the Notifications page render them in the current language (falls back to the stored text for older notifications). Covers IP-request approve/reject/pending, anomaly alerts, certificate expiry/drift/deploy, and stale-IP reminders. Emails keep the default-language text.


## [0.5.75] — 2026-07-01

### Changed
- **Connections list OS column matches the IP detail page** — shared `OsCell`: OS icon + localized family name + （source） annotation, with the raw detected string on hover; the OS shown is the source-precedence-resolved value (same as IP detail), not just the raw scanner guess.


## [0.5.74] — 2026-07-01

### Changed
- **Disconnected overlay now covers only the display area** — it no longer dims the toolbar, so the Reconnect button stays fully visible and clickable.
- **Export button now has a border** — matched the neighbouring Columns / Refresh buttons (was borderless `quaternary`); applies to every table page via the shared `ExportButton`.


## [0.5.73] — 2026-07-01

### Fixed
- **BMC blank-screen hint text no longer hides behind the info icon** — the previous row-tightening also shrank the alert's left padding (which reserves space for the icon); now only the vertical padding is reduced.


## [0.5.72] — 2026-07-01

### Changed
- **Scan agent — much more accurate OS detection (agent 1.7.0)** — OS probe now adds `nmap -sV` service/banner detection + `smb-os-discovery` and derives the OS from **banners** (SSH `OpenSSH … Debian/Ubuntu`, `Service Info: OS:`, SMB) instead of trusting raw TCP/IP-stack fingerprinting, which confidently mis-guessed appliances/BMCs. The aggressive `-O` guess is now the last resort and is dropped when it's a device model (NAS/router/OpenWrt/…) rather than a general-purpose OS — better to show unknown than a wrong model. Verified: Proxmox Datacenter Manager `HP P2000 NAS`→`Debian`, Windows `XP SP3`→`Windows`, BMC `OpenWrt Kamikaze`→unknown.


## [0.5.71] — 2026-07-01

### Added
- **Remote console — clear "Disconnected" overlay** — when an SSH / RDP / VNC / noVNC / xterm / BMC session drops, a large centered overlay with a broken-link icon and "Disconnected" appears over the display so it's obvious at a glance; it fades out automatically on reconnect. Shared `ConsoleDisconnectedOverlay` across all console types.


## [0.5.70] — 2026-07-01

### Changed
- **Connection buttons are now single buttons** — dropped the split-button dropdown chevron (the "open in popout window" menu) on SSH/RDP/VNC/noVNC/BMC in both the Connections list and the IP detail card; the button just opens the console (new tab). Tighter connection-list row height. BMC blank-screen hint trimmed to one line (details behind the Setup-guide link).


## [0.5.69] — 2026-07-01

### Changed
- **Connection buttons — clearer RDP/VNC/noVNC icons** — the three shared a monitor glyph with a tiny 10px letter that was hard to tell apart; the letter is now large (13.5px) and bold, filling the screen, so R / V / N read at a glance. The split-button dropdown chevron is narrower (Connections list + IP detail).


## [0.5.68] — 2026-07-01

### Added
- **BMC console — "Fit to window" button** — serial consoles carry no window-size negotiation, so full-screen apps default to 80×24 with black margins. The button sends an `stty rows/cols` command (using xterm.js's real dimensions) into the session to match the browser window. Hovering shows an immediate tooltip that it **sends a command** and must be pressed at a shell prompt. No per-host script needed.
- **BMC setup guide — Troubleshooting section** — SPCR can point to the wrong ttyS (echo-test each port), baud must match SOL's bit rate, `TERM=xterm-256color` for clean curses rendering (glances), and the fit-to-window note. README (EN/zh) mirrors it.


## [0.5.67] — 2026-07-01

### Fixed
- **BMC "remember credentials" never saved** — the credential-vault create/list endpoint rejected `protocol='bmc'` (400, swallowed by the UI), so BMC passwords were never stored and every session re-prompted. `bmc` is now accepted in create/list/permission dispatch (password-only, `can_use_bmc`).

### Added
- **BMC console — built-in serial-console setup guide** — a **Setup guide** button (form + toolbar + blank-screen hint) opens a step-by-step modal: find the ttyS SOL maps to (ACPI SPCR / dmesg), add `console=tty0 console=ttySx,115200n8` (GRUB or PVE `/etc/kernel/cmdline`), enable `serial-getty`, optional BIOS Console Redirection, reboot. README (EN/zh) + docs landing page document the same.


## [0.5.66] — 2026-07-01

### Changed
- **BMC console blank-screen hint now explains the two-layer serial-console requirement** — BIOS Console Redirection (POST/BIOS/boot menu) **and** an OS serial console (kernel `console=ttySx,115200n8` + `serial-getty`; ttyS from ACPI SPCR; PVE uses `/etc/kernel/cmdline` + `proxmox-boot-tool refresh`). Without the OS layer, SOL goes blank once the kernel loads.
- test: `test_map_provider` accepts the `builtin` default map provider.


## [0.5.65] — 2026-07-01

### Fixed
- BMC console terminal: prominent drop-shadow to match the RDP/VNC console screen.
- **DNS (Univention UCS): username is now required on save.** An empty username produced a UCS `400 "basic auth malformed"` and the sync silently pulled 0 records.


## [0.5.64] — 2026-07-01

### Fixed
- **BMC console connect button now appears on the IP detail card** (next to SSH/RDP/VNC) — the editor modal wasn't rendering it / emitting the event.
- **BMC console screen restyled to match SSH/RDP/VNC** (card height, left-label form, `switch` for "remember", aligned title icon, status-pill toolbar, full-height terminal) + a "blank screen is normal — press Enter" hint for an idle SOL console.


## [0.5.63] — 2026-07-01

### Fixed
- **Connections page 500** — `list_connection_targets` had a leftover 4-tuple unpack after BMC added a 5th
  element; the page errored with no rows. Fixed.
- BMC console: added the connect button to the IP detail page (it was only on the Connections page).

### Changed
- Terminology: dropped "帶外" (not Taiwan usage) from the BMC console UI; comments use OOB.


## [0.5.62] — 2026-07-01

### Changed
- BMC console: generic username placeholder (`ADMIN / root`).


## [0.5.61] — 2026-07-01

### Added
- **BMC out-of-band console (Beta)** — a browser IPMI **SOL** console (keyboard + text screen) for BMC
  management IPs, integrated into the Connections page and the IP editor (per-IP toggle). Standard, vendor-agnostic
  transport (`ipmitool` SOL over RMCP+) with **cipher auto-fallback (17→3)**, connection self-check (SOL enabled /
  privilege), single-session handling, credential vault (`protocol=bmc`), **same RBAC as SSH**, and audit on
  open/close. Non-destructive: keyboard + screen only — no mouse, no power/sensor/boot control. Migration 0092
  (`bmc_enabled`). Install/upgrade auto-install `ipmitool` + `freeipmi-tools`; the nginx WebSocket location now
  covers `bmc`. (Graphic screenshot adapters are a future, isolated phase.)


## [0.5.60] — 2026-06-30

### Fixed
- **Subnets list: the CIDR column was squished.** `scroll-x` was set far below the columns' real total, so the
  table compressed the flexible CIDR/description columns below their `minWidth`. Fixed `scroll-x` to the real
  total and widened the CIDR minimum, so the CIDR (the key column) stays fully readable — the table scrolls
  horizontally when the window is narrow.


## [0.5.59] — 2026-06-30

### Changed
- Terminology: replaced the remaining "前綴" with "首碼" (Taiwan usage) — notably the OUI search placeholder.


## [0.5.58] — 2026-06-30

### Fixed
- **IP request list now actually shows the subnet CIDR.** 0.5.56 made the frontend use `subnet_cidr`, but the
  list endpoint never populated it (only the detail endpoint did), so the column still fell back to the UUID.
  The list response now fills `subnet_cidr`.


## [0.5.57] — 2026-06-30

### Added
- **IP heatmap legend now has hover tooltips** explaining each state (online / recently-seen / offline /
  reserved / unknown / idle), including the actual liveness thresholds. "Recently seen" = last detected between
  the online threshold (default 30 min) and 4× that (default 2 h) — likely a missed scan or flapping.


## [0.5.56] — 2026-06-30

### Fixed
- **IP request list: the "subnet" column now shows the subnet CIDR** instead of the raw subnet UUID (the read
  already returned `subnet_cidr`; the list just wasn't using it).

### Changed
- New-IP-request dialog: added an icon to the title and to both buttons (cancel / submit).


## [0.5.55] — 2026-06-30

### Fixed
- **IP request approval now writes the request's hostname and purpose onto the allocated IP.** The hostname is
  recorded as a **manual** hostname observation (top precedence, so a later scan/sync won't overwrite it) and the
  purpose is saved to the IP's **note**. (The description was already copied.) Applies to both direct and
  multi-stage approval (both fulfil through the same path).


## [0.5.54] — 2026-06-30

### Changed
- Change-password dialog: added an icon to the title and to both footer buttons (cancel / change), matching the
  other dialogs.


## [0.5.53] — 2026-06-30

### Changed
- **IP list: gateway / DHCP-server markers are now compact icons (with tooltips)** instead of wide text tags,
  so they no longer squeeze the IP into a one-character-per-line vertical strip. In-DHCP-range shows as a small dot.
- **IP list: widened the OS column** (110→150 px) so the OS family label is no longer truncated.


## [0.5.52] — 2026-06-30

### Changed
- **Scan-agent installer now installs base tools (`curl git sudo`) and, by default, `avahi-utils` for mDNS** —
  mDNS name resolution works out of the box (previously opt-in via `JT_IPAM_ENABLE_MDNS`). `avahi-utils` brings
  up `avahi-daemon` (UDP 5353); set `JT_IPAM_NO_MDNS=1` to skip it, `JT_IPAM_SKIP_PROBE_TOOLS=1` to skip all probe tools.
- **Docs: install instructions now install `curl` first** (a minimal system may not ship it, and the one-liner needs it).


## [0.5.51] — 2026-06-30

### Changed
- **LibreNMS "auto-add devices" now defaults ON** (and existing instances are flipped on by migration), so every
  sync / pull also match-or-creates the jt-ipam devices — no more clicking "Link devices" by hand each time.

### Added
- **DNS integration: a "Sync now" button** on the DNS servers list. DNS was only synced silently by the periodic
  timer (never showing in Tasks); the manual pull now enqueues a `dns.sync` task that appears in Tasks like the
  other integrations.


## [0.5.50] — 2026-06-30

### Changed
- **Subnet scan: enabling scan now requires an explicit choice** — "Local scan (jt-ipam host)" or a specific
  scan agent; saving with nothing selected is blocked with a warning. The old ambiguous "blank = scan from the
  host" became an explicit **Local scan** option, so a scan no longer silently does nothing in setups (e.g.
  Docker) where the host can't reach the LAN. Existing locally-scanned subnets show as "Local scan".


## [0.5.49] — 2026-06-30

### Added
- **Self-service password change for local accounts**: a "Change password" item in the top-right account menu
  opens a dialog that verifies the current password and sets a new one (≥ 12 chars). Hidden for externally
  authenticated accounts (LDAP / SSO). New endpoint `POST /api/v1/auth/change-password` (audited).


## [0.5.48] — 2026-06-30

### Fixed
- **pfSense sync no longer crashes** on (a) aliases whose `detail` is returned as a **list** (now coerced to
  text) and (b) NAT port-forward **targets that are alias names** rather than IPs (now skipped instead of being
  cast to INET). Both previously raised an asyncpg `DataError` and aborted the whole fetch.

### Changed
- **Scan-agent OS detection now uses `nmap --osscan-guess`**: hosts with no exact fingerprint match still get a
  best-guess OS (the top guess, shown with a confidence %), instead of nothing. Agent v1.6.0 (auto-updates).


## [0.5.47] — 2026-06-30

### Fixed
- **IP relationship chain: a device placed in a rack now inherits the rack's location (machine room)** even when
  the device row has no location of its own. Previously the chain stopped at the rack for such devices (e.g. a
  PVE node whose rack has a location but the device's own `location_id` was empty), so two hosts in the same
  rack could show inconsistently — one with the machine room, one without.


## [0.5.46] — 2026-06-29

### Added
- **IP list: special-role markers on each IP** — **Gateway** (the subnet's gateway), **DHCP server**
  (auto-detected when the IP matches an integrated OPNsense/pfSense firewall, plus a manual per-IP toggle in
  the IP editor), and **in DHCP range / lease**. Shown as small colour-coded tags with tooltips next to the IP.


## [0.5.45] — 2026-06-29

### Changed
- **Sections: the "strict mode" toggle (and column) are hidden from the UI.** It was a phpIPAM-compatibility
  field that jt-ipam never enforced, so the switch did nothing. The field is still stored and round-tripped via
  the phpIPAM-compatible API / migration (existing values are preserved), just no longer shown as a control.


## [0.5.44] — 2026-06-29

### Fixed
- **AI chat widget no longer shows until LLM/AI is enabled** (管理 → LLM/AI). On a fresh install you could
  type and click Send before configuring an LLM; `/me` now exposes `ai_enabled` and the widget is gated on it.
- **LLM/AI settings: the model list is no longer fetched while "啟用 Ollama 伺服器連接" is off**, so it no
  longer shows a spurious "無法連 Ollama：Internal Server Error". Toggling off clears the list and the error.
- **LLM/AI: a half-width space before "(未在 Ollama 找到)"** on model names.


## [0.5.43] — 2026-06-29

### Added
- **Docker Compose air-gapped (offline) workflow**: `offline-export.sh` builds + saves all four images
  (app + postgres/redis) into one archive on an internet-connected host; `offline-import.sh` loads them and
  starts the stack on a host with no internet (`--no-build --pull never`). Same flow for install and upgrade.
  Documented in `deploy/docker/README*`.

### Changed
- Terminology: anomaly detection "MAC 漂移" → "MAC 變動" (proper Taiwan usage).


## [0.5.42] — 2026-06-29

### Fixed
- **IP list "switch port" column widened** so it shows the full `switch@port` (e.g. `switch-003@eth1/0/24`)
  instead of truncating to `switch-003@eth1/…`.


## [0.5.41] — 2026-06-29

### Fixed
- **Locations map (built-in) now zooms in to fit all markers** instead of always showing a wide ~24°×16°
  view, so nearby sites no longer collapse into what looks like a single point. A small minimum view is kept
  only to avoid over-zooming a single/very-close point (the built-in low-res basemap would blur).


## [0.5.40] — 2026-06-29

### Changed
- **pfSense integration table now shows the same columns as OPNsense** (name / API URL / TLS / last sync /
  last error / actions); removed the extra 啟用 / 同步項目 / 別名數 / 規則 columns.

### Added
- **The left sidebar auto-expands the group that contains the current page** (管理 / 進階 / a subnet group),
  whether you navigate there or land on it directly, so your location is visible.


## [0.5.39] — 2026-06-29

### Fixed
- **OPNsense firewall column picker no longer lists phantom columns.** It used to offer 狀態/DHCP/ARP/OpenVPN/
  Rules/NAT entries that the table doesn't actually render (all shown checked but never appearing). The picker
  now matches the real columns: name, API URL, TLS, last sync, last error, actions.


## [0.5.38] — 2026-06-29

### Changed
- **pfSense integration page now matches the OPNsense page**: adds a TLS column, a "TLS verification disabled"
  warning banner when any instance has Verify TLS off, the same in-form TLS warning, and the same action-button
  order (edit / test / sync / delete).
- **PVE LXC (xterm) console hint moved into the toolbar** (single line next to the status tags, ellipsis if too
  long, dismissible) instead of a full-width banner, with shorter wording.


## [0.5.37] — 2026-06-29

### Added
- **Change-log entries older than a configurable number of days are shown dimmed**, so recent changes stand
  out. Threshold set in 管理 → 系統設定 → 顯示 (default 30 days; 0 = never dim). Applies to the IP-detail
  change-log timeline and the IP 異動記錄 page.


## [0.5.36] — 2026-06-29

### Added
- **PVE LXC (xterm) console: a dismissible hint banner** reminds you to click the screen and press Enter once
  if only a cursor shows and no prompt appears (a known PVE LXC console quirk).


## [0.5.35] — 2026-06-28

### Fixed
- **RDP: modifier shortcuts (Ctrl+V / Ctrl+C / Ctrl+A …) now work — which makes the clipboard paste actually
  paste.** Letter/number keys were sent as Unicode characters, and RDP does not combine a Unicode key event
  with the scancode Ctrl/Alt modifier, so Ctrl+V did nothing (it just typed "v"). When a modifier is held the
  key is now sent as a scancode. Verified end-to-end against a real Windows host (server issues
  `CB_FORMAT_DATA_REQUEST` on Ctrl+V and we answer with the clipboard text).
- The RDP Paste button now reports the number of characters actually sent to the remote clipboard.


## [0.5.34] — 2026-06-28

### Fixed
- **RDP clipboard paste: fixed RDP dropping ~10–20s after connecting when the feature was enabled.** When the
  remote requested our clipboard before any text had been set, aardwolf's cliprdr channel crashed
  (`'NoneType' object has no attribute 'datatype'`) and tore down the session. We now seed an empty clipboard
  on connect so `clipboard.data` is never null.

### Changed
- **All consoles (SSH / RDP / VNC / noVNC / xterm): the display area is greyed out** (grayscale + dimmed,
  non-interactive) once the session disconnects, so it is obvious the connection is closed.


## [0.5.33] — 2026-06-28

### Fixed
- **Users admin table: the Actions column is now pinned to the right** so it stays visible when the table
  scrolls horizontally on narrow screens (previously it scrolled off-screen).


## [0.5.32] — 2026-06-28

### Added
- **RDP console: optional one-way clipboard paste (controller → controlled host).** A new "貼上" button in the
  RDP toolbar pushes your local clipboard text into the remote's clipboard (text only; then press Ctrl+V on the
  remote). The remote clipboard is **never** sent back to the browser/server. Gated by a new admin security
  toggle **管理 → 系統設定 → 資安 → 允許 RDP 控制端貼上文字到被控端**, **off by default (deny by default)**.
  Backend only attaches the RDP clipboard (cliprdr) channel when the toggle is on; pastes are length-capped.
  Verified end-to-end against a real Windows RDP host.


## [0.5.31] — 2026-06-28

### Fixed
- **Connections page: the PVE console buttons now match the IP detail page** — the label is just noVNC / xterm
  with a small "PVE" badge in the top-right corner (instead of an inline "·PVE").


## [0.5.30] — 2026-06-28

### Fixed
- **PVE console (noVNC/xterm) disconnect now behaves like RDP** — clicking 中斷連線 (or a dropped connection)
  leaves the last frame frozen in a "已關閉" state with a 重新連線 button, instead of jumping back to the
  connection form.


## [0.5.29] — 2026-06-27

### Fixed
- **noVNC / xterm console screen now has the same framed look as the RDP console** — border, rounded corners
  and drop shadow (previously it was flush with no frame).


## [0.5.28] — 2026-06-27

### Fixed
- **PVE console connect form now matches the SSH form.** It auto-selects the most recent saved PVE credential
  (compact form, ready to connect), the hint switches to the saved-credential wording when one is selected,
  and the card title / connect button icon reflects the protocol (xterm → terminal, noVNC → screen).


## [0.5.27] — 2026-06-27

### Fixed
- **PVE xterm (CT) console now has padding around the terminal** (like the SSH console) instead of sitting
  flush against the edges.


## [0.5.26] — 2026-06-27

### Fixed
- **Version page now lists the noVNC dependencies** that were missing: backend `websockets` (the PVE
  console relay) and frontend `@novnc/novnc`.
- **Connections page: the PVE console button now matches the IP detail page** — it shows xterm (CT) / noVNC
  (VM), is highlighted (orange / PVE), and its tooltip reads "xterm 連線" / "noVNC 連線" instead of a generic
  "連線".
- **Global search: a matching Proxmox VMID now surfaces the VM/CT itself** — by name, under a "Virtualization"
  group. Previously the result used a type the dropdown didn't recognise, so it was dropped entirely (only
  unrelated IP matches showed).


## [0.5.25] — 2026-06-27

### Fixed
- **noVNC button now uses a distinct icon** (a screen with "N") instead of reusing the RDP icon, so noVNC and
  RDP are no longer visually identical.
- **PVE console connect form is now centred on the page in the error state too** (previously only the initial
  form was centred; an error left the card stuck top-left).
- **Console connection buttons (SSH / RDP / VNC / noVNC) now use the in-app tooltip** instead of the
  browser-native `title` popup, on both the Connections page and the IP detail header.
- **Audit log** now resolves PVE-credential targets to their label instead of showing a raw UUID.
- **Fixed a 500 when connecting with a *saved* PVE credential** — the stored password was decoded twice
  (`str` has no `.decode()`); now decrypts once like the RDP/VNC paths.


## [0.5.24] — 2026-06-27

### Fixed
- **Device detail page: Edit now opens the dialog in-place** (it used to jump to the device list). The device
  edit dialog is now a shared `DeviceEditModal` component.
- **Virtualization VM table filter:** a numeric query (e.g. `102`) no longer matches internal fields such as
  `memory_mb` (1024) — the quick filter now only matches the **displayed columns** (name / VMID / node / IP /
  MAC / status), and matches inside IP/MAC lists.


## [0.5.23] — 2026-06-27

### Fixed / Changed
- **PVE console (noVNC/xterm) UI now matches SSH/RDP/VNC.** Same card connect form (帳號 → 密碼 → realm order,
  short "記住此帳密"), and the connected toolbar gains **send-keys + scale (fit / native) + "中斷連線"** for
  graphical VM consoles. The connect button uses the right icon/tooltip (noVNC vs xterm), and the
  connection-type filter no longer truncates "noVNC/xterm".
- The PVE console toggle now appears on **all of a VM's IPs** — a multi-IP VM resolves via its interface MAC,
  not only its primary IP.
- **Global search:** a numeric query (e.g. `227`) is now also treated as a possible Proxmox **VMID** and finds
  the matching VM/CT; the right-side hint shows "VLAN / VMID" instead of only "vlan_number".
- **Rack:** the device dialog's "U 位 (起始)" field is wider (the number shows), and the U-position picker now
  reflects **half-U** occupancy (left/right) — you can place into the free half.


## [0.5.22] — 2026-06-27

### Added
- **In-browser PVE console (noVNC / xterm) for Proxmox VE VMs/CTs.** For an IP that maps to a Proxmox VM/CT,
  a per-IP toggle adds an in-browser console button (with a **PVE** badge): QEMU VMs open a graphical **noVNC**
  console, LXC containers open an **xterm** terminal. The connection uses the **PVE credentials you enter at
  connect time** (optionally saved to the encrypted vault, like SSH/RDP/VNC) and is gated by PVE's own
  permissions — without `VM.Console` you can't connect. The browser talks only to jt-ipam's **same-origin**
  WebSocket, which byte-relays to PVE's `vncwebsocket` (vncproxy for VMs, termproxy for CTs); credentials are
  never stored on the server beyond the optional vault, the WebSocket relay is single-use-ticketed, and every
  session is audited (`novnc.session_open` / `novnc.session_close`).
- The Proxmox sync now back-links each VM/CT's primary IP (`VirtualMachine.primary_ip_id`) so an IP can resolve
  to its PVE console target (also backfills existing VMs).


## [0.5.21] — 2026-06-27

### Fixed
- Traditional-Chinese wording: use 內建 / 本機 phrasing instead of the mainland terms 自帶 / 同源 in the map-provider UI text and comments.


## [0.5.20] — 2026-06-27

### Added / Changed
- **Map provider now defaults to "Built-in (offline)"** — the self-contained world map (no external calls).
  Admins can still switch the Locations preview to **OpenStreetMap** or **Google Maps** under
  Settings → System.
- **OpenStreetMap tiles load through a same-origin backend proxy** (`/api/v1/system/map-tile/{z}/{x}/{y}`):
  the browser never contacts OSM directly, so the CSP stays `img-src 'self'` + COEP `require-corp` (ZAP clean)
  even when an admin selects OSM. The proxy is bounded read-only (server-built OSM-only URL, validated tile
  coordinates, small in-memory LRU cache, nginx-rate-limited).
- Google Maps: the in-page preview uses the built-in map (Google tiles cannot be proxied per their Terms);
  the "open externally" link opens Google Maps.


## [0.5.19] — 2026-06-27

### Security
- Hardening + documentation around the one remaining accepted finding (CSP `style-src 'unsafe-inline'`,
  inherent to Vue + Naive UI — `v-show` / `:style` / floating-element positioning emit inline style
  *attributes*, which CSP cannot nonce/hash). Enabled Naive UI's **`inline-theme-disabled`** to move theme
  styling out of inline attributes into `<style>` blocks (smaller inline surface + SSR/perf), and documented it
  as an **accepted risk with compensating controls** in `SECURITY.md` (EN/zh): strict `script-src 'self'` (no JS
  exec) + `img-src`/`connect-src 'self'` (no exfiltration) + Vue auto-escaping. No real exploitability remains.


## [0.5.18] — 2026-06-27

### Security / Changed
- **The Locations map is now fully self-contained — no embedded OpenStreetMap.** The OSM tile renderer is
  replaced by a bundled Natural Earth world outline (public domain) projected locally. The map now works on
  isolated/offline networks, sends **no requests to OSM** (it no longer leaks which sites an admin is viewing),
  and lets the headers tighten: the OSM exception is dropped from CSP `img-src`, and
  `Cross-Origin-Embedder-Policy` is upgraded to **`require-corp`** (the strongest value — now that there are
  zero cross-origin subresources). nginx proxy snippets `proxy_hide_header` COEP too (single source).
- **Column-picker labels across all admin tables re-translate on a live language switch** — 19 pickers wrapped
  in `computed` (they were frozen at the language active when the page first loaded).
- pfSense NAT sync was **verified against a live port-forward** and refined (external `destination_port` for the
  NAT port; `target` linked to the internal IP).

### Added
- `deploy/zap-baseline.conf` — a documented ZAP baseline-triage of accepted, justified low/informational
  exceptions (Naive-UI `style-src 'unsafe-inline'`, IPAM example IPs, asset caching, SPA detection). The release
  gate is now: a ZAP scan with **no findings beyond this baseline** (0 FAIL / 0 WARN).


## [0.5.17] — 2026-06-27

### Changed
- **More pfSense/OPNsense parity.** The "pfSense firewall" admin page no longer has a view-rules button —
  rule/alias viewing lives in **Advanced → Firewall (pfSense)** (read-only), matching OPNsense. Menu entries
  renamed: **Firewall (OPN) → Firewall (OPNsense)**, **Firewall (pf) → Firewall (pfSense)**, with the in-page
  titles made consistent; the pfSense rules tab is now labelled **"Firewall rules"**.
- The NAT-rules **Source** filter now offers **pfSense**, and pfSense NAT port-forwards are synced into the NAT
  table (`source_origin = pfsense:<id>`) so they list alongside OPNsense NAT.

### Fixed
- Column-picker labels now re-translate immediately on a live language switch (no page refresh needed) on the
  pfSense pages and the NAT source filter — they were frozen at the language active when the page first loaded.


## [0.5.16] — 2026-06-27

### Changed
- **pfSense UI aligned with the OPNsense pages.** The "pfSense firewall" admin table now has a column picker
  + export and a fitting default column set (the actions column is no longer cut off on narrow widths); the
  add/edit dialog spacing is fixed (sync toggles / Expose-DSV grouped into form rows); and the page title is
  now **"pfSense firewall"** (was "Integrate pfSense").
- The Advanced → "Firewall rules / aliases" entry (OPNsense) was renamed to **"Firewall (OPN)"**.

### Added
- **Advanced → "Firewall (pf)"** — a read-only pfSense rules & aliases viewer (instance selector + tabs +
  quick filter + column picker + export), mirroring the OPNsense "Firewall (OPN)" page.
- `pfsense` is registered in the **hostname/ARP source precedence**, defaulting just below `opnsense`.


## [0.5.15] — 2026-06-27

### Security / Docs
- **The security headers are now documented as a required deployment setting and surfaced in install/upgrade
  output.** When jt-ipam is fronted by your *own* edge reverse proxy / load balancer (Mode C), that proxy
  **must** set the security headers itself — they don't survive an extra proxy hop, so otherwise the public
  site ships with no CSP/HSTS. The external-proxy snippet (`jt-ipam-external-proxy-snippet.conf`) now also
  `proxy_hide_header`s the upstream's security headers (dedup, matching the internal snippet in v0.5.14);
  INSTALL (EN/zh), README (EN/zh) and the landing page now call this out as **required** with a
  verify-through-the-public-URL step; and `jt-ipam.sh install`/`upgrade` print a required-headers notice.


## [0.5.14] — 2026-06-27

### Security
- **Fixed duplicate security headers + a stale CSP on `/api/*` responses** (found by an authenticated ZAP
  scan). The backend middleware still emitted the pre-v0.5.8 permissive CSP (`frame-src` allowing
  google/openstreetmap), and behind nginx every proxied `/api` response carried **two** copies of each
  security header (HSTS / CSP / X-Frame-Options / Referrer-Policy / Permissions-Policy / COOP / CORP) — ZAP
  flagged "Strict-Transport-Security multiple header entries". Backend CSP tightened to `frame-src 'self'`
  (so the `direct`/`self-signed` TLS mode is also correct), and the nginx proxy snippet now
  `proxy_hide_header`s the upstream's security headers so the server block's hardened values are the single
  canonical source. Verified live: one of each header, tightened CSP.


## [0.5.13] — 2026-06-27

### Fixed
- **Full test suite & lint green.** Ran the complete pytest suite (412 tests) + migrations 0001→0088 on a
  fresh DB and fixed 4 test assertions that had drifted behind earlier feature work — the new
  `list_connection_targets` MCP tool (missing from the tool-args guard), the Proxmox guest-agent `timeout`
  arg (test mock signature), and the external-MCP toggle now returning **403** when disabled (was asserted
  as 401). Also removed two dead-code lint errors and sorted imports. No product behaviour change.


## [0.5.12] — 2026-06-27

### Added
- **pfSense integration Phase 2** — firewall **rules sync** + a read-only **Rules / NAT viewer** (eye action
  on the pfSense page), and **Graylog DSV** endpoints for pfSense: `…/lookup/pfsense/{id}/aliases`
  (alias → members) and `…/lookup/pfsense/{id}/rules` (filterlog `tracker` → rule description), token-gated
  and per-instance `expose_dsv`. New per-instance toggles: sync rules, expose DSV. Verified against pfSense
  CE 2.8.1. (migration 0088)
- TEST_CHECKLIST: added a pfSense integration section + spot-checks for recent features.


## [0.5.11] — 2026-06-27

### Added
- **pfSense integration (Phase 1)** — a separate integration with its own settings page (Admin →
  pfSense), independent of OPNsense. pfSense CE has no built-in REST API, so this connects via the
  third-party **pfSense-pkg-RESTAPI** package (pfrest.org): base path `/api/v2`, `X-API-Key` auth. It pulls
  the **ARP table** and **DHCP leases** to stamp IP liveness / MAC / hostname within scoped subnets
  (overlap-safe), and reads **firewall aliases**. Per-instance sync toggles (DHCP off by default to avoid
  clashing with another DHCP server), subnet scoping, verify-TLS, test-connection and sync-now; runs in the
  periodic sync loop. `pfsense` is registered as a hostname/ARP source. Verified end-to-end against pfSense
  CE 2.8.1. (migration 0087; firewall rules / NAT / Graylog-DSV are planned for Phase 2.)


## [0.5.10] — 2026-06-27

### Fixed
- **"Add address" inside a subnet's IP list had no IP input field**, so submitting failed with HTTP 422
  (missing IP) (issue #14). The create form now shows a required **IP** field (prefilled from context when
  one is provided), and submitting with an empty IP is blocked client-side with a clear message.


## [0.5.9] — 2026-06-27

### Added
- **Notification matrix** (Admin → Notification settings): a per-event × per-channel grid (in-app bell /
  email) to choose which events send notifications. Events: IP request submitted / approved / rejected,
  certificate expiring or expired, **agent deployed a new certificate** (new), certificate drift, anomaly
  detected. Every notification site now respects the matrix; certificate and anomaly events can now also be
  emailed (previously in-app only).
- **New event `cert.deployed`**: when a distribution agent successfully swaps a cert for a new version, admins
  are notified (the agent report endpoint diffs the previous vs new fingerprint per cert/service).
- **Certificate distribution: a `files` service profile** that only writes the cert files (fullchain + key to
  `/etc/ssl/jt-ipam`) and does **not** test, reload or restart any service — for operators who reload
  themselves.


## [0.5.8] — 2026-06-26

### Security
- **Removed the embedded third-party map iframe** on the Locations page (Google Maps / OpenStreetMap); the
  map now opens in a new tab. The embed pulled a third-party page (and its scripts) into ours — a privacy
  leak and the source of the ZAP findings **Cross-Domain JavaScript Source File Inclusion** and **Sub
  Resource Integrity Attribute Missing** (they came from Google's/OSM's embed page, not jt-ipam). Google/OSM
  are now contacted only when the user clicks.
- **Tightened CSP `frame-src` to `'self'`** (dropped the google/openstreetmap allowances now that nothing is framed).
- **nginx reference config hardened**: hide the upstream (uvicorn) `Server` / `X-Powered-By` headers (no
  framework fingerprint), and add `Cross-Origin-Resource-Policy: same-origin`.

### Docs
- INSTALL (EN/zh) and the landing page now document the **hardened nginx reverse proxy as the production
  standard** (TLS 1.2/1.3, HSTS preload, strict CSP, full security-header set, hidden upstream banner,
  backend bound to loopback).


## [0.5.7] — 2026-06-26

### Added
- **MCP client-config generator.** On Admin → LLM/AI, the "expose MCP" card has a "Generate client config"
  button that produces ready-to-paste MCP server snippets for Claude Desktop (via `mcp-remote`), opencode,
  mcpo, and generic clients (Cursor / Cline / VS Code) — with the endpoint URL and API key filled in, each
  with its own copy button.


## [0.5.6] — 2026-06-26

### Changed
- **Anomaly detection page reorganized into tabs.** The four detectors (IP conflicts / MAC drift / ghost
  IPs / unauthorized IPs) are now tabs instead of one long stacked page.
- **Each anomaly table now has a column picker**, and the internal `ip_address_id` UUID column is hidden by
  default (still selectable).

### Added
- **MAC drift now also shows the matching IP / hostname** for each drifting MAC (resolved from IPAM, with
  ARP fallback) — so you can tell which host a roaming MAC belongs to.


## [0.5.5] — 2026-06-26

### Added
- **Scan agents: a "Dependencies" column.** Each agent now reports its probe-tool inventory; the column
  shows how many are installed (e.g. `4/7`) and clicking opens a detail dialog listing every tool — whether
  it is installed and at which version, which probes it enables (nmap → OS/ports, nmblookup → NetBIOS,
  avahi-resolve → mDNS …), and the install command for the missing ones. Helps diagnose "no machine name"
  (NetBIOS needs `nmblookup`) at a glance. Agent self-updates to v1.5.0 to report this (migration 0086).


## [0.5.4] — 2026-06-24

### Fixed
- **Background tasks could stay "in progress" forever after a restart (issue #9).** Tasks run via
  `asyncio.create_task` inside the worker process, so a backend restart (deploy / upgrade / crash) orphaned
  any in-flight task with no terminal status, leaving it stuck "running" in Operations. On startup, lingering
  pending/running tasks are now reconciled to `failed` ("interrupted: backend restarted").
- **LibreNMS sync aborted midway with a duplicate device-port error (issue #12).** Port sync now upserts
  (`ON CONFLICT (device_id, name)`) instead of a plain insert, so an existing port (e.g. two LibreNMS
  devices mapped to one jt-ipam device, or a re-processed interface) no longer breaks the whole sync with
  `UniqueViolationError` on `device_port_unique_name`.


## [0.5.3] — 2026-06-24

### Fixed
- **Contact groups could not be created / edited / deleted — "Method Not Allowed" (issue #11).** The
  backend only had `GET /contact-groups`; added `POST` / `PATCH` / `DELETE`.
- Added the missing `DELETE` endpoints for **providers, circuits, wireless SSIDs and wireless links** —
  their delete buttons previously returned 405 (same class of bug).


## [0.5.2] — 2026-06-24

### Fixed
- **Proxmox VM list capped at 500 (issue #9).** The list now fetches every page, so all VMs show
  (e.g. 592, not 500). The same paginate-all fix covers other advanced-resource lists.
- **Proxmox sync slow / stuck "in progress" (issue #9).** The best-effort per-VM guest-agent IP query
  now uses a short 6 s timeout, so unresponsive guest agents on running VMs no longer stall the whole
  sync (previously each could hold the shared 20 s timeout).
- **Wazuh agent list showed only 200 (issue #10).** All agents were stored; the admin page now fetches
  every page instead of just the first 200.
- **Other integrations audited for the same cap.** LibreNMS `/devices` and AdGuard already return
  everything; OPNsense alias / rule / IPsec searches no longer cap at 1000 / 500 (`rowCount = -1` = all).

### Changed
- Table footers now show the total row count on the left (e.g. "Total: 592").
- The floating AI-chat button is semi-transparent at rest and turns solid on hover.


## [0.5.1] — 2026-06-24

### Added
- **RDP / VNC "send keys".** Send special key combos the browser/OS would otherwise intercept (Esc, Tab,
  F1–F12, Ctrl + Alt + Del, ⊞ Win, Alt + Tab; VNC adds macOS ⌘ combos) from a keycap-styled menu with
  per-platform icons.
- **RDP "refit".** One click reconnects at the current window size for a crisp native picture (aardwolf
  cannot hot-resize a live session, so it rebuilds the session to match).
- **Richer version page.** Adds asyncssh / aardwolf / Pillow package versions, a host-environment section
  (OS / kernel / nginx / Node.js / PostgreSQL) and frontend-framework versions (Vue / Naive UI / Vite…),
  with a reorganized layout.
- **Expose MCP to external systems (read-only).** New toggle under Admin → LLM / AI; only when on does
  jt-ipam accept external HTTP MCP calls (`/api/mcp`, Streamable HTTP / JSON-RPC). Generate/regenerate a
  **read-only** API key (stored encrypted); the page shows the endpoint URL and auth header (name → value).
  The read-only key always blocks the 6 data-changing tools (and hides them from the tool list). Off by
  default (deny-by-default); existing per-user API-token auth still works and is also gated by the toggle.
- New MCP tool `list_connection_targets` (read-only): lists IPs/devices with a browser remote console
  enabled (SSH / RDP / VNC) that the caller may reach — never returns credentials.

### Changed
- Console toolbar: a protocol label (SSH / RDP / VNC) sits next to the hostname; buttons are more compact
  and clearly clickable, with a red-outline disconnect. In Advanced → Connections and on IP detail, the
  console action buttons collapse to icon-only only when too narrow (threshold scales with the protocols
  per row).
- The relationship graph now shows the PVE node a VM runs on (and that node's rack/room) when a host is a
  Proxmox VM guest — on both the IP and device detail pages.

### Fixed
- **Proxmox VMs with the same name in one cluster could not be imported (issue #8).** The VM uniqueness
  key changed from `(cluster, name)` to `(cluster, VMID)` (migration 0085) — Proxmox allows same-named VMs
  with different VMIDs, which previously collided with `vm_cluster_name_uq`.
- **AI chat: recover tool calls emitted as text.** A (tool-capable) model occasionally returns a tool call
  as inline text instead of structured `tool_calls`; these are now parsed and executed instead of leaking
  into the answer, with a neutral retry notice when unrecoverable.
- The external MCP sub-app no longer serves FastAPI's auto-generated `/openapi.json` and `/docs` (MCP is
  discovered via JSON-RPC `tools/list`, not OpenAPI; that schema was meaningless to MCP clients and
  unauthenticated).
- Audit detail shows `switch_port` as `device@port` (consistent with other pages) and resolves credential
  targets to a label instead of a raw UUID.


## [0.5.0] — 2026-06-22

### Added
- **In-browser RDP connection management (Beta).** Open a Windows RDP desktop straight from an IP's
  detail page — verified against NLA-enforced Windows 11.
  - Per-IP `rdp_enabled` toggle (migration 0083); permission `can_use_rdp` (deny-by-default, reuses the
    `can_ssh` capability); detail-page split button + an "RDP" filter/action in Advanced → Connections.
  - Backend `endpoints/rdp_console.py`: single-use ticket → WebSocket bridge to the remote desktop
    (NLA / CredSSP+NTLM); framebuffer streamed as PNG tiles to a `<canvas>`, keyboard/mouse/wheel sent
    back; target host locked to the catalogued IP (anti-SSRF); session open/close audited (never the
    password); a concurrency cap (`rdp_max_sessions`).
  - Native `<canvas>` rendering — **no new frontend dependency**. Resolution picker incl. "auto-fit".
- **In-browser VNC connection management (Beta).** Same pattern for VNC (RFB) targets — verified against
  a real VNC server.
  - Per-IP `vnc_enabled` toggle (migration 0084); permission `can_use_vnc`; detail-page split button +
    "VNC" in Advanced → Connections.
  - Desktop size is server-decided; the screen has a **Fit / 1:1 scale toggle** (with correct
    mouse-coordinate mapping when scaled).
  - **VNC auth support: RFB security types None and VNC Authentication (password) only.** Account-based
    schemes (UltraVNC MS-Logon, VeNCrypt, RealVNC RA2/RA2ne) are not supported; the connect screen
    states this.
- **Optional dependency, zero impact on the base install.** RDP/VNC use `aardwolf` (pinned to a version
  with prebuilt manylinux wheels → no Rust toolchain needed). Install/upgrade attempt it **best-effort**
  (`pip install --only-binary=:all: -e ".[rdp]"`); if no wheel exists it fails fast and the feature is
  simply disabled. The backend detects availability and the UI hides the entry points when absent.
- The shared **per-user encrypted credential vault** now stores SSH / RDP / VNC credentials
  (`protocol` + optional `domain`); credential audit records carry the protocol (e.g. `rdp_credential`).

### Changed
- Advanced → Connections lists SSH/RDP/VNC targets together; the OS column resolves through the same
  source-precedence as the detail page.
- nginx WebSocket-upgrade location widened to cover the SSH/RDP/VNC console paths; the upgrade path
  patches existing sites in place.

### Fixed
- Audit detail shows `switch_port` as `device@port` (consistent with other pages) and resolves credential
  targets to a label instead of a raw UUID.

## [0.4.210] — 2026-06-21

### Added
- **"Remember" SSH credentials (per-user, individually owned).** Each user can store their own
  password / private key and reuse it next time without retyping:
  - Backend `ssh_credentials` (migration 0082): password / private key / passphrase are each
    **envelope-encrypted** (per-field random DEK wrapped by the master KEK = ENCRYPTION_KEY, AAD bound to
    owner+field); plaintext never hits the DB, logs, or the frontend.
  - `GET/POST/DELETE /api/v1/ssh-credentials`: owner-only, masked reads (never plaintext).
  - Connecting now uses a **reference (credential_id)**: the frontend sends only the id; the backend
    decrypts in-memory at connect time and discards it. `can_use_ssh(target)` is still enforced; scope
    supports both target-bound and personal-default (any IP the user may reach).
  - Audit logs the `credential_id` (never plaintext) and flows to the existing SIEM forwarder; disabling a
    user makes their credentials unusable immediately.
  - Connect form gains a "Saved credential" dropdown (pick to connect) and a "Remember" toggle.

### Out of scope (roadmap)
- PTY session recording, MFA re-auth for sensitive targets, external Vault/KMS-backed KEK, SSH CA short-lived certs.

## [0.4.209] — 2026-06-21

### Added
- **Advanced → Connections page**: a table of all SSH-enabled targets you're allowed to connect to (backend `GET /addresses/ssh/targets`, same deny-by-default filtering as `can_use_ssh`), with sort / live filter / column picker / export, and per-row "SSH" (new tab) or dropdown "open in new window".

### Changed
- The IP detail "SSH" button now **opens a new tab** (main click) and **a new window** (dropdown); the in-page embedded terminal was removed.
- SSH connect form reordered: auth method first, password directly under username.
- Connection status is now a colored-dot pill badge (connected pulses green); disconnect / reconnect / open-in-new-window all have icons.

### Fixed
- After enabling "SSH management" and saving, the SSH button required a refresh to appear — the PATCH `/addresses/{id}` response didn't compute `ssh_available`; now it does (matching GET).

## [0.4.208] — 2026-06-21

### Added
- **SSH connection management for IP addresses (embedded / pop-out terminal).** A new "Enable SSH management"
  toggle in the IP edit dialog; once enabled, authorized users see an "SSH" split button at the top-right of the
  detail page (left of Edit): the main button opens an xterm.js terminal inline, and the dropdown arrow offers
  "Open in new window" for a standalone full-page terminal.
- **Connection security:** the client first exchanges its JWT for a single-use 60-second ticket, then opens a
  WebSocket with `?ticket=` (bridged to SSH via asyncssh on the backend). Credentials (password / private key)
  are **sent only at connect time, never stored, never logged**; the target host is fixed to the IP record's
  address (so it can't be abused as a generic SSH proxy); host keys use trust-on-first-use pinning (mismatch warns);
  session open/close are audited.
- **Permission:** a new standalone "SSH access" capability (`users.can_ssh`). Usage is allowed for admins, users
  with write on the IP, or users with the SSH-access capability who can at least view the IP (deny-by-default).
  Toggle per user in the Users admin page.

### Changed
- nginx site config (incl. the external reverse-proxy template) now sets WebSocket upgrade headers and a long
  read timeout for the SSH terminal (`deploy/nginx/*.conf`). ⚠️ Apply this to the production nginx as well.
- New frontend deps `@xterm/xterm` / `@xterm/addon-fit` (pure frontend, bundled at build time; picked up
  automatically by the install/upgrade pnpm install).

## [0.4.207] — 2026-06-19

### Changed
- **Docker Compose now auto-generates the admin password.** `gen-env.sh` also generates a random `admin`
  password (printed in its output, stored as `JT_IPAM_ADMIN_PASSWORD` in `.env`, mode 0600); the backend
  creates the admin on first boot using it, so you can log in straight away — matching the systemd installer's
  "auto-create admin" experience.
- **The site's Deployment section is now split into two zones:** "Primary: systemd + apt" and "Optional:
  Docker Compose", each boxed/badged with its own install / first-password / upgrade commands. The Docker
  zone spells out that upgrading is `./update.sh` (**not** `jt-ipam.sh upgrade`).
- docs/INSTALL §2.7 and the deploy/docker README (EN + zh) "first admin" notes updated to match.

## [0.4.206] — 2026-06-19

### Changed
- **Graylog DSV settings: "Format" and "Token" are now two side-by-side cards** (each bordered / tinted /
  rounded) for a clear, tidy separation, wrapping on narrow screens — replacing the stacked layout.

## [0.4.205] — 2026-06-19

### Fixed
- **Two Docker Compose startup issues** (caught by actually running `docker compose` end-to-end):
  1. **`.env.example` had `BACKEND_BIND_HOST=0.0.0.0`, which the security check rejects** in nginx mode (it
     requires a loopback bind) → changed to `127.0.0.1`; the container's uvicorn still binds `0.0.0.0` (via the
     image CMD, only on the compose network, not published to the host).
  2. **`sync` / `web` started before DB migrations finished** (`depends_on: service_started` only waits for the
     container to start) → `backend` now has a healthcheck (healthy once uvicorn is listening = after
     migrations), and `sync` / `web` use `depends_on: service_healthy`, eliminating the first-boot
     `relation "opnsense_firewalls" does not exist` error.
- Verified by a full `docker compose up`: all 5 services healthy, HTTP→HTTPS redirect, frontend and `/api`
  proxy both return 200, admin auto-created, admin login returns an access token, and the `sync` loop runs
  with zero errors.

## [0.4.204] — 2026-06-19

### Added
- **Optional Docker Compose deployment** (`deploy/docker/`). A secondary / optional path (systemd + apt
  remains the primary one): one compose file brings up `postgres` (pgvector) / `redis` / `backend` / `sync`
  (a background sync loop replacing the systemd timer) / `web` (nginx serving the frontend + reverse-proxying
  `/api` + self-signed HTTPS). Ships `gen-env.sh` (random secrets) and `update.sh` (`git pull` → rebuild →
  restart). **Upgrading is just `./update.sh`** — the backend container runs `alembic upgrade head` on start,
  so there's no manual migration step. Verified end-to-end: images build, a fresh pgvector runs all
  migrations 0001→0080, the admin is auto-created, and uvicorn boots.

## [0.4.203] — 2026-06-18

### Changed
- **Proxmox VE VM DSV is now per-cluster (supports multiple PVE clusters / standalone nodes).** Since vmids
  repeat across clusters, a single global DSV would conflate them. Added a per-cluster endpoint
  `GET /api/v1/lookup/proxmox/{cluster_id}/vms`; the Graylog DSV settings page lists **one row per cluster**
  (mirroring OPNsense's multiple firewalls), each with its own URL / lookup table. The global
  `…/proxmox/vms` (all clusters, de-duplicated) is kept for single-cluster setups.

## [0.4.202] — 2026-06-18

### Added
- **New Graylog DSV source for Proxmox VE VMs (vmid → VM name).** Endpoint
  `GET /api/v1/lookup/proxmox/vms` (reusing the Graylog DSV token) maps key = Proxmox VMID to value = the
  synced VM name, so Graylog can enrich a log's vmid with a readable VM name. If vmids collide across
  clusters, only the first per vmid is emitted. The Graylog DSV settings page lists it automatically
  (global, alongside "IP → hostname").

### Fixed
- **Firewall DSV hint text column indices** also corrected to key = 0, value = 1 (0-based; the previous
  release only fixed the main guide table and missed this hint string).

## [0.4.201] — 2026-06-18

### Changed
- **Added a "Delete" button to the subnet detail page toolbar** (with a confirm prompt). Previously you had to
  go back to the "All subnets" list and use the row trash icon or batch delete — and the actions column is
  often pushed off the right edge. Now you can delete a subnet straight from its detail page; it refreshes the
  sidebar subnet tree and returns to the list.

## [0.4.200] — 2026-06-18

### Fixed
- **Version check flagged an older version as newer.** "Check GitHub latest" compared version strings with
  `!=`, so `0.4.79` looked newer than `0.4.199` (string-wise `'7' > '1'`); and since releases are pushed to
  main without a release/tag, it fell back to a stale tag. It now reads `version.py` from the **main branch**
  (reflecting what's actually published) and compares **numerically** (the tags fallback also picks the
  numerically-highest).

### Changed
- **Version Info page layout:** "Check GitHub latest" now sits in the third cell of the top row (next to
  Current version / Python) instead of spanning its own full-width row.
- **Hardened LibreNMS auto-create subnet selection to avoid wrong placement.** The target subnet is now the
  *single most-specific* (longest-prefix) match: nested ranges pick the most specific; under **overlapping
  subnets where two+ share the longest prefix, it skips rather than guessing** (better to not create than
  create in the wrong unit); no creation if no existing subnet contains the IP. Set the instance's subnet
  scope to disambiguate.

## [0.4.199] — 2026-06-18

### Fixed
- **Graylog DSV guide had the wrong Key/Value column indices.** Graylog's "DSV File from HTTP" adapter uses
  **0-based** column indices, so the correct values are **Key column = 0, Value column = 1**; the guide page
  and README previously said 1/2.

## [0.4.198] — 2026-06-18

### Fixed
- **Firewall rule DSV (`rid → alias`) dropped UUID-format rules.** A filterlog `rid` (the pf rule label) comes
  in two formats: a 32-char md5 (pure hex) and a UUID (with hyphens). The old `_RL_LABEL` regex `[0-9A-Za-z]+`
  excluded hyphens, so rules with a UUID label failed to match entirely and were skipped — only the md5-labeled
  ones survived (one firewall captured 10 rules when it should have been 59, covering 44 aliases). The pattern
  now captures the full quoted label content (which *is* the `rid`), covering md5 / UUID / custom labels.
  > Note: `rid → alias` only ever covers aliases referenced by a labeled rule; aliases not used in any rule have
  > no `rid` (and never appear in filterlog), which is expected.

## [0.4.197] — 2026-06-18

### Added
- **Cert-distribution agents can link to a device.** The agent edit dialog gains a "Linked device" picker
  (`cert_agents.device_id`, migration 0080, SET NULL on device delete). Once linked: ① the agent **name**
  in the distribution-agents list and the **Advanced → Cert distribution status** page becomes a clickable
  link to that device's detail; ② the **source-IP column** becomes clickable — the backend resolves the
  agent's reported source IP to its IPAM address (preferring the one attached to the linked device under
  overlapping ranges) and links to it. Falls back to plain text when there is no linked device or the
  source IP has no matching address.

### Changed
- **Graylog DSV guide tweaks.** "Format" (output setting) and "Regenerate token" (the key) are unrelated and
  no longer share a row. The Extractor and Pipeline are **alternatives** (pick one), not sequential steps —
  they are now "Method A / Method B" under Step 2 sharing one "log field" input, instead of being numbered
  Steps 2 and 3. The click-to-copy toast now says "Copied to clipboard".

## [0.4.196] — 2026-06-18

### Added
- **LibreNMS sync can auto-create discovered IPs.** Each LibreNMS instance gains an "Auto-create
  discovered IPs" toggle (default on): on sync, each monitored device's **primary IP** is auto-created
  as an IPAddress inside the matching existing subnet (tagged `discovery_source=librenms`). Device
  primary IPs only — not ARP neighbours; if the instance has a subnet scope, only within that scope; and
  skipped if the subnet does not exist in IPAM yet. Fixes the confusing "0 used / live status all zero"
  state when only LibreNMS is connected (no scan agent): LibreNMS imports devices and previously only
  stamped liveness onto pre-existing IPs, never creating them.

### Fixed
- **Dashboard "live status" miscounted scanner/LibreNMS-confirmed online IPs as "unknown".** The counter
  matched against case-mismatched literals (`Online (scanner)` etc.), but the values actually written are
  lowercase with a source suffix (`online (scanner)` / `online (librenms)`) → now uses
  `startswith("online")` (matching `recompute_effective_status`).

### Changed
- **Default chat model is now `gemma4:26b`** (was `gpt-oss:120b`) — aligning the compiled default with
  the README's existing recommendation; applies to anything that hasn't overridden it in LLM settings
  (including fresh installs). Existing overrides are unaffected.
- **Docs:** the Local AI section now notes that no LLM Server is bundled — set one up on a GPU-capable
  host and point jt-ipam at it.

## [0.4.195] — 2026-06-18

### Changed
- **Graylog DSV page cleanup.** The DSV sources table loses the redundant "Copy" button in the actions
  column (value copying already lives in the guide below — click any value to copy); the "Details" button
  is renamed to "URLs / settings" to better describe the lookup URLs and settings it shows.
- **"Log field to query" input moved into Step 2 (Extractor).** It used to sit orphaned between Step 1 and
  Step 2 with no step number; it now lives where it is first used (above the Extractor's Source field), and
  the Step 3 (Pipeline) text now points at "the log field configured in Step 2".

## [0.4.194] — 2026-06-18

### Changed
- **Graylog DSV guide polish.** The setup steps now use prominent numbered circles (matching the cert
  install help), and every source — including the firewall rule/alias DSVs — shows **both** the Extractor
  and the Pipeline method (each with the concrete field / Lookup Table / output for that source). The
  config tables now tint the left (field-name) column to separate it from the values, and every value you
  paste into Graylog is **click-to-copy** (click any highlighted value).

## [0.4.193] — 2026-06-18

### Changed
- **Graylog DSV page: the endpoint list is now a real data table and drives the guide.** The DSV sources
  table gains sorting, a column picker, a quick-filter box and a refresh button; clicking a row selects
  that source and the Graylog setup guide below re-renders for it (correct lookup URL, Lookup Table
  names, key/value columns and a matching pipeline rule — IP→hostname keeps the LAN cidr_match guard,
  firewall rule/alias sources use a plain rid/alias lookup), with a fade/slide transition when switching.
  The page also drops its fixed max-width and uses the full width. Term: "詳情" → "詳細資料".

## [0.4.192] — 2026-06-18

### Changed
- **Graylog DSV page reworked into one extensible endpoint table + detail drawer.** Instead of stacking a
  separate card with two URL boxes per DSV source (which got cluttered as firewalls were added), all DSV
  endpoints (IP→hostname plus each firewall's rule and alias lookups) now appear in a single table
  (name / mapping / status / actions); clicking "Details" opens a drawer with the HTTPS + intranet-HTTP
  URLs, copy buttons, and per-source settings (the IP→hostname enable/path live there). The shared format
  and token sit above the table. New DSV types only need a row in the source list, so the layout scales.

## [0.4.191] — 2026-06-18

### Added
- **OPNsense firewall Graylog DSV (rule label → alias, and alias → members).** In addition to the existing
  IP→hostname DSV, each OPNsense firewall can now expose two token-protected lookup tables for Graylog to
  enrich firewall logs: `/api/v1/lookup/firewall/{id}/rule-aliases` (key = filterlog `rid` / pf rule
  label, value = the alias names that rule references) and `/api/v1/lookup/firewall/{id}/aliases`
  (key = alias name, value = member list). The rule-label map is parsed each sync cycle from
  `/api/diagnostics/firewall/pf_statistics/rules` (covers user + plugin + auto rules); the alias DSV uses
  the already-synced alias content. Enable per firewall with the new "Expose firewall DSV" toggle
  (Integrations → OPNsense); the lookup URLs (per firewall, distinct paths) appear on the Graylog DSV
  settings page. Migration 0078 (opnsense_rule_labels + opnsense_firewalls.expose_dsv).

## [0.4.190] — 2026-06-17

### Changed
- **Circuits table now shows bandwidth, static IP and gateway columns.** These fields already existed on
  the circuit (and in the edit form) but weren't surfaced in the list; added a human-readable bandwidth
  column (↓down / ↑up, formatted as Gbps/Mbps/kbps) plus the static IP/CIDR and gateway columns (all
  toggleable in the column picker).

## [0.4.189] — 2026-06-17

### Security
- **Cleared the open Dependabot alerts** (frontend build toolchain) by pinning patched versions via
  `pnpm.overrides`: `form-data` ≥4.0.6 (CRLF injection, GHSA-hmw2-7cc7-3qxx — reached via axios/jsdom),
  `vite` ≥6.4.3 (`server.fs.deny` bypass on Windows, GHSA-fx2h-pf6j-xcff — also fixes the bundled
  launch-editor NTLMv2 advisory), and `js-yaml` ≥4.2.0 (quadratic-complexity DoS in merge keys). `pnpm
  audit` is now clean and the build is unchanged (vite stays in 6.x). These are build/dev dependencies and
  are not part of the shipped browser bundle.

## [0.4.188] — 2026-06-17

### Changed
- **The scan-agent installer no longer installs avahi (mDNS) by default.** `avahi-utils` depends on
  `avahi-daemon`, so installing it brings up a resident service that listens on UDP 5353 and announces
  the host over mDNS — an unwanted side effect on most servers. The installer now installs only `nmap`
  (OS) and `samba-common-bin` (NetBIOS), neither of which starts a daemon; mDNS is opt-in via
  `JT_IPAM_ENABLE_MDNS=1`. (The main server install/upgrade never touched these.) The agent
  install-help note now flags that avahi-utils brings up avahi-daemon.

## [0.4.187] — 2026-06-17

### Changed
- **NetBIOS / mDNS hostname sources now show localized labels** in the IP detail panel (the source tags
  and the "pin hostname source" dropdown), matching the source-precedence page. Added a regression test
  asserting NetBIOS / mDNS names from a scan-agent report are recorded as distinct `netbios` / `mdns`
  observation sources.

## [0.4.186] — 2026-06-17

### Fixed
- **Save button in the IP address edit modal did nothing / lost edits (issue #6, thanks @lin-junyou).**
  The conditionally-rendered action buttons (Save / Edit / Create / Cancel / Back) and the delete
  popconfirm shared a slot via `v-if`/`v-else` with no unique `:key`, so Vue reused the vnode across the
  view↔edit switch and kept the *previous* branch's `@click` — clicking Save fired Back/Edit and the edit
  was silently dropped. Gave each conditional button/popconfirm a stable `key` (both the inline
  `#header-extra` and the modal `#footer`).
- **Install on Ubuntu 26 failed with "requires a different Python: 3.14 not in '<3.14,>=3.11'" (issue #5,
  thanks @Ghucos).** Ubuntu 26.04 ships Python 3.14; the backend's `requires-python` capped it below 3.14,
  so pip refused to install. Widened to `>=3.11,<3.15` to allow 3.14.

## [0.4.185] — 2026-06-16

### Added
- **NetBIOS and mDNS name probes are now actually implemented** in the scan agent (previously they were
  advertised as selectable probes but were no-op Phase-B stubs that produced no name). The agent now runs
  `nmblookup -A <ip>` (or `nbtscan`) for NetBIOS and `avahi-resolve -a <ip>` for mDNS against alive hosts
  that have those probes enabled, and reports the resolved names. They are recorded as **distinct hostname
  sources** (`netbios` / `mdns`) so you can order or disable them independently in **Name / ARP source
  precedence**. Agent bumped to v1.4.0 (self-updates). SNMP remains intentionally unimplemented
  (credential-based). No migration (the observation `source` column is unconstrained).

## [0.4.184] — 2026-06-16

### Changed
- **Login language switcher is now a click-to-open dropdown** listing both languages, instead of a button
  that toggled immediately.
- **"Save order" buttons on the source-precedence page now have a save icon** (all five sections).

## [0.4.183] — 2026-06-16

### Changed
- **Login page now has a language switcher** (zh-TW ⇄ en-US) in the card header, so you can switch
  language before signing in.
- **Notification bell tidy-ups:** an icon before the "Notifications" title and on the "mark all read"
  button, and the list now scrolls inside the popover (capped height) instead of growing past the screen
  when there are many notifications.
- **IP-request notifications are now Chinese** ("IP 申請已核准" / "IP 申請已拒絕") instead of the
  hardcoded English "IP request approved/rejected" (matching the other in-app notifications).
- **Scan-agents table column widths:** the source-IP column no longer wraps, and the spare width is
  shared between the name and last-error columns instead of leaving the name column overly wide.

## [0.4.182] — 2026-06-16

### Changed
- **Login: SSO buttons only show for configured providers.** `/auth/realms` now also reports which SSO
  providers (OIDC / SAML) are enabled, and the login page renders a provider's button only when it is
  actually configured — so clicking e.g. "Sign in with SAML" no longer dumps a raw `{"detail":"SAML is
  disabled"}` page. The whole "or SSO" section is hidden when neither is enabled.
- **Login: the jt-ipam logo now appears before the title** on the login card.
- **Webhooks: events are now a checkbox list with descriptions** instead of a free-text tag input. The
  catalogue lists exactly the events the backend emits (`subnet.created`, `ip_request.created` /
  `.fulfilled` / `.rejected`, `anomaly.detected`) plus `*` (all), each with a one-line explanation.
- **Integration scope: tidier layout.** On the six integration settings forms the scope-subnet dropdown
  and the overlap warning now stack in a full-width block instead of being squeezed side-by-side.
- **RIPE / TWNIC import: less cramped fields** — added comfortable spacing between the Handle / CIDR /
  target-section rows so the hints no longer touch the next label.

### Added
- **LLM settings: optional chat context length (`num_ctx`).** Lets an admin raise the chat model's
  context window so tool-heavy MCP chats with large injected data don't overflow Ollama's default (~4096)
  and get silently truncated. Blank / 0 = use the model/Ollama default; flows into Ollama `options.num_ctx`
  for chat only (not embeddings).

## [0.4.181] — 2026-06-16

### Changed
- **Tidier certificate detail panel.** The per-version detail in the certificate Files modal (domains /
  subject / issuer / serial / validity / fingerprint / uploaded-at) is now a two-column aligned grid
  (definition list) so every value lines up in a single column, with serial and fingerprint in a
  monospace font. Previously it was a ragged list of `label：value` lines.

## [0.4.180] — 2026-06-16

### Fixed
- **nginx config test failing on Debian 13 with `"server_tokens" directive is duplicate`.** Our nginx
  site set `server_tokens off;` at http context (top of the included file). Debian 13's stock
  `nginx.conf` now ships `server_tokens off;` in its own `http{}` block, so a second one in the same
  context is a fatal `[emerg]` (older Debian/Ubuntu had it commented out, so it never clashed). Moved
  `server_tokens off;` into each `server{}` block in both `jt-ipam.conf` and the external-proxy template
  — server context coexists with / overrides any http-level value on every distro. Verified with
  `nginx -t` under a parent `http{}` that already sets it. Config template only.

## [0.4.179] — 2026-06-15

### Fixed
- **Install silently aborting right after `Building frontend…` on hosts without `~/.nvm`** (same
  `set -e` + `pipefail` class as v0.4.178). In `ensure_node`, `nb=$(find ~/.nvm/... | sort | head -1)`
  fails the whole assignment when `find` hits a missing directory (or `head` SIGPIPEs `sort`), and under
  `set -e` that exits the script with **no error message** — leaving Node uninstalled and the frontend
  unbuilt while the run "looked" like it just stopped. Guarded that and the other pipe-in-`$()` spots
  (nvm lookup, admin-password generation, backup-file lookup) with `|| true` so a failed/SIGPIPE'd
  pipeline can no longer abort the install. The success path is unchanged (the guard is a no-op when the
  pipeline succeeds), so working installs are unaffected. Install-script only.

## [0.4.178] — 2026-06-15

### Fixed
- **Real root cause of the Debian 13 install failure: a `set -o pipefail` + `grep -q` SIGPIPE bug in the
  package-availability check.** `apt-cache madison <pkg> | grep -q .` reports a package as *unavailable*
  whenever madison emits multiple version lines (e.g. trixie lists `postgresql-17` twice — 17.10 from
  -security and 17.9 from main): `grep -q` exits on the first line and closes the pipe, `apt-cache` gets
  SIGPIPE (rc 141) writing the next line, and `pipefail` propagates that as a failed pipeline. So the
  installer "couldn't see" native PG 17 + pgvector even though both exist, and fell through to PGDG and a
  FATAL. Replaced the piped check with a pipe-free `_pkg_installable()` (command substitution + `[ -n ]`),
  applied to both the PostgreSQL and Python detection loops. Single-version distros (Ubuntu 24.04) emit
  one line and never hit it, which is why it surfaced only on Debian 13. Install-script only.

## [0.4.177] — 2026-06-15

### Changed
- **Installer refreshes the apt index and retries before falling back to PGDG.** If no PostgreSQL
  (>= 16) with a matching `postgresql-N-pgvector` is found in the default repos on the first look, the
  script now runs `apt-get update` once and re-checks before adding the PGDG repo — so a transient/stale
  apt index at install time (the likely reason a Debian 13 box with native PG 17 + pgvector wasn't picked
  up) uses the native packages cleanly instead of needlessly pulling in PGDG. Install-script only.

## [0.4.176] — 2026-06-15

### Fixed
- **Install on Debian 13 (trixie) no longer dies on `postgresql-16-pgvector` not installable** (customer
  report). The installer used to pick a PostgreSQL server package by itself and, on fallback, hardcode
  PG 16 — but PGDG for trixie currently ships pgvector only for its newer versions (17/18), so
  `postgresql-16-pgvector` was missing and the install aborted. It now selects a PostgreSQL version where
  **both** the server **and** the matching `postgresql-N-pgvector` are installable (tries 16 → 17 → 18 in
  the default repos first, then adds PGDG and retries), instead of forcing 16. Install-script only.

## [0.4.175] — 2026-06-15

### Changed
- **Config-generator service grid no longer wraps long labels** — the service multi-select now uses
  auto-fill columns wide enough (min 135px) for the longest profile name (`wazuh-dashboard`) and keeps
  each label on a single line, so only that one option no longer breaks onto two rows.
- Docs: the certificate-distribution caption now reads "certificate files can be uploaded manually or
  pulled from a URL / SFTP source on a periodic sync".

## [0.4.174] — 2026-06-15

### Changed
- **Hid the `jitsi` and `coturn` cert-distribution service types** from the deploy-profile picker for now —
  docker-jitsi-meet is not officially supported yet, so those options are no longer offered in the UI or
  listed in the docs (the dormant agent profile code is kept for easy re-enable later). Also refreshed the
  docs gallery (added a certificate-distribution screenshot) and the feature map's certificate-vault branch.

## [0.4.173] — 2026-06-15

### Added
- **Auto-fetched certificates (SFTP / URL sources) now auto-complete their chain.** When a sync pulls a
  new cert that only has leaf+intermediate, jt-ipam builds the full intermediate+root chain before storing
  (using the fetched files or the server's system trust store, e.g. ISRG Root X1) — so strict services
  (Zimbra / PDM) keep verifying on every renewal without anyone clicking "Build full chain" again.
- New distribution profiles **`jitsi`** (docker-jitsi-meet web: `/root/.jitsi-meet-cfg/web/keys/cert.{crt,key}`,
  restarts the jitsi web container) and **`coturn`** (`/etc/coturn/certs/turn.{crt,key}`, root:65534 so the
  container user can read the key; restarts the coturn container or native systemd coturn).

## [0.4.172] — 2026-06-15

### Fixed
- **The cert-agent installer no longer hangs silently** in LXC/containers with a dead IPv6 path or a
  firewall blackhole. Its curl calls now use `--connect-timeout 10 --max-time 60 --retry 2` (so a stuck
  IPv6 attempt falls back to IPv4 in ~10s instead of hanging forever), print a "Downloading agent…" line,
  and emit a clear error with a connectivity-test hint if the download fails.

## [0.4.171] — 2026-06-15

### Changed
- The cert agent now prints progress lines for the slow Zimbra steps even without `--debug`
  ("verifying… / deploying… / restarting Zimbra (zmcontrol restart — can take a few minutes)…"),
  so a normal run no longer looks hung during the multi-minute `zmcontrol restart`.
- The installer-generated nginx site config (`deploy/nginx/*.conf` → `/etc/nginx/sites-enabled/jt-ipam`)
  now has **English-only comments** (customer-facing deployed files should not contain Chinese).

## [0.4.170] — 2026-06-15

### Fixed
- **Zimbra deployment ran `zmcertmgr` as root and failed** (`zmcertmgr: ERROR: no longer runs as root!`).
  It now runs via `su - zimbra` and stages the cert/chain/key in a zimbra-readable dir
  (`/etc/.../jt-ipam` → `/opt/zimbra/ssl/jt-ipam`), matching Proxmox/Zimbra's documented flow.
- The cert-status page no longer shows "up to date" for a deployment that actually failed — status now
  requires both a fingerprint match **and** an `ok` report.

### Added
- **Certificate chain check + one-click fix.** The Files/info dialog now analyses each version's chain:
  "Full chain" (reaches the root CA), "Chain fixable" (root present but not in the chain — a **Build full
  chain** button rebuilds it in place, fingerprint unchanged), or "Missing root CA" (with a hint on how to
  obtain and re-upload the root). Strict-validating services (Zimbra / PDM) need the full chain.
- The **Files dialog is now a detailed certificate-info view**: SAN domains, subject, issuer, serial,
  validity window, full SHA-256 fingerprint (copyable), upload time, plus per-format download.
- **Export buttons** on the Certificates, Distribution-agents and cert-status pages (the last two were missing).
- The cert-status page now shows **one row per agent** with its services aggregated (e.g. `pbs, pve`)
  instead of one row per deployment; the status tooltip lists each cert/service.

## [0.4.169] — 2026-06-15

### Fixed
- **Corrected the `pdm` (Proxmox Datacenter Manager) profile** to the official paths and service:
  cert+chain → `/etc/proxmox-datacenter-manager/auth/api.pem`, key → `…/auth/api.key` (root:www-data 640),
  reload `systemctl restart proxmox-datacenter-api.service`. (Previous paths/service were wrong guesses.)
- **Every generated shell command that used `sudo` is now root-aware.** A shared `SUDO` helper
  (`$([ "$(id -u)" -ne 0 ] && echo sudo)`) is applied to: the cert-agent dry-run / run commands and the
  install/uninstall one-liners, the scan-agent install one-liner, and the probe-tool `apt install` hints.
  On hosts that are already root with no `sudo` binary they now run directly.

### Added
- The cert agent gains a **`--debug`** flag (default off) that prints each command and shows the full
  output of config-test / reload / `zmcertmgr` / downloads — useful for diagnosing e.g. a Zimbra
  `verifycrt` failure (whose root cause is usually a chain missing the root CA).

### Changed
- Install-help step 3 now leads with the **Generate config** tool (quick path) and demotes manual
  config editing to a secondary note.

## [0.4.168] — 2026-06-15

### Fixed
- **Critical: the conditional-sudo install one-liner from 0.4.167 failed as root.** With `$(…)` expanding
  to empty, the `VAR=value` env assignments after it were parsed as a command, not an assignment
  (`JT_IPAM_URL=…: No such file or directory`). Fixed by running through `env`
  (`… | $([ "$(id -u)" -ne 0 ] && echo sudo) env JT_IPAM_URL=… bash`), which works as both root and non-root.
- The AI chat header action buttons now align hard-right (they could drift left when the header wrapped).

### Changed
- The cert-agent **install-help dialog no longer duplicates the full install command** — each agent's
  dialog already shows its ready-to-paste one-liner (key filled in, sudo auto-detected), so the help now
  just points there and keeps the supported-OS overview.
- Relabeled the one-liner from "(root)" to "(auto root / sudo)".

## [0.4.167] — 2026-06-15

### Fixed
- The cert-agent install / uninstall one-liners now add `sudo` **only when not already root**
  (`$([ "$(id -u)" -ne 0 ] && echo sudo)`). On hosts that are already root and have no `sudo` binary
  (common on Proxmox VE / PBS / PDM and minimal appliances) the previous `| sudo … bash` failed with
  `sudo: command not found`; it now runs directly as root.

## [0.4.166] — 2026-06-15

### Fixed
- **Deleting a certificate that a distribution agent still references is now blocked** (409 with the
  agent names) instead of leaving an orphan UUID in the agent's scope. The edit-agent dialog also now
  shows any already-orphaned scope entries as "<id>… (certificate deleted)" so they can be removed,
  rather than a bare UUID.

### Added
- New distribution profiles: **`pdm`** (Proxmox Datacenter Manager) and **`wazuh-dashboard`**
  (OpenSearch Dashboards). Univention UCS was evaluated and intentionally left to manual mode (its
  cert path is FQDN-specific and managed by the UCS internal CA).
- **Filter the distribution-agent list by certificate** (which cert an agent is scoped to), alongside
  the existing name/IP filter.

### Changed
- The distribution-agent **"deployed / reported" count** now shows the actual deployments on hover
  (each cert / profile and its status).
- **Tidied the cert-agent installer's post-install output** — one compact summary (timer, config status,
  deployable certs, test/apply commands, logs) instead of a long multi-line dump.

## [0.4.165] — 2026-06-15

### Changed — consistent table pagination + filter alignment
- Applied the shared `useTablePagination` (page-size bound to the user preference, cross-device) to all
  client-side list tables that were still missing it — the certificate + distribution-agent tables, the
  read-only cert-status page, and a sweep across Advanced resources, Physical (cabling/power/VPN),
  Virtualization, VLANs/VRFs, NAT, Devices, Scan agents, Groups, Permissions, Wazuh, Anomaly, firewall
  alias mappings, customer sub-tables and device ports. Server-paginated tables (addresses, audit, users,
  tasks, IP changes) and small fixed config/instance panels are intentionally left unpaginated.
- Fixed the certificate/agent/cert-status **filter inputs** rendering shorter than the toolbar buttons
  (toolbar buttons are forced to 34px; the inputs now use the default size to match).

## [0.4.164] — 2026-06-15

### Added — certificate tools for AI chat / MCP
- Two read-only MCP tools so the AI chat (and external MCP clients) can answer about certificates:
  - `list_certificates` — managed cert metadata: name, domains, current fingerprint, expiry, days
    remaining, version count, self-signed flag, auto-fetch source; `expiring_within_days` filters to
    soon-to-expire certs.
  - `list_cert_distribution` — distribution agents and their per-site deployment status (cert/profile,
    up-to-date vs drift, expiry, agent version, and whether one key is shared by multiple hosts).
- Both are **read-only and never expose private keys / PEM bodies**, and are gated as global-read
  infrastructure data (admin or a universal-read viewer), consistent with the cert-status page.

## [0.4.163] — 2026-06-15

### Added
- **Manual renew for self-signed certificates** — self-signed certs get a **Renew** action that
  re-issues a new version reusing the current CN/SANs (adjustable validity days), so agents pick it
  up on the next fingerprint change.
- **Same-key-on-multiple-hosts detection** — the agent records recent reporting source IPs
  (migration 0077, `recent_sources`); if a key is used from more than one IP within 7 days the
  distribution-agent list flags a warning next to the source IP, and the create-agent dialog +
  install help now recommend **one key per host**.
- **Agent CLI flags** — `--help` usage, `--upgrade` (self-update to the server's latest agent then
  exit, even when `AUTO_UPDATE=false`), and `--force` (re-deploy even when already up to date).
- Name/IP **filter box** on the certificate + distribution-agent tables; the read-only cert-status
  page (Advanced) gains a column picker, sortable columns, a filter row, and **source-IP + agent-version**
  columns. Tab headers got icons.

### Changed / Fixed
- **Agent now reports even when already up to date** — previously a re-keyed agent showed `0/0`
  because the up-to-date path sent no report; it now reports the current state every run.
- **Proxmox/Zimbra hardening (cont. from 0.4.162):** carried into this release with the version-column
  "update available" indicator changed from a text tag to a single icon that no longer wraps.

## [0.4.162] — 2026-06-15

### Added — more web-server / service profiles for the distribution agent
- The cert distribution agent (and the **Generate config** tool + installer) now ship 9 more profiles:
  **caddy / traefik / lighttpd / zoraxy / jetty / exim4 / mosquitto / cockpit / webmin** (on top of
  nginx / apache / haproxy / postfix / dovecot / pve / pmg / pbs / zimbra). Each provides its fixed
  write paths + reload command; **jetty** receives a **PKCS#12 keystore** (`<cert>.p12`), served via a
  new `part=pkcs12` on `GET /cert-agents/bundle/raw`.

### Changed — install-help UX
- Supported OS / distributions are shown as prominent tags (Debian / Ubuntu / RHEL family / Fedora / SUSE).
- Fixed the leading-space indent on the first line of the curl one-liner (inline `<code>` now `display: block`).
- The standalone **Config help** toolbar button is hidden — config generation lives in the per-agent
  **Generate config** action; step 3 of the install help points to it (with its tool icon).

## [0.4.161] — 2026-06-15

### Added — certificate file viewer & multi-format download
- A **Files** button on each certificate row lists every version (fingerprint / expiry / domains / current)
  and lets you **download** each one in a chosen format: full chain / cert (.crt) / chain / private key
  (.key) / combined / **DER** / **PKCS#12 (.pfx)** (built server-side via cryptography). Formats containing
  the private key (key / combined / pfx) are audited (`GET /certificates/{id}/versions/{vid}/file?fmt=`).

## [0.4.160] — 2026-06-15

### Changed
- Added right padding to the action column (delete button) so it no longer hugs the edge.
- When a certificate already has a source or a version, the **"Self-signed" button is disabled** (avoids
  overwriting the existing cert), with a hover explanation.
- The installer config comments now note you can use the "Generate config" tool in jt-ipam.

### Security
- Fixed Dependabot alert (GHSA-gv7w-rqvm-qjhr, High): bumped **esbuild to 0.28.1** via a pnpm override
  (0.25.12 came in through vite; <0.28.1 has a "Deno module binary integrity" issue). It's a build-time
  dev dependency and this project builds via Node/vite (not esbuild's Deno install path), so it isn't
  actually reachable; the frontend build passes after the bump.

## [0.4.159] — 2026-06-15

### Changed — richer config generator
- Each "certificate / service" block now **generates the service's SSL config snippet** (e.g. nginx
  ssl_certificate / ssl_certificate_key, apache SSLCertificate*), with a **copy button on every write path
  and on each snippet**. Services that read fixed paths (pve/pmg/pbs) show "no service config change".
- Added the **full dry-run / real-run commands** (with the complete sudo bash path) plus copy buttons.
- The service checkboxes are now laid out in a tidy grid.

### Added — edit agent / enable toggle
- The distribution-agent action column gains an **Edit** button: rename, **adjust the deployable-certificate
  scope** (add more later for more sites), and toggle enabled.
- The "Enabled" column is now a **switch** for one-click enable/disable.
- The "Deployable certs" column shows **which certificates** (names) on hover.
- "Rotate key" / "View key" tooltips clarify it's the **agent connection key (not the SSL cert)**.
- Install help step 3 points to the "Generate config" tool (with its icon); the toolbar "Config help"
  button was removed (reachable from inside the install help).

## [0.4.158] — 2026-06-15

### Added — distribution-agents page improvements
- **Config generator** (a tool button in the action column): pick certificates (within the agent's scope)
  and check services (nginx/apache/pve… multiple) to auto-generate the quick-mode config; an "Advanced /
  manual mode" section lets you fill custom paths. Live preview + one-click copy to paste into the host.
  It also **lists the full on-host paths/filenames each quick-mode profile writes** (cert / key / chain),
  so you know where to point your service config.
- The "Deployed / reported" column gained a tooltip (successful deployments / total reported).
- **Slimmer install help**: the config-format explanation is split into a separate **"Config help"** button;
  the install help keeps only the install/uninstall steps.
- **Latest server agent version** shown in the distribution-agents toolbar (`GET /cert-agents/server-version`).
- The "Close" button in the agent-info dialog now has an icon.

## [0.4.157] — 2026-06-15

### Changed
- The installer's `DEPLOY_1_CERT` example now uses the generic placeholder `example.com` (RFC 2606
  reserved domain) instead of a real certificate name; the real deployable names are still listed in the
  "This agent is allowed to deploy" comment above for you to substitute.

## [0.4.156] — 2026-06-15

### Changed — installer pre-fills the certificate names this agent can deploy
- At install time the installer asks the server (with the agent key) which certificates this agent may
  deploy, and **lists the real names in the config comments and pre-fills the `DEPLOY_1_CERT` example**, so
  you no longer have to guess what `DEPLOY_<N>_CERT` should be (it's the certificate name from jt-ipam).
- The installer also prints the deployable certificate list at the end (it won't overwrite an existing
  config, but still prints the list for reference).

## [0.4.155] — 2026-06-15

### Fixed
- Distribution-agent table: the version column's "update available" tag now wraps (and the column is
  wider) instead of overflowing into the source-IP column.
- Name and last-report columns are both flexible so they share the leftover width — the name column no
  longer over-stretches on its own.

## [0.4.154] — 2026-06-15

### Changed
- The agent config template is now split into **QUICK MODE (preferred)** and **MANUAL MODE** sections.
  The quick-mode comments spell out exactly which cert / key / chain paths and filenames each profile
  writes, with the matching nginx / apache directives, so you know what to point your service config at.

## [0.4.153] — 2026-06-14

### Changed
- The agent config template comments now **list every built-in profile** (nginx / apache / haproxy /
  postfix / dovecot / pve / pmg / pbs / zimbra / generic) with each one's default file paths and reload
  command, so opening the config file shows exactly what's available.

## [0.4.152] — 2026-06-14

### Changed
- Distribution-agent config now centers on `DEPLOY_<N>_PROFILE` (the service), which **provides the reload
  command**, so `DEPLOY_<N>_RELOAD` is no longer needed in the common case. Set just "cert + service", or
  add custom paths (`FULLCHAIN`/`KEY`…) to override where files go while still using the profile's reload;
  `DEPLOY_<N>_RELOAD` is demoted to an advanced override for custom services. Template and help updated.

## [0.4.151] — 2026-06-14

### Changed — distribution-agent config is now one setting per line
- The agent config moved from a single packed line (`DEPLOY_1="cert=..; profile=..; fullchain_path=.."`)
  to readable, one-setting-per-line `DEPLOY_<N>_*` groups:
  - `DEPLOY_1_CERT=` (certificate), `DEPLOY_1_FULLCHAIN=` (cert file path), `DEPLOY_1_KEY=` (key path),
    `DEPLOY_1_RELOAD=` (reload command); optional `DEPLOY_1_CHAIN/CRT/COMBINED/TEST`.
  - Or just `DEPLOY_1_CERT=` + `DEPLOY_1_PROFILE=nginx` to use a built-in profile (fixed paths).
- Installer template and the install-help modal example updated. Validated end-to-end against a live
  server (dry-run + real apply).

## [0.4.150] — 2026-06-14

### Changed
- The distribution-agent scripts (`jt_ipam_cert_agent.sh` and the installer) are now fully English
  (comments, terminal output, config template), matching the `scripts/*.sh` convention — scripts that run
  on customer terminals don't contain Chinese.
- The installer gains an **uninstall** mode: `JT_IPAM_UNINSTALL=1` stops and removes the timer / service,
  agent program, config and state (certificate files already deployed to services are kept). The install
  help modal now includes the uninstall one-liner.

## [0.4.149] — 2026-06-14

### Added — re-viewable agent key & install command
- A distribution agent's enroll key is now also stored AES-GCM encrypted (alongside the hash), so it can
  be **retrieved again from the "View" action** in the list (admin only, `GET /cert-agents/{id}/key`). The
  action column gains a "View" button that shows the key + the one-line install command (with the key) +
  copy buttons.
- The create / rotate-key dialog now also shows the one-line install command; "cannot be retrieved later"
  is replaced with "retrievable later via View".
- Deleting an agent also removes its encrypted key.
- Agents created on older versions (no stored plaintext) return a hint to rotate the key instead.

## [0.4.148] — 2026-06-14

### Changed
- After "Generate & install key", the login-private-key field becomes disabled and shows "Generated and
  stored by jt-ipam", so users don't think they still need to paste a key.

## [0.4.147] — 2026-06-14

### Fixed
- Certificate table layout: the action column is now `fixed: "right"` (pinned, never pushed off-screen on
  narrow widths) and widened to fit all icons; name and domains are flexible and share the leftover width.
- Traditional-Chinese copy now uses full-width punctuation and Taiwan-localized terms (rollback, one-time,
  atomic-write wording) across the agent install help, source config, and agent script comments.

## [0.4.146] — 2026-06-14

### Changed — distribution agent is now pure bash (no Python / PyYAML)
- The distribution agent was rewritten as **pure bash** (`jt_ipam_cert_agent.sh`), depending only on
  **curl + coreutils** — no Python, jq or YAML. Config is now `KEY=VALUE`
  (`/etc/jt-ipam-cert-agent/config`, `DEPLOY_N="cert=..; profile=.."`); profiles, atomic write,
  config-test, reload, rollback, `--dry-run` and self-update are all preserved.
- Backend support for the bash agent: `GET /cert-agents/check?format=text` (line-based, no JSON to parse),
  a new `GET /cert-agents/bundle/raw?cert=&part=cert|key|chain|fullchain|combined` (raw PEM straight to
  `curl -o`, with an `X-Cert-Fingerprint` header), and `POST /report` also accepts TSV. The download route
  is now `agent.sh` and version/self-update compare against the `.sh`. The installer no longer installs
  python3-yaml.
- The install-instructions modal was reorganized (numbered steps + spacing); requirements now read
  "pure bash, only needs curl + coreutils".

## [0.4.145] — 2026-06-14

### Fixed / Changed
- Certificate / distribution-agent tables now set `:scroll-x` (matching the rest of the app): the name
  column no longer over-stretches and the action column is no longer pushed off-screen; narrow viewports
  scroll horizontally instead of clipping.
- Source-type selector: the **selected type is now a solid green filled button** (previously only a thin
  border, making the active choice hard to tell); "Off (manual upload)" shortened to **"Manual upload"**.

## [0.4.144] — 2026-06-14

### Changed
- Certificate / distribution-agent action-column buttons are now **left-aligned** (centering removed),
  matching every other list page in the app.

## [0.4.143] — 2026-06-14

### Fixed — a class of post-commit serialization 500s (found via flow review)
- `updated_at` has a SQL-side `onupdate=func.now()`, so it's expired after an UPDATE flush; several cert
  endpoints serialized the ORM object right after commit, triggering a sync lazy load → `MissingGreenlet`
  500. Added `session.refresh` after commit (matching other endpoints): `PATCH /certificates/{id}`,
  `PATCH /cert-agents/{id}`, `POST /cert-agents/{id}/rotate-key` (v0.4.142 already fixed
  `PUT /certificates/{id}/source`).

### Changed — generating a key now installs the public key on the host
- Since jt-ipam already has the SFTP login password, "Generate key" now **logs in with the password and
  appends the public key to `~/.ssh/authorized_keys`** (idempotent), so you don't have to paste it. On
  success it shows "installed"; with no password or on failure the key is still generated and the public
  key is shown for manual install with the reason (`POST /certificates/{id}/source/ssh-keypair` now takes
  the source config and returns installed/message).

## [0.4.142] — 2026-06-14

### Fixed
- **500 when saving an SFTP/URL source** (MissingGreenlet): `PUT /certificates/{id}/source` serialized
  the ORM object after commit, triggering a lazy load in a sync context. Now refreshes the object
  (`session.refresh`) after commit before serializing.

### Added — Source connection test + auto-generated SSH key
- Source config gains a **"Test connection"** button: it actually probes the URL / SFTP source using the
  current form values (blank password/key = reuse stored), returning a success message or a readable
  failure reason, without saving (`POST /certificates/{id}/source/test`).
- The SFTP login private key gains a **"Generate key"** button: jt-ipam generates an ed25519 keypair,
  stores the private key AES-GCM encrypted (never returned), and returns the **public key** to add to the
  SFTP host's `authorized_keys` (`POST /certificates/{id}/source/ssh-keypair`).

### Changed
- Certificate / distribution-agent action buttons are now **icon-only with hover tooltips** (matching the
  rest of the app), with tighter, centered columns — fixing the over-wide left gap, right overflow, and
  left-aligned icons.

## [0.4.141] — 2026-06-14

### Fixed / Changed
- The "update available" reload banner had its icon and text misaligned vertically — the icon is now
  centered in a 16×16 box, with `line-height:1` on the container and text.
- The certificate table's "Expiry" column is split into two independent columns: **"Expiry date"** and
  **"Days left"** (each sortable and pickable).
- Certificate / distribution-agent action-column icons are now centered (column `align:center` +
  NSpace `justify:center`).

## [0.4.140] — 2026-06-14

### Changed — Certificate auto-fetch source UX
- SFTP source config clarity: **"Login password" / "Login private key (SSH key, PEM)"** are now a
  distinct "SFTP login auth" section placed right under the username, with a hint: "Used to log in to
  the SFTP host. Provide a password OR an SSH private key (key takes precedence). The certificate's own
  private key is the remote key_path file below — unrelated to this." Remote file paths
  (cert_path/key_path/chain_path) are grouped separately. (The backend already supported SSH-key login;
  only the field placement/naming was easy to mistake for the certificate's private key.)
- The "Off" source type now reads **"Off (manual upload)"** so it's clear upload / paste / self-signed
  are still available.

### Changed — Certificate / distribution-agent tables match the rest of the app
- Both tables now have **sortable columns** (autoSort) and a **column picker** (preferences persisted to
  the backend and synced across devices).
- Action-column buttons now show **icon + text** and collapse to **icon-only** when the column is too
  narrow (col-actions container query; the label still shows on hover).

## [0.4.139] — 2026-06-14

### Added — Distribution-agent version display & self-update
- The admin "Distribution agents" tab now shows the agent **version** (flagged "update available"
  with a hint when it lags the server) and **source IP**, mirroring the scan agent.
- The distribution agent now **self-updates**: `/check` returns the sha256 of the server's agent.py;
  if the running copy differs the agent downloads the new version, atomically replaces itself and
  re-execs (the download is sha-verified before replacing; a failure is logged and never aborts
  deployment). Set `auto_update: false` in the config to disable.
- The read-only "Certificate distribution status" page (`GET /cert-agents/status`) now also returns
  `last_source_ip` / `server_agent_version`.

## [0.4.138] — 2026-06-13

### Added — Certificate auto-fetch source
- A certificate can now have an **auto-fetch source** (in addition to upload / paste / self-signed):
  the system periodically (and on demand via "Fetch now") pulls the renewed bundle from the source,
  and **only stores a new version if the content actually changed** — if the fingerprint matches the
  current version it is skipped (no-op). If the source provides no key, the current version's key is
  reused (common for renewals that keep the same key).
- Sources: **URL** (fetched via the SSRF-guarded safe_http client) and **SFTP** (asyncssh; the host
  is checked against the SSRF block-list). Credentials (SFTP password / private key) are AES-GCM
  encrypted (`encrypted_secret`) and never returned. New migration `0076`.
- Endpoints: `PUT /certificates/{id}/source`, `POST /certificates/{id}/fetch-now`; the sync timer
  auto-fetches each source-backed certificate on its own interval. Frontend: per-certificate source
  config (URL/SFTP) + "Fetch now", with last-fetch error surfaced.
- CIFS / NFS are out of scope for now (the backend runs non-root and can't mount); use a pre-mounted
  path or fetch via URL/SFTP.

## [0.4.137] — 2026-06-13

### Fixed
- **Certificate pages returned 405 / "server error" (regression in the cert API client)** — the
  `certificates.ts` API calls (and the subnet-overlap check in `integrations.ts`) were missing the
  `/api/v1` prefix that the shared axios client requires (its baseURL is `/`), so requests hit the
  SPA paths (`/certificates`, `/cert-agents`) and nginx returned 405 for POST / index.html for GET.
  All cert API paths are now correctly prefixed. The certificate admin page, agents, self-signed,
  and the Advanced status view work.
- Added the missing icon on the certificate/agent "Save" buttons.

## [0.4.136] — 2026-06-13

### Certificate distribution — UX
- The certificate version upload now supports **pasting PEM text** (certificate / key / chain) as
  an alternative to uploading files — a toggle in the upload dialog.
- Renamed the Advanced-menu read-only certificate view label to match the admin one.

## [0.4.135] — 2026-06-13

### Certificate distribution — follow-ups
- **Cross-distro agent installer** — the cert-agent installer now auto-detects the package
  manager (apt / dnf / yum / zypper), so it works on Debian 11/12/13, Ubuntu 22.04/24.04/26.04,
  RHEL / Rocky / AlmaLinux / CentOS, Fedora and openSUSE/SLES (all systemd). PyYAML is installed
  via the right package name per distro.
- **More profiles** — added `pbs` (Proxmox Backup Server: `proxy.pem`/`proxy.key`, reloads
  `proxmox-backup-proxy`). The `apache` profile now reloads `apache2` or `httpd` (whichever exists),
  so it works on Debian/Ubuntu and RHEL/SUSE.
- **Install-instructions button** on the Distribution Agents tab (like Scan Agents): one-liner
  install command, config example, supported distros, and the `--dry-run` hint.
- **Read-only certificate status under Advanced** — a non-admin viewer with global read can now
  see each agent's deployment status (last update, valid-from, expiry, days remaining, up-to-date
  vs drift) via a new Advanced menu entry. New `GET /cert-agents/status` (gated `require_global_read`).

## [0.4.134] — 2026-06-13

### Fixed
- **PGDG repo setup failed on Debian 12 when the keyring file already existed (customer report)** —
  the installer ran `gpg --dearmor` onto `/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg`
  (a file owned by the `postgresql-common` package). When that file already existed, gpg prompted
  "File exists. Overwrite?" / failed non-interactively, so the key was never written, the PGDG repo
  signature was invalid, and `postgresql-16-pgvector` was "not installable". Now the key is written
  to its own `/etc/apt/keyrings/jt-ipam-pgdg.gpg` with `gpg --dearmor --yes` (no collision, idempotent).
  Verified end-to-end in a Debian 12 container.

### Added — Certificate distribution (commercial certs → push to all sites)
- Central store for commercial certificates with a pull-based distribution agent. You upload a
  renewed bundle (crt/key/chain) once; agents on each host pick up the new version, write it to
  the right paths, run a config-test, reload the service, and roll back on failure.
- **Backend**: migration `0075` (`certificates` / `cert_versions` / `cert_agents`); the private
  key is stored AES-GCM encrypted and is never returned by any management API. `/certificates`
  admin CRUD + `POST /{id}/versions` (validates key↔cert match, SAN/expiry, rejects mismatched/
  expired/duplicate) + **`POST /{id}/self-signed`** (generate a self-signed cert with a custom
  CN/SAN/validity — handy while waiting for the commercial cert). `/cert-agents` admin CRUD +
  key rotate, plus the agent protocol (`X-Agent-Key`): `check` / `bundle` (decrypts the key,
  scope-limited, audited every time) / `report`.
- **Agent** (`agent/jt_ipam_cert_agent.py` + installer): pull model, built-in service profiles
  (nginx / apache / haproxy / pve / pmg / postfix / dovecot / zimbra / generic), atomic write +
  timestamped backup + config-test gate + rollback, idempotent, and **`--dry-run`**. Config is a
  small per-host YAML listing which certs deploy via which profile.
- **Monitoring**: daily expiry alerts and **drift detection** (an agent reporting a fingerprint
  other than the current version → that site didn't update) via the existing notification/bell.
- **Frontend**: a Certificates admin page (upload, self-signed, version/expiry status, agents +
  one-time key, scope).

## [0.4.133] — 2026-06-13

### Fixed
- **Install on minimal Debian 12 / 13 containers (customer report)** — two gaps surfaced on
  clean container images:
  - The PGDG-repo step runs `curl | gpg` and the later PostgreSQL setup uses `sudo -u postgres`,
    but `ca-certificates` / `curl` / `gnupg` / `sudo` were not guaranteed present (minimal Debian
    container images often omit them). The PGDG step (which Debian 12 always takes, since its
    default repo ships PG 15, not 16) failed at `curl`, and the PostgreSQL config step failed with
    `sudo: command not found`. These four are now installed up-front.
  - Combined with the v0.4.131 `apt-cache madison` version detection, the matrix is now: Debian 12
    → PGDG PostgreSQL 16; Debian 13 → native PostgreSQL 17 (+ `postgresql-17-pgvector`, no PGDG);
    Ubuntu 24.04 → native 16; Ubuntu 26.04 → native 17/18. The app supports PG 16/17/18.

## [0.4.132] — 2026-06-12

### Fixed
- **CSV import 500 on real import (customer report / issue #4)** — the import endpoint passed
  `subnet.cidr` (an asyncpg `IPv4Network` object, not a str) as the background task's VARCHAR
  `target_label` → asyncpg `DataError`. Dry-run was unaffected (no task spawned), which is why
  preview worked but the actual import 500'd. Now coerced with `str()`.
- **IP request list 500 when a request has a manually-specified IP (issue #4)** — asyncpg returns
  `IPv4Address` from the `INET` column, but `IPRequestRead.requested_ip` is typed `str`, so Pydantic
  validation failed and the whole list page 500'd. Added a `mode="before"` coercion (the same
  pattern already used for `IPAddressRead.ip` / `SubnetRead.cidr`).
- **Scan agent could not return hostnames (customer report)** — reports carrying rdns/NetBIOS/mDNS/OS
  hostnames 500'd for newly-discovered IPs. With `autoflush=False` and a DB-generated UUID, a freshly
  added `IPAddress` had `id=None` when `apply_observation` built the hostname-observation FK row →
  `NOT NULL` violation. Now flushes right after creating the IP so its id is populated. (icmp+arp-only
  reports were unaffected because they never call `apply_observation`.)
- **Hardened the same asyncpg INET/CIDR-as-str class of bug** across other read schemas that build via
  `model_validate(ORM)` and were missing coercion: `APITokenRead.last_used_ip`, `VMInterfaceRead`
  (`primary_ip`/`mac`), and `ARPEntryRead`/`FDBEntryRead` (`ip`/`mac`).

## [0.4.131] — 2026-06-12

### Fixed
- **Install on Ubuntu 26.04 (customer report)** — the installer hardcoded `postgresql-16`,
  which isn't in Ubuntu 26.04's default repos (it ships PG 17/18). The old fallback added the
  PGDG repo for the new release codename, which PGDG often doesn't carry until months after
  release → `apt-get update` 404'd and the install aborted. The installer now detects the
  PostgreSQL version already available in the enabled repos (prefers 16, otherwise the distro's
  native 17/18/…) and installs that plus the matching `postgresql-N-pgvector`; PGDG is only
  used as a last resort when no `postgresql-N` (>=16) exists at all. The app is compatible with
  PG 16/17/18. Python detection also now includes `python3.14` (Ubuntu 26.04's default).

### Fixed
- **ARP table retention** — `arp_entries` was insert/update only and never pruned, so it
  grew unbounded over time (MAC↔IP churn and orphaned rows from deleted devices each left a
  row). The sync timer now deletes ARP entries older than `ARP_RETENTION_DAYS` (default 30;
  set 0 to disable) once per run, including orphan rows.

### Added
- **Overlapping-subnet warning on integration settings** — when overlapping subnets exist
  (the same IP can appear in more than one subnet) and an integration (LibreNMS / OPNsense /
  Wazuh / Proxmox / AdGuard / DNS) has no subnet scope set, the settings form now shows a
  warning that a sync may stamp liveness / DHCP / MAC onto the wrong tenant's copy of an IP,
  pointing the admin to set the subnet scope. New `GET /subnets/overlaps/exists` (admin).

### Notes
- No new duplicate-IP / duplicate-ARP risk: `ip_addresses` is unique on `(subnet_id, ip)`;
  `arp_entries` is upserted on `(ip, mac, device_id)`; only LibreNMS writes ARP (scanner and
  OPNsense only stamp existing IPs). Same-IP-string across overlapping subnets remains by design.

## [0.4.129] — 2026-06-11

### Security
- **RBAC IDOR fixes** — several detail/aggregate endpoints accepted an object id without an
  object-level visibility check, letting any signed-in account read objects outside its scope:
  `GET /devices/{id}` and its sub-resources (`/integrations` exposed Wazuh CVE counts + Proxmox
  VMs, plus `/librenms`, `/vlans`, `/relations`), `GET /customers/{id}` and `/{id}/summary`
  (full per-customer asset dump), and `GET /racks/{id}/diagram`. All now require object `read`
  permission (404 on no access). The MCP `get_topology` tool no longer leaks the full topology
  to scoped accounts (was missing the `user` filter) and is gated as global-read; the REST
  `GET /topology` is gated with `require_global_read` to match.
- **OIDC ID Token verification** — the callback previously base64-decoded the ID Token and
  trusted its claims (including `groups`, which drives admin promotion) without verifying the
  signature. It now verifies the ID Token against the provider's JWKS (signature + `aud`/`iss`/
  `nonce`) before trusting any claim; on failure it falls back to userinfo only instead of
  trusting unverified groups.
- **CSV export formula injection** — IP address CSV export now escapes cells beginning with
  `= + - @` / tab / CR so spreadsheets don't execute them as formulas.

### Fixed
- **Integration sync resilience** — `jt-ipam-sync.py` now rolls back the session before writing
  `last_error` in every integration's exception handler; a single failing instance (e.g. an
  AdGuard `MultipleResultsFound` on overlapping subnets) no longer aborts the whole sync run.
- **Overlapping subnets** — AdGuard sync (`sync_clients` / `sync_rewrites`) and the MCP ARP
  lookup matched `IPAddress.ip` with `scalar_one_or_none()`; with overlapping subnets the same
  IP yields multiple rows → `MultipleResultsFound`. Changed to `limit(1)` + `first()`.
- **Non-UCS DNS server connection tests** — BIND 9 (dnspython `OSError`/connection-refused),
  Windows DNS (WinRM/`requests` exceptions), PowerDNS and OPNsense Unbound (non-JSON responses
  on auth failure) leaked raw exceptions that the `/dns/servers/{id}/test` endpoint didn't catch,
  producing a 500 with no message. Adapters now wrap these as `DNSAdapterError`, and the test
  endpoint has a safety net that turns any unexpected error into a readable 502.

### UI / Docs
- Fixed a missing i18n key on the section detail page ("display order" showed the raw key).
- Added error feedback to the notifications "mark all read" and group-members actions.
- Terminology: use 「外掛」 (not 「插件」) for "plugin" in zh-TW docs.

## [0.4.128] — 2026-06-10

### Fixed / Improved
- **External reverse proxy + OIDC / Microsoft 365 (Entra ID) login**: the frontend now parses
  the token the backend returns in the URL fragment after the OIDC/SAML callback (previously
  ignored → stuck on the login page); the backend merges **ID Token** claims into userinfo —
  Entra ID returns `groups` only in the ID Token (not the Graph userinfo endpoint), so admin-
  group mapping now matches. Added `deploy/nginx/jt-ipam-external-proxy.{conf,snippet}`
  templates (HTTP-only, no HSTS, `X-Forwarded-Proto` passthrough) and a README "Mode C —
  external reverse proxy" section (set `APP_PUBLIC_URL`/CORS to your domain, forward the proto).
- **Install (Ubuntu 24.04)**: `ensure_node` no longer pipes the NodeSource output to `/dev/null`
  and now **verifies Node ≥ 18** after install, otherwise it stops with a clear remedy — fixes a
  silent Node-install failure that left the frontend unbuilt while the run "looked" successful.
- **AI chat**: when Ollama is disabled / unreachable / misconfigured, a **friendly, actionable**
  error is shown (pointing to Admin → LLM / AI) instead of a cryptic string.
- **Circuits**: fixed the empty "associated device" dropdown when editing (device query exceeded
  the backend `page_size` cap); circuit table gains Device / Description columns and a localized
  Status column.
- **Tables (scan agents / device detail)**: tightened column widths so the actions column no
  longer overflows, empty columns no longer hog width, and MAC / timestamps no longer wrap.
- **NAT rules**: moved under the Advanced menu; clicking a row opens a read-only view (fields
  disabled), editing is via the pencil action.
- **Update banner**: a bordered + shadowed clickable box with an SVG icon (not an emoji) and
  clearer wording.
- **Per-table page size** now remembers the user preference (`user_preferences.page_size`).

## [0.4.114] — 2026-06-09

### Added / Improved
- **DNS records page**: filter by server / type (type dropdown shows per-type counts), a source
  column showing the originating DNS server, IP matching resolved against the **actual IP value**
  in `ip_addresses` (fixes "the IP is in IPAM but shows no match"), and a column picker. DNS sync
  now keeps only **A / AAAA / PTR** (IP↔name mapping) — CNAME/MX/TXT etc. are no longer stored.
- **IP addresses**: new `in_dhcp_lease` (migration 0074) auto-managed by the OPNsense DHCP-lease
  sync; phpIPAM import now labels `discovery_source='phpipam'` (was mislabeled "manual"); OPNsense
  DHCP/ARP sync scopes the IP lookup to the firewall's subnets + `limit(1)`, fixing
  `MultipleResultsFound` on overlapping subnets sharing an IP.
- **Global search**: matches **partial MAC prefixes** (e.g. `bc:24`); DNS-record hits open
  Advanced → DNS records with the name pre-filled.
- **Racks**: the merged single-card view can export **SVG / PNG / draw.io** (all racks side-by-
  side); draw.io device boxes are now square to match the on-screen diagram.
- **AI chat**: the zero-dependency Markdown renderer now supports **GFM tables**.
- **MCP**: new `list_dns_records` tool; AI answers about subnet usage call real data instead of
  generic CIDR arithmetic.
- **IP request approval emails** include a **clickable link** (routes through login then back to
  the approval page if not signed in).
- The IP change log renders `switch_port` as **device@port**.

## [0.4.113] — 2026-06-09

### Added — IP request approval gate + notifications
- **Configurable approval policy** (Admin → IP Request Approval) with four modes so
  each site can pick: `admin only`; `administrators + designated users/groups`
  (single gate, any one approves); **parallel sign-off** (multiple gates, any order,
  all must approve); and **sequential multi-stage** (ordered gates, each with its own
  approvers — must pass gate 1→2→3…). Plus a separation-of-duties self-approval
  toggle. Per-step approvals are tracked in a new `ip_request_stage_approvals` table
  (migration 0073). Approve/reject authorize via the policy, not a blanket admin check.
- The request detail page shows **gate progress** (which gates passed / which is
  awaiting); each sequential gate's approvers are notified only when it's their turn.
- **Inline approve / reject** on the IP Requests list for approvers (pending rows),
  in addition to the request detail page.
- Request detail: fully localized; shows the subnet CIDR (linked) and, for pending
  requests, the **IP that will be allocated** — including the auto-picked first-free
  IP — which the **approver can change** before approving.
- **Approver notifications**: when a request is submitted, every approver gets an
  in-app bell notification and (if the Email channel is enabled) an email.
- **Notification channels settings** (Admin → Notification Channels): an SMTP/email
  channel (host/port/TLS/credentials/from, encrypted password, test-send button).
  Telegram / Slack / Teams / Nextcloud / Zulip are shown as "in development".

### Added — DHCP
- Subnet detail shows a **DHCP ranges** row when OPNsense DHCP pool ranges exist for
  that subnet (hidden when none), and a **DHCP-only** filter on its IP list.

### Added — DNS records (Advanced → DNS Records)
- New page listing DNS records pulled from integrated DNS servers, with search, an
  **IP lookup** (find records matching an IP — forward A/AAAA or the IP's PTR), and a
  **"no matching IP"** filter (A/AAAA records whose target isn't in IPAM).

## [0.4.112] — 2026-06-09

### Fixed
- **Manually-edited MAC was not protected from sync overwrite.** Unlike hostname
  (which records a `manual` observation), editing an IP's MAC in the UI only set
  `ip.mac` without marking `mac_source="manual"`, so the next scan/ARP sync could
  clobber it. The IP-edit endpoint now stamps `mac_source="manual"` on manual MAC
  edits (highest ARP precedence) and clears the source when the MAC is cleared.
  (Hostname's manual-vs-precedence path was verified correct end-to-end; if a
  manually-set hostname seems to vanish, hard-refresh — it is usually a stale SPA
  bundle, not the backend.)
- IP Requests toolbar: the status filter select was `small` while the buttons next
  to it were default size, so it sat shorter — aligned to the same height.

## [0.4.111] — 2026-06-08

### Security (MCP per-object RBAC scoping)
- Several MCP/AI list tools returned data outside the caller's visible scope.
  `list_racks` / `list_locations` / `list_sections` / `list_customers` now filter
  rows by per-object visibility; `recent_ip_changes` is scoped to visible subnets;
  `get_customer_summary` denies non-visible customers; `stats_overview` scales its
  per-object counts to the caller's scope and omits global-infrastructure counts
  for users without global read. `dns_lookup` is now treated as global-infra.
- Added a regression test suite (`test_mcp_rbac_scope.py`) covering zero-visibility
  denial, partial-visibility blocking global-infra tools, row scoping, and scoped
  stats counts.

## [0.4.110] — 2026-06-08

### Fixed (create-admin CLI)
- `create-admin` crashed with `MultipleResultsFound` when the given username matched
  one account and the email matched a different one (or an email was shared by more
  than one account). The lookup now queries username and email separately and
  reports a clear conflict instead of crashing.
- `--force-update` now also writes the supplied username/email onto the matched
  account — previously it reset only the password, so a new `--email` was silently
  ignored and the old address kept showing.

## [0.4.109] — 2026-06-08

### Added (MCP / AI tools)
- **10 new MCP tools** for entities that had no AI coverage: `list_circuits`,
  `list_providers`, `list_asns`, `list_tenants`, `list_contacts`, `list_ssids`,
  `list_cables`, `cable_trace`, `list_power`, `list_wazuh_agents`.

### Changed (MCP field coverage caught up with recent feature growth)
- `list_subnet_ips` now returns `effective_status` (online/offline) + `os_family`.
- `list_nat` now resolves real source/destination IPs and adds interface, aliases,
  disabled/no_rdr, ip_version (was name/proto/ports only).
- `get_subnet_detail` adds scan_method, scan agent, VRF, parent subnet, archived.
- `get_device` adds customer, fqdn, location, description, power ports;
  `list_devices` adds customer + fqdn.
- `list_vms` adds tenant/primary_ip/device + network interfaces.
- `get_ip_detail` adds `effective_probes`; `list_customers` adds title/address;
  `stats_overview` now also counts VMs / circuits / providers / ASNs / tenants /
  contacts / cables.

### Security (MCP RBAC hardening)
- The MCP HTTP/stdio dispatch (`tools/call`) previously applied **no** visibility
  gate. Both the MCP protocol and the NL-chat path now share one `authorize_tool`
  gate: zero-visibility users are denied all data tools, global-infrastructure
  tools (VLAN/VRF/NAT/firewall/DNS/VM/VPN/circuits/cables/power/Wazuh…) require
  admin-or-wildcard read, and mutating tools require admin. `tools/list` and the
  LLM tool list are filtered to what the caller may actually call.

## [0.4.108] — 2026-06-07

### Fixed
- **Effective status stuck "offline" after a scan-agent reported the host alive.**
  The agent `/report` endpoint stamped `last_seen_scanner` but never recomputed
  `effective_status`, so 實際狀態 reflected the last LibreNMS recompute (which could
  be stale by days). It now flips the IP to `online (scanner)` / `online` immediately
  on a fresh agent sighting and logs the offline→online transition.

### Added
- **Installer auto-creates the first `admin` account with a random password** and
  prints it once at the end (also saved to `/etc/jt-ipam/.admin-initial-password`,
  root-only). README documents the `create-admin --force-update` password-reset CLI.
- **Scan-agent installer now installs optional probe tools** (`nmap`,
  `samba-common-bin`, `avahi-utils`) so OS / NetBIOS / mDNS probes work out of the
  box; skip with `JT_IPAM_SKIP_PROBE_TOOLS=1`.
- **"Install help" popover next to unavailable probes** (scan-agent page and the
  subnet edit dialog) showing the exact package/command to unlock the probe.

## [0.4.107] — 2026-06-07

### Added
- **Subnet scope for Wazuh / Proxmox VE / AdGuard / DNS integrations** (migration
  0072), mirroring LibreNMS: each integration can be limited to specific subnets so
  syncs only match IPs within those subnets — overlapping subnets from unrelated
  systems no longer mis-attach hostname / OS / etc. Empty scope = global matching.

### Changed
- Subnet edit: scan-probe checkboxes are disabled when the selected scan agent
  can't run that probe (consistent with the scan-agent page).
- NAT table: clicking a rule row opens its detail (ignores the IP / device links).
- Subnet list: the tree expand arrow now sits on the CIDR column, not the pin column.

### Fixed
- switch_port tooltip shows the `device@port` form (not `device / port`).

## [0.4.106] — 2026-06-07

### Added
- **OPNsense firewall association scope** (migration 0071): each firewall can be
  scoped by location / customer / explicit subnets / interface→subnet map. Synced
  NAT rules then resolve their IPs only within the firewall's scope, so multiple
  firewalls reusing the same RFC1918 subnet no longer cross-attach to the wrong
  jt-ipam IP. Unscoped firewalls keep the previous global IP-string matching.
- NAT page: hovering an IP that's linked to a jt-ipam IP shows its details
  (hostname / status / MAC / vendor / subnet / customer / device / switch port …),
  lazily loaded; clicking opens that IP's detail page.

### Changed
- New child subnets inherit the parent subnet's customer (unit); this is now
  self-healing in `rebuild_subnet_hierarchy` (cascades through levels).
- Sidebar subnet tree: child subnets render as real nested, expandable nodes with
  connector lines (instead of a "↳" prefix); the parent label still opens its detail.
- Sidebar version label enlarged.

### Fixed
- Light-theme tooltips containing links (e.g. table ellipsis tooltips) used the
  green link colour on a dark tooltip; links now inherit the tooltip's light text.
- Firewall scope form: the customer / unit select showed "no data" (options weren't
  loaded).

## [0.4.105] — 2026-06-07

### Fixed
- Subnet save returned "Invalid request": the edit form sends `master_subnet_id`
  but `SubnetUpdate` (a strict, extra-forbid schema) didn't declare it. Added the
  field so editing a subnet works again.
- Subnet list: clicking a row's tree expand arrow navigated into the subnet instead
  of expanding its children; the row-click now ignores the expand trigger.

### Changed
- **Unified subnet edit**: the subnet list and the subnet-detail page now share one
  `SubnetEditModal` component, so both edit the same fields (section / VLAN / VRF /
  parent subnet / per-probe scan options / scan agent …) — they previously diverged.
- Left sidebar: subnets are nested under their parent (by `master_subnet_id`) within
  each unit group, indented with a "↳" marker (still clickable to open).
- Responsive top bar restyle: language / theme / account are pill buttons with hover,
  dividers around the bell, vertically centered with the search box; dropdown carets
  removed to save width.
- Graylog guide: suggested Title / Description / Name (`jt_ipam_adapter` /
  `jt_ipam_cache` / `jt_ipam_table`); both HTTPS and plain-HTTP (8088) lookup URLs;
  Line Separator `\n`, Ignore characters `#`, Refresh interval 300s, Expire-after-
  access 300s, Default single/multi value empty; an IP-field-name box that rewrites
  the pipeline rule live with Graylog field-name validation; rule named
  `jt-ipam enrich <field> -> <field>_hostname`; pipeline `lookup_value()` uses the
  table name; examples use `src_ip_hostname`.

## [0.4.104] — 2026-06-07

### Fixed
- Graylog DSV lookup endpoint now emits each IP only once. The same IP can exist
  in multiple (overlapping) subnets, which produced duplicate rows and made
  Graylog's "DSV File from HTTP" data adapter fail with "Multiple entries with
  same key". Keys are now de-duplicated (first by IP order).

## [0.4.103] — 2026-06-07

### Changed
- Top bar is now responsive: on narrow screens the language / theme / account
  controls collapse to icon-only (icon-triggered dropdowns) and the search box
  shrinks, instead of wrapping onto multiple rows.
- New subnets inherit the **customer (unit)** of their containing parent subnet
  when none is specified, so a child subnet lands under the same sidebar group;
  the sidebar subnet tree refreshes immediately after create / edit / delete.
- IP edit dialog: the OS probe now shows the same "intrusive" tag + tooltip as the
  subnet / scan-agent settings.

## [0.4.102] — 2026-06-07

### Changed
- Anomaly detection (MAC roaming): the "seen at" location now resolves the switch
  to its friendly name (LibreNMS sysname / hostname) instead of a raw device UUID,
  and the device / port / last-seen fields are rendered as an aligned grid.

## [0.4.101] — 2026-06-06

### Changed
- Dashboard **Section heat** card redesigned: the bar now reflects *average subnet
  utilization* (no longer diluted to ~0% by a single large sparse subnet), plus a
  per-section distribution of subnets by utilization band (full / high / medium /
  low) and a subnet/used summary — the card is far more informative.

### Fixed
- Racks: the leftmost rack's frame left border was clipped by the horizontal-scroll
  container; added small side padding so it renders fully.
- OS source precedence section title wording.

## [0.4.100] — 2026-06-06

### Added
- **OS source precedence** (scan agent / LibreNMS / Wazuh): a drag-to-reorder list
  (under Name / ARP source precedence) that decides which source wins when several
  report an OS; the IP detail OS row shows the resolved source.
- Racks: the "merged single card" toggle is now a clear two-option switch (separate
  cards / merged card); the merged card gains a shared front/rear toggle and an
  export action (combined device list).

### Changed
- Audit forwarding settings relabelled from "Graylog" to the generic "log server"
  (GELF / syslog work with any collector).
- IP list OS column shows on one line (icon never shrinks; label truncates when
  space is tight); hostname column narrowed to give other columns room.
- Scan-agent table column widths tuned so "last seen" no longer wraps.
- switch_port tooltip always shows the full `device@port` text (plus the
  low-confidence note when applicable).

## [0.4.99] — 2026-06-06

### Changed
- MCP tools surface the new scan/OS fields: `get_ip_detail` returns OS guess /
  family / source and excluded probes; `list_scan_agents` returns enabled /
  available probes and last source IP.

## [0.4.98] — 2026-06-06

### Changed
- OS detection result now shown as an **"Operating system"** field in the IP detail
  table (top), and as an optional **OS** column in the subnet IP list.

## [0.4.97] — 2026-06-06

### Added
- Scan agents: a **"Scan now"** action that triggers the agent to run all enabled
  probes immediately on its next poll (migration 0070); OS detection (`nmap -O`)
  is now exercised end-to-end on agent hosts that have nmap (runs as root).
- Probe-interval inputs show a unit (seconds) + human-readable equivalent.

## [0.4.96] — 2026-06-06

### Changed
- Scan agent probe-interval inputs now show a unit suffix (seconds) and a
  human-readable equivalent (e.g. 86400 -> "1 day").

## [0.4.95] — 2026-06-06

### Changed
- Scan probes: removed the port-probing options (TCP port liveness, port/service
  scan) and SNMP — jt-ipam does not expose credential-based or port-scan probes.
  Remaining: ICMP / ARP / reverse DNS / NetBIOS / mDNS / OS detection.
- Scan Agents: show an **"update available"** tag when an agent's reported version
  is behind the server's bundled agent (it self-updates from the jt-ipam server,
  not GitHub; the tag surfaces agents that failed to self-update).
- Topology: VPN-paired firewalls are kept near each other instead of being pushed
  to opposite far ends when subnet centers are spread apart.

## [0.4.94] — 2026-06-06

### Added
- **Configurable scan probes** with a three-layer model (migration 0069):
  - **Probe catalog** (icmp / tcp / arp / rdns / netbios / mdns / os / ports)
    with per-probe class (light/heavy), default interval, and intrusiveness; default
    is **ICMP only**. Heavy probes (OS / port scan) run on their **own long interval**,
    never at the ICMP cadence.
  - **Scan agent**: pick which probes it may run + per-heavy-probe interval; the agent
    self-reports which probes it can actually perform (others greyed out).
  - **Subnet**: choose the probes to run (`scan_method`).
  - **IP address**: skip specific probes (the old "exclude from ping" generalised; icmp
    stays in sync). The IP detail page shows the **effective** probe set
    (subnet probes − IP skips ∩ agent capability).
- **OS detection display**: scan results are normalised into an OS family
  (Windows / Linux / macOS / BSD / network / printer / storage / hypervisor / …) and
  shown with a per-family SVG icon (IP list column + IP detail), tooltip = raw string.
- Agent poll/report protocol carries per-subnet probes, per-probe intervals, per-IP
  skip overrides, and richer results (rdns / os_guess / open_ports / probes_run); the
  bundled agent gains tcp/arp/rdns probes and fast/slow scheduling.

## [0.4.92] — 2026-06-06

### Added
- Advanced (tenancy / circuits / contacts) and Power pages: multi-table pages are
  split into **inner tabs** (matching the firewall rules/aliases style). Every
  Advanced / Power / Virtualization table now has a **unified toolbar**:
  filter + refresh + create + column picker + export.
- Racks: a **merged single-card** view mode (all racks of a room in one card).
- Topology: **persistent edge-selection highlight** with a two-end card (IP /
  device / port, shown even when only one end is known); multi-subnet centers
  spread apart so dual-homed devices no longer overlap.
- Circuits: **fixed-IP fields** (IP / gateway / netmask / DNS) + device link
  (migration 0067); built-in **circuit types** with add/delete management.
- Scan agents: show **last source IP** (migration 0068).
- Device detail: one-click **link-IP-mapping** button in the IP list.
- Graylog DSV settings promoted to a standalone **Graylog integration** page
  under Wazuh, with a wiring guide.

### Changed
- nginx API rate-limit raised (100 → 1200 r/m, burst 20 → 80) to stop spurious
  "connection failed" on API-heavy pages.
- IP-address editor: green save buttons; save/cancel returns to the IP detail page.

### Fixed
- **Stale-bundle navigation hang**: the router auto-reloads once when a code-split
  chunk fails to load, and the build now **retains hashed assets across deploys**
  (pruning ones older than 7 days), so tabs opened before a deploy no longer 404
  their chunks and hang on navigation.
- Contact-groups table reused tenant-group columns (hit the wrong API) — fixed.
- ruff: corrected noqa rule code in `audit.py`, import ordering in `sso.py`.

### Security / Chore
- **vitest** dev dependency bumped 3.2.6 → 4.1.8 (resolves the critical
  "Vitest UI server arbitrary file read/exec" advisory; dev-only, not in the
  production bundle).
- Bilingual docs added: `CHANGELOG_zh-TW.md`, `SECURITY_zh-TW.md`, and
  `TEST_CHECKLIST.md` (English) alongside `TEST_CHECKLIST_zh-TW.md`.
- All `scripts/*.sh` are now English-only (comments and messages); behavior
  unchanged.

## [0.4.79] — 2026-06-06

### Added
- **SSO web UI configuration**: OIDC and SAML are now DB-backed with an admin web
  UI (env defaults + DB override, AES-GCM encrypted secrets); LDAP management page.
- **Device power ports ↔ PDU outlets** modeling (NetBox PowerPort style,
  migration 0066).
- **Version auto-reload**: `dist/version.json` polling prompts a reload when a new
  build is deployed (the root cause behind "my save didn't take" stale-bundle
  reports).
- **Full bilingual documentation**: README and all `docs/*.md` in English and
  zh-TW; GitHub Pages feature-map tree.

### Changed
- Universal table **column picker + multi-format export** everywhere (incl.
  zero-dependency `.ods` / `.odt`, `.xlsx`, PDF).
- Generic pinning moved to backend preferences (migration 0065); rack front/rear
  face support.

## [0.4.61] — 2026-06-05

### Added
- **RBAC convergence for global infrastructure data**: `require_global_read` /
  `has_global_read` / `can_edit`. Lists, details, search, dashboard aggregates,
  counts and trends all scale to the user's visibility; action buttons grey out
  by capability.
- **Cable Trace** (NetBox-style multi-hop, migration 0063): a `device_ports`
  table with bridge → NIC → external-device traversal.
- Rack **half-U** support and front/rear visualization; device-detail rack
  diagram highlighting the current device.

### Changed
- AI / MCP: 100-question test-and-fix pass; tool list filtered by permission;
  a **change-confirm gate** before any write; cursor pagination + "next batch"
  continuation for large results.
- Archived subnets also hide their IPs (lists + search).

### Fixed
- AI chat / topology **RBAC leaks** closed (zero-permission accounts could
  previously query IPs/devices and view the topology).

## [0.4.43] — 2026-06-04

### Added
- **Device-to-device cabling / port** connection management; cabling and power
  resources gain full CRUD editing.
- LDAP management page; AI change-confirmation gate; Graylog DSV lookup
  (plain HTTP on port 8088).
- Dashboard charts; device detail shows its rack diagram.

### Changed
- Proxmox connection settings moved to the admin area; node network interfaces
  (bridge / bond / NIC) are pulled and made traceable.

### Fixed
- Hostname sync thrash (repeated re-sync); left half-U device save 500;
  audit `object_id` must be a UUID (`append_audit` needs `request_id`).

## [0.4.32] — 2026-06-02

### Added
- Device install direction (migration 0057): a device can be marked as mounted
  on the **rack front or rear**; the rack presentation-face field was removed.
- Version-info admin page: current version + Python and key backend package
  versions, with a button to check the latest version on GitHub.
- Locations list shows **rack count / device count** columns; subnets list shows
  a **pinned** column with one-click toggle.
- IP-address editor offers a one-click **link to a matching device** (mirror of
  the device→IP link button).
- Common rack width/depth **preset chips** in the rack form; the rack page
  auto-selects a pinned location on entry.
- Table export can fetch the **full dataset** (not just the visible page) on
  remote-paginated lists (Addresses / Audit / Users / Devices).
- GitHub Actions CI now actually runs and gates: frontend (eslint flat config /
  vue-tsc / vitest / build) and backend (alembic / pytest / bandit / gitleaks).
- Device ↔ IP linking: the device list resolves an effective management IP
  (primary_ip → LibreNMS mgmt IP → name-is-IP), renders it as a clickable link
  when a matching address object exists, and offers a one-click "link" button
  when a same-IP address object exists but isn't yet attached to the device.
  The IP-address editor can pick its device, and `/devices/{id}/relations`
  exposes the relation chain.
- Scan-agent auto-discovery: an agent push for an unknown IP inside one of its
  assigned, scan-enabled subnets auto-creates the address object (with its own
  descriptive note, not copied from phpIPAM); overlapping ranges are matched by
  longest-prefix within the agent's own subnets only.
- Per-source precedence is now split into independent cards: hostname,
  device-name, ARP/MAC, and a new **device model** precedence (manual is highest
  and cannot be disabled in each).
- Address search gains an **exact-match** toggle (IP / hostname must equal the
  query, so `192.168.1.1` no longer also matches `192.168.1.1xx`).
- Subnets may explicitly **allow overlap** (e.g. same CIDR under a different
  tenant / location) via `allow_overlap`.
- OPNsense alias sync: aliases are pulled into `opnsense_synced_aliases`.
- Dashboard: pinned locations / pinned racks cards; rack page can pick a device
  into an empty U-slot via a mini-rack picker.
- GitHub Pages site: project logo + favicon, inline SVG icons (no emoji),
  corrected positioning (not "built on phpIPAM").

### Changed
- Device naming from LibreNMS now prefers **sysName** over hostname (which is
  often just an IP); device-name precedence default reordered accordingly, and
  model is backfilled from LibreNMS hardware.
- DNS sync applies one deterministic hostname per IP (sorted), fixing name
  flapping when an IP has multiple A records.
- Hostname precedence now includes the Wazuh and AdGuard sources in the order
  list (they were observed but missing from the precedence UI).
- Floor-plan racks rotate to **any angle** (soft-snap to orthogonal), not just
  0/90/180/270; footprint scales by real width/depth.
- VPN tunnel pairing is labelled by method (migration 0058): WireGuard pubkey
  (reliable) vs IPsec endpoint-match (best-effort); IPsec matching also maps each
  firewall's own tunnel local endpoints to raise hit rate.
- Reworded taglines from "next-generation" to "self-hosted, integration-focused"
  across Pages / README / SPEC / app; Pages emphasizes OSS integration + phpIPAM
  import, adds accent colors, and splits Install / Upgrade / Uninstall.

### Security
- OPNsense config.xml parsed via **defusedxml** (XXE); subnet overlap/master SQL
  fully parameterized; bandit clean at medium+ severity.

### Fixed
- Subnet-pin persistence survived a refresh inconsistently — pins now persist
  synchronously on toggle instead of via a component-scoped watcher.
- GeoIP database download switched to the legacy `geoip_download` endpoint
  (the new permalink 302'd to S3 and rejected the forwarded auth header).
- Floor-plan upload 500 (uploads dir ownership); audit_logs doc table row
  broken by unescaped SQL `||` operators.

### Tests
- Added regression coverage: model precedence, subnet overlap, exact IP search,
  scan auto-discovery, hostname-source clearing, hostname-order completeness,
  device IP-matching flags, device/address relation chains, OPNsense alias
  parse+sync, LibreNMS device link, DNS pull naming, Wazuh/Proxmox sync,
  IPsec pairing, version endpoint, rack-face/location-counts, and a frontend
  usePinned unit test.

## [0.4.31] — 2026-06-01

### Added
- NAT/Circuit field expansion (migration 0053): NAT gains the full OPNsense
  rule set (disabled / no-RDR / IP-version / source·dest invert / port ranges /
  log / category / NAT-reflection / pool / filter-rule / alias references);
  Circuit gains up/down bandwidth. OPNsense sync populates them.
- Device detail: Wazuh agent + Proxmox VM panels (matched by IP); edit button.
- Tools: DNS/mail diagnostics (MX/SPF/DKIM/DMARC) + data-center power calculators.
- Rack diagram → draw.io-editable SVG export; room pinning; quick filter across
  list pages; self AI chat-history in the user menu; permissions overview.
- Topology: zoom/fit buttons, clickable legend toggles, default-to-pinned-subnets.

### Changed
- 機房 = 地點 (nav relabelled「機房 / 地點」); 站對站 VPN; NAT alias references are
  clickable to the Firewall page.
- VPN WireGuard pairing cross-fills each side's real WAN IP (was showing LAN).
- Floor plan: fixed-size handles, 0/90/180/270 rotation snap, toolbar below canvas.
- Global card header band + dark-mode table/card depth.

### Fixed
- Topology subnet filter dropped name-/ARP-derived devices.
- Many i18n/terminology/button-height fixes (協定, 配電盤/饋線/插座, Notifications…).

## [0.4.30] — 2026-06-01

### Added
- Table export (CSV / Markdown / PDF / ODS / ODT) on the admin tables: Users,
 Audit, DNS, LibreNMS, Wazuh (instances/agents/missing), Firewall
 (firewalls/mappings/rules), and Scan Agents.

### Changed
- The global **map provider** selector moved from the Locations page to
 **Settings → System** (admin-only). A non-admin `GET /system/map-provider`
 endpoint now lets the Locations map preview render for all users while the
 `PUT` stays admin-gated.

### Fixed
- Data-table column headers no longer wrap: a global rule keeps short CJK titles
 (e.g. 子網路) on one line regardless of the sort-arrow spacing.

## [0.4.x] — 2026-05/06

### Added
- **Object-level RBAC** across 7 object types (customer / section / subnet / IP /
 device / rack / location) with hierarchical cascade, per-type "All" wildcard,
 and 5 built-in roles (System Administrator, Read-only Viewer, Network
 Operator, Auditor, Department Administrator). Visibility is enforced on list
 endpoints, global search, the topology graph, and every selector.
- **Permission management UI** — principal (user/group) picker, grant table, and
 add-grant flow with "All"/specific multi-select and read/write/admin levels.
- **MCP server** — expanded toolset with both stdio and Streamable HTTP
 transports; mounted under `/api/mcp` so it is reachable through the nginx
 reverse proxy. Write tools self-gate on admin.
- **Customers** (managing units) attached to sections/subnets/devices/IPs, and an
 IEEE **OUI vendor** table with a monthly refresh timer.
- **AI chat** improvements — persistent history, per-message timestamps, model &
 elapsed-time display, and a model-parameters tooltip (family / parameter size /
 quantization / context length via Ollama `/api/show`).
- **Global search** now covers VPN, customers, racks, locations, NAT, DNS
 records, firewalls, and IP requests — all RBAC-filtered.
- Floating sticky horizontal scrollbar on wide tables; premium light/dark theme;
 Cabling / Power / VPN split into three independent pages.

### Changed
- prod database migrated from `SQL_ASCII` to `UTF8`.
- Terminology fixes for Taiwanese usage (e.g. 首碼 instead of 前綴).

### Fixed
- Numerous QA-driven UI fixes (column widths, dashboard widget styling, text
 selection contrast in light mode, topology node detail popovers, tooltip
 clipping).

## [0.3] — Phase 1–3 baseline

- phpIPAM parity (Sections/Subnets/IPs/VLANs/VRFs/NAT/Devices/Racks/Locations/
 IP-Requests), TOTP + API tokens, forced TLS.
- Multi-vendor DNS, deep LibreNMS integration, anomaly detection, SHA-256 audit
 chain, pgvector semantic search.
- Tenancy/Cabling/Power/VPN/Virtualization, Proxmox sync, Cytoscape topology,
 OIDC/SAML SSO, OPNsense firewall sync, Wazuh agent inventory.
