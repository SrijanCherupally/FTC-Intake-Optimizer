"""Explicit candidate retests and persistent scenario history for the viewer."""
import time
from dataclasses import asdict
import tkinter as tk
from tkinter import ttk, messagebox
from geometry import Geometry
from physics import Case, Settings, Simulation, PATTERNS
from library import Library

PANEL='#131d31'; TEXT='#e8effc'; MUTED='#90a2bf'; CYAN='#67ddd0'
FIELDS=[('angle','Approach °'),('orientation','Line angle °'),('offset','Offset mm'),
        ('spacing','Spacing mm'),('stagger','Stagger mm'),('seed','Scatter seed')]

class TestingPanel:
    def build_testing(self):
        p=self.testing; self.saved_scenarios={}; self.loaded_test=None
        self.test_candidate_name=tk.StringVar(value='Select a candidate in the sidebar')
        self.text(p,var=self.test_candidate_name,size=20,bold=True).pack(fill='x',padx=20,pady=(16,5))
        self.text(p,'Test different approaches using this candidate’s unchanged geometry and physics.',10,MUTED).pack(fill='x',padx=20,pady=(0,10))
        form=tk.Frame(p,bg=PANEL); form.pack(fill='x',padx=20)
        self.test_pattern=tk.StringVar(value=Case().name); self.test_values={}
        self.text(form,'Formation',9,MUTED).grid(row=0,column=0,sticky='w')
        ttk.Combobox(form,textvariable=self.test_pattern,values=PATTERNS,state='readonly',width=20).grid(row=1,column=0,padx=(0,12),pady=(0,10))
        for i,(key,label) in enumerate(FIELDS,1):
            col=i%4; row=(i//4)*2
            self.text(form,label,9,MUTED).grid(row=row,column=col,sticky='w')
            value=tk.StringVar(value=str(getattr(Case(),key))); self.test_values[key]=value
            ttk.Entry(form,textvariable=value,width=17).grid(row=row+1,column=col,sticky='ew',padx=(0,12),pady=(0,10))
            form.columnconfigure(col,weight=1)
        bar=tk.Frame(p,bg=PANEL); bar.pack(fill='x',padx=20,pady=8)
        for label,cmd in [('Preview',self.preview_test),('Run & save result',self.run_custom_test),('Save setup to history',self.save_custom_test)]:
            self.button(bar,label,cmd).pack(side='left',padx=(0,8))
        self.button(bar,'Open results',lambda:self.tabs.select(self.overview)).pack(side='right')
        self.retest_text=tk.StringVar(value='Saved results appear under the selected candidate as Retest runs.')
        self.text(p,var=self.retest_text,size=10,color=CYAN,wraplength=950).pack(fill='x',padx=20,pady=(4,12))
        bar=tk.Frame(p,bg=PANEL); bar.pack(fill='x',padx=20,pady=6)
        self.text(bar,'SAVED TEST HISTORY',10,CYAN,True).pack(side='left')
        self.button(bar,'Save current search’s top 5',self.save_current_hard_tests).pack(side='right')
        self.button(bar,'Run this top 5',self.run_hard_group).pack(side='right',padx=8)
        frame=tk.Frame(p,bg=PANEL); frame.pack(fill='both',expand=True,padx=20,pady=8)
        self.saved_test_tree=self.tree(frame,{'name':'Saved setup','rate':'Jam rate','votes':'Jams / tried','source':'Search'},[230,90,100,130],height=5)
        self.saved_test_tree.bind('<<TreeviewSelect>>',self.load_saved_test)
        self.text(p,'Click a saved setup to load its angles and spacing. Failure rates count each geometry once.\nTop 5 are saved on pause/completion and survive candidate cleanup. Fewer than five appear if fewer tests jammed.',9,MUTED,justify='left').pack(fill='x',padx=20,pady=(0,14))
        self.refresh_saved_tests()

    def refresh_saved_tests(self):
        if not hasattr(self,'saved_test_tree'): return
        rows=self.lib.saved_tests(); self.saved_scenarios={r['id']:r for r in rows}
        wanted=set(self.saved_scenarios)
        for iid in self.saved_test_tree.get_children():
            if iid not in wanted: self.saved_test_tree.delete(iid)
        for i,row in enumerate(rows):
            rate=f"{row['failures']/row['attempts']:.1%}" if row['attempts'] else '—'
            vals=(row['name']+' · '+row['case']['name'],rate,f"{row['failures']}/{row['attempts']}" if row['attempts'] else 'Custom',row['source_session'][-10:] or 'Custom')
            if self.saved_test_tree.exists(row['id']): self.saved_test_tree.item(row['id'],values=vals); self.saved_test_tree.move(row['id'],'',i)
            else: self.saved_test_tree.insert('','end',iid=row['id'],values=vals)
        if self.loaded_test not in wanted: self.loaded_test=None

    def load_saved_test(self,event=None):
        ids=self.saved_test_tree.selection()
        if not ids or ids[0] not in self.saved_scenarios: return
        self.loaded_test=ids[0]; row=self.saved_scenarios[ids[0]]; case=row['case']
        self.test_pattern.set(case['name'])
        for key,value in self.test_values.items(): value.set(str(case[key]))
        self.retest_text.set(f"Loaded {row['name']}. Run with the selected candidate’s physics; original setup friction was {row['settings']['friction']:g}.")

    def configured_test(self):
        return Case(name=self.test_pattern.get(),**{k:(int(v.get()) if k=='seed' else float(v.get())) for k,v in self.test_values.items()})

    def test_candidate(self):
        c=self.lib.candidate(self.selected) if self.selected else None
        if not c: raise ValueError('Select a saved candidate first.')
        return c

    def preview_test(self):
        try:
            c=self.test_candidate(); case=self.configured_test()
            sim=Simulation(Geometry(**c['geometry']),Settings(**c['settings']),case)
        except (ValueError,TypeError) as e: messagebox.showerror('Check test configuration',str(e)); return
        self.geo=sim.geo; self.settings=sim.settings; self.case=case
        self.sync_inputs(); self.reset(); self.tabs.select(self.live)
        self.status.set('Previewing a custom test. Use Run & save result to record it.')

    def save_custom_test(self):
        try:
            c=self.test_candidate(); case=self.configured_test()
            Simulation(Geometry(**c['geometry']),Settings(**c['settings']),case)
            lib=Library(self.db_path)
            try: lib.save_test('Custom '+time.strftime('%Y-%m-%d %H:%M:%S'),asdict(case),c['settings'])
            finally: lib.close()
            self.refresh_saved_tests(); self.retest_text.set('Saved this configuration to test history.')
        except (ValueError,TypeError) as e: messagebox.showerror('Check test configuration',str(e))

    def save_current_hard_tests(self):
        if not self.active_session: self.retest_text.set('Select a training session in Training monitor first.'); return
        lib=Library(self.db_path)
        try: count=lib.save_hard_tests(self.active_session)
        finally: lib.close()
        self.refresh_saved_tests(); self.retest_text.set(f'Saved {count} highest-failure configurations from the current search.')

    def run_custom_test(self):
        try: cases=[self.configured_test()]
        except ValueError as e: messagebox.showerror('Check test configuration',str(e)); return
        self.run_candidate_tests(cases,'custom')

    def run_hard_group(self):
        row=self.saved_scenarios.get(self.loaded_test)
        if not row or not row['source_session']:
            self.retest_text.set('Select a saved hard case to choose its top-five group.'); return
        group=sorted([r for r in self.saved_scenarios.values() if r['source_session']==row['source_session']],key=lambda r:r['rank'])
        self.run_candidate_tests([Case(**r['case']) for r in group],'top 5 · '+row['source_session'][-10:])

    def run_candidate_tests(self,cases,label):
        if self.busy: self.retest_text.set('Wait for the current test job to finish.'); return
        try:
            c=self.test_candidate(); geo=Geometry(**c['geometry']); settings=Settings(**c['settings'])
            for case in cases: Simulation(geo,settings,case)
        except (ValueError,TypeError) as e: messagebox.showerror('Check test configuration',str(e)); return
        cid=c['id']; run_label=f'Retest · {label} · {time.time_ns()}'
        def job():
            lib=Library(self.db_path)
            try:
                run=lib.run(cid,run_label,cases)
                for i,case in enumerate(cases):
                    if self.cancel.is_set(): break
                    sim=Simulation(geo,settings,case,record_trails=False)
                    while not sim.done and not self.cancel.is_set(): sim.step(120)
                    if not sim.done: break
                    lib.save_results(run['id'],[sim.result()])
                    self.events.put({'retest_run':run['id'],'candidate':cid,'message':f"{c['name']}: saved test {i+1}/{len(cases)}. Open results to inspect or replay."})
            finally: lib.close()
        self.retest_text.set(f"Testing {c['name']} on {len(cases)} configurations…")
        self.launch_job(job)
