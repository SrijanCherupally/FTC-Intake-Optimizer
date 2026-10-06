"""Regression tests for jam scoring, persistence, curriculum, resume and native UI."""
import json, math, tempfile, threading, unittest
from pathlib import Path
from dataclasses import asdict
from geometry import Geometry
from physics import Settings, Case, Simulation
from library import Library, metrics, ranking, key
from adaptive import SearchConfig, SearchRunner, create_session, cases, focus_cases, acquire_session_lock

def result(case=None,jam=False,missed=0):
    return {'case':asdict(case or Case('Two abreast')),'fed':2-missed,'total':2,'remaining':0,
            'missed':missed,'jam':jam,'elapsed':2,'last_exit':2,'largest_gap':.3,
            'max_stall':2 if jam else 0,'slow_fraction':.2,'max_overlap_mm':.1,'order':[1,2]}

class StudioTests(unittest.TestCase):
    def test_session_cannot_run_twice_concurrently(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'library.db'; lease=acquire_session_lock(path,'test-session')
            with self.assertRaises(RuntimeError): acquire_session_lock(path,'test-session')
            lease.close(); acquire_session_lock(path,'test-session').close()
    def test_jams_are_only_failure_and_misses_do_not_change_rank(self):
        clean=metrics([result()]); missed=metrics([result(missed=2)])
        self.assertEqual(ranking(clean),ranking(missed))
        self.assertLess(ranking(missed),ranking(metrics([result(jam=True)])))

    def test_case_coverage_and_disjoint_validation(self):
        broad=cases(240,11); holdout=cases(300,12,exclude=[key(asdict(c)) for c in broad])
        self.assertEqual(len({key(asdict(c)) for c in broad}),240)
        self.assertFalse({key(asdict(c)) for c in broad}&{key(asdict(c)) for c in holdout})
        self.assertEqual([sum(c.name==p for c in broad) for p in ['Two abreast','Three abreast','Four abreast']],[80,80,80])
        for c in broad:
            pts=c.points(); self.assertTrue(all(math.dist(a,b)>=74 for i,a in enumerate(pts) for b in pts[i+1:]))

    def test_hardness_deduplicates_cache_and_ignores_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            lib=Library(Path(folder)/'db'); c=SearchConfig(broad_tests=4,hard_tests=1,coverage_tests=1)
            sid=create_session(lib,Geometry(),Settings(),c); cid=lib.state(sid)['baseline']
            pool=[Case('Two abreast',angle=i) for i in range(4)]
            values=[result(pool[0],jam=True),result(pool[1],missed=2),result(pool[2]),result(pool[3])]
            run=lib.run(cid,'Broad survey',pool); lib.save_results(run['id'],values)
            run2=lib.run(cid,'Focus 001',pool); lib.save_results(run2['id'],values)
            hard=lib.hardness(sid); self.assertEqual(hard[0]['case'],asdict(pool[0])); self.assertTrue(all(r['attempts']==1 for r in hard))
            selected=focus_cases(lib,sid,pool,c,1); self.assertEqual(selected[0],pool[0]); self.assertEqual(len(selected),2)
            lib.star(cid); lib.close(); lib=Library(Path(folder)/'db'); self.assertTrue(lib.candidate(cid)['starred']); lib.close()

    def test_real_physics_pipeline_pause_resume_and_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'search.db'; lib=Library(path)
            c=SearchConfig(broad_geometries=4,broad_tests=12,focused_geometries=8,hard_tests=4,coverage_tests=2,round_size=4,finalists=2,validation_tests=15,workers=2)
            sid=create_session(lib,Geometry(),Settings(),c); lib.close(); stop=threading.Event()
            def notify(d):
                if d.get('done',0)>=1: stop.set()
            SearchRunner(path,sid,stop,notify).run(); lib=Library(path)
            self.assertEqual(lib.state(sid)['status'],'paused'); self.assertFalse(any(r['auto_star'] for r in lib.candidates()))
            before=lib.db.execute('SELECT COUNT(*) FROM results').fetchone()[0]; self.assertGreater(before,0); lib.close()
            resumed=SearchRunner(path,sid); state=resumed.run(); lib=Library(path)
            self.assertEqual(state['phase'],'complete'); self.assertEqual(len(state['broad_ids']),4); self.assertEqual(state['focus_done'],8)
            self.assertGreater(resumed.cached_tests,0); self.assertTrue(lib.candidate(state['winner'])['auto_star'])
            broad_keys={key(asdict(x)) for x in cases(c.broad_tests,c.seed)}
            scored=[]
            for cid in state['shortlist']:
                r=next(r for r in lib.runs(cid) if r['label']=='Fresh validation'); results=lib.results(r['id'])
                self.assertEqual(len(results),c.validation_tests); self.assertFalse({key(x['case']) for x in results}&broad_keys)
                scored.append((ranking(metrics(results)),cid))
            self.assertEqual(sorted(scored)[0][1],state['winner'])
            for label in ['Focus 001','Focus 002','Fresh validation']:
                cohorts={r[0] for r in lib.db.execute('SELECT cases FROM runs WHERE label=?',(label,))}; self.assertEqual(len(cohorts),1)
            for candidate in lib.candidates():
                g=Geometry(**candidate['geometry']) if isinstance(candidate['geometry'],dict) else Geometry(**json.loads(candidate['geometry']))
                g.validate(); self.assertEqual(g.outer_span,280.35); self.assertEqual(g.mount_y,107.38628)
            lib.close()
            self.assertEqual(SearchRunner(path,sid).run()['phase'],'complete')
            lib=Library(path); self.assertEqual(lib.state(sid)['status'],'complete'); lib.close()

    def test_native_candidate_selection_replay_and_star(self):
        import tkinter as tk
        from app import App
        with tempfile.TemporaryDirectory() as folder:
            root=tk.Tk(); app=App(root,Path(folder)/'gui.db',read_only=False); root.update()
            app.select_candidate('original-cad'); self.assertEqual(len(app.visible_results),137)
            app.jams_only.set(True); app.show_run(); self.assertTrue(all(r['jam'] for r in app.visible_results))
            expected=app.visible_results[0]['case']; app.test_tree.selection_set('0'); root.update()
            self.assertEqual(asdict(app.case),expected); self.assertEqual(app.tabs.select(),str(app.live))
            app.step_once(); self.assertGreater(app.sim.time,0)
            app.toggle_star(); self.assertTrue(app.lib.candidate('original-cad')['starred'])
            self.assertEqual(app.config().focused_geometries,2000)
            app.close()

if __name__=='__main__': unittest.main(verbosity=2)
