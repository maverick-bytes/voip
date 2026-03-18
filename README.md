# VoIP on UniFiOS

These scripts allow you to configure VoIP service for customers who have replaced their ISP router with a Unifi gateway based on UnifiOS (Cloud Gateways (UCG-XX), Gateways (UXG-XX), Dream routers (UDM-XX), etc...).

Since it is currently impossible to configure via the Unifi GUI a WAN interface with multiple VLANs, as someone would expect from such prosumer products to have, in contrast to 10$ devices and default isp ONTs.. this script creates a new interface and handles VoIP traffic routing to your local network.

Tested on **Unifi Cloud Gateway Fiber (UCG-Fiber)** with UnifiOS.

## Prerequisites

-  A UniFi gateway running UniFiOS (UCG-Fiber, UCG-Max, UDM-Pro, UXG-Pro, etc.)
-  SSH access to the gateway
-  Your ISP's VoIP delivered on a VLAN tag on the same WAN port as internet (common with FTTH IMS/SIP services)
-  A SIP softphone or ATA on your LAN (e.g. MicroSIP, Grandstream, Cisco SPA)

## Installation

As previously mentionned SSH access to the gateway is required.

On the gateway, download this repository into the `/data/voip` directory (the `/data` content is preserved during reboots and firmware upgrades):
```bash
cd /data
curl -sL https://github.com/maverick-bytes/voip/archive/refs/heads/main.tar.gz | tar -xvz
mv voip-main voip
cd voip
```

Make the scripts executable:
```bash
chmod +x voip voipd
```

Install by running:
```bash
cd /data/voip
./voip install
./voip install-ui
```

You can then open **`<gateway-ip>/voip`** in your browser to configure the VoIP service using the Web UI (recommended), or manually edit the configuration file:
```bash
vi /data/voip/voipd.conf
```

A full example configuration with all available options is available in `voipd.conf.example`.


### Routing modes

| Mode | Description |
|------|-------------|
| `b2bua` | **Default (recommended).** A signaling-only SIP Back-to-Back User Agent runs on the gateway. LAN clients register to the gateway with a local SIP account instead of directly to the ISP's IMS network. RTP flows peer-to-peer — no media is relayed and no latency is added. |
| `pbr` | Policy-Based Routing. Creates a dedicated routing table (table 203) and ip rules so IMS traffic always exits via the voip interface, regardless of UniFiOS default WAN priority. LAN clients register directly to the ISP's SIP proxy. |
| `forward` | Routes IMS traffic through one or more existing VLAN interfaces (`VOIP_FORWARD_INTERFACE`). Use if you want to steer traffic through a different port or pre-existing VLAN. |

### Configuration guide

**Common settings (all modes)**

* `VOIP_WAN_INTERFACE`: Physical WAN port the VoIP VLAN is attached to
  - `eth4` or `eth6` on UCG-Fiber
  - `eth9` on some UDM models

* `VOIP_WAN_VLAN`: VLAN ID your ISP uses for VoIP traffic (verify with your ISP or ONT)

* `PCSCF_HOSTNAME`: Your ISP's SIP proxy hostname — resolved at startup using DNS from the VoIP DHCP lease

**B2BUA mode settings** (`ROUTING_MODE="b2bua"`)

* `VOIP_B2BUA_USER` / `VOIP_B2BUA_PASS` / `VOIP_B2BUA_DOMAIN`: ISP SIP credentials the B2BUA uses to register upstream to the IMS network

* `VOIP_B2BUA_LISTEN_PORT`: Local SIP port (UDP + TCP) that LAN clients register to — default `5060`

* `VOIP_B2BUA_LOCAL_USER` / `VOIP_B2BUA_LOCAL_PASS`: Optional credentials to require from LAN clients when registering. Leave empty to allow any LAN client without authentication.

* `VOIP_B2BUA_REG_EXPIRES`: REGISTER refresh interval in seconds — default `600`

In B2BUA mode, configure your SIP softphone or ATA as follows:
- **SIP Server / Registrar**: your gateway IP address (shown in the web UI Status page)
- **SIP Username**: your local username (or your ISP number if no local auth is set)
- **SIP Password**: your local password (or your ISP password if no local auth is set)
- **Transport**: UDP or TCP

**PBR mode settings** (`ROUTING_MODE="pbr"`)

In PBR mode, configure your SIP softphone or ATA as follows:
- **SIP Server / Registrar**: the P-CSCF IP shown in the status banner / web UI
- **SIP Username / Password**: your ISP-provided credentials
- **Transport**: UDP/TCP/TLS

**Forward mode settings** (`ROUTING_MODE="forward"`)

* `VOIP_FORWARD_INTERFACE`: The VLAN interface(s) to route IMS traffic through
  - `br0` = default LAN bridge
  - `br102` = VLAN 102 bridge
  - Multiple interfaces: `"br0 br102"`

**VPN / WireGuard support (optional)**

* `VOIP_VPN_INTERFACES`: Space-separated WireGuard interface names to allow VoIP traffic from. Enables SIP/RTP over Teleport or the built-in WireGuard VPN server. **Disabled by default** — opt-in only.
  - Find your WireGuard interface: `ip link show type wireguard`
  - UniFi naming convention: `wgsrv1` (VPN Server), `wgcli1` (VPN Client), `wgs2s1` (Site-to-Site)
  - Example: `VOIP_VPN_INTERFACES="wgsrv1"`
  - Can also be configured from the web UI under **Config → VPN Support**


### IMS subnet detection

The script auto-detects the IMS subnet from the DHCP-assigned IP on the voip interface:

| First octet | Subnet used |
|-------------|-------------|
| `10.x.x.x` | `10.0.0.0/8` |
| `100.x.x.x` | `100.64.0.0/10` |
| `172.x.x.x` | `172.16.0.0/12` |
| `192.x.x.x` | `192.168.0.0/16` |
| Other | `10.0.0.0/8` (fallback) |

To override: set `VOIP_IMS_SUBNET="x.x.x.x/y"` in `voipd.conf`.

## Web UI

After running `./voip install-ui`, the web interface is accessible at `https://<gateway-ip>/voip`.

It provides:
- **Status page**: service state, VoIP IP, gateway, routing mode, IMS subnet, and the SIP registrar address to use in your client
- **Config page**: all settings including routing mode, B2BUA credentials, VPN support — save triggers an automatic service restart
- **Logs page**: live journalctl output with colour-coded log levels
- **Commands page**: install, reinstall, update and uninstall actions with full output returned in the browser

## Verification

Check the service is running:

```sh
systemctl status voipd
```

View logs and confirm the SIP address:

```sh
journalctl -u voipd -f
```

The status banner shows everything you need. In B2BUA mode:

```
==========================================================
 VoIP -- Running
==========================================================
  Interface : voip (VLAN 11 on eth4)
  VoIP IP   : 10.x.x.x
  Gateway   : 10.x.x.1
  Routing   : b2bua (signaling-only SIP proxy)
  IMS subnet: 10.0.0.0/8
  NAT       : MASQUERADE in UBIOS_POSTROUTING_USER_HOOK
----------------------------------------------------------
  SIP Proxy : 10.x.x.x  (upstream, used by B2BUA)
  SIP Client: register to 192.168.x.1:5060 (local B2BUA)
==========================================================
```

In PBR mode:

```
==========================================================
 VoIP -- Running
==========================================================
  Interface : voip (VLAN 11 on eth4)
  VoIP IP   : 10.x.x.x
  Gateway   : 10.x.x.1
  Routing   : PBR -> table 203 (voip)
  IMS subnet: 10.0.0.0/8
  NAT       : MASQUERADE in UBIOS_POSTROUTING_USER_HOOK
----------------------------------------------------------
  SIP Proxy : 10.x.x.x  <-- use this in your SIP client / ATA
==========================================================
```

Verify routing is correct (PBR and B2BUA modes):

```sh
# Routing table
ip route show table 203

# Policy rules (two entries at priority 100)
ip rule show | grep "100:"

# Confirm IMS traffic exits via voip
ip route get <your-ISP-SIP-proxy-IP>
```


## Persistence across reboots and firmware upgrades

The `/data` directory persists across reboots and firmware upgrades on UniFiOS. The systemd service file in `/etc/systemd/system/` does **not** persist after a firmware upgrade. Reinstall it with:

```bash
cd /data/voip
./voip install
./voip install-ui
```

## Updating

```bash
cd /data/voip
./voip update
```

This downloads the latest release, replaces all scripts (including `b2bua.py`), and restarts the service. Your `voipd.conf` is preserved.

## Uninstallation

```bash
cd /data/voip
./voip uninstall
chmod +x uninstall.sh && ./uninstall.sh
./voip uninstall-ui
rm -rf /data/voip
```

## Troubleshooting

### VoIP interface has no IP

```sh
ip addr show voip
journalctl -u systemd-networkd -n 30
```

The VLAN ID or WAN interface name may be wrong for your device model.

### SIP proxy not resolved

The script reads the DNS server from the DHCP lease file at `/run/systemd/netif/leases/<index>`. Verify:

```sh
IFIDX=$(ip link show voip | awk -F': ' 'NR==1{print $1}')
cat /run/systemd/netif/leases/$IFIDX
```

You should see a `DNS=` line. Then test manually:

```sh
DNS=$(grep "^DNS=" /run/systemd/netif/leases/$IFIDX | cut -d= -f2 | awk '{print $1}')
nslookup <your-pcscf-hostname> $DNS
```

### B2BUA not registering upstream

Check the B2BUA log:

```sh
cat /var/run/voipd/b2bua.log
```

Common causes:
- `VOIP_B2BUA_USER`, `VOIP_B2BUA_PASS` or `VOIP_B2BUA_DOMAIN` not set in `voipd.conf`
- P-CSCF hostname not resolved — check the DNS troubleshooting step above
- ISP credentials incorrect — verify with your ISP

Enable debug logging by setting `VOIP_DEBUG="true"` in `voipd.conf` and restarting the service.

### Registration times out (PBR mode)

Monitor SIP traffic on both interfaces simultaneously:

```sh
# Terminal 1 — traffic leaving to ISP
tcpdump -i voip -n port 5060

# Terminal 2 — traffic reaching your LAN
tcpdump -i br0 -n port 5060
```

If packets appear on `voip` but replies don't appear on your LAN bridge, check conntrack:

```sh
conntrack -L 2>/dev/null | grep <SIP-proxy-IP>
```

The reply direction `dst` should be your voip IP, not your internet IP.

### Calls fail or one-way audio

Do **not** load `nf_nat_sip` or `nf_conntrack_sip`. These modules rewrite SDP in-flight and corrupt SRTP key material. Modern IMS networks use symmetric RTP and do not need SIP ALG.

```sh
# Confirm these are NOT loaded
lsmod | grep sip
# Expected: no output
```

### SIP does not work over VPN (Teleport / WireGuard)

By default VPN clients cannot reach the VoIP network. Set `VOIP_VPN_INTERFACES` to your WireGuard interface name:

```sh
ip link show type wireguard
```

Then either set it in `voipd.conf`:
```sh
VOIP_VPN_INTERFACES="wgsrv1"
```

Or enable it from the web UI under **Config → VPN Support**, tick the interface, and save.


---

## FAQ

**Q: What VLAN ID should I use?**
A: See the local VLAN ID in your area — the default in this script is VLAN 11. You can verify by checking your ISP ONT web-ui WAN page or using OMCI commands via telnet from your aftermarket ONT unit.

**Q: What is the difference between B2BUA and PBR mode?**
A: In B2BUA mode, your LAN clients register to the gateway (e.g. `192.168.1.1:5060`) using a local account, and the gateway handles the upstream IMS registration on their behalf. Your ISP credentials never leave the router. In PBR mode, clients register directly to the ISP's SIP proxy — the gateway only handles the routing so packets take the right path.

**Q: Does B2BUA add latency?**
A: No. The B2BUA only processes SIP signaling (REGISTER, INVITE, BYE, etc.). RTP media flows directly between your SIP client and the ISP's media servers — the gateway is not in the media path at all.

**Q: Can multiple SIP clients register at the same time in B2BUA mode?**
A: Yes. Multiple LAN clients can register simultaneously. Inbound calls are delivered to the first registered client. Outbound calls from any registered client are forwarded upstream.

**Q: Does this support IPv6?**
A: Currently, this script only configures IPv4. IPv6 support can be added if needed.

**Q: Will this survive a reboot?**
A: Yes, the systemd service is enabled to start automatically on boot.

**Q: Will this survive a firmware upgrade?**
A: The `/data/voip` directory and your `voipd.conf` survive firmware upgrades. The systemd service registration does not — run `./voip install && ./voip install-ui` after a firmware upgrade to restore it.

**Q: Can I use VoIP over VPN (Teleport / WireGuard)?**
A: Yes. Set `VOIP_VPN_INTERFACES` to your WireGuard interface name (e.g. `"wgsrv1"`) in `voipd.conf`, or enable it from the web UI under **Config → VPN Support**. It is disabled by default. Works with all three routing modes.

## Want to help out and buy me a coffee ?

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/H2H31UPAFR)

## License

GNU General Public License v2.0 — see [LICENSE](LICENSE).
