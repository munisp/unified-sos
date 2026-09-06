import { STATES, type StateId } from '../lib/types';
import { useConsole } from '../lib/store';

export function Header() {
  const { stateId, setStateId, demoMode } = useConsole();
  return (
    <header className="app-header">
      <div>
        <h1>Dispatch Console</h1>
        <p className="tagline">112 emergency CAD — Safe City operations</p>
      </div>
      <div className="header-controls">
        {demoMode && <span className="badge badge-warn">demo mode</span>}
        <label className="tenant-picker">
          <span className="sr-only">State tenant</span>
          <select
            aria-label="State tenant"
            value={stateId}
            onChange={(e) => setStateId(e.target.value as StateId)}
          >
            {STATES.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
      </div>
    </header>
  );
}
