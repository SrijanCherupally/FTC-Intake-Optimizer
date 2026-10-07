"""Funnel Lab candidate studio with a persistent adaptive search library."""
import argparse, sqlite3
import csv, json, multiprocessing as mp, queue, sys, threading, time
from pathlib import Path
from dataclasses import asdict
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from geometry import Geometry
from physics import Settings, Case, PATTERNS, Simulation, suite
from viewer import Viewer, write_svg
from testing_panel import TestingPanel
from library import Library, metrics
from adaptive import SearchConfig, SearchRunner, create_session
from search import evaluate

HERE=Path(__file__).resolve().parent
BG='#0b1020'; PANEL='#131d31'; CARD='#19253c'; TEXT='#e8effc'; MUTED='#90a2bf'; CYAN='#67ddd0'; GOLD='#f6c879'; RED='#fb9eaa'

class App(TestingPanel,Viewer):
    def __init__(self,root,db_path=None,read_only=True):
        self.read_only=read_only; self.db_version=None
        self.root=root; root.title('Funnel Lab · Results Viewer' if read_only else 'Funnel Lab · Candidate Studio')
        root.geometry(f'{min(1520,root.winfo_screenwidth()-60)}x{min(970,root.winfo_screenheight()-90)}+20+20'); root.minsize(1100,740); root.configure(bg=BG)
        self.db_path=Path(db_path or HERE/'data'/'funnel_lab.sqlite3'); self.lib=Library(self.db_path,read_only=read_only)
        if not read_only: self.lib.import_legacy(HERE)
        if not read_only and not self.lib.candidates(): self.lib.add('Original CAD',Geometry().dict(),asdict(Settings()),cid='original-cad')
        self.geo=Geometry(); self.settings=Settings(); self.case=Case(); self.vars={}; self.sim=None
        self.running=False; self.busy=False; self.events=queue.Queue(); self.cancel=threading.Event(); self.job=None
        self.speed_factor=tk.DoubleVar(value=1); self.show_trails=tk.BooleanVar(value=True)
        self.selected=None; self.selected_run=None; self.visible_results=[]; self.active_session=None; self.run_rows=[]; self.run_names=[]
        self.last_refresh=0.; self.library_dirty=False; self.refresh_pending=None
        self.styles(); self.build(); self.refresh_library()
        candidates=self.lib.candidates()
        if candidates: self.select_candidate(candidates[0]['id'])
        else:
            self.sync_inputs(); self.reset(); self.running=False; self.play.configure(text='Play')
            self.title.set('Waiting for training results'); self.status.set('Start training with train.cmd start. Results appear here automatically.')
        sessions=self.lib.sessions()
        if sessions: self.active_session=sessions[0]['id']; self.update_session()
        self.last_clock=time.perf_counter(); self.accumulator=0.; root.protocol('WM_DELETE_WINDOW',self.close); self.tick()

    def styles(self):
        s=ttk.Style(); s.theme_use('clam')
        s.configure('.',background=PANEL,foreground=TEXT,font=('Segoe UI',10),borderwidth=0)
        s.configure('TButton',padding=(12,8),background='#263653',foreground=TEXT)
        s.map('TButton',background=[('active','#355478'),('disabled','#172136')],foreground=[('disabled','#66778f')])
        s.configure('Accent.TButton',background='#287f80',foreground='#ffffff',font=('Segoe UI',10,'bold'))
        s.configure('TEntry',fieldbackground='#1d2c47',foreground=TEXT,insertcolor=TEXT,padding=5)
        s.configure('TCombobox',fieldbackground='#1d2c47',foreground=TEXT,arrowcolor=TEXT,padding=5)
        s.map('TCombobox',fieldbackground=[('readonly','#1d2c47')],foreground=[('readonly',TEXT)])
        s.configure('Treeview',background=PANEL,fieldbackground=PANEL,foreground=TEXT,rowheight=33,borderwidth=0)
        s.configure('Treeview',bordercolor=PANEL,lightcolor=PANEL,darkcolor=PANEL)
        s.configure('Treeview.Heading',background=CARD,foreground=MUTED,font=('Segoe UI',9,'bold'),padding=8)
        s.map('Treeview',background=[('selected','#254962')],foreground=[('selected','#ffffff')])
        s.configure('TNotebook',background=BG,tabmargins=(0,0,0,10)); s.configure('TNotebook.Tab',background=PANEL,foreground=MUTED,padding=(20,11))
        s.map('TNotebook.Tab',background=[('selected',CARD)],foreground=[('selected',CYAN)])
        s.configure('TProgressbar',background=CYAN,troughcolor=CARD); s.configure('TCheckbutton',background=PANEL,foreground=MUTED)

    def text(self,parent,text='',size=10,color=TEXT,bold=False,var=None,**kw):
        return tk.Label(parent,text=text,textvariable=var,bg=parent.cget('bg'),fg=color,font=('Segoe UI',size,'bold' if bold else 'normal'),anchor='w',**kw)
    def button(self,parent,label,command,accent=False):
        return ttk.Button(parent,text=label,command=command,style='Accent.TButton' if accent else 'TButton')
    def tree(self,parent,columns,widths,height=10):
        frame=tk.Frame(parent,bg=PANEL); frame.pack(fill='both',expand=True)
        t=ttk.Treeview(frame,columns=list(columns),show='headings',height=height,selectmode='browse')
        for i,(col,title) in enumerate(columns.items()):
            t.heading(col,text=title); t.column(col,width=widths[i],minwidth=45,stretch=i==0)
        sb=ttk.Scrollbar(frame,orient='vertical',command=t.yview); t.configure(yscrollcommand=sb.set); sb.pack(side='right',fill='y'); t.pack(fill='both',expand=True)
        return t

    def build(self):
        head=tk.Frame(self.root,bg=BG); head.pack(fill='x',padx=24,pady=(18,14))
        self.text(head,'FUNNEL LAB',22,bold=True).pack(side='left'); self.text(head,'  /  results viewer' if self.read_only else '  /  candidate studio',12,MUTED).pack(side='left')
        self.text(head,'74 mm balls   /   75 mm outlet   /   168 mm mounts',10,CYAN,True).pack(side='right')
        body=tk.Frame(self.root,bg=BG); body.pack(fill='both',expand=True,padx=18,pady=(0,14))
        side=tk.Frame(body,bg=PANEL,width=300); side.pack(side='left',fill='y',padx=(0,14)); side.pack_propagate(False)
        self.text(side,'GEOMETRY CANDIDATES',10,CYAN,True).pack(fill='x',padx=14,pady=(16,5))
        self.count_text=tk.StringVar(); self.text(side,var=self.count_text,size=9,color=MUTED).pack(fill='x',padx=14)
        self.scope=tk.StringVar(value='All candidates')
        scope_combo=ttk.Combobox(side,textvariable=self.scope,values=['All candidates','Current search'],state='readonly')
        scope_combo.pack(fill='x',padx=12,pady=(10,0)); scope_combo.bind('<<ComboboxSelected>>',lambda e:self.refresh_library())
        self.sort_order=tk.StringVar(value='Stars first')
        self.sort_combo=ttk.Combobox(side,textvariable=self.sort_order,values=['Stars first','Best first','Worst first','Newest first'],state='readonly')
        self.sort_combo.pack(fill='x',padx=12,pady=(8,0)); self.sort_combo.bind('<<ComboboxSelected>>',lambda e:self.refresh_library())
        self.text(side,'Score: jam-free % · stall breaks ties\n* provisional; different tests may differ',8,MUTED,justify='left').pack(fill='x',padx=14,pady=(5,0))
        self.filter_text=tk.StringVar(); ttk.Entry(side,textvariable=self.filter_text).pack(fill='x',padx=12,pady=10)
        self.filter_text.trace_add('write',lambda *a:self.schedule_filter()); self.only_stars=tk.BooleanVar(value=False)
        ttk.Checkbutton(side,text='Starred candidates only',variable=self.only_stars,command=self.refresh_library).pack(anchor='w',padx=14,pady=(0,10))
        self.candidate_tree=self.tree(side,{'candidate':'Candidate','score':'Clear %','stage':'Stage'},[145,65,60],height=5); self.candidate_tree.bind('<<TreeviewSelect>>',self.candidate_clicked)
        self.thumb=tk.Canvas(side,bg=PANEL,height=85,highlightthickness=0); self.thumb.pack(fill='x',padx=14,pady=(8,0))
        if not self.read_only: self.button(side,'★  Toggle favorite',self.toggle_star).pack(fill='x',padx=12,pady=10)
        self.button(side,'Clear history · keep stars',lambda:self.clear_history('unstarred')).pack(fill='x',padx=12,pady=(8,4))
        self.button(side,'Clear starred candidates',lambda:self.clear_history('starred')).pack(fill='x',padx=12,pady=(0,10))
        self.text(side,'Auto stars: top fresh-validation results.\nMisses do not affect ranking.',9,MUTED,justify='left').pack(fill='x',padx=14,pady=(0,14))
        right=tk.Frame(body,bg=BG); right.pack(side='left',fill='both',expand=True)
        self.tabs=ttk.Notebook(right); self.tabs.pack(fill='both',expand=True)
        self.overview=tk.Frame(self.tabs,bg=PANEL); self.live=tk.Frame(self.tabs,bg=PANEL); self.optimizer=tk.Frame(self.tabs,bg=PANEL)
        self.testing=tk.Frame(self.tabs,bg=PANEL)
        for frame,title in [(self.overview,'Candidate & tests'),(self.testing,'Test candidate'),(self.live,'Replay' if self.read_only else 'Live simulation'),(self.optimizer,'Training monitor' if self.read_only else 'Adaptive optimizer')]: self.tabs.add(frame,text=title)
        self.build_overview(); self.build_live(); self.build_optimizer(); self.build_testing()
        self.hardness_refresh=0.
        self.tabs.bind('<<NotebookTabChanged>>',lambda e:self.refresh_hardness(force=True))
        self.status=tk.StringVar(value='Select a geometry to inspect its test runs.'); self.text(right,var=self.status,size=9,color=MUTED,wraplength=1000).pack(fill='x',pady=(10,0))

    def build_overview(self):
        top=tk.Frame(self.overview,bg=PANEL); top.pack(fill='x',padx=20,pady=(18,12))
        self.title=tk.StringVar(value='Select a candidate'); self.text(top,var=self.title,size=20,bold=True).pack(side='left')
        self.button(top,'Open replay' if self.read_only else 'Open live view',lambda:self.tabs.select(self.live)).pack(side='right')
        self.candidate_meta=tk.StringVar(); self.text(self.overview,var=self.candidate_meta,size=10,color=MUTED).pack(fill='x',padx=20,pady=(0,12))
        cards=tk.Frame(self.overview,bg=PANEL); cards.pack(fill='x',padx=20,pady=(0,14)); self.card_vars={}
        for i,(key,label,color) in enumerate([('jamfree','JAM-FREE TESTS',CYAN),('jams','JAMS',RED),('runs','TESTS FINISHED',TEXT),('misses','MISSED · NOT SCORED',MUTED)]):
            card=tk.Frame(cards,bg=CARD); card.grid(row=0,column=i,sticky='ew',padx=(0,8)); cards.columnconfigure(i,weight=1)
            self.text(card,label,9,MUTED).pack(fill='x',padx=14,pady=(12,3)); v=tk.StringVar(value='—'); self.card_vars[key]=v
            self.text(card,var=v,size=23,color=color,bold=True).pack(fill='x',padx=14,pady=(0,12))
        bar=tk.Frame(self.overview,bg=PANEL); bar.pack(fill='x',padx=20,pady=6)
        self.run_var=tk.StringVar(); self.run_combo=ttk.Combobox(bar,textvariable=self.run_var,state='readonly',width=35); self.run_combo.pack(side='left'); self.run_combo.bind('<<ComboboxSelected>>',lambda e:self.show_run())
        self.jams_only=tk.BooleanVar(value=False); ttk.Checkbutton(bar,text='Jams only',variable=self.jams_only,command=self.show_run).pack(side='left',padx=12)
        self.button(bar,'Export selected run',self.export_results).pack(side='right')
        self.run_info=tk.StringVar(); self.text(self.overview,var=self.run_info,size=9,color=MUTED,wraplength=1000).pack(fill='x',padx=20,pady=(6,10))
        frame=tk.Frame(self.overview,bg=PANEL); frame.pack(fill='both',expand=True,padx=20,pady=(0,10))
        self.test_tree=self.tree(frame,{'case':'Formation / test','angle':'Entry','line':'Line','offset':'Offset','result':'Result','fed':'Fed','stall':'Stall / s'},[265,65,65,65,95,55,75])
        self.test_tree.tag_configure('jam',foreground=RED); self.test_tree.tag_configure('clear',foreground=CYAN); self.test_tree.bind('<<TreeviewSelect>>',self.replay_selected)
        self.text(self.overview,'Click a test to replay this candidate’s exact geometry and physics.',10,MUTED).pack(fill='x',padx=20,pady=(0,14))

    def build_live(self):
        if self.read_only:
            self.build_replay(); return
        pane=tk.PanedWindow(self.live,orient='horizontal',bg=PANEL,sashwidth=8); pane.pack(fill='both',expand=True,padx=10,pady=10)
        left=tk.Frame(pane,bg=PANEL); pane.add(left,width=295,minsize=285)
        sc=tk.Canvas(left,bg=PANEL,highlightthickness=0); sb=ttk.Scrollbar(left,command=sc.yview); sb.pack(side='right',fill='y'); sc.pack(fill='both',expand=True); sc.configure(yscrollcommand=sb.set)
        form=tk.Frame(sc,bg=PANEL); sc.create_window((0,0),window=form,anchor='nw',width=275); form.bind('<Configure>',lambda e:sc.configure(scrollregion=sc.bbox('all')))
        def section(title): self.text(form,title,10,CYAN,True).pack(fill='x',padx=14,pady=(14,8))
        section('ENTRY SCENARIO'); self.pattern=tk.StringVar(value=self.case.name)
        ttk.Combobox(form,textvariable=self.pattern,values=PATTERNS,state='readonly',width=23).pack(fill='x',padx=14,pady=5)
        for k,label,v in [('angle','Approach °',0),('orientation','Line orientation °',0),('offset','Lateral offset mm',0),('spacing','Center spacing mm',80),('stagger','Row stagger mm',0),('seed','Scatter seed',1)]: self.field(form,k,label,v)
        section('FUNNEL GEOMETRY · mm')
        for side in ['left','right']:
            for param,label in [('lip','lip'),('straight','outlet straight'),('radius','radius'),('drop','wedge drop'),('bow','wedge curve')]: self.field(form,side+'_'+param,side.title()+' '+label,getattr(self.geo,side+'_'+param))
        for side in ['left','right']: self.field(form,side+'_angle',side.title()+' wall angle °',self.geo.side(side)['angle'])
        self.button(form,'Use wall angles',self.use_angles).pack(fill='x',padx=14,pady=8); section('FIXED PHYSICS · provisional')
        for k,label in [('speed','Target speed mm/s'),('friction','Contact friction'),('response','Drive response s'),('acceleration','Traction mm/s²')]: self.field(form,k,label,getattr(self.settings,k))
        section('FIXED MOUNTS · mm')
        for k,label in [('mount_y','Mounting depth'),('outer_span','Outer anchor span')]: self.field(form,k,label,getattr(self.geo,k))
        for label,cmd in [('Apply & restart',self.apply),('Save as candidate',self.save_candidate),('Test this geometry',self.manual_tests),('Restore original CAD',self.restore),('Export geometry SVG',self.export_svg),('Save setup JSON',self.save_setup),('Load setup JSON',self.load_setup)]: self.button(form,label,cmd).pack(fill='x',padx=14,pady=4)
        self.text(form,' ',9).pack(); right=tk.Frame(pane,bg=PANEL); pane.add(right,minsize=420)
        bar=tk.Frame(right,bg=PANEL); bar.pack(fill='x',pady=(0,8))
        self.play=self.button(bar,'Play',self.toggle); self.play.pack(side='left',padx=3)
        self.button(bar,'Restart',self.reset).pack(side='left',padx=3); self.button(bar,'Step',self.step_once).pack(side='left',padx=3)
        ttk.Combobox(bar,textvariable=self.speed_factor,values=[.25,.5,1,2,4],width=4,state='readonly').pack(side='left',padx=5); ttk.Checkbutton(bar,text='Trails',variable=self.show_trails,command=self.draw).pack(side='left',padx=5)
        self.stats=tk.StringVar(); self.text(right,var=self.stats,size=9,wraplength=700).pack(fill='x',pady=5)
        self.canvas=tk.Canvas(right,bg='#101c2e',highlightthickness=0); self.canvas.pack(fill='both',expand=True); self.canvas.bind('<Configure>',lambda e:self.draw())
        self.text(right,'Uncalibrated 2D physics · fixed friction · finite roller drive',9,MUTED).pack(fill='x',pady=8)

    def build_optimizer(self):
        if self.read_only:
            self.build_monitor(); return
        p=self.optimizer
        self.text(p,'Find the shapes that stop jamming.',20,bold=True).pack(fill='x',padx=22,pady=(20,6))
        self.text(p,'Survey widely → learn the jam cases → refine → validate on fresh tests',11,MUTED).pack(fill='x',padx=22,pady=(0,16))
        boxes=tk.Frame(p,bg=PANEL); boxes.pack(fill='x',padx=20); self.config_vars={}; defaults=asdict(SearchConfig())
        groups=[('01  BROAD SURVEY',[('broad_geometries','Geometries'),('broad_tests','Tests per geometry')]),('02  JAM-FOCUSED SEARCH',[('focused_geometries','New geometries'),('hard_tests','Hard tests'),('coverage_tests','Coverage tests'),('round_size','Reanalyze every')]),('03  FRESH VALIDATION',[('finalists','Finalists'),('validation_tests','Fresh tests'),('workers','CPU workers'),('seed','Search seed')])]
        for i,(title,fields) in enumerate(groups):
            box=tk.Frame(boxes,bg=CARD); box.grid(row=0,column=i,sticky='nsew',padx=(0,10)); boxes.columnconfigure(i,weight=1)
            self.text(box,title,10,CYAN,True).pack(fill='x',padx=13,pady=(13,8))
            for k,label in fields:
                row=tk.Frame(box,bg=CARD); row.pack(fill='x',padx=13,pady=5); self.text(row,label,9,MUTED).pack(side='left')
                v=tk.StringVar(value=str(defaults[k])); self.config_vars[k]=v; ttk.Entry(row,textvariable=v,width=9,justify='right').pack(side='right')
            self.text(box,' ',5).pack()
        bar=tk.Frame(p,bg=PANEL); bar.pack(fill='x',padx=22,pady=15)
        self.button(bar,'Start new search',self.start_search,True).pack(side='left',padx=(0,8)); self.button(bar,'Pause & save',self.pause_search).pack(side='left',padx=4)
        self.button(bar,'Resume saved search',self.resume_search).pack(side='left',padx=4); self.button(bar,'Open winner',self.open_winner).pack(side='right')
        self.session_var=tk.StringVar(); self.session_combo=ttk.Combobox(p,textvariable=self.session_var,state='readonly'); self.session_combo.pack(fill='x',padx=22,pady=(0,8)); self.session_combo.bind('<<ComboboxSelected>>',self.session_changed)
        self.progress_text=tk.StringVar(value='Default: 100 × 240 broad tests, then 2,000 focused candidates. Progress is saved locally.')
        self.text(p,var=self.progress_text,size=10,wraplength=1000).pack(fill='x',padx=22,pady=7)
        self.progress=ttk.Progressbar(p,mode='determinate'); self.progress.pack(fill='x',padx=22,pady=8)
        self.text(p,'HARDEST JAM CASES',10,CYAN,True).pack(fill='x',padx=22,pady=(16,4))
        self.text(p,'Each geometry votes once per test. Escaped balls are never penalized.',9,MUTED).pack(fill='x',padx=22,pady=(0,8))
        frame=tk.Frame(p,bg=PANEL); frame.pack(fill='both',expand=True,padx=22,pady=(0,14))
        self.hard_tree=self.tree(frame,{'case':'Formation','angle':'Entry','line':'Line','offset':'Offset','rate':'Jam rate','count':'Jams / tried'},[220,75,75,75,95,110],height=6)

    def export_svg(self):
        if not self.read_only: return super().export_svg()
        if not self.selected: return
        path=filedialog.asksaveasfilename(initialdir=HERE,defaultextension='.svg',initialfile='funnel_geometry.svg')
        if path: write_svg(self.geo,path); self.status.set('Exported the selected geometry in mm.')

    def build_replay(self):
        # Shared playback methods use these values, but the viewer never edits them.
        values={**self.geo.dict(),**asdict(self.settings),**asdict(self.case),'left_angle':0,'right_angle':0}
        self.vars={k:tk.StringVar(value=str(v)) for k,v in values.items()}; self.pattern=tk.StringVar(value=self.case.name)
        right=tk.Frame(self.live,bg=PANEL); right.pack(fill='both',expand=True,padx=20,pady=16)
        self.text(right,'Replay a saved test',20,bold=True).pack(fill='x')
        self.text(right,'Choose a candidate, then click one of its tests. Geometry and physics match that saved run.',10,MUTED).pack(fill='x',pady=(5,14))
        bar=tk.Frame(right,bg=PANEL); bar.pack(fill='x')
        self.play=self.button(bar,'Play',self.toggle); self.play.pack(side='left',padx=3)
        self.button(bar,'Restart',self.reset).pack(side='left',padx=3); self.button(bar,'Step',self.step_once).pack(side='left',padx=3)
        ttk.Combobox(bar,textvariable=self.speed_factor,values=[.25,.5,1,2,4],width=4,state='readonly').pack(side='left',padx=8)
        ttk.Checkbutton(bar,text='Trails',variable=self.show_trails,command=self.draw).pack(side='left')
        self.button(bar,'Export geometry SVG',self.export_svg).pack(side='right')
        self.stats=tk.StringVar(); self.text(right,var=self.stats,size=10).pack(fill='x',pady=12)
        self.canvas=tk.Canvas(right,bg='#101c2e',highlightthickness=0); self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda e:self.draw())
        self.text(right,'Uncalibrated 2D physics · fixed friction · finite roller drive',9,MUTED).pack(fill='x',pady=8)

    def build_monitor(self):
        p=self.optimizer
        self.text(p,'Training runs in the terminal.',20,bold=True).pack(fill='x',padx=22,pady=(20,8))
        self.text(p,'train.cmd start     /     train.cmd pause     /     train.cmd resume',11,CYAN).pack(fill='x',padx=22,pady=8)
        self.text(p,'Results refresh automatically. History cleanup is available in the sidebar; closing the viewer leaves training running.',10,MUTED).pack(fill='x',padx=22,pady=8)
        self.button(p,'Open validated winner',self.open_winner).pack(anchor='e',padx=22,pady=8)
        self.session_var=tk.StringVar(); self.session_combo=ttk.Combobox(p,textvariable=self.session_var,state='readonly')
        self.session_combo.pack(fill='x',padx=22,pady=8); self.session_combo.bind('<<ComboboxSelected>>',self.session_changed)
        self.progress_text=tk.StringVar(value='No training session yet.'); self.text(p,var=self.progress_text,wraplength=950).pack(fill='x',padx=22,pady=8)
        self.progress=ttk.Progressbar(p,mode='determinate'); self.progress.pack(fill='x',padx=22,pady=8)
        self.text(p,'HARDEST JAM CASES',10,CYAN,True).pack(fill='x',padx=22,pady=(16,6))
        self.text(p,'Escapes do not affect ranking. Auto stars appear after fresh validation.',10,MUTED).pack(fill='x',padx=22,pady=(0,10))
        frame=tk.Frame(p,bg=PANEL); frame.pack(fill='both',expand=True,padx=22,pady=(0,16))
        self.hard_tree=self.tree(frame,{'case':'Formation','angle':'Entry','line':'Line','offset':'Offset','rate':'Jam rate','count':'Jams / tried'},[220,75,75,75,95,110],height=6)

    def clear_history(self,mode):
        if self.busy:
            self.status.set('Wait for the candidate test to finish before clearing history.'); return
        detail=('Delete unstarred candidates and their tests? Starred candidates and their tests stay.' if mode=='unstarred'
                else 'Delete starred candidates and all their saved tests? Unstarred candidates stay.')
        if not messagebox.askyesno('Clear saved history',detail+'\n\nSearch checkpoints will also be cleared. This cannot be undone.'): return
        writer=None
        try:
            writer=Library(self.db_path); count=writer.clear_history(mode)
        except (sqlite3.Error,RuntimeError) as e:
            messagebox.showerror('History was not fully cleared',str(e)); return
        finally:
            if writer: writer.close()
        self.scope.set('All candidates'); self.filter_text.set('')
        self.only_stars.set(mode=='unstarred'); self.poll_external(force=True)
        self.status.set(f'Cleared {count:,} candidates and reclaimed database space.')

    def empty_selection(self):
        self.selected=None; self.selected_run=None; self.visible_results=[]; self.run_rows=[]; self.run_names=[]
        self.running=False; self.sim=None; self.play.configure(text='Play')
        self.test_candidate_name.set('Select a candidate in the sidebar')
        self.title.set('No saved candidates'); self.candidate_meta.set(''); self.thumb.delete('all'); self.canvas.delete('all'); self.stats.set('')
        self.run_var.set(''); self.run_combo.configure(values=[]); self.show_run()

    def poll_external(self,force=False):
        if not force and time.monotonic()-self.last_refresh<2: return
        self.last_refresh=time.monotonic()
        try:
            # An empty viewer starts in memory and attaches when the trainer creates the file.
            if not self.lib.db.execute('PRAGMA database_list').fetchone()[2] and self.db_path.exists():
                new=Library(self.db_path,read_only=True)
                try: new.sessions()  # The trainer may still be creating its schema.
                except sqlite3.Error:
                    new.close(); raise
                self.lib.close(); self.lib=new; self.db_version=None
            version=self.lib.db.execute('PRAGMA data_version').fetchone()[0]
            if not force and version==self.db_version:
                self.refresh_hardness(); return
            self.db_version=version
            sessions=self.lib.sessions()
            if self.active_session not in {s['id'] for s in sessions}: self.active_session=sessions[0]['id'] if sessions else None
            if self.selected and not self.lib.candidate(self.selected): self.empty_selection()
            self.update_session(); self.refresh_library(); self.refresh_saved_tests()
            if not self.selected:
                rows=self.lib.candidates()
                if rows: self.select_candidate(rows[0]['id'])
            elif self.selected:
                candidate=self.lib.candidate(self.selected)
                self.title.set(('★ ' if candidate['starred'] or candidate['auto_star'] else '')+candidate['name'])
                old_id=self.selected_run
                self.run_rows=self.lib.runs(self.selected); self.run_names=[f'{r["label"]}  ·  {r["status"]}' for r in self.run_rows]
                self.run_combo.configure(values=self.run_names)
                index=next((i for i,r in enumerate(self.run_rows) if r['id']==old_id),0)
                self.run_var.set(self.run_names[index] if self.run_names else ''); self.show_run()
        except sqlite3.Error as e:
            self.db_version=None; self.status.set('Waiting for training database: '+str(e))

    def monitor_progress(self):
        if not self.read_only or not self.active_session: return
        state=self.lib.state(self.active_session); event=self.lib.progress(self.active_session)
        self.progress_text.set(f'{state["status"]} · {state["state"]["phase"]} — '+event.get('message',''))
        self.progress.configure(maximum=max(1,event.get('total',1)),value=event.get('done',0))
        self.status.set('Results viewer · connected to '+str(self.db_path))

    def schedule_filter(self):
        if self.refresh_pending: self.root.after_cancel(self.refresh_pending)
        self.refresh_pending=self.root.after(150,self.refresh_library)
    def refresh_library(self):
        self.refresh_pending=None; rows=self.lib.candidates(); filt=self.filter_text.get().lower(); self.count_text.set(f'{len(rows):,} saved candidates • click to open tests')
        visible=[r for r in rows if (self.scope.get()!='Current search' or r['session']==self.active_session) and (not filt or filt in (r['name']+' '+r['stage']).lower()) and (not self.only_stars.get() or r['starred'] or r['auto_star'])]
        scores=self.lib.candidate_scores()
        order=self.sort_order.get()
        if order in ('Best first','Worst first'):
            direction=-1 if order=='Best first' else 1
            def score_key(row):
                score=scores.get(row['id'])
                if not score: return (1,0,0,-row['created'],row['id'])
                return (0,direction*score['pass_rate'],-direction*score['mean_stall'],-row['created'],row['id'])
            visible.sort(key=score_key)
        elif order=='Newest first': visible.sort(key=lambda row:(-row['created'],row['id']))
        wanted={r['id'] for r in visible}
        for cid in self.candidate_tree.get_children():
            if cid not in wanted: self.candidate_tree.delete(cid)
        for i,r in enumerate(visible):
            score=scores.get(r['id']); display=(f'{score["pass_rate"]:.0%}'+('' if score['validated'] else '*')) if score else '—'
            vals=(('★ ' if r['starred'] or r['auto_star'] else '')+r['name'],display,r['stage'])
            if self.candidate_tree.exists(r['id']): self.candidate_tree.item(r['id'],values=vals); self.candidate_tree.move(r['id'],'',i)
            else: self.candidate_tree.insert('','end',iid=r['id'],values=vals)
        self.library_dirty=False
    def candidate_clicked(self,e=None):
        ids=self.candidate_tree.selection()
        if ids: self.select_candidate(ids[0])
    def select_candidate(self,cid):
        c=self.lib.candidate(cid)
        if not c: return
        self.test_candidate_name.set('Test candidate · '+c['name'])
        self.selected=cid; self.title.set(('★ ' if c['starred'] or c['auto_star'] else '')+c['name']); g=Geometry(**c['geometry'])
        self.geo=g; self.settings=Settings(**c['settings']); self.case=Case(); self.sync_inputs(); self.reset(); self.running=False; self.play.configure(text='Play')
        self.candidate_meta.set(f'{c["stage"]}   •   Left {g.side("left")["angle"]:.1f}° / right {g.side("right")["angle"]:.1f}°   •   R{g.left_radius:.1f} / R{g.right_radius:.1f}   •   μ {self.settings.friction:g}   •   {c["session"][-6:]}')
        self.thumb.delete('all'); scale=min(250/g.outer_span,70/(g.entrance_y+10)); cx=140
        for side in ['left','right']:
            pts=g.side(side)['polygon']; self.thumb.create_polygon(*[v for x,y in pts for v in (cx+x*scale,8+y*scale)],fill=CARD,outline=CYAN,width=1)
        self.run_rows=self.lib.runs(cid); self.run_names=[f'{r["label"]}  ·  {r["status"]}' for r in self.run_rows]
        self.run_combo.configure(values=self.run_names); self.run_var.set(self.run_names[0] if self.run_names else ''); self.show_run(); self.tabs.select(self.overview)
    def sync_inputs(self):
        self.sync_geo(); self.pattern.set(self.case.name)
        for k in ['angle','orientation','offset','spacing','stagger','seed']: self.vars[k].set(str(getattr(self.case,k)))
        for k in ['speed','friction','response','acceleration']: self.vars[k].set(str(getattr(self.settings,k)))
    def show_run(self):
        for iid in self.test_tree.get_children(): self.test_tree.delete(iid)
        self.visible_results=[]; self.selected_run=None
        if self.run_var.get() not in self.run_names:
            for v in self.card_vars.values(): v.set('—')
            self.run_info.set('No saved test runs yet. Training results appear automatically.'); return
        run=self.run_rows[self.run_names.index(self.run_var.get())]; self.selected_run=run['id']; results=self.lib.results(run['id']); s=metrics(results)
        self.card_vars['jamfree'].set(f'{s["pass_rate"]:.1%}' if results else '—'); self.card_vars['jams'].set(str(s['jams'])); self.card_vars['runs'].set(f'{len(results)} / {run["expected"]}'); self.card_vars['misses'].set(str(s['missed']))
        self.run_info.set(f'{run["label"]} · {run["status"]} · {s["fed"]}/{s["total"]} balls delivered (informational)'+(' · Early jam screens: timing/stalls are lower bounds; replay continues the full test.' if any(r.get('censored') for r in results) else ''))
        self.visible_results=sorted([r for r in results if not self.jams_only.get() or r['jam']],key=lambda r:(not r['jam'],-r['max_stall']))
        for i,r in enumerate(self.visible_results):
            c=r['case']; self.test_tree.insert('','end',iid=str(i),values=(c['name'],f'{c["angle"]:+.1f}°',f'{c["orientation"]:+.1f}°',f'{c["offset"]:+.1f}', ('JAM · early' if r.get('censored') else 'JAM') if r['jam'] else 'No jam',f'{r["fed"]}/{r["total"]}',r['max_stall']),tags=('jam' if r['jam'] else 'clear',))
    def replay_selected(self,e=None):
        ids=self.test_tree.selection()
        if not ids: return
        r=self.visible_results[int(ids[0])]; c=self.lib.candidate(self.selected); self.geo=Geometry(**c['geometry']); self.settings=Settings(**c['settings']); self.case=Case(**r['case'])
        self.sync_inputs(); self.reset(); self.tabs.select(self.live); self.status.set('Replaying the selected candidate and test.')
    def toggle_star(self):
        if self.selected: self.lib.star(self.selected); self.refresh_library(); self.select_candidate(self.selected)
    def save_candidate(self):
        if not self.apply(): return
        cid=self.lib.add('Custom geometry '+time.strftime('%H:%M:%S'),self.geo.dict(),asdict(self.settings)); self.refresh_library(); self.select_candidate(cid)
    def manual_tests(self):
        if self.busy or not self.apply(): return
        cid=self.lib.add('Tested geometry '+time.strftime('%H:%M:%S'),self.geo.dict(),asdict(self.settings),stage='Manual'); geo,settings=self.geo,self.settings
        def job():
            lib=Library(self.db_path)
            try:
                run=lib.run(cid,'Manual 137-case suite',suite())
                def result(i,n,r):
                    lib.save_results(run['id'],[r]); self.events.put({'candidate':cid,'done':i,'total':n,'message':f'Manual test {i}/{n}'})
                evaluate(geo,settings,suite(),self.cancel.is_set,result)
            finally: lib.close()
        self.refresh_library(); self.select_candidate(cid); self.launch_job(job)
    def config(self): return SearchConfig(**{k:int(v.get()) for k,v in self.config_vars.items()}).validate()
    def start_search(self):
        if self.busy: return
        try:
            config=self.config(); geo=self.read_geo().validate(); settings=self.read_settings(); Simulation(geo,settings,Case()); self.active_session=create_session(self.lib,geo,settings,config)
        except (ValueError,TypeError) as e: messagebox.showerror('Check search inputs',str(e)); return
        self.update_session(); sid=self.active_session; self.launch_job(lambda:SearchRunner(self.db_path,sid,self.cancel,self.events.put).run())
        self.scope.set('Current search'); self.refresh_library()
    def resume_search(self):
        if self.busy or not self.active_session: return
        if self.lib.state(self.active_session)['status']=='complete': self.status.set('This search is complete. Start a new search for another round.'); return
        sid=self.active_session; self.launch_job(lambda:SearchRunner(self.db_path,sid,self.cancel,self.events.put).run())
    def pause_search(self):
        if self.busy: self.cancel.set(); self.progress_text.set('Pausing… saving completed tests and waiting for workers to stop.')
    def launch_job(self,fn):
        self.busy=True; self.running=False; self.play.configure(text='Play'); self.cancel.clear()
        def work():
            error=None
            try: fn()
            except Exception as e: error=str(e)
            finally: self.events.put({'finished':True,'error':error,'message':('Error: '+error) if error else ('Paused and saved.' if self.cancel.is_set() else 'Job finished. Open candidates to inspect test runs.')})
        self.job=threading.Thread(target=work,daemon=True); self.job.start()
    def poll(self):
        if self.read_only:
            message=None; finished=False
            while not self.events.empty():
                event=self.events.get(); message=event.get('message',''); self.retest_text.set(message)
                if event.get('retest_run') and self.selected==event.get('candidate'): self.selected_run=event['retest_run']
                if event.get('finished'): self.busy=False; finished=True
            self.poll_external(force=finished)
            if message: self.status.set(message)
            return
        changed=False
        while not self.events.empty():
            d=self.events.get(); self.status.set(d.get('message','')); self.progress_text.set(d.get('message','')); changed=True
            if 'done' in d: self.progress.configure(maximum=d['total'],value=d['done'])
            if d.get('error'): self.status.set('Error: '+d['error'])
            if d.get('finished'):
                self.busy=False; self.update_session(); self.refresh_library()
                if self.selected:
                    # Refresh test records without replacing an in-progress replay/editor.
                    old_run=self.run_var.get(); self.run_rows=self.lib.runs(self.selected)
                    self.run_names=[f'{r["label"]}  ·  {r["status"]}' for r in self.run_rows]
                    self.run_combo.configure(values=self.run_names)
                    self.run_var.set(old_run if old_run in self.run_names else (self.run_names[0] if self.run_names else ''))
                    self.show_run()
        if changed: self.library_dirty=True
        if self.library_dirty and time.monotonic()-self.last_refresh>3:
            self.refresh_library(); self.refresh_hardness(); self.last_refresh=time.monotonic()
    def update_session(self):
        sessions=self.lib.sessions(); self.session_ids=[s['id'] for s in sessions]; self.session_names=[f'{s["id"][-10:]} · {s["status"]} · {json.loads(s["state"])["phase"]}' for s in sessions]
        self.session_combo.configure(values=self.session_names)
        if self.active_session in self.session_ids: self.session_var.set(self.session_names[self.session_ids.index(self.active_session)])
        else:
            self.session_var.set(''); self.progress_text.set('No training session yet.'); self.progress.configure(value=0)
        self.refresh_hardness(); self.monitor_progress()
    def session_changed(self,e=None):
        if self.busy: self.update_session(); return
        if self.session_var.get() in self.session_names:
            self.active_session=self.session_ids[self.session_names.index(self.session_var.get())]; self.refresh_hardness(force=True); self.refresh_library(); self.monitor_progress()
    def refresh_hardness(self,force=False):
        if self.active_session:
            if self.tabs.select()!=str(self.optimizer): return
            if not force and time.monotonic()-self.hardness_refresh<10: return
        self.hardness_refresh=time.monotonic()
        for iid in self.hard_tree.get_children(): self.hard_tree.delete(iid)
        if not self.active_session: return
        for i,r in enumerate(self.lib.hardness(self.active_session)[:50]):
            c=r['case']; self.hard_tree.insert('','end',iid=str(i),values=(c['name'],f'{c["angle"]:+.1f}°',f'{c["orientation"]:+.1f}°',f'{c["offset"]:+.1f}',f'{r["rate"]:.0%}',f'{r["failures"]}/{r["attempts"]}'))
    def open_winner(self):
        if not self.active_session: return
        winner=self.lib.state(self.active_session)['state'].get('winner')
        if winner: self.select_candidate(winner)
        else: self.status.set('A winner is chosen only after fresh validation completes.')
    def export_results(self):
        if self.selected_run is None: return
        path=filedialog.asksaveasfilename(initialdir=HERE,defaultextension='.json',initialfile='candidate_test_run.json')
        if not path: return
        results=self.lib.results(self.selected_run); candidate=self.lib.candidate(self.selected); Path(path).write_text(json.dumps({'candidate':candidate,'results':results,'summary':metrics(results)},indent=2))
        if results:
            with Path(path).with_suffix('.csv').open('w',newline='') as f:
                rows=[{**r['case'],**{k:v for k,v in r.items() if k not in ('case','order')}} for r in results]; writer=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for row in rows for k in row))); writer.writeheader(); writer.writerows(rows)
        self.status.set('Exported this candidate’s selected run as JSON and CSV.')
    def close(self):
        if hasattr(self,'tick_id'): self.root.after_cancel(self.tick_id)
        if self.refresh_pending: self.root.after_cancel(self.refresh_pending); self.refresh_pending=None
        if self.busy: self.pause_search(); self.root.after(150,self.wait_close)
        else: self.lib.close(); self.root.destroy()
    def wait_close(self):
        if self.job and self.job.is_alive(): self.root.after(150,self.wait_close)
        else: self.lib.close(); self.root.destroy()

if __name__=='__main__':
    mp.freeze_support()
    parser=argparse.ArgumentParser(description='Read-only funnel training results viewer')
    parser.add_argument('--db',type=Path,default=HERE/'data'/'funnel_lab.sqlite3'); parser.add_argument('--capture',action='store_true')
    args=parser.parse_args(); root=tk.Tk(); app=App(root,args.db)
    if '--capture' in sys.argv:
        from PIL import ImageGrab
        root.update()
        def capture():
            try: ImageGrab.grab(window=int(root.tk.call('wm','frame',root._w),16)).save(HERE/'preview.png')
            finally: app.close()
        root.after(500,capture)
    root.mainloop()
