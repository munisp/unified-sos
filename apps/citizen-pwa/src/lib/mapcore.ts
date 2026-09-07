// Dependency-free mapping core for the citizen PWA: demo map style, GeoJSON
// plumbing and environment gating. Demo mode requires NO network: the base
// style is built inline and fixture GeoJSON ships under public/geo/. Live mode
// points at the self-hosted GeoLibre style endpoint via VITE_MAP_STYLE_URL.
// maplibre-gl itself is only ever loaded via dynamic import() (map chunk).

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

/** Schematic Nigeria outline (lon/lat pairs). */
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

// Approximate state geofence boxes (minLat, minLon, maxLat, maxLon) — mirrors
// STATE_GEOFENCES in services/mod-police-cad for camera framing only.
export const STATE_BOUNDS: Record<string, [number, number, number, number]> = {
  lagos: [6.35, 2.65, 6.75, 4.35],
  ogun: [6.3, 2.65, 7.35, 4.6],
  osun: [7.05, 4.0, 8.1, 5.1],
  benue: [6.35, 7.5, 8.2, 10.0],
  nasarawa: [7.45, 7.1, 9.3, 9.6],
  taraba: [6.4, 9.4, 9.6, 11.6],
};

export interface MaplibreLayerSpec {
  id: string;
  type: string;
  source?: string;
  paint?: Record<string, unknown>;
  layout?: Record<string, unknown>;
  filter?: unknown[];
  [key: string]: unknown;
}

export interface MaplibreStyleSpec {
  version: 8;
  name: string;
  sources: Record<string, unknown>;
  layers: MaplibreLayerSpec[];
}

/**
 * Minimal OSM-free demo style (maplibre style spec v8). Background fill plus a
 * schematic Nigeria landmass from inline GeoJSON — zero network, zero glyphs.
 * Mirrored by public/geo/demo-style.json; in production GeoLibre serves a full
 * style at VITE_MAP_STYLE_URL instead.
 */
export function buildDemoStyle(): MaplibreStyleSpec {
  return {
    version: 8,
    name: 'sos-demo-nigeria',
    sources: {
      'ng-land': {
        type: 'geojson',
        data: {
          type: 'FeatureCollection',
          features: [
            {
              type: 'Feature',
              geometry: { type: 'Polygon', coordinates: [NIGERIA_OUTLINE] },
              properties: { name: 'Nigeria (schematic)' },
            },
          ],
        },
      },
    },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': '#faf6ef' } },
      {
        id: 'ng-land-fill',
        type: 'fill',
        source: 'ng-land',
        paint: { 'fill-color': '#f3ece1', 'fill-outline-color': '#e3d9c9' },
      },
    ],
  };
}

export type MapMode = 'demo' | 'live';

export interface ResolvedMapStyle {
  style: string | MaplibreStyleSpec;
  mode: MapMode;
}

/** Live mode requires VITE_MAP_STYLE_URL (GeoLibre); otherwise demo style. */
export function resolveMapStyle(env: Record<string, unknown> = import.meta.env): ResolvedMapStyle {
  const url = typeof env.VITE_MAP_STYLE_URL === 'string' ? env.VITE_MAP_STYLE_URL.trim() : '';
  if (url && /^https?:\/\//.test(url)) return { style: url, mode: 'live' };
  return { style: buildDemoStyle(), mode: 'demo' };
}

/** WebGL probe — jsdom and old browsers skip map mounting entirely. */
export function isWebGLAvailable(doc: Document = document): boolean {
  try {
    const canvas = doc.createElement('canvas');
    return !!(canvas.getContext('webgl2') ?? canvas.getContext('webgl'));
  } catch {
    return false;
  }
}

/** Same-origin URL for a bundled fixture (no external network in demo mode). */
export function fixtureUrl(path: string, env: Record<string, unknown> = import.meta.env): string {
  const base = typeof env.BASE_URL === 'string' ? env.BASE_URL : '/';
  return `${base}geo/${path}`;
}
