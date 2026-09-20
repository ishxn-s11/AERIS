"""Gaussian plume atmospheric dispersion model for AERIS.

Implements the Pasquill-Gifford Gaussian plume model to predict ground-level
concentration of toxic gas downwind from a point source. Used to generate
isopleth contours (concentration rings) overlaid on a GIS map.

The model accounts for:
- Wind speed and direction (determines plume axis and dilution)
- Atmospheric stability class (Pasquill classes A-F, controls spread rates)
- Source emission rate (kg/s from measured gas concentration)
- Gas molecular weight (for buoyancy and diffusion)
- Stack/effective release height
- Ground-level receptor assumption (worst case for worker exposure)

References:
- Pasquill, F. (1976). Atmospheric Diffusion.
- Turner, D.B. (1970). Workbook of Atmospheric Dispersion Estimates.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

import numpy as np


class StabilityClass(str, Enum):
    """Pasquill-Gifford atmospheric stability classes."""
    A = "A"  # Very unstable (strong daytime insolation, light wind)
    B = "B"  # Moderately unstable
    C = "C"  # Slightly unstable
    D = "D"  # Neutral (overcast day/night, moderate wind)
    E = "E"  # Slightly stable (night, light wind)
    F = "F"  # Stable (clear night, light wind)


@dataclass
class WeatherConditions:
    """Current meteorological conditions affecting dispersion."""
    wind_speed: float = 3.0          # m/s at 10m height
    wind_direction: float = 0.0      # degrees from North (meteorological convention)
    stability_class: StabilityClass = StabilityClass.D
    temperature: float = 293.15      # Kelvin (20 C)
    mixing_height: float = 1000.0    # m, vertical mixing depth
    humidity: float = 0.5            # relative humidity (0-1)

    @classmethod
    def from_params(
        cls,
        wind_speed: float = 3.0,
        wind_direction: float = 0.0,
        stability_class: str = "D",
        temperature_c: float = 20.0,
    ) -> "WeatherConditions":
        return cls(
            wind_speed=max(wind_speed, 0.5),  # minimum 0.5 m/s for Gaussian
            wind_direction=wind_direction % 360,
            stability_class=StabilityClass(stability_class),
            temperature=temperature_c + 273.15,
        )


@dataclass
class GasSource:
    """Properties of the gas emission source."""
    gas_type: str = "methane"        # methane, co, h2s
    emission_rate: float = 0.01      # kg/s (estimated from sensor readings)
    release_height: float = 1.5      # m above ground (pipe leak height)
    molecular_weight: float = 16.04  # g/mol (CH4 = 16.04, CO = 28.01, H2S = 34.08)

    # Sensor ADC counts -> release rate (kg/s) calibration.
    #
    # The span covers a small instrument/pinhole leak at the low end up to a
    # full-bore line rupture at the top, which is the range that actually
    # matters for a gas dispersion study. An earlier profile topped out at
    # 0.1 kg/s (a pinhole), which put every ground-level contour three orders
    # of magnitude below any exposure limit — the plume could never reach a
    # safety band no matter how high the reading was.
    GAS_PROFILES = {
        "methane": {"mw": 16.04, "idlw": 50, "idhigh": 400, "q_low": 0.01, "q_high": 50.0},
        "co": {"mw": 28.01, "idlw": 30, "idhigh": 90, "q_low": 0.005, "q_high": 20.0},
        "h2s": {"mw": 34.08, "idlw": 20, "idhigh": 100, "q_low": 0.002, "q_high": 10.0},
    }

    @classmethod
    def from_sensor(cls, gas_type: str, sensor_value: float) -> "GasSource":
        """Estimate emission rate from the sensor reading.

        Interpolation is GEOMETRIC (log-linear) between the calibration points,
        not linear. Release rate spans four orders of magnitude — a leaking
        flange to a full-bore rupture — so a linear map makes the middle of the
        sensor span mean a 25 kg/s blowout, which over-states almost every real
        event. Log interpolation puts the mid-span reading at the geometric
        mean (0.7 kg/s of methane), which is a serious but plausible leak, and
        reserves the top of the range for genuinely catastrophic releases.
        """
        profile = cls.GAS_PROFILES.get(gas_type, cls.GAS_PROFILES["methane"])
        span = profile["idhigh"] - profile["idlw"]
        t = max(0.0, min(1.0, (sensor_value - profile["idlw"]) / span))
        q = profile["q_low"] * (profile["q_high"] / profile["q_low"]) ** t
        return cls(
            gas_type=gas_type,
            emission_rate=q,
            molecular_weight=profile["mw"],
        )


# Pasquill-Gifford dispersion coefficient parameters (sigma_y, sigma_z)
# Power-law coefficients: sigma = a * x^b
# Source: Turner (1970) / Martin (1976)
# x in meters, output in meters
_GAUSSIAN_COEFFS: dict[str, dict[str, tuple[float, float]]] = {
    # Stability class -> (sigma_y coeffs, sigma_z coeffs)
    "A": {"y": (0.3658, 0.8887), "z": (0.192, 1.149)},
    "B": {"y": (0.2751, 0.8954), "z": (0.156, 1.033)},
    "C": {"y": (0.2090, 0.8954), "z": (0.116, 0.925)},
    "D": {"y": (0.1471, 0.8954), "z": (0.079, 0.846)},
    "E": {"y": (0.1046, 0.8954), "z": (0.063, 0.786)},
    "F": {"y": (0.0726, 0.8954), "z": (0.052, 0.728)},
}


def sigma_y(x: float, stability: str) -> float:
    """Lateral dispersion coefficient (m) at downwind distance x (m)."""
    a, b = _GAUSSIAN_COEFFS[stability]["y"]
    return a * max(x, 1.0) ** b


def sigma_z(x: float, stability: str) -> float:
    """Vertical dispersion coefficient (m) at downwind distance x (m)."""
    a, b = _GAUSSIAN_COEFFS[stability]["z"]
    return a * max(x, 1.0) ** b


def ground_level_concentration(
    x: float, y: float, source: GasSource, weather: WeatherConditions,
) -> float:
    """Compute ground-level concentration (g/m3) at receptor (x, y).

    Coordinates: x = downwind distance, y = crosswind distance (m).
    Uses the Gaussian plume equation with ground reflection.

    Returns 0.0 for upwind or very close (within 1m) points.
    """
    if x <= 1.0:
        return 0.0

    u = weather.wind_speed
    sy = sigma_y(x, weather.stability_class.value)
    sz = sigma_z(x, weather.stability_class.value)
    H = source.release_height

    # Gaussian plume with ground reflection (image source)
    Q = source.emission_rate * 1000.0  # kg/s -> g/s

    # Lateral spread
    lateral = math.exp(-0.5 * (y / sy) ** 2)

    # Vertical spread with ground reflection
    vert = (
        math.exp(-0.5 * ((0 - H) / sz) ** 2)
        + math.exp(-0.5 * ((0 + H) / sz) ** 2)
    )

    denom = 2.0 * math.pi * u * sy * sz
    if denom < 1e-10:
        return 0.0

    return (Q / denom) * lateral * vert


def compute_isopleths(
    source: GasSource,
    weather: WeatherConditions,
    thresholds_g_m3: list[float] | None = None,
    max_distance: float = 2000.0,
    resolution: int = 80,
    wind_rotation: float = 0.0,
) -> dict:
    """Compute concentration isopleth contours around the source.

    Returns a dict with:
      - contours: list of {level, coordinates: [{x, y}, ...]} where x/y are
        map-frame metre offsets from the source (x = east, y = north)
      - max_concentration: peak ground-level value (g/m3)
      - max_distance_downwind: farthest point above lowest threshold
      - plume_axis_angle: bearing the plume blows towards (degrees)
      - heatmap: sparse grid cells {x, y, intensity} in the same map frame

    Parameters:
      thresholds_g_m3: concentration levels to contour (g/m3). When omitted
        the levels are derived from the plume's own peak so the diagram always
        shows a graded set of rings.
      resolution: heatmap sampling density (grid points per side, halved)
      wind_rotation: additional rotation to convert plume axis to map frame
    """
    # Meteorological wind_direction = where wind comes FROM (0=N, 90=E);
    # the plume axis is where the wind goes TO.
    plume_axis = (weather.wind_direction + 180.0) % 360.0
    x_max = min(max_distance, 2000.0)

    # Peak ground-level concentration along the centreline.
    centre_x = np.linspace(0.0, x_max, 240)
    centre = np.array([ground_level_concentration(x, 0.0, source, weather) for x in centre_x])
    max_c = float(centre.max()) if centre.size else 0.0

    levels = (
        list(thresholds_g_m3)
        if thresholds_g_m3
        else _adaptive_levels(max_c, source.gas_type, source.molecular_weight)
    )

    contours = []
    max_x_dist = 0.0
    for level in levels:
        ring = _isopleth_ring(source, weather, float(level), x_max)
        if not ring:
            continue
        points, x_hi = ring
        max_x_dist = max(max_x_dist, x_hi)
        rotated = _rotate_points(points, plume_axis + wind_rotation)
        contours.append({
            "level": round(float(level), 6),
            "coordinates": [{"x": round(px, 2), "y": round(py, 2)} for px, py in rotated],
        })

    # Fallback extent when no ring closed: furthest centreline point above the
    # lowest level (a very small plume still has a downwind reach).
    if max_x_dist == 0.0 and levels and centre.size:
        above = centre >= min(levels)
        if above.any():
            max_x_dist = float(centre_x[above][-1])

    # Heatmap cells sampled from the same analytic field, in map-frame metre
    # offsets (x = east, y = north) relative to the source.
    samples = max(8, resolution // 2)
    floor = (min(levels) * 0.25) if levels else 0.0
    heatmap = []
    for x in np.linspace(0.0, x_max, samples):
        for y in np.linspace(-x_max * 0.4, x_max * 0.4, max(8, samples * 2 // 3)):
            c = ground_level_concentration(float(x), float(y), source, weather)
            if c > floor:
                ex, nth = _rotate_point(float(x), float(y), plume_axis + wind_rotation)
                heatmap.append({"x": round(ex, 2), "y": round(nth, 2), "intensity": round(float(c), 6)})

    alarm_ppm = lowest_alarm_ppm(source.gas_type)
    alarm_range = alarm_downwind_distance(source, weather, x_max, alarm_ppm)

    return {
        "contours": contours,
        "max_concentration": float(max_c),
        "max_distance_downwind": float(max_x_dist),
        "plume_axis_angle": float(plume_axis),
        "wind_direction_from": float(weather.wind_direction),
        "wind_speed": weather.wind_speed,
        "stability_class": weather.stability_class.value,
        "source_gas": source.gas_type,
        "emission_rate_kg_s": source.emission_rate,
        "heatmap": heatmap,
        "alarm_threshold_ppm": alarm_ppm,
        "alarm_distance_downwind": alarm_range,
        "alarm_reached": alarm_range > 0.0,
    }


def lowest_alarm_ppm(gas_type: str) -> float:
    """Lowest hazardous exposure level for the gas, in ppm.

    Methane is graded by %LEL rather than toxicity, so its first band is the
    standard 10 %LEL low-alarm point; CO and H2S use their occupational
    exposure limits. See HEALTH_BANDS.
    """
    bands = HEALTH_BANDS.get(gas_type) or []
    return float(bands[0][0]) if bands else 0.0


def alarm_downwind_distance(
    source: GasSource, weather: WeatherConditions, x_max: float, alarm_ppm: float,
) -> float:
    """Downwind distance at which the plume drops below the gas's first alarm.

    This is the number a safety officer actually needs: how far from the leak
    a fixed detector would trigger. It is NOT the same as the drawn plume's
    extent — the outermost contour is a fixed fraction of the peak, so it
    always runs to the search radius and would report the same range for a
    pinhole and a rupture. Returns 0.0 when even the source never reaches the
    alarm level (the honest answer for a small leak of a gas with a high
    threshold).
    """
    if alarm_ppm <= 0:
        return 0.0
    threshold = ppm_to_concentration(alarm_ppm, source.molecular_weight)
    scan = np.linspace(1.0, x_max, 400)
    centre = np.array([ground_level_concentration(x, 0.0, source, weather) for x in scan])
    above = np.where(centre >= threshold)[0]
    if above.size == 0:
        return 0.0
    return float(scan[above[-1]])


def _adaptive_levels(
    peak: float, gas_type: str | None = None, molecular_weight: float = 16.04,
) -> list[float]:
    """Contour levels spanning the plume's own concentration range.

    A fixed g/m³ ladder cannot work across gases and release sizes — a pinhole
    and a full-bore rupture differ by four orders of magnitude, so a fixed set
    either collapses every ring onto one band or draws none at all.

    When the plume crosses one or more of the gas's own exposure limits, those
    limits ARE the levels, so the outermost ring is the first alarm level —
    i.e. the evacuation boundary — and the inner rings grade the hazard above
    it. Only when no exposure limit is crossed (a release too small to matter)
    does the ladder fall back to geometric fractions of the peak, which anchor
    the map scale and are honestly labelled LOW_RISK by `get_safety_zones`.

    An earlier version used geometric fractions unconditionally, so the
    outermost ring was 0.2 % of the peak — a meaningless trace contour that
    always ran to the search radius, making a small leak look as if it covered
    the whole map.
    """
    if peak <= 0:
        return []

    inner_fractions = (0.02, 0.08, 0.25, 0.5, 0.75)
    trace_fractions = (0.002, 0.01, 0.04, 0.12, 0.3, 0.55, 0.8)

    crossed = [
        ppm_to_concentration(band_ppm, molecular_weight)
        for band_ppm, _label, _colour in HEALTH_BANDS.get(gas_type or "", [])
    ]
    crossed = [c for c in crossed if 0.0 < c < peak]
    if not crossed:
        return [round(peak * f, 6) for f in trace_fractions]

    floor_level = min(crossed)
    graded = [peak * f for f in inner_fractions if floor_level < peak * f < peak]
    return sorted({round(v, 6) for v in crossed + graded})


def _solve_crossing(
    x_a: float,
    x_b: float,
    level: float,
    source: GasSource,
    weather: WeatherConditions,
    iterations: int = 40,
) -> float:
    """Bisect for the x in [x_a, x_b] where the centreline equals `level`.

    Assumes `level` lies between the endpoint concentrations (one above, one
    below), which holds for either side of the centreline peak. Falls back to
    the nearer endpoint when it does not.
    """
    f_a = ground_level_concentration(x_a, 0.0, source, weather) - level
    f_b = ground_level_concentration(x_b, 0.0, source, weather) - level
    if f_a == 0.0:
        return x_a
    if f_b == 0.0 or f_a * f_b > 0.0:
        return x_b if abs(f_b) < abs(f_a) else x_a

    lo, hi = x_a, x_b
    for _ in range(iterations):
        mid = 0.5 * (lo + hi)
        f_mid = ground_level_concentration(mid, 0.0, source, weather) - level
        if f_mid == 0.0:
            return mid
        if f_mid * f_a > 0.0:
            lo, f_a = mid, f_mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _isopleth_ring(
    source: GasSource,
    weather: WeatherConditions,
    level: float,
    x_max: float,
    samples: int = 40,
) -> tuple[list[tuple[float, float]], float] | None:
    """Trace one closed concentration isopleth in the plume frame.

    Walks the centreline to find the downwind span where the plume still
    reaches `level`, then inverts the Gaussian crosswind profile at each step
    to get the half-width. Returns the closed ring (x = downwind, y =
    crosswind) plus its downwind extent, or None when the level is not reached.

    This produces the smooth cigar outline of a published isopleth chart. A
    naive grid-crossing tracer connects scattered edge hits in scan order and
    renders a self-intersecting zig-zag instead.
    """
    scan = np.linspace(1.0, x_max, 200)
    centre = np.array([ground_level_concentration(x, 0.0, source, weather) for x in scan])
    above = np.where(centre >= level)[0]
    if above.size < 2:
        return None

    # Refine both ends onto the level so the ring closes to a point at its
    # nose and tail. Taking the raw scan indices leaves a blunt, detached nose:
    # the concentration climbs steeply near the source, so the first index
    # above the level can already be well clear of it, and the ring then starts
    # with a fat open edge (6 m wide in the CO case) instead of a tip.
    x_lo = _solve_crossing(
        float(scan[max(above[0] - 1, 0)]), float(scan[above[0]]), level, source, weather,
    )
    x_hi = _solve_crossing(
        float(scan[above[-1]]),
        float(scan[min(above[-1] + 1, scan.size - 1)]),
        level, source, weather,
    )
    if x_hi - x_lo < 1.0:
        return None

    stability = weather.stability_class.value
    xs = np.linspace(x_lo, x_hi, samples)
    half_widths: list[float] = []
    for x in xs:
        c_centre = ground_level_concentration(float(x), 0.0, source, weather)
        if c_centre <= level:
            half_widths.append(0.0)
            continue
        # Solve exp(-0.5 * (y / sigma_y)^2) = level / c_centre for y.
        sy = sigma_y(float(x), stability)
        half_widths.append(sy * math.sqrt(-2.0 * math.log(level / c_centre)))

    right = [(float(x), h) for x, h in zip(xs, half_widths)]
    left = [(float(x), -h) for x, h in zip(xs, half_widths)]
    return right + list(reversed(left)), x_hi


def _rotate_point(x: float, y: float, angle_deg: float) -> tuple[float, float]:
    """Map a plume-frame point to (east, north) offsets in metres.

    x = downwind distance along the blowing direction, y = crosswind distance.
    angle_deg is the meteorological bearing the plume blows TOWARDS (the plume
    axis). Returns (east_m, north_m) so consumers can treat x as the longitude
    offset and y as the latitude offset from the source.

    Example: wind FROM the north (0°) blows TOWARDS 180°; a point 100 m
    downwind maps to (0, -100) — 100 m due south. The previous implementation
    rotated by the complementary angle and laid the plume 90° off the wind.
    """
    rad = math.radians(angle_deg)
    s, c = math.sin(rad), math.cos(rad)
    return x * s + y * c, x * c - y * s


def _rotate_points(
    points: list[tuple[float, float]], angle_deg: float,
) -> list[tuple[float, float]]:
    return [_rotate_point(x, y, angle_deg) for x, y in points]


# Exposure bands per gas, as (ppm threshold, zone label, colour) ordered from
# the mildest band upward. A ring takes the highest band its ppm reaches.
#
# Methane is graded by %LEL (lower explosive limit = 5 %vol = 50 000 ppm)
# because that — not toxicity — is what makes it dangerous. The 10 %LEL and
# 20 %LEL steps are the standard gas-detector alarm points.
#
# These bands are absolute, so a small release honestly reads LOW_RISK rather
# than being painted red. An earlier version coloured any methane ring above
# 0.5 g/m³ (1.7 ppm) as "EXPLOSIVE_RISK", which is 30 000× below the LEL.
HEALTH_BANDS: dict[str, list[tuple[float, str, str]]] = {
    "methane": [
        (5_000, "WARNING", "#FFA500"),          # 10 %LEL — low alarm
        (10_000, "DANGER", "#FF4500"),          # 20 %LEL — high alarm
        (25_000, "EXPLOSIVE_RISK", "#FF0000"),  # 50 %LEL — explosive range
    ],
    "co": [
        (35, "WARNING", "#FFA500"),     # NIOSH REL (15-min STEL)
        (50, "DANGER", "#FF4500"),      # OSHA PEL (8-hour TWA)
        (1_200, "IDLH", "#8B0000"),     # NIOSH IDLH
    ],
    "h2s": [
        (10, "WARNING", "#FFA500"),     # NIOSH STEL
        (20, "DANGER", "#FF4500"),      # OSHA ceiling
        (100, "IDLH", "#8B0000"),       # NIOSH IDLH
    ],
}

LOW_RISK_COLOR = "#FFD700"

# Backwards-compatible alias (older imports referenced this name).
HEALTH_THRESHOLDS = HEALTH_BANDS


# Molar volume of an ideal gas at 25 C, 1 atm, in litres per mole. The
# standard occupational-hygiene conversion is ppm = mg/m3 * 24.45 / MW.
MOLAR_VOLUME_L_PER_MOL = 24.45


def concentration_to_ppm(c_g_m3: float, molecular_weight: float) -> float:
    """Convert a mass concentration in g/m3 to a volume fraction in ppm.

    ppm = (mg/m3 * 24.45) / MW, and there are 1000 mg in a gram, so the factor
    below is 24 450 / MW. Sanity check: methane's 5 %vol lower explosive limit
    is 50 000 ppm, which is 32.8 g/m3 — the same number the band table below
    uses for its top warning.

    An earlier version dropped the gram-to-milligram factor, understating every
    plume's strength by 1000x and letting a release that was 118 % by volume of
    methane report as a harmless 1200 ppm.
    """
    return (c_g_m3 * 1000.0 * MOLAR_VOLUME_L_PER_MOL) / molecular_weight


def ppm_to_concentration(ppm_value: float, molecular_weight: float) -> float:
    """Inverse of concentration_to_ppm: ppm to g/m3."""
    return (ppm_value * molecular_weight) / (1000.0 * MOLAR_VOLUME_L_PER_MOL)


def get_safety_zones(contours: list[dict], gas_type: str) -> list[dict]:
    """Classify contour levels into exposure bands for map colouring.

    Every band is an absolute ppm limit for the gas, so the label states what
    the concentration actually means for people on the ground.
    """
    bands = HEALTH_BANDS.get(gas_type, HEALTH_BANDS["methane"])
    mw = GasSource.GAS_PROFILES.get(gas_type, GasSource.GAS_PROFILES["methane"])["mw"]

    zones = []
    for contour in contours:
        level = contour["level"]
        ppm = concentration_to_ppm(level, mw)

        zone_type, color = "LOW_RISK", LOW_RISK_COLOR
        for threshold_ppm, label, band_color in bands:
            # Tolerance matters here: band thresholds ARE contour levels, and
            # levels are rounded to 6 dp before conversion, so an exact 35 ppm
            # CO ring lands on 34.9998 and would fall into the band below it —
            # labelling the OSHA exposure limit itself as low-risk.
            if ppm >= threshold_ppm or math.isclose(ppm, threshold_ppm, rel_tol=1e-4):
                zone_type, color = label, band_color

        zones.append({
            "level_g_m3": level,
            "concentration_ppm": round(ppm, 1),
            "zone_type": zone_type,
            "color": color,
            "coordinates": contour["coordinates"],
            "label": f"{ppm:.1f} ppm",
        })

    return zones
