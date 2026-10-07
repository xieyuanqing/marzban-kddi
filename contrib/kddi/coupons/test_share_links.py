import json,tempfile,unittest,urllib.parse
from pathlib import Path
from share_links import ShareLinks,ShareLinkError,CloudflareError
class FakeCF:
    def __init__(self):self.records={};self.writes=0;self.fail=False
    def account_request(self,method,path,data=None,raw=False):
        if self.fail:raise CloudflareError('Cloudflare API HTTP 503')
        if method=='PUT':self.writes+=1;self.records[path]=json.loads(data);return None
        if method=='GET':
            if path not in self.records:raise CloudflareError('Cloudflare API HTTP 404')
            return self.records[path]
        if method=='DELETE':self.records.pop(path,None);return None
        raise AssertionError(method)
class ShareLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.cfg=root/'config.json';self.cfg.write_text(json.dumps({'public_url':'https://share.example.pages.dev','origin_url':'https://origin.example','kv_namespace_id':'test-id','local_db':str(root/'private'/'aliases.sqlite3')}))
        self.cf=FakeCF();self.gateway=ShareLinks(str(self.cfg),cloudflare=self.cf)
        self.raw='https://origin.example/sub/'+'a'*40
    def tearDown(self):self.gateway.close();self.tmp.cleanup()
    def test_short_opaque_url(self):
        u=self.gateway.ensure('kc_one',self.raw);self.assertRegex(u,r'^https://share\.example\.pages\.dev/s/[A-Za-z0-9_-]{22}$');self.assertNotIn('origin.example',u);self.assertEqual(self.cf.writes,1)
    def test_idempotent_no_extra_kv_writes(self):
        u=self.gateway.ensure('kc_one',self.raw);self.assertEqual(u,self.gateway.ensure('kc_one',self.raw));self.assertEqual(self.cf.writes,1)
    def test_different_users_different_alias(self):self.assertNotEqual(self.gateway.ensure('kc_one',self.raw),self.gateway.ensure('kc_two',self.raw))
    def test_invalid_origin_rejected(self):
        with self.assertRaises(ShareLinkError):self.gateway.ensure('kc_one',self.raw.replace('origin.example','evil.example'))
        self.assertEqual(self.cf.writes,0)
    def test_management_path_rejected(self):
        with self.assertRaises(ShareLinkError):self.gateway.ensure('kc_one','https://origin.example/api/users')
    def test_query_rejected(self):
        with self.assertRaises(ShareLinkError):self.gateway.ensure('kc_one',self.raw+'?url=https://evil.example')
    def test_kv_failure_retains_retryable_alias(self):
        self.cf.fail=True
        with self.assertRaises(ShareLinkError):self.gateway.ensure('kc_one',self.raw)
        row=self.gateway.db.execute('SELECT alias,synced FROM aliases').fetchone();self.assertEqual(row[1],0)
        self.cf.fail=False;u=self.gateway.ensure('kc_one',self.raw);self.assertTrue(u.endswith(row[0]));self.assertEqual(self.cf.writes,1)
    def test_path_update_preserves_share_url(self):
        u=self.gateway.ensure('kc_one',self.raw);self.assertEqual(u,self.gateway.ensure('kc_one',self.raw.replace('a'*40,'b'*40)));self.assertEqual(self.cf.writes,2)
    def test_forget_exact_readback(self):
        self.gateway.ensure('kc_one',self.raw);self.gateway.forget('kc_one');self.assertEqual(self.cf.records,{});self.assertEqual(self.gateway.db.execute('SELECT count(*) FROM aliases').fetchone()[0],0)
if __name__=='__main__':unittest.main()
