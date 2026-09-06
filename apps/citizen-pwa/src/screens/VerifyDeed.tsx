import { useState, type FormEvent } from 'react';
import { verifyDeed } from '../lib/api';
import { UssdNote } from '../components/UssdNote';
import type { DeedVerification, StateId } from '../lib/types';

/** Verify a Certificate of Occupancy / deed against the state cadastre (mod-gis-lands). */
export function VerifyDeed({ stateId }: { stateId: StateId }) {
  const [cofo, setCofo] = useState('');
  const [parcelUin, setParcelUin] = useState('');
  const [result, setResult] = useState<DeedVerification | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await verifyDeed(stateId, cofo.trim(), parcelUin.trim() || undefined));
    } catch {
      setError('Verification failed. Please check the number and try again.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="deed-heading">
      <h1 id="deed-heading">Verify a C-of-O or deed</h1>
      <p className="muted">
        Check a Certificate of Occupancy against the state land registry before you buy, lease or
        use land as collateral.
      </p>
      {error && <p className="notice notice-bad" role="alert">{error}</p>}
      <form onSubmit={onSubmit} className="card">
        <div className="field">
          <label htmlFor="cofo">C-of-O number</label>
          <input id="cofo" required value={cofo} onChange={(e) => setCofo(e.target.value)} placeholder="e.g. LA-2024-018834" />
        </div>
        <div className="field">
          <label htmlFor="uin">Parcel UIN (optional)</label>
          <input id="uin" value={parcelUin} onChange={(e) => setParcelUin(e.target.value)} />
        </div>
        <button className="btn-primary" type="submit" disabled={busy}>
          {busy ? 'Verifying…' : 'Verify deed'}
        </button>
      </form>
      {result && (
        <div className="card" role="status">
          <p>
            <span className={`badge ${result.valid ? 'badge-ok' : 'badge-bad'}`}>
              {result.valid ? '✓ Genuine' : '✗ Not verified'}
            </span>
          </p>
          <p className="small">{result.detail}</p>
          {result.signature_chain_valid != null && (
            <p className="small muted">
              Signature chain: {result.signature_chain_valid ? 'valid' : 'invalid'}
            </p>
          )}
        </div>
      )}
      <UssdNote stateId={stateId} suffix="5" />
    </section>
  );
}
