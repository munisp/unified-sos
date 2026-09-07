import { describe, expect, it } from 'vitest';

import { fixtureIncidents, fixtureUnits } from '../lib/fixtures';
import {
  SOURCE_GEOFENCES,
  SOURCE_INCIDENTS,
  SOURCE_UNITS,
  geofenceLayers,
  geofencesToGeoJson,
  incidentClusterLayers,
  incidentsToGeoJson,
  mapSources,
  unitLayers,
  unitsToGeoJson,
} from '../lib/mapLayers';
import { isFeatureCollection } from '../lib/mapcore';
import { STATES } from '../lib/types';

const VALID_LAYER_TYPES = ['background', 'fill', 'line', 'symbol', 'circle', 'fill-extrusion'];

function assertValidLayers(layers: { id: string; type: string; source?: string }[]) {
  const ids = layers.map((l) => l.id);
  expect(new Set(ids).size).toBe(ids.length); // unique ids
  for (const l of layers) {
    expect(VALID_LAYER_TYPES).toContain(l.type);
    expect(l.source).toBeTruthy();
  }
}

describe('incidentsToGeoJson', () => {
  it('converts open incidents to point features, excluding closed', () => {
    const incidents = fixtureIncidents('lagos');
    const fc = incidentsToGeoJson([...incidents, { ...incidents[0], status: 'closed' as const }]);
    expect(isFeatureCollection(fc)).toBe(true);
    expect(fc.features).toHaveLength(incidents.length); // closed one dropped
    for (const f of fc.features) {
      expect(f.geometry.type).toBe('Point');
      const [lon, lat] = f.geometry.coordinates as [number, number];
      expect(lon).toBeGreaterThan(2);
      expect(lat).toBeGreaterThan(4);
      expect(f.properties?.id).toMatch(/^INC-/);
    }
  });
});

describe('unitsToGeoJson + geofencesToGeoJson', () => {
  it('units become point features', () => {
    const fc = unitsToGeoJson(fixtureUnits('ogun'));
    expect(isFeatureCollection(fc)).toBe(true);
    expect(fc.features).toHaveLength(6);
  });

  it('every state geofence becomes a closed polygon', () => {
    const fc = geofencesToGeoJson();
    expect(fc.features).toHaveLength(STATES.length);
    for (const f of fc.features) {
      const ring = (f.geometry.coordinates as number[][][])[0];
      expect(ring[0]).toEqual(ring[ring.length - 1]);
      expect(STATES.map((s) => s.id)).toContain(f.properties?.state_id);
    }
  });
});

describe('layer spec builders', () => {
  it('incident cluster stack is valid maplibre layer specs', () => {
    const layers = incidentClusterLayers();
    assertValidLayers(layers);
    expect(layers.map((l) => l.id)).toEqual([
      'incident-clusters',
      'incident-cluster-count',
      'incident-points',
    ]);
    expect(layers.every((l) => l.source === SOURCE_INCIDENTS)).toBe(true);
  });

  it('unit + geofence layers reference their sources', () => {
    assertValidLayers(unitLayers());
    assertValidLayers(geofenceLayers(undefined, 'lagos'));
    expect(unitLayers()[0].source).toBe(SOURCE_UNITS);
    expect(geofenceLayers().every((l) => l.source === SOURCE_GEOFENCES)).toBe(true);
  });
});

describe('mapSources', () => {
  it('incidents source has clustering enabled', () => {
    const srcs = mapSources(fixtureIncidents('benue'), fixtureUnits('benue'));
    expect(srcs[SOURCE_INCIDENTS].cluster).toBe(true);
    expect(srcs[SOURCE_INCIDENTS].type).toBe('geojson');
    expect(isFeatureCollection(srcs[SOURCE_INCIDENTS].data)).toBe(true);
    expect(isFeatureCollection(srcs[SOURCE_UNITS].data)).toBe(true);
    expect(isFeatureCollection(srcs[SOURCE_GEOFENCES].data)).toBe(true);
  });
});
