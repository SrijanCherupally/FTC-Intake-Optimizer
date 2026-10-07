"""Compare exhaustive and staged search on identical seeds, budgets and physics."""
import argparse, json, multiprocessing as mp, tempfile, time
from pathlib import Path
from dataclasses import asdict
from adaptive import SearchConfig, SearchRunner, create_session
from library import Library, metrics
from geometry import Geometry
from physics import Settings

def benchmark(workers=4):
    reports={}
    base=dict(broad_geometries=24,broad_tests=72,focused_geometries=48,hard_tests=18,
              coverage_tests=6,round_size=24,finalists=3,validation_tests=60,
              screen_tests=6,workers=workers,patience=0)
    with tempfile.TemporaryDirectory() as folder:
        for name,racing in [('exhaustive',0),('staged',1)]:
            path=Path(folder)/(name+'.db'); lib=Library(path)
            config=SearchConfig(**base,racing=racing)
            sid=create_session(lib,Geometry(),Settings(),config); lib.close()
            runner=SearchRunner(path,sid); start=time.perf_counter(); state=runner.run()
            elapsed=time.perf_counter()-start; lib=Library(path,read_only=True)
            run=next(r for r in lib.runs(state['winner']) if r['label']=='Fresh validation')
            results=lib.results(run['id'])
            assert len(results)==base['validation_tests'] and not any(r.get('censored') for r in results)
            reports[name]={'seconds':elapsed,'simulations':runner.new_tests,'cached_results':runner.cached_tests,
                           'database_bytes':path.stat().st_size,'winner_validation':metrics(results),
                           'winner_geometry':lib.candidate(state['winner'])['geometry'],'config':asdict(config)}
            lib.close(); print(name,round(elapsed,2),'s,',runner.new_tests,'simulations',flush=True)
    reports['speedup']=reports['exhaustive']['seconds']/reports['staged']['seconds']
    reports['trial_reduction']=1-reports['staged']['simulations']/reports['exhaustive']['simulations']
    reports['note']='One fixed-seed, reduced-budget comparison on this machine. Both modes use the optimized physics loop. This is not a full-budget runtime guarantee.'
    return reports

if __name__=='__main__':
    mp.freeze_support(); p=argparse.ArgumentParser(); p.add_argument('--workers',type=int,default=4)
    p.add_argument('--output',type=Path,default=Path('training_benchmark.json')); args=p.parse_args()
    report=benchmark(args.workers); args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(f"Speedup: {report['speedup']:.2f}x; simulations reduced {report['trial_reduction']:.1%}")
