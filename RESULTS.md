# Initial simulation results

These are results of the uncalibrated planar model, not measured robot performance. Ball diameter, friction and drive settings were fixed throughout.

The baseline matches the provided dimensions. Unspecified left radius and wedge drops were inferred as described in README.md.

| Baseline test suite | Result |
|---|---:|
| Cases | 137 |
| Balls delivered | 214 / 411 |
| Cases with a detected jam | 58 |
| Cases unfinished at 8 seconds | 57 |
| Balls missed | 45 |
| Maximum recorded contact overlap | 0.229 mm |

Most cases use two, three or four balls, with independent incoming velocity and line orientations. The additional single-file sanity case uses five balls. A detected jam can clear later, so the jam and unfinished counts need not match.

The 24-candidate search selected a shape using 16 training cases. Its separate 110-case check showed:

| Holdout result | Original | Search candidate |
|---|---:|---:|
| Delivered | 186 / 330 | 174 / 330 |
| Jammed cases | 45 | 35 |
| Missed balls | 26 | 63 |

**The candidate is not an overall improvement.** It reduces jams partly at the cost of losing more balls. The app therefore starts with the original geometry. The candidate is provided for inspection, not recommended for fabrication.

Geometry checks, nonoverlapping starts, single-ball and single-file passage, a known symmetric arch jam, outside-miss detection, deterministic replay and half-time-step comparisons passed. Five representative convergence cases preserved their delivered/missed/jammed outcomes and kept last-exit differences below 0.08 seconds. This checks numerical behavior on those cases; it does not validate the material or roller model against reality.

All detailed results and exact reproducible inputs are in validation_report.json and verified_search.json.
