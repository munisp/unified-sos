// Dependency-light SVG map: schematic Nigeria outline + state geofence box +
// incident/unit markers projected from GeoJSON fixtures. No tile servers.

import { NIGERIA_OUTLINE, project, projectNational } from '../lib/geo';
import { useConsole } from '../lib/store';
import { STATE_GEOFENCES } from '../lib/fixtures';

const VIEW = { width: 480, height: 360, pad: 20 };

const PRIORITY_FILL: Record<string, string> = {
  P1: '#9c3d2b',
  P2: '#8c5230',
  P3: '#6f6154',
};

export function MapPanel() {
  const { stateId, incidents, units } = useConsole();
  const outline = NIGERIA_OUTLINE.map(([lon, lat], i) => {
    const { x, y } = projectNational(lon, lat, VIEW);
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');

  const [minLat, minLon, maxLat, maxLon] = STATE_GEOFENCES[stateId];
  const tl = projectNational(minLon, maxLat, VIEW);
  const br = projectNational(maxLon, minLat, VIEW);

  return (
    <section className="panel" aria-label="Map">
      <h2>Map — {stateId}</h2>
      <svg
        role="img"
        aria-label={`map of ${stateId} with incidents and units`}
        viewBox={`0 0 ${VIEW.width} ${VIEW.height}`}
        className="map"
      >
        <path d={`${outline} Z`} fill="#f3ece1" stroke="#e3d9c9" strokeWidth={1.5} />
        <rect
          x={tl.x}
          y={tl.y}
          width={br.x - tl.x}
          height={br.y - tl.y}
          fill="none"
          stroke="#8c5230"
          strokeDasharray="4 3"
          strokeWidth={1.5}
        />
        {incidents
          .filter((i) => i.status !== 'closed')
          .map((inc) => {
            const { x, y } = project(stateId, inc.longitude, inc.latitude, VIEW);
            return (
              <circle
                key={inc.incident_id}
                data-testid={`map-incident-${inc.incident_id}`}
                cx={x}
                cy={y}
                r={inc.priority === 'P1' ? 7 : 5}
                fill={PRIORITY_FILL[inc.priority] ?? '#6f6154'}
                stroke="#fff"
                strokeWidth={1.5}
              >
                <title>
                  {inc.incident_id} {inc.category} ({inc.status})
                </title>
              </circle>
            );
          })}
        {units.map((u) => {
          const { x, y } = project(stateId, u.longitude, u.latitude, VIEW);
          return (
            <rect
              key={u.unit_id}
              data-testid={`map-unit-${u.unit_id}`}
              x={x - 4}
              y={y - 4}
              width={8}
              height={8}
              fill={u.status === 'available' ? '#3f6b4a' : '#8c5230'}
              stroke="#fff"
              strokeWidth={1}
            >
              <title>
                {u.call_sign} ({u.status})
              </title>
            </rect>
          );
        })}
      </svg>
      <p className="legend muted">
        <span className="dot dot-incident" /> incident (size = priority) ·{' '}
        <span className="dot dot-unit" /> unit
      </p>
    </section>
  );
}
