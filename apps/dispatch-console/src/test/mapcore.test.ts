import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import {
  NIGERIA_BOUNDS,
  buildDemoStyle,
  fixtureUrl,
  is3DEnabled,
  isFeatureCollection,
  resolveMapStyle,
} from '../lib/mapcore';

describe('buildDemoStyle', () => {
  it('produces a valid maplibre style spec v8', () => {
    const style = buildDemoStyle();
    expect(style.version).toBe(8);
    expect(style.layers.length).toBeGreaterThanOrEqual(2);
    const ids = style.layers.map((l) => l.id);
    expect(new Set(ids).size).toBe(ids.length);
    // Every non-background layer references a declared source.
    for (const layer of style.layers) {
      if (layer.type !== 'background') {
        expect(Object.keys(style.sources)).toContain(layer.source);
      }
    }
    // No external network: all sources are inline geojson, no glyphs/tiles.
    for (const src of Object.values(style.sources) as { type: string; data?: unknown }[]) {
      expect(src.type).toBe('geojson');
      expect(isFeatureCollection(src.data)).toBe(true);
    }
    expect(style.glyphs).toBeUndefined();
  });

  it('matches the bundled public/geo/demo-style.json artifact', () => {
    const onDisk: unknown = JSON.parse(
      readFileSync(join(__dirname, '../../public/geo/demo-style.json'), 'utf8'),
    );
    expect(onDisk).toEqual(JSON.parse(JSON.stringify(buildDemoStyle())));
  });
});

describe('resolveMapStyle', () => {
  it('falls back to the demo style when no URL is configured', () => {
    const r = resolveMapStyle({});
    expect(r.mode).toBe('demo');
    expect(typeof r.style).toBe('object');
  });

  it('uses the GeoLibre style URL in live mode', () => {
    const r = resolveMapStyle({ VITE_MAP_STYLE_URL: 'http://localhost:8085/styles/sos.json' });
    expect(r.mode).toBe('live');
    expect(r.style).toBe('http://localhost:8085/styles/sos.json');
  });

  it('rejects malformed URLs fail-soft', () => {
    expect(resolveMapStyle({ VITE_MAP_STYLE_URL: 'not-a-url' }).mode).toBe('demo');
    expect(resolveMapStyle({ VITE_MAP_STYLE_URL: '  ' }).mode).toBe('demo');
  });
});

describe('is3DEnabled', () => {
  it('is off by default and on for true/1', () => {
    expect(is3DEnabled({})).toBe(false);
    expect(is3DEnabled({ VITE_ENABLE_3D: 'false' })).toBe(false);
    expect(is3DEnabled({ VITE_ENABLE_3D: 'true' })).toBe(true);
    expect(is3DEnabled({ VITE_ENABLE_3D: '1' })).toBe(true);
  });
});

describe('isFeatureCollection', () => {
  it('accepts valid and rejects broken collections', () => {
    expect(
      isFeatureCollection({
        type: 'FeatureCollection',
        features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: [3, 6] } }],
      }),
    ).toBe(true);
    expect(isFeatureCollection({ type: 'FeatureCollection', features: [{ type: 'Feature' }] })).toBe(false);
    expect(isFeatureCollection(null)).toBe(false);
    expect(isFeatureCollection({ type: 'Feature' })).toBe(false);
  });
});

describe('fixtureUrl', () => {
  it('builds same-origin URLs', () => {
    expect(fixtureUrl('parcels-lagos.geojson', { BASE_URL: '/' })).toBe('/geo/parcels-lagos.geojson');
  });
});

describe('bundled GeoJSON fixtures', () => {
  const geoDir = join(__dirname, '../../public/geo');
  const files = readdirSync(geoDir).filter((f) => f.endsWith('.geojson'));

  it('ships per-state parcel fixtures', () => {
    expect(files.sort()).toEqual([
      'parcels-benue.geojson',
      'parcels-lagos.geojson',
      'parcels-nasarawa.geojson',
      'parcels-ogun.geojson',
      'parcels-osun.geojson',
      'parcels-taraba.geojson',
    ]);
  });

  it.each(files)('%s is a valid FeatureCollection of extrudable parcels', (file: string) => {
    const fc: any = JSON.parse(readFileSync(join(geoDir, file), 'utf8'));
    expect(isFeatureCollection(fc)).toBe(true);
    expect(fc.features.length).toBeGreaterThan(0);
    for (const f of fc.features) {
      expect(f.geometry.type).toBe('Polygon');
      const ring = f.geometry.coordinates[0];
      expect(ring.length).toBeGreaterThanOrEqual(4);
      expect(ring[0]).toEqual(ring[ring.length - 1]); // closed ring
      for (const [lon, lat] of ring) {
        expect(lon).toBeGreaterThanOrEqual(NIGERIA_BOUNDS[0][0]);
        expect(lon).toBeLessThanOrEqual(NIGERIA_BOUNDS[1][0]);
        expect(lat).toBeGreaterThanOrEqual(NIGERIA_BOUNDS[0][1]);
        expect(lat).toBeLessThanOrEqual(NIGERIA_BOUNDS[1][1]);
      }
      expect(typeof f.properties.parcel_uin).toBe('string');
      expect(typeof f.properties.luc_band).toBe('string');
      expect(['VERIFIED', 'PENDING', 'DISPUTED']).toContain(f.properties.cofo_status);
      expect(f.properties.height_m).toBeGreaterThan(0);
    }
  });
});
