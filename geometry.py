"""Millimetre CAD geometry. y=0 is the fixed outlet plane; intake is +y."""
from dataclasses import dataclass, asdict
import math

BALL_D = 74.0
THROAT = 75.0
MOUNT_SPAN = 168.0

@dataclass
class Geometry:
    mount_y: float = 107.38628   # user-confirmed fixed mounting line
    outer_span: float = 280.35  # user-confirmed outer anchors
    left_lip: float = 10.0
    right_lip: float = 4.0
    left_straight: float = 73.218984
    right_straight: float = 24.0
    left_radius: float = 30.0
    right_radius: float = 30.0
    left_drop: float = 40.0
    right_drop: float = 40.0
    left_bow: float = 0.0
    right_bow: float = 0.0

    def dict(self):
        return asdict(self)

    def side(self, side):
        sign = -1 if side == 'left' else 1
        lip = getattr(self, side + '_lip')
        length = getattr(self, side + '_straight')
        radius = getattr(self, side + '_radius')
        drop = getattr(self, side + '_drop')
        bow = getattr(self, side + '_bow')
        h, width = self.mount_y + lip - length, (MOUNT_SPAN - THROAT) / 2
        if min(self.mount_y, self.outer_span - MOUNT_SPAN, radius) <= 0:
            raise ValueError('Mount depth, outer overhang and radii must be positive.')
        if length < 0 or lip < 0 or drop < lip or h <= 0:
            raise ValueError(f'{side.title()}: straight must end below the top and above the lip; wedge drop must exceed lip.')
        # beta is turn from vertical. Circle starts tangent to the outlet straight.
        def end_y(beta):
            dx = radius * (1 - math.cos(beta))
            return radius * math.sin(beta) + (width - dx) / math.tan(beta)
        lo, hi = 1e-5, math.pi / 2 - 1e-5
        if h <= end_y(hi):
            raise ValueError(f'{side.title()}: radius is too large for this straight length.')
        for _ in range(50):
            mid = (lo + hi) / 2
            if end_y(mid) > h: lo = mid
            else: hi = mid
        beta = (lo + hi) / 2
        dx = radius * (1 - math.cos(beta))
        if dx >= width:
            raise ValueError(f'{side.title()}: curve does not fit inside the mounting width.')
        pts = [(THROAT / 2, 0), (THROAT / 2, length)]
        # Fine circular chord approximation (under 0.002 mm radial error).
        count = max(12, math.ceil(beta / 0.015))
        pts += [(THROAT/2 + radius*(1-math.cos(beta*i/count)),
                 length + radius*math.sin(beta*i/count)) for i in range(1, count+1)]
        pts.append((MOUNT_SPAN/2, self.mount_y+lip))
        a, b = pts[-1], (self.outer_span/2, self.mount_y+drop)
        c = ((a[0]+b[0])/2, (a[1]+b[1])/2+bow)
        if not min(a[1], b[1]) <= c[1] <= max(a[1], b[1]):
            raise ValueError(f'{side.title()}: wedge bow must keep the edge monotonic.')
        pts += [((1-t)**2*a[0]+2*(1-t)*t*c[0]+t*t*b[0],
                 (1-t)**2*a[1]+2*(1-t)*t*c[1]+t*t*b[1]) for t in [i/24 for i in range(1,25)]]
        edge = [(sign*x,y) for x,y in pts]
        # Solid material extends outward, never into the 75 mm free throat.
        polygon = edge + [(sign*self.outer_span/2, self.mount_y),
                          (sign*MOUNT_SPAN/2, self.mount_y), (sign*MOUNT_SPAN/2,0)]
        return {'edge':edge, 'polygon':polygon, 'angle':90-math.degrees(beta)}

    def set_angle(self, side, degrees):
        if not 5 <= degrees <= 85: raise ValueError('Wall angles must be 5–85 degrees from horizontal.')
        beta = math.radians(90-degrees)
        r = getattr(self, side+'_radius')
        h = r*math.sin(beta) + ((MOUNT_SPAN-THROAT)/2-r*(1-math.cos(beta)))/math.tan(beta)
        setattr(self, side+'_straight', self.mount_y+getattr(self,side+'_lip')-h)

    def validate(self):
        if not all(math.isfinite(v) for v in self.dict().values()):
            raise ValueError('All geometry dimensions must be finite numbers.')
        if abs(self.mount_y-107.38628)>1e-6 or abs(self.outer_span-280.35)>1e-6:
            raise ValueError('Mounting depth and outer anchor span are fixed at 107.38628 and 280.35 mm.')
        self.side('left'); self.side('right')
        return self

    @property
    def entrance_y(self):
        return self.mount_y + max(self.left_drop,self.right_drop)

