#!/usr/bin/env python3
"""
VoIP Web UI — API backend for UniFiOS
Runs on 127.0.0.1:8099, accessed only via nginx reverse proxy at /voip
"""

import http.server
import json
import re
import subprocess
import threading
import urllib.parse
from pathlib import Path

PORT = 8099
BIND = "127.0.0.1"
BASE_DIR  = Path("/data/voip")
UI_DIR    = BASE_DIR / "ui"
DIST_DIR  = UI_DIR / "dist"
CONF_FILE = BASE_DIR / "voipd.conf"
STATE_DIR = Path("/var/run/voipd")
TOKEN_FILE = UI_DIR / ".internal-token"

def _internal_token():
    """Read the shared secret nginx injects as X-Voip-Token."""
    try:
        return TOKEN_FILE.read_text().strip()
    except Exception:
        return None

CONF_KEYS = [
    "VOIP_WAN_INTERFACE", "VOIP_WAN_VLAN", "VOIP_WAN_VLAN_INTERFACE",
    "VOIP_WAN_VLAN_INTERFACE_EGRESS_QOS", "PCSCF_HOSTNAME", "ROUTING_MODE",
    "VOIP_RT_TABLE", "VOIP_RT_TABLE_NAME", "VOIP_FORWARD_INTERFACE",
    "VOIP_FORWARD_GATEWAY", "VOIP_IMS_SUBNET", "VOIP_VPN_INTERFACES", "VOIP_DEBUG",
]

# ── Config ────────────────────────────────────────────────────────────────────

def read_conf():
    conf = {}
    if not CONF_FILE.exists():
        return conf
    for line in CONF_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key in CONF_KEYS:
            conf[key] = val
    return conf

def write_conf(data: dict):
    # Refuse to overwrite with an empty payload — guards against accidental wipes
    meaningful = {k: v for k, v in data.items() if k in CONF_KEYS and v != ""}
    if not meaningful:
        return
    lines = ["# /data/voip/voipd.conf — managed by voip-ui",
             "# Edit here or via the web UI at https://<gateway>/voip", ""]
    for key in CONF_KEYS:
        if key in data:
            lines.append(f'{key}="{data[key]}"')
    CONF_FILE.write_text("\n".join(lines) + "\n")

def read_state(name):
    f = STATE_DIR / name
    return f.read_text().strip() if f.exists() else ""

# ── Shell helpers ─────────────────────────────────────────────────────────────

def sh(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""

def sh_lines(cmd, timeout=15):
    return sh(cmd, timeout).splitlines()

# ── API: status ───────────────────────────────────────────────────────────────

def api_status():
    running = sh("systemctl is-active voipd") == "active"
    uptime_raw = sh(
        "systemctl show voipd --property=ActiveEnterTimestamp --value")
    uptime = ""
    if uptime_raw and uptime_raw != "n/a":
        try:
            import datetime
            from dateutil import parser as dp
            t = dp.parse(uptime_raw)
            delta = (datetime.datetime.now(datetime.timezone.utc)
                     - t.astimezone(datetime.timezone.utc))
            h, rem = divmod(int(delta.total_seconds()), 3600)
            uptime = f"{h}h {rem//60}m" if h else f"{rem//60}m"
        except Exception:
            uptime = uptime_raw
    conf = read_conf()
    return {
        "serviceRunning": running,
        "interface":    read_state("voip_ip") and conf.get(
                            "VOIP_WAN_VLAN_INTERFACE", "voip"),
        "vlan":         conf.get("VOIP_WAN_VLAN", ""),
        "wanInterface": conf.get("VOIP_WAN_INTERFACE", ""),
        "voipIp":       read_state("voip_ip"),
        "gateway":      read_state("gw"),
        "routingMode":  conf.get("ROUTING_MODE", "pbr").upper(),
        "routingTable": conf.get("VOIP_RT_TABLE", ""),
        "imsSubnet":    read_state("subnet"),
        "natRule":      "MASQUERADE in UBIOS_POSTROUTING_USER_HOOK",
        "sipProxy":     read_state("pcscf_ip"),
        "uptime":       uptime,
    }

# ── API: logs ─────────────────────────────────────────────────────────────────

def api_logs(lines=150):
    raw = sh(f"journalctl -u voipd -n {lines} "
             "--no-pager --output=short-iso 2>/dev/null")
    entries = []
    for line in raw.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            entries.append({"time": "", "level": "info", "msg": line})
            continue
        time_part = parts[0][:19].replace("T", " ")
        msg = re.sub(r'^voipd\[\d+\]: ', '', parts[4])
        level = ("error"   if "ERROR"   in msg or "FATAL"   in msg else
                 "warning" if "WARNING" in msg or "watchdog" in msg else
                 "debug"   if "[DEBUG]" in msg else
                 "success" if ("started successfully" in msg
                               or "VoIP service started" in msg)
                           else "info")
        entries.append({"time": time_part, "level": level, "msg": msg})
    return entries

# ── UniFi config reader (cached) ──────────────────────────────────────────────

_udapi_cache = None
_udapi_cache_mtime = None

def _udapi():
    """
    Load udapi-net-cfg.json (or its hashed variant) and cache it.
    UniFi OS sometimes writes the file as udapi-net-cfg-<hash>.json.
    We load all matching files and merge them so nothing is missed.
    Cache is invalidated automatically when the file changes on disk
    so stale interface data after a UniFi network reconfiguration is
    returned at most until the next config page load.
    """
    global _udapi_cache, _udapi_cache_mtime
    import glob as _glob, os as _os
    patterns = [
        "/data/udapi-config/udapi-net-cfg*.json",
        "/mnt/data/udapi-config/udapi-net-cfg*.json",
    ]
    latest_mtime = 0
    for pattern in patterns:
        for p in sorted(_glob.glob(pattern)):
            try:
                latest_mtime = max(latest_mtime, _os.path.getmtime(p))
            except Exception:
                pass
    if _udapi_cache is not None and latest_mtime == _udapi_cache_mtime:
        return _udapi_cache
    if _udapi_cache is None:
        pass  # fall through to reload below
    _udapi_cache = {}
    for pattern in patterns:
        for p in sorted(_glob.glob(pattern)):
            try:
                data = json.loads(Path(p).read_text())
                # Merge: later files override earlier ones for top-level keys
                _udapi_cache.update(data)
            except Exception:
                pass
    _udapi_cache_mtime = latest_mtime
    return _udapi_cache


def _pppoe_parents():
    """
    Return the set of ethN physical interfaces that have a PPPoE session.
    Reads /proc/net/pppoe which lists the underlying ethernet device
    (e.g. eth4.12) for each active session, then walks up to the base ethN.
    This is more reliable than ip-link @parent parsing since ppp0 has no @.
    """
    parents = set()
    try:
        for line in Path("/proc/net/pppoe").read_text().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3:
                dev = parts[2].strip()           # e.g. "eth4.12"
                base = re.match(r'^(eth\d+)', dev)
                if base:
                    parents.add(base.group(1))   # eth4
    except Exception:
        pass
    return parents


def _wan_ports_from_config():
    """
    Return the set of ethN names that are WAN uplinks.
    Sources (combined):
    1. firewall/filter WAN_LOCAL/WAN_IN rules — direct eth or ppp ifaces
    2. /proc/net/pppoe — maps PPPoE sessions to their physical ethN parent
       (more reliable than ip-link @parent since ppp0 has no @ in ip-link)
    """
    data = _udapi()
    wan = set()

    for chain in data.get("firewall/filter", []):
        for rule in chain.get("rules", []):
            target = rule.get("target", "")
            if "WAN_LOCAL" not in target and "WAN_IN" not in target:
                continue
            iface_id = rule.get("inInterface", {}).get("id", "")
            if re.match(r'^eth\d+$', iface_id):
                wan.add(iface_id)
            elif re.match(r'^ppp\d+', iface_id):
                p = sh(f"ip -o link show dev {iface_id} 2>/dev/null "
                       f"| grep -oP '(?<=@)\\S+(?=:)'")
                while p:
                    base = re.match(r'^(eth\d+)', p)
                    if base:
                        wan.add(base.group(1))
                        break
                    p = sh(f"ip -o link show dev {p} 2>/dev/null "
                           f"| grep -oP '(?<=@)\\S+(?=:)'")

    wan.update(_pppoe_parents())
    return wan

def _vpn_names():
    """
    Build a map of WireGuard interface name -> UniFi VPN server name.
    UniFi names VPN server interfaces wgsrvN where N is the server id.
    e.g. wgsrv1 -> vpn/wireguard/servers[id=1].name -> "One-Click VPN"
    Also handles wgcliN (VPN clients) and wgs2sN (site-to-site) interfaces.
    """
    names = {}
    data = _udapi()
    for server in data.get("vpn/wireguard/servers", []):
        sid  = str(server.get("id", "")).strip()
        name = server.get("name", "")
        if sid and name:
            names[f"wgsrv{sid}"] = name
    for client in data.get("vpn/wireguard/clients", []):
        cid  = str(client.get("id", "")).strip()
        name = client.get("name", "")
        if cid and name:
            names[f"wgcli{cid}"] = name
    for s2s in data.get("vpn/wireguard/site-to-sites", []):
        sid  = str(s2s.get("id", "")).strip()
        name = s2s.get("name", "")
        if sid and name:
            names[f"wgs2s{sid}"] = name
    return names


# --- API: interfaces ----------------------------------------------------------
#
# UCG-Fiber ip link topology:
#   eth0-3@switch0  — LAN switch fabric ports (never WAN)
#   eth4@switch0    — WAN port 5 on the switch fabric (WAN1 by default)
#   eth5            — SFP+1, may be enslaved to br0 if used as LAN uplink
#   eth6            — SFP+2, WAN2 slot (no master when not bridged)
#   ethN.VID@ethN   — VLAN sub-interfaces (any device) — excluded
#   br0, br10…      — one bridge per UniFi network
#   bond0           — internal switch bond — excluded
#
# WAN detection:
#   Standalone SFPs (no @switch0, no master brX) → WAN
#   Switch-fabric ports (@switch0) → WAN only if confirmed by firewall rules
#   or PPPoE parent chain in udapi-net-cfg.json.

def api_interfaces():
    wan, lan = [], []
    confirmed_wan = _wan_ports_from_config()  # set of ethN names from UniFi config

    for line in sh("ip -o link show").splitlines():
        m = re.match(r'^\d+:\s+(\S+?)(@(\S+?))?:\s+<([^>]*)>', line)
        if not m:
            continue
        name   = m.group(1)
        parent = m.group(3) or ""
        flags  = m.group(4)

        master_m = re.search(r'\bmaster\s+(\S+)', line)
        master   = master_m.group(1) if master_m else ""

        # ── Skip ──────────────────────────────────────────────────────────
        if name in ("lo", "sit0", "ip6tnl0", "tunl0", "gre0",
                    "ip_vti0", "switch0"):
            continue
        if re.match(r'^bond', name):          # UniFi internal switch bond
            continue
        if re.match(r'^eth\d+\.\d+', name):  # VLAN sub-ifaces
            continue

        has_carrier = "LOWER_UP" in flags

        # ── WAN candidates ─────────────────────────────────────────────────
        if re.match(r'^eth\d+$', name):
            if parent == "switch0":
                # Switch-fabric port: only WAN if confirmed by config
                if name not in confirmed_wan:
                    continue
            elif re.match(r'^br', master):
                # Standalone SFP enslaved to a bridge → LAN, skip
                continue
            # else: standalone SFP with no master → WAN candidate

            wan.append({
                "name":        name,
                "type":        "wan",
                "description": _wan_desc(name, has_carrier, confirmed_wan),
                "status":      "up" if has_carrier else "down",
            })

        # ── LAN: bridge interfaces ─────────────────────────────────────────
        elif re.match(r'^br\d+', name):
            lan.append({
                "name":        name,
                "type":        "lan",
                "description": _br_desc(name),
                "status":      "up" if has_carrier else "down",
            })

    wan.sort(key=lambda x: int(re.search(r'\d+', x["name"]).group()))
    lan.sort(key=lambda x: int(re.search(r'\d+', x["name"]).group()))

    # WireGuard / VPN interfaces — detected by type or wg* name prefix.
    # Names resolved from udapi vpn/wireguard/servers: wgsrvN -> server id N.
    vpn = []
    wg_names = _vpn_names()
    for line in sh("ip -o link show type wireguard 2>/dev/null; "
                   "ip -o link show 2>/dev/null | grep -E ' wg[a-z0-9]'").splitlines():
        m = re.match(r'^\d+:\s+(\S+?)(@\S+)?:\s+<([^>]*)>', line)
        if not m:
            continue
        name = m.group(1)
        if not re.match(r'^wg', name):
            continue
        has_carrier = "LOWER_UP" in (m.group(3) or "")
        vpn.append({
            "name": name,
            "type": "vpn",
            "description": wg_names.get(name, "WireGuard VPN"),
            "status": "up" if has_carrier else "down",
        })

    return {"wanInterfaces": wan, "lanInterfaces": lan, "vpnInterfaces": vpn}

def _wan_desc(name, has_carrier, confirmed_wan=None):
    if not has_carrier:
        return "no carrier"
    # Check for PPPoE child — trace back from any ppp interface
    for ppp in sh("ip -o link show | awk '/^[0-9]+: ppp/{print $2}' "
                  "| tr -d ':'").splitlines():
        ppp = ppp.strip()
        if not ppp:
            continue
        p = sh(f"ip -o link show dev {ppp} 2>/dev/null "
               f"| grep -oP '(?<=@)\\S+(?=:)'")
        while p:
            if p == name:
                ip = sh(f"ip -o addr show dev {ppp} 2>/dev/null "
                        f"| awk '/inet /{{print $4}}' | head -1")
                return f"PPPoE{' — ' + ip if ip else ''}"
            p = sh(f"ip -o link show dev {p} 2>/dev/null "
                   f"| grep -oP '(?<=@)\\S+(?=:)'")
    # Also check /proc/net/pppoe for the parent link
    if name in _pppoe_parents():
        ip = ""
        for ppp in sh("ip -o link show | awk '/^[0-9]+: ppp/{print $2}' | tr -d ':'").splitlines():
            ppp = ppp.strip()
            if not ppp:
                continue
            ip = sh(f"ip -o addr show dev {ppp} 2>/dev/null | awk '/inet /{{print $4}}' | head -1")
            if ip:
                break
        return f"PPPoE{' — ' + ip if ip else ''}"
    ip = sh(f"ip -o addr show dev {name} 2>/dev/null "
            f"| awk '/inet /{{print $4}}' | head -1")
    if ip:
        return f"Internet — {ip}"
    return "link up"


def _br_desc(name):
    m = re.match(r'^br(\d+)$', name)
    if not m:
        return "bridge"
    vlan_id  = int(m.group(1))
    net_name = _unifi_network_name(vlan_id)
    if net_name:
        return f"{net_name} VLAN"
    return "Default VLAN" if vlan_id == 0 else f"VLAN {vlan_id}"


def _unifi_network_name(vlan_id):
    """
    Resolve a bridge VLAN ID → UniFi network name.

    UniFi OS encodes network names in the pattern:
      "net_<Name>_<bridge>_<subnet>"
    e.g. "net_Main_br10_192-168-10-0-24"

    We search all loaded udapi-net-cfg*.json files (merged via _udapi())
    for this pattern and extract the name for the matching bridge.
    """
    br_name = f"br{vlan_id}"

    # Build name map from all udapi config text (fast regex scan)
    import glob as _glob
    patterns = [
        "/data/udapi-config/udapi-net-cfg*.json",
        "/mnt/data/udapi-config/udapi-net-cfg*.json",
    ]
    for pattern in patterns:
        for p in sorted(_glob.glob(pattern)):
            try:
                txt = Path(p).read_text()
                # Find all "net_Name_brN_..." occurrences
                for m in re.finditer(
                        r'net_([A-Za-z0-9]+)_(br\d+)', txt):
                    if m.group(2) == br_name:
                        return m.group(1)   # e.g. "Main", "Guest"
            except Exception:
                pass

    # Fallback: identification.status.comment in the live config
    for iface in _udapi().get("interfaces", []):
        ident = iface.get("identification", {})
        if ident.get("id") == br_name:
            comment = iface.get("status", {}).get("comment", "")
            if comment and comment != br_name:
                return comment

    return ""

# ── API: routing tables ───────────────────────────────────────────────────────

def api_rt_tables():
    tables = []
    for line in sh_lines("cat /etc/iproute2/rt_tables 2>/dev/null"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            try:
                tables.append({"number": int(parts[0]), "name": parts[1]})
            except ValueError:
                pass
    return sorted(tables, key=lambda x: x["number"])

# ── API: commands ─────────────────────────────────────────────────────────────

def api_command(body: dict):
    cmd = body.get("command", "")
    allowed = {
        "install-ui":
            "rm -f /data/voip/ui/.ui-version && cd /data/voip && ./voip install-ui --no-restart",
        "install-daemon":
            "cd /data/voip && ./voip install",
        "install-all":
            "cd /data/voip && ./voip install && ./voip install-ui --no-restart",
        "reinstall-daemon":
            "cd /data/voip && ./voip install",
        "uninstall":
            "cd /data/voip && ./voip uninstall "
            "&& chmod +x uninstall.sh && ./uninstall.sh",
        "uninstall-ui":
            "cd /data/voip && ./voip uninstall-ui",
        "uninstall-all":
            "cd /data/voip && ./voip uninstall "
            "&& chmod +x uninstall.sh && ./uninstall.sh "
            "&& ./voip uninstall-ui && rm -rf /data/voip",
        "update":
            "cd /data/voip && ./voip update",
        "verify":
            "ip route show table "
            "$(grep -E '^[0-9]+ voip' /etc/iproute2/rt_tables "
            "| awk '{print $1}') 2>/dev/null; "
            "ip rule show | grep '100:'",
        "restart": "systemctl restart voipd",
        "stop":    "systemctl stop voipd",
        "start":   "systemctl start voipd",
    }
    if cmd not in allowed:
        return {"ok": False, "output": f"Unknown command: {cmd}"}

    # Commands that previously restarted voip-ui mid-execution now use
    # --no-restart so we can capture full output first, then schedule
    # the restart to fire 1s after we return the response.
    DEFERRED_RESTART_CMDS = {"update", "install-ui", "install-all"}

    try:
        result = subprocess.run(
            allowed[cmd], shell=True, capture_output=True,
            text=True, timeout=120)
        output = (result.stdout + result.stderr).strip()
        if cmd in DEFERRED_RESTART_CMDS:
            # Schedule voip-ui restart 1s after we return — gives the HTTP
            # response time to reach the browser before this process is killed.
            import threading
            threading.Timer(1.0, lambda: subprocess.Popen(
                "systemctl restart voip-ui && nginx -s reload",
                shell=True, start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )).start()
        return {"ok": result.returncode == 0, "output": output}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "Command timed out after 120s"}
    except Exception as e:
        return {"ok": False, "output": str(e)}

# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data:")

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _check_api_token(self):
        """Validate the nginx-injected internal token on API requests."""
        expected = _internal_token()
        if expected is None:
            return False  # Token file missing — deny (fail closed)
        provided = self.headers.get("X-Voip-Token", "")
        return provided == expected

    def _serve_file(self, path: Path):
        if not path.exists():
            self.send_response(404); self.end_headers(); return
        mime = {
            ".html": "text/html", ".js": "application/javascript",
            ".css": "text/css", ".json": "application/json",
            ".svg": "image/svg+xml", ".ico": "image/x-icon",
            ".png": "image/png", ".woff2": "font/woff2",
        }.get(path.suffix.lower(), "application/octet-stream")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", len(data))
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        if path.startswith("/voip/api/"):
            if not self._check_api_token():
                self._json(403, {"error": "forbidden"}); return
        if   path == "/voip/api/status":    self._json(200, api_status())
        elif path == "/voip/api/logs":       self._json(200, api_logs())
        elif path == "/voip/api/config":     self._json(200, read_conf())
        elif path == "/voip/api/interfaces": self._json(200, api_interfaces())
        elif path == "/voip/api/rt-tables":  self._json(200, api_rt_tables())
        else:
            rel = path[len("/voip"):].lstrip("/") or "index.html"
            fp  = DIST_DIR / rel
            if not fp.exists() or fp.is_dir():
                fp = DIST_DIR / "index.html"
            self._serve_file(fp)

    def do_POST(self):
        path   = urllib.parse.urlparse(self.path).path.rstrip("/")
        if not self._check_api_token():
            self._json(403, {"error": "forbidden"}); return
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}
        if path == "/voip/api/config":
            write_conf(body)
            subprocess.run("systemctl restart voipd", shell=True)
            self._json(200, {"ok": True})
        elif path == "/voip/api/command":
            result_holder = {}
            def run():
                result_holder.update(api_command(body))
            t = threading.Thread(target=run, daemon=True)
            t.start()
            # Long-running commands (update, install) need more time.
            cmd = body.get("command", "")
            # nginx proxy_read_timeout is 120s — stay under it with buffer
            wait = 110 if cmd in ("update", "install", "reinstall",
                                  "uninstall", "uninstall-all") else 10
            t.join(timeout=wait)
            self._json(200, result_holder if result_holder
                       else {"ok": True, "output": "Still running — check logs with: journalctl -u voip-ui -f"})
        else:
            self._json(404, {"error": "not found"})


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"VoIP UI API server listening on {BIND}:{PORT}")
    server.serve_forever()
