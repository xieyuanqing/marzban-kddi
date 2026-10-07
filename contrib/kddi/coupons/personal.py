#!/usr/bin/env python3
"""Create a separate private-owner account, unlimited and without expiration. Never revoke others."""
import argparse,json,sys,urllib.request,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'coupons'))
from coupons import MarzbanClient,load_config
from share_links import ShareLinks

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--username',required=True,help='New unique username starting with personal_owner_')
    p.add_argument('--config',default='/root/.secrets/auth/kddi-marzban.json')
    p.add_argument('--output',default='/root/.secrets/proxy/personal-owner')
    p.add_argument('--apply',action='store_true');a=p.parse_args()
    if not a.username.startswith('personal_owner_') or not a.username.replace('_','').isalnum() or len(a.username)>32:
        p.error('Use a unique personal_owner_ name, <=32 alphanumeric/underscore characters')
    if not a.apply:
        print('Plan: create active owner identity; unlimited quota, no expiration, no hold or auto-delete. No changes made.');return
    output=Path(a.output);output.mkdir(parents=True,exist_ok=True,mode=0o700);output.chmod(0o700)
    marker=output/'identity.json'
    if marker.exists():raise RuntimeError('Identity already exists in this output directory; refuse accidental rotation')
    cfg=load_config(a.config);c=MarzbanClient(cfg)
    u=c.create_user({'username':a.username,'status':'active','expire':None,'data_limit':0,
      'data_limit_reset_strategy':'no_reset','on_hold_expire_duration':None,'on_hold_timeout':None,
      'auto_delete_in_days':None,'next_plan':None,
      'note':json.dumps({'purpose':'owner_personal_only','distribution':False,'plan':'unlimited_no_expiry'}),
      'proxies':{'vless':{'id':str(uuid.uuid4()),'flow':'xtls-rprx-vision'}},'inbounds':{'vless':['kddi-reality']}})
    # Save the exact created identity before network export; partial failures remain recoverable.
    marker.write_text(json.dumps(u,indent=2));marker.chmod(0o600)
    current=c.get_user(a.username)
    assert current['status']=='active' and current['data_limit'] in (0,None) and current['expire'] is None
    assert current['auto_delete_in_days'] is None and current['on_hold_expire_duration'] is None
    if cfg.share_gateway_config:
        gateway=ShareLinks(cfg.share_gateway_config)
        try:url=gateway.ensure(a.username,current['subscription_url'])
        finally:gateway.close()
    else:url=current['subscription_url']
    with urllib.request.urlopen(urllib.request.Request(url,method='HEAD',headers={'User-Agent':'Karing/1.2 platform/android ClashMeta;sing-box'}),timeout=25) as r:
        assert r.status==200 and not r.read()
        values=dict(x.strip().split('=',1) for x in r.headers['subscription-userinfo'].split(';') if '=' in x)
        assert int(values['total'])==0 and int(values['expire'])==0
    dest=output/'subscription.txt';dest.write_text(url+'\n');dest.chmod(0o600)
    dest=output/'vless.txt';dest.write_text('\n'.join(current['links'])+'\n');dest.chmod(0o600)
    print('Created unlimited/no-expiry private identity; deliverables saved under',output)
    print('No old account was revoked. Rotation requires separately verifying old subscription and connection revocation.')
if __name__=='__main__':main()
