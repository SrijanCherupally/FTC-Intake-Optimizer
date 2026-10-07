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

class Viewer:
    def label(self,parent,text,size=10,color=TEXT,bold=False):
        return tk.Label(parent,text=text,bg=PANEL,fg=color,font=('Segoe UI',size,'bold' if bold else 'normal'),anchor='w')

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
        self.running=bool(self.sim) and not self.running; self.play.configure(text='Pause' if self.running else 'Play'); self.draw()

    def step_once(self):
        self.running=False; self.play.configure(text='Play')
        if self.sim and not self.sim.done: self.sim.step(round(.1/self.settings.dt))
        self.draw()

    def tick(self):
        now=time.perf_counter(); elapsed=min(.06,now-self.last_clock); self.last_clock=now
        visible=not hasattr(self,'tabs') or self.tabs.select()==str(self.live)
        changed=False
        if self.running and self.sim and not self.sim.done and visible:
            self.accumulator+=elapsed*self.speed_factor.get()
            n=min(120,int(self.accumulator/self.settings.dt))
            if n: self.sim.step(n); self.accumulator-=n*self.settings.dt; changed=True
        self.poll()
        if changed: self.draw()
        self.tick_id=self.root.after(25,self.tick)

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


def write_svg(geo,path):
    width=geo.outer_span+20; height=geo.entrance_y+20
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}mm" height="{height}mm" viewBox="{-width/2} -10 {width} {height}">',
           '<title>Funnel geometry in mm; 75 mm outlet, 168 mm mounts, 280.35 mm outer anchors</title>']
    for side in ['left','right']:
        pts=' '.join(f'{x:.5f},{y:.5f}' for x,y in geo.side(side)['polygon'])
        parts.append(f'<polygon points="{pts}" fill="none" stroke="black" stroke-width="0.15"/>')
    parts.append('</svg>'); Path(path).write_text('\n'.join(parts),encoding='utf-8')

