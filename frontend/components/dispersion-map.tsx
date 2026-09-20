"use client";
/**
 * GIS Dispersion Map — Google Earth–style satellite view with the Gaussian
 * plume drawn on top.
 *
 * Basemap: Esri World Imagery satellite tiles (keyless) plus dark place
 * labels, rendered through Leaflet. The plume contours, heatmap and source
 * arrow are drawn from the backend's map-frame metre offsets (x = east,
 * y = north relative to the source). KML export opens the same plume in
 * Google Earth.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import "leaflet/dist/leaflet.css";
import type { CircleMarker, Layer, Map as LeafletMap } from "leaflet";
import { api } from "@/lib/api";
import type {
  DispersionDefaults,
  DispersionRequest,
  DispersionResult,
  ZoneLive,
} from "@/lib/types";
import { riskHexOf } from "@/lib/risk";

/** Plant centre. Zones ride on a 0–100 grid; one grid unit ≈ 11 m. */
const CENTER: [number, number] = [28.6139, 77.209];
const SATELLITE_TILES =
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const LABEL_TILES =
  "https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png";

/** Smallest map span (metres) used when framing a plume, so a short plume
 * still reads in the context of the plant around it. */
const MIN_SPAN_M = 420;

/** Assumed footprint when a plant has coordinates but no surveyed span. */
const DEFAULT_SITE_SPAN_M = 800;

/** Where the map parks before a plant with a site fix has loaded. */
const NEUTRAL_CENTER: [number, number] = [22.5, 78.9];
const NEUTRAL_ZOOM = 4;

const ZONE_COLORS: Record<string, string> = {
  IDLH: "#8B0000",
  DANGER: "#FF4500",
  EXPLOSIVE_RISK: "#FF0000",
  WARNING: "#FFA500",
  LOW_RISK: "#FFD700",
};

/**
 * Reading span → release rate, mirroring GasSource.GAS_PROFILES on the
 * backend. Readings below `low` floor at the pinhole rate and readings above
 * `high` cap at the rupture rate.
 */
const SENSOR_SPANS: Record<string, { low: number; high: number; qLow: number; qHigh: number }> = {
  methane: { low: 50, high: 400, qLow: 0.01, qHigh: 50 },
  co: { low: 30, high: 90, qLow: 0.005, qHigh: 20 },
  h2s: { low: 20, high: 100, qLow: 0.002, qHigh: 10 },
};

type LeafletCtx = {
  L: typeof import("leaflet");
  map: LeafletMap;
};

/**
 * Where a plant is on the earth, and how its 0-100 zone grid maps onto it.
 *
 * Every map coordinate used by this component comes from this frame, injected
 * from the factory record. An earlier version hard-coded a lat/lng constant,
 * which drew an imaginary plume across whatever real city that constant
 * pointed at — the demo happened to land on central Delhi.
 */
type SiteFrame = {
  lat: number;
  lng: number;
  /** Metres per grid unit (site span / 100). */
  mPerUnit: number;
  spanM: number;
  name: string;
  location: string;
};

/** Build the site frame from a zone's plant record. Null when unsurveyed. */
function siteFrameFor(zone: ZoneLive | null | undefined): SiteFrame | null {
  if (!zone) return null;
  const { factory_latitude: lat, factory_longitude: lng, factory_site_span_m: span } = zone;
  if (lat === null || lng === null || lat === undefined || lng === undefined) return null;
  const spanM = span && span > 0 ? span : DEFAULT_SITE_SPAN_M;
  return {
    lat,
    lng,
    mPerUnit: spanM / 100,
    spanM,
    name: zone.factory_name ?? `FACTORY_${zone.factory_id}`,
    location: "",
  };
}

/** Grid (0–100) → lat/lng inside a plant's own frame. */
function gridToLatLng(frame: SiteFrame, x: number, y: number): [number, number] {
  const mPerDegLat = 111320;
  const mPerDegLng = 111320 * Math.cos((frame.lat * Math.PI) / 180);
  return [
    frame.lat + ((100 - y) - 50) * frame.mPerUnit / mPerDegLat,
    frame.lng + (x - 50) * frame.mPerUnit / mPerDegLng,
  ];
}

/** Corners of the plant footprint (grid 0-100) as lat/lngs. */
function siteFootprint(frame: SiteFrame): [number, number][] {
  return [
    gridToLatLng(frame, 0, 0),
    gridToLatLng(frame, 100, 0),
    gridToLatLng(frame, 100, 100),
    gridToLatLng(frame, 0, 100),
  ];
}

/** Metre offsets (east, north) from a source → lat/lng (proper mercator scale). */
function metresToLatLng(
  srcLat: number,
  srcLng: number,
  eastM: number,
  northM: number,
): [number, number] {
  const mPerDegLat = 111320;
  const mPerDegLng = 111320 * Math.cos((srcLat * Math.PI) / 180);
  return [srcLat + northM / mPerDegLat, srcLng + eastM / mPerDegLng];
}

/** Web-mercator metres per pixel at a latitude and integer zoom. */
function metresPerPixel(lat: number, zoom: number): number {
  return (156543.03392 * Math.cos((lat * Math.PI) / 180)) / 2 ** zoom;
}

/**
 * Frame a set of points, computing the zoom from the container's real pixel
 * size rather than relying on Leaflet's cached map size.
 *
 * `map.fitBounds` silently kept the previous zoom here: Leaflet caches its
 * size at construction and only re-reads it on a window resize, so a container
 * that is laid out by flex/aspect rules afterwards introduces a stale size and
 * the fit computes no zoom change (the plume stayed a few pixels tall on a
 * 1 km-wide view). Measuring the element directly sidesteps that entirely.
 */
function framePoints(
  L: typeof import("leaflet"),
  map: LeafletMap,
  container: HTMLElement | null,
  points: [number, number][],
  fill = 0.8,
): void {
  if (points.length === 0) return;
  const bounds = L.latLngBounds(points);
  const center = bounds.getCenter();

  const width = container?.clientWidth || 640;
  const height = container?.clientHeight || 380;

  const north = bounds.getNorth();
  const south = bounds.getSouth();
  const east = bounds.getEast();
  const west = bounds.getWest();
  const spanNorth = Math.abs(north - south) * 111320;
  const spanEast =
    Math.abs(east - west) * 111320 * Math.cos((center.lat * Math.PI) / 180);

  // Zoom that makes the larger span fill the smaller viewport dimension.
  const usable = Math.max(1, Math.min(width, height) * fill);
  // Floor the span so a small plume does not zoom past the point where the
  // satellite basemap still identifies the plant it sits in.
  const span = Math.max(spanNorth, spanEast, MIN_SPAN_M);
  const zoom = Math.log2((156543.03392 * Math.cos((center.lat * Math.PI) / 180)) / (span / usable));

  map.invalidateSize();
  map.setView([center.lat, center.lng], Math.max(3, Math.min(19, zoom)), { animate: false });
}

export function DispersionMap({
  zones,
  onSiteChanged,
}: {
  zones: ZoneLive[];
  /** Called after a site fix is saved so the caller can refetch its zones. */
  onSiteChanged?: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const leafletRef = useRef<LeafletCtx | null>(null);
  const zoneMarkersRef = useRef<Map<number, CircleMarker>>(new Map());
  const plumeLayerRef = useRef<Layer[]>([]);
  const siteLayerRef = useRef<Layer[]>([]);
  const zonesRef = useRef<ZoneLive[]>([]);
  const framedSiteRef = useRef<string | null>(null);

  const [ready, setReady] = useState(false);
  const [defaults, setDefaults] = useState<DispersionDefaults | null>(null);
  const [result, setResult] = useState<DispersionResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [windSpeed, setWindSpeed] = useState(3.0);
  const [windDir, setWindDir] = useState(0);
  const [stability, setStability] = useState("D");
  const [gasType, setGasType] = useState("methane");
  const [sensorValue, setSensorValue] = useState(200);
  const [temperature, setTemperature] = useState(20);
  const [maxDistance, setMaxDistance] = useState(500);
  const [sourceZone, setSourceZone] = useState<string>("");
  const [editingSite, setEditingSite] = useState(false);
  const [siteDraft, setSiteDraft] = useState({ lat: "", lng: "", span: "" });
  const [siteError, setSiteError] = useState<string | null>(null);
  const [savingSite, setSavingSite] = useState(false);

  // Which plant is on screen decides the whole geographic frame.
  const activeZone = zones.find((z) => String(z.zone_id) === sourceZone) ?? zones[0] ?? null;
  const frame = siteFrameFor(activeZone);

  useEffect(() => {
    zonesRef.current = zones;
  }, [zones]);

  const saveSite = useCallback(async () => {
    if (!activeZone) return;
    const lat = Number(siteDraft.lat);
    const lng = Number(siteDraft.lng);
    const span = Number(siteDraft.span);
    if (![lat, lng, span].every(Number.isFinite)) {
      setSiteError("Latitude, longitude and span must all be numbers");
      return;
    }
    if (lat < -90 || lat > 90 || lng < -180 || lng > 180) {
      setSiteError("Latitude must be -90…90 and longitude -180…180");
      return;
    }
    setSavingSite(true);
    setSiteError(null);
    try {
      await api.setFactoryLocation(activeZone.factory_id, {
        latitude: lat,
        longitude: lng,
        site_span_m: span,
      });
      setEditingSite(false);
      onSiteChanged?.();
    } catch (e: unknown) {
      setSiteError(e instanceof Error ? e.message : "Could not save the site fix");
    } finally {
      setSavingSite(false);
    }
  }, [activeZone, siteDraft, onSiteChanged]);

  /* ── Load model defaults ─────────────────────────────────────────── */
  useEffect(() => {
    api.dispersionDefaults().then(setDefaults).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (zones.length > 0 && !sourceZone) setSourceZone(String(zones[0].zone_id));
  }, [zones, sourceZone]);

  // Seed the source strength from the selected unit's live telemetry, so the
  // model starts from the reading that actually tripped the risk engine rather
  // than an arbitrary default. H2S has no channel on the node, so it keeps
  // whatever is on the slider.
  useEffect(() => {
    const zone = zones.find((z) => String(z.zone_id) === sourceZone);
    if (!zone?.latest) return;
    const reading =
      gasType === "co" ? zone.latest.co : gasType === "methane" ? zone.latest.methane : null;
    if (reading === null || !Number.isFinite(reading)) return;
    setSensorValue(Math.max(0, Math.min(1023, Math.round(reading))));
  }, [sourceZone, gasType, zones]);

  /* ── Boot the Leaflet map ───────────────────────────────────────── */
  useEffect(() => {
    let disposed = false;
    (async () => {
      const L = (await import("leaflet")).default;
      if (disposed || !containerRef.current || leafletRef.current) return;

      const boot = siteFrameFor(zonesRef.current[0]);
      const map = L.map(containerRef.current, {
        center: boot ? [boot.lat, boot.lng] : NEUTRAL_CENTER,
        zoom: boot ? 15 : NEUTRAL_ZOOM,
        zoomControl: true,
        attributionControl: true,
      });

      L.tileLayer(SATELLITE_TILES, {
        maxZoom: 19,
        attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
      }).addTo(map);
      L.tileLayer(LABEL_TILES, { maxZoom: 19, opacity: 0.85, attribution: "© CARTO" }).addTo(map);

      // Soften the imagery so the dark UI chrome stays legible.
      const pane = map.getPane("tilePane");
      if (pane) pane.style.filter = "saturate(0.85) brightness(0.88)";

      leafletRef.current = { L, map };
      setReady(true);
    })();

    return () => {
      disposed = true;
      leafletRef.current?.map.remove();
      leafletRef.current = null;
    };
  }, []);

  /* ── Zone markers (live risk colour) ────────────────────────────── */
  useEffect(() => {
    const ctx = leafletRef.current;
    if (!ready || !ctx || zones.length === 0) return;
    const { L, map } = ctx;

    // Markers are created once and then restyled in place. Rebuilding them on
    // every telemetry frame (the feed hands back a new array each push) also
    // re-fitted the viewport on each frame, which snapped the map back to the
    // whole plant and shrank the plume to a few pixels mid-inspection.
    const seen = new Set<number>();
    // One plant at a time. Grid coordinates are per-site, so projecting two
    // factories into one frame would place a second plant's units inside the
    // first one's fence line.
    const plantZones = zones.filter((z) => z.factory_id === activeZone?.factory_id);
    for (const z of plantZones) {
      seen.add(z.zone_id);
      if (!frame) continue;
      const [lat, lng] = gridToLatLng(frame, z.x, z.y);
      const tooltip = `<span style="font-family:monospace;font-size:11px">${z.name} · ${Math.round(z.risk_score)} ${z.risk_level}</span>`;

      let marker = zoneMarkersRef.current.get(z.zone_id);
      if (!marker) {
        marker = L.circleMarker([lat, lng], {
          radius: 8,
          color: "#08090a",
          weight: 2,
          fillColor: riskHexOf(z.risk_level),
          fillOpacity: 0.9,
        })
          .addTo(map)
          .bindTooltip(tooltip, { direction: "top", offset: [0, -6], opacity: 1 });
        marker.on("click", () => setSourceZone(String(z.zone_id)));
        zoneMarkersRef.current.set(z.zone_id, marker);
      } else {
        marker.setLatLng([lat, lng]);
        marker.setStyle({ fillColor: riskHexOf(z.risk_level) });
        marker.setTooltipContent(tooltip);
      }
    }

    for (const [id, marker] of zoneMarkersRef.current) {
      if (!seen.has(id)) {
        marker.remove();
        zoneMarkersRef.current.delete(id);
      }
    }

  }, [ready, zones, frame, activeZone?.factory_id]);

  /* ── Plant footprint ────────────────────────────────────────────── */
  useEffect(() => {
    const ctx = leafletRef.current;
    if (!ready || !ctx) return;
    const { L, map } = ctx;

    const plantKey = frame ? `${frame.lat},${frame.lng},${frame.spanM}` : null;
    if (framedSiteRef.current === plantKey) return;
    framedSiteRef.current = plantKey;

    siteLayerRef.current.forEach((l) => l.remove());
    siteLayerRef.current = [];

    // A different plant means the previous plume no longer belongs anywhere.
    plumeLayerRef.current.forEach((l) => l.remove());
    plumeLayerRef.current = [];
    setResult(null);

    if (!frame) {
      map.setView(NEUTRAL_CENTER, NEUTRAL_ZOOM, { animate: false });
      return;
    }

    const corners = siteFootprint(frame);
    const boundary = L.polygon(corners, {
      color: "#d8f14e",
      weight: 1.6,
      dashArray: "7 5",
      fillColor: "#d8f14e",
      fillOpacity: 0.05,
      interactive: false,
    }).addTo(map);
    siteLayerRef.current.push(boundary);

    // Plant identification, hung off the north-west corner so it is readable
    // at the default framing.
    const label = L.marker(corners[3], {
      interactive: false,
      icon: L.divIcon({
        className: "",
        html: `<div style="transform:translate(4px,4px);font-family:monospace;font-size:10px;line-height:1.4;letter-spacing:0.12em;color:#d8f14e;text-shadow:0 0 4px #000,0 0 2px #000;white-space:nowrap">`
          + `${frame.name.toUpperCase()} SITE BOUNDARY<br/><span style="color:#e9e7e2">${
              (frame.spanM / 1000).toFixed(2)} KM GRID · 100 × 100 UNITS</span></div>`,
        iconSize: [0, 0],
      }),
    }).addTo(map);
    siteLayerRef.current.push(label);

    framePoints(L, map, containerRef.current, corners, 1.0);
  }, [ready, frame]);

  /* ── Draw the plume ─────────────────────────────────────────────── */
  const drawPlume = useCallback((res: DispersionResult, zone: ZoneLive, site: SiteFrame) => {
    const ctx = leafletRef.current;
    if (!ctx) return;
    const { L, map } = ctx;

    plumeLayerRef.current.forEach((l) => l.remove());
    plumeLayerRef.current = [];

    const [srcLat, srcLng] = gridToLatLng(site, zone.x, zone.y);
    const framed: [number, number][] = [[srcLat, srcLng]];

    // Safety-zone polygons, outer (weakest) first so inner bands stay on top.
    [...res.safety_zones].reverse().forEach((zonePatch) => {
      if (zonePatch.coordinates.length < 3) return;
      const path = zonePatch.coordinates.map((pt) =>
        metresToLatLng(srcLat, srcLng, pt.x, pt.y),
      );
      const color = ZONE_COLORS[zonePatch.zone_type] ?? "#FFD700";
      const polygon = L.polygon(path, {
        color,
        weight: 2,
        opacity: 0.95,
        fillColor: color,
        fillOpacity: 0.3,
      })
        .addTo(map)
        .bindPopup(
          `<div style="font-family:monospace;font-size:11px;line-height:1.5">
             <b style="color:${color}">${zonePatch.zone_type}</b><br/>
             ${zonePatch.label} · ${zonePatch.level_g_m3.toFixed(3)} g/m³
           </div>`,
        );
      framed.push(...path);
      plumeLayerRef.current.push(polygon);
    });

    // Concentration heatmap cells.
    res.heatmap.forEach((cell) => {
      const radius = Math.max(6, Math.min(40, cell.intensity * 18));
      const circle = L.circle(metresToLatLng(srcLat, srcLng, cell.x, cell.y), {
        radius,
        stroke: false,
        fillColor: heatColor(cell.intensity),
        fillOpacity: Math.min(0.45, cell.intensity * 0.08 + 0.04),
        interactive: false,
      }).addTo(map);
      plumeLayerRef.current.push(circle);
    });

    // Source marker: arrow pointing along the plume axis (CSS rotation).
    const arrow = L.divIcon({
      className: "",
      html: `<div style="transform:rotate(${res.plume_axis_angle - 90}deg);font-size:20px;line-height:20px;color:#e5484d;text-shadow:0 0 6px #000,0 0 2px #000">➤</div>`,
      iconSize: [20, 20],
      iconAnchor: [10, 10],
    });
    const srcMarker = L.marker([srcLat, srcLng], { icon: arrow, zIndexOffset: 1000 })
      .addTo(map)
      .bindPopup(
        `<div style="font-family:monospace;font-size:11px">
           <b>${zone.name}</b><br/>${res.source_gas.toUpperCase()} source<br/>
           ${res.emission_rate_kg_s.toFixed(4)} kg/s<br/>
           plume bearing ${res.plume_axis_angle.toFixed(0)}°
         </div>`,
      );
    plumeLayerRef.current.push(srcMarker);

    // Always keep the plant boundary in frame, so the plume is never shown
    // detached from the site it belongs to.
    framePoints(L, map, containerRef.current, [...framed, ...siteFootprint(site)], 0.9);
  }, []);

  /* ── Actions ────────────────────────────────────────────────────── */
  const selectedZone = zones.find((z) => String(z.zone_id) === sourceZone) ?? zones[0] ?? null;

  const runPrediction = useCallback(async () => {
    if (!selectedZone || !frame) return;
    setLoading(true);
    setError(null);
    try {
      const req: DispersionRequest = {
        gas_type: gasType,
        sensor_value: sensorValue,
        wind_speed: windSpeed,
        wind_direction: windDir,
        stability_class: stability,
        temperature_c: temperature,
        source_x: selectedZone.x,
        source_y: selectedZone.y,
        max_distance: maxDistance,
        resolution: 60,
      };
      const res = await api.dispersionPredict(req);
      setResult(res);
      drawPlume(res, selectedZone, frame);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Dispersion model failed");
    } finally {
      setLoading(false);
    }
  }, [
    selectedZone, frame, gasType, sensorValue, windSpeed, windDir, stability,
    temperature, maxDistance, drawPlume,
  ]);

  const downloadKml = useCallback(() => {
    if (!selectedZone || !frame) return;
    const [srcLat, srcLng] = gridToLatLng(frame, selectedZone.x, selectedZone.y);
    const url = api.dispersionKmlUrl({
      gas_type: gasType,
      sensor_value: sensorValue,
      wind_speed: windSpeed,
      wind_direction: windDir,
      stability_class: stability,
      temperature_c: temperature,
      source_x: selectedZone.x,
      source_y: selectedZone.y,
      max_distance: maxDistance,
      resolution: 60,
      source_lat: srcLat,
      source_lng: srcLng,
    });
    window.open(url, "_blank");
  }, [selectedZone, frame, gasType, sensorValue, windSpeed, windDir, stability, temperature, maxDistance]);

  // Rings grouped by exposure band: several isopleths share one band, and the
  // legend should say what the band means (ppm range), not repeat itself.
  // Release rate implied by the current reading — mirrors
  // GasSource.from_sensor on the backend (log interpolation, see there) so the
  // operator can sanity-check the source term BEFORE running the model.
  const release = (() => {
    const span = SENSOR_SPANS[gasType] ?? SENSOR_SPANS.methane;
    const t = Math.max(0, Math.min(1, (sensorValue - span.low) / (span.high - span.low)));
    return span.qLow * (span.qHigh / span.qLow) ** t;
  })();
  const releaseBand = (() => {
    const span = SENSOR_SPANS[gasType] ?? SENSOR_SPANS.methane;
    if (release >= span.qHigh * 0.5) return "full-bore rupture";
    if (release >= span.qHigh * 0.1) return "major leak";
    if (release >= span.qHigh * 0.01) return "leak";
    return "pinhole";
  })();

  const bands = (() => {
    if (!result) return [];
    const byType = new Map<string, { type: string; color: string; min: number; max: number }>();
    for (const zone of result.safety_zones) {
      const entry = byType.get(zone.zone_type);
      if (entry) {
        entry.min = Math.min(entry.min, zone.concentration_ppm);
        entry.max = Math.max(entry.max, zone.concentration_ppm);
      } else {
        byType.set(zone.zone_type, {
          type: zone.zone_type,
          color: zone.color,
          min: zone.concentration_ppm,
          max: zone.concentration_ppm,
        });
      }
    }
    return [...byType.values()]
      .sort((a, b) => a.max - b.max)
      .map((b) => ({
        type: b.type.replace("_", " "),
        color: b.color,
        range:
          b.min === b.max
            ? `${b.min.toFixed(1)} ppm`
            : `${b.min.toFixed(1)}–${b.max.toFixed(1)} ppm`,
      }));
  })();

  const field = "micro text-faint";
  const value = "font-mono text-[11px] tabular-nums text-paper";
  const btn = (active: boolean) =>
    `border px-2 py-2 font-mono text-[11px] uppercase tracking-wider transition-colors ${
      active
        ? "border-volt bg-volt/10 text-volt"
        : "border-line text-muted hover:border-volt/50 hover:text-paper"
    }`;

  return (
    <div className="flex flex-col border border-line lg:flex-row">
      {/* ── Map ─────────────────────────────────────────────────────── */}
      <div className="relative h-[58vh] min-h-[380px] flex-1 lg:h-[74vh]">
        <div ref={containerRef} className="absolute inset-0 z-0" />

        {loading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-ink/60">
            <div className="border border-volt bg-ink-2 px-5 py-3 font-mono text-[11px] tracking-[0.2em] text-volt">
              COMPUTING DISPERSION MODEL…
            </div>
          </div>
        )}
        {error && (
          <div className="absolute left-1/2 top-4 z-20 -translate-x-1/2 border border-scarlet bg-ink-2 px-4 py-2 font-mono text-[11px] text-scarlet">
            {error}
          </div>
        )}

        {/* legend */}
        {result && (
          <div className="absolute bottom-4 left-4 z-10 max-w-[290px] border border-line bg-ink/90 p-3 backdrop-blur-sm">
            <div className="micro mb-2 text-faint">Exposure bands</div>
            {bands.map((band) => (
              <div key={band.type} className="flex items-baseline justify-between gap-3">
                <span className="flex items-center gap-2">
                  <span
                    className="h-2.5 w-2.5 border"
                    style={{ backgroundColor: `${band.color}55`, borderColor: band.color }}
                  />
                  <span className="font-mono text-[10px] text-muted">{band.type}</span>
                </span>
                <span className="font-mono text-[10px] tabular-nums text-paper">{band.range}</span>
              </div>
            ))}

            <div className="micro mb-2 mt-3 border-t border-line-soft pt-2 text-faint">
              Ground concentration
            </div>
            <div className="flex items-center gap-1">
              {["#ADFF2F", "#FFD700", "#FFA500", "#FF4500", "#FF0000"].map((c) => (
                <span key={c} className="h-2 w-6" style={{ backgroundColor: c, opacity: 0.75 }} />
              ))}
              <span className="ml-1 font-mono text-[9px] text-faint">
                low → {result.max_concentration.toFixed(2)} g/m³
              </span>
            </div>

            <div className="micro mt-3 border-t border-line-soft pt-2 text-faint">
              Rings = exposure isopleths · satellite © Esri
            </div>
          </div>
        )}
      </div>

      {/* ── Controls ────────────────────────────────────────────────── */}
      <aside className="w-full shrink-0 overflow-y-auto border-t border-line bg-ink-2 lg:w-[320px] lg:border-l lg:border-t-0">
        <div className="space-y-6 p-5">
          <div>
            <div className="micro mb-1 text-volt">07 · GIS</div>
            <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-paper">
              Dispersion model
            </h2>
            <p className="mt-2 font-mono text-[10px] leading-relaxed text-faint">
              Gaussian plume with Pasquill–Gifford coefficients. Contours are
              drawn on real satellite imagery; export the KML to inspect the
              same plume in Google Earth.
            </p>
          </div>

          <div className="space-y-2">
            <div className={field}>Gas source</div>
            {/* Grouped by plant: zone grid coordinates are site-local, and
                picking one moves the map to that plant's own coordinates. */}
            <select
              value={sourceZone}
              onChange={(e) => setSourceZone(e.target.value)}
              className="w-full border border-line bg-ink px-2 py-2 font-mono text-[11px] text-paper outline-none focus:border-volt"
            >
              {[...new Set(zones.map((z) => z.factory_id))].map((fid) => (
                <optgroup
                  key={fid}
                  label={zones.find((z) => z.factory_id === fid)?.factory_name ?? `FACTORY_${fid}`}
                >
                  {zones.filter((z) => z.factory_id === fid).map((z) => (
                    <option key={z.zone_id} value={z.zone_id}>
                      {z.name} · {z.risk_level}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>

          <div className="space-y-2">
            <div className={field}>Gas type</div>
            <div className="grid grid-cols-3 gap-2">
              {["methane", "co", "h2s"].map((g) => (
                <button key={g} onClick={() => setGasType(g)} className={btn(gasType === g)}>
                  {g}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <div className="flex items-baseline justify-between">
              <span className={field}>Sensor reading</span>
              <span className={value}>{sensorValue} ADC</span>
            </div>
            <input
              type="range" min={0} max={1023} value={sensorValue}
              onChange={(e) => setSensorValue(Number(e.target.value))}
              className="w-full accent-volt"
            />
            <div className="flex items-baseline justify-between border-t border-line-soft pt-1.5">
              <span className="font-mono text-[10px] text-muted">Release rate</span>
              <span className={value}>
                {release.toFixed(3)} kg/s · {(release * 3.6).toFixed(1)} t/h
              </span>
            </div>
            <div className="font-mono text-[10px] uppercase tracking-wider text-faint">
              {releaseBand}
            </div>
          </div>

          <div className="space-y-3 border-t border-line-soft pt-4">
            <div className={field}>Wind</div>
            <div className="space-y-1.5">
              <div className="flex items-baseline justify-between">
                <span className="font-mono text-[10px] text-muted">Speed</span>
                <span className={value}>{windSpeed.toFixed(1)} m/s</span>
              </div>
              <input
                type="range" min={0.5} max={20} step={0.5} value={windSpeed}
                onChange={(e) => setWindSpeed(Number(e.target.value))}
                className="w-full accent-volt"
              />
            </div>
            <div className="space-y-1.5">
              <div className="flex items-baseline justify-between">
                <span className="font-mono text-[10px] text-muted">From</span>
                <span className={value}>{windDir}° N</span>
              </div>
              <input
                type="range" min={0} max={355} step={5} value={windDir}
                onChange={(e) => setWindDir(Number(e.target.value))}
                className="w-full accent-volt"
              />
            </div>
          </div>

          <div className="space-y-2 border-t border-line-soft pt-4">
            <div className={field}>Stability class</div>
            <div className="grid grid-cols-6 gap-1">
              {defaults?.stability_classes.map((sc) => (
                <button
                  key={sc.id}
                  title={sc.description}
                  onClick={() => setStability(sc.id)}
                  className={btn(stability === sc.id)}
                >
                  {sc.id}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-1.5">
            <div className="flex items-baseline justify-between">
              <span className={field}>Temperature</span>
              <span className={value}>{temperature}°C</span>
            </div>
            <input
              type="range" min={-10} max={50} value={temperature}
              onChange={(e) => setTemperature(Number(e.target.value))}
              className="w-full accent-volt"
            />
          </div>

          <div className="space-y-1.5">
            <div className="flex items-baseline justify-between">
              <span className={field}>Plume range</span>
              <span className={value}>{maxDistance} m</span>
            </div>
            <input
              type="range" min={100} max={2000} step={50} value={maxDistance}
              onChange={(e) => setMaxDistance(Number(e.target.value))}
              className="w-full accent-volt"
            />
          </div>

          <div className="space-y-2 border-t border-line-soft pt-3">
            <div className="flex items-baseline justify-between">
              <span className={field}>Site fix</span>
              <button
                onClick={() => {
                  setSiteError(null);
                  setSiteDraft({
                    lat: frame ? String(frame.lat) : "",
                    lng: frame ? String(frame.lng) : "",
                    span: frame ? String(frame.spanM) : "800",
                  });
                  setEditingSite((v) => !v);
                }}
                className="font-mono text-[10px] uppercase tracking-wider text-volt hover:text-paper"
              >
                {editingSite ? "Cancel" : frame ? "Edit" : "Set"}
              </button>
            </div>

            {!editingSite && frame && (
              <div className="space-y-0.5">
                <div className="font-mono text-[11px] tabular-nums text-paper">
                  {frame.lat.toFixed(5)}, {frame.lng.toFixed(5)}
                </div>
                <div className="font-mono text-[10px] text-faint">
                  {frame.name} · grid spans {(frame.spanM / 1000).toFixed(2)} km
                </div>
              </div>
            )}

            {!editingSite && !frame && (
              <div className="border border-scarlet/60 bg-scarlet/5 p-3">
                <div className="micro text-scarlet">No site fix</div>
                <p className="mt-1 font-mono text-[10px] leading-relaxed text-muted">
                  {activeZone?.factory_name ?? "This plant"} has no latitude/longitude on record, so
                  there is nowhere to draw a plume. A dispersion map only means something once the
                  site is placed on the earth.
                </p>
              </div>
            )}

            {editingSite && (
              <div className="space-y-2 border border-line p-2.5">
                <div className="grid grid-cols-2 gap-2">
                  {([
                    { key: "lat", label: "Latitude", ph: "28.63333" },
                    { key: "lng", label: "Longitude", ph: "77.14389" },
                  ] as const).map((f) => (
                    <label key={f.key} className="space-y-1">
                      <span className="micro text-faint">{f.label}</span>
                      <input
                        value={siteDraft[f.key]}
                        onChange={(e) => setSiteDraft((d) => ({ ...d, [f.key]: e.target.value }))}
                        placeholder={f.ph}
                        inputMode="decimal"
                        className="w-full border border-line bg-ink px-2 py-1.5 font-mono text-[11px] tabular-nums text-paper outline-none focus:border-volt"
                      />
                    </label>
                  ))}
                </div>
                <label className="block space-y-1">
                  <span className="micro text-faint">Grid span (m)</span>
                  <input
                    value={siteDraft.span}
                    onChange={(e) => setSiteDraft((d) => ({ ...d, span: e.target.value }))}
                    placeholder="900"
                    inputMode="numeric"
                    className="w-full border border-line bg-ink px-2 py-1.5 font-mono text-[11px] tabular-nums text-paper outline-none focus:border-volt"
                  />
                </label>
                {siteError && (
                  <p className="font-mono text-[10px] leading-relaxed text-scarlet">{siteError}</p>
                )}
                <button
                  onClick={saveSite}
                  disabled={savingSite || !activeZone}
                  className="w-full bg-volt py-2 font-mono text-[10px] uppercase tracking-[0.16em] text-ink transition-opacity hover:opacity-90 disabled:opacity-40"
                >
                  {savingSite ? "Saving…" : "Save site fix"}
                </button>
                <p className="font-mono text-[10px] leading-relaxed text-faint">
                  Zone grid is 0–100 across each axis, so the span sets metres per grid unit. An
                  admin or safety officer role is required to save.
                </p>
              </div>
            )}
          </div>

          <div className="space-y-2 pt-1">
            <button
              onClick={runPrediction}
              disabled={loading || !selectedZone || !frame}
              className="w-full bg-volt py-3 font-mono text-[11px] uppercase tracking-[0.16em] text-ink transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              {loading ? "Computing…" : "Run dispersion model"}
            </button>
            <button
              onClick={downloadKml}
              disabled={!result || !frame}
              className="w-full border border-line py-3 font-mono text-[11px] uppercase tracking-[0.16em] text-paper transition-colors hover:border-volt hover:text-volt disabled:opacity-30"
            >
              KML → Google Earth
            </button>
          </div>

          {result && (
            <div className="space-y-3 border-t border-line-soft pt-4">
              <div className={field}>Results</div>
              <div className="grid grid-cols-2 gap-3">
                <Metric label="Peak conc" value={result.max_concentration.toFixed(3)} unit="g/m³" />
                <Metric
                  label="Alarm range"
                  value={
                    result.alarm_reached
                      ? `${result.alarm_distance_downwind.toFixed(0)}${
                          // The sweep stops at the search radius, so a range that
                          // lands exactly on it is a floor, not a measurement.
                          result.alarm_distance_downwind >= maxDistance - 1 ? "+" : ""
                        }`
                      : "—"
                  }
                  unit="m"
                />
                <Metric label="Emission" value={result.emission_rate_kg_s.toFixed(3)} unit="kg/s" />
                <Metric label="Plume axis" value={result.plume_axis_angle.toFixed(0)} unit="°" />
              </div>
              <p className="font-mono text-[10px] leading-relaxed text-faint">
                {result.alarm_reached
                  ? `Alarm range is where ground concentration drops below ${result.alarm_threshold_ppm.toFixed(0)} ppm — the first alarm level for this gas. Raise the max-range slider to find the true extent.`
                  : `This release never reaches ${result.alarm_threshold_ppm.toFixed(0)} ppm at ground level — no fixed detector would trip. Plume reach shown at ${result.max_distance_downwind.toFixed(0)} m.`}
              </p>
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

function Metric({ label, value, unit }: { label: string; value: string; unit: string }) {
  return (
    <div className="border-t border-line-soft pt-2">
      <div className="micro text-faint">{label}</div>
      <div className="font-mono text-[13px] tabular-nums text-paper">
        {value} <span className="text-[9px] text-faint">{unit}</span>
      </div>
    </div>
  );
}

function heatColor(intensity: number): string {
  if (intensity > 5) return "#FF0000";
  if (intensity > 1) return "#FF4500";
  if (intensity > 0.5) return "#FFA500";
  if (intensity > 0.1) return "#FFD700";
  return "#ADFF2F";
}
