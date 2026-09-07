// Real mapping engine: MapLibre GL panel with clustered incident markers,
// unit markers, state geofence polygons and click-to-inspect popups.
// maplibre-gl is loaded via dynamic import() so the main bundle stays lean
// and jsdom tests never touch WebGL. Demo mode uses the inline bundled style
// (no network); live mode uses VITE_MAP_STYLE_URL (self-hosted GeoLibre).

import { useEffect, useRef } from 'react';

import { mapSources, geofenceLayers, incidentClusterLayers, unitLayers } from '../lib/mapLayers';
import {
  NIGERIA_BOUNDS,
  isWebGLAvailable,
  resolveMapStyle,
} from '../lib/mapcore';
import { useConsole } from '../lib/store';
import { STATE_GEOFENCES } from '../lib/fixtures';

export function MapLibrePanel({ onError }: { onError: (err: unknown) => void }) {
  const { stateId, incidents, units } = useConsole();
  const containerRef = useRef<HTMLDivElement | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mapRef = useRef<any>(null);

  // Create/destroy the map once per mount.
  useEffect(() => {
    if (!containerRef.current || !isWebGLAvailable()) {
      onError(new Error('WebGL unavailable — falling back to schematic map'));
      return;
    }
    let disposed = false;
    let cleanup: (() => void) | undefined;

    void (async () => {
      try {
        const maplibregl = await import('maplibre-gl');
        await import('maplibre-gl/dist/maplibre-gl.css');
        if (disposed || !containerRef.current) return;

        const { style, mode } = resolveMapStyle();
        const map = new maplibregl.Map({
          container: containerRef.current,
          style: style as never,
          bounds: NIGERIA_BOUNDS,
          fitBoundsOptions: { padding: 24 },
          attributionControl: false,
        });
        mapRef.current = map;
        map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');

        const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: true });

        map.on('load', () => {
          if (disposed) return;
          const sources = mapSources(incidents, units);
          for (const [id, def] of Object.entries(sources)) map.addSource(id, def as never);
          for (const layer of [
            ...geofenceLayers(undefined, stateId),
            ...incidentClusterLayers(),
            ...unitLayers(),
          ]) {
            map.addLayer(layer as never);
          }

          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const inspect = (e: any) => {
            const f = e.features?.[0];
            if (!f?.properties) return;
            const p = f.properties;
            const rows = Object.entries(p)
              .map(([k, v]) => `<div><strong>${k}</strong>: ${String(v)}</div>`)
              .join('');
            popup.setLngLat(e.lngLat).setHTML(`<div class="map-popup">${rows}</div>`).addTo(map);
          };
          for (const layerId of ['incident-points', 'unit-points', 'geofence-fill']) {
            map.on('click', layerId, inspect as never);
            map.on('mouseenter', layerId, () => {
              map.getCanvas().style.cursor = 'pointer';
            });
            map.on('mouseleave', layerId, () => {
              map.getCanvas().style.cursor = '';
            });
          }
          // Zoom into a cluster on click.
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          map.on('click', 'incident-clusters', (e: any) => {
            const f = e.features?.[0];
            if (!f?.properties?.cluster_id) return;
            const src = map.getSource('sos-incidents') as unknown as {
              getClusterExpansionZoom: (id: number, cb: (err: unknown, zoom: number) => void) => void;
            };
            src.getClusterExpansionZoom(f.properties.cluster_id, (_err, zoom) => {
              map.easeTo({ center: f.geometry.coordinates, zoom });
            });
          });
        });

        map.on('error', (e: { error?: unknown }) => {
          // Live style/tiles unreachable → fail soft to schematic panel.
          if (mode === 'live') onError(e.error ?? new Error('map error'));
        });

        cleanup = () => {
          popup.remove();
          map.remove();
          mapRef.current = null;
        };
      } catch (err) {
        if (!disposed) onError(err);
      }
    })();

    return () => {
      disposed = true;
      cleanup?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep GeoJSON sources in sync with the store (demo + live).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded?.()) return;
    const incSrc = map.getSource?.('sos-incidents');
    const unitSrc = map.getSource?.('sos-units');
    incSrc?.setData?.(mapSources(incidents, units)['sos-incidents'].data);
    unitSrc?.setData?.(mapSources(incidents, units)['sos-units'].data);
  }, [incidents, units]);

  // Refit when the tenant state changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map?.fitBounds) return;
    const [minLat, minLon, maxLat, maxLon] = STATE_GEOFENCES[stateId];
    map.fitBounds(
      [
        [minLon, minLat],
        [maxLon, maxLat],
      ],
      { padding: 32 },
    );
  }, [stateId]);

  const { mode } = resolveMapStyle();
  return (
    <div>
      <div
        ref={containerRef}
        data-testid="maplibre-container"
        className="map maplibre-map"
        style={{ width: '100%', height: 360 }}
        aria-label={`interactive map of ${stateId}`}
      />
      <p className="legend muted">
        {mode === 'demo'
          ? 'Demo base map (bundled, no network). Live tiles served by GeoLibre via VITE_MAP_STYLE_URL.'
          : 'Live style: GeoLibre (VITE_MAP_STYLE_URL)'}
      </p>
    </div>
  );
}
