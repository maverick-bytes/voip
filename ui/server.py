#!/usr/bin/env python3
"""
VoIP Web UI — API backend for UniFiOS
Runs on 127.0.0.1:8099, accessed only via nginx reverse proxy at /voip
"""

import hmac
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
MAX_BODY_BYTES = 65536  # cap on POST body size (config/command payloads are tiny)

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
    "VOIP_B2BUA_USER", "VOIP_B2BUA_PASS", "VOIP_B2BUA_DOMAIN",
    "VOIP_B2BUA_LISTEN_PORT", "VOIP_B2BUA_LOCAL_USER", "VOIP_B2BUA_LOCAL_PASS",
    "VOIP_B2BUA_REG_EXPIRES",
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

# Per-key validation patterns. voipd.conf is SOURCED by the shell daemon
# (`. /data/voip/voipd.conf`), so any value written here is executed as root
# when voipd (re)starts. Structured fields are format-validated below; the
# remaining free-form fields (SIP credentials, which may legitimately contain
# spaces and punctuation) are made safe by _shell_escape_dq() at write time.
_CONF_VALIDATORS = {
    # Linux interface names (single). No shell metacharacters possible.
    "VOIP_WAN_INTERFACE":         r'^[A-Za-z0-9._-]{1,32}$',
    "VOIP_WAN_VLAN_INTERFACE":    r'^[A-Za-z0-9._-]{1,32}$',
    "VOIP_FORWARD_INTERFACE":     r'^[A-Za-z0-9._-]{1,32}( [A-Za-z0-9._-]{1,32})*$',
    "VOIP_VPN_INTERFACES":        r'^[A-Za-z0-9._-]{1,32}( [A-Za-z0-9._-]{1,32})*$',
    "VOIP_RT_TABLE_NAME":         r'^[A-Za-z0-9._-]{1,32}$',
    # Numeric fields
    "VOIP_WAN_VLAN":              r'^\d{1,4}$',
    "VOIP_RT_TABLE":              r'^\d{1,5}$',
    "VOIP_B2BUA_LISTEN_PORT":     r'^\d{1,5}$',
    "VOIP_B2BUA_REG_EXPIRES":     r'^\d{1,6}$',
    # DNS hostnames
    "PCSCF_HOSTNAME":             r'^[A-Za-z0-9.-]{1,253}$',
    "VOIP_B2BUA_DOMAIN":          r'^[A-Za-z0-9.-]{1,253}$',
    # IP / CIDR
    "VOIP_IMS_SUBNET":            r'^[0-9./]{0,32}$',
    "VOIP_FORWARD_GATEWAY":       r'^[0-9.]{0,32}$',
    # QoS map: "0:5 1:5 ..." — digits, colons, spaces
    "VOIP_WAN_VLAN_INTERFACE_EGRESS_QOS": r'^[0-9: ]{0,64}$',
    # Enums
    "ROUTING_MODE":               r'^(pbr|forward|b2bua|b2bua_netns)$',
    "VOIP_DEBUG":                 r'^(true|false)$',
}

# Characters that must never reach a sourced shell value. Free-form fields
# (SIP user/pass) are rejected if they contain these rather than escaped,
# because a SIP credential has no legitimate reason to contain them and
# rejecting is safer than trying to round-trip escaping through the shell.
_CONF_FORBIDDEN = set('"`$\\\n\r')

def _conf_value_ok(key: str, val: str) -> bool:
    pat = _CONF_VALIDATORS.get(key)
    if pat is not None:
        return re.match(pat, val) is not None
    # Free-form field (credentials): allow anything except shell-dangerous chars
    return not (_CONF_FORBIDDEN & set(val))

def write_conf(data: dict):
    # Validate every supplied value against its key's allowed format. Any
    # value that fails validation causes the whole write to be rejected —
    # we never write a partially-validated config that could contain an
    # injection payload for the shell daemon that sources this file.
    to_write = {}
    for key in CONF_KEYS:
        if key not in data:
            continue
        val = data[key]
        if not isinstance(val, str):
            val = str(val)
        if not _conf_value_ok(key, val):
            # Reject the entire request — do not write anything.
            return False, f"Invalid value for {key}"
        to_write[key] = val

    # Refuse to overwrite with an empty payload — guards against accidental wipes
    meaningful = {k: v for k, v in to_write.items() if v != ""}
    if not meaningful:
        return False, "Empty configuration payload"

    lines = ["# /data/voip/voipd.conf — managed by voip-ui",
             "# Edit here or via the web UI at https://<gateway>/voip", ""]
    for key in CONF_KEYS:
        if key in to_write:
            lines.append(f'{key}={_shell_escape_dq(to_write[key])}')
    CONF_FILE.write_text("\n".join(lines) + "\n")
    return True, "OK"


def _shell_escape_dq(val: str) -> str:
    """
    Return val as a safe double-quoted POSIX shell string. Backslash-escapes
    the four characters that remain special inside double quotes: " ` $ \\.
    Any value produced here is inert when the file is sourced, regardless of
    content — this is the last line of defense behind _conf_value_ok().
    """
    escaped = (val.replace('\\', '\\\\')
                  .replace('"', '\\"')
                  .replace('`', '\\`')
                  .replace('$', '\\$'))
    return f'"{escaped}"'

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

# ── API: service metrics (CPU / memory) ───────────────────────────────────────

# Cached page size and clock ticks — these never change at runtime.
import os as _os
_PAGE_SIZE  = _os.sysconf("SC_PAGE_SIZE")   # bytes per memory page
_CLOCK_TICK = _os.sysconf("SC_CLK_TCK")     # scheduler ticks per second

def _service_pids():
    """
    Return the set of PIDs that make up the whole service footprint:
    the voipd process tree (voipd + shell children + ip monitor), the
    sandboxed b2bua.py, and the voip-ui web backend itself.

    We resolve each systemd unit's cgroup process list via
    /sys/fs/cgroup, which is a single cheap read per unit and captures
    all child processes without spawning ps/pgrep.
    """
    pids = set()
    for unit in ("voipd", "voip-ui"):
        # cgroup v2 path used by systemd on UniFi OS
        for base in (f"/sys/fs/cgroup/system.slice/{unit}.service/cgroup.procs",
                     f"/sys/fs/cgroup/systemd/system.slice/{unit}.service/cgroup.procs"):
            try:
                with open(base) as f:
                    for line in f:
                        line = line.strip()
                        if line.isdigit():
                            pids.add(int(line))
                break  # first path that exists wins
            except OSError:
                continue
    # b2bua.py runs inside a network namespace but is still a normal host
    # process in the root PID namespace — include it explicitly in case it
    # is not under either service cgroup (it is spawned by voipd). Guard
    # against a stale pid file (e.g. after switching to pbr/forward mode
    # without a reboot): only count the PID if it is a live b2bua process,
    # so a recycled PID never attributes an unrelated process's CPU/RSS
    # to the service.
    try:
        b2_pid = read_state("b2bua_pid")
        if b2_pid.isdigit():
            with open(f"/proc/{b2_pid}/cmdline", "rb") as f:
                if b"b2bua" in f.read():
                    pids.add(int(b2_pid))
    except Exception:
        pass
    return pids

def _proc_cpu_ticks(pid):
    """Return utime+stime (in clock ticks) for a PID, or None if gone."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            data = f.read()
        # Field 14 = utime, 15 = stime (1-indexed, after the comm field which
        # may contain spaces/parens — so split on the last ')' first).
        after = data[data.rfind(")") + 2:].split()
        return int(after[11]) + int(after[12])   # utime + stime
    except (OSError, ValueError, IndexError):
        return None

def _proc_rss_bytes(pid):
    """Return resident set size in bytes for a PID, or 0 if gone."""
    try:
        with open(f"/proc/{pid}/statm") as f:
            # field 2 = resident pages
            resident = int(f.read().split()[1])
        return resident * _PAGE_SIZE
    except (OSError, ValueError, IndexError):
        return 0

def _service_uptime_seconds():
    """
    Seconds since the voipd main process started, from /proc only. Reads the
    process starttime (field 22 of /proc/<pid>/stat, in ticks since boot) and
    subtracts it from the system uptime. Returns None if voipd isn't running.
    """
    mainpid = sh("systemctl show voipd --property=MainPID --value")
    if not mainpid.isdigit() or mainpid == "0":
        return None
    try:
        with open(f"/proc/{mainpid}/stat") as f:
            data = f.read()
        after = data[data.rfind(")") + 2:].split()
        starttime_ticks = int(after[19])          # field 22 overall
        proc_start_s = starttime_ticks / _CLOCK_TICK
        with open("/proc/uptime") as f:
            system_uptime_s = float(f.read().split()[0])
        return max(system_uptime_s - proc_start_s, 0.0)
    except (OSError, ValueError, IndexError):
        return None

# CPU% is computed from the tick delta between consecutive polls (the UI polls
# every ~8 s). A one-shot short window cannot work here: with CLK_TCK=100 one
# kernel tick is 10 ms, so the smallest non-zero reading over a 150 ms window
# is 10/150 = 6.7% — a light steady load such as an active RTP relay (~1-3%)
# reads as 0.0, while idle background blips read as exactly 6.6/6.7. Averaging
# across the poll interval gives 0.125% resolution at 8 s.
_cpu_lock = threading.Lock()
_cpu_last = {"ticks": None, "t": 0.0, "value": 0.0}

def api_metrics():
    """
    CPU % and memory (MB) across the whole service footprint, computed from
    /proc with no external process spawns. CPU is averaged over the interval
    since the previous call (~ the UI poll period); the first call after a
    backend start returns 0.0 while the baseline is established.
    """
    import time as _time
    pids = _service_pids()

    # --- CPU: tick delta since the previous poll ------------------------------
    now = _time.monotonic()
    ticks = sum(v for v in (_proc_cpu_ticks(p) for p in pids) if v is not None)

    with _cpu_lock:
        prev_ticks = _cpu_last["ticks"]
        prev_t     = _cpu_last["t"]
        prev_val   = _cpu_last["value"]
        if prev_ticks is None:
            cpu_pct = 0.0                    # first sample: establish baseline
            _cpu_last.update(ticks=ticks, t=now, value=cpu_pct)
        elif now - prev_t < 1.0:
            cpu_pct = prev_val               # rapid re-poll: reuse last value
        else:
            delta = ticks - prev_ticks
            if delta < 0:
                delta = 0                    # process churn/restart between polls
            cpu_seconds = delta / _CLOCK_TICK
            cpu_pct = round(min(cpu_seconds / (now - prev_t) * 100.0,
                                100.0 * (_os.cpu_count() or 1)), 1)
            _cpu_last.update(ticks=ticks, t=now, value=cpu_pct)

    # --- Memory: sum RSS across all service PIDs -----------------------------
    mem_bytes = sum(_proc_rss_bytes(p) for p in pids)
    mem_mb = round(mem_bytes / (1024 * 1024), 1)

    return {
        "cpuPercent": cpu_pct,
        "memoryMb":   mem_mb,
        "procCount":  len(pids),
    }

# ── API: status ───────────────────────────────────────────────────────────────

def api_status():
    running = sh("systemctl is-active voipd") == "active"
    # Uptime of the voipd service. Derived from the main process start time in
    # /proc (boot-relative starttime vs system uptime) — no external date
    # parser dependency, works regardless of locale/timezone formatting.
    uptime = ""
    uptime_seconds = _service_uptime_seconds()
    if uptime_seconds is not None:
        d, rem = divmod(int(uptime_seconds), 86400)
        h, rem = divmod(rem, 3600)
        m = rem // 60
        if d:
            uptime = f"{d}d {h}h {m}m"
        elif h:
            uptime = f"{h}h {m}m"
        else:
            uptime = f"{m}m"
    conf = read_conf()
    routing_mode = conf.get("ROUTING_MODE", "pbr")

    # Service resource metrics (CPU %, memory MB) — computed from /proc with
    # no external process spawns. Folded into the status response so the page
    # gets them on its existing poll with no extra request or timer. Only
    # sampled when the service is actually running.
    metrics = api_metrics() if running else {"cpuPercent": 0.0, "memoryMb": 0.0, "procCount": 0}

    # In B2BUA mode the SIP endpoint for clients is the gateway itself,
    # not the upstream P-CSCF — the B2BUA terminates SIP locally.
    sip_proxy = read_state("pcscf_ip")
    b2bua_registered = None
    b2bua_clients = None
    b2bua_listen_port = conf.get("VOIP_B2BUA_LISTEN_PORT", "5060")
    if routing_mode in ("b2bua", "b2bua_netns"):
        b2bua_status_file = STATE_DIR / "b2bua_status"
        if b2bua_status_file.exists():
            for line in b2bua_status_file.read_text().splitlines():
                if line.startswith("upstream_registered="):
                    b2bua_registered = line.split("=", 1)[1].strip()
                elif line.startswith("local_clients="):
                    b2bua_clients = line.split("=", 1)[1].strip() or None
        # Gateway IP (br0) is the SIP address LAN clients should register to
        gw_ip = sh("ip -o -4 addr show dev br0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1")
        if not gw_ip:
            gw_ip = sh("ip -o -4 addr show scope global 2>/dev/null | awk '!/lo/{print $4}' | cut -d/ -f1 | head -1")
        sip_proxy = f"{gw_ip}:{b2bua_listen_port}" if gw_ip else f":{b2bua_listen_port}"

    return {
        "serviceRunning": running,
        "interface":    read_state("voip_ip") and conf.get(
                            "VOIP_WAN_VLAN_INTERFACE", "voip"),
        "vlan":         conf.get("VOIP_WAN_VLAN", ""),
        "wanInterface": conf.get("VOIP_WAN_INTERFACE", ""),
        "voipIp":       read_state("voip_ip"),
        "gateway":      read_state("gw"),
        "routingMode":  routing_mode,
        "routingTable": conf.get("VOIP_RT_TABLE", ""),
        "imsSubnet":    read_state("subnet"),
        "natRule":      "MASQUERADE in UBIOS_POSTROUTING_USER_HOOK",
        "sipProxy":     sip_proxy,
        "uptime":       uptime,
        "b2buaRegistered": b2bua_registered,
        "b2buaClients":    b2bua_clients,
        "b2buaListenPort": b2bua_listen_port,
        "cpuPercent":      metrics["cpuPercent"],
        "memoryMb":        metrics["memoryMb"],
        "procCount":       metrics["procCount"],
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
    seen_vpn = set()
    for line in sh("ip -o link show type wireguard 2>/dev/null; "
                   "ip -o link show 2>/dev/null | grep -E ' wg[a-z0-9]'").splitlines():
        m = re.match(r'^\d+:\s+(\S+?)(@\S+)?:\s+<([^>]*)>', line)
        if not m:
            continue
        name = m.group(1)
        if not re.match(r'^wg', name) or name in seen_vpn:
            continue
        seen_vpn.add(name)
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
            "rm -f /data/voip/ui/.ui-version && cd /data/voip && ./voip install && ./voip install-ui --no-restart",
        "reinstall-daemon":
            "cd /data/voip && ./voip install",
        "uninstall":
            "cd /data/voip && ./voip uninstall "
            "&& chmod +x uninstall.sh && ./uninstall.sh",
        "uninstall-ui":
            "cd /data/voip && ./voip uninstall-ui --no-stop",
        "uninstall-all":
            "cd /data/voip && ./voip uninstall "
            "&& chmod +x uninstall.sh && ./uninstall.sh "
            "&& ./voip uninstall-ui --no-stop && rm -rf /data/voip",
        "update":
            "cd /data/voip && ./voip update",
        "verify":
            "ip route show table "
            "$(grep -E '^[0-9]+ voip' /etc/iproute2/rt_tables "
            "| awk '{print $1}') 2>/dev/null; "
            "ip rule show | grep -E '99:|100:|101:'",
        "restart": "systemctl restart voipd",
        "stop":    "systemctl stop voipd",
        "start":   "systemctl start voipd",
    }
    if cmd not in allowed:
        return {"ok": False, "output": f"Unknown command: {cmd}"}

    try:
        result = subprocess.run(
            allowed[cmd], shell=True, capture_output=True,
            text=True, timeout=120)
        output = (result.stdout + result.stderr).strip()
        # Some commands restart or stop the voip-ui service itself, which would
        # kill this process mid-response. We don't fire that here; instead we
        # return the deferred action to the caller (do_POST), which runs it
        # only AFTER the HTTP response has been flushed to the client. This
        # removes the fixed-delay race where a slow response could be cut off
        # by the service restart. See DEFERRED_ACTIONS below.
        return {
            "ok": result.returncode == 0,
            "output": output,
            "_deferred": DEFERRED_ACTIONS.get(cmd),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "Command timed out after 120s"}
    except Exception as e:
        return {"ok": False, "output": str(e)}


# Commands that restart or stop the voip-ui service itself map to a shell
# action that must run only after the HTTP response has been sent.
#   - update/install-ui/install-all: restart voip-ui + reload nginx
#   - uninstall-ui/uninstall-all:    stop voip-ui
DEFERRED_ACTIONS = {
    "update":        "systemctl restart voip-ui && nginx -s reload",
    "install-ui":    "systemctl restart voip-ui && nginx -s reload",
    "install-all":   "systemctl restart voip-ui && nginx -s reload",
    "uninstall-ui":  "systemctl stop voip-ui",
    "uninstall-all": "systemctl stop voip-ui",
}


def _run_deferred(shell_action: str):
    """Run a post-response deferred action detached from this process."""
    subprocess.Popen(
        shell_action, shell=True, start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

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
        # No Access-Control-Allow-Origin: the UI and API are same-origin behind
        # the UniFi nginx proxy, so cross-origin access is never needed. A
        # wildcard here would let any site attempt cross-origin API reads.
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _check_api_token(self):
        """Validate the nginx-injected internal token on API requests."""
        expected = _internal_token()
        if expected is None:
            return False  # Token file missing — deny (fail closed)
        provided = self.headers.get("X-Voip-Token", "")
        # Constant-time compare to avoid leaking the token via response timing.
        return hmac.compare_digest(provided, expected)

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
        # Same-origin app: no cross-origin CORS grants are advertised. Browsers
        # do not send a preflight for same-origin requests, so this is only a
        # well-formed no-op response for any stray OPTIONS probe.
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
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

        # Bound the request body — config/command payloads are tiny (< 4 KB in
        # practice). Reject anything oversized rather than buffering it.
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._json(400, {"error": "invalid Content-Length"}); return
        if length < 0 or length > MAX_BODY_BYTES:
            self._json(413, {"error": "payload too large"}); return

        # Parse the JSON body defensively — a malformed body must return a
        # clean 400, not a 500 with a half-written response.
        try:
            body = json.loads(self.rfile.read(length)) if length else {}
        except (json.JSONDecodeError, ValueError):
            self._json(400, {"error": "invalid JSON body"}); return
        if not isinstance(body, dict):
            self._json(400, {"error": "body must be a JSON object"}); return

        if path == "/voip/api/config":
            ok, msg = write_conf(body)
            if not ok:
                self._json(400, {"ok": False, "error": msg}); return
            subprocess.run("systemctl restart voipd", shell=True)
            self._json(200, {"ok": True})
        elif path == "/voip/api/command":
            result_holder = {}
            def run():
                result_holder.update(api_command(body))
            t = threading.Thread(target=run, daemon=True)
            t.start()
            # Long-running commands need more time than the default. These are
            # the actual command IDs the UI sends (install-* run ./voip install
            # which restarts services; uninstall-* tear things down). nginx
            # proxy_read_timeout is 120s — stay under it with buffer.
            cmd = body.get("command", "")
            SLOW_COMMANDS = (
                "update",
                "install-daemon", "install-ui", "install-all", "reinstall-daemon",
                "uninstall", "uninstall-ui", "uninstall-all",
            )
            wait = 110 if cmd in SLOW_COMMANDS else 10
            t.join(timeout=wait)
            payload = result_holder if result_holder else {
                "ok": True,
                "output": "Still running — check logs with: journalctl -u voip-ui -f",
            }
            # Pull the deferred action (restart/stop voip-ui) out of the payload
            # so it isn't serialized to the client, then run it strictly AFTER
            # the response is flushed — otherwise restarting voip-ui here would
            # kill this process before the client receives the reply.
            deferred = payload.pop("_deferred", None)
            self._json(200, payload)
            if deferred:
                try:
                    self.wfile.flush()
                except Exception:
                    pass
                _run_deferred(deferred)
        else:
            self._json(404, {"error": "not found"})


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"VoIP UI API server listening on {BIND}:{PORT}")
    server.serve_forever()
