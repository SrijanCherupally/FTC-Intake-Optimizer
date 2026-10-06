"""Persistent candidate/run hierarchy. Short SQLite transactions suit local OneDrive use."""
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from dataclasses import asdict
from physics import summarize

def encode(value): return json.dumps(value,sort_keys=True,separators=(',',':'))
def key(value): return hashlib.sha256(encode(value).encode()).hexdigest()[:24]
def passed(r): return not r['jam']
def metrics(results):
    s=summarize(results)
    s['passed']=sum(passed(r) for r in results)
    s['pass_rate']=s['passed']/max(1,s['cases'])
    s['delivery_rate']=s['fed']/max(1,s['total'])
    s['mean_time']=sum(r['elapsed'] for r in results)/max(1,len(results))
    s['mean_stall']=sum(r['max_stall'] for r in results)/max(1,len(results))
    return s
def ranking(s):
    # User objective: misses and delivery percentage do NOT affect selection.
    return (-s['pass_rate'],s['mean_stall'])

class Library:
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path,timeout=15)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS candidates(
          id TEXT PRIMARY KEY, name TEXT, geometry TEXT, settings TEXT, session TEXT,
          stage TEXT, created REAL, starred INTEGER DEFAULT 0, auto_star INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS runs(
          id INTEGER PRIMARY KEY, candidate TEXT REFERENCES candidates(id), label TEXT,
          cases TEXT, expected INTEGER, status TEXT, summary TEXT, UNIQUE(candidate,label));
        CREATE TABLE IF NOT EXISTS results(
          run INTEGER REFERENCES runs(id), case_key TEXT, payload TEXT,
          PRIMARY KEY(run,case_key));
        CREATE TABLE IF NOT EXISTS sessions(
          id TEXT PRIMARY KEY, config TEXT, baseline TEXT, state TEXT, status TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS training_votes(
          session TEXT, candidate TEXT, case_key TEXT, case_data TEXT, jam INTEGER, missed INTEGER,
          PRIMARY KEY(session,candidate,case_key));
        CREATE INDEX IF NOT EXISTS votes_session_case ON training_votes(session,case_key);
        CREATE INDEX IF NOT EXISTS candidates_session ON candidates(session);
        CREATE INDEX IF NOT EXISTS runs_candidate ON runs(candidate);
        ''')
        self.db.commit()
        if not self.db.execute('SELECT 1 FROM training_votes LIMIT 1').fetchone():
            with self.db:
                self.db.execute('''INSERT OR IGNORE INTO training_votes
                  SELECT c.session,c.id,t.case_key,json_extract(t.payload,'$.case'),
                         json_extract(t.payload,'$.jam'),json_extract(t.payload,'$.missed')
                  FROM results t JOIN runs r ON t.run=r.id JOIN candidates c ON c.id=r.candidate
                  WHERE c.session<>'' AND (r.label='Broad survey' OR r.label LIKE 'Focus %')''')
    def close(self): self.db.close()
    def candidate(self,cid):
        row=self.db.execute('SELECT * FROM candidates WHERE id=?',(cid,)).fetchone()
        if not row: return None
        out=dict(row)
        for k in ['geometry','settings']: out[k]=json.loads(out[k])
        return out
    def add(self,name,geometry,settings,session='',stage='Manual',cid=None):
        cid=cid or key([name,geometry,settings,time.time_ns()])
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO candidates(id,name,geometry,settings,session,stage,created) VALUES(?,?,?,?,?,?,?)',
                            (cid,name,encode(geometry),encode(settings),session,stage,time.time()))
        return cid
    def candidates(self):
        rows=self.db.execute('SELECT * FROM candidates ORDER BY (starred OR auto_star) DESC,created DESC').fetchall()
        return [dict(r) for r in rows]
    def star(self,cid):
        with self.db: self.db.execute('UPDATE candidates SET starred=1-starred WHERE id=?',(cid,))
    def run(self,cid,label,cases):
        serial=[asdict(c) if hasattr(c,'__dataclass_fields__') else c for c in cases]
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO runs(candidate,label,cases,expected,status) VALUES(?,?,?,?,?)',
                            (cid,label,encode(serial),len(serial),'pending'))
        row=dict(self.db.execute('SELECT * FROM runs WHERE candidate=? AND label=?',(cid,label)).fetchone())
        if json.loads(row['cases'])!=serial: raise ValueError('A saved run cannot change its test set.')
        return row
    def runs(self,cid):
        return [dict(r) for r in self.db.execute('SELECT * FROM runs WHERE candidate=? ORDER BY id DESC',(cid,))]
    def results(self,run_id):
        return [json.loads(r[0]) for r in self.db.execute('SELECT payload FROM results WHERE run=? ORDER BY rowid',(run_id,))]
    def cache(self,cid):
        return {r[0]:json.loads(r[1]) for r in self.db.execute('''SELECT t.case_key,t.payload FROM results t
          JOIN runs r ON t.run=r.id WHERE r.candidate=? ORDER BY r.id''',(cid,))}
    def save_results(self,run_id,results):
        with self.db:
            self.db.executemany('INSERT OR REPLACE INTO results VALUES(?,?,?)',[(run_id,key(r['case']),encode(r)) for r in results])
            run=self.db.execute('SELECT r.label,c.id,c.session FROM runs r JOIN candidates c ON c.id=r.candidate WHERE r.id=?',(run_id,)).fetchone()
            if run['session'] and (run['label']=='Broad survey' or run['label'].startswith('Focus ')):
                self.db.executemany('INSERT OR REPLACE INTO training_votes VALUES(?,?,?,?,?,?)',
                  [(run['session'],run['id'],key(r['case']),encode(r['case']),int(r['jam']),r['missed']) for r in results])
            actual=self.results(run_id)
            expected=self.db.execute('SELECT expected FROM runs WHERE id=?',(run_id,)).fetchone()[0]
            status='complete' if len(actual)==expected else 'partial'
            self.db.execute('UPDATE runs SET status=?,summary=? WHERE id=?',(status,encode(metrics(actual)),run_id))
        return status
    def state(self,sid):
        row=dict(self.db.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone())
        for k in ['config','state']: row[k]=json.loads(row[k])
        return row
    def save_state(self,sid,state,status='running'):
        with self.db: self.db.execute('UPDATE sessions SET state=?,status=? WHERE id=?',(encode(state),status,sid))
    def sessions(self): return [dict(r) for r in self.db.execute('SELECT * FROM sessions ORDER BY created DESC')]
    def hardness(self,sid):
        # Each geometry votes once per case, even when a cache is reused across rounds.
        rows=self.db.execute('''SELECT case_key,case_data,COUNT(*) attempts,SUM(jam) failures,
          SUM(missed>0) misses FROM training_votes WHERE session=? GROUP BY case_key''',(sid,))
        stats=[{'key':r['case_key'],'case':json.loads(r['case_data']),'attempts':r['attempts'],
                'failures':r['failures'],'jams':r['failures'],'misses':r['misses'],
                'rate':r['failures']/r['attempts']} for r in rows]
        return sorted(stats,key=lambda s:(-s['rate'],-s['failures'],s['key']))
    def import_legacy(self,folder):
        folder=Path(folder)
        baseline=folder/'validation_report.json'
        if baseline.exists():
            d=json.loads(baseline.read_text()); cid=self.add('Original CAD',d['geometry'],d['settings'],stage='Imported',cid='original-cad')
            run=self.run(cid,'Original 137-case baseline',[r['case'] for r in d['baseline_results']]); self.save_results(run['id'],d['baseline_results'])
        for filename in ['verified_search.json','latest_search.json']:
            p=folder/filename
            if not p.exists(): continue
            d=json.loads(p.read_text())
            if not d.get('best_geometry') or not d.get('validation_results'): continue
            cid=self.add('Earlier search candidate',d['best_geometry'],d['settings'],stage='Imported',cid='legacy-'+key(d['best_geometry']))
            run=self.run(cid,'Earlier holdout',[r['case'] for r in d['validation_results']]); self.save_results(run['id'],d['validation_results'])
