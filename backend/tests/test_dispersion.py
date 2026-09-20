"""Dispersion model tests.

Two classes of defect these lock down, both of which produced dangerous
under-reporting in earlier versions of the model:

1. Unit conversion. The g/m3 -> ppm step must include the gram-to-milligram
   factor (24 450 / MW, not 24.45 / MW). Dropping it made a release that was
   118 % by volume of methane read as a harmless 1200 ppm.
2. Reporting. "How far does the plume reach" must be the distance at which the
   concentration drops below the gas's first alarm level, not the extent of the
   outermost drawn contour — that contour is a fixed fraction of the peak, so
   it always ran to the search radius and reported the same range for a pinhole
   and a full-bore rupture.
"""
from __future__ import annotations

import pytest

from app.ml.dispersion import (
    GasSource,
    WeatherConditions,
    compute_isopleths,
    concentration_to_ppm,
    get_safety_zones,
    lowest_alarm_ppm,
    ppm_to_concentration,
)


def _weather(stability: str = "D", wind_speed: float = 3.0) -> WeatherConditions:
    return WeatherConditions.from_params(
        wind_speed=wind_speed, wind_direction=0, stability_class=stability,
        temperature_c=20,
    )


class TestConcentrationConversion:
    def test_methane_lel_converts_to_its_published_ppm(self):
        """5 %vol methane = 50 000 ppm = ~32.8 g/m3 at 25 C."""
        assert concentration_to_ppm(32.8, 16.04) == pytest.approx(50_000, rel=0.01)

    def test_co_osha_pel_converts_to_its_published_ppm(self):
        """50 ppm CO (8-hour PEL) is 57 mg/m3."""
        assert concentration_to_ppm(0.0573, 28.01) == pytest.approx(50, rel=0.02)

    def test_round_trip(self):
        for ppm in (10, 500, 5_000, 50_000):
            g_m3 = ppm_to_concentration(ppm, 34.08)
            assert concentration_to_ppm(g_m3, 34.08) == pytest.approx(ppm, rel=1e-6)


class TestSourceTerm:
    def test_release_rate_is_bounded_by_the_calibration_span(self):
        profile = GasSource.GAS_PROFILES["methane"]
        assert GasSource.from_sensor("methane", 0).emission_rate == pytest.approx(profile["q_low"])
        assert GasSource.from_sensor("methane", 1023).emission_rate == pytest.approx(profile["q_high"])

    def test_mid_span_reading_is_geometric_not_arithmetic_mean(self):
        """Log interpolation: mid-span must be far below the arithmetic mean.

        A linear map put the middle of the sensor span at a 25 kg/s blowout,
        which over-stated almost every real leak.
        """
        profile = GasSource.GAS_PROFILES["methane"]
        mid = (profile["idlw"] + profile["idhigh"]) / 2
        q = GasSource.from_sensor("methane", mid).emission_rate
        arithmetic = (profile["q_low"] + profile["q_high"]) / 2
        geometric = (profile["q_low"] * profile["q_high"]) ** 0.5
        assert q == pytest.approx(geometric, rel=0.02)
        assert q < arithmetic / 10

    def test_release_rate_increases_with_the_reading(self):
        rates = [GasSource.from_sensor("co", v).emission_rate for v in (20, 50, 90, 300)]
        assert rates == sorted(rates)


class TestAlarmRange:
    def test_a_pinhole_that_never_reaches_the_alarm_reports_zero(self):
        """Honest answer beats a fabricated range."""
        source = GasSource.from_sensor("methane", 55)
        result = compute_isopleths(source, _weather(), max_distance=1000, resolution=30)
        if not result["alarm_reached"]:
            assert result["alarm_distance_downwind"] == 0.0

    def test_bigger_release_travels_further(self):
        small = compute_isopleths(GasSource.from_sensor("methane", 200), _weather(), max_distance=1500, resolution=30)
        large = compute_isopleths(GasSource.from_sensor("methane", 400), _weather(), max_distance=1500, resolution=30)
        assert small["alarm_reached"] and large["alarm_reached"]
        assert large["alarm_distance_downwind"] > small["alarm_distance_downwind"]

    def test_alarm_range_is_not_just_the_search_radius(self):
        """The old bug: range always equalled max_distance regardless of size."""
        small = compute_isopleths(GasSource.from_sensor("methane", 200), _weather(), max_distance=1500, resolution=30)
        assert small["alarm_distance_downwind"] < 1500

    def test_less_stable_air_dilutes_the_plume_sooner(self):
        source = GasSource.from_sensor("h2s", 115)
        unstable = compute_isopleths(source, _weather("A"), max_distance=1200, resolution=30)
        stable = compute_isopleths(source, _weather("F"), max_distance=1200, resolution=30)
        assert stable["alarm_distance_downwind"] > unstable["alarm_distance_downwind"]

    def test_threshold_is_the_gases_own_first_band(self):
        assert lowest_alarm_ppm("methane") == 5_000     # 10 %LEL
        assert lowest_alarm_ppm("co") == 35             # NIOSH REL
        assert lowest_alarm_ppm("h2s") == 10            # NIOSH STEL


class TestPlumeGeometry:
    def test_rings_are_closed_and_graded(self):
        source = GasSource.from_sensor("co", 100)
        result = compute_isopleths(source, _weather(), max_distance=800, resolution=40)
        rings = result["contours"]
        assert len(rings) >= 5
        levels = [r["level"] for r in rings]
        assert levels == sorted(levels)
        for ring in rings:
            xs = [p["x"] for p in ring["coordinates"]]
            ys = [p["y"] for p in ring["coordinates"]]
            assert min(xs) < max(xs)
            assert min(ys) < max(ys)
            # a closed ring walks out and back, so it returns near its start
            first, last = ring["coordinates"][0], ring["coordinates"][-1]
            assert abs(first["x"] - last["x"]) < 2.0

    def test_wind_from_north_lays_the_plume_south(self):
        """Meteorological convention: 0 deg means wind FROM the north."""
        source = GasSource.from_sensor("methane", 200)
        result = compute_isopleths(source, _weather(), max_distance=600, resolution=30)
        assert result["plume_axis_angle"] == pytest.approx(180.0)
        outer = result["contours"][0]
        assert max(p["y"] for p in outer["coordinates"]) < 0    # nothing upwind
        # Extent is set by the 5000 ppm alarm ring, so compare against the
        # model's own alarm distance rather than a hard-coded length.
        reach = abs(min(p["y"] for p in outer["coordinates"]))
        assert reach > 20
        assert reach == pytest.approx(result["alarm_distance_downwind"], rel=0.15)

    def test_wind_from_west_lays_the_plume_east(self):
        source = GasSource.from_sensor("methane", 200)
        west = WeatherConditions.from_params(
            wind_speed=3.0, wind_direction=270, stability_class="D", temperature_c=20,
        )
        result = compute_isopleths(source, west, max_distance=600, resolution=30)
        outer = result["contours"][0]
        assert min(p["x"] for p in outer["coordinates"]) > 0


class TestSafetyBands:
    def test_a_small_methane_release_is_not_painted_red(self):
        """Methane's hazard is explosivity (10 %LEL = 5000 ppm), not toxicity.

        An earlier version coloured any methane ring above 0.5 g/m3 (1.7 ppm)
        as EXPLOSIVE_RISK, which is ~30 000x below the lower explosive limit.
        """
        source = GasSource.from_sensor("methane", 60)
        result = compute_isopleths(source, _weather(), max_distance=500, resolution=30)
        zones = get_safety_zones(result["contours"], "methane")
        assert zones
        assert all(z["zone_type"] == "LOW_RISK" for z in zones)

    def test_a_rupture_does_reach_the_explosive_band(self):
        source = GasSource.from_sensor("methane", 1023)
        result = compute_isopleths(source, _weather(), max_distance=1500, resolution=40)
        zones = get_safety_zones(result["contours"], "methane")
        assert "EXPLOSIVE_RISK" in {z["zone_type"] for z in zones}

    def test_a_ring_exactly_on_a_threshold_takes_that_band(self):
        """Levels are rounded before conversion, so the boundary needs a
        tolerance — otherwise a 35 ppm CO ring lands on 34.9998 and the OSHA
        exposure limit gets painted LOW_RISK."""
        co_rel_g_m3 = ppm_to_concentration(35, 28.01)
        contours = [{"level": round(co_rel_g_m3, 6), "coordinates": []}]
        zones = get_safety_zones(contours, "co")
        assert zones[0]["zone_type"] == "WARNING"

    def test_outermost_ring_is_the_lowest_crossed_exposure_limit(self):
        """The evacuation boundary must be a real limit, not a trace fraction."""
        source = GasSource.from_sensor("co", 100)
        result = compute_isopleths(source, _weather(), max_distance=800, resolution=30)
        zones = get_safety_zones(result["contours"], "co")
        assert zones[0]["concentration_ppm"] == pytest.approx(lowest_alarm_ppm("co"), rel=1e-3)

    def test_h2s_reaches_idlh_far_below_methanes_low_alarm(self):
        """Toxicity and explosivity are different scales; the labels must differ."""
        h2s = GasSource.from_sensor("h2s", 115)
        result = compute_isopleths(h2s, _weather(), max_distance=1200, resolution=40)
        zones = get_safety_zones(result["contours"], "h2s")
        assert "IDLH" in {z["zone_type"] for z in zones}
