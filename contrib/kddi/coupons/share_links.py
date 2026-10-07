"""Issue short opaque links via dedicated CF KV; no network on ordinary guard/API polls."""
import json,os,re,secrets,sqlite3,sys,time,urllib.parse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'share'))
from cf_api import Cloudflare,CloudflareError
class ShareLinkError(RuntimeError):pass
class ShareLinks:
    def __init__(self,config_path='/root/.secrets/auth/kddi-share.json',cloudflare=None):
        self.cfg=json.loads(Path(config_path).read_text());self.cf=cloudflare or Cloudflare()
        self.base=self.cfg['public_url'].rstrip('/');self.namespace=self.cfg['kv_namespace_id']
        db=Path(self.cfg['local_db']);db.parent.mkdir(parents=True,exist_ok=True,mode=0o700);db.parent.chmod(0o700)
        self.db=sqlite3.connect(db,timeout=40);db.chmod(0o600)
        self.db.execute('CREATE TABLE IF NOT EXISTS aliases (username TEXT PRIMARY KEY, alias TEXT UNIQUE NOT NULL, origin_path TEXT NOT NULL, synced INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)');self.db.commit()
    def close(self):self.db.close()
    def ensure(self,username,raw_url):
        parsed=urllib.parse.urlparse(raw_url);origin=urllib.parse.urlparse(self.cfg['origin_url'])
        if parsed.query or parsed.fragment or (parsed.netloc and (parsed.netloc!=origin.netloc or parsed.scheme!='https')) or not re.fullmatch(r'/sub/[A-Za-z0-9_.=-]{20,512}',parsed.path):
            raise ShareLinkError('Refuse invalid subscription backend path')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT alias,origin_path,synced FROM aliases WHERE username=?',(username,)).fetchone()
            if row:
                alias,path,synced=row
                if path!=parsed.path:
                    self.db.execute('UPDATE aliases SET origin_path=?,synced=0 WHERE username=?',(parsed.path,username));synced=0
            else:
                alias=secrets.token_urlsafe(16);synced=0
                self.db.execute('INSERT INTO aliases VALUES (?,?,?,?,?)',(username,alias,parsed.path,0,int(time.time())))
            self.db.commit()
        except Exception:self.db.rollback();raise
        if not synced:
            key='s:'+alias;api='storage/kv/namespaces/'+self.namespace+'/values/'+urllib.parse.quote(key,safe='')
            record={'path':parsed.path}
            try:
                self.cf.account_request('PUT',api,json.dumps(record),raw=True)
                actual=self.cf.account_request('GET',api)
                if actual!=record:raise ShareLinkError('KV exact-target read-back mismatch')
            except CloudflareError as e:raise ShareLinkError(str(e)) from None
            self.db.execute('UPDATE aliases SET synced=1 WHERE username=? AND origin_path=?',(username,parsed.path));self.db.commit()
        return self.base+'/s/'+alias
    def forget(self,username):
        row=self.db.execute('SELECT alias FROM aliases WHERE username=?',(username,)).fetchone()
        if not row:return
        api='storage/kv/namespaces/'+self.namespace+'/values/'+urllib.parse.quote('s:'+row[0],safe='')
        self.cf.account_request('DELETE',api)
        try:self.cf.account_request('GET',api)
        except CloudflareError as e:
            if 'HTTP 404' not in str(e):raise
        else:raise ShareLinkError('KV delete did not take effect')
        self.db.execute('DELETE FROM aliases WHERE username=?',(username,));self.db.commit()
