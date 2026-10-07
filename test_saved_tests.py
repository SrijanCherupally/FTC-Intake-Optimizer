import tempfile, time, unittest
from pathlib import Path
from dataclasses import asdict
from adaptive import SearchConfig, create_session
from geometry import Geometry
from physics import Settings, Case
from library import Library
from test_studio import result

class SavedTestTests(unittest.TestCase):
    def seed(self,path):
        lib=Library(path); sid=create_session(lib,Geometry(),Settings(duration=3),SearchConfig())
        cases=[Case('Two abreast',angle=i) for i in range(7)]
        for i in range(3):
            cid=lib.add(str(i),Geometry().dict(),asdict(Settings(duration=3)),sid,cid='g'+str(i))
            for label in ('Broad survey','Focus 001'):
                run=lib.run(cid,label,cases)
                lib.save_results(run['id'],[result(c,jam=j<5 or i==0) for j,c in enumerate(cases)])
        return lib,sid

    def test_top_five_snapshot_unique_votes_and_survives_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'db'; lib,sid=self.seed(path)
            expected=[r['case'] for r in lib.hardness(sid)[:5]]
            self.assertEqual(lib.save_hard_tests(sid),5); lib.save_hard_tests(sid)
            rows=lib.saved_tests(); self.assertEqual(len(rows),5)
            self.assertEqual([r['case'] for r in rows],expected)
            self.assertTrue(all(r['attempts']==3 for r in rows))
            lib.clear_history('all'); lib.close()
            lib=Library(path); self.assertEqual(len(lib.saved_tests()),5); self.assertEqual(lib.candidates(),[]); lib.close()

    def test_custom_and_group_retests_use_exact_candidate_and_save_results(self):
        import tkinter as tk
        from app import App
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'db'; lib,sid=self.seed(path); lib.save_hard_tests(sid)
            before=lib.candidate('g0'); scores=lib.candidate_scores(); lib.close()
            root=tk.Tk(); app=App(root,path); root.update(); app.select_candidate('g0')
            try:
                first=app.saved_test_tree.get_children()[0]; app.saved_test_tree.selection_set(first); root.update()
                self.assertEqual(asdict(app.configured_test()),app.saved_scenarios[first]['case'])
                app.test_values['angle'].set('23'); app.test_values['orientation'].set('-32')
                app.save_custom_test(); self.assertEqual(len(app.lib.saved_tests()),6)
                app.run_custom_test(); self.wait_job(app,root)
                runs=[r for r in app.lib.runs('g0') if r['label'].startswith('Retest ')]
                results=app.lib.results(runs[0]['id']); self.assertEqual(len(results),1)
                self.assertEqual(results[0]['case']['angle'],23); self.assertEqual(results[0]['case']['orientation'],-32)
                app.saved_test_tree.selection_set(first); app.load_saved_test()
                app.run_hard_group(); self.wait_job(app,root)
                run=app.lib.runs('g0')[0]; results=app.lib.results(run['id'])
                self.assertEqual(len(results),5); self.assertFalse(any(r.get('censored') for r in results))
                self.assertEqual(app.lib.candidate('g0'),before); self.assertEqual(app.lib.candidate_scores(),scores)
                self.assertEqual(app.selected_run,run['id'])
                self.assertTrue(all(r['elapsed']<=3.002 for r in results))
            finally: app.close()

    def wait_job(self,app,root):
        deadline=time.monotonic()+20
        while app.busy and time.monotonic()<deadline: root.update(); time.sleep(.01)
        self.assertFalse(app.busy); self.assertNotIn('Error:',app.retest_text.get())

if __name__=='__main__': unittest.main(verbosity=2)
