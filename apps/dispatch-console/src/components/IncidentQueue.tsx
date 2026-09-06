import { useState } from 'react';

import { elapsedSeconds } from '../lib/slo';
import { useConsole } from '../lib/store';
import { DISPATCH_SLO_S, type Incident } from '../lib/types';

function formatClock(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function LatencyTimer({ incident, now }: { incident: Incident; now: number }) {
  const seconds = incident.dispatch_latency_s ?? elapsedSeconds(incident, now);
  const over = seconds > DISPATCH_SLO_S && incident.status !== 'closed';
  return (
    <span
      className={`latency ${over ? 'latency-over' : ''}`}
      aria-label={`dispatch latency ${seconds} seconds`}
    >
      {formatClock(seconds)}
      {incident.dispatch_latency_s !== undefined ? '' : ' …'}
    </span>
  );
}

export function IncidentQueue({ now }: { now: number }) {
  const { incidents, units, assignUnit, escalateIncident, closeIncident } = useConsole();
  const [pickFor, setPickFor] = useState<string | null>(null);
  const available = units.filter((u) => u.status === 'available');

  const open = incidents.filter((i) => i.status !== 'closed');
  const closed = incidents.filter((i) => i.status === 'closed');

  return (
    <section className="panel" aria-label="Incident queue">
      <h2>Incident queue ({open.length})</h2>
      {open.length === 0 && <p className="muted">No open incidents.</p>}
      <ul className="queue">
        {open.map((inc) => (
          <li key={inc.incident_id} className={`queue-item priority-${inc.priority.toLowerCase()}`}>
            <div className="queue-row">
              <span className={`badge priority-badge priority-${inc.priority.toLowerCase()}`}>
                {inc.priority}
              </span>
              <div className="queue-main">
                <strong>{inc.category}</strong>
                <span className="muted">
                  {inc.incident_id} · {inc.agency} ·{' '}
                  {inc.assigned_unit_id ? `unit ${inc.assigned_unit_id}` : 'unassigned'}
                </span>
              </div>
              <span className={`badge status-${inc.status}`}>{inc.status}</span>
              <LatencyTimer incident={inc} now={now} />
            </div>
            <div className="queue-actions">
              {pickFor === inc.incident_id ? (
                <select
                  aria-label={`assign unit for ${inc.incident_id}`}
                  autoFocus
                  defaultValue=""
                  onChange={(e) => {
                    const unitId = e.target.value;
                    setPickFor(null);
                    if (unitId) void assignUnit(inc.incident_id, unitId);
                  }}
                  onBlur={() => setPickFor(null)}
                >
                  <option value="" disabled>
                    choose available unit…
                  </option>
                  {available.map((u) => (
                    <option key={u.unit_id} value={u.unit_id}>
                      {u.call_sign} ({u.agency})
                    </option>
                  ))}
                </select>
              ) : (
                <button
                  className="btn-primary"
                  disabled={available.length === 0}
                  onClick={() => setPickFor(inc.incident_id)}
                >
                  Assign unit
                </button>
              )}
              <button onClick={() => escalateIncident(inc.incident_id)}>Escalate</button>
              <button onClick={() => closeIncident(inc.incident_id)}>Close</button>
            </div>
          </li>
        ))}
      </ul>
      {closed.length > 0 && (
        <details>
          <summary className="muted">Closed ({closed.length})</summary>
          <ul className="queue">
            {closed.map((inc) => (
              <li key={inc.incident_id} className="queue-item closed">
                {inc.incident_id} · {inc.category} · closed
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
