#!/usr/bin/env python3
"""Read Xray access records via FIFO; retain only user/peer tuple, not destinations.
Revoke exhausted/expired coupons and terminate their exact TCP sockets. No core restart.
"""
import ipaddress,json,os,re,select,stat,subprocess,threading,time
from pathlib import Path
from coupons import MarzbanClient,load_config,APIError
FIFO=Path('/var/lib/marzban-kddi/access.pipe')
ADDR=str(ipaddress.IPv4Address(os.environ['KDDI_ENTRY_IP'])); PORT=int(os.environ.get('KDDI_ENTRY_PORT','24444'))
if not 1<=PORT<=65535: raise ValueError('Invalid entry port')
PEER=re.compile(r'(?:from\s+)?(?:tcp:)?(\d{1,3}(?:\.\d{1,3}){3}):(\d+)\s+accepted\b')
EMAIL=re.compile(r'\bemail:\s*\d+\.([a-zA-Z0-9_@.-]+)')
sessions={}; revoked=set(); lock=threading.RLock(); counts={'mapped':0,'killed':0,'destroyed':0,'unparsed':0}
STATE=Path('/run/kddi-coupon-guard.json')
AUDIT=Path('/var/lib/marzban-kddi/coupon-audit.jsonl')
gate_closed=None

def gate(close):
    global gate_closed
    if gate_closed is close: return
    existing=subprocess.run(['nft','list','table','inet','kddi_coupon_gate'],capture_output=True)
    commands=[]
    if existing.returncode==0: commands.append('delete table inet kddi_coupon_gate')
    rule='ip daddr '+ADDR+' tcp dport '+str(PORT)+' counter drop' if close else ''
    commands.append('table inet kddi_coupon_gate {\n chain input {\n  type filter hook input priority -5; policy accept;\n'+('  '+rule+';\n' if rule else '')+' }\n}\n')
    subprocess.run(['nft','-f','-'],input='\n'.join(commands)+'\n',text=True,check=True,capture_output=True)
    gate_closed=close

def log(msg): print(msg,flush=True)
def destroy_peer(ip,port):
    ip=str(ipaddress.IPv4Address(ip)); port=int(port)
    if not 1<=port<=65535: raise ValueError('invalid peer port')
    args=['ss','-Knt','src',ADDR,'sport','=',':'+str(PORT),'dst',ip,'dport','=',':'+str(port)]
    r=subprocess.run(args,capture_output=True,text=True,timeout=5)
    if r.returncode: raise RuntimeError('Exact socket termination failed')
    remaining=subprocess.run(['ss','-Hnt','state','established','src',ADDR,'sport','=',':'+str(PORT),'dst',ip,'dport','=',':'+str(port)],capture_output=True,text=True,timeout=5)
    if remaining.returncode or remaining.stdout.strip(): raise RuntimeError('Socket remained established after termination')
    counts['killed']+=1

def parse_line(line):
    # Never persist or print raw access line: it contains destination information.
    if 'accepted' not in line or 'kddi-reality' not in line: return
    p,e=PEER.search(line),EMAIL.search(line)
    if not p or not e:
        counts['unparsed']+=1; return
    username=e.group(1)
    if not username.startswith('kc_'): return
    peer=(str(ipaddress.IPv4Address(p.group(1))),int(p.group(2)))
    with lock:
        sessions[peer]=username; counts['mapped']+=1
        if username in revoked:
            destroy_peer(*peer); sessions.pop(peer,None)

def reader(fd):
    pending=b''
    while True:
        ready,_,_=select.select([fd],[],[],1)
        if not ready: continue
        try: chunk=os.read(fd,65536)
        except BlockingIOError: continue
        if not chunk: continue
        pending+=chunk
        while b'\n' in pending:
            line,pending=pending.split(b'\n',1)
            parse_line(line.decode('utf-8','replace'))
        if len(pending)>262144: pending=b''

def kill_user(username):
    with lock:
        revoked.add(username)
        for peer in [k for k,v in sessions.items() if v==username]:
            destroy_peer(*peer); sessions.pop(peer,None)

def get_all(client):
    result=[]; off=0
    while True:
        data=client.get_users(offset=off,limit=100)
        users=data.get('users',[]); result+=users; off+=len(users)
        if not users or off>=data.get('total',off): return result

def audit(name,reason):
    with AUDIT.open('a') as f:
        f.write(json.dumps({'time':int(time.time()),'username':name,'event':'revoked_and_deleted','reason':reason})+'\n')
    AUDIT.chmod(0o600)

def poll(client):
    users=get_all(client); now=time.time()
    for u in users:
        name=u['username']
        if not name.startswith('kc_'): continue
        status=u['status']; used=u.get('used_traffic') or 0; limit=u.get('data_limit') or 0; expire=u.get('expire') or 0
        expired=bool(expire and expire<=now); limited=bool(limit and used>=limit)
        if status=='on_hold' and not limited: continue
        if status not in ('expired','limited') and not expired and not limited: continue
        reason='quota' if limited or status=='limited' else 'expiry'
        if status not in ('disabled','expired','limited'):
            client.modify_user(name,{'status':'disabled'})
            if client.get_user(name)['status']!='disabled': raise RuntimeError('Disable readback failed')
        # Do not delete until all previously accepted sockets for this user are closed.
        kill_user(name)
        client.delete_user(name)
        try:
            client.get_user(name)
        except APIError as e:
            if e.status_code!=404: raise
        else: raise RuntimeError('Delete readback failed')
        audit(name,reason); counts['destroyed']+=1; log('Coupon revoked and deleted: '+name+' ('+reason+')')
    # Reconcile sessions for manually removed/disabled users, not only automated expiry.
    status_map={u['username']:u['status'] for u in users}
    with lock:
        names=set(sessions.values())
    for name in names:
        if name not in status_map or status_map[name] in ('disabled','limited','expired'):
            kill_user(name)
    state={'time':int(time.time()),'alive':True,'sessions':len(sessions),'counts':dict(counts)}
    tmp=STATE.with_suffix('.tmp'); tmp.write_text(json.dumps(state)); tmp.chmod(0o600); tmp.replace(STATE)

def main():
    FIFO.parent.mkdir(parents=True,exist_ok=True)
    if FIFO.exists():
        if not stat.S_ISFIFO(FIFO.stat().st_mode): raise RuntimeError('Access path is not FIFO')
    else: os.mkfifo(FIFO,0o600)
    FIFO.chmod(0o600)
    gate(True)
    fd=os.open(FIFO,os.O_RDWR|os.O_NONBLOCK)
    # On exceptional guard restart, fail closed for this dedicated coupon port only.
    # This does not touch personal SS2022, other Xray services, SSH, or production sockets.
    subprocess.run(['ss','-Knt','src',ADDR,'sport','=',':'+str(PORT)],check=True,stdout=subprocess.DEVNULL)
    worker=threading.Thread(target=reader,args=(fd,),daemon=True); worker.start()
    client=MarzbanClient(load_config(),timeout=8); last_error=0; last_success=time.time()
    log('Coupon session guard ready; raw destination records are not written to disk')
    while True:
        if not worker.is_alive(): raise RuntimeError('FIFO reader failed')
        try:
            poll(client)
            if counts['unparsed']:
                gate(True)
                subprocess.run(['ss','-Knt','src',ADDR,'sport','=',':'+str(PORT)],check=True,stdout=subprocess.DEVNULL)
            else:
                gate(False)
                last_success=time.time()
        except APIError as e:
            if e.status_code==401: client._token=None
            if time.time()-last_error>30: log('Panel API unavailable; retrying (status '+str(e.status_code)+')'); last_error=time.time()
        except Exception as e:
            if time.time()-last_error>30: log('Guard operation failed: '+type(e).__name__); last_error=time.time()
        if time.time()-last_success>15:
            gate(True)
            subprocess.run(['ss','-Knt','src',ADDR,'sport','=',':'+str(PORT)],check=True,stdout=subprocess.DEVNULL)
        time.sleep(1)
if __name__=='__main__': main()
