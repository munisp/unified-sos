// Dependency-free mapping core: demo map style, GeoJSON plumbing and
// environment gating shared by the MapLibre panel and the Cesium 3D cadastre.
// Demo mode requires NO network: the base style is built inline and fixture
// GeoJSON is attached as in-memory sources. Live mode points at the
// self-hosted GeoLibre tile/style endpoints via VITE_MAP_STYLE_URL.

export interface GeoJsonGeometry {
  type: 'Point' | 'Polygon' | 'MultiPolygon' | 'LineString';
  coordinates: unknown;
}

export interface GeoJsonFeature {
  type: 'Feature';
  geometry: GeoJsonGeometry;
  properties?: Record<string, unknown> | null;
}

export interface GeoJsonFeatureCollection {
  type: 'FeatureCollection';
  name?: string;
  features: GeoJsonFeature[];
}

/** Structural sanity check — not a full RFC 7946 validator. */
export function isFeatureCollection(v: unknown): v is GeoJsonFeatureCollection {
  if (!v || typeof v !== 'object') return false;
  const fc = v as GeoJsonFeatureCollection;
  if (fc.type !== 'FeatureCollection' || !Array.isArray(fc.features)) return false;
  return fc.features.every(
    (f) =>
      f &&
      f.type === 'Feature' &&
      f.geometry &&
      typeof f.geometry.type === 'string' &&
      f.geometry.coordinates !== undefined,
  );
}

/** Schematic Nigeria outline (lon/lat pairs), kept in sync with lib/geo.ts. */
export const NIGERIA_OUTLINE: [number, number][] = [
  [2.7, 6.4], [3.6, 6.45], [4.3, 6.1], [5.6, 5.6], [6.1, 4.6], [7.0, 4.4],
  [8.5, 4.5], [8.5, 5.0], [9.4, 5.3], [9.7, 6.1], [10.4, 6.9], [11.6, 6.7],
  [12.5, 7.4], [13.6, 8.0], [14.6, 9.3], [14.8, 10.8], [14.0, 12.4],
  [13.6, 13.7], [12.0, 13.6], [10.0, 13.3], [7.8, 13.3], [5.8, 13.5],
  [4.4, 13.8], [3.6, 12.4], [3.5, 11.7], [2.8, 11.6], [0.9, 11.2],
  [0.9, 10.0], [1.2, 8.9], [1.6, 7.4], [2.7, 6.4],
];

/** National extent as [minLon, minLat, maxLon, maxLat] — maplibre bounds order. */
export const NIGERIA_BOUNDS: [[number, number], [number, number]] = [
  [2.5, 4.0],
  [15.0, 14.2],
];

export const DEMO_COLORS = {
  background: '#faf6ef',
  land: '#f3ece1',
  landOutline: '#e3d9c9',
  geofence: '#8c5230',
  geofenceFill: 'rgba(140, 82, 48, 0.08)',
} as const;

function nigeriaLandFeature(): GeoJsonFeature {
  return {
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [NIGERIA_OUTLINE] },
    properties: { name: 'Nigeria (schematic)' },
  };
}

export interface MaplibreLayerSpec {
  id: string;
  type: string;
  source?: string;
  paint?: Record<string, unknown>;
  layout?: Record<string, unknown>;
  filter?: unknown[];
  minzoom?: number;
  maxzoom?: number;
  [key: string]: unknown;
}

export interface MaplibreStyleSpec {
  version: 8;
  name: string;
  sources: Record<string, unknown>;
  layers: MaplibreLayerSpec[];
  glyphs?: string;
}

/**
 * Minimal OSM-free demo style (maplibre style spec v8). Background fill plus a
 * schematic Nigeria landmass from inline GeoJSON — zero network, zero glyphs.
 * In production GeoLibre serves a full style at VITE_MAP_STYLE_URL instead.
 */
export function buildDemoStyle(): MaplibreStyleSpec {
  return {
    version: 8,
    name: 'sos-demo-nigeria',
    sources: {
      'ng-land': {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [nigeriaLandFeature()] },
      },
    },
    layers: [
      {
        id: 'background',
        type: 'background',
        paint: { 'background-color': DEMO_COLORS.background },
      },
      {
        id: 'ng-land-fill',
        type: 'fill',
        source: 'ng-land',
        paint: { 'fill-color': DEMO_COLORS.land, 'fill-outline-color': DEMO_COLORS.landOutline },
      },
    ],
  };
}

export type MapMode = 'demo' | 'live';

export interface ResolvedMapStyle {
  /** Style URL (live) or inline style object (demo). */
  style: string | MaplibreStyleSpec;
  mode: MapMode;
}

/**
 * Resolve the base style. Live mode requires VITE_MAP_STYLE_URL (GeoLibre);
 * anything missing or malformed fails soft to the bundled demo style.
 */
export function resolveMapStyle(env: Record<string, unknown> = import.meta.env): ResolvedMapStyle {
  const url = typeof env.VITE_MAP_STYLE_URL === 'string' ? env.VITE_MAP_STYLE_URL.trim() : '';
  if (url && /^https?:\/\//.test(url)) return { style: url, mode: 'live' };
  return { style: buildDemoStyle(), mode: 'demo' };
}

/** WebGL probe — jsdom and old browsers fail soft to the schematic panel. */
export function isWebGLAvailable(doc: Document = document): boolean {
  try {
    const canvas = doc.createElement('canvas');
    return !!(canvas.getContext('webgl2') ?? canvas.getContext('webgl'));
  } catch {
    return false;
  }
}

/** Cesium 3D cadastre is opt-in via VITE_ENABLE_3D=true. */
export function is3DEnabled(env: Record<string, unknown> = import.meta.env): boolean {
  return env.VITE_ENABLE_3D === 'true' || env.VITE_ENABLE_3D === '1';
}

/** Same-origin URL for a bundled fixture (no external network in demo mode). */
export function fixtureUrl(path: string, env: Record<string, unknown> = import.meta.env): string {
  const base = typeof env.BASE_URL === 'string' ? env.BASE_URL : '/';
  return `${base}geo/${path}`;
}
