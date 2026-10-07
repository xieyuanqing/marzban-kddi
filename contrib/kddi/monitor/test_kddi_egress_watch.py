import importlib.util
from pathlib import Path
import sqlite3
import subprocess
import unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('watch',str(Path(__file__).with_name('kddi_egress_watch.py')))
w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
class Tests(unittest.TestCase):
    def test_success_no_direct(self):
        with patch.object(w.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'8.8.4.4','')) as p:
            self.assertEqual(w.probe()[0],'8.8.4.4')
            cmd=p.call_args.args[0];self.assertEqual(cmd[cmd.index('--proxy')+1],w.PROXY)
    def test_fallback_same_proxy(self):
        results=[subprocess.CompletedProcess([],22,'',''),subprocess.CompletedProcess([],0,'ip=8.8.4.4\nloc=JP\n','')]
        with patch.object(w.subprocess,'run',side_effect=results) as p:
            self.assertEqual(w.probe()[:2],('8.8.4.4','cloudflare'))
            for call in p.call_args_list:self.assertIn(w.PROXY,call.args[0])
    def test_fail_never_fabricates(self):
        with patch.object(w.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'10.0.0.1','')):
            self.assertIsNone(w.probe()[0]);self.assertTrue(w.probe()[3])
    def test_transition_across_failure(self):
        db=sqlite3.connect(':memory:');db.execute('CREATE TABLE samples(id INTEGER PRIMARY KEY,utc TEXT,beijing TEXT,ip TEXT,source TEXT,elapsed_ms INTEGER,error TEXT,changed INTEGER)')
        results=[('8.8.4.4','ipify',1,None),(None,None,None,'timeout'),('8.8.8.8','ipify',1,None)]
        with patch.object(w,'probe',side_effect=results):
            self.assertFalse(w.sample(db)['changed']);self.assertFalse(w.sample(db)['changed']);self.assertTrue(w.sample(db)['changed'])
        self.assertEqual(db.execute('SELECT COUNT(*) FROM samples').fetchone()[0],3)
        db.close()
if __name__=='__main__':unittest.main()
