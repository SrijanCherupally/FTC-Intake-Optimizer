import tempfile, unittest
from pathlib import Path
from dataclasses import asdict
from library import Library
from geometry import Geometry
from physics import Settings, Case
from test_studio import result

class CandidateSortTests(unittest.TestCase):
    def test_quality_order_filters_and_preferred_evidence(self):
        import tkinter as tk
        from app import App
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'db'; lib=Library(path)
            cases=[Case('Two abreast'),Case('Three abreast')]
            for cid in ('best','slow','bad','untested','partial'):
                lib.add(cid,Geometry().dict(),asdict(Settings()),cid=cid)
            for cid,values in [('best',[result(c,missed=2) for c in cases]),
                               ('slow',[dict(result(c),max_stall=.5) for c in cases]),
                               ('bad',[result(c,jam=True) for c in cases])]:
                run=lib.run(cid,'Fresh validation',cases); lib.save_results(run['id'],values)
            run=lib.run('bad','Broad survey',cases); lib.save_results(run['id'],[result(c) for c in cases])
            run=lib.run('partial','Broad survey',cases); lib.save_results(run['id'],[result(cases[0])])
            lib.star('bad')
            scores=lib.candidate_scores(); self.assertEqual(scores['bad']['pass_rate'],0)
            self.assertNotIn('partial',scores); self.assertNotIn('untested',scores)
            root=tk.Tk(); app=App(root,path); root.update()
            try:
                app.select_candidate('slow'); sim=app.sim
                app.sort_order.set('Best first'); app.refresh_library()
                self.assertEqual(app.candidate_tree.get_children()[:3],('best','slow','bad'))
                self.assertIs(app.sim,sim)
                app.sort_order.set('Worst first'); app.refresh_library()
                self.assertEqual(app.candidate_tree.get_children()[:3],('bad','slow','best'))
                app.only_stars.set(True); app.refresh_library()
                self.assertEqual(app.candidate_tree.get_children(),('bad',))
                app.only_stars.set(False); app.sort_order.set('Stars first'); app.refresh_library()
                self.assertEqual(app.candidate_tree.get_children()[0],'bad')
                app.sort_order.set('Newest first'); app.refresh_library()
                self.assertEqual(app.candidate_tree.get_children()[0],'partial')
            finally: app.close(); lib.close()

if __name__=='__main__': unittest.main(verbosity=2)
