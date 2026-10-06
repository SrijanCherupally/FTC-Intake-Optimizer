# Funnel Lab

Double-click **Launch Funnel Lab.vbs** on this computer. The included Pymunk engine is already installed locally in `vendor`; the launcher uses the bundled Python 3.12 runtime. Keep this folder together. No internet is needed to run it.

On another computer, use Python with Tkinter, install `requirements.txt`, and run `python app.py`. The bundled vendor wheels are Windows x64 / Python 3.12; use the installed package instead of `vendor` for a different Python version/platform.

## Using the simulator

1. Start with Four abreast. Choose two or three balls from the scenario list as needed.
2. **Approach angle** changes the incoming velocity. **Ball line angle** rotates the line of balls independently. Offset moves the formation sideways; stagger shifts neighboring balls forward/backward. Positive approach angles move to the right.
3. Change lips, straight lengths, radii, wedge drops or wedge curves, then **Apply inputs & restart**. To specify wall angles directly, edit the angle fields and press **Use angles**. Angles and straight lengths cannot be independent with fixed endpoints and radii.
4. Pause, single-step, or slow playback. Numbered balls show exit order; arrows show actual velocity. A jam is at least 1.5 seconds without meaningful forward progress while balls remain at the intake.
5. **Run 137 test cases** tests 2/3/4 balls × five approach angles × three line orientations × three offsets, plus two sanity cases. Select any result to replay its exact settings. Missed balls, unfinished cases and detected jams are separate outcomes.
6. **Search 24 geometries** runs a reproducible bounded random search on 16 mixed training scenarios, then checks the baseline and selected candidate against 110 separate scenarios. The candidate may be better or worse on those holdout cases. Inspect that comparison before adopting it. This is not an exhaustive optimization.
7. Save/load setup JSON, export results as JSON and CSV, or export an SVG with one drawing unit per mm. The search never changes the ball diameter, friction, drive settings, or fixed anchors. Stop interrupts work between short simulation chunks.

The supplied `validation_report.json` contains baseline results and numerical checks. `verified_search.json` contains the pre-run search and its holdout comparison. `search_candidate.json` can be opened using Load setup. Saved results load when the app starts.

## Geometry and constraints

- Ball diameter: **74 mm**, fixed as requested.
- Inner outlet clearance: **75 mm**, exactly between collision surfaces.
- Funnel mounting width: **168 mm**, fixed.
- Outer wedge anchor width: **280.35 mm**, user-confirmed and fixed.
- Mounting line: **107.38628 mm** below the fixed outlet/top line, user-confirmed and fixed.
- Baseline left/right lips: **10 / 4 mm**. Left wall angle **30°**; right straight **24 mm**; right fillet **R30**.
- The screenshot does not label the left radius or outer wedge drop. Baseline assumes **R30 on the left and 40 mm wedge drops**, matching the image approximately. Those are adjustable.
- The top line is a geometric limit and open discharge, not a collision wall across the outlet. All solid funnel geometry stays at or below it.
- Outer upper wedge anchors stay fixed. Adjustable wedge drop moves the lower end along the plate edge. Wedge curve bends the lower contact surface while preserving its endpoints. Invalid/self-reversing designs are rejected.
- Curves use densely sampled circular arcs and quadratic wedge curves. SVG exports the exact polylines used by the solver, not native CAD arc entities.

## Physics and accuracy

Pymunk / Chipmunk2D provides simultaneous rigid-body contact impulses, nonpenetration constraints, Coulomb friction and rotational contact response. Both live and batch runs use the same engine, **960 fixed steps per simulated second**, and 50 solver iterations. Contact surfaces have zero added thickness, so the 75 mm outlet does not secretly shrink. Neighbor-aware segments avoid artificial cracks between arc pieces. Contact overlap is recorded in the reports.

The balls are represented as planar circles with hollow-sphere vertical-axis inertia. Their equal masses are normalized to 1 because drive is specified as acceleration, and the model contains no gravity or absolute force measurements. Printed Newton values or motor-load predictions are therefore intentionally absent.

The no-deadzone roller assumption is implemented as a finite drive toward an upward target speed wherever a ball reaches the wedge area. Before that point, the approach angle sets its target velocity. Drive acceleration is `(target velocity - actual velocity) / response time`, capped by the traction limit. Collisions can slow or stop the balls. Enforcing an exact velocity through a jam would violate the collision physics.

Defaults **220 mm/s**, **0.08 s response**, **2500 mm/s² traction**, **friction 0.25**, zero bounce and **0.12 s axial spin damping** are provisional, not measured properties of Pollen balls on PLA. Friction remains constant with angle: changing the wall angle changes contact normals and forces. This first model uses the same effective friction for ball-wall and ball-ball contact; those values may differ physically. Spin damping approximates roller restraint about the vertical axis, not a complete 3D rolling model.

This is a **planar transfer screening model**, not a validated digital twin. It cannot represent lifting, stacking, the holes in Pollen balls, ball or PLA deformation, layer texture, full sphere rolling on a floor, individual roller contact geometry, drive yaw during a pass, or motor torque. Diagonal translation and independently oriented ball formations are included. Every result applies only to these assumptions and the tested time horizon (8 s).

To improve real-world agreement, record a straight four-ball pickup and an angled pickup with your actual roller speed. Measure single-ball travel speed and compare observed stalls/exit timing. Calibrate the fixed friction and drive settings once, then rerun the geometry tests. A rigid planar simulation cannot prove that a physical mechanism never jams.

## Verification

`python validate.py --search` repeats geometry constraints, nonoverlapping initial formations, centered/single-file passage, outside-miss classification, a known symmetric arch jam, deterministic replay, half-time-step convergence on five representative cases, all 137 baseline cases and the 24-candidate search. `python app.py --smoke` checks native controls, drawing, stepping and diagonal setup.

Engine reference: https://www.pymunk.org/en/7.2.0/pymunk.html
