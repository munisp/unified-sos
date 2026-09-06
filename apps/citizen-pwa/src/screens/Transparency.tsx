import { useEffect, useState } from 'react';
import {
  escrowStatements,
  procurementAudit,
  procurementAuditVerify,
  trustFundFeed,
} from '../lib/api';
import { formatDate, formatNaira, truncateHash } from '../lib/format';
import { UssdNote } from '../components/UssdNote';
import type {
  ConcessionEscrowStatement,
  ProcurementAuditDigest,
  ProcurementAuditVerification,
  StateId,
  TrustFundFeed,
} from '../lib/types';

/** Displays pseudonymised hashes only — mod-transparency structurally redacts PII. */
export function HashPseudonym({ hash, label }: { hash: string | null | undefined; label: string }) {
  if (!hash) return <span className="muted small">—</span>;
  return (
    <span className="hash" title={`${label} (pseudonymised — no personal identity is published)`}>
      {label}: {truncateHash(hash)}
    </span>
  );
}

type Tab = 'trust' | 'escrow' | 'audit';

export function Transparency({ stateId }: { stateId: StateId }) {
  const [tab, setTab] = useState<Tab>('trust');
  const [feed, setFeed] = useState<TrustFundFeed | null>(null);
  const [statements, setStatements] = useState<ConcessionEscrowStatement[] | null>(null);
  const [digests, setDigests] = useState<ProcurementAuditDigest[] | null>(null);
  const [verify, setVerify] = useState<ProcurementAuditVerification | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setFeed(null); setStatements(null); setDigests(null); setVerify(null); setError(null);
    trustFundFeed(stateId)
      .then(({ data }) => live && setFeed(data))
      .catch(() => live && setError('Transparency feed unavailable for this state.'));
    escrowStatements(stateId).then(({ data }) => live && setStatements(data)).catch(() => undefined);
    procurementAudit(stateId).then(({ data }) => live && setDigests(data)).catch(() => undefined);
    procurementAuditVerify(stateId).then(({ data }) => live && setVerify(data)).catch(() => undefined);
    return () => {
      live = false;
    };
  }, [stateId]);

  return (
    <section aria-labelledby="tp-heading">
      <h1 id="tp-heading">Public transparency</h1>
      <p className="muted">
        Read-only public feeds. All identities are pseudonymised — no donor, actor or concessionaire
        names are ever published.
      </p>
      {error && <p className="notice notice-bad" role="alert">{error}</p>}

      <div role="tablist" aria-label="Transparency feeds" className="row" style={{ justifyContent: 'flex-start' }}>
        {([['trust', 'Trust fund'], ['escrow', 'Escrow'], ['audit', 'Procurement audit']] as [Tab, string][]).map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? 'btn-primary' : ''}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'trust' && (
        <div role="tabpanel" aria-label="Security trust fund">
          {!feed ? (
            <p role="status">Loading…</p>
          ) : (
            <>
              <div className="card">
                <div className="row">
                  <span className="muted">Current balance</span>
                  <strong className="fee">{formatNaira(feed.balance_kobo)}</strong>
                </div>
                <div className="small muted">Chain head <span className="hash">{truncateHash(feed.head_cursor)}</span></div>
              </div>
              <ul style={{ listStyle: 'none', padding: 0 }} className="stack">
                {feed.entries.map((e) => (
                  <li key={e.entry_id} className="card">
                    <div className="row">
                      <span className={e.kind === 'donation' ? 'amount-pos' : 'amount-neg'}>
                        {e.kind === 'donation' ? '+' : '−'}{formatNaira(e.amount_kobo)}
                      </span>
                      <span className="small muted">{formatDate(e.occurred_at)}</span>
                    </div>
                    <div className="small">
                      {e.kind === 'donation' ? (
                        <HashPseudonym hash={e.donor_alias_hash} label="Donor" />
                      ) : (
                        <span>{e.purpose_label ?? 'Disbursement'}</span>
                      )}
                    </div>
                    <div className="hash">cursor {truncateHash(e.cursor)}</div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {tab === 'escrow' && (
        <div role="tabpanel" aria-label="Concession escrow statements">
          {!statements ? (
            <p role="status">Loading…</p>
          ) : statements.length === 0 ? (
            <p className="muted">No statements published yet.</p>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table className="ledger">
                <thead>
                  <tr><th>Period</th><th>Gross</th><th>State share</th><th>Reconciled</th></tr>
                </thead>
                <tbody>
                  {statements.map((s) => (
                    <tr key={s.statement_id}>
                      <td>{s.period}</td>
                      <td>{formatNaira(s.gross_collections_kobo)}</td>
                      <td>{formatNaira(s.state_share_kobo)} ({s.applied_state_share_bps / 100}%)</td>
                      <td>
                        <span className={`badge ${s.reconciled ? 'badge-ok' : 'badge-bad'}`}>
                          {s.reconciled ? 'Yes' : 'No'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="small muted" style={{ marginTop: '0.6rem' }}>
                Contracts: {statements.map((s) => (
                  <HashPseudonym key={s.statement_id} hash={s.contract_ref_hash} label={s.statement_id} />
                ))}
              </p>
            </div>
          )}
        </div>
      )}

      {tab === 'audit' && (
        <div role="tabpanel" aria-label="Procurement audit chain">
          {verify && (
            <p className="card">
              <span className={`badge ${verify.chain_valid ? 'badge-ok' : 'badge-bad'}`}>
                {verify.chain_valid ? '✓ Chain verified' : '✗ Chain broken'}
              </span>{' '}
              <span className="small muted">
                {verify.entries_checked} entries checked · head{' '}
                <span className="hash">{truncateHash(verify.head_hash)}</span>
                {!verify.chain_valid && verify.first_invalid_seq != null && (
                  <> · first invalid entry #{verify.first_invalid_seq}</>
                )}
              </span>
            </p>
          )}
          {!digests ? (
            <p role="status">Loading…</p>
          ) : (
            <ul style={{ listStyle: 'none', padding: 0 }} className="stack">
              {digests.map((d) => (
                <li key={d.seq} className="card">
                  <div className="row">
                    <strong>#{d.seq} {d.action}</strong>
                    <span className="small muted">{formatDate(d.at)}</span>
                  </div>
                  <HashPseudonym hash={d.subject_ref_hash} label="Subject" />
                  <div className="hash">hash {truncateHash(d.entry_hash)} · prev {truncateHash(d.prev_hash)}</div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <UssdNote stateId={stateId} suffix="9" />
    </section>
  );
}
