"""Deterministic, bounded geometry search with a separate holdout suite."""
from dataclasses import replace, asdict
import random
from geometry import Geometry
from physics import Simulation, suite, summarize, score

def evaluate(geo, settings, cases, stop=lambda:False, progress=lambda i,n,r:None):
    results=[]
    for i,case in enumerate(cases):
        if stop(): break
        sim=Simulation(geo,settings,case)
        while not sim.done:
            if stop(): return results
            sim.step(120)
        results.append(sim.result()); progress(i+1,len(cases),results[-1])
    return results

def optimize(geo, settings, count=24, stop=lambda:False, progress=lambda item:None):
    rng=random.Random(20261005)
    # Fixed ball, friction, mounting span, top line, outer anchors, and drive settings.
    training=suite()[::9]  # all counts and headings, mixed formations/offsets below
    from physics import Case
    training=[Case(c.name,c.angle,[-20,0,20][i%3],78,0,1,[-40,0,40][(i//2)%3]) for i,c in enumerate(training)]
    rows=[]; best_geo=replace(geo); best_score=None
    for k in range(count):
        if stop(): break
        candidate=replace(best_geo)
        if k:
            for _ in range(100):
                candidate=replace(best_geo)
                for side in ['left','right']:
                    setattr(candidate,side+'_lip',rng.uniform(1,22))
                    setattr(candidate,side+'_radius',rng.uniform(8,45))
                    setattr(candidate,side+'_straight',rng.uniform(5,90))
                    setattr(candidate,side+'_drop',rng.uniform(27,65))
                    setattr(candidate,side+'_bow',rng.uniform(-3,3))
                try: candidate.validate(); break
                except ValueError: pass
            else: continue
        results=evaluate(candidate,settings,training,stop)
        if len(results)!=len(training): break
        summary=summarize(results)
        item={'candidate':k,'geometry':candidate.dict(),'summary':summary}
        rows.append(item)
        if best_score is None or score(summary)<best_score:
            best_score=score(summary); best_geo=candidate
        progress(item)
    if stop(): return {'cancelled':True,'candidates':rows}
    baseline_validation=evaluate(geo,settings,suite(True),stop)
    best_validation=evaluate(best_geo,settings,suite(True),stop)
    complete=len(baseline_validation)==len(suite(True)) and len(best_validation)==len(suite(True))
    return {'cancelled':not complete,'candidates':rows,'best_geometry':best_geo.dict(),
            'training_cases':[asdict(x) for x in training],
            'baseline_validation':summarize(baseline_validation),
            'best_validation':summarize(best_validation),
            'validation_results':best_validation,'baseline_results':baseline_validation,
            'settings':asdict(settings),
            'note':'Bounded random search; provisional planar model, not a global optimum or jam-free guarantee.'}

