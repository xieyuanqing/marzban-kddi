import os,tempfile,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
os.environ.setdefault('KDDI_ENTRY_IP','198.51.100.10')
import session_guard as guard
from coupons import APIError
class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.state=patch.object(guard,'STATE',Path(self.temp.name)/'state.json');self.state.start()
        guard.sessions.clear();guard.revoked.clear()
    def tearDown(self):self.state.stop();self.temp.cleanup()
    def client(self,user):
        c=MagicMock();c.get_users.return_value={'users':[user],'total':1};return c
    def test_unused_inventory_is_not_deleted(self):
        c=self.client({'username':'kc_stock','status':'on_hold','used_traffic':0,'data_limit':1000000000,'expire':None})
        guard.poll(c);c.delete_user.assert_not_called();c.modify_user.assert_not_called()
    def test_unlimited_user_is_not_limited_by_zero(self):
        c=self.client({'username':'kc_unlimited','status':'active','used_traffic':2000000000,'data_limit':0,'expire':None})
        guard.poll(c);c.delete_user.assert_not_called();c.modify_user.assert_not_called()
    def test_personal_owner_exempt_from_coupon_policy(self):
        c=self.client({'username':'personal_owner_test','status':'active','used_traffic':2000000000,'data_limit':None,'expire':None})
        guard.poll(c);c.delete_user.assert_not_called();c.modify_user.assert_not_called()
    def test_expired_coupon_disabled_killed_deleted_and_readback(self):
        u={'username':'kc_expired','status':'active','used_traffic':0,'data_limit':1000000000,'expire':1}
        c=self.client(u);c.get_user.side_effect=[{'status':'disabled'},APIError('gone',status_code=404)]
        with patch.object(guard,'kill_user') as kill,patch.object(guard,'audit'):
            guard.poll(c);kill.assert_called_once_with('kc_expired')
        c.modify_user.assert_called_once_with('kc_expired',{'status':'disabled'});c.delete_user.assert_called_once_with('kc_expired')
if __name__=='__main__':unittest.main()
