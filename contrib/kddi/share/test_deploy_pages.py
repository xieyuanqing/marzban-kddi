import json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
import deploy_pages as deploy
class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'share.json'
        self.cfg={'project_name':'example-share','origin_url':'https://backend.example','credentials_file':'/not-a-real-credential-file','kv_namespace_id':'fixture-kv','local_db':'/tmp/not-used'}
        self.path.write_text(json.dumps(self.cfg))
    def tearDown(self):self.temp.cleanup()
    def test_placeholder_origin_rejected_before_api(self):
        self.cfg['origin_url']='https://subscription.example.invalid';self.path.write_text(json.dumps(self.cfg))
        with patch.object(deploy,'Cloudflare') as cf:
            with self.assertRaises(ValueError):deploy.deploy(str(self.path))
            cf.assert_not_called()
    def test_existing_project_exact_readback_and_secret_origin(self):
        cf=MagicMock();cf.account='fixture-account';cf.token='fixture-api-token'
        production={'kv_namespaces':{'SUB_LINKS':{'namespace_id':'fixture-kv'}},'env_vars':{'UPSTREAM_ORIGIN':{'type':'secret_text'}}}
        check={'subdomain':'example-share.pages.dev','production_branch':'main','deployment_configs':{'production':production}}
        cf.account_request.side_effect=[{},None,check,{'canonical_deployment':{'id':'fixture-deployment','environment':'production','latest_stage':{'status':'success'}}},{'latest_stage':{'status':'success'}}]
        with patch.object(deploy,'Cloudflare',return_value=cf),patch.object(deploy.shutil,'which',return_value='/usr/bin/npx'),patch.object(deploy.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'fixture log','')) as run:
            deploy.deploy(str(self.path))
            self.assertIn('wrangler@4.130.0',run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs['env']['CLOUDFLARE_API_TOKEN'],'fixture-api-token')
        actual=json.loads(self.path.read_text());self.assertEqual(actual['deployment_id'],'fixture-deployment')
        sent=cf.account_request.call_args_list[1].args[2]['deployment_configs']['production']
        self.assertEqual(sent['env_vars']['UPSTREAM_ORIGIN']['type'],'secret_text');self.assertNotIn('SHARE_ORIGIN_KEY',sent['env_vars'])
        self.assertEqual(cf.account_request.call_args_list[-1].args[1],'pages/projects/example-share/deployments/fixture-deployment')
    def test_wrong_project_readback_stops_before_deploy(self):
        cf=MagicMock();cf.account_request.side_effect=[{},None,{'subdomain':'wrong-project.pages.dev','production_branch':'main'}]
        with patch.object(deploy,'Cloudflare',return_value=cf),patch.object(deploy.subprocess,'run') as run:
            with self.assertRaises(AssertionError):deploy.deploy(str(self.path))
            run.assert_not_called()
if __name__=='__main__':unittest.main()
