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
UBIOS_NAT_CHAIN="UBIOS_POSTROUTING_USER_HOOK"
VOIP_STATE_DIR="/var/run/voipd"

if [ -f "$CONF_FILE" ]; then
    . "$CONF_FILE"
    echo "[config] Read from $CONF_FILE"
else
    echo "[warn] $CONF_FILE not found — using built-in defaults"
fi

_ims_subnet="${VOIP_IMS_SUBNET:-10.0.0.0/8}"

echo "[config] WAN_INTERFACE=$VOIP_WAN_INTERFACE  VLAN=$VOIP_WAN_VLAN  VLAN_IFACE=$VOIP_WAN_VLAN_INTERFACE"
echo "[config] RT_TABLE=$VOIP_RT_TABLE  IMS_SUBNET=${_ims_subnet}"
echo ""

echo "[1/7] Stopping voipd service..."
systemctl stop voipd 2>/dev/null || true
systemctl disable voipd 2>/dev/null || true

echo "[2/7] Removing systemd service..."
rm -f "$VOIPD_SERVICE_LIB" "$VOIPD_SERVICE_ETC"
systemctl daemon-reload 2>/dev/null || true

echo "[3/7] Removing voipd binary..."
rm -f "$VOIPD_BIN"

echo "[4/7] Removing DHCP network config..."
rm -f "$VOIPD_NETWORK"
systemctl restart systemd-networkd 2>/dev/null || true

echo "[5/7] Removing VLAN interface $VOIP_WAN_VLAN_INTERFACE..."
if ip link show "$VOIP_WAN_VLAN_INTERFACE" >/dev/null 2>&1; then
    ip link set dev "$VOIP_WAN_VLAN_INTERFACE" down 2>/dev/null || true
    ip link delete dev "$VOIP_WAN_VLAN_INTERFACE" 2>/dev/null || true
    echo "  Removed $VOIP_WAN_VLAN_INTERFACE"
else
    echo "  $VOIP_WAN_VLAN_INTERFACE already gone"
fi

echo "[6/7] Removing iptables rules..."
while iptables -t nat -D "$UBIOS_NAT_CHAIN" \
    -o "$VOIP_WAN_VLAN_INTERFACE" -j MASQUERADE 2>/dev/null; do :; done
echo "  NAT MASQUERADE removed"
while iptables -t mangle -D PREROUTING \
    -d "$_ims_subnet" -j MARK --set-xmark 0x1e0000/0x7e0000 2>/dev/null; do :; done
while iptables -t mangle -D PREROUTING \
    -d "$_ims_subnet" -j CONNMARK --save-mark --nfmask 0x7e0000 2>/dev/null; do :; done
echo "  mangle MARK/CONNMARK removed"
for br in $(ip link show type bridge 2>/dev/null \
        | awk -F': ' '/^[0-9]/{print $2}' | awk '{print $1}'); do
    while iptables -D FORWARD -i "$br" -o "$VOIP_WAN_VLAN_INTERFACE" \
        -j ACCEPT 2>/dev/null; do :; done
done
while iptables -D FORWARD -i "$VOIP_WAN_VLAN_INTERFACE" \
    -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; do :; done
for sub in $(ip link show 2>/dev/null \
        | awk -F'[ :@]' "/@${VOIP_WAN_INTERFACE}[: ]/{print \$3}" \
        | grep -v "^${VOIP_WAN_VLAN_INTERFACE}$"); do
    [ -z "$sub" ] && continue
    while iptables -D FORWARD -i "$sub" \
        -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; do :; done
done
echo "  FORWARD rules removed"
while iptables -D UBIOS_LAN_IN_USER \
    -d "$_ims_subnet" -j ACCEPT 2>/dev/null; do :; done
echo "  UBIOS_LAN_IN_USER rule removed"

echo "[7/7] Removing policy routing..."
while ip rule del to "$_ims_subnet" priority 100 2>/dev/null; do :; done
_voip_ip=$(cat "$VOIP_STATE_DIR/voip_ip" 2>/dev/null || true)
[ -n "$_voip_ip" ] && ip rule del from "$_voip_ip" priority 100 2>/dev/null || true
ip route flush table "$VOIP_RT_TABLE" 2>/dev/null || true
sed -i "/^$VOIP_RT_TABLE /d" /etc/iproute2/rt_tables 2>/dev/null || true
echo "  PBR table $VOIP_RT_TABLE removed"
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
