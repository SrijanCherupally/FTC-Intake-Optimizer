"""Run with Python 3.12 on this machine, or install requirements on another machine."""
import sys, json, time, math, threading, queue, csv
from pathlib import Path
from dataclasses import asdict
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from geometry import Geometry, BALL_D, THROAT, MOUNT_SPAN
from physics import Simulation, Settings, Case, PATTERNS, suite, summarize
from search import evaluate, optimize

HERE=Path(__file__).resolve().parent
BG='#0c1423'; PANEL='#142136'; TEXT='#edf3fc'; MUTED='#9bacc5'; CYAN='#5de0d6'; GOLD='#f5bd66'
COLORS=['#5de0d6','#88a8ff','#f5bd66','#f68ba9','#ba9bff','#8cdd97','#f28c64','#83c9e8']

class App:
    def __init__(self, root):
        self.root=root; root.title('Funnel Lab • FTC ball transfer')
        root.geometry(f'{min(1430,root.winfo_screenwidth()-80)}x{min(940,root.winfo_screenheight()-100)}+25+25'); root.minsize(1100,740)
        root.configure(bg=BG)
        style=ttk.Style(root); style.theme_use('clam')
        style.configure('.',background=PANEL,foreground=TEXT,font=('Segoe UI',10))
        style.configure('TButton',padding=(10,7),background='#263b59',foreground=TEXT,borderwidth=0)
        style.map('TButton',background=[('active','#345776'),('disabled','#1c2b40')])
        style.configure('TEntry',fieldbackground='#21334d',foreground=TEXT,insertcolor=TEXT)
        style.configure('TCombobox',fieldbackground='#21334d',foreground=TEXT,arrowcolor=TEXT)
        style.map('TCombobox',fieldbackground=[('readonly','#21334d')],foreground=[('readonly',TEXT)])
        style.configure('Treeview',background=PANEL,fieldbackground=PANEL,foreground=TEXT,rowheight=26)
        style.configure('Treeview.Heading',background='#23344e',foreground=TEXT)
        style.map('Treeview',background=[('selected','#316272')])
        self.geo=Geometry(); self.settings=Settings(); self.case=Case()
        self.vars={}; self.running=True; self.speed_factor=tk.DoubleVar(value=1)
        self.show_trails=tk.BooleanVar(value=True); self.events=queue.Queue(); self.cancel=threading.Event()
        self.busy=False; self.batch_results=[]; self.best=None; self.search_report=None; self.sim=None
        self._build(); self.reset(); self.last_clock=time.perf_counter(); self.accumulator=0
        prior=HERE/'validation_report.json'
        if prior.exists():
            data=json.loads(prior.read_text(encoding='utf-8')); self.batch_results=data['baseline_results']
            self.results_geo=Geometry(**data['geometry']); self.results_settings=Settings(**data['settings']); self.populate(self.batch_results)
            self.status.set(f'Loaded {len(self.batch_results)} baseline test results. Click a row to replay its exact scenario.')
        prior_search=HERE/'verified_search.json'
        if prior_search.exists():
            self.search_report=json.loads(prior_search.read_text(encoding='utf-8'))
            self.best=Geometry(**self.search_report['best_geometry']); self.apply_best.configure(state='normal')
        root.protocol('WM_DELETE_WINDOW',self.close)
        self.tick()

    def label(self,parent,text,size=10,color=TEXT,bold=False):
        return tk.Label(parent,text=text,bg=PANEL,fg=color,font=('Segoe UI',size,'bold' if bold else 'normal'),anchor='w')

    def _build(self):
        head=tk.Frame(self.root,bg=BG); head.pack(fill='x',padx=22,pady=(15,12))
        tk.Label(head,text='FUNNEL LAB',font=('Segoe UI',21,'bold'),bg=BG,fg=TEXT).pack(side='left')
        tk.Label(head,text='  /  FTC transfer geometry',font=('Segoe UI',12),bg=BG,fg=MUTED).pack(side='left')
        tk.Label(head,text='74 mm balls   •   75 mm outlet   •   168 mm mounts',bg=BG,fg=CYAN,font=('Segoe UI',11,'bold')).pack(side='right')
        main=tk.Frame(self.root,bg=BG); main.pack(fill='both',expand=True,padx=18,pady=(0,12))
        left=tk.Frame(main,bg=PANEL,width=335); left.pack(side='left',fill='y',padx=(0,12)); left.pack_propagate(False)
        sc=tk.Canvas(left,bg=PANEL,highlightthickness=0,width=315)
        sb=ttk.Scrollbar(left,orient='vertical',command=sc.yview); sb.pack(side='right',fill='y'); sc.pack(fill='both',expand=True)
        sc.configure(yscrollcommand=sb.set)
        controls=tk.Frame(sc,bg=PANEL); sc.create_window((0,0),window=controls,anchor='nw',width=310)
        controls.bind('<Configure>',lambda e:sc.configure(scrollregion=sc.bbox('all')))
        def wheel(e): sc.yview_scroll(int(-e.delta/120),'units')
        sc.bind('<Enter>',lambda e:sc.bind_all('<MouseWheel>',wheel))
        sc.bind('<Leave>',lambda e:sc.unbind_all('<MouseWheel>'))
        def section(text): self.label(controls,text,11,CYAN,True).pack(fill='x',padx=14,pady=(16,6))
        section('ENTRY SCENARIO')
        self.pattern=tk.StringVar(value=self.case.name)
        ttk.Combobox(controls,textvariable=self.pattern,values=PATTERNS,state='readonly').pack(fill='x',padx=14,pady=3)
        self.field(controls,'angle','Approach angle (deg)',0)
        self.field(controls,'orientation','Ball line angle (deg)',0)
        self.field(controls,'offset','Lateral offset (mm)',0)
        self.field(controls,'spacing','Ball center spacing (mm)',80)
        self.field(controls,'stagger','Row stagger (mm)',0)
        self.field(controls,'seed','Scatter seed',1)
        section('FUNNEL GEOMETRY · mm')
        for key,label in [('left_lip','Left lip'),('right_lip','Right lip'),('left_straight','Left outlet straight'),('right_straight','Right outlet straight'),('left_radius','Left curve radius'),('right_radius','Right curve radius'),('left_drop','Left wedge drop'),('right_drop','Right wedge drop'),('left_bow','Left wedge curve'),('right_bow','Right wedge curve')]:
            self.field(controls,key,label,getattr(self.geo,key))
        self.angle_text=tk.StringVar()
        self.label(controls,'Angles measured from horizontal',9,MUTED).pack(fill='x',padx=14,pady=(8,2))
        self.field(controls,'left_angle','Left wall angle (deg)',30)
        self.field(controls,'right_angle','Right wall angle (deg)',59.4)
        ttk.Button(controls,text='Use angles → update straight lengths',command=self.use_angles).pack(fill='x',padx=14,pady=5)
        section('FIXED PHYSICS · provisional values')
        for key,label in [('speed','Roller target speed (mm/s)'),('friction','Contact friction coefficient'),('response','Drive response (s)'),('acceleration','Traction limit (mm/s²)')]: self.field(controls,key,label,getattr(self.settings,key))
        self.label(controls,'Friction and ball size stay fixed during search.',9,MUTED).pack(fill='x',padx=14,pady=5)
        section('CONFIRMED MOUNTING DIMENSIONS · mm')
        self.field(controls,'mount_y','Top → mounting line',self.geo.mount_y)
        self.field(controls,'outer_span','Outer wedge anchors span',self.geo.outer_span)
        self.label(controls,'Fixed in search: 107.38628 / 280.35.',9,GOLD).pack(fill='x',padx=14,pady=5)
        ttk.Button(controls,text='Apply inputs & restart',command=self.apply).pack(fill='x',padx=14,pady=(12,5))
        ttk.Button(controls,text='Restore screenshot geometry',command=self.restore).pack(fill='x',padx=14,pady=5)
        ttk.Button(controls,text='Save setup…',command=self.save_setup).pack(fill='x',padx=14,pady=5)
        ttk.Button(controls,text='Load setup…',command=self.load_setup).pack(fill='x',padx=14,pady=5)
        ttk.Button(controls,text='Export geometry SVG…',command=self.export_svg).pack(fill='x',padx=14,pady=(5,18))
        right=tk.Frame(main,bg=BG); right.pack(side='left',fill='both',expand=True)
        toolbar=tk.Frame(right,bg=PANEL); toolbar.pack(fill='x')
        self.play=ttk.Button(toolbar,text='Pause',command=self.toggle); self.play.pack(side='left',padx=8,pady=8)
        ttk.Button(toolbar,text='Restart',command=self.reset).pack(side='left',padx=4)
        ttk.Button(toolbar,text='Step 0.1 s',command=self.step_once).pack(side='left',padx=4)
        self.label(toolbar,'Playback').pack(side='left',padx=(16,3))
        ttk.Combobox(toolbar,textvariable=self.speed_factor,values=[.25,.5,1,2,4],width=5,state='readonly').pack(side='left')
        ttk.Checkbutton(toolbar,text='Trails',variable=self.show_trails).pack(side='left',padx=12)
        self.stats=tk.StringVar(); tk.Label(right,textvariable=self.stats,bg=BG,fg=TEXT,font=('Segoe UI',11),anchor='w').pack(fill='x',pady=8)
        self.canvas=tk.Canvas(right,bg='#101c2e',highlightthickness=0); self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda e:self.draw())
        tk.Label(right,text='Uncalibrated 2D physics • upward roller drive • no gravity in this view • fixed friction',bg=BG,fg=MUTED,font=('Segoe UI',9),anchor='w').pack(fill='x',pady=5)
        actions=tk.Frame(right,bg=PANEL); actions.pack(fill='x',pady=(4,6))
        ttk.Button(actions,text=f'Run {len(suite())} test cases',command=self.batch).pack(side='left',padx=6,pady=8)
        ttk.Button(actions,text='Search 24 geometries',command=self.search).pack(side='left',padx=4)
        ttk.Button(actions,text='Stop',command=self.cancel.set).pack(side='left',padx=4)
        self.apply_best=ttk.Button(actions,text='Apply search candidate',command=self.use_best,state='disabled'); self.apply_best.pack(side='left',padx=4)
        ttk.Button(actions,text='Export results',command=self.export_results).pack(side='right',padx=6)
        self.status=tk.StringVar(value='Ready. Change inputs, then Apply. Select a test result to replay it.')
        tk.Label(right,textvariable=self.status,bg=BG,fg=GOLD,font=('Segoe UI',10),anchor='w',wraplength=1000).pack(fill='x',pady=(2,6))
        cols=('case','angle','fed','jam','time','missed')
        self.table=ttk.Treeview(right,columns=cols,show='headings',height=5)
        for c,title,width in zip(cols,['Scenario · click to replay','Angle','Delivered','Jam','Last exit / s','Missed'],[290,65,80,65,95,65]):
            self.table.heading(c,text=title); self.table.column(c,width=width,stretch=c=='case')
        self.table.pack(fill='x'); self.table.bind('<<TreeviewSelect>>',self.replay)

    def field(self,parent,key,label,value):
        row=tk.Frame(parent,bg=PANEL); row.pack(fill='x',padx=14,pady=3)
        self.label(row,label,9).pack(side='left')
        var=tk.StringVar(value=f'{value:g}'); self.vars[key]=var
        ttk.Entry(row,textvariable=var,width=11,justify='right',state='readonly' if key in ('mount_y','outer_span') else 'normal').pack(side='right')

    def read_geo(self): return Geometry(**{k:float(self.vars[k].get()) for k in self.geo.dict()})
    def read_settings(self):
        return Settings(**{k:float(self.vars[k].get()) for k in ['speed','friction','response','acceleration']})
    def read_case(self):
        return Case(self.pattern.get(),*[float(self.vars[k].get()) for k in ['angle','offset','spacing','stagger']],int(self.vars['seed'].get()),float(self.vars['orientation'].get()))

    def sync_geo(self):
        for k,v in self.geo.dict().items(): self.vars[k].set(f'{v:.6f}'.rstrip('0').rstrip('.'))
        for side in ['left','right']: self.vars[side+'_angle'].set(f'{self.geo.side(side)["angle"]:.3f}')

    def apply(self):
        if self.busy: self.status.set('Stop the current test/search before changing its configuration.'); return False
        try:
            geo=self.read_geo().validate(); settings=self.read_settings(); case=self.read_case()
            sim=Simulation(geo,settings,case)
        except (ValueError,OverflowError) as e: messagebox.showerror('Check inputs',str(e)); return False
        self.geo,self.settings,self.case,self.sim=geo,settings,case,sim
        self.sync_geo(); self.running=True; self.play.configure(text='Pause'); self.accumulator=0
        self.status.set('Inputs applied. All distances are mm; positive entry angles move balls toward the right.')
        self.draw(); return True

    def use_angles(self):
        if self.busy: return
        try:
            g=self.read_geo()
            for side in ['left','right']: g.set_angle(side,float(self.vars[side+'_angle'].get()))
            g.validate()
            for side in ['left','right']: self.vars[side+'_straight'].set(f'{getattr(g,side+"_straight"):.5f}')
            self.apply()
        except ValueError as e: messagebox.showerror('Angle does not fit',str(e))

    def restore(self):
        if self.busy: return
        self.geo=Geometry(); self.sync_geo(); self.reset()

    def reset(self):
        self.sim=Simulation(self.geo,self.settings,self.case); self.running=True; self.accumulator=0
        self.play.configure(text='Pause'); self.sync_geo(); self.draw()

    def toggle(self):
        self.running=not self.running; self.play.configure(text='Pause' if self.running else 'Play')

    def step_once(self):
        self.running=False; self.play.configure(text='Play')
        if not self.sim.done: self.sim.step(round(.1/self.settings.dt))
        self.draw()

    def tick(self):
        now=time.perf_counter(); elapsed=min(.06,now-self.last_clock); self.last_clock=now
        if self.running and not self.busy and not self.sim.done:
            self.accumulator+=elapsed*self.speed_factor.get()
            n=min(120,int(self.accumulator/self.settings.dt))
            if n: self.sim.step(n); self.accumulator-=n*self.settings.dt
        self.poll(); self.draw(); self.root.after(25,self.tick)

    def draw(self):
        if not self.sim: return
        c=self.canvas; c.delete('all'); w=max(c.winfo_width(),400); h=max(c.winfo_height(),250)
        bottom=max(260,max((p[1] for p in self.sim.initial),default=210)+50)
        half=max(210,self.geo.outer_span/2+60,max((abs(p[0]) for p in self.sim.initial),default=0)+45)
        scale=min((w-70)/(2*half),(h-42)/(bottom+65)); cx=w/2; oy=50*scale+20
        def xy(x,y): return cx+x*scale,oy+y*scale
        def line(points,**kw): c.create_line(*[v for p in points for v in xy(*p)],**kw)
        for x in range(-int(half)//25*25,int(half)+1,25): line([(x,-40),(x,bottom)],fill='#1a2c43')
        for y in range(0,int(bottom)+1,25): line([(-half,y),(half,y)],fill='#1a2c43')
        line([(0,-45),(0,bottom)],fill='#36516e',dash=(4,7))
        for side in ['left','right']:
            g=self.geo.side(side)
            c.create_polygon(*[v for p in g['polygon'] for v in xy(*p)],fill='#2a3b53',outline='#5e7594',width=1)
            line(g['edge'],fill=CYAN,width=2.5)
            sign=-1 if side=='left' else 1
            for p in [(sign*84,0),(sign*84,self.geo.mount_y),(sign*self.geo.outer_span/2,self.geo.mount_y)]:
                x,y=xy(*p); c.create_rectangle(x-3,y-3,x+3,y+3,fill=GOLD,outline='')
            x,y=xy(sign*115,45)
            c.create_text(x,y,text=f'{side.upper()}\n{g["angle"]:.1f}°\nR{getattr(self.geo,side+"_radius"):.1f}',fill=MUTED,font=('Segoe UI',9),justify='center')
        line([(-84,0),(84,0)],fill=GOLD,dash=(3,4),width=1)
        # Dimension annotations are visual references, not collision walls.
        line([(-37.5,-12),(37.5,-12)],fill=TEXT,arrow='both')
        x,y=xy(0,-25); c.create_text(x,y,text='75 mm outlet  ↑',fill=TEXT,font=('Segoe UI',10,'bold'))
        line([(-84,self.geo.mount_y),(84,self.geo.mount_y)],fill='#60738e',dash=(4,5))
        x,y=xy(0,self.geo.mount_y+8); c.create_text(x,y,text='168 mm mounts',fill=MUTED,font=('Segoe UI',9))
        if self.show_trails.get():
            for b in self.sim.balls:
                if len(b['trail'])>1: line(b['trail'],fill='#3b6870',width=1)
        for ball in self.sim.balls:
            b=ball['body']; x,y=xy(*b.position); r=BALL_D/2*scale; color=COLORS[(ball['id']-1)%len(COLORS)]
            c.create_oval(x-r,y-r,x+r,y+r,fill='#20364d',outline=color,width=2)
            c.create_line(x,y,x+math.cos(b.angle)*r*.8,y+math.sin(b.angle)*r*.8,fill=color,width=1)
            c.create_text(x,y,text=str(ball['id']),fill=TEXT,font=('Segoe UI',11,'bold'))
            v=b.velocity
            c.create_line(x,y,x+v.x*scale*.12,y+v.y*scale*.12,fill=color,arrow='last',width=1.5)
        c.create_text(14,h-14,anchor='sw',text='Squares: fixed anchors    •    Colored arrows: actual velocity',fill=MUTED,font=('Segoe UI',9))
        status='JAM DETECTED' if self.sim.jam_seen else ('FINISHED' if self.sim.done else 'RUNNING' if self.running else 'PAUSED')
        self.stats.set(f'{self.sim.time:4.2f} s    |    Delivered {len(self.sim.exits)} / {self.sim.total}    |    Missed {len(self.sim.missed)}    |    {status}    |    Order: '+(' → '.join(str(x['id']) for x in self.sim.exits) or '—'))

    def start_job(self, fn):
        if self.busy: return
        if not self.apply(): return
        self.busy=True; self.running=False; self.play.configure(text='Play'); self.cancel.clear()
        def work():
            try: fn()
            except Exception as e: self.events.put(('error',str(e)))
            finally: self.events.put(('done',None))
        threading.Thread(target=work,daemon=True).start()

    def batch(self):
        def job():
            results=evaluate(self.geo,self.settings,suite(),self.cancel.is_set,
                             lambda i,n,r:self.events.put(('progress',f'Testing {i}/{n}: {r["case"]["name"]}, {r["case"]["angle"]:+g}°')))
            self.events.put(('batch',results))
        self.start_job(job)

    def search(self):
        def job():
            def update(item):
                s=item['summary']; self.events.put(('progress',f'Candidate {item["candidate"]+1}/24 • {s["fed"]}/{s["total"]} delivered • {s["jams"]} jams. Separate validation follows.'))
            report=optimize(self.geo,self.settings,24,self.cancel.is_set,update)
            self.events.put(('search',report))
        self.start_job(job)

    def poll(self):
        while not self.events.empty():
            kind,data=self.events.get()
            if kind=='progress': self.status.set(data)
            elif kind=='done': self.busy=False
            elif kind=='error': self.status.set('Run failed: '+data)
            elif kind=='batch':
                self.batch_results=data; self.results_geo=Geometry(**self.geo.dict()); self.results_settings=Settings(**asdict(self.settings)); self.populate(data)
                s=summarize(data); self.status.set(f'{s["cases"]} cases • {s["fed"]}/{s["total"]} delivered • {s["jams"]} jams • {s["unfinished"]} unfinished • {s["missed"]} missed. Select a row to replay.'+(' Stopped early.' if self.cancel.is_set() else ''))
            elif kind=='search':
                self.search_report=data
                if data['cancelled']: self.status.set('Search stopped. No incomplete candidate applied.'); continue
                self.best=Geometry(**data['best_geometry']); self.apply_best.configure(state='normal')
                a,b=data['baseline_validation'],data['best_validation']
                self.batch_results=data['validation_results']; self.results_geo=self.best; self.results_settings=Settings(**data['settings']); self.populate(self.batch_results)
                self.status.set(f'Holdout: baseline {a["fed"]}/{a["total"]}, {a["jams"]} jams → candidate {b["fed"]}/{b["total"]}, {b["jams"]} jams. Inspect replays before adopting it.')
                (HERE/'latest_search.json').write_text(json.dumps(data,indent=2),encoding='utf-8')

    def populate(self,results):
        for i in self.table.get_children(): self.table.delete(i)
        for i,r in enumerate(results):
            desc=f'{r["case"]["name"]} · line {r["case"]["orientation"]:+g}° · x {r["case"]["offset"]:+g}'
            self.table.insert('', 'end',iid=str(i),values=(desc,f'{r["case"]["angle"]:+g}°',f'{r["fed"]}/{r["total"]}', 'YES' if r['jam'] else '—',r['last_exit'],r['missed']))

    def replay(self,e=None):
        if self.busy: return
        selected=self.table.selection()
        if not selected: return
        r=self.batch_results[int(selected[0])]
        self.geo=Geometry(**self.results_geo.dict()); self.settings=Settings(**asdict(self.results_settings)); self.case=Case(**r['case'])
        self.pattern.set(self.case.name)
        for k in ['angle','offset','spacing','stagger','seed','orientation']: self.vars[k].set(str(getattr(self.case,k)))
        for k in ['speed','friction','response','acceleration']: self.vars[k].set(str(getattr(self.settings,k)))
        self.sync_geo(); self.reset(); self.status.set('Replaying the exact geometry, settings and entry scenario from this result.')

    def use_best(self):
        if self.best and not self.busy:
            self.geo=Geometry(**self.best.dict()); self.sync_geo(); self.reset()
            if self.search_report:
                a=self.search_report['baseline_validation']; b=self.search_report['best_validation']
                self.status.set(f'Candidate applied for inspection. Holdout delivered: baseline {a["fed"]}/{a["total"]} → candidate {b["fed"]}/{b["total"]}; jams {a["jams"]} → {b["jams"]}.')

    def save_setup(self):
        if not self.apply(): return
        path=filedialog.asksaveasfilename(initialdir=HERE,defaultextension='.json',initialfile='my_funnel.json')
        if path: Path(path).write_text(json.dumps({'geometry':self.geo.dict(),'settings':asdict(self.settings),'case':asdict(self.case)},indent=2),encoding='utf-8')

    def load_setup(self):
        if self.busy: return
        path=filedialog.askopenfilename(initialdir=HERE,filetypes=[('Setup JSON','*.json')])
        if not path: return
        try:
            data=json.loads(Path(path).read_text(encoding='utf-8'))
            geo=Geometry(**data['geometry']); settings=Settings(**data['settings']); case=Case(**data['case'])
            sim=Simulation(geo,settings,case)
            self.geo,self.settings,self.case,self.sim=geo,settings,case,sim
            self.sync_geo(); self.pattern.set(case.name)
            for k in ['angle','offset','spacing','stagger','seed','orientation']: self.vars[k].set(str(getattr(case,k)))
            for k in ['speed','friction','response','acceleration']: self.vars[k].set(str(getattr(settings,k)))
            self.reset()
        except (ValueError,TypeError,KeyError) as e: messagebox.showerror('Cannot load setup',str(e))

    def export_svg(self):
        if not self.apply(): return
        path=filedialog.asksaveasfilename(initialdir=HERE,defaultextension='.svg',initialfile='funnel_geometry.svg')
        if path: write_svg(self.geo,path); self.status.set('SVG saved at 1 mm per drawing unit with your confirmed anchor dimensions.')

    def export_results(self):
        if not self.batch_results: self.status.set('Run test cases or a search first.'); return
        path=filedialog.asksaveasfilename(initialdir=HERE,defaultextension='.json',initialfile='test_results.json')
        if not path: return
        p=Path(path)
        p.write_text(json.dumps({'geometry':self.results_geo.dict(),'settings':asdict(self.results_settings),'summary':summarize(self.batch_results),'results':self.batch_results,'search':self.search_report},indent=2),encoding='utf-8')
        with p.with_suffix('.csv').open('w',newline='',encoding='utf-8') as f:
            rows=[{**r['case'],**{k:v for k,v in r.items() if k not in ('case','order')},'order':' '.join(map(str,r['order']))} for r in self.batch_results]
            writer=csv.DictWriter(f,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
        self.status.set('Saved detailed JSON plus a matching CSV table.')

    def close(self): self.cancel.set(); self.root.destroy()

def write_svg(geo,path):
    width=geo.outer_span+20; height=geo.entrance_y+20
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}mm" height="{height}mm" viewBox="{-width/2} -10 {width} {height}">',
           '<title>Funnel geometry in mm; 75 mm outlet, 168 mm mounts, 280.35 mm outer anchors</title>']
    for side in ['left','right']:
        pts=' '.join(f'{x:.5f},{y:.5f}' for x,y in geo.side(side)['polygon'])
        parts.append(f'<polygon points="{pts}" fill="none" stroke="black" stroke-width="0.15"/>')
    parts.append('</svg>'); Path(path).write_text('\n'.join(parts),encoding='utf-8')

if __name__=='__main__':
    root=tk.Tk(); app=App(root)
    if '--capture' in sys.argv:
        from PIL import ImageGrab
        app.running=False; app.sim.step(500); app.draw(); root.update()
        def capture():
            try: ImageGrab.grab(window=int(root.tk.call('wm','frame',root._w),16)).save(HERE/'preview.png')
            finally: root.destroy()
        root.after(400,capture)
        root.mainloop()
    elif '--smoke' in sys.argv:
        root.update(); app.running=False; app.step_once()
        assert app.canvas.find_all() and app.sim.time>.09
        app.pattern.set('Two abreast'); app.vars['angle'].set('25'); assert app.apply()
        app.sim.step(960); app.draw(); root.update()
        print('GUI smoke passed: controls, canvas, apply, stepping, diagonal input.')
        root.destroy()
    else: root.mainloop()
