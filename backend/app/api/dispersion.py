"""Gas dispersion API — computes Gaussian plume spread and returns contours.

Endpoints:
  POST /api/dispersion/predict   — compute isopleth contours from weather + gas data
  GET  /api/dispersion/kml       — downloadable KML file for Google Earth
  GET  /api/dispersion/defaults  — default weather conditions and gas thresholds
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.ml.dispersion import (
    GasSource,
    HEALTH_THRESHOLDS,
    WeatherConditions,
    compute_isopleths,
    concentration_to_ppm,
    get_safety_zones,
)

dispersion_router = APIRouter(prefix="/dispersion", tags=["dispersion"])


# ── Request / Response schemas ────────────────────────────────────────

class DispersionRequest(BaseModel):
    """Input for dispersion prediction."""
    gas_type: str = Field(default="methane", description="Gas type: methane, co, h2s")
    sensor_value: float = Field(default=200.0, ge=0, le=1023, description="Sensor ADC reading")
    wind_speed: float = Field(default=3.0, ge=0.1, le=50.0, description="Wind speed (m/s)")
    wind_direction: float = Field(default=0.0, ge=0, le=360, description="Wind from direction (degrees, N=0)")
    stability_class: str = Field(default="D", description="Pasquill stability class: A-F")
    temperature_c: float = Field(default=20.0, description="Temperature in Celsius")
    source_x: float = Field(default=50.0, description="Source X on factory map (0-100)")
    source_y: float = Field(default=50.0, description="Source Y on factory map (0-100)")
    max_distance: float = Field(default=500.0, ge=50, le=5000, description="Max dispersion distance (m)")
    resolution: int = Field(default=60, ge=20, le=120, description="Grid resolution")


class ContourPoint(BaseModel):
    x: float
    y: float


class Contour(BaseModel):
    level: float
    coordinates: list[ContourPoint]


class SafetyZone(BaseModel):
    level_g_m3: float
    concentration_ppm: float
    zone_type: str
    color: str
    coordinates: list[ContourPoint]
    label: str


class DispersionResponse(BaseModel):
    """Computed dispersion result."""
    source_x: float
    source_y: float
    plume_axis_angle: float
    wind_direction_from: float
    wind_speed: float
    stability_class: str
    source_gas: str
    emission_rate_kg_s: float
    max_concentration: float
    max_distance_downwind: float
    alarm_threshold_ppm: float = 0.0
    alarm_distance_downwind: float = 0.0
    alarm_reached: bool = False
    contours: list[Contour]
    safety_zones: list[SafetyZone]
    heatmap: list[dict]


class WeatherDefaults(BaseModel):
    wind_speed: float = 3.0
    wind_direction: float = 0.0
    stability_class: str = "D"
    temperature_c: float = 20.0
    stability_classes: list[dict]
    gas_types: list[dict]


# ── Endpoints ─────────────────────────────────────────────────────────

@dispersion_router.get("/defaults", response_model=WeatherDefaults)
def get_defaults(_: None = Depends(get_current_user)):
    """Return default weather parameters and available gas types."""
    return WeatherDefaults(
        stability_classes=[
            {"id": "A", "name": "Very Unstable", "description": "Strong daytime sun, light wind"},
            {"id": "B", "name": "Moderately Unstable", "description": "Moderate sun, light wind"},
            {"id": "C", "name": "Slightly Unstable", "description": "Slight sun or overcast day"},
            {"id": "D", "name": "Neutral", "description": "Overcast day/night, moderate wind"},
            {"id": "E", "name": "Slightly Stable", "description": "Clear night, light wind"},
            {"id": "F", "name": "Stable", "description": "Clear night, very light wind"},
        ],
        gas_types=[
            {"id": "methane", "name": "Methane (CH4)", "mw": 16.04, "lel": "5% vol"},
            {"id": "co", "name": "Carbon Monoxide (CO)", "mw": 28.01, "pel": "50 ppm"},
            {"id": "h2s", "name": "Hydrogen Sulfide (H2S)", "mw": 34.08, "idlh": "100 ppm"},
        ],
    )


@dispersion_router.post("/predict", response_model=DispersionResponse)
def predict_dispersion(
    req: DispersionRequest,
    _: None = Depends(get_current_user),
):
    """Compute gas dispersion contours from source + weather conditions.

    Returns isopleth contours (concentration rings), safety zones, and
    heatmap data for overlay on the factory GIS map.
    """
    # Build source and weather objects
    source = GasSource.from_sensor(req.gas_type, req.sensor_value)
    weather = WeatherConditions.from_params(
        wind_speed=req.wind_speed,
        wind_direction=req.wind_direction,
        stability_class=req.stability_class,
        temperature_c=req.temperature_c,
    )

    # Contour levels are derived from the plume's own peak concentration, so a
    # pinhole leak and a line rupture both produce a graded set of rings.
    result = compute_isopleths(
        source=source,
        weather=weather,
        thresholds_g_m3=None,
        max_distance=req.max_distance,
        resolution=req.resolution,
    )

    # Add safety zone classification
    safety_zones = get_safety_zones(result["contours"], req.gas_type)

    return DispersionResponse(
        source_x=req.source_x,
        source_y=req.source_y,
        plume_axis_angle=result["plume_axis_angle"],
        wind_direction_from=result["wind_direction_from"],
        wind_speed=result["wind_speed"],
        stability_class=result["stability_class"],
        source_gas=result["source_gas"],
        emission_rate_kg_s=result["emission_rate_kg_s"],
        max_concentration=result["max_concentration"],
        max_distance_downwind=result["max_distance_downwind"],
        alarm_threshold_ppm=result.get("alarm_threshold_ppm", 0.0),
        alarm_distance_downwind=result.get("alarm_distance_downwind", 0.0),
        alarm_reached=result.get("alarm_reached", False),
        contours=result["contours"],
        safety_zones=safety_zones,
        heatmap=result["heatmap"],
    )


@dispersion_router.get("/kml")
def generate_kml(
    gas_type: str = Query(default="methane"),
    sensor_value: float = Query(default=200.0),
    wind_speed: float = Query(default=3.0),
    wind_direction: float = Query(default=0.0),
    stability_class: str = Query(default="D"),
    temperature_c: float = Query(default=20.0),
    source_lat: float = Query(default=28.6139, description="Source latitude"),
    source_lng: float = Query(default=77.2090, description="Source longitude"),
    max_distance: float = Query(default=500.0),
    resolution: int = Query(default=60),
):
    """Generate a KML file for Google Earth with dispersion contours.

    Plume contours are placed as polygons at the source lat/lng.
    """
    source = GasSource.from_sensor(gas_type, sensor_value)
    weather = WeatherConditions.from_params(
        wind_speed=wind_speed,
        wind_direction=wind_direction,
        stability_class=stability_class,
        temperature_c=temperature_c,
    )

    result = compute_isopleths(
        source=source,
        weather=weather,
        thresholds_g_m3=None,  # adaptive levels (see compute_isopleths)
        max_distance=max_distance,
        resolution=resolution,
    )
    safety_zones = get_safety_zones(result["contours"], gas_type)

    kml = _build_kml(safety_zones, source_lat, source_lng, gas_type, wind_direction, wind_speed)

    return Response(
        content=kml,
        media_type="application/vnd.google-earth.kml+xml",
        headers={"Content-Disposition": f"attachment; filename=aeris_dispersion_{gas_type}.kml"},
    )


def _build_kml(
    safety_zones: list[dict],
    lat: float, lng: float,
    gas_type: str, wind_dir: float, wind_speed: float,
) -> str:
    """Build KML XML string with dispersion contour polygons."""
    mw = GasSource.GAS_PROFILES.get(gas_type, GasSource.GAS_PROFILES["methane"])["mw"]
    gas_name = gas_type.upper()

    # Convert contour coordinates to lat/lng offsets
    # 1 degree latitude ~ 111,320 m; 1 degree longitude ~ 111,320 * cos(lat)
    m_per_deg_lat = 111320.0
    m_per_deg_lng = 111320.0 * math.cos(math.radians(lat))

    placemarks = []
    for zone in safety_zones:
        coords = zone["coordinates"]
        if len(coords) < 3:
            continue

        # Convert meters to lat/lng offsets
        coord_str = "\n".join(
            f"                    {lng + pt['x'] / m_per_deg_lng:.6f},{lat + pt['y'] / m_per_deg_lat:.6f},0"
            for pt in coords
        )
        # Close the polygon
        first = coords[0]
        coord_str += f"\n                    {lng + first['x'] / m_per_deg_lng:.6f},{lat + first['y'] / m_per_deg_lat:.6f},0"

        ppm = zone["concentration_ppm"]
        ztype = zone["zone_type"]
        color = zone["color"]

        # KML color is AABBGGRR
        kml_color = _hex_to_kml_color(color, alpha="80")

        placemarks.append(f"""
        <Placemark>
            <name>{gas_name} {ztype} - {ppm:.1f} ppm</name>
            <description>Concentration: {zone['level_g_m3']:.3f} g/m3 ({ppm:.1f} ppm)</description>
            <styleUrl>#zone_{ztype}</styleUrl>
            <Polygon>
                <outerBoundaryIs>
                    <LinearRing>
                        <coordinates>
                    {coord_str}
                        </coordinates>
                    </LinearRing>
                </outerBoundaryIs>
            </Polygon>
        </Placemark>""")

    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>AERIS Gas Dispersion - {gas_name}</name>
    <description>
      Gaussian plume dispersion model for {gas_name}.
      Wind: {wind_speed:.1f} m/s from {wind_dir:.0f} deg.
      Generated by AERIS Atmospheric Monitoring Platform.
    </description>

    <Style id="zone_IDLH">
      <LineStyle><color>ff00008b</color><width>2</width></LineStyle>
      <PolyStyle><color>ff00008b</color></PolyStyle>
    </Style>
    <Style id="zone_DANGER">
      <LineStyle><color>ff0045ff</color><width>2</width></LineStyle>
      <PolyStyle><color>800045ff</color></PolyStyle>
    </Style>
    <Style id="zone_WARNING">
      <LineStyle><color>ff00a5ff</color><width>2</width></LineStyle>
      <PolyStyle><color>8000a5ff</color></PolyStyle>
    </Style>
    <Style id="zone_EXPLOSIVE_RISK">
      <LineStyle><color>ff0000ff</color><width>3</width></LineStyle>
      <PolyStyle><color>800000ff</color></PolyStyle>
    </Style>
    <Style id="zone_LOW_RISK">
      <LineStyle><color>ff00d7ff</color><width>1</width></LineStyle>
      <PolyStyle><color>6000d7ff</color></PolyStyle>
    </Style>

    <!-- Source marker -->
    <Placemark>
      <name>Gas Source ({gas_name})</name>
      <Point>
        <coordinates>{lng},{lat},0</coordinates>
      </Point>
      <styleUrl>#source_marker</styleUrl>
    </Placemark>

    {"".join(placemarks)}
  </Document>
</kml>"""
    return kml


def _hex_to_kml_color(hex_color: str, alpha: str = "80") -> str:
    """Convert #RRGGBB to KML AABBGGRR format."""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"{alpha}{b}{g}{r}"


import math  # needed by _build_kml
