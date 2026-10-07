#!/usr/bin/env python3
"""Deploy only the explicitly configured Pages/KV project; no local production config mutation."""
import argparse,json,os,re,shutil,subprocess,urllib.parse
from pathlib import Path
import cf_api
from cf_api import Cloudflare,CloudflareError

def save(path,config):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(config,indent=2)+'\n');tmp.chmod(0o600);tmp.replace(path)

def deploy(config_path):
    path=Path(config_path).resolve();config=json.loads(path.read_text())
    project=config['project_name']
    if not re.fullmatch('[a-z0-9][a-z0-9-]{2,57}[a-z0-9]',project):raise ValueError('Set a valid, dedicated Pages project name')
    origin=urllib.parse.urlparse(config['origin_url'])
    if origin.scheme!='https' or not origin.netloc or origin.path not in ('','/') or origin.query or origin.fragment:
        raise ValueError('origin_url must be a root HTTPS origin')
    if origin.hostname.endswith('.invalid') or 'REPLACE' in config['origin_url']:
        raise ValueError('Fill the private origin configuration first')
    cf_api.CREDENTIALS=config.get('credentials_file',cf_api.CREDENTIALS)
    cf=Cloudflare();host=project+'.pages.dev'
    if not config.get('kv_namespace_id') or config['kv_namespace_id'].startswith('REPLACE'):
        title=project+'-aliases';found=None
        for page in range(1,21):
            items=cf.account_request('GET',f'storage/kv/namespaces?per_page=100&page={page}')
            found=next((i for i in items if i['title']==title),None)
            if found or len(items)<100:break
        else:raise RuntimeError('Namespace list exceeded bounded scan; configure its exact ID')
        ns=found or cf.account_request('POST','storage/kv/namespaces',{'title':title})
        config['kv_namespace_id']=ns['id'];save(path,config)
    production={'compatibility_date':config.get('compatibility_date','2026-10-01'),
        'compatibility_flags':[],'fail_open':False,
        'env_vars':{'PUBLIC_HOST':{'type':'plain_text','value':host},
                    'UPSTREAM_ORIGIN':{'type':'secret_text','value':config['origin_url']}},
        'kv_namespaces':{'SUB_LINKS':{'namespace_id':config['kv_namespace_id']}}}
    payload={'name':project,'production_branch':'main','deployment_configs':{'production':production,
        'preview':{'compatibility_date':production['compatibility_date'],'fail_open':False,'env_vars':{},'kv_namespaces':{}}}}
    try:cf.account_request('GET','pages/projects/'+project)
    except CloudflareError as e:
        if 'HTTP 404' not in str(e):raise
        cf.account_request('POST','pages/projects',payload)
    else:cf.account_request('PATCH','pages/projects/'+project,{'deployment_configs':payload['deployment_configs']})
    check=cf.account_request('GET','pages/projects/'+project)
    assert check['subdomain']==host and check['production_branch']=='main'
    assert check['deployment_configs']['production']['kv_namespaces']['SUB_LINKS']['namespace_id']==config['kv_namespace_id']
    assert check['deployment_configs']['production']['env_vars']['UPSTREAM_ORIGIN']['type']=='secret_text'
    npx=shutil.which('npx')
    if not npx:raise RuntimeError('Install Node/npm/npx before deployment')
    env=dict(os.environ,CLOUDFLARE_API_TOKEN=cf.token,CLOUDFLARE_ACCOUNT_ID=cf.account,
             CI='true',WRANGLER_SEND_METRICS='false',NO_COLOR='1',NODE_OPTIONS='--max-old-space-size=384')
    args=[npx,'--yes','wrangler@4.130.0','pages','deploy',str(Path(__file__).with_name('site')),
          '--project-name',project,'--branch','main','--commit-dirty=true']
    if hasattr(os,'sched_getaffinity') and shutil.which('taskset') and shutil.which('nice'):
        args=['nice','-n','10','taskset','-c',str(min(os.sched_getaffinity(0)))]+args
    r=subprocess.run(args,cwd=Path(__file__).parent,env=env,capture_output=True,text=True,timeout=480)
    log=path.with_name('pages-deploy.log');log.write_text(r.stdout+'\n'+r.stderr);log.chmod(0o600)
    if r.returncode:raise RuntimeError('Deployment failed; inspect private pages-deploy.log')
    result=cf.account_request('GET','pages/projects/'+project)
    d=result.get('canonical_deployment') or result.get('latest_deployment')
    assert d and d['environment']=='production' and d['latest_stage']['status']=='success'
    exact=cf.account_request('GET','pages/projects/'+project+'/deployments/'+d['id'])
    assert exact['latest_stage']['status']=='success'
    config.update(public_url='https://'+host,deployment_id=d['id']);save(path,config)
    print('Verified Pages deployment:',config['public_url'])

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True,help='Private share config outside this repository');p.add_argument('--apply',action='store_true',help='Create/update only this dedicated Pages/KV project')
    a=p.parse_args()
    if not a.apply:p.error('Deployment has external side effects; pass --apply after reviewing the dedicated config')
    deploy(a.config)
if __name__=='__main__':main()
