import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from dataclasses import asdict
from library import Library
from geometry import Geometry
from physics import Settings, Case
from adaptive import SearchConfig, create_session, acquire_session_lock
from test_studio import result

class CleanupTests(unittest.TestCase):
    def test_keep_manual_and_auto_stars_then_clear_them(self):
        with tempfile.TemporaryDirectory() as folder:
            lib=Library(Path(folder)/'db')
            sid=create_session(lib,Geometry(),Settings(),SearchConfig())
            for cid in ('manual','auto','ordinary'):
                lib.add(cid,Geometry().dict(),asdict(Settings()),sid,cid=cid)
                run=lib.run(cid,'Broad survey',[Case('Two abreast')]); lib.save_results(run['id'],[result()])
            lib.star('manual')
            with lib.db: lib.db.execute("UPDATE candidates SET auto_star=1 WHERE id='auto'")
            lease=acquire_session_lock(lib.path,sid)
            with self.assertRaises(RuntimeError): lib.clear_history('all')
            self.assertEqual(len(lib.candidates()),4); lease.close()
            lib.clear_history()
            self.assertEqual({r['id'] for r in lib.candidates()},{'manual','auto'})
            self.assertEqual(len(lib.results(lib.runs('auto')[0]['id'])),1)
            self.assertEqual(lib.sessions(),[])
            lib.close(); lib=Library(Path(folder)/'db')
            self.assertEqual(lib.db.execute('SELECT COUNT(*) FROM training_votes').fetchone()[0],0)
            lib.clear_history('starred'); self.assertEqual(lib.candidates(),[])
            self.assertEqual(lib.db.execute('PRAGMA integrity_check').fetchone()[0],'ok'); lib.close()

    def test_viewer_cleanup_and_empty_playback(self):
        import tkinter as tk
        from app import App
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'db'; lib=Library(db)
            lib.add('Star',Geometry().dict(),asdict(Settings()),cid='star'); lib.star('star')
            lib.add('Other',Geometry().dict(),asdict(Settings()),cid='other'); lib.close()
            root=tk.Tk(); app=App(root,db)
            try:
                app.select_candidate('other')
                with patch('app.messagebox.askyesno',return_value=False): app.clear_history('unstarred')
                self.assertEqual(len(app.lib.candidates()),2)
                with patch('app.messagebox.askyesno',return_value=True): app.clear_history('unstarred')
                self.assertEqual(app.selected,'star'); self.assertTrue(app.only_stars.get())
                with patch('app.messagebox.askyesno',return_value=True): app.clear_history('starred')
                self.assertEqual(app.lib.candidates(),[]); self.assertIsNone(app.selected)
                app.toggle(); app.step_once(); root.update()
                self.assertFalse(app.running); self.assertEqual(app.run_names,[])
            finally: app.close()

if __name__=='__main__': unittest.main(verbosity=2)
