"""Exercise the CLI across processes and the viewer against a concurrent writer."""
import json, sqlite3, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from dataclasses import asdict
from library import Library
from adaptive import SearchConfig, create_session
from geometry import Geometry
from physics import Settings, Case
from test_studio import result

HERE=Path(__file__).resolve().parent

class CliTests(unittest.TestCase):
    def command(self,db,*args):
        p=subprocess.run([sys.executable,str(HERE/'train.py'),'--db',str(db),*args],cwd=db.parent,
                         capture_output=True,text=True,encoding='utf-8',timeout=120)
        self.assertEqual(p.returncode,0,p.stderr)
        return p.stdout

    def test_cli_pause_resume_export(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'train.db'
            self.assertEqual(json.loads(self.command(db,'list','--json')),[])
            self.assertFalse(db.exists())
            with (db.parent/'log.txt').open('w',encoding='utf-8') as log:
                proc=subprocess.Popen([sys.executable,str(HERE/'train.py'),'start','--db',str(db),'--quick','--json'],stdout=log,stderr=log,cwd=folder)
                try:
                    deadline=time.monotonic()+30
                    while time.monotonic()<deadline:
                        try:
                            lib=Library(db,read_only=True); sessions=lib.sessions(); lib.close()
                            if sessions and sessions[0]['status']=='running': break
                        except sqlite3.Error: pass
                        time.sleep(.1)
                    else: self.fail('Trainer did not start')
                    paused=json.loads(self.command(db,'pause','--json'))
                    self.assertTrue(paused['pause_requested'] or paused['status']=='paused')
                    self.assertEqual(proc.wait(timeout=30),0)
                finally:
                    if proc.poll() is None: proc.kill(); proc.wait()
            status=json.loads(self.command(db,'status','--json')); self.assertEqual(status['status'],'paused')
            events=[json.loads(line) for line in self.command(db,'resume','--json').splitlines()]
            self.assertEqual(events[-1]['state']['phase'],'complete')
            status=json.loads(self.command(db,'status','--json')); self.assertEqual(status['status'],'complete')
            self.assertGreater(status['saved_test_records'],0)
            output=db.parent/'winner.json'; self.command(db,'export','--output',str(output))
            setup=json.loads(output.read_text()); self.assertEqual(setup['candidate'],status['state']['winner'])
            Geometry(**setup['geometry']).validate()
            self.assertEqual(json.loads(self.command(db,'resume','--json'))['state']['phase'],'complete')

    def test_viewer_read_only_and_live_refresh(self):
        import tkinter as tk
        from app import App
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'viewer.db'; root=tk.Tk(); app=App(root,db)
            try:
                root.update(); self.assertFalse(db.exists())
                lib=Library(db); sid=create_session(lib,Geometry(),Settings(duration=3),SearchConfig())
                cid=lib.state(sid)['baseline']; run=lib.run(cid,'Broad survey',[Case('Two abreast'),Case('Three abreast')])
                lib.save_results(run['id'],[result()]); app.poll_external(force=True)
                self.assertEqual(app.selected,cid); self.assertEqual(len(app.visible_results),1)
                app.test_tree.selection_set('0'); root.update(); app.step_once()
                t=app.sim.time; case=app.case; self.assertEqual(app.settings.duration,3)
                lib.save_results(run['id'],[result(Case('Three abreast'),jam=True)])
                lib.save_progress(sid,{'message':'Live update','done':1,'total':4})
                app.poll_external(force=True); root.update()
                self.assertEqual(len(app.visible_results),2); self.assertEqual(app.sim.time,t); self.assertEqual(app.case,case)
                self.assertIn('Live update',app.progress_text.get())
                with self.assertRaises(sqlite3.OperationalError): app.lib.star(cid)
                lib.close(); before=db.read_bytes(); app.close(); app=None
                self.assertEqual(db.read_bytes(),before)
            finally:
                if app: app.close()

if __name__=='__main__': unittest.main(verbosity=2)
