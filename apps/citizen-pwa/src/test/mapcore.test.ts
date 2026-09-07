import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import {
  NIGERIA_BOUNDS,
  STATE_BOUNDS,
  buildDemoStyle,
  fixtureUrl,
  isFeatureCollection,
  resolveMapStyle,
} from '../lib/mapcore';
import { STATES } from '../lib/types';

describe('buildDemoStyle', () => {
  it('produces a valid, fully offline maplibre style spec v8', () => {
    const style = buildDemoStyle();
    expect(style.version).toBe(8);
    const ids = style.layers.map((l) => l.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const layer of style.layers) {
      if (layer.type !== 'background') {
        expect(Object.keys(style.sources)).toContain(layer.source);
      }
    }
    for (const src of Object.values(style.sources) as { type: string; data?: unknown }[]) {
      expect(src.type).toBe('geojson');
      expect(isFeatureCollection(src.data)).toBe(true);
    }
  });

  it('matches the bundled public/geo/demo-style.json artifact', () => {
    const onDisk: unknown = JSON.parse(
      readFileSync(join(__dirname, '../../public/geo/demo-style.json'), 'utf8'),
    );
    expect(onDisk).toEqual(JSON.parse(JSON.stringify(buildDemoStyle())));
  });
});

describe('resolveMapStyle', () => {
  it('defaults to demo style without a URL (fail-soft)', () => {
    expect(resolveMapStyle({}).mode).toBe('demo');
    expect(resolveMapStyle({ VITE_MAP_STYLE_URL: 'nope' }).mode).toBe('demo');
  });

  it('uses the GeoLibre URL when configured', () => {
    const r = resolveMapStyle({ VITE_MAP_STYLE_URL: 'https://geo.example/styles/sos.json' });
    expect(r.mode).toBe('live');
    expect(r.style).toBe('https://geo.example/styles/sos.json');
  });
});

describe('isFeatureCollection / fixtureUrl', () => {
  it('validates structure', () => {
    expect(isFeatureCollection({ type: 'FeatureCollection', features: [] })).toBe(true);
    expect(isFeatureCollection({ features: [] })).toBe(false);
    expect(isFeatureCollection(undefined)).toBe(false);
  });

  it('builds same-origin fixture URLs', () => {
    expect(fixtureUrl('x.geojson', { BASE_URL: '/' })).toBe('/geo/x.geojson');
  });
});

describe('bundled GeoJSON fixtures', () => {
  const geoDir = join(__dirname, '../../public/geo');
  const files = readdirSync(geoDir).filter((f) => f.endsWith('.geojson'));

  it('ships parcel + LGA project fixtures for every state', () => {
    for (const { id } of STATES) {
      expect(files).toContain(`parcels-${id}.geojson`);
      expect(files).toContain(`lga-projects-${id}.geojson`);
    }
  });

  it.each(files)('%s is valid GeoJSON within Nigeria bounds', (file: string) => {
    const fc: any = JSON.parse(readFileSync(join(geoDir, file), 'utf8'));
    expect(isFeatureCollection(fc)).toBe(true);
    expect(fc.features.length).toBeGreaterThan(0);
    for (const f of fc.features) {
      expect(f.geometry.type).toBe('Polygon');
      const ring = f.geometry.coordinates[0];
      expect(ring[0]).toEqual(ring[ring.length - 1]); // closed ring
      for (const [lon, lat] of ring) {
        expect(lon).toBeGreaterThanOrEqual(NIGERIA_BOUNDS[0][0]);
        expect(lon).toBeLessThanOrEqual(NIGERIA_BOUNDS[1][0]);
        expect(lat).toBeGreaterThanOrEqual(NIGERIA_BOUNDS[0][1]);
        expect(lat).toBeLessThanOrEqual(NIGERIA_BOUNDS[1][1]);
      }
    }
  });

  it('LGA project fixtures carry choropleth attributes', () => {
    for (const { id } of STATES) {
      const fc: any = JSON.parse(readFileSync(join(geoDir, `lga-projects-${id}.geojson`), 'utf8'));
      expect(fc.features).toHaveLength(3);
      for (const f of fc.features) {
        expect(f.properties.project_count).toBeGreaterThan(0);
        expect(f.properties.total_value_kobo).toBeGreaterThan(0);
        expect(typeof f.properties.lga_name).toBe('string');
      }
      // fixtures sit inside the state geofence used for camera framing
      const [minLat, minLon, maxLat, maxLon] = STATE_BOUNDS[id];
      const [lon, lat] = fc.features[0].geometry.coordinates[0][0];
      expect(lon).toBeGreaterThanOrEqual(minLon);
      expect(lon).toBeLessThanOrEqual(maxLon);
      expect(lat).toBeGreaterThanOrEqual(minLat);
      expect(lat).toBeLessThanOrEqual(maxLat);
    }
  });
});
