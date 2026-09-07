// MapLibre layer/source builders for the dispatch console map.
// Pure functions — unit-tested without maplibre-gl or WebGL.

import { STATE_GEOFENCES } from './fixtures';
import type { GeoJsonFeature, GeoJsonFeatureCollection, MaplibreLayerSpec } from './mapcore';
import { DEMO_COLORS } from './mapcore';
import type { Incident, StateId, Unit } from './types';

export const SOURCE_INCIDENTS = 'sos-incidents';
export const SOURCE_UNITS = 'sos-units';
export const SOURCE_GEOFENCES = 'sos-geofences';

const PRIORITY_COLOR: Record<string, string> = {
  P1: '#9c3d2b',
  P2: '#8c5230',
  P3: '#6f6154',
};

/** Incidents as a GeoJSON FeatureCollection (live store data, not fixtures). */
export function incidentsToGeoJson(incidents: Incident[]): GeoJsonFeatureCollection {
  return {
    type: 'FeatureCollection',
    features: incidents
      .filter((i) => i.status !== 'closed')
      .map((inc): GeoJsonFeature => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [inc.longitude, inc.latitude] },
        properties: {
          id: inc.incident_id,
          category: inc.category,
          priority: inc.priority,
          status: inc.status,
          agency: inc.agency,
        },
      })),
  };
}

export function unitsToGeoJson(units: Unit[]): GeoJsonFeatureCollection {
  return {
    type: 'FeatureCollection',
    features: units.map((u): GeoJsonFeature => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [u.longitude, u.latitude] },
      properties: {
        id: u.unit_id,
        call_sign: u.call_sign,
        status: u.status,
        agency: u.agency,
        enrolled: u.biometric_enrolled,
      },
    })),
  };
}

/** State geofence bounding boxes as polygon features (all states). */
export function geofencesToGeoJson(): GeoJsonFeatureCollection {
  const features = (Object.entries(STATE_GEOFENCES) as [StateId, [number, number, number, number]][]).map(
    ([stateId, [minLat, minLon, maxLat, maxLon]]): GeoJsonFeature => ({
      type: 'Feature',
      geometry: {
        type: 'Polygon',
        coordinates: [[
          [minLon, minLat],
          [maxLon, minLat],
          [maxLon, maxLat],
          [minLon, maxLat],
          [minLon, minLat],
        ]],
      },
      properties: { state_id: stateId },
    }),
  );
  return { type: 'FeatureCollection', features };
}

/**
 * Incident cluster layer stack: clustered circles + count labels + unclustered
 * priority-coloured points. Requires a source with `cluster: true`.
 */
export function incidentClusterLayers(source: string = SOURCE_INCIDENTS): MaplibreLayerSpec[] {
  return [
    {
      id: 'incident-clusters',
      type: 'circle',
      source,
      filter: ['has', 'point_count'],
      paint: {
        'circle-color': '#8c5230',
        'circle-radius': ['step', ['get', 'point_count'], 14, 5, 18, 15, 24],
        'circle-stroke-width': 2,
        'circle-stroke-color': '#ffffff',
      },
    },
    {
      id: 'incident-cluster-count',
      type: 'symbol',
      source,
      filter: ['has', 'point_count'],
      layout: {
        'text-field': '{point_count_abbreviated}',
        'text-size': 12,
      },
      paint: { 'text-color': '#ffffff' },
    },
    {
      id: 'incident-points',
      type: 'circle',
      source,
      filter: ['!', ['has', 'point_count']],
      paint: {
        'circle-color': [
          'match',
          ['get', 'priority'],
          'P1', PRIORITY_COLOR.P1,
          'P2', PRIORITY_COLOR.P2,
          PRIORITY_COLOR.P3,
        ],
        'circle-radius': ['case', ['==', ['get', 'priority'], 'P1'], 8, 6],
        'circle-stroke-width': 1.5,
        'circle-stroke-color': '#ffffff',
      },
    },
  ];
}

export function unitLayers(source: string = SOURCE_UNITS): MaplibreLayerSpec[] {
  return [
    {
      id: 'unit-points',
      type: 'circle',
      source,
      paint: {
        'circle-color': [
          'match',
          ['get', 'status'],
          'available', '#3f6b4a',
          'enroute', '#8c5230',
          '#6f6154',
        ],
        'circle-radius': 5,
        'circle-stroke-width': 1,
        'circle-stroke-color': '#ffffff',
      },
    },
  ];
}

export function geofenceLayers(
  source: string = SOURCE_GEOFENCES,
  activeState?: StateId,
): MaplibreLayerSpec[] {
  return [
    {
      id: 'geofence-fill',
      type: 'fill',
      source,
      paint: { 'fill-color': DEMO_COLORS.geofenceFill },
    },
    {
      id: 'geofence-outline',
      type: 'line',
      source,
      paint: {
        'line-color': DEMO_COLORS.geofence,
        'line-dasharray': [3, 2],
        'line-width': ['case', ['==', ['get', 'state_id'], activeState ?? ''], 2.5, 1],
      },
    },
  ];
}

/** GeoJSON source definitions (maplibre `addSource` payloads). */
export function mapSources(
  incidents: Incident[],
  units: Unit[],
): Record<string, Record<string, unknown>> {
  return {
    [SOURCE_INCIDENTS]: {
      type: 'geojson',
      data: incidentsToGeoJson(incidents),
      cluster: true,
      clusterMaxZoom: 12,
      clusterRadius: 40,
    },
    [SOURCE_UNITS]: { type: 'geojson', data: unitsToGeoJson(units) },
    [SOURCE_GEOFENCES]: { type: 'geojson', data: geofencesToGeoJson() },
  };
}
