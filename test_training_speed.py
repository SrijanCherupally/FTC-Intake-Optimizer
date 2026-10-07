import json, tempfile, threading, unittest
from pathlib import Path
from dataclasses import asdict
from unittest.mock import patch
from adaptive import SearchConfig, SearchRunner, create_session, simulate_job
from geometry import Geometry
from physics import Settings, Case, Simulation
from library import Library, metrics, ranking
from test_studio import result

class FasterTrainingTests(unittest.TestCase):
    def test_physics_matches_preoptimization_reference(self):
        fixture=json.loads((Path(__file__).parent/'physics_reference.json').read_text())
        for row in fixture['tests']:
            with self.subTest(case=row['case'],geometry=row['geometry']):
                actual=Simulation(Geometry(**row['geometry']),Settings(**row['settings']),Case(**row['case']),record_trails=False).run()
                self.assertEqual(actual,row['result'])

    def test_confirmed_jam_screen_cannot_pollute_full_cache(self):
        case=Case('Two abreast',spacing=74.5)
        geo=Geometry(left_straight=50,right_straight=50,left_lip=4,right_lip=4)
        screened=simulate_job(geo.dict(),asdict(Settings()),[asdict(case)],True)[0]
        full=Simulation(geo,Settings(),case).run()
        self.assertTrue(full['jam']); self.assertEqual(screened['jam'],full['jam'])
        self.assertTrue(screened['censored']); self.assertLess(screened['elapsed'],full['elapsed'])
        with tempfile.TemporaryDirectory() as folder:
            lib=Library(Path(folder)/'db'); cid=lib.add('test',Geometry().dict(),asdict(Settings()))
            run=lib.run(cid,'Screen',[case]); lib.save_results(run['id'],[screened])
            self.assertEqual(lib.cache(cid),{}); self.assertEqual(len(lib.cache(cid,full=False)),1)
            run=lib.run(cid,'Full',[case]); lib.save_results(run['id'],[full])
            self.assertEqual(list(lib.cache(cid).values()),[full]); lib.close()

    def test_staged_resume_and_full_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'db'; lib=Library(path)
            config=SearchConfig(broad_geometries=8,broad_tests=18,focused_geometries=8,hard_tests=6,
                                coverage_tests=3,round_size=4,finalists=2,validation_tests=15,
                                workers=2,screen_tests=3,reduction=2,patience=0)
            sid=create_session(lib,Geometry(),Settings(),config); lib.close(); stop=threading.Event()
            def notify(event):
                if 'advancing' in event.get('message',''): stop.set()
            SearchRunner(path,sid,stop,notify).run()
            lib=Library(path); self.assertEqual(lib.state(sid)['status'],'paused')
            self.assertGreater(lib.state(sid)['state']['races']['Broad survey']['stage'],0); lib.close()
            runner=SearchRunner(path,sid); state=runner.run(); self.assertEqual(state['phase'],'complete')
            lib=Library(path); self.assertTrue(lib.db.execute("SELECT 1 FROM runs WHERE status='screened out'").fetchone())
            scored=[]
            for cid in state['shortlist']:
                run=next(r for r in lib.runs(cid) if r['label']=='Fresh validation')
                results=lib.results(run['id']); self.assertEqual(len(results),15)
                self.assertFalse(any(r.get('censored') for r in results)); scored.append((ranking(metrics(results)),cid))
            self.assertEqual(sorted(scored)[0][1],state['winner']); lib.close()

    def test_plateau_uses_fixed_training_cases(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'db'; lib=Library(path)
            config=SearchConfig(patience=2,min_focused=4,progress_tests=6)
            sid=create_session(lib,Geometry(),Settings(),config); lib.close(); runner=SearchRunner(path,sid)
            runner.state.update(focus_done=4,elites=[runner.baseline])
            seen=[]
            def evaluate(ids,label,tests,*args):
                seen.append([asdict(c) for c in tests]); run=runner.lib.run(ids[0],label,tests)
                runner.lib.save_results(run['id'],[result(c) for c in tests]); return ids
            with patch.object(runner,'evaluate',side_effect=evaluate):
                self.assertFalse(runner.check_plateau(None,None)); self.assertTrue(runner.check_plateau(None,None))
            self.assertEqual(seen[0],seen[1]); self.assertIn('No improvement',runner.state['stop_reason']); runner.lib.close()

    def test_plateau_ends_refinement_but_completes_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'db'; lib=Library(path)
            config=SearchConfig(broad_geometries=2,broad_tests=6,focused_geometries=12,hard_tests=2,
                                coverage_tests=1,round_size=2,finalists=1,validation_tests=6,
                                workers=2,screen_tests=2,patience=1,min_focused=2,progress_tests=6)
            sid=create_session(lib,Geometry(),Settings(),config); lib.close()
            with patch('adaptive.mutate',return_value=Geometry().dict()): state=SearchRunner(path,sid).run()
            self.assertEqual(state['phase'],'complete'); self.assertEqual(state['focus_done'],2)
            self.assertIn('No improvement',state['stop_reason'])
            lib=Library(path); run=next(r for r in lib.runs(state['winner']) if r['label']=='Fresh validation')
            self.assertEqual(len(lib.results(run['id'])),6); lib.close()

    def test_older_checkpoints_keep_exhaustive_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'db'; lib=Library(path); config=asdict(SearchConfig())
            sid=create_session(lib,Geometry(),Settings(),SearchConfig())
            for k in ['racing','screen_tests','reduction','batch_size','patience','min_focused','progress_tests']: config.pop(k)
            with lib.db: lib.db.execute('UPDATE sessions SET config=? WHERE id=?',(json.dumps(config),sid))
            lib.close(); runner=SearchRunner(path,sid)
            self.assertEqual(runner.config.racing,0); self.assertEqual(runner.config.patience,0); runner.lib.close()

if __name__=='__main__': unittest.main(verbosity=2)
