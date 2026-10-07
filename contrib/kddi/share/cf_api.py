"""Scoped Cloudflare API helper. Credentials stay in the existing private env file."""
import json,os,shlex,urllib.request,urllib.error
from pathlib import Path
from typing import Any
CREDENTIALS=os.environ.get('KDDI_CF_CREDENTIALS','/root/.secrets/credentials.env')
class CloudflareError(RuntimeError):pass
def credentials():
    result={}
    for line in Path(CREDENTIALS).read_text().splitlines():
        if '=' not in line or line.lstrip().startswith('#'):continue
        key,value=line.removeprefix('export ').split('=',1)
        try:result[key.strip()]=shlex.split(value)[0]
        except (ValueError,IndexError):pass
    return result
class Cloudflare:
    def __init__(self):
        d=credentials();self.account=d['CF_ACCOUNT_ID'];self.token=d['CLOUDFLARE_WORKERS_API_TOKEN']
    def request(self,method,path,data=None,raw=False) -> Any:
        payload=None if data is None else data.encode() if raw else json.dumps(data).encode()
        req=urllib.request.Request('https://api.cloudflare.com/client/v4/'+path,method=method,data=payload,
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'text/plain' if raw else 'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=35) as response:body=response.read()
        except urllib.error.HTTPError as exc:
            status=exc.code;exc.close();raise CloudflareError('Cloudflare API HTTP '+str(status)) from None
        except urllib.error.URLError:raise CloudflareError('Cloudflare API connection failed') from None
        value=json.loads(body)
        if isinstance(value,dict) and 'success' in value:
            if not value['success']:raise CloudflareError('Cloudflare API rejected operation')
            return value.get('result')
        return value
    def account_request(self,method,path,data=None,raw=False):
        return self.request(method,'accounts/'+self.account+'/'+path,data,raw)
