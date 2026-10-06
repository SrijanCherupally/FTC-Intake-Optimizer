"""Headless trainer CLI. No Tkinter dependency or window is opened."""
import math, sqlite3
import argparse, json, multiprocessing as mp, signal, sys, threading
from pathlib import Path
from dataclasses import asdict
from adaptive import SearchConfig, SearchRunner, create_session
from geometry import Geometry
from physics import Settings, Simulation, Case
from library import Library

DEFAULT_DB=Path(__file__).resolve().parent/'data'/'funnel_lab.sqlite3'
QUICK=dict(broad_geometries=4,broad_tests=12,focused_geometries=8,hard_tests=4,
           coverage_tests=2,round_size=4,finalists=2,validation_tests=15,workers=2)

def parser():
    p=argparse.ArgumentParser(description='Train funnel geometries; prioritize jams and stall duration, never misses.')
    p.add_argument('--db',type=Path,default=DEFAULT_DB,help='Shared database (default: project/data/funnel_lab.sqlite3)')
    sub=p.add_subparsers(dest='command',required=True)
    for command in ['start','resume','pause','status','list','export']:
        s=sub.add_parser(command)
        s.add_argument('--db',type=Path,default=argparse.SUPPRESS,help='Shared database path')
        s.add_argument('--json',action='store_true',help='Machine-readable JSON (training emits one JSON object per line)')
        if command in ['resume','pause','status','export']: s.add_argument('session',nargs='?',default='latest')
        if command in ['start','resume']: s.add_argument('--max-seconds',type=float,help='Pause cleanly after this many seconds')
        if command=='start':
            s.add_argument('--quick',action='store_true',help='Small complete pipeline for checking setup')
            source=s.add_mutually_exclusive_group(); source.add_argument('--setup',type=Path); source.add_argument('--from-candidate')
            for name in asdict(SearchConfig()): s.add_argument('--'+name.replace('_','-'),type=int,default=None)
            for name in ['speed','friction','response','acceleration','duration']: s.add_argument('--'+name,type=float,default=None)
        if command=='export': s.add_argument('--output',type=Path,required=True)
    return p

def resolve_session(lib,value):
    sessions=lib.sessions()
    if not sessions: raise ValueError('No training sessions yet. Run: python train.py start')
    if value=='latest': return sessions[0]['id']
    if value not in {s['id'] for s in sessions}: raise ValueError(f'Unknown session: {value}')
    return value

def snapshot(lib,sid):
    session=lib.state(sid)
    session['candidates']=lib.db.execute('SELECT COUNT(*) FROM candidates WHERE session=?',(sid,)).fetchone()[0]
    session['saved_test_records']=lib.db.execute('SELECT COUNT(*) FROM results t JOIN runs r ON r.id=t.run JOIN candidates c ON c.id=r.candidate WHERE c.session=?',(sid,)).fetchone()[0]
    session['progress']=lib.progress(sid)
    try: session['pause_requested']=lib.pause_requested(sid)
    except sqlite3.OperationalError: session['pause_requested']=False
    return session

def print_status(item,json_mode=False):
    if json_mode: print(json.dumps(item,ensure_ascii=False),flush=True); return
    print(f'{item["id"]}  {item["status"]} / {item["state"]["phase"]}',flush=True)
    print(f'  {item["candidates"]:,} candidates; {item["saved_test_records"]:,} saved test records',flush=True)
    if item.get('pause_requested'): print('  Pause requested; waiting for trainer to checkpoint.',flush=True)
    if item.get('progress'): print('  '+item['progress'].get('message',''),flush=True)
    if item['state'].get('winner'): print('  Winner: '+item['state']['winner'],flush=True)

def main(argv=None):
    args=parser().parse_args(argv)
    if hasattr(args,'max_seconds') and args.max_seconds is not None and (not math.isfinite(args.max_seconds) or args.max_seconds<=0):
        parser().error('--max-seconds must be positive')
    try:
        read_only=args.command in ['status','list','export']
        lib=Library(args.db,read_only=read_only)
        try:
            if args.command=='list':
                items=[snapshot(lib,row['id']) for row in lib.sessions()]
                if args.json: print(json.dumps(items,ensure_ascii=False))
                elif not items: print('No training sessions yet. Run: python train.py start')
                else:
                    for item in items: print_status(item)
                return 0
            if args.command=='start':
                config=asdict(SearchConfig(**(QUICK if args.quick else {})))
                config.update({k:getattr(args,k) for k in config if getattr(args,k) is not None})
                config=SearchConfig(**config).validate()
                source={}
                if args.setup: source=json.loads(args.setup.read_text(encoding='utf-8'))
                elif args.from_candidate:
                    source=lib.candidate(args.from_candidate)
                    if not source: raise ValueError('Unknown starting candidate.')
                geo=Geometry(**source.get('geometry',{})).validate(); settings=asdict(Settings(**source.get('settings',{})))
                settings.update({k:getattr(args,k) for k in ['speed','friction','response','acceleration','duration'] if getattr(args,k) is not None})
                settings=Settings(**settings); Simulation(geo,settings,Case())
                sid=create_session(lib,geo,settings,config)
            else: sid=resolve_session(lib,args.session)
            if args.command=='status': print_status(snapshot(lib,sid),args.json); return 0
            if args.command=='pause':
                status=lib.state(sid)['status']
                if status=='running': lib.request_pause(sid)
                print_status(snapshot(lib,sid),args.json); return 0
            if args.command=='export':
                session=snapshot(lib,sid); cid=session['state'].get('winner')
                if not cid: raise ValueError('No validated winner yet; wait for fresh validation to complete.')
                candidate=lib.candidate(cid)
                payload={'geometry':candidate['geometry'],'settings':candidate['settings'],'case':asdict(Case()),'candidate':cid,'session':sid}
                args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(payload,indent=2),encoding='utf-8')
                print(json.dumps({'output':str(args.output.resolve()),'candidate':cid}) if args.json else f'Exported {cid} to {args.output.resolve()}'); return 0
        finally: lib.close()
        stop=threading.Event(); timer=None
        def interrupt(signum,frame):
            stop.set()
            print('Pausing: saving completed tests. Please wait...',file=sys.stderr,flush=True)
        old_handlers={}
        for sig in [signal.SIGINT,signal.SIGTERM]: old_handlers[sig]=signal.signal(sig,interrupt)
        def notify(event):
            if args.json: print(json.dumps(event,ensure_ascii=False),flush=True)
            else: print(f'[{event["phase"]}] {event["message"]}',flush=True)
        try:
            if args.max_seconds:
                timer=threading.Timer(args.max_seconds,stop.set); timer.daemon=True; timer.start()
            if not args.json: print(f'Session: {sid}\nDatabase: {Path(args.db).resolve()}\nCtrl+C pauses safely. The viewer may be open separately.',flush=True)
            state=SearchRunner(args.db,sid,stop,notify).run()
            if args.json: print(json.dumps({'event':'stopped','session':sid,'state':state}),flush=True)
            if state['phase']=='complete' and not args.json: print(f'Winner: {state["winner"]}',flush=True)
            elif not args.json: print(f'Resume with: python train.py --db "{Path(args.db).resolve()}" resume {sid}',flush=True)
            return 0
        finally:
            if timer: timer.cancel()
            for sig,handler in old_handlers.items(): signal.signal(sig,handler)
    except (ValueError,OSError,RuntimeError,KeyError,TypeError,sqlite3.Error) as e:
        print(f'Error: {e}',file=sys.stderr); return 2

if __name__=='__main__':
    mp.freeze_support()
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    raise SystemExit(main())
