#!/usr/bin/env python3
"""
voipd-b2bua v1.4 — UDP + TCP SIP B2BUA for UniFiOS voipd
Single registrar for LAN clients (both UDP and TCP transport).
Parallel forking on inbound calls — all registered clients ring simultaneously.
Upstream toward IMS always uses UDP.
Kernel PBR routes upstream traffic via the voip VLAN interface.
"""
import os, sys, re, time, socket, threading, hashlib, random, logging, signal

_debug_env = os.environ.get('B2BUA_DEBUG','false').lower() == 'true'
logging.basicConfig(stream=sys.stdout,
    level=logging.DEBUG if _debug_env else logging.INFO,
    format='%(asctime)s [B2BUA] %(levelname)s %(message)s')
log = logging.getLogger('voipd.b2bua')

_e = os.environ.get
PROXY_IP    = _e('B2BUA_PROXY',        '')
PROXY_PORT  = int(_e('B2BUA_PROXY_PORT','5060'))
SIP_DOMAIN  = _e('B2BUA_DOMAIN',       '')
SIP_USER    = _e('B2BUA_USER',         '')
SIP_PASS    = _e('B2BUA_PASS',         '')
VOIP_IP     = _e('B2BUA_VOIP_IP',      '')
LOCAL_PORT  = int(_e('B2BUA_LOCAL_PORT','5060'))
LOCAL_PASS  = _e('B2BUA_LOCAL_PASS',   '')
LOCAL_USER  = _e('B2BUA_LOCAL_USER',   '')
REG_EXPIRES = int(_e('B2BUA_REG_EXPIRES','600'))
STATE_DIR   = _e('B2BUA_STATE_DIR',    '/var/run/voipd')

# ── Sockets ────────────────────────────────────────────────────────────────────
_udp_sock  = None   # single UDP socket: 0.0.0.0:LOCAL_PORT
_tcp_srv   = None   # TCP listener socket
_tcp_conns = {}     # {(ip,port): socket}  — active TCP client connections
_tcp_lock  = threading.Lock()

# Event set right before the main UDP recv loop starts
_loop_ready = threading.Event()

def _log_sip(direction, addr, data):
    if not _debug_env: return
    try:
        text = data.decode('utf-8','replace').replace('\r\n','\n')
        lines = text.split('\n')
        log.debug(f'SIP {direction} {addr[0]}:{addr[1]} | {lines[0]}')
        for l in lines[1:7]:
            if l.strip(): log.debug(f'  {l.rstrip()}')
    except Exception: pass

# ── Helpers ────────────────────────────────────────────────────────────────────
def _rnd(n=8): return ''.join(random.choice('0123456789abcdef') for _ in range(n))
def new_branch():  return f'z9hG4bK{_rnd(12)}'
def new_call_id(): return f'{_rnd(16)}@b2bua'
def new_tag():     return _rnd(8)
def _md5(s):       return hashlib.md5(s.encode()).hexdigest()

def _build_auth(method, uri, user, pw, challenge, hdr='Authorization', nc_val=None):
    rm  = re.search(r'realm="([^"]+)"',      challenge, re.I)
    nm  = re.search(r'nonce="([^"]+)"',      challenge, re.I)
    qm  = re.search(r'qop="([^"]+)"',        challenge, re.I)
    am  = re.search(r'algorithm=([^,\s"]+)', challenge, re.I)
    if not rm or not nm: return ''
    realm=rm.group(1); nonce=nm.group(1)
    qop=(qm.group(1).split(',')[0].strip() if qm else '')
    algo=(am.group(1).upper() if am else 'MD5')
    # nc must increment for each use of the same nonce (replay protection)
    nc = f'{nc_val:08x}' if nc_val else '00000001'
    cnonce=_rnd(8)
    ha1=_md5(f'{user}:{realm}:{pw}'); ha2=_md5(f'{method}:{uri}')
    resp=(_md5(f'{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}') if qop
          else _md5(f'{ha1}:{nonce}:{ha2}'))
    h=(f'Digest username="{user}",realm="{realm}",'
       f'nonce="{nonce}",uri="{uri}",response="{resp}",algorithm={algo}')
    if qop: h+=f',qop={qop},nc={nc},cnonce="{cnonce}"'
    log.debug(f'Auth: realm={realm} algo={algo} qop={qop} nc={nc}')
    return h

_COMPACT={'v':'via','f':'from','t':'to','i':'call-id','m':'contact',
          'c':'content-type','l':'content-length','k':'supported',
          'o':'event','r':'refer-to','b':'referred-by'}

def _parse(data:bytes)->dict:
    try:
        sep=b'\r\n\r\n' if b'\r\n\r\n' in data else b'\n\n'
        hpart,body=(data.split(sep,1) if sep in data else (data,b''))
        text=hpart.decode('utf-8','replace').replace('\r\n','\n')
        lines=[]
        for l in text.split('\n'):
            if l and l[0] in(' ','\t') and lines: lines[-1]+=' '+l.strip()
            else: lines.append(l)
        fl=lines[0] if lines else ''
        hdrs=[]
        for l in lines[1:]:
            if ':' not in l: continue
            n,_,v=l.partition(':')
            n=_COMPACT.get(n.strip().lower(),n.strip().lower())
            hdrs.append((n,v.strip()))
        cl=0
        for n,v in hdrs:
            if n=='content-length':
                try: cl=int(v)
                except: pass
                break
        return {'first_line':fl,'headers':hdrs,'body':body[:cl] if cl>0 else b''}
    except Exception as e: log.debug(f'_parse:{e}'); return {}

def _gh(msg,name):
    name=name.lower()
    for n,v in msg.get('headers',[]):
        if n==name: return v
    return ''

def _method(msg):
    fl=msg.get('first_line','')
    return '' if fl.startswith('SIP/') else (fl.split()[0] if fl else '')

def _status(msg):
    fl=msg.get('first_line','')
    try: return int(fl.split()[1]) if fl.startswith('SIP/2.0') else 0
    except: return 0

def _uri(hval):
    m=re.search(r'<([^>]+)>',hval)
    if m: return m.group(1)
    m=re.search(r'sips?:[^\s;,>]+',hval)
    return m.group(0) if m else hval

def _tag(hval):
    m=re.search(r'[;,]\s*tag=([^;,>\s]+)',hval)
    return m.group(1) if m else ''

def _user_from_uri(uri_str):
    m=re.search(r'sips?:([^@;?>\s]+)@',uri_str)
    return m.group(1) if m else ''

def _hdr_uri_list(hval):
    uris=[u.strip() for u in re.findall(r'<([^>]+)>', hval or '') if u.strip()]
    if uris:
        return uris
    v=(hval or '').strip()
    return [v] if v else []

def _preferred_identity(uris):
    for uri in uris or []:
        if ';user=phone' in uri.lower():
            return uri
    for uri in uris or []:
        if _user_from_uri(uri) == SIP_USER:
            return uri
    if uris:
        return uris[0]
    return f'sip:{SIP_USER}@{SIP_DOMAIN}'

def _local_user(default=''):
    return LOCAL_USER or default or SIP_USER

def _local_contact(default=''):
    return f'<sip:{_local_user(default)}@{VOIP_IP}:{LOCAL_PORT}>'

def _up_invite_resp_hdrs(dlg, to_hdr):
    via_hdrs=[('via', v) for v in (getattr(dlg, 'up_vias', []) or [])]
    return via_hdrs+[
        ('from', dlg.up_from),
        ('to', to_hdr),
        ('call-id', dlg.up_id),
        ('cseq', getattr(dlg, 'up_req_cseq', '1 INVITE')),
        ('contact', f'<sip:{SIP_USER}@{VOIP_IP}:{LOCAL_PORT}>'),
    ]

def _up_uri(local_ruri):
    uri=_uri(local_ruri).strip()
    if not uri:
        return f'sip:{SIP_DOMAIN}'

    if uri.lower().startswith('tel:'):
        callee=re.sub(r'\s+', '', uri[4:])
        return f'sip:{callee}@{SIP_DOMAIN};user=phone'

    params=''
    for param in re.findall(r';([^;>\s?]+)', uri):
        key=param.split('=',1)[0].lower()
        if key in ('user', 'phone-context'):
            params+=f';{param}'

    callee=_user_from_uri(uri)
    if not callee:
        callee=re.sub(r'^sips?:', '', uri.split('@')[0].split(';')[0])
    if callee and all(ch.isdigit() or ch in '+*#' for ch in callee) and ';user=' not in params.lower():
        params+=';user=phone'
    return f'sip:{callee}@{SIP_DOMAIN}{params}'

def _cseq_num(hval):
    m=re.match(r'\s*(\d+)',hval)
    return int(m.group(1)) if m else 1

def _cseq_method(hval):
    p=hval.split(None,1)
    return p[1].strip() if len(p)>1 else ''

def _map_upstream_final_response(st, reason, msg):
    """Translate carrier-specific 403 finals into clearer client-facing SIP codes.

    Huawei/IMS sometimes returns 403 for busy/rejected destinations. Mapping those
    to standard UAC-facing statuses keeps softphones from mistaking busy callee
    conditions for auth/permission failures.
    """
    if st != 403:
        return st, reason

    warning = ' '.join(v for n, v in msg.get('headers', []) if n == 'warning').lower()
    reason_hdr = ' '.join(v for n, v in msg.get('headers', []) if n == 'reason').lower()
    detail = f'{warning} {reason_hdr}'

    if 'busy' in detail or 'cause=17' in detail:
        return 486, 'Busy Here'
    if 'call rejected' in detail or 'cause=21' in detail:
        return 603, 'Decline'
    return st, reason

def _build(first_line,headers,body=b''):
    lines=[first_line]; has_cl=False
    for n,v in headers:
        dn='-'.join(w.capitalize() for w in n.split('-'))
        lines.append(f'{dn}: {v}')
        if n=='content-length': has_cl=True
    if not has_cl: lines.append(f'Content-Length: {len(body)}')
    return ('\r\n'.join(lines)+'\r\n\r\n').encode()+body

def _respond(req,status,reason,extra=None,body=b'',addr=None):
    fl=f'SIP/2.0 {status} {reason}'; hdrs=[]
    for name in ('via','from','to','call-id','cseq'):
        for n,v in req.get('headers',[]):
            if n==name:
                if name=='via' and addr:
                    src_ip,src_port=addr[0],addr[1]
                    vim=re.search(r'SIP/2\.0/(?:UDP|TCP)\s+([^;:]+)',v,re.I)
                    via_ip=(vim.group(1).strip() if vim else '')
                    if via_ip and via_ip!=src_ip:
                        v+=f';received={src_ip}'
                    if re.search(r';rport(?!=\d)',v):
                        v=re.sub(r';rport(?!=\d)',f';rport={src_port}',v,count=1)
                    elif ';rport' not in v:
                        v+=f';received={src_ip};rport={src_port}'
                hdrs.append((n,v))
                if name!='via': break
    if extra: hdrs.extend(extra)
    if body:  hdrs.append(('content-type','application/sdp'))
    return _build(fl,hdrs,body)

def _send(raw, addr, transport='udp', conn=None):
    """Send raw SIP bytes. Uses TCP conn if provided, else UDP."""
    if transport == 'tcp' and conn:
        try:    conn.sendall(raw)
        except Exception as exc: log.error(f'tcp send {addr}: {exc}')
    else:
        try:    _udp_sock.sendto(raw, addr)
        except Exception as exc: log.error(f'udp send {addr}: {exc}')

# ── TCP: message framing + server ─────────────────────────────────────────────

def _extract_sip_msg(buf):
    """Extract one complete SIP message from a TCP byte buffer.
    Returns (msg_bytes, remaining_buf) or (None, buf) if incomplete."""
    sep = b'\r\n\r\n'
    idx = buf.find(sep)
    if idx < 0:
        sep = b'\n\n'
        idx = buf.find(sep)
        if idx < 0:
            return None, buf
    hdr_part = buf[:idx]
    body_start = idx + len(sep)
    cl = 0
    for line in hdr_part.split(b'\r\n' if b'\r\n' in hdr_part else b'\n'):
        if line.lower().startswith(b'content-length:'):
            try:    cl = int(line.split(b':',1)[1].strip())
            except: pass
            break
    total = body_start + cl
    if len(buf) < total:
        return None, buf
    return buf[:total], buf[total:]

def _tcp_client_loop(conn, addr):
    """Handle one TCP client connection — buffer data, parse SIP messages."""
    with _tcp_lock:
        _tcp_conns[addr] = conn
    log.debug(f'TCP connected: {addr[0]}:{addr[1]}')
    buf = b''
    try:
        while True:
            chunk = conn.recv(8192)
            if not chunk:
                break
            buf += chunk
            while True:
                raw, buf = _extract_sip_msg(buf)
                if raw is None:
                    break
                threading.Thread(target=_dispatch,
                                 args=(raw, addr, 'tcp', conn),
                                 daemon=True).start()
    except Exception as exc:
        log.debug(f'TCP client {addr}: {exc}')
    finally:
        with _tcp_lock:
            _tcp_conns.pop(addr, None)
        try:    conn.close()
        except: pass
        log.debug(f'TCP disconnected: {addr[0]}:{addr[1]}')

def _tcp_server_loop():
    global _tcp_srv
    try:
        _tcp_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _tcp_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            _tcp_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        _tcp_srv.bind(('0.0.0.0', LOCAL_PORT))
        _tcp_srv.listen(32)
        log.info(f'TCP listener ready on 0.0.0.0:{LOCAL_PORT}')
        while True:
            conn, addr = _tcp_srv.accept()
            threading.Thread(target=_tcp_client_loop, args=(conn, addr),
                             daemon=True).start()
    except Exception as exc:
        log.error(f'TCP server: {exc}')

def _tcp_conn_for(addr):
    """Return existing TCP conn for addr, or None."""
    with _tcp_lock:
        return _tcp_conns.get(addr)

# ── State ──────────────────────────────────────────────────────────────────────
class _Reg:
    __slots__=('contact','aor','addr','expires','call_id','username','transport','conn')
    def __init__(self,contact,aor,addr,expires,call_id,username='',transport='udp',conn=None):
        self.contact=contact; self.aor=aor; self.addr=addr; self.expires=expires
        self.call_id=call_id; self.username=username
        self.transport=transport; self.conn=conn

class _Dialog:
    def __init__(self,direction='out'):
        self.direction=direction
        self.lc_id=''; self.lc_addr=None; self.lc_from=''
        self.lc_target=''; self.lc_to=''; self.lc_peer_to=''; self.lc_tag=''; self.lc_cseq=1; self.lc_branch=''
        self.lc_vias=[]; self.lc_transport='udp'; self.lc_conn=None
        self.lc_body=b''          # stored SDP body for outbound auth retry
        self.up_id=''; self.up_from=''; self.up_to=''; self.up_identity=''
        self.up_vias=[]; self.up_req_cseq='1 INVITE'
        self.up_to_tag=''; self.up_cseq=1; self.up_branch=''
        self.up_routes=[]         # Route headers used in INVITE (required in ACK per RFC 3261)
        self.up_contact=None      # Contact URI from IMS 200 OK (for ACK routing)
        self.up_contact_addr=None # (ip, port) parsed from up_contact
        self.up_auth_tried=False  # prevent infinite auth retry loops
        self.state='trying'; self.lock=threading.Lock()

class _Fork:
    """Tracks all parallel fork legs for one inbound call from the IMS.
    All legs ring simultaneously; the first 200 OK wins and the others are CANCELled.
    """
    def __init__(self, up_id, up_from, up_to, up_vias=None, up_req_cseq='1 INVITE'):
        self.up_id       = up_id
        self.up_from     = up_from
        self.up_to       = up_to
        self.up_vias     = list(up_vias or [])
        self.up_req_cseq = up_req_cseq
        self.up_to_tag   = ''
        self.legs        = {}     # lc_id -> _Dialog for each fork leg
        self.answered    = False  # True once first 200 accepted
        self.cancelled   = False  # True once upstream CANCEL was processed
        self.prov_sent   = False  # True once first 180/183 relayed upstream
        self.lock        = threading.Lock()

_regs={}; _regs_lock=threading.Lock()
_dlg_by_up={}; _dlg_by_lc={}; _dlg_lock=threading.Lock()
_forks={}; _forks_lock=threading.Lock()

# ── Upstream registration ──────────────────────────────────────────────────────
_ureg={'registered':False,'challenge':None,'call_id':None,'cseq':1,
       'from_tag':None,'service_route':[],'associated_uri':[],
       'nc':0,'invite_nc':2}
_ureg_lock=threading.Lock()

def _send_register(with_auth=False):
    # Periodic re-registration always starts WITHOUT auth to get a fresh nonce.
    # The IMS sends 401 with new nonce → we reply with nc=1.
    # This keeps nc permanently bounded at 1 for REGISTER.
    if not _ureg.get('from_tag'): _ureg['from_tag']=new_tag()
    cid=_ureg.get('call_id') or new_call_id(); _ureg['call_id']=cid
    seq=_ureg['cseq']; uri=f'sip:{SIP_DOMAIN}'
    hdrs=[
        ('via',         f'SIP/2.0/UDP {VOIP_IP}:{LOCAL_PORT};branch={new_branch()};rport'),
        ('from',        f'<sip:{SIP_USER}@{SIP_DOMAIN}>;tag={_ureg["from_tag"]}'),
        ('to',          f'<sip:{SIP_USER}@{SIP_DOMAIN}>'),
        ('call-id',     cid), ('cseq',f'{seq} REGISTER'),
        ('contact',     f'<sip:{SIP_USER}@{VOIP_IP}:{LOCAL_PORT}>'),
        ('expires',     str(REG_EXPIRES)), ('max-forwards','70'),
        ('user-agent',  'voipd-b2bua/1.4'),
        ('allow',       'INVITE,ACK,BYE,CANCEL,OPTIONS,REGISTER'),
        ('supported',   'path,gruu'),
    ]
    if with_auth and _ureg.get('challenge'):
        _ureg['nc'] = _ureg.get('nc', 0) + 1   # monotonic nc for REGISTER only (INVITEs do not use pre-auth)
        h=_build_auth('REGISTER',uri,SIP_USER,SIP_PASS,_ureg['challenge'],nc_val=_ureg['nc'])
        if h: hdrs.append(('authorization',h))
    _ureg['cseq']+=1
    raw=_build(f'REGISTER {uri} SIP/2.0',hdrs)
    _log_sip('>>UP',(PROXY_IP,PROXY_PORT),raw)
    _send(raw,(PROXY_IP,PROXY_PORT))
    log.debug(f'-> REGISTER cseq={seq} auth={with_auth}')

def _register_loop():
    _loop_ready.wait()
    with _ureg_lock: _send_register(False)
    _retry_count = 0
    while True:
        with _ureg_lock:
            is_reg = _ureg.get('registered', False)
        # Always 30s — IMS nonce for INVITE auth has short TTL (~90-120s).
        # invite_nc resets to 2 on each fresh nonce so nc stays at 2-5 max.
        interval = 30
        time.sleep(interval)
        try:
            with _ureg_lock:
                if not _ureg.get('registered', False):
                    _retry_count += 1
                    if _retry_count >= 5:
                        log.warning(f'register_loop: {_retry_count} failed retries — resetting dialog')
                        _ureg['call_id']=None; _ureg['from_tag']=None
                        _ureg['cseq']=1; _ureg['challenge']=None
                        _ureg['nc']=0; _ureg['invite_nc']=2
                        _retry_count=0
                        _send_register(False)
                    else:
                        _send_register(bool(_ureg.get('challenge')))
                else:
                    _retry_count=0
                    # Always send periodic re-reg WITHOUT auth so IMS issues
                    # a fresh nonce each cycle via 401. The 401 handler then
                    # responds with nc=1, keeping nc permanently bounded.
                    _send_register(False)
        except Exception as exc: log.warning(f'register_loop:{exc}')

def _on_register_resp(msg,raw=b''):
    st=_status(msg); _log_sip('<<UP',(PROXY_IP,PROXY_PORT),raw)
    with _ureg_lock:
        if st==200:
            _ureg['registered']=True
            sr=[v for n,v in msg.get('headers',[]) if n=='service-route']
            _ureg['service_route']=sr
            assoc=[]
            for n,v in msg.get('headers',[]):
                if n=='p-associated-uri':
                    assoc.extend(_hdr_uri_list(v))
            _ureg['associated_uri']=assoc
            if sr: log.info(f'Service-Route: {sr[0][:60]}')
            log.info(f'*** Upstream registered: {SIP_USER}@{SIP_DOMAIN} ***')
            _write_state()
        elif st==401:
            chal=_gh(msg,'www-authenticate')
            if chal:
                # New challenge = new nonce, reset nc counter
                _ureg['challenge']=chal; _ureg['registered']=False
                _ureg['nc']=0          # reset register nc for fresh nonce
                _ureg['invite_nc']=2   # INVITE nc starts at 2 (nc=1 used by REGISTER)
                _send_register(True)
        elif st==403:
            log.error(f'REGISTER 403 — user={SIP_USER}')
            _ureg['call_id']=None; _ureg['from_tag']=None; _ureg['cseq']=1
        else:
            log.debug(f'REGISTER resp {st}')

# ── Local registrar ────────────────────────────────────────────────────────────
# AOR is keyed by username@source_ip:source_port so that:
#   - Two devices at different IPs get separate slots
#   - One device registering on both UDP and TCP gets separate slots (different port)
#   - Domain in From header is ignored
# TCP ports change on reconnect; the old slot expires naturally within its TTL.

def _aor(from_val, addr=None):
    user = _user_from_uri(_uri(from_val))
    if not user:
        user = re.sub(r'[;?].*', '', _uri(from_val))
    if addr:
        return f'sip:{user}@{addr[0]}:{addr[1]}'
    return f'sip:{user}'

def _on_local_register(msg,addr,transport='udp',conn=None):
    from_val=_gh(msg,'from'); cont_val=_gh(msg,'contact')
    call_id=_gh(msg,'call-id')
    # Key by username@source_ip:port — domain in From header is irrelevant
    aor=_aor(from_val, addr)
    local_username=_user_from_uri(_uri(from_val)) or SIP_USER
    aor_hdr=_gh(msg,'to') or from_val

    if LOCAL_PASS:
        auth=_gh(msg,'authorization')
        if not auth:
            nonce=_rnd(16); realm=SIP_DOMAIN or 'b2bua.local'
            _send(_respond(msg,401,'Unauthorized',extra=[
                ('www-authenticate',
                 f'Digest realm="{realm}",nonce="{nonce}",algorithm=MD5')
            ],addr=addr),addr,transport,conn); return
        rm=re.search(r'realm="([^"]+)"',auth); nm=re.search(r'nonce="([^"]+)"',auth)
        rm2=re.search(r'response="([^"]+)"',auth); um=re.search(r'uri="([^"]+)"',auth)
        um2=re.search(r'username="([^"]+)"',auth)
        check=LOCAL_USER if LOCAL_USER else (um2.group(1) if um2 else local_username)
        if rm and nm and rm2 and um:
            ha1=_md5(f'{check}:{rm.group(1)}:{LOCAL_PASS}')
            ha2=_md5(f'REGISTER:{um.group(1)}')
            exp=_md5(f'{ha1}:{nm.group(1)}:{ha2}')
            if exp!=rm2.group(1):
                log.warning(f'Local auth failed for {check} from {addr[0]}')
                _send(_respond(msg,403,'Forbidden',addr=addr),addr,transport,conn); return

    exp_hdr=_gh(msg,'expires')
    expires=int(exp_hdr) if exp_hdr and exp_hdr.isdigit() else 3600
    if cont_val:
        em=re.search(r'expires=(\d+)',cont_val)
        if em: expires=int(em.group(1))
    cont_uri=_uri(cont_val) if cont_val else ''

    with _regs_lock:
        if expires==0:
            _regs.pop(aor,None); log.info(f'Unregistered {local_username} @ {addr[0]}')
        else:
            _regs[aor]=_Reg(cont_uri,aor_hdr,addr,time.time()+expires,call_id,
                            local_username,transport,conn)
            log.info(f'Registered {local_username} @ {addr[0]}:{addr[1]} '
                     f'({transport.upper()} exp={expires}s)')

    to_val=_gh(msg,'to')
    if not _tag(to_val): to_val+=f';tag={new_tag()}'
    _send(_respond(msg,200,'OK',extra=[
        ('to',to_val),
        ('contact',_local_contact(local_username)),
        ('expires',str(expires)),
    ],addr=addr),addr,transport,conn)
    _write_state()

# ── Outbound: local client -> IMS ──────────────────────────────────────────────
def _on_local_invite(msg,addr,transport='udp',conn=None):
    lc_id=_gh(msg,'call-id'); from_val=_gh(msg,'from')
    to_val=_gh(msg,'to'); cseq_val=_gh(msg,'cseq'); body=msg.get('body',b'')

    with _ureg_lock:
        up_reg = _ureg.get('registered', False)
    if not up_reg:
        log.warning('INVITE rejected — upstream not registered with IMS')
        _send(_respond(msg,480,'Temporarily Unavailable',addr=addr),addr,transport,conn)
        return

    _send(_respond(msg,100,'Trying',addr=addr),addr,transport,conn)

    fl_parts=msg.get('first_line','').split()
    local_ruri=fl_parts[1] if len(fl_parts)>1 else _uri(to_val)
    up_req_uri=_up_uri(local_ruri)
    log.info(f'INVITE {local_ruri} -> {up_req_uri}')

    msg['headers']=[(n,v) for n,v in msg.get('headers',[])
                    if n not in ('route','record-route')]

    dlg=_Dialog('out')
    dlg.lc_id=lc_id; dlg.lc_addr=addr; dlg.lc_from=from_val
    dlg.lc_to=to_val; dlg.lc_tag=new_tag(); dlg.lc_cseq=_cseq_num(cseq_val)
    dlg.lc_vias=[v for n,v in msg.get('headers',[]) if n=='via']
    dlg.lc_transport=transport; dlg.lc_conn=conn
    dlg.lc_body=body  # stored for potential auth retry
    dlg.up_id=new_call_id(); dlg.up_branch=new_branch()
    dlg.up_identity=''
    dlg.up_from=f'<sip:{SIP_USER}@{SIP_DOMAIN}>;tag={new_tag()}'
    dlg.up_to=f'<{up_req_uri}>'; dlg.up_cseq=1

    up_hdrs=[
        ('via',         f'SIP/2.0/UDP {VOIP_IP}:{LOCAL_PORT};branch={dlg.up_branch};rport'),
        ('from',        dlg.up_from),('to',dlg.up_to),
        ('call-id',     dlg.up_id),('cseq','1 INVITE'),
        ('contact',     f'<sip:{SIP_USER}@{VOIP_IP}:{LOCAL_PORT}>'),
        ('max-forwards','70'),('user-agent','voipd-b2bua/1.4'),
        ('allow',       'INVITE,ACK,BYE,CANCEL,UPDATE,INFO,SUBSCRIBE,NOTIFY,REFER,MESSAGE,OPTIONS'),
        ('supported',   'path,replaces,timer,norefersub'),
        ('session-expires','1800'),
        ('min-se',      '90'),
    ]
    with _ureg_lock:
        dlg.up_identity=_preferred_identity(_ureg.get('associated_uri',[]))
        if dlg.up_identity:
            up_hdrs.append(('p-preferred-identity', f'<{dlg.up_identity}>'))
        up_hdrs.append(('p-called-party-id', f'<{up_req_uri}>'))
        for sr in _ureg.get('service_route',[]):
            up_hdrs.insert(0,('route',sr))
        dlg.up_routes = [sr for sr in _ureg.get('service_route',[])]
        # Pre-emptive Proxy-Authorization: IMS requires auth on INVITE.
        # It does NOT send 407 — it returns 403 'Invalid User' if auth is absent.
        # Use invite_nc (separate from REGISTER nc=1) so REGISTER's nc=1 never
        # conflicts with INVITE nc=2,3,4... invite_nc resets to 2 on new nonce.
        if _ureg.get('challenge'):
            nc = _ureg.get('invite_nc', 2)
            _ureg['invite_nc'] = nc + 1
            h = _build_auth('INVITE', up_req_uri, SIP_USER, SIP_PASS,
                            _ureg['challenge'], 'Proxy-Authorization', nc_val=nc)
            if h: up_hdrs.append(('proxy-authorization', h))
            log.debug(f'INVITE pre-auth: nonce=...{_ureg["challenge"][-8:]} nc={nc:08x}')
    if body: up_hdrs.append(('content-type','application/sdp'))

    with _dlg_lock:
        _dlg_by_up[dlg.up_id]=dlg; _dlg_by_lc[dlg.lc_id]=dlg

    raw=_build(f'INVITE {up_req_uri} SIP/2.0',up_hdrs,body)
    _log_sip('>>UP',(PROXY_IP,PROXY_PORT),raw)
    _send(raw,(PROXY_IP,PROXY_PORT))

def _on_upstream_invite_resp(msg,raw=b''):
    st=_status(msg); call_id=_gh(msg,'call-id')
    _log_sip('<<UP',(PROXY_IP,PROXY_PORT),raw)
    with _dlg_lock: dlg=_dlg_by_up.get(call_id)
    if not dlg: return
    with dlg.lock:
        if st==100: return

        # ── INVITE auth challenge (401/407) — retry once with credentials ──────
        if st in (401, 407) and not dlg.up_auth_tried:
            dlg.up_auth_tried = True
            chal_hdr = 'www-authenticate' if st == 401 else 'proxy-authenticate'
            chal = _gh(msg, chal_hdr)
            if chal:
                # SIP requires ACKing INVITE error responses
                ack_hdrs=[
                    ('via',   f'SIP/2.0/UDP {VOIP_IP}:{LOCAL_PORT};branch={dlg.up_branch}'),
                    ('from',  dlg.up_from),
                    ('to',    dlg.up_to + (f';tag={dlg.up_to_tag}' if dlg.up_to_tag else '')),
                    ('call-id', dlg.up_id), ('cseq', f'1 ACK'), ('max-forwards','70'),
                ]
                _send(_build(f'ACK {_uri(dlg.up_to)} SIP/2.0', ack_hdrs), (PROXY_IP, PROXY_PORT))
                auth_key = 'authorization' if st == 401 else 'proxy-authorization'
                h = _build_auth('INVITE', _uri(dlg.up_to), SIP_USER, SIP_PASS, chal,
                                'Authorization' if st == 401 else 'Proxy-Authorization',
                                nc_val=1)
                if h:
                    dlg.up_branch = new_branch()
                    dlg.up_cseq   = 2
                    retry_hdrs=[
                        ('via',     f'SIP/2.0/UDP {VOIP_IP}:{LOCAL_PORT};branch={dlg.up_branch};rport'),
                        ('from',    dlg.up_from), ('to', dlg.up_to),
                        ('call-id', dlg.up_id),   ('cseq', f'{dlg.up_cseq} INVITE'),
                        ('contact', f'<sip:{SIP_USER}@{VOIP_IP}:{LOCAL_PORT}>'),
                        ('max-forwards','70'), ('user-agent','voipd-b2bua/1.4'),
                        ('allow',   'INVITE,ACK,BYE,CANCEL,UPDATE,INFO,SUBSCRIBE,NOTIFY,REFER,MESSAGE,OPTIONS'),
                        ('supported','path,replaces,timer,norefersub'),
                        ('session-expires','1800'),
                        ('min-se',  '90'),
                        (auth_key,  h),
                    ]
                    if getattr(dlg, 'up_identity', ''):
                        retry_hdrs.append(('p-preferred-identity', f'<{dlg.up_identity}>'))
                    retry_hdrs.append(('p-called-party-id', f'<{_uri(dlg.up_to)}>'))
                    with _ureg_lock:
                        for sr in _ureg.get('service_route',[]):
                            retry_hdrs.insert(0,('route',sr))
                        dlg.up_routes = list(_ureg.get('service_route',[]))
                    if dlg.lc_body: retry_hdrs.append(('content-type','application/sdp'))
                    _send(_build(f'INVITE {_uri(dlg.up_to)} SIP/2.0', retry_hdrs, dlg.lc_body),
                          (PROXY_IP, PROXY_PORT))
                    log.info(f'INVITE auth retry ({st}) for {dlg.up_id}')
                    return   # wait for retry response; don't relay challenge to client
        # ── end auth retry ─────────────────────────────────────────────────────

        if st>=200 and not dlg.up_to_tag: dlg.up_to_tag=_tag(_gh(msg,'to'))
        if st>=300:
            # ACK non-2xx finals on the IMS leg immediately; the client's ACK
            # is for the downstream dialog and does not complete this leg.
            ack_to = dlg.up_to
            if dlg.up_to_tag and 'tag=' not in ack_to:
                ack_to += f';tag={dlg.up_to_tag}'
            ack_hdrs=[
                ('via',         f'SIP/2.0/UDP {VOIP_IP}:{LOCAL_PORT};branch={new_branch()};rport'),
                ('from',        dlg.up_from),
                ('to',          ack_to),
                ('call-id',     dlg.up_id),
                ('cseq',        f'{dlg.up_cseq} ACK'),
                ('max-forwards','70'),
            ]
            for route in reversed(getattr(dlg, 'up_routes', []) or []):
                ack_hdrs.insert(0, ('route', route))
            _send(_build(f'ACK {_uri(dlg.up_to)} SIP/2.0', ack_hdrs),
                  (PROXY_IP, PROXY_PORT))
        if st==200 and not dlg.up_contact:
            # Store Contact from 200 OK for RFC-3261-compliant ACK routing
            cont = _gh(msg, 'contact')
            if cont:
                dlg.up_contact = _uri(cont)
                m = re.search(r'sips?:[^@]+@([^;>\s]+)', dlg.up_contact)
                if m:
                    hp = m.group(1).rstrip('>')
                    if ':' in hp:
                        h, p = hp.rsplit(':', 1)
                        try: dlg.up_contact_addr = (h, int(p))
                        except ValueError: pass
                    else:
                        dlg.up_contact_addr = (hp, 5060)
        if 180<=st<=199: dlg.state='ringing'
        elif st==200:    dlg.state='established'
        elif st>=400:    dlg.state='terminated'

        parts=msg.get('first_line','').split(None,2)
        reason=parts[2] if len(parts)>2 else 'OK'; body=msg.get('body',b'')
        local_st, local_reason = _map_upstream_final_response(st, reason, msg)
        to_hdr=dlg.lc_to
        if not _tag(to_hdr): to_hdr+=f';tag={dlg.lc_tag}'
        via_hdrs=[('via',v) for v in
                  (dlg.lc_vias or [f'SIP/2.0/UDP {dlg.lc_addr[0]}:{dlg.lc_addr[1]}'])]
        lc_hdrs=via_hdrs+[
            ('from',dlg.lc_from),('to',to_hdr),('call-id',dlg.lc_id),
            ('cseq',f'{dlg.lc_cseq} INVITE'),
            ('contact',_local_contact(_user_from_uri(_uri(dlg.lc_from)))),
        ]
        for hdr_name in ('warning', 'reason'):
            hdr_val = _gh(msg, hdr_name)
            if hdr_val:
                lc_hdrs.append((hdr_name, hdr_val))
        if body: lc_hdrs.append(('content-type','application/sdp'))
        _send(_build(f'SIP/2.0 {local_st} {local_reason}',lc_hdrs,body),
              dlg.lc_addr,dlg.lc_transport,dlg.lc_conn)
        log.info(f'-> {local_st} {local_reason} to {dlg.lc_addr[0]}')
        if st>=400: _cleanup_dlg(dlg)

def _on_local_ack(msg,addr,transport='udp',conn=None):
    call_id=_gh(msg,'call-id')
    with _dlg_lock: dlg=_dlg_by_lc.get(call_id)
    if not dlg: return
    to_hdr=dlg.up_to
    if dlg.up_to_tag and 'tag=' not in to_hdr: to_hdr+=f';tag={dlg.up_to_tag}'
    # RFC 3261 §13.2.2.4: ACK for 2xx MUST go to Contact URI from the 200 OK,
    # not to the proxy. Use stored contact from 200 if available, else fall back.
    ack_target = dlg.up_contact if getattr(dlg, 'up_contact', None) else _up_uri(_uri(to_hdr))
    ack_dest_ip = dlg.up_contact_addr if getattr(dlg, 'up_contact_addr', None) else (PROXY_IP, PROXY_PORT)
    hdrs=[
        ('via',         f'SIP/2.0/UDP {VOIP_IP}:{LOCAL_PORT};branch={new_branch()};rport'),
        ('from',dlg.up_from),('to',to_hdr),('call-id',dlg.up_id),
        ('cseq',f'{dlg.up_cseq} ACK'),('max-forwards','70'),
    ]
    # RFC 3261 §13.2.2.4: ACK for 2xx MUST include same Route as the INVITE
    for rt in getattr(dlg, 'up_routes', []):
        hdrs.insert(1, ('route', rt))
    body=msg.get('body',b'')
    if body: hdrs.append(('content-type','application/sdp'))
    _send(_build(f'ACK {ack_target} SIP/2.0',hdrs,body), ack_dest_ip)

def _on_local_bye(msg,addr,transport='udp',conn=None):
    call_id=_gh(msg,'call-id')
    with _dlg_lock: dlg=_dlg_by_lc.get(call_id)
    if not dlg:
        _send(_respond(msg,481,'Call Does Not Exist',addr=addr),addr,transport,conn); return
    _send(_respond(msg,200,'OK',addr=addr),addr,transport,conn)
    to_hdr=dlg.up_to
    if dlg.up_to_tag and 'tag=' not in to_hdr: to_hdr+=f';tag={dlg.up_to_tag}'
    up_req_uri=_up_uri(_uri(to_hdr))
    hdrs=[
        ('via',         f'SIP/2.0/UDP {VOIP_IP}:{LOCAL_PORT};branch={new_branch()};rport'),
        ('from',dlg.up_from),('to',to_hdr),('call-id',dlg.up_id),
        ('cseq',f'{dlg.up_cseq+1} BYE'),('max-forwards','70'),
    ]
    _send(_build(f'BYE {up_req_uri} SIP/2.0',hdrs),(PROXY_IP,PROXY_PORT))
    log.info('BYE upstream'); _cleanup_dlg(dlg)

def _on_local_cancel(msg,addr,transport='udp',conn=None):
    call_id=_gh(msg,'call-id')
    with _dlg_lock: dlg=_dlg_by_lc.get(call_id)
    _send(_respond(msg,200,'OK',addr=addr),addr,transport,conn)
    if dlg:
        hdrs=[
            ('via',     f'SIP/2.0/UDP {VOIP_IP}:{LOCAL_PORT};branch={dlg.up_branch}'),
            ('from',dlg.up_from),('to',dlg.up_to),
            ('call-id',dlg.up_id),('cseq',f'{dlg.up_cseq} CANCEL'),('max-forwards','70'),
        ]
        _send(_build(f'CANCEL {_uri(dlg.up_to)} SIP/2.0',hdrs),(PROXY_IP,PROXY_PORT))
        _cleanup_dlg(dlg)


def _ack_local_invite_final(dlg, to_hdr):
    hdrs=[
        ('via',     f'SIP/2.0/UDP {VOIP_IP}:{LOCAL_PORT};branch={new_branch()}'),
        ('from',dlg.lc_from),('to',to_hdr),
        ('call-id',dlg.lc_id),('cseq','1 ACK'),('max-forwards','70'),
    ]
    _send(_build(f'ACK {dlg.lc_target or _uri(dlg.lc_to)} SIP/2.0',hdrs),
          dlg.lc_addr,dlg.lc_transport,dlg.lc_conn)


def _on_upstream_cancel(msg,raw=b''):
    call_id=_gh(msg,'call-id')
    _log_sip('<<UP CAN',(PROXY_IP,PROXY_PORT),raw)
    with _forks_lock:
        fork=_forks.get(call_id)

    if not fork:
        _send(_respond(msg,481,'Call Does Not Exist'),(PROXY_IP,PROXY_PORT))
        return

    with fork.lock:
        _send(_respond(msg,200,'OK'),(PROXY_IP,PROXY_PORT))
        if fork.answered or fork.cancelled:
            return

        fork.cancelled=True
        if not fork.up_to_tag:
            fork.up_to_tag=new_tag()
        to_hdr=fork.up_to
        if not _tag(to_hdr):
            to_hdr+=f';tag={fork.up_to_tag}'
        _send(_build('SIP/2.0 487 Request Terminated',
                     _up_invite_resp_hdrs(fork,to_hdr)),
              (PROXY_IP,PROXY_PORT))
        log.info(f'Inbound CANCEL — cancelling {len(fork.legs)} local leg(s)')

        for dlg in list(fork.legs.values()):
            if dlg.state in ('trying','proceeding'):
                dlg.state='cancelled'
                hdrs=[
                    ('via',     f'SIP/2.0/UDP {VOIP_IP}:{LOCAL_PORT};branch={dlg.lc_branch}'),
                    ('from',fork.up_from),('to',dlg.lc_to),
                    ('call-id',dlg.lc_id),('cseq','1 CANCEL'),('max-forwards','70'),
                ]
                _send(_build(f'CANCEL {dlg.lc_target or _uri(dlg.lc_to)} SIP/2.0',hdrs),
                      dlg.lc_addr,dlg.lc_transport,dlg.lc_conn)

# ── Inbound: IMS -> all local clients (parallel fork) ─────────────────────────
def _on_upstream_invite(msg,raw=b''):
    from_val=_gh(msg,'from'); to_val=_gh(msg,'to')
    call_id=_gh(msg,'call-id'); body=msg.get('body',b'')
    up_vias=[v for n,v in msg.get('headers',[]) if n=='via']
    up_req_cseq=_gh(msg,'cseq') or '1 INVITE'
    _log_sip('<<UP IN',(PROXY_IP,PROXY_PORT),raw)
    _send(_respond(msg,100,'Trying'),(PROXY_IP,PROXY_PORT))

    with _regs_lock:
        live=[(aor,r) for aor,r in _regs.items() if r.expires>time.time()]
    if not live:
        log.warning('Inbound INVITE: no local clients -> 480')
        _send(_respond(msg,480,'Temporarily Unavailable',
                        extra=[('to',to_val+f';tag={new_tag()}')]),(PROXY_IP,PROXY_PORT))
        return

    fork = _Fork(call_id, from_val, to_val, up_vias, up_req_cseq)
    with _forks_lock:
        _forks[call_id] = fork

    log.info(f'Inbound INVITE — forking to {len(live)} client(s)')
    for aor, reg in live:
        dlg=_Dialog('in')
        dlg.up_id=call_id; dlg.up_from=from_val; dlg.up_to=to_val
        dlg.up_vias=list(up_vias); dlg.up_req_cseq=up_req_cseq
        dlg.lc_id=new_call_id(); dlg.lc_addr=reg.addr
        dlg.lc_tag=new_tag(); dlg.lc_branch=new_branch()
        dlg.lc_from=from_val; dlg.lc_transport=reg.transport; dlg.lc_conn=reg.conn
        dlg.lc_target=_uri(reg.contact) if reg.contact else f'sip:{_local_user(reg.username)}@{reg.addr[0]}:{reg.addr[1]}'
        dlg.lc_to=reg.aor or _local_contact(reg.username)
        if '<' not in dlg.lc_to:
            dlg.lc_to=f'<{_uri(dlg.lc_to)}>'
        fork.legs[dlg.lc_id] = dlg
        with _dlg_lock:
            _dlg_by_lc[dlg.lc_id] = dlg
        lc_hdrs=[
            ('via',     f'SIP/2.0/UDP {VOIP_IP}:{LOCAL_PORT};branch={dlg.lc_branch};rport'),
            ('from',    from_val), ('to', dlg.lc_to), ('call-id', dlg.lc_id),
            ('cseq',    '1 INVITE'),
            ('contact', _local_contact(reg.username)),
            ('max-forwards','70'),
        ]
        if body: lc_hdrs.append(('content-type','application/sdp'))
        _send(_build(f'INVITE {dlg.lc_target} SIP/2.0',lc_hdrs,body),
              reg.addr,reg.transport,reg.conn)
        log.info(f'  -> fork leg {dlg.lc_id[:8]} to {reg.addr[0]}:{reg.addr[1]} ({reg.transport.upper()})')

def _on_local_invite_resp(msg,addr,transport='udp',conn=None):
    st=_status(msg); call_id=_gh(msg,'call-id'); body=msg.get('body',b'')
    with _dlg_lock: dlg=_dlg_by_lc.get(call_id)
    if not dlg or dlg.direction!='in': return

    parts=msg.get('first_line','').split(None,2)
    reason=parts[2] if len(parts)>2 else 'OK'
    to_resp=_gh(msg,'to') or dlg.lc_to
    dlg.lc_peer_to = to_resp

    with _forks_lock:
        fork = _forks.get(dlg.up_id)

    if fork is None:
        # No fork state — single-client path (shouldn't happen but handle gracefully)
        if st==200:
            to_hdr=dlg.up_to
            if not _tag(to_hdr): to_hdr+=f';tag={dlg.lc_tag}'
            up_hdrs=_up_invite_resp_hdrs(dlg, to_hdr)
            if body: up_hdrs.append(('content-type','application/sdp'))
            _send(_build(f'SIP/2.0 200 OK',up_hdrs,body),(PROXY_IP,PROXY_PORT))
            with _dlg_lock: _dlg_by_up[dlg.up_id]=dlg
        elif st>=400:
            _ack_local_invite_final(dlg, to_resp)
            _cleanup_dlg(dlg)
        return

    with fork.lock:
        # ── Provisional (180/183) — relay first one upstream ─────────────────
        if 180<=st<=199:
            dlg.state='proceeding'
            if not fork.answered and not fork.cancelled and not fork.prov_sent:
                fork.prov_sent = True
                if not fork.up_to_tag:
                    fork.up_to_tag = dlg.lc_tag
                to_hdr = fork.up_to
                if not _tag(to_hdr):
                    to_hdr += f';tag={fork.up_to_tag}'
                up_hdrs=_up_invite_resp_hdrs(fork, to_hdr)
                if body: up_hdrs.append(('content-type','application/sdp'))
                _send(_build(f'SIP/2.0 {st} {reason}',up_hdrs,body),(PROXY_IP,PROXY_PORT))
            return

        # ── 200 OK — first one wins ───────────────────────────────────────────
        if st==200:
            if fork.cancelled:
                ack_hdrs=[
                    ('via',     f'SIP/2.0/UDP {VOIP_IP}:{LOCAL_PORT};branch={new_branch()}'),
                    ('from',dlg.lc_from),('to',to_resp),
                    ('call-id',dlg.lc_id),('cseq','1 ACK'),('max-forwards','70'),
                ]
                _send(_build(f'ACK {dlg.lc_target or _uri(dlg.lc_to)} SIP/2.0',ack_hdrs),
                      dlg.lc_addr,dlg.lc_transport,dlg.lc_conn)
                bye_hdrs=[
                    ('via',     f'SIP/2.0/UDP {VOIP_IP}:{LOCAL_PORT};branch={new_branch()}'),
                    ('from',dlg.lc_from),('to',to_resp),
                    ('call-id',dlg.lc_id),('cseq','2 BYE'),('max-forwards','70'),
                ]
                _send(_build(f'BYE {dlg.lc_target or _uri(dlg.lc_to)} SIP/2.0',bye_hdrs),
                      dlg.lc_addr,dlg.lc_transport,dlg.lc_conn)
                dlg.state='terminated'
                fork.legs.pop(call_id, None)
                with _dlg_lock: _dlg_by_lc.pop(call_id,None)
                if not fork.legs:
                    with _forks_lock: _forks.pop(fork.up_id, None)
                    with _dlg_lock:   _dlg_by_up.pop(fork.up_id, None)
            elif not fork.answered:
                fork.answered = True
                dlg.state = 'established'
                with _dlg_lock:
                    _dlg_by_up[fork.up_id] = dlg  # winning leg
                if not fork.up_to_tag:
                    fork.up_to_tag = dlg.lc_tag
                to_hdr = fork.up_to
                if not _tag(to_hdr):
                    to_hdr += f';tag={fork.up_to_tag}'
                up_hdrs=_up_invite_resp_hdrs(fork, to_hdr)
                if body: up_hdrs.append(('content-type','application/sdp'))
                _send(_build(f'SIP/2.0 200 OK',up_hdrs,body),(PROXY_IP,PROXY_PORT))
                log.info(f'Fork answered by {dlg.lc_addr[0]} — cancelling other legs')
                # CANCEL all other pending legs
                for lid, other in list(fork.legs.items()):
                    if lid != call_id and other.state in ('trying', 'proceeding'):
                        other.state = 'cancelled'
                        hdrs=[
                            ('via',     f'SIP/2.0/UDP {VOIP_IP}:{LOCAL_PORT};branch={other.lc_branch}'),
                            ('from',fork.up_from),('to',other.lc_to),
                            ('call-id',other.lc_id),('cseq','1 CANCEL'),('max-forwards','70'),
                        ]
                        _send(_build(f'CANCEL {other.lc_target or _uri(other.lc_to)} SIP/2.0',hdrs),
                              other.lc_addr,other.lc_transport,other.lc_conn)
                        with _dlg_lock: _dlg_by_lc.pop(lid,None)
                # Keep winning leg in fork.legs for BYE routing
                fork.legs = {call_id: dlg}
            else:
                # Late 200 from another leg — ACK then BYE to refuse it
                ack_hdrs=[
                    ('via',     f'SIP/2.0/UDP {VOIP_IP}:{LOCAL_PORT};branch={new_branch()}'),
                    ('from',dlg.lc_from),('to',to_resp),
                    ('call-id',dlg.lc_id),('cseq','1 ACK'),('max-forwards','70'),
                ]
                _send(_build(f'ACK {dlg.lc_target or _uri(dlg.lc_to)} SIP/2.0',ack_hdrs),
                      dlg.lc_addr,dlg.lc_transport,dlg.lc_conn)
                bye_hdrs=[
                    ('via',     f'SIP/2.0/UDP {VOIP_IP}:{LOCAL_PORT};branch={new_branch()}'),
                    ('from',dlg.lc_from),('to',to_resp),
                    ('call-id',dlg.lc_id),('cseq','2 BYE'),('max-forwards','70'),
                ]
                _send(_build(f'BYE {dlg.lc_target or _uri(dlg.lc_to)} SIP/2.0',bye_hdrs),
                      dlg.lc_addr,dlg.lc_transport,dlg.lc_conn)
                with _dlg_lock: _dlg_by_lc.pop(call_id,None)
            return

        # ── Error (4xx-6xx) — remove this leg; if all failed, notify upstream ─
        if st>=400:
            _ack_local_invite_final(dlg, to_resp)
            dlg.state = 'terminated'
            fork.legs.pop(call_id, None)
            with _dlg_lock: _dlg_by_lc.pop(call_id,None)
            if not fork.legs and not fork.answered:
                if fork.cancelled:
                    with _forks_lock: _forks.pop(fork.up_id, None)
                    with _dlg_lock:   _dlg_by_up.pop(fork.up_id, None)
                    return
                # All legs rejected — tell IMS nobody answered
                if not fork.up_to_tag:
                    fork.up_to_tag = new_tag()
                to_hdr = fork.up_to
                if not _tag(to_hdr):
                    to_hdr += f';tag={fork.up_to_tag}'
                up_hdrs=_up_invite_resp_hdrs(fork, to_hdr)
                _send(_build('SIP/2.0 480 Temporarily Unavailable', up_hdrs),
                      (PROXY_IP, PROXY_PORT))
                with _forks_lock: _forks.pop(fork.up_id, None)
                with _dlg_lock:   _dlg_by_up.pop(fork.up_id, None)


def _on_upstream_ack(msg):
    call_id=_gh(msg,'call-id')
    with _dlg_lock: dlg=_dlg_by_up.get(call_id)
    if not dlg or dlg.direction!='in': return
    to_hdr = dlg.lc_peer_to or (dlg.lc_to+f';tag={dlg.lc_tag}')
    hdrs=[
        ('via',     f'SIP/2.0/UDP {VOIP_IP}:{LOCAL_PORT};branch={new_branch()}'),
        ('from',dlg.lc_from),('to',to_hdr),
        ('call-id',dlg.lc_id),('cseq','1 ACK'),('max-forwards','70'),
    ]
    _send(_build(f'ACK {dlg.lc_target or _uri(dlg.lc_to)} SIP/2.0',hdrs),
          dlg.lc_addr,dlg.lc_transport,dlg.lc_conn)

def _on_upstream_bye(msg):
    call_id=_gh(msg,'call-id')
    with _dlg_lock: dlg=_dlg_by_up.get(call_id)
    if not dlg: return
    _send(_respond(msg,200,'OK'),(PROXY_IP,PROXY_PORT))
    if dlg.lc_addr:
        to_hdr = dlg.lc_peer_to or (dlg.lc_to+f';tag={dlg.lc_tag}')
        hdrs=[
            ('via',     f'SIP/2.0/UDP {VOIP_IP}:{LOCAL_PORT};branch={new_branch()}'),
            ('from',dlg.lc_from),('to',to_hdr),
            ('call-id',dlg.lc_id),('cseq',f'{dlg.lc_cseq+1} BYE'),('max-forwards','70'),
        ]
        _send(_build(f'BYE {dlg.lc_target or _uri(dlg.lc_to)} SIP/2.0',hdrs),
              dlg.lc_addr,dlg.lc_transport,dlg.lc_conn)
    _cleanup_dlg(dlg); log.info('Inbound BYE relayed')

def _cleanup_dlg(dlg):
    with _dlg_lock:
        _dlg_by_up.pop(dlg.up_id,None)
        _dlg_by_lc.pop(dlg.lc_id,None)
    with _forks_lock:
        _forks.pop(dlg.up_id,None)
    dlg.state='terminated'

# ── Main dispatcher ─────────────────────────────────────────────────────────────
def _dispatch(data, addr, transport='udp', conn=None):
    msg=_parse(data)
    if not msg: return
    _log_sip(f'<<{"UP" if addr[0]==PROXY_IP else "LC"}',addr,data)
    method=_method(msg); st=_status(msg); cseq_m=_cseq_method(_gh(msg,'cseq'))

    if addr[0]==PROXY_IP:
        if st and cseq_m=='REGISTER':     _on_register_resp(msg,data)
        elif method=='INVITE':             _on_upstream_invite(msg,data)
        elif method=='CANCEL':             _on_upstream_cancel(msg,data)
        elif method=='ACK':                _on_upstream_ack(msg)
        elif method=='BYE':                _on_upstream_bye(msg)
        elif method in ('NOTIFY','INFO','MESSAGE','UPDATE','SUBSCRIBE'):
            _send(_respond(msg,200,'OK'),(PROXY_IP,PROXY_PORT))
            log.debug(f'Absorbed {method} from IMS')
        elif method=='OPTIONS':
            _send(_respond(msg,200,'OK',extra=[('allow','INVITE,ACK,BYE,CANCEL,OPTIONS')]),
                  addr,transport,conn)
        elif st:                           _on_upstream_invite_resp(msg,data)
        else: log.debug(f'UP unhandled:{method or st} from {addr[0]}')
    else:
        if   method=='REGISTER': _on_local_register(msg,addr,transport,conn)
        elif method=='INVITE':   _on_local_invite(msg,addr,transport,conn)
        elif method=='ACK':      _on_local_ack(msg,addr,transport,conn)
        elif method=='BYE':      _on_local_bye(msg,addr,transport,conn)
        elif method=='CANCEL':   _on_local_cancel(msg,addr,transport,conn)
        elif method=='OPTIONS':
            _send(_respond(msg,200,'OK',extra=[
                ('allow','INVITE,ACK,BYE,CANCEL,OPTIONS,REGISTER')],addr=addr),
                  addr,transport,conn)
        elif st:                 _on_local_invite_resp(msg,addr,transport,conn)
        else: log.debug(f'LC unhandled:{method or st} from {addr[0]}')

# ── State file ─────────────────────────────────────────────────────────────────
def _write_state():
    os.makedirs(STATE_DIR,exist_ok=True)
    with _regs_lock:
        # Deduplicate display by username@ip — a device registering on both
        # UDP and TCP gets two slots in _regs but shows as one client.
        seen = set()
        clients = []
        for r in _regs.values():
            if r.expires > time.time():
                key = f'{r.username}@{r.addr[0]}'
                if key not in seen:
                    seen.add(key)
                    clients.append(key)
    try:
        with open(f'{STATE_DIR}/b2bua_status','w') as f:
            f.write(f'upstream_registered={_ureg.get("registered",False)}\n')
            f.write(f'upstream_user={SIP_USER}\n')
            f.write(f'upstream_domain={SIP_DOMAIN}\n')
            f.write(f'upstream_proxy={PROXY_IP}:{PROXY_PORT}\n')
            f.write(f'local_port={LOCAL_PORT}\n')
            f.write(f'voip_ip={VOIP_IP}\n')
            f.write(f'local_clients={",".join(clients)}\n')
    except Exception: pass

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    global _udp_sock, _tcp_srv
    missing=[k for k,v in [('B2BUA_PROXY',PROXY_IP),('B2BUA_DOMAIN',SIP_DOMAIN),
                             ('B2BUA_USER',SIP_USER),('B2BUA_PASS',SIP_PASS),
                             ('B2BUA_VOIP_IP',VOIP_IP)] if not v]
    if missing:
        log.error(f'Missing env vars: {", ".join(missing)}'); sys.exit(1)

    log.info('voipd-b2bua v1.4 starting (UDP + TCP)')
    log.info(f'  Local  : 0.0.0.0:{LOCAL_PORT}  (LAN clients, UDP + TCP)')
    log.info(f'  IMS    : {SIP_USER}@{SIP_DOMAIN} via {PROXY_IP}:{PROXY_PORT} (UDP)')
    log.info(f'  Inbound: parallel fork to all registered clients')
    if LOCAL_PASS: log.info('  Auth   : local client password required')

    _udp_sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    _udp_sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    try: _udp_sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEPORT,1)
    except (AttributeError,OSError): pass
    # Mark all outgoing UDP packets with the voip fwmark so the kernel routes
    # them via the voip routing table (ip rule fwmark 0x1e0000 lookup voip).
    # This is required because the socket is bound to 0.0.0.0 — without an
    # explicit source IP the kernel would otherwise route upstream SIP via the
    # default WAN (ppp0) instead of the voip VLAN interface.
    try: _udp_sock.setsockopt(socket.SOL_SOCKET,socket.SO_MARK,0x1e0000)
    except (AttributeError,OSError) as e: import logging; logging.getLogger('voipd.b2bua').warning(f'SO_MARK unavailable: {e}')
    except (AttributeError,OSError): pass
    _udp_sock.bind(('0.0.0.0',LOCAL_PORT))

    threading.Thread(target=_tcp_server_loop,daemon=True,name='tcp-srv').start()

    _write_state()

    threading.Thread(target=_register_loop,daemon=True,name='reg').start()
    threading.Thread(target=lambda:[(_write_state(),time.sleep(30)) for _ in iter(int,1)],
                     daemon=True,name='state').start()

    def _stop(sig,_): log.info('B2BUA shutting down'); sys.exit(0)
    signal.signal(signal.SIGTERM,_stop); signal.signal(signal.SIGINT,_stop)

    log.info('B2BUA ready')
    _loop_ready.set()

    while True:
        try:
            data,addr=_udp_sock.recvfrom(65535)
            threading.Thread(target=_dispatch,args=(data,addr,'udp',None),daemon=True).start()
        except OSError: break
        except Exception as exc: log.debug(f'udp recv:{exc}')

if __name__=='__main__':
    main()
