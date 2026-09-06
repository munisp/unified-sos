import { useConsole } from '../lib/store';

export function UnitRoster() {
  const { units } = useConsole();
  return (
    <section className="panel" aria-label="Unit roster">
      <h2>Unit roster ({units.length})</h2>
      <table className="table">
        <thead>
          <tr>
            <th>Call sign</th>
            <th>Agency</th>
            <th>Status</th>
            <th>Personnel</th>
            <th>Biometric</th>
          </tr>
        </thead>
        <tbody>
          {units.map((u) => (
            <tr key={u.unit_id} data-testid={`roster-${u.unit_id}`}>
              <td>
                <strong>{u.call_sign}</strong>
              </td>
              <td>{u.agency}</td>
              <td>
                <span className={`badge unit-${u.status}`}>{u.status}</span>
              </td>
              <td>{u.personnel_count}</td>
              <td>
                {u.biometric_enrolled ? (
                  <span className="badge badge-ok">biometric ✓</span>
                ) : (
                  <span className="badge badge-bad">not enrolled</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
