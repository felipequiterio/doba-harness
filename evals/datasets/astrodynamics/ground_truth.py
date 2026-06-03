"""
Compute expected answers in code from trusted formulas + constants.

Never let an LLM produce ground truth: it's the rule the experiment is
measured against, so it has to be deterministic and reviewable.

The public surface is `compute(problem_id, given)` plus a CLI:

    python -m src.ground_truth --check        # assert dataset == computed
    python -m src.ground_truth --regenerate   # rewrite "expected" in the JSON

Add new problems by registering a function with @gt(problem_id).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Callable, Dict

# ---------------------------------------------------------------------------
# Constants — single source of truth for the whole project.
# ---------------------------------------------------------------------------
# Earth's standard gravitational parameter (m^3 / s^2). The conventional
# astrodynamics value, matching e.g. JGM-3 and most textbooks.
MU_EARTH = 3.986004418e14
# WGS-84 equatorial radius (m).
R_EARTH = 6_378_137.0

# Mars: mean equatorial μ and mean radius (MRO-era values).
MU_MARS = 4.282837e13
R_MARS  = 3_389_500.0

# Sun (heliocentric) standard gravitational parameter.
MU_SUN = 1.32712440018e20

# Heliocentric semi-major axes (m). 1 AU = IAU 2012 value.
AU             = 1.49597870700e11
A_EARTH_HELIO  = AU
A_MARS_HELIO   = 2.27939200000e11    # ≈ 1.52366 AU

# Earth–Moon distance (m). Use the conventional mean Earth–Moon distance.
A_MOON_GEO = 3.84400e8

# Earth's sidereal rotation period (s) — for the GEO altitude problem.
SIDEREAL_DAY = 86164.0905

# Standard gravity (m/s^2). Used by Tsiolkovsky (Isp * g0 * ln(m0/mf)).
G0 = 9.80665

# Earth mass / Sun mass ratio. Used by sphere-of-influence formula.
M_EARTH_OVER_M_SUN = 3.00273e-6

# Universal gravitational constant.
G = 6.67430e-11

# Earth's second zonal harmonic (J2). Drives nodal regression / apsidal
# rotation of low-altitude orbits — the workhorse "real-world" perturbation.
J2_EARTH = 1.08263e-3

# Sidereal year (s) — for sun-synchronous orbits + synodic period problems.
SIDEREAL_YEAR = 31_558_149.5

# Convenience exports so tests / calculator can import the same values.
# Anything added here is callable by the model from inside CALC: expressions
# AND visible to oracle cards. Add sparingly — every name is a possible
# distractor.
CONSTANTS: Dict[str, float] = {
    "mu_earth": MU_EARTH,
    "R_earth": R_EARTH,
    "mu_mars": MU_MARS,
    "R_mars": R_MARS,
    "mu_sun": MU_SUN,
    "AU": AU,
    "a_earth_helio": A_EARTH_HELIO,
    "a_mars_helio": A_MARS_HELIO,
    "a_moon_geo": A_MOON_GEO,
    "sidereal_day": SIDEREAL_DAY,
    "g0": G0,
    "m_earth_over_m_sun": M_EARTH_OVER_M_SUN,
    "G": G,
    "J2_earth": J2_EARTH,
    "sidereal_year": SIDEREAL_YEAR,
    "pi": math.pi,
    "e": math.e,
}

# ---------------------------------------------------------------------------
# Formula primitives. Documented references.
# ---------------------------------------------------------------------------
def v_circ(r: float, mu: float = MU_EARTH) -> float:
    """Circular orbital speed at radius r. (Curtis §2.4)"""
    return math.sqrt(mu / r)

def vis_viva(r: float, a: float, mu: float = MU_EARTH) -> float:
    """Vis-viva: v² = μ(2/r − 1/a). (Curtis §2.5)"""
    return math.sqrt(mu * (2.0 / r - 1.0 / a))

def period(a: float, mu: float = MU_EARTH) -> float:
    """Kepler's third law: T = 2π √(a³/μ). (Curtis §2.6)"""
    return 2.0 * math.pi * math.sqrt(a ** 3 / mu)

def v_escape(r: float, mu: float = MU_EARTH) -> float:
    """Escape speed at radius r from a primary of mass μ/G. (Curtis §2.5)"""
    return math.sqrt(2.0 * mu / r)


# ---------------------------------------------------------------------------
# Problem registry. Each function takes the problem's `given` dict (SI units)
# and returns a (value, unit) tuple in SI.
# ---------------------------------------------------------------------------
_REGISTRY: Dict[str, Callable[[dict], tuple[float, str]]] = {}


def gt(problem_id: str):
    def _wrap(fn):
        _REGISTRY[problem_id] = fn
        return fn
    return _wrap


@gt("astro-001")
def _astro_001(g):
    """Circular orbital speed at 400 km altitude."""
    r = R_EARTH + g["altitude_m"]
    return v_circ(r), "m/s"


@gt("astro-002")
def _astro_002(g):
    """Speed at perigee given perigee + apogee altitudes (multi: build a, then vis-viva)."""
    r_p = R_EARTH + g["perigee_altitude_m"]
    r_a = R_EARTH + g["apogee_altitude_m"]
    a = 0.5 * (r_p + r_a)
    return vis_viva(r_p, a), "m/s"


@gt("astro-003")
def _astro_003(g):
    """Orbital period of a circular orbit at 400 km altitude (Kepler-3)."""
    a = R_EARTH + g["altitude_m"]
    return period(a), "s"


@gt("astro-004")
def _astro_004(g):
    """Escape velocity from Earth's surface."""
    return v_escape(R_EARTH), "m/s"


@gt("astro-005")
def _astro_005(g):
    """Total Δv for a Hohmann transfer LEO(400km)→GEO. Multi: 4 formulas chained."""
    r1 = R_EARTH + g["start_altitude_m"]
    r2 = R_EARTH + g["target_altitude_m"]
    a_t = 0.5 * (r1 + r2)
    v_c1 = v_circ(r1)
    v_c2 = v_circ(r2)
    v_t1 = vis_viva(r1, a_t)
    v_t2 = vis_viva(r2, a_t)
    return (v_t1 - v_c1) + (v_c2 - v_t2), "m/s"


@gt("astro-006")
def _astro_006(g):
    """Period of a circular orbit at the GEO altitude (35 786 km)."""
    a = R_EARTH + g["altitude_m"]
    return period(a), "s"


@gt("astro-007")
def _astro_007(g):
    """Time of flight on a Hohmann transfer LEO→GEO. Multi: a_t, then Kepler-3 / 2."""
    r1 = R_EARTH + g["start_altitude_m"]
    r2 = R_EARTH + g["target_altitude_m"]
    a_t = 0.5 * (r1 + r2)
    return 0.5 * period(a_t), "s"


@gt("astro-008")
def _astro_008(g):
    """Circular orbital speed at GEO altitude."""
    r = R_EARTH + g["altitude_m"]
    return v_circ(r), "m/s"


@gt("astro-009")
def _astro_009(g):
    """Speed at apogee given perigee + apogee altitudes (multi)."""
    r_p = R_EARTH + g["perigee_altitude_m"]
    r_a = R_EARTH + g["apogee_altitude_m"]
    a = 0.5 * (r_p + r_a)
    return vis_viva(r_a, a), "m/s"


@gt("astro-010")
def _astro_010(g):
    """Simple plane-change Δv: 2 v_c sin(θ/2) at circular altitude."""
    v = v_circ(R_EARTH + g["altitude_m"])
    theta = math.radians(g["plane_change_deg"])
    return 2.0 * v * math.sin(theta / 2.0), "m/s"


# ---------------------------------------------------------------------------
# Round 2: 011..024. Mix of single (8) + multi (6). Topics chosen to stress
# both the knowledge axis (specific orbital energy, mean motion, Tsiolkovsky,
# sphere of influence, eccentricity, angular momentum, v_infinity) and the
# reasoning-depth axis (multi-step composition, domain shifts to Mars / the
# Sun, an inverse Kepler-3).
# ---------------------------------------------------------------------------
@gt("astro-011")
def _astro_011(g):
    """Specific orbital energy ε = −μ/(2a). Multi: compute a from apsides first."""
    r_p = R_EARTH + g["perigee_altitude_m"]
    r_a = R_EARTH + g["apogee_altitude_m"]
    a = 0.5 * (r_p + r_a)
    return -MU_EARTH / (2.0 * a), "J/kg"


@gt("astro-012")
def _astro_012(g):
    """Mean motion n = sqrt(μ/a³) of a circular orbit."""
    a = R_EARTH + g["altitude_m"]
    return math.sqrt(MU_EARTH / a ** 3), "rad/s"


@gt("astro-013")
def _astro_013(g):
    """Tsiolkovsky rocket equation: Δv = Isp · g0 · ln(m0/mf)."""
    return g["isp_s"] * G0 * math.log(g["mass_ratio"]), "m/s"


@gt("astro-014")
def _astro_014(g):
    """Hohmann Δv from one circular Earth orbit to another (smaller transfer)."""
    r1 = R_EARTH + g["start_altitude_m"]
    r2 = R_EARTH + g["target_altitude_m"]
    a_t = 0.5 * (r1 + r2)
    return (vis_viva(r1, a_t) - v_circ(r1)) + (v_circ(r2) - vis_viva(r2, a_t)), "m/s"


@gt("astro-015")
def _astro_015(g):
    """Hohmann Δv from LEO to a circular orbit at the Moon's distance
    (Earth-gravity only; ignores lunar gravity capture). Multi."""
    r1 = R_EARTH + g["start_altitude_m"]
    r2 = g["target_radius_m"]
    a_t = 0.5 * (r1 + r2)
    return (vis_viva(r1, a_t) - v_circ(r1)) + (v_circ(r2) - vis_viva(r2, a_t)), "m/s"


@gt("astro-016")
def _astro_016(g):
    """Sphere of influence radius: r_SOI = a · (m_planet/m_sun)^(2/5).
    Rare formula — discriminator for whether the oracle helps."""
    return g["a_planet_helio_m"] * (g["mass_ratio_to_sun"]) ** (2.0 / 5.0), "m"


@gt("astro-017")
def _astro_017(g):
    """Eccentricity from perigee + apogee: e = (r_a − r_p) / (r_a + r_p)."""
    r_p = R_EARTH + g["perigee_altitude_m"]
    r_a = R_EARTH + g["apogee_altitude_m"]
    return (r_a - r_p) / (r_a + r_p), "dimensionless"


@gt("astro-018")
def _astro_018(g):
    """Specific angular momentum at perigee: h = r_p · v_p (v ⊥ r at apsides)."""
    return g["perigee_radius_m"] * g["perigee_speed_m_s"], "m^2/s"


@gt("astro-019")
def _astro_019(g):
    """Escape velocity from Mars's surface — same formula, different μ and R."""
    return math.sqrt(2.0 * MU_MARS / R_MARS), "m/s"


@gt("astro-020")
def _astro_020(g):
    """Hyperbolic excess velocity from characteristic energy: v_∞ = sqrt(C3)."""
    # C3 given in km²/s² (the standard unit for launch literature); convert.
    c3_si = g["c3_km2_s2"] * 1.0e6  # (km/s)² → (m/s)²
    return math.sqrt(c3_si), "m/s"


@gt("astro-021")
def _astro_021(g):
    """Inverse Kepler-3: solve T = 2π√(a³/μ) for a, then altitude = a − R.
    GEO falls out when T = sidereal day."""
    T = g["period_s"]
    a = (MU_EARTH * T ** 2 / (4.0 * math.pi ** 2)) ** (1.0 / 3.0)
    return a - R_EARTH, "m"


@gt("astro-022")
def _astro_022(g):
    """Molniya-style perigee velocity. Multi: build a from apsides, vis-viva."""
    r_p = R_EARTH + g["perigee_altitude_m"]
    r_a = R_EARTH + g["apogee_altitude_m"]
    a = 0.5 * (r_p + r_a)
    return vis_viva(r_p, a), "m/s"


@gt("astro-023")
def _astro_023(g):
    """Hohmann Earth → Mars time of flight, heliocentric (uses μ_sun and AU).
    Multi + domain shift away from Earth-centric μ."""
    r1 = g["r1_helio_m"]
    r2 = g["r2_helio_m"]
    a_t = 0.5 * (r1 + r2)
    return math.pi * math.sqrt(a_t ** 3 / MU_SUN), "s"


@gt("astro-024")
def _astro_024(g):
    """Plane change at GEO altitude — same formula as astro-010, much higher r."""
    v = v_circ(R_EARTH + g["altitude_m"])
    theta = math.radians(g["plane_change_deg"])
    return 2.0 * v * math.sin(theta / 2.0), "m/s"


# ---------------------------------------------------------------------------
# Round 3: 025..034. Drawn from Hintz, "Orbital Mechanics and Astrodynamics"
# (Springer 2015). These touch material the earlier rounds skipped:
#   - flight-path angle, position from true anomaly (Ch 2)
#   - hyperbolic geometry, bi-elliptic transfer (Ch 3)
#   - Kepler's equation (Ch 4)
#   - J2 perturbation, sun-synchronous design (Ch 5)
# A pure mode-(b) discriminator: small-models often "know" these names but
# misremember the exponents / signs. The oracle cards should fix that.
# ---------------------------------------------------------------------------
@gt("astro-025")
def _astro_025(g):
    """Flight-path angle γ on an ellipse: tan γ = e sin ν / (1 + e cos ν).  (Hintz §2.4)"""
    e = g["eccentricity"]
    nu = math.radians(g["true_anomaly_deg"])
    return math.atan(e * math.sin(nu) / (1.0 + e * math.cos(nu))), "rad"


@gt("astro-026")
def _astro_026(g):
    """Orbital radius from true anomaly: r = a(1-e²) / (1 + e cos ν).  (Hintz §2.3)
    Multi: build semi-latus rectum p = a(1-e²), then divide."""
    a = g["semi_major_m"]
    e = g["eccentricity"]
    nu = math.radians(g["true_anomaly_deg"])
    p = a * (1.0 - e ** 2)
    return p / (1.0 + e * math.cos(nu)), "m"


@gt("astro-027")
def _astro_027(g):
    """Hyperbolic turn (deflection) angle δ from v_∞ and periapsis radius.  (Hintz §3.5)

    e = 1 + r_p v_∞² / μ;  sin(δ/2) = 1/e  ⇒  δ = 2 asin(1/e)."""
    r_p = g["periapsis_radius_m"]
    v_inf = g["v_infinity_m_s"]
    e_hyp = 1.0 + r_p * v_inf ** 2 / MU_EARTH
    return 2.0 * math.asin(1.0 / e_hyp), "rad"


@gt("astro-028")
def _astro_028(g):
    """Bi-elliptic transfer total Δv: LEO → high apogee r_b → r2 (three burns).  (Hintz §3.4)
    Multi: vis-viva four times + circular speed twice + three burn magnitudes."""
    r1 = R_EARTH + g["start_altitude_m"]
    r2 = R_EARTH + g["target_altitude_m"]
    r_b = R_EARTH + g["intermediate_altitude_m"]
    a_t1 = 0.5 * (r1 + r_b)
    a_t2 = 0.5 * (r_b + r2)
    v_c1 = v_circ(r1)
    v_c2 = v_circ(r2)
    v_t1_p = vis_viva(r1, a_t1)
    v_t1_a = vis_viva(r_b, a_t1)
    v_t2_a = vis_viva(r_b, a_t2)
    v_t2_p = vis_viva(r2, a_t2)
    dv1 = v_t1_p - v_c1               # burn at r1: speed up onto first transfer
    dv2 = v_t2_a - v_t1_a             # burn at r_b: swap onto second transfer
    dv3 = v_t2_p - v_c2               # burn at r2: circularize
    return dv1 + dv2 + dv3, "m/s"


@gt("astro-029")
def _astro_029(g):
    """J2 nodal regression rate of a circular orbit.  (Hintz §5.4)

    Ω_dot = -(3/2) J2 (R/p)² n cos i, with p = a(1-e²); for e=0, p=a.
    Sign: negative for prograde (i<90°) → westward node drift."""
    a = R_EARTH + g["altitude_m"]
    i = math.radians(g["inclination_deg"])
    p = a  # circular, e = 0
    n = math.sqrt(MU_EARTH / a ** 3)
    return -1.5 * J2_EARTH * (R_EARTH / p) ** 2 * n * math.cos(i), "rad/s"


@gt("astro-030")
def _astro_030(g):
    """Sun-synchronous orbit inclination — solve Ω_dot = 2π/sidereal_year for i.
    Inverts the J2 nodal regression formula.  (Hintz §5.4)
    Multi: compute target Ω_dot, build n and (R/p)², invert for cos(i)."""
    a = R_EARTH + g["altitude_m"]
    p = a  # circular
    n = math.sqrt(MU_EARTH / a ** 3)
    omega_dot_target = 2.0 * math.pi / SIDEREAL_YEAR
    cos_i = -omega_dot_target / (1.5 * J2_EARTH * (R_EARTH / p) ** 2 * n)
    return math.acos(cos_i), "rad"


@gt("astro-031")
def _astro_031(g):
    """Combined plane-change + circularize at Hohmann apogee — law of cosines.
    (Hintz §3.6, "Combined Maneuvers")

    Δv = sqrt(v_t² + v_c² − 2 v_t v_c cos θ),  with v_t at apogee of the
    transfer, v_c at the target circular orbit, θ the plane-change angle."""
    r1 = R_EARTH + g["start_altitude_m"]
    r2 = R_EARTH + g["target_altitude_m"]
    a_t = 0.5 * (r1 + r2)
    v_c2 = v_circ(r2)
    v_t2 = vis_viva(r2, a_t)
    theta = math.radians(g["plane_change_deg"])
    return math.sqrt(v_t2 ** 2 + v_c2 ** 2 - 2.0 * v_t2 * v_c2 * math.cos(theta)), "m/s"


@gt("astro-032")
def _astro_032(g):
    """Mean anomaly M from true anomaly ν via Kepler's equation.  (Hintz §2.4)

    E = 2 atan(sqrt((1-e)/(1+e)) · tan(ν/2));  M = E − e sin E.

    The answer is the mean anomaly itself (radians); time-since-periapsis
    would be t = M / n once the period is known."""
    e = g["eccentricity"]
    nu = math.radians(g["true_anomaly_deg"])
    E = 2.0 * math.atan(math.sqrt((1.0 - e) / (1.0 + e)) * math.tan(nu / 2.0))
    return E - e * math.sin(E), "rad"


@gt("astro-033")
def _astro_033(g):
    """Mars heliocentric orbital period via Kepler-3 with μ_sun.  (Hintz §2.4)

    Single but cross-domain: the same formula astro-003 uses, just with
    μ_sun and Mars's heliocentric a — a small "did you pick the right μ?" test."""
    a = A_MARS_HELIO
    return 2.0 * math.pi * math.sqrt(a ** 3 / MU_SUN), "s"


@gt("astro-034")
def _astro_034(g):
    """Synodic period of Earth and Mars: T_syn = 1 / |1/T_E − 1/T_M|.
    (Hintz §3.5, Table I, "Interplanetary Mission Design")
    Multi: build T_M from Mars's heliocentric a, use known T_E."""
    T_earth = SIDEREAL_YEAR
    T_mars = 2.0 * math.pi * math.sqrt(A_MARS_HELIO ** 3 / MU_SUN)
    return 1.0 / abs(1.0 / T_earth - 1.0 / T_mars), "s"


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------
def compute(problem_id: str, given: dict) -> tuple[float, str]:
    if problem_id not in _REGISTRY:
        raise KeyError(f"No ground-truth function registered for {problem_id!r}")
    return _REGISTRY[problem_id](given)


def known_ids() -> list[str]:
    return list(_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Round 4: 035..036. Designed for eval 003 to test whether `<think>` mode
# helps when oracle cards are already provided. Both require multi-step
# composition with a decision/check point where thinking *might* pay off.
# ---------------------------------------------------------------------------
@gt("astro-035")
def _astro_035(g):
    """Compare Hohmann vs bi-elliptic; return the SMALLER total Δv.
    For r2/r1 ratio of ~8 (LEO → 50000 km), Hohmann is optimal — but the
    model has to compute both transfers correctly to see that."""
    r1 = R_EARTH + g["start_altitude_m"]
    r2 = R_EARTH + g["target_altitude_m"]
    r_b = R_EARTH + g["intermediate_altitude_m"]

    # Hohmann
    a_t = 0.5 * (r1 + r2)
    dv_hohmann = (vis_viva(r1, a_t) - v_circ(r1)) + (v_circ(r2) - vis_viva(r2, a_t))

    # Bi-elliptic
    a_t1 = 0.5 * (r1 + r_b)
    a_t2 = 0.5 * (r_b + r2)
    dv_be = (
        (vis_viva(r1, a_t1) - v_circ(r1))      # burn at r1
        + (vis_viva(r_b, a_t2) - vis_viva(r_b, a_t1))  # burn at r_b (apogee of both)
        + (vis_viva(r2, a_t2) - v_circ(r2))    # burn at r2
    )
    return min(dv_hohmann, dv_be), "m/s"


@gt("astro-036")
def _astro_036(g):
    """Hyperbolic-flyby periapsis radius from energy conservation.

    At any radius on a hyperbolic trajectory:
        v² = v_∞² + 2 μ / r
    so periapsis r_p satisfies:
        r_p = 2 μ / (v_p² − v_∞²)

    Crucial point that thinking *might* catch: v_∞² and v_p² both have to
    be in SI (m/s)². The problem statement quotes km/s, so a unit
    conversion is needed."""
    v_p = g["periapsis_speed_m_s"]
    v_inf = g["v_infinity_m_s"]
    return 2.0 * MU_EARTH / (v_p ** 2 - v_inf ** 2), "m"


# ---------------------------------------------------------------------------
# Round 5: 037..038. Derivation tests for eval 005 — the source passage
# states the relation in NON-closed form (period-as-a-function-of-a; the
# energy equation), so producing the usable card requires SOLVING for the
# target, not copying. Both single-target → clean formula-equivalence metric.
# ---------------------------------------------------------------------------
@gt("astro-037")
def _astro_037(g):
    """Semi-major axis from orbital period — invert Kepler's third law.
    (Hintz §2.4)  T = 2π√(a³/μ)  ⇒  a = (μ T² / 4π²)^(1/3).
    The passage gives T(a); the model must SOLVE for a."""
    T = g["period_s"]
    return (MU_EARTH * T ** 2 / (4.0 * math.pi ** 2)) ** (1.0 / 3.0), "m"


@gt("astro-038")
def _astro_038(g):
    """Orbital speed from the energy equation (derive vis-viva).  (Hintz §2.3)
    E = v²/2 − μ/r = −μ/(2a)  ⇒  v = sqrt(μ(2/r − 1/a)).
    The passage gives the energy equation; the model must SOLVE for v."""
    r = g["radius_m"]
    a = g["semi_major_axis_m"]
    return vis_viva(r, a), "m/s"


# ---------------------------------------------------------------------------
# CLI: --check (assert match) / --regenerate (overwrite expected in JSON).
# ---------------------------------------------------------------------------
def _dataset_path() -> Path:
    # ground_truth.py lives next to its dataset, in datasets/astrodynamics/.
    return Path(__file__).resolve().parent / "v1.json"


def _load_problems(path: Path) -> list[dict]:
    with path.open() as f:
        return json.load(f)


def _save_problems(path: Path, problems: list[dict]) -> None:
    with path.open("w") as f:
        json.dump(problems, f, indent=2)
        f.write("\n")


def _check(rel_tol: float = 1e-9) -> int:
    path = _dataset_path()
    problems = _load_problems(path)
    bad = []
    missing = []
    for p in problems:
        pid = p["id"]
        if pid not in _REGISTRY:
            missing.append(pid)
            continue
        value, unit = compute(pid, p["given"])
        exp = p["expected"]
        if unit != exp["unit"]:
            bad.append((pid, f"unit mismatch: computed {unit}, JSON {exp['unit']}"))
            continue
        if exp["value"] == 0:
            ok = abs(value) < rel_tol
        else:
            ok = abs(value - exp["value"]) / abs(exp["value"]) < rel_tol
        if not ok:
            bad.append((pid, f"value mismatch: computed {value!r}, JSON {exp['value']!r}"))
    if missing:
        print(f"WARN: {len(missing)} problem(s) have no registered ground-truth fn: {missing}", file=sys.stderr)
    if bad:
        print(f"FAIL: {len(bad)} problem(s) differ from computed:", file=sys.stderr)
        for pid, msg in bad:
            print(f"  {pid}: {msg}", file=sys.stderr)
        return 1
    print(f"OK: {len(problems)} problem(s) match ground truth.")
    return 0


def _regenerate() -> int:
    path = _dataset_path()
    problems = _load_problems(path)
    updated = 0
    for p in problems:
        pid = p["id"]
        if pid not in _REGISTRY:
            print(f"  skip {pid} (no fn)")
            continue
        value, unit = compute(pid, p["given"])
        prev = p.get("expected", {})
        p["expected"] = {"value": value, "unit": unit}
        if prev != p["expected"]:
            updated += 1
            print(f"  updated {pid}: {prev} -> {p['expected']}")
    _save_problems(path, problems)
    print(f"Wrote {path} ({updated} updated).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compute / verify ground-truth answers.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="Assert dataset JSON matches computed values.")
    g.add_argument("--regenerate", action="store_true", help="Overwrite expected values in dataset JSON.")
    ap.add_argument("--rel-tol", type=float, default=1e-9, help="Relative tolerance for --check.")
    args = ap.parse_args(argv)
    if args.check:
        return _check(args.rel_tol)
    return _regenerate()


if __name__ == "__main__":
    raise SystemExit(main())
