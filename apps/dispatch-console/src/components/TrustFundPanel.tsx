import { verifyTrustFundChain } from '../lib/fixtures';
import { useConsole } from '../lib/store';

function formatNaira(kobo: number): string {
  return `₦${(kobo / 100).toLocaleString('en-NG', { maximumFractionDigits: 0 })}`;
}

export function TrustFundPanel() {
  const { trustFund } = useConsole();
  const badLink = verifyTrustFundChain(trustFund);
  const intact = badLink === -1;
  const donations = trustFund
    .filter((e) => e.kind === 'donation')
    .reduce((s, e) => s + e.amount_kobo, 0);
  const disbursed = trustFund
    .filter((e) => e.kind === 'disbursement')
    .reduce((s, e) => s + e.amount_kobo, 0);

  return (
    <section className="panel" aria-label="Trust-fund transparency">
      <h2>Security trust fund — public audit feed</h2>
      <p>
        <span
          className={`badge ${intact ? 'badge-ok' : 'badge-bad'}`}
          data-testid="hash-chain-badge"
        >
          hash chain {intact ? 'intact ✓' : `broken at entry ${badLink + 1}`}
        </span>{' '}
        <span className="muted">
          in {formatNaira(donations)} · out {formatNaira(disbursed)}
        </span>
      </p>
      <table className="table">
        <thead>
          <tr>
            <th>Entry</th>
            <th>Type</th>
            <th>Reference</th>
            <th className="num">Amount</th>
            <th>Hash</th>
          </tr>
        </thead>
        <tbody>
          {trustFund.map((e) => (
            <tr key={e.entry_id}>
              <td>{e.entry_id}</td>
              <td>
                <span className={`badge ${e.kind === 'donation' ? 'badge-ok' : 'badge-warn'}`}>
                  {e.kind}
                </span>
              </td>
              <td>{e.ref}</td>
              <td className="num">{formatNaira(e.amount_kobo)}</td>
              <td>
                <code title={`prev: ${e.prev_hash}`}>{e.hash}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
