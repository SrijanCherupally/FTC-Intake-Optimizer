"""Physics sanity, numerical convergence, batch evaluation and optional search."""
import json, time, math, sys
from pathlib import Path
from dataclasses import replace, asdict
from geometry import Geometry, THROAT, MOUNT_SPAN, BALL_D
from physics import Simulation, Settings, Case, suite, summarize
from search import evaluate, optimize

def checks():
    g=Geometry(); s=Settings(); report={}
    assert abs(g.side('left')['angle']-30)<.02
    for side in ['left','right']:
        sign=-1 if side=='left' else 1
        geo=g.side(side)
        assert geo['edge'][0]==(sign*37.5,0)
        assert min(y for x,y in geo['polygon'])>=0
        assert (sign*84,g.mount_y) in geo['polygon']
        assert (sign*140.175,g.mount_y) in geo['polygon']
    for c in suite():
        sim=Simulation(g,s,c)
        assert all(math.dist(p,q)>=BALL_D-.001 for i,p in enumerate(sim.initial) for q in sim.initial[i+1:])
    # Free centered transport and single-file passage must feed without jams.
    for pattern in ['Single ball','Single file']:
        result=Simulation(g,s,Case(pattern)).run()
        assert result['fed']==result['total'] and not result['jam'],result
        report[pattern]=result
    # Balls passing around the entire outside must never count as delivered.
    outside=Simulation(g,s,Case('Single ball',offset=200)).run()
    assert outside['fed']==0 and outside['missed']==1,outside
    # Deterministic replay and independent line rotation.
    c=Case('Four abreast',25,0,78,0,1,40)
    a=Simulation(g,s,c).run(); b=Simulation(g,s,c).run(); assert a==b
    assert Simulation(case=Case('Four abreast',orientation=40)).initial!=Simulation(case=Case('Four abreast')).initial
    # A narrow-throat symmetric wedge is capable of arching: solver must allow a stall.
    symmetric=replace(g,left_straight=50,right_straight=50,left_lip=4,right_lip=4)
    jam=Simulation(symmetric,s,Case('Two abreast',spacing=74.5)).run()
    assert jam['jam'] and jam['remaining']>0,jam
    report['symmetric_jam']=jam
    # Halving timestep should preserve the qualitative result and close exit timing.
    converged=[]
    for c in [Case('Single ball'),Case('Two abreast'),Case('Three abreast'),Case('Four abreast'),Case('Four abreast',25,orientation=40)]:
        a=Simulation(g,s,c).run(); b=Simulation(g,replace(s,dt=s.dt/2),c).run()
        converged.append({'case':asdict(c),'standard':a,'half_step':b,
                          'same_outcome':(a['fed'],a['missed'],a['jam'])==(b['fed'],b['missed'],b['jam'])})
        assert converged[-1]['same_outcome'],converged[-1]
        assert abs(a['last_exit']-b['last_exit'])<.08,(a,b)
    report['convergence']=converged
    report['checks_passed']=True
    return report

if __name__=='__main__':
    folder=Path(__file__).parent; t=time.perf_counter()
    report=checks(); print('Physics and geometry checks passed.',flush=True)
    settings=Settings(); geo=Geometry()
    results=evaluate(geo,settings,suite(),progress=lambda i,n,r: print(f'Batch {i}/{n}',flush=True) if i%30==0 else None)
    report.update({'geometry':geo.dict(),'settings':asdict(settings),'baseline_summary':summarize(results),'baseline_results':results})
    (folder/'validation_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report['baseline_summary']),flush=True)
    if '--search' in sys.argv:
        search=optimize(geo,settings,count=24,progress=lambda row:print(f'Search {row["candidate"]+1}/24: {row["summary"]}',flush=True))
        (folder/'verified_search.json').write_text(json.dumps(search,indent=2),encoding='utf-8')
        (folder/'search_candidate.json').write_text(json.dumps({'geometry':search['best_geometry'],'settings':asdict(settings),'case':asdict(Case())},indent=2),encoding='utf-8')
        print('Holdout:',search['baseline_validation'],search['best_validation'],flush=True)
    print(f'Completed in {time.perf_counter()-t:.1f}s.',flush=True)
