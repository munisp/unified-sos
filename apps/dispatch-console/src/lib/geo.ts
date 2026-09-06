// Dependency-light equirectangular projection of lat/lon onto an SVG viewport
// plus a schematic Nigeria outline (no external tile dependency).

import type { StateId } from './types';
import { STATE_GEOFENCES } from './fixtures';

export interface ViewBox {
  width: number;
  height: number;
  pad: number;
}

/** Project [lon, lat] into svg x/y within the state geofence. */
export function project(
  stateId: StateId,
  lon: number,
  lat: number,
  view: ViewBox,
): { x: number; y: number } {
  const [minLat, minLon, maxLat, maxLon] = STATE_GEOFENCES[stateId];
  const innerW = view.width - view.pad * 2;
  const innerH = view.height - view.pad * 2;
  const x = view.pad + ((lon - minLon) / (maxLon - minLon)) * innerW;
  // SVG y grows downward; latitude grows upward.
  const y = view.pad + ((maxLat - lat) / (maxLat - minLat)) * innerH;
  return { x, y };
}

/** Schematic Nigeria outline (simplified polygon, lon/lat pairs). */
export const NIGERIA_OUTLINE: [number, number][] = [
  [2.7, 6.4], [3.6, 6.45], [4.3, 6.1], [5.6, 5.6], [6.1, 4.6], [7.0, 4.4],
  [8.5, 4.5], [8.5, 5.0], [9.4, 5.3], [9.7, 6.1], [10.4, 6.9], [11.6, 6.7],
  [12.5, 7.4], [13.6, 8.0], [14.6, 9.3], [14.8, 10.8], [14.0, 12.4],
  [13.6, 13.7], [12.0, 13.6], [10.0, 13.3], [7.8, 13.3], [5.8, 13.5],
  [4.4, 13.8], [3.6, 12.4], [3.5, 11.7], [2.8, 11.6], [0.9, 11.2],
  [0.9, 10.0], [1.2, 8.9], [1.6, 7.4], [2.7, 6.4],
];

/** Nigeria outline projected onto the full-country extent (national context). */
export const NIGERIA_EXTENT: [number, number, number, number] = [4.0, 2.5, 14.2, 15.0]; // minLat, minLon, maxLat, maxLon

export function projectNational(
  lon: number,
  lat: number,
  view: ViewBox,
): { x: number; y: number } {
  const [minLat, minLon, maxLat, maxLon] = NIGERIA_EXTENT;
  const innerW = view.width - view.pad * 2;
  const innerH = view.height - view.pad * 2;
  return {
    x: view.pad + ((lon - minLon) / (maxLon - minLon)) * innerW,
    y: view.pad + ((maxLat - lat) / (maxLat - minLat)) * innerH,
  };
}
