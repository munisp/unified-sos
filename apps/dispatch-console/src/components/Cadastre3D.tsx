// CesiumJS 3D cadastre viewer (feature-flagged via VITE_ENABLE_3D).
// Renders extruded building footprints / cadastral parcels from the bundled
// per-state GeoJSON fixtures. Cesium Ion is DISABLED — no ion token, no
// external network: base imagery off, terrain = ellipsoid, assets served from
// the locally bundled /cesium directory (see vite.config.ts static copy).

import { useEffect, useRef, useState } from 'react';

import { STATE_GEOFENCES } from '../lib/fixtures';
import { fixtureUrl, is3DEnabled, isWebGLAvailable, type GeoJsonFeatureCollection } from '../lib/mapcore';
import { useConsole } from '../lib/store';

interface ParcelAttrs {
  parcel_uin?: string;
  cofo_number?: string;
  lga?: string;
  luc_band?: string;
  cofo_status?: string;
  area_sqm?: number;
  height_m?: number;
}

/** Inner viewer — only ever loaded when the 3D flag is on. */
function Cadastre3DInner() {
  const { stateId } = useConsole();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [selected, setSelected] = useState<ParcelAttrs | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    let disposed = false;
    let cleanup: (() => void) | undefined;

    void (async () => {
      try {
        const Cesium = await import('cesium');
        await import('cesium/Build/Cesium/Widgets/widgets.css');
        if (disposed || !containerRef.current) return;

        // Sovereign/offline posture: never touch Cesium Ion.
        Cesium.Ion.defaultAccessToken = '';

        const viewer = new Cesium.Viewer(containerRef.current, {
          baseLayer: false, // no ion imagery
          baseLayerPicker: false,
          geocoder: false,
          homeButton: false,
          sceneModePicker: true, // 3D / 2D / columbus tilt controls
          animation: false,
          timeline: false,
          navigationHelpButton: false,
          fullscreenButton: false,
          terrainProvider: new Cesium.EllipsoidTerrainProvider(),
        });
        viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString('#f3ece1');
        viewer.scene.skyAtmosphere = undefined as never;
        viewer.scene.backgroundColor = Cesium.Color.fromCssColorString('#faf6ef');

        const res = await fetch(fixtureUrl(`parcels-${stateId}.geojson`));
        if (!res.ok) throw new Error(`parcel fixtures ${res.status}`);
        const fc = (await res.json()) as GeoJsonFeatureCollection;

        const ds = await Cesium.GeoJsonDataSource.load(fc, {
          clampToGround: false,
          fill: Cesium.Color.fromCssColorString('#8c5230').withAlpha(0.75),
          stroke: Cesium.Color.fromCssColorString('#5c3520'),
        });
        for (const entity of ds.entities.values) {
          const props = entity.properties?.getValue(Cesium.JulianDate.now()) as ParcelAttrs;
          if (entity.polygon) {
            entity.polygon.extrudedHeight = new Cesium.ConstantProperty(props?.height_m ?? 10);
            entity.polygon.height = new Cesium.ConstantProperty(0);
            entity.polygon.outline = new Cesium.ConstantProperty(true);
          }
        }
        await viewer.dataSources.add(ds);

        const [minLat, minLon, maxLat, maxLon] = STATE_GEOFENCES[stateId];
        viewer.camera.setView({
          destination: Cesium.Rectangle.fromDegrees(minLon, minLat, maxLon, maxLat),
        });

        // Click parcel → attribute card.
        const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        handler.setInputAction((movement: any) => {
          const picked = viewer.scene.pick(movement.position) as
            | { id?: InstanceType<typeof Cesium.Entity> }
            | undefined;
          const entity = picked?.id;
          if (entity instanceof Cesium.Entity && entity.properties) {
            setSelected(entity.properties.getValue(Cesium.JulianDate.now()) as ParcelAttrs);
          } else {
            setSelected(null);
          }
        }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

        cleanup = () => {
          handler.destroy();
          viewer.destroy();
        };
      } catch (err) {
        if (!disposed) setError(err instanceof Error ? err.message : '3D viewer failed');
      }
    })();

    return () => {
      disposed = true;
      cleanup?.();
    };
  }, [stateId]);

  if (error) {
    return (
      <p className="notice notice-bad" role="alert">
        3D cadastre unavailable: {error}
      </p>
    );
  }

  return (
    <div>
      <div
        ref={containerRef}
        data-testid="cesium-container"
        style={{ width: '100%', height: 360 }}
        aria-label={`3D cadastre of ${stateId}`}
      />
      {selected && (
        <div className="card map-popup" role="status" aria-label="Parcel details">
          <div className="row">
            <strong>{selected.parcel_uin ?? 'parcel'}</strong>
            <span className={`badge ${selected.cofo_status === 'VERIFIED' ? 'badge-ok' : 'badge-bad'}`}>
              C-of-O {selected.cofo_status ?? 'UNKNOWN'}
            </span>
          </div>
          <p className="small">
            C-of-O № {selected.cofo_number ?? '—'} · LGA {selected.lga ?? '—'} · LUC band{' '}
            {selected.luc_band ?? '—'}
          </p>
          <p className="small muted">
            Area ≈ {selected.area_sqm?.toLocaleString() ?? '—'} m² · extrusion {selected.height_m ?? '—'} m
          </p>
        </div>
      )}
      <p className="legend muted">
        Extruded demo parcels from bundled fixtures (Cesium Ion disabled — local assets only).
      </p>
    </div>
  );
}

/** Gated wrapper — renders nothing unless VITE_ENABLE_3D is set and WebGL exists. */
export function Cadastre3D() {
  if (!is3DEnabled() || !isWebGLAvailable()) return null;
  return (
    <section className="panel" aria-label="3D cadastre">
      <h2>3D cadastre</h2>
      <Cadastre3DInner />
    </section>
  );
}
