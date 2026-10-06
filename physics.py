"""Fixed-step planar contact model shared by live viewer and batch search."""
import sys, math, random
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'vendor'))
import pymunk
from dataclasses import dataclass, asdict
from geometry import Geometry, BALL_D

@dataclass
class Settings:
    speed: float = 220.0        # mm/s, provisional
    friction: float = 0.25      # fixed within every run/search, provisional
    response: float = 0.08     # velocity servo time constant, seconds
    acceleration: float = 2500.0 # finite traction, mm/s^2
    dt: float = 1/960
    duration: float = 8.0
    stall_time: float = 1.5

@dataclass
class Case:
    name: str = 'Four abreast'
    angle: float = 0.0
    offset: float = 0.0
    spacing: float = 80.0
    stagger: float = 0.0
    seed: int = 1
    orientation: float = 0.0  # rotation of the ball formation, independent of approach

    def points(self):
        d = max(BALL_D+0.5, self.spacing)
        if self.name == 'Single ball': return [(0,0)]
        if self.name == 'Two abreast': return [(-d/2,0),(d/2,self.stagger)]
        if self.name == 'Three abreast': return [(-d,0),(0,self.stagger),(d,2*self.stagger)]
        if self.name == 'Four abreast': return [((i-1.5)*d,i*self.stagger) for i in range(4)]
        if self.name == 'Single file': return [(0,i*d) for i in range(5)]
        if self.name == 'Packed cluster':
            return [(-d,0),(0,0),(d,0),(-d/2,d*.88),(d/2,d*.88),(0,d*1.76)]
        rng = random.Random(self.seed)
        points = []
        for _ in range(3000):
            p = (rng.uniform(-112,112), rng.uniform(0,300))
            if all(math.dist(p,q) >= d for q in points): points.append(p)
            if len(points)==8: break
        return points

PATTERNS = ['Single ball','Two abreast','Three abreast','Four abreast','Single file','Packed cluster','Scattered balls']

def suite(held_out=False):
    cases=[]
    angles = [-37,-17,13,37] if held_out else [-45,-25,0,25,45]
    orientations = [-32,12,38] if held_out else [-40,0,40]
    patterns = ['Two abreast','Three abreast','Four abreast']
    for i, pattern in enumerate(patterns):
        for j, a in enumerate(angles):
            for orientation in orientations:
                for offset in [-20,0,20]:
                    cases.append(Case(pattern,a,offset,78,0,1,orientation))
    cases += [Case('Single ball',0,0), Case('Single file',0,0)]
    return cases

class Simulation:
    def __init__(self, geo=None, settings=None, case=None):
        self.geo=(geo or Geometry()).validate()
        self.settings=settings or Settings()
        self.case=case or Case()
        if not all(math.isfinite(v) for v in asdict(self.settings).values()):
            raise ValueError('Physics values must be finite numbers.')
        if not all(math.isfinite(v) for k,v in asdict(self.case).items() if k!='name'):
            raise ValueError('Entry values must be finite numbers.')
        if not 1/10000<=self.settings.dt<=1/240 or not .1<=self.settings.duration<=120:
            raise ValueError('Invalid physics step or duration.')
        if self.case.name not in PATTERNS: raise ValueError('Unknown entry pattern.')
        if not 0 < self.settings.speed <= 1200: raise ValueError('Speed must be 1–1200 mm/s.')
        if not 0 <= self.settings.friction <= 2: raise ValueError('Friction must be 0–2.')
        if not 0.01 <= self.settings.response <= 1: raise ValueError('Drive response must be 0.01–1 s.')
        if not 100 <= self.settings.acceleration <= 20000: raise ValueError('Traction acceleration must be 100–20000 mm/s².')
        if abs(self.case.angle)>60: raise ValueError('Approach angle must be between -60 and 60 degrees.')
        if abs(self.case.orientation)>90: raise ValueError('Ball line angle must be between -90 and 90 degrees.')
        if abs(self.case.offset)>500 or not 74<=self.case.spacing<=200 or abs(self.case.stagger)>150:
            raise ValueError('Use offset ±500 mm, spacing 74–200 mm, and stagger ±150 mm.')
        self.space=pymunk.Space()
        self.space.iterations=50
        self.space.collision_slop=0.005
        self.space.collision_bias=(1-.35)**60
        self.space.gravity=(0,0)
        self.time=0.0; self.exits=[]; self.missed=[]; self.balls=[]
        self.jam_seen=False; self.max_overlap=0.; self.max_stall=0.
        self.stalled=0.; self._window_time=0.; self._window_best={}
        self._progress=0.; self._slow_sum=0.; self._samples=0
        self.wall_shapes=[]
        mu=math.sqrt(self.settings.friction) # Chipmunk multiplies shape coefficients.
        for side in ['left','right']:
            poly=self.geo.side(side)['polygon']
            for i,a in enumerate(poly):
                b=poly[(i+1)%len(poly)]
                if math.dist(a,b)<1e-8: continue
                shape=pymunk.Segment(self.space.static_body,a,b,0)
                shape.set_neighbors(poly[(i-1)%len(poly)],poly[(i+2)%len(poly)])
                shape.friction=mu; shape.elasticity=0
                self.space.add(shape); self.wall_shapes.append(shape)
        self.space.on_collision(None,None,post_solve=self._contact)
        a=math.radians(self.case.angle)
        vx,vy=self.settings.speed*math.sin(a),-self.settings.speed*math.cos(a)
        base=self.geo.entrance_y+BALL_D/2+12
        raw=self.case.points(); orientation=math.radians(self.case.orientation)
        points=[(x*math.cos(orientation)-y*math.sin(orientation),
                 x*math.sin(orientation)+y*math.cos(orientation)) for x,y in raw]
        miny=min(y for x,y in points)
        # Rigidly translate the incoming formation so its front row arrives at offset.
        # Its other rows retain their spacing (including at diagonal approach angles).
        drift=(base-(self.geo.entrance_y+BALL_D/2))*math.tan(a)
        for idx,(x,y) in enumerate(points):
            b=pymunk.Body(1, (2/3)*(BALL_D/2)**2) # normalized mass, hollow-sphere inertia
            b.position=(x+self.case.offset-drift,base+y-miny)
            b.velocity=(vx,vy)
            shape=pymunk.Circle(b,BALL_D/2)
            shape.friction=mu; shape.elasticity=0
            self.space.add(b,shape)
            self.balls.append({'id':idx+1,'body':b,'shape':shape,'trail':[], 'best':b.position.y})
        self.total=len(self.balls)
        self.initial=[(b['body'].position.x,b['body'].position.y) for b in self.balls]

    def _contact(self, arbiter, space, data):
        for p in arbiter.contact_point_set.points:
            self.max_overlap=max(self.max_overlap,-p.distance)

    def step(self, steps=1):
        s=self.settings; dt=s.dt
        for _ in range(steps):
            if self.done: break
            a=math.radians(self.case.angle)
            for ball in self.balls:
                b=ball['body']
                # Once the ball reaches the wedge, every position is driven upward.
                inside=b.position.y<=self.geo.entrance_y+BALL_D/2
                target=pymunk.Vec2d(0,-s.speed) if inside else pymunk.Vec2d(s.speed*math.sin(a),-s.speed*math.cos(a))
                accel=(target-b.velocity)/s.response
                if accel.length>s.acceleration: accel=accel.normalized()*s.acceleration
                b.force=accel*b.mass
                b.torque=-b.angular_velocity*b.moment/0.12
            self.space.step(dt); self.time+=dt
            for ball in list(self.balls):
                b=ball['body']; x,y=b.position
                if y < -BALL_D/2-2:
                    if abs(x)<=(75-BALL_D)/2+.05:
                        self.exits.append({'id':ball['id'],'time':self.time})
                    else:
                        self.missed.append(ball['id'])
                    self.space.remove(ball['shape'],b); self.balls.remove(ball)
                elif abs(x)>self.geo.outer_span/2+BALL_D or y>1100:
                    self.missed.append(ball['id'])
                    self.space.remove(ball['shape'],b); self.balls.remove(ball)
                else:
                    gain=max(0,ball['best']-y); ball['best']=min(ball['best'],y)
                    self._progress+=gain
            if self.time-self._window_time>=.1:
                active=[b for b in self.balls if b['body'].position.y<=self.geo.entrance_y+BALL_D]
                # No new forward progress: vibration in place does not clear a jam.
                if active and self._progress<.3: self.stalled+=self.time-self._window_time
                else: self.stalled=0
                self.max_stall=max(self.max_stall,self.stalled)
                if self.stalled>=s.stall_time: self.jam_seen=True
                for b in self.balls:
                    b['trail'].append(tuple(b['body'].position)); b['trail']=b['trail'][-100:]
                    self._slow_sum+=max(0,1-max(0,-b['body'].velocity.y)/s.speed)
                    self._samples+=1
                self._progress=0; self._window_time=self.time

    @property
    def done(self): return not self.balls or self.time>=self.settings.duration

    def run(self):
        while not self.done: self.step(24)
        return self.result()

    def result(self):
        times=[x['time'] for x in self.exits]
        gaps=[b-a for a,b in zip(times,times[1:])]
        return {'case':asdict(self.case),'fed':len(self.exits),'total':self.total,
                'remaining':len(self.balls),'missed':len(self.missed),'jam':self.jam_seen,
                'elapsed':round(self.time,3),'last_exit':round(max(times,default=0),3),
                'largest_gap':round(max(gaps,default=0),3),'max_stall':round(self.max_stall,3),
                'slow_fraction':round(self._slow_sum/max(1,self._samples),4),
                'max_overlap_mm':round(self.max_overlap,4),'order':[x['id'] for x in self.exits]}

def summarize(results):
    return {'cases':len(results),'fed':sum(r['fed'] for r in results),
            'total':sum(r['total'] for r in results),'jams':sum(r['jam'] for r in results),
            'unfinished':sum(r['remaining']>0 for r in results),
            'missed':sum(r['missed'] for r in results),
            'worst_time':max((r['last_exit'] if not r['remaining'] else r['elapsed'] for r in results),default=0),
            'mean_slow':sum(r['slow_fraction'] for r in results)/max(1,len(results)),
            'max_overlap_mm':max((r['max_overlap_mm'] for r in results),default=0)}

def score(summary):
    # Prioritize ALL delivered balls, then persistent jams, then time and stalls.
    return (summary['total']-summary['fed'],summary['jams'],summary['worst_time'],summary['mean_slow'])

