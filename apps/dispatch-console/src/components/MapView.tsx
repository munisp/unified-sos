// Map view switcher: real MapLibre GL engine by default, with the original
// dependency-free SVG schematic kept as a fallback (manual toggle or automatic
// fail-soft when WebGL/maplibre is unavailable, e.g. jsdom or old browsers).

import { Suspense, lazy, useState } from 'react';

import { MapPanel } from './MapPanel';

const MapLibrePanel = lazy(() =>
  import('./MapLibrePanel').then((m) => ({ default: m.MapLibrePanel })),
);

type View = 'maplibre' | 'schematic';

export function MapView() {
  const [view, setView] = useState<View>('maplibre');
  const [failed, setFailed] = useState(false);

  const showSchematic = view === 'schematic' || failed;

  return (
    <section className="panel" aria-label="Map">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h2>Map</h2>
        <div role="group" aria-label="Map renderer">
          <button
            type="button"
            aria-pressed={!showSchematic}
            className={!showSchematic ? 'btn-primary' : ''}
            onClick={() => {
              setFailed(false);
              setView('maplibre');
            }}
          >
            Live map
          </button>{' '}
          <button
            type="button"
            aria-pressed={showSchematic}
            className={showSchematic ? 'btn-primary' : ''}
            onClick={() => setView('schematic')}
          >
            Schematic
          </button>
        </div>
      </div>
      {showSchematic ? (
        <>
          {failed && view === 'maplibre' && (
            <p className="notice notice-bad" role="alert">
              Interactive map unavailable (WebGL/tiles) — showing schematic fallback.
            </p>
          )}
          <MapPanel />
        </>
      ) : (
        <Suspense fallback={<p className="muted">Loading map engine…</p>}>
          <MapLibrePanel onError={() => setFailed(true)} />
        </Suspense>
      )}
    </section>
  );
}
