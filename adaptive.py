"""Checkpointed multiprocessing curriculum search. No holdout results feed mutations."""
import argparse
import concurrent.futures as futures
import multiprocessing as mp
import os, random, signal, threading, time
from dataclasses import dataclass, asdict
from pathlib import Path
from geometry import Geometry
from physics import Simulation, Settings, Case
from library import Library, key, encode, metrics, ranking

@dataclass
class SearchConfig:
    broad_geometries: int = 100
    broad_tests: int = 240
    focused_geometries: int = 2000
    hard_tests: int = 48
    coverage_tests: int = 12
    round_size: int = 100
    finalists: int = 6
    validation_tests: int = 300
    workers: int = min(8,max(1,(os.cpu_count() or 2)-2))
    seed: int = 20261005
    def validate(self):
        for k,v in asdict(self).items():
            if not isinstance(v,int) or v<1: raise ValueError(f'{k} must be a positive integer.')
        if self.broad_geometries<2: raise ValueError('Use at least two broad geometries.')
        if self.broad_tests<self.hard_tests+self.coverage_tests: raise ValueError('Broad tests must cover the hard + coverage test counts.')
        if self.workers>32: raise ValueError('Use at most 32 workers.')
        if self.finalists>30: raise ValueError('Use at most 30 finalists.')
        if max(self.broad_geometries,self.focused_geometries)>100000: raise ValueError('Geometry count exceeds 100,000.')
        if max(self.broad_tests,self.validation_tests)>10000: raise ValueError('Test count exceeds 10,000.')
        return self

def cases(count,seed,exclude=()):
    """Balanced counts, continuous headings/orientations, offsets, gaps and timing."""
    rng=random.Random(seed); seen=set(exclude); out=[]
    while len(out)<count:
        n=2+len(out)%3
        c=Case({2:'Two abreast',3:'Three abreast',4:'Four abreast'}[n],
               round(rng.uniform(-50,50),3),round(rng.uniform(-26,26),3),
               round(rng.uniform(74.5,90),3),round(rng.uniform(-12,12),3),1,
               round(rng.uniform(-65,65),3))
        ck=key(asdict(c))
        if ck not in seen: out.append(c); seen.add(ck)
    return out

BOUNDS={'lip':(0,25),'radius':(5,55),'straight':(0,98),'drop':(25,80),'bow':(-6,6)}
def mutate(base,rng,global_sample=False,scale=.15):
    for _ in range(500):
        g=Geometry(**base)
        for side in ['left','right']:
            for param,(low,high) in BOUNDS.items():
                field=side+'_'+param
                v=rng.uniform(low,high) if global_sample else getattr(g,field)+rng.gauss(0,(high-low)*scale)
                setattr(g,field,round(max(low,min(high,v)),5))
        try: return g.validate().dict()
        except ValueError: pass
    raise ValueError('Unable to sample a valid funnel within the search bounds.')

_worker_stop=None
def worker_init(stop):
    global _worker_stop
    _worker_stop=stop
    signal.signal(signal.SIGINT,signal.SIG_IGN)

def acquire_session_lock(path,sid):
    """OS-released lock prevents two windows resuming the same search at once."""
    handle=open(Path(path).parent/(sid+'.lock'),'a+b')
    if handle.tell()==0: handle.write(b'0'); handle.flush()
    handle.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        handle.close(); raise RuntimeError('This search is already running in another window or process.')
    return handle
def simulate_job(geometry,settings,case_data):
    results=[]
    for data in case_data:
        if _worker_stop and _worker_stop.is_set(): break
        sim=Simulation(Geometry(**geometry),Settings(**settings),Case(**data))
        while not sim.done:
            if _worker_stop and _worker_stop.is_set(): return results
            sim.step(120)
        results.append(sim.result())
    return results

def create_session(lib,geo,settings,config):
    config.validate(); sid='search-'+str(time.time_ns())
    baseline=lib.add('Baseline · '+sid[-6:],geo.dict(),asdict(settings),sid,'Broad',sid+'-b0000')
    state={'phase':'broad','broad_ids':[baseline],'focus_done':0,'round':0,'elites':[],
           'champions':[],'pending_ids':[],'cohort':[],'winner':None,'shortlist':[]}
    with lib.db:
        lib.db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',(sid,encode(asdict(config)),baseline,encode(state),'paused',time.time()))
    return sid

def focus_cases(lib,sid,training,config,round_index):
    hard=lib.hardness(sid)
    # Keep the hardest cases; periodically revisit other environments for regressions.
    bykey={key(asdict(c)):c for c in training}
    selected=[bykey[r['key']] for r in hard if r['key'] in bykey][:config.hard_tests]
    used={key(asdict(c)) for c in selected}
    remaining=[c for c in training if key(asdict(c)) not in used]
    rng=random.Random(config.seed+round_index*4099); rng.shuffle(remaining)
    return selected+remaining[:config.coverage_tests]

class SearchRunner:
    def __init__(self,path,sid,stop=None,notify=lambda data:None):
        self.lib=Library(path); self.sid=sid; self.stop=stop or threading.Event(); self.notify=notify
        session=self.lib.state(sid); self.config=SearchConfig(**session['config']).validate(); self.state=session['state']
        self.settings=self.lib.candidate(session['baseline'])['settings']; self.baseline=session['baseline']
        self.start=time.monotonic(); self.new_tests=0; self.cached_tests=0
    def save(self,status='running'): self.lib.save_state(self.sid,self.state,status)
    def emit(self,**data):
        payload={'session':self.sid,'phase':self.state['phase'],'updated':time.time(),**data}
        self.lib.save_progress(self.sid,payload); self.notify(payload)
    def evaluate(self,ids,label,tests,pool,worker_stop):
        todo=[]; all_results={}; total=len(ids); completed=0
        serial=[asdict(c) for c in tests]
        for cid in ids:
            run=self.lib.run(cid,label,tests); cache=self.lib.cache(cid)
            reused=[cache[key(c)] for c in serial if key(c) in cache]
            self.cached_tests+=len(reused); self.lib.save_results(run['id'],reused)
            missing=[c for c in serial if key(c) not in cache]
            if missing: todo.append((cid,run['id'],missing))
            else:
                all_results[cid]=metrics(reused); completed+=1
        pending={}; index=0; heartbeat=time.monotonic()
        while pending or index<len(todo):
            if self.lib.pause_requested(self.sid): self.stop.set()
            if self.stop.is_set(): worker_stop.set()
            while not self.stop.is_set() and len(pending)<self.config.workers and index<len(todo):
                cid,rid,missing=todo[index]; index+=1; candidate=self.lib.candidate(cid)
                f=pool.submit(simulate_job,candidate['geometry'],self.settings,missing)
                pending[f]=(cid,rid)
            if not pending: break
            try: done,_=futures.wait(pending,timeout=.15,return_when=futures.FIRST_COMPLETED)
            except KeyboardInterrupt:
                self.stop.set(); worker_stop.set(); continue
            if time.monotonic()-heartbeat>2:
                self.emit(done=completed,total=total,label=label,message=f'{label}: {completed}/{total} geometries • {len(pending)} workers active • {self.new_tests:,} new simulations saved')
                heartbeat=time.monotonic()
            for f in done:
                cid,rid=pending.pop(f); results=f.result(); self.new_tests+=len(results)
                status=self.lib.save_results(rid,results)
                if status=='complete':
                    all_results[cid]=metrics(self.lib.results(rid)); completed+=1
                elapsed=time.monotonic()-self.start
                self.emit(candidate=cid,done=completed,total=total,label=label,
                          tests=self.new_tests,cached=self.cached_tests,elapsed=elapsed,
                          message=f'{label}: {completed}/{total} geometries • {self.new_tests:,} new simulations')
            if self.stop.is_set() and not pending: break
        return sorted(all_results,key=lambda cid:(ranking(all_results[cid]),cid))

    def run(self):
        c=self.config; s=self.state
        if s['phase']=='complete': self.lib.close(); return s
        try: lease=acquire_session_lock(self.lib.path,self.sid)
        except Exception: self.lib.close(); raise
        try:
            if not self.lib.db.execute('SELECT 1 FROM sessions WHERE id=?',(self.sid,)).fetchone():
                raise RuntimeError('This session was cleared. Start a new search.')
            self.lib.request_pause(self.sid,False)
            training=cases(c.broad_tests,c.seed)
            ctx=mp.get_context('spawn'); worker_stop=ctx.Event()
            self.save(); self.emit(message='Starting workers; saved runs will be reused.')
            with futures.ProcessPoolExecutor(max_workers=c.workers,mp_context=ctx,initializer=worker_init,initargs=(worker_stop,)) as pool:
                if s['phase']=='broad':
                    base=self.lib.candidate(self.baseline)['geometry']
                    while len(s['broad_ids'])<c.broad_geometries:
                        i=len(s['broad_ids']); rng=random.Random(c.seed+i*104729)
                        geo=mutate(base,rng,global_sample=i%3==0,scale=.24)
                        cid=self.sid+f'-b{i:04d}'
                        self.lib.add(f'Broad {i:03d}',geo,self.settings,self.sid,'Broad',cid)
                        s['broad_ids'].append(cid)
                    self.save()
                    ranked=self.evaluate(s['broad_ids'],'Broad survey',training,pool,worker_stop)
                    if self.stop.is_set(): return self.pause()
                    s['elites']=ranked[:c.finalists]; s['champions']=s['elites'][:]
                    s['phase']='focus'; self.save(); self.emit(message='Broad survey complete. Analyzing failure rates by test.')
                while s['phase']=='focus' and s['focus_done']<c.focused_geometries:
                    if self.stop.is_set(): return self.pause()
                    if not s['pending_ids']:
                        s['round']+=1
                        tests=focus_cases(self.lib,self.sid,training,c,s['round']); s['cohort']=[asdict(x) for x in tests]
                        n=min(c.round_size,c.focused_geometries-s['focus_done'])
                        for j in range(n):
                            i=s['focus_done']+j; rng=random.Random(c.seed+1000003+i*7919)
                            parent=self.lib.candidate(s['elites'][i%len(s['elites'])])['geometry']
                            # Local evolution plus random exploration avoids freezing at one shape.
                            geo=mutate(parent,rng,global_sample=i%8==0,scale=max(.025,.14*(1-i/max(1,c.focused_geometries))))
                            cid=self.sid+f'-f{i:06d}'
                            self.lib.add(f'Refine {i+1:04d}',geo,self.settings,self.sid,'Focus',cid)
                            s['pending_ids'].append(cid)
                        self.save()
                    tests=[Case(**x) for x in s['cohort']]
                    # Incumbents are reevaluated on the SAME cohort before comparing to challengers.
                    ids=list(dict.fromkeys([*s['elites'],*s['pending_ids']]))
                    ranked=self.evaluate(ids,f'Focus {s["round"]:03d}',tests,pool,worker_stop)
                    if self.stop.is_set(): return self.pause()
                    s['elites']=ranked[:c.finalists]
                    s['champions']=list(dict.fromkeys([*s['champions'],*s['elites'][:2]]))
                    s['focus_done']+=len(s['pending_ids']); s['pending_ids']=[]; self.save()
                if s['phase']=='focus':
                    s['phase']='screen'; self.save()
                if s['phase']=='screen':
                    # A common full training suite compares champions from different focus rounds fairly.
                    ids=list(dict.fromkeys([self.baseline,*s['champions'],*s['elites']]))
                    ranked=self.evaluate(ids,'Finalist screening',training,pool,worker_stop)
                    if self.stop.is_set(): return self.pause()
                    s['shortlist']=list(dict.fromkeys([self.baseline,*ranked[:c.finalists]]))
                    s['phase']='validate'; self.save()
                if s['phase']=='validate':
                    holdout=cases(c.validation_tests,c.seed+70000001,exclude=[key(asdict(x)) for x in training])
                    ranked=self.evaluate(s['shortlist'],'Fresh validation',holdout,pool,worker_stop)
                    if self.stop.is_set(): return self.pause()
                    s['winner']=ranked[0]; s['ranked_finalists']=ranked
                    with self.lib.db:
                        self.lib.db.execute('UPDATE candidates SET auto_star=0 WHERE session=?',(self.sid,))
                        self.lib.db.executemany('UPDATE candidates SET auto_star=1 WHERE id=?',[(cid,) for cid in ranked[:3]])
                    s['phase']='complete'; self.save('complete')
                    self.emit(winner=s['winner'],message='Complete. Top validated candidates starred; baseline was eligible to win.')
            return s
        except Exception:
            self.save('error'); raise
        finally: self.lib.close(); lease.close()
    def pause(self):
        self.save('paused'); self.lib.request_pause(self.sid,False)
        self.emit(message='Paused and saved. Resume continues missing tests without repeating completed results.')
        return self.state

if __name__=='__main__':
    mp.freeze_support(); parser=argparse.ArgumentParser()
    parser.add_argument('--db',default='data/funnel_lab.sqlite3'); parser.add_argument('--resume'); parser.add_argument('--quick',action='store_true')
    args=parser.parse_args(); lib=Library(args.db)
    config=SearchConfig(broad_geometries=4,broad_tests=12,focused_geometries=8,hard_tests=4,coverage_tests=2,round_size=4,finalists=2,validation_tests=15,workers=2) if args.quick else SearchConfig()
    sid=args.resume or create_session(lib,Geometry(),Settings(),config); lib.close()
    stop=threading.Event()
    try: SearchRunner(args.db,sid,stop,lambda d:print(d['message'],flush=True)).run()
    except KeyboardInterrupt: stop.set()
