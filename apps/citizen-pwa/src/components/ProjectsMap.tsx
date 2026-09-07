// Transparency "projects by LGA" choropleth-lite — bundled per-state GeoJSON
// fixtures coloured by project_count, with click-to-inspect popups. Lazy
// maplibre chunk; fail-soft note when WebGL is unavailable.

import { useEffect, useRef, useState } from 'react';

import { formatNaira } from '../lib/format';
import { fixtureUrl, isFeatureCollection } from '../lib/mapcore';
import { initBaseMap } from './mapBase';
import type { StateId } from '../lib/types';

export function ProjectsMap({ stateId }: { stateId: StateId }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState(false);
  const [mode, setMode] = useState<'demo' | 'live'>('demo');

  useEffect(() => {
    if (!containerRef.current) return;
    let disposed = false;
    let cleanup: (() => void) | undefined;

    void (async () => {
      try {
        const { map, maplibregl, mode: m } = await initBaseMap(containerRef.current!);
        if (disposed) return;
        setMode(m);
        const res = await fetch(fixtureUrl(`lga-projects-${stateId}.geojson`));
        if (!res.ok) throw new Error(`fixtures ${res.status}`);
        const fc: unknown = await res.json();
        if (!isFeatureCollection(fc)) throw new Error('invalid fixture GeoJSON');
        if (disposed) return;

        const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: true });
        map.on('load', () => {
          if (disposed) return;
          map.addSource('lga-projects', { type: 'geojson', data: fc });
          map.addLayer({
            id: 'lga-projects-fill',
            type: 'fill',
            source: 'lga-projects',
            paint: {
              // Choropleth-lite: project_count buckets.
              'fill-color': [
                'step',
                ['get', 'project_count'],
                '#e8dcc8',
                5, '#c99e6e',
                9, '#8c5230',
              ],
              'fill-opacity': 0.75,
            },
          });
          map.addLayer({
            id: 'lga-projects-outline',
            type: 'line',
            source: 'lga-projects',
            paint: { 'line-color': '#5c3520', 'line-width': 1 },
          });
          map.on('click', 'lga-projects-fill', (e: { features?: { properties?: Record<string, unknown> }[]; lngLat: unknown }) => {
            const p = e.features?.[0]?.properties;
            if (!p) return;
            popup
              .setLngLat(e.lngLat as never)
              .setHTML(
                `<div class="map-popup"><strong>${p.lga_name}</strong><br/>` +
                  `${p.project_count} published projects<br/>${formatNaira(Number(p.total_value_kobo))}</div>`,
              )
              .addTo(map);
          });
        });
        cleanup = () => {
          popup.remove();
          map.remove();
        };
      } catch {
        if (!disposed) setError(true);
      }
    })();

    return () => {
      disposed = true;
      cleanup?.();
    };
  }, [stateId]);

  if (error) {
    return (
      <p className="small muted" role="note">
        Map view unavailable on this device — the tabular feeds above carry the same data.
      </p>
    );
  }

  return (
    <div>
      <div
        ref={containerRef}
        data-testid="projects-map"
        style={{ width: '100%', height: 300 }}
        aria-label={`projects by LGA map for ${stateId}`}
      />
      <p className="small muted">
        {mode === 'demo'
          ? 'Demo fixture data (bundled, no network). Live styles served by GeoLibre in production.'
          : 'Live GeoLibre style.'}
      </p>
    </div>
  );
}
