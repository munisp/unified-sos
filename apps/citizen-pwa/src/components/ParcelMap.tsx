// Parcel boundary preview for VerifyDeed — renders the state cadastre parcel
// fixtures (bundled GeoJSON, no external network in demo mode) with the
// matching parcel highlighted. Loaded lazily; fails soft to a plain note.

import { useEffect, useRef, useState } from 'react';

import { fixtureUrl, isFeatureCollection, type GeoJsonFeature } from '../lib/mapcore';
import { initBaseMap, fitFeature } from './mapBase';
import type { StateId } from '../lib/types';

export function ParcelMap({ stateId, parcelUin }: { stateId: StateId; parcelUin?: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!containerRef.current) return;
    let disposed = false;
    let cleanup: (() => void) | undefined;

    void (async () => {
      try {
        const { map } = await initBaseMap(containerRef.current!);
        const res = await fetch(fixtureUrl(`parcels-${stateId}.geojson`));
        if (!res.ok) throw new Error(`fixtures ${res.status}`);
        const fc: unknown = await res.json();
        if (!isFeatureCollection(fc)) throw new Error('invalid fixture GeoJSON');
        if (disposed) return;

        const target =
          (parcelUin &&
            fc.features.find(
              (f) => f.properties?.parcel_uin === parcelUin || f.properties?.cofo_number === parcelUin,
            )) ||
          fc.features[0];

        map.on('load', () => {
          if (disposed) return;
          map.addSource('parcels', { type: 'geojson', data: fc });
          map.addLayer({
            id: 'parcel-fill',
            type: 'fill',
            source: 'parcels',
            paint: {
              'fill-color': [
                'match',
                ['get', 'cofo_status'],
                'VERIFIED', '#3f6b4a',
                'DISPUTED', '#9c3d2b',
                '#8c5230',
              ],
              'fill-opacity': 0.35,
            },
          });
          map.addLayer({
            id: 'parcel-outline',
            type: 'line',
            source: 'parcels',
            paint: { 'line-color': '#5c3520', 'line-width': 1.5 },
          });
          if (target) {
            map.addSource('parcel-target', {
              type: 'geojson',
              data: { type: 'FeatureCollection', features: [target as GeoJsonFeature] },
            });
            map.addLayer({
              id: 'parcel-target-outline',
              type: 'line',
              source: 'parcel-target',
              paint: { 'line-color': '#9c3d2b', 'line-width': 3 },
            });
            fitFeature(map, target.geometry.coordinates as number[][][]);
          }
        });
        cleanup = () => map.remove();
      } catch {
        if (!disposed) setError(true);
      }
    })();

    return () => {
      disposed = true;
      cleanup?.();
    };
  }, [stateId, parcelUin]);

  if (error) {
    return (
      <p className="small muted" role="note">
        Map preview unavailable on this device — parcel geometry is held by the state Lands Bureau.
      </p>
    );
  }

  return (
    <div
      ref={containerRef}
      data-testid="parcel-map"
      style={{ width: '100%', height: 260 }}
      aria-label={`parcel boundary preview for ${stateId}`}
    />
  );
}
