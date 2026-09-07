// Lazy maplibre-gl loader shared by the citizen map components. Dynamic
// import() keeps maplibre out of the main bundle (separate async chunk).

import {
  NIGERIA_BOUNDS,
  isWebGLAvailable,
  resolveMapStyle,
} from '../lib/mapcore';

export interface BaseMap {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  map: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  maplibregl: any;
  mode: 'demo' | 'live';
}

/** Create a base map; throws when WebGL/maplibre is unavailable (fail-soft). */
export async function initBaseMap(container: HTMLElement): Promise<BaseMap> {
  if (!isWebGLAvailable()) throw new Error('WebGL unavailable');
  const maplibregl = await import('maplibre-gl');
  await import('maplibre-gl/dist/maplibre-gl.css');
  const { style, mode } = resolveMapStyle();
  const map = new maplibregl.Map({
    container,
    style: style as never,
    bounds: NIGERIA_BOUNDS,
    fitBoundsOptions: { padding: 16 },
    attributionControl: false,
  });
  return { map, maplibregl, mode };
}

/** Fit the map to a polygon feature's bbox. */
export function fitFeature(map: { fitBounds: (b: unknown, o?: unknown) => void }, coords: number[][][]): void {
  let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
  for (const ring of coords) {
    for (const [lon, lat] of ring) {
      if (lon < minLon) minLon = lon;
      if (lat < minLat) minLat = lat;
      if (lon > maxLon) maxLon = lon;
      if (lat > maxLat) maxLat = lat;
    }
  }
  if (Number.isFinite(minLon)) {
    map.fitBounds(
      [
        [minLon, minLat],
        [maxLon, maxLat],
      ],
      { padding: 40 },
    );
  }
}
