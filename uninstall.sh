#!/bin/sh
# ============================================================
#  VoIP on UniFiOS — daemon uninstaller
#  Removes voipd service, VLAN interface, routing, iptables.
#  Does NOT touch voipd.conf (config preserved for reinstall).
#  Does NOT touch the web UI (use: ./voip uninstall-ui).
#  Safe to run multiple times.
# ============================================================

set -e

VOIPD_BIN="/usr/local/bin/voipd"
VOIPD_SERVICE_LIB="/lib/systemd/system/voipd.service"
VOIPD_SERVICE_ETC="/etc/systemd/system/voipd.service"
VOIPD_NETWORK="/etc/systemd/network/99-voip.network"
CONF_FILE="/data/voip/voipd.conf"

echo "=========================================================="
echo " voipd uninstaller"
echo "=========================================================="

VOIP_WAN_INTERFACE="eth4"
VOIP_WAN_VLAN="11"
VOIP_WAN_VLAN_INTERFACE="voip"
VOIP_RT_TABLE="203"
VOIP_IMS_SUBNET=""
ROUTING_MODE="b2bua_netns"
VOIP_B2BUA_LISTEN_PORT="5060"
UBIOS_NAT_CHAIN="UBIOS_POSTROUTING_USER_HOOK"
VOIP_STATE_DIR="/var/run/voipd"

if [ -f "$CONF_FILE" ]; then
    . "$CONF_FILE"
    echo "[config] Read from $CONF_FILE"
else
    echo "[warn] $CONF_FILE not found — using built-in defaults"
fi

_ims_subnet="${VOIP_IMS_SUBNET:-10.0.0.0/8}"
_fwmark=$(cat "$VOIP_STATE_DIR/fwmark" 2>/dev/null || echo "0x1e0000/0x7e0000")

echo "[config] WAN_INTERFACE=$VOIP_WAN_INTERFACE  VLAN=$VOIP_WAN_VLAN  VLAN_IFACE=$VOIP_WAN_VLAN_INTERFACE"
echo "[config] RT_TABLE=$VOIP_RT_TABLE  IMS_SUBNET=${_ims_subnet}  ROUTING_MODE=${ROUTING_MODE}"
echo ""

echo "[1/8] Stopping voipd service..."
systemctl stop voipd 2>/dev/null || true
systemctl disable voipd 2>/dev/null || true
# Stop B2BUA process if running independently
if [ -f "$VOIP_STATE_DIR/b2bua_pid" ]; then
    kill "$(cat $VOIP_STATE_DIR/b2bua_pid)" 2>/dev/null || true
    rm -f "$VOIP_STATE_DIR/b2bua_pid" "$VOIP_STATE_DIR/b2bua_status"
fi

echo "[2/8] Removing systemd service..."
rm -f "$VOIPD_SERVICE_LIB" "$VOIPD_SERVICE_ETC"
systemctl daemon-reload 2>/dev/null || true

echo "[3/8] Removing voipd binary and B2BUA script..."
rm -f "$VOIPD_BIN"
rm -f /data/voip/b2bua.py

echo "[4/8] Removing DHCP network config..."
rm -f "$VOIPD_NETWORK"
systemctl restart systemd-networkd 2>/dev/null || true

echo "[5/8] Removing VLAN interface $VOIP_WAN_VLAN_INTERFACE..."
if ip link show "$VOIP_WAN_VLAN_INTERFACE" >/dev/null 2>&1; then
    ip link set dev "$VOIP_WAN_VLAN_INTERFACE" down 2>/dev/null || true
    ip link delete dev "$VOIP_WAN_VLAN_INTERFACE" 2>/dev/null || true
    echo "  Removed $VOIP_WAN_VLAN_INTERFACE"
else
    echo "  $VOIP_WAN_VLAN_INTERFACE already gone"
fi

# ── B2BUA NETNS: tear down sandbox ──────────────────────────────────────────
if [ "$ROUTING_MODE" = "b2bua_netns" ]; then
    echo "[5b/8] Removing B2BUA network namespace and veth pair..."
    ip link del b2bua_v0 2>/dev/null || true
    ip netns del b2bua_ns 2>/dev/null || true
    echo "  Removed b2bua_v0 / b2bua_ns"
fi

echo "[6/8] Removing iptables rules..."

# NAT: MASQUERADE on voip interface (all modes)
while iptables -t nat -D "$UBIOS_NAT_CHAIN" \
    -o "$VOIP_WAN_VLAN_INTERFACE" -j MASQUERADE 2>/dev/null; do :; done
echo "  NAT MASQUERADE removed"

# Mangle MARK/CONNMARK (pbr + b2bua modes) — use saved fwmark, fall back to legacy
while iptables -t mangle -D PREROUTING \
    -d "$_ims_subnet" -j MARK --set-xmark "$_fwmark" 2>/dev/null; do :; done
while iptables -t mangle -D PREROUTING \
    -d "$_ims_subnet" -j CONNMARK --save-mark --nfmask 0x7e0000 2>/dev/null; do :; done
while iptables -t mangle -D PREROUTING \
    -m connmark --mark "$_fwmark" \
    -j CONNMARK --restore-mark --nfmask 0x7e0000 2>/dev/null; do :; done
# Legacy fallback if fwmark file was absent
while iptables -t mangle -D PREROUTING \
    -d "$_ims_subnet" -j MARK --set-xmark 0x1e0000/0x7e0000 2>/dev/null; do :; done
while iptables -t mangle -D PREROUTING \
    -m connmark --mark 0x1e0000/0x7e0000 \
    -j CONNMARK --restore-mark --nfmask 0x7e0000 2>/dev/null; do :; done
echo "  mangle MARK/CONNMARK removed"

# b2bua RETURN rules (b2bua mode — block direct LAN access to IMS SIP)
for _proto in udp tcp; do
    while iptables -t mangle -D PREROUTING \
        -d "$_ims_subnet" -p "$_proto" --dport 5060 -j RETURN 2>/dev/null; do :; done
done
echo "  mangle RETURN rules removed"

# ── B2BUA NETNS specific rules ───────────────────────────────────────────────
if [ "$ROUTING_MODE" = "b2bua_netns" ]; then
    # DNAT rules — remove all current and legacy variants
    for _i in 1 2 3; do
        iptables -t nat -D PREROUTING ! -i b2bua_v0 -p udp \
            --dport "$VOIP_B2BUA_LISTEN_PORT" -m addrtype --dst-type LOCAL \
            -j DNAT --to-destination 172.31.255.2 2>/dev/null || true
        iptables -t nat -D PREROUTING ! -i b2bua_v0 -p tcp \
            --dport "$VOIP_B2BUA_LISTEN_PORT" -m addrtype --dst-type LOCAL \
            -j DNAT --to-destination 172.31.255.2 2>/dev/null || true
        iptables -t nat -D PREROUTING -p udp --dport "$VOIP_B2BUA_LISTEN_PORT" \
            -m addrtype --dst-type LOCAL -j DNAT --to-destination 172.31.255.2 2>/dev/null || true
        iptables -t nat -D PREROUTING -p tcp --dport "$VOIP_B2BUA_LISTEN_PORT" \
            -m addrtype --dst-type LOCAL -j DNAT --to-destination 172.31.255.2 2>/dev/null || true
        iptables -t nat -D PREROUTING -i "$VOIP_WAN_VLAN_INTERFACE" -p udp \
            --dport "$VOIP_B2BUA_LISTEN_PORT" -j DNAT --to-destination 172.31.255.2 2>/dev/null || true
    done
    echo "  DNAT rules removed (b2bua_netns)"

    # Namespace MASQUERADE / SNAT rules
    while iptables -t nat -D "$UBIOS_NAT_CHAIN" \
        -s 172.31.255.2 ! -o "$VOIP_WAN_VLAN_INTERFACE" -j MASQUERADE 2>/dev/null; do :; done
    while iptables -t nat -D POSTROUTING \
        -s 172.31.255.2 -j MASQUERADE 2>/dev/null; do :; done
    while iptables -t nat -D POSTROUTING \
        -s 172.31.255.2 -o "$VOIP_WAN_VLAN_INTERFACE" \
        -j SNAT --to-source "" 2>/dev/null; do :; done
    echo "  Namespace MASQUERADE removed"

    # FORWARD accept rules for veth
    for _i in 1 2 3; do
        iptables -D FORWARD -i b2bua_v0 -j ACCEPT 2>/dev/null || true
        iptables -D FORWARD -o b2bua_v0 -j ACCEPT 2>/dev/null || true
        iptables -D FORWARD ! -i b2bua_v0 -d "$_ims_subnet" -p udp \
            --dport "$VOIP_B2BUA_LISTEN_PORT" -j DROP 2>/dev/null || true
        iptables -D FORWARD ! -i b2bua_v0 -d "$_ims_subnet" -p tcp \
            --dport "$VOIP_B2BUA_LISTEN_PORT" -j DROP 2>/dev/null || true
    done
    echo "  FORWARD rules removed (b2bua_netns)"

    # Zone firewall accept rule for veth address
    while iptables -D UBIOS_LAN_IN_USER \
        -d 172.31.255.2 -j ACCEPT 2>/dev/null; do :; done
    echo "  UBIOS_LAN_IN_USER veth rule removed"
fi

# UBIOS_LAN_IN_USER IMS subnet accept rule (all modes)
while iptables -D UBIOS_LAN_IN_USER \
    -d "$_ims_subnet" -j ACCEPT 2>/dev/null; do :; done
echo "  UBIOS_LAN_IN_USER IMS rule removed"

# Legacy raw FORWARD rules from older installs
for br in $(ip link show type bridge 2>/dev/null \
        | awk -F': ' '/^[0-9]/{print $2}' | awk '{print $1}'); do
    while iptables -D FORWARD -i "$br" -o "$VOIP_WAN_VLAN_INTERFACE" \
        -j ACCEPT 2>/dev/null; do :; done
done
while iptables -D FORWARD -i "$VOIP_WAN_VLAN_INTERFACE" \
    -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; do :; done
echo "  Legacy FORWARD rules removed"

echo "[7/8] Removing policy routing..."

# fwmark ip rule (pbr / b2bua modes) — dynamic + legacy static
ip rule del fwmark "$_fwmark" lookup "$VOIP_RT_TABLE" priority 101 2>/dev/null || true
ip rule del fwmark 0x1e0000/0x7e0000 lookup "$VOIP_RT_TABLE" priority 101 2>/dev/null || true

# Source-based ip rules (pbr / b2bua modes)
while ip rule del to "$_ims_subnet" priority 100 2>/dev/null; do :; done
_voip_ip=$(cat "$VOIP_STATE_DIR/voip_ip" 2>/dev/null || true)
[ -n "$_voip_ip" ] && ip rule del from "$_voip_ip" lookup "$VOIP_RT_TABLE" priority 100 2>/dev/null || true
[ -n "$_voip_ip" ] && ip rule del from "$_voip_ip" lookup "$VOIP_RT_TABLE" priority 99  2>/dev/null || true
[ -n "$_voip_ip" ] && ip rule del from "$_voip_ip" priority 100 2>/dev/null || true

# b2bua_netns ip rules
ip rule del iif b2bua_v0 lookup "$VOIP_RT_TABLE" priority 100 2>/dev/null || true
ip rule del from 172.31.255.2 to "$_ims_subnet" priority 100 2>/dev/null || true
ip rule del from 172.31.255.2 priority 100 2>/dev/null || true
[ -n "$_voip_ip" ] && ip rule del from "$_voip_ip" lookup "$VOIP_RT_TABLE" priority 99 2>/dev/null || true

# Routing table + rt_tables entry
ip route del 172.31.255.0/30 dev b2bua_v0 2>/dev/null || true
ip route flush table "$VOIP_RT_TABLE" 2>/dev/null || true
sed -i "/^$VOIP_RT_TABLE /d" /etc/iproute2/rt_tables 2>/dev/null || true
echo "  Voip routing table $VOIP_RT_TABLE removed"

# Main table IMS route (b2bua_netns LAN RTP route)
ip route del "$_ims_subnet" 2>/dev/null || true

echo "[8/8] Flushing conntrack and route cache..."
conntrack -D --orig-dst  "$_ims_subnet" 2>/dev/null || true
conntrack -D --reply-dst "$_ims_subnet" 2>/dev/null || true
ip route flush cache 2>/dev/null || true
rm -rf "$VOIP_STATE_DIR"

echo ""
echo "=========================================================="
echo " voipd fully removed."
echo ""
echo " Preserved (intentionally NOT deleted):"
echo "   /data/voip/voipd.conf   — your configuration"
echo "   /data/voip/ui/          — web UI files"
echo ""
echo " To also remove the web UI:"
echo "   cd /data/voip && ./voip uninstall-ui"
echo ""
echo " To remove everything:"
echo "   cd /data/voip && ./voip uninstall-ui && rm -rf /data/voip"
echo ""
echo " Note: rp_filter on WAN subinterfaces resets on next reboot."
echo "=========================================================="
