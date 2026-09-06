import { useState, type FormEvent } from 'react';
import { createWallet } from '../lib/api';
import { useAppStore } from '../lib/store';
import { UssdNote } from '../components/UssdNote';
import type { StateId } from '../lib/types';

const KYC_LABEL: Record<string, string> = {
  ACTIVE: 'Verified',
  PENDING: 'Pending verification',
  SUSPENDED: 'Suspended',
};

export function Profile({ stateId }: { stateId: StateId }) {
  const { wallet, setWallet, clearState } = useAppStore();
  const [nin, setNin] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    if (!/^\d{11}$/.test(nin.trim())) {
      setError('NIN must be 11 digits.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setWallet(await createWallet(stateId, nin.trim()));
      setNin('');
    } catch {
      setError('Could not create your wallet. Please try again.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="profile-heading">
      <h1 id="profile-heading">Profile</h1>

      <div className="card" aria-label="Identity wallet">
        <h2 style={{ marginTop: 0 }}>Identity wallet</h2>
        {wallet ? (
          <>
            <div className="row"><span className="muted">Wallet</span><span className="hash">{wallet.wallet_id}</span></div>
            <div className="row"><span className="muted">NIN</span><span>{wallet.masked_nin}</span></div>
            <div className="row"><span className="muted">Realm</span><span>{wallet.keycloak_realm}</span></div>
            <div className="row">
              <span className="muted">KYC status</span>
              <span className={`badge ${wallet.status === 'ACTIVE' ? 'badge-ok' : 'badge-warn'}`}>
                {KYC_LABEL[wallet.status] ?? wallet.status}
              </span>
            </div>
          </>
        ) : (
          <>
            <p className="muted small">
              Create your identity wallet with your NIN. The number is hashed immediately and never
              stored in raw form.
            </p>
            {error && <p className="notice notice-bad" role="alert">{error}</p>}
            <form onSubmit={onCreate}>
              <div className="field">
                <label htmlFor="nin">National Identification Number (NIN)</label>
                <input id="nin" inputMode="numeric" autoComplete="off" maxLength={11} value={nin} onChange={(e) => setNin(e.target.value)} placeholder="11 digits" />
              </div>
              <button className="btn-primary" type="submit" disabled={busy}>
                {busy ? 'Creating…' : 'Create wallet'}
              </button>
            </form>
          </>
        )}
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>State</h2>
        <div className="row">
          <span style={{ textTransform: 'capitalize' }}>{stateId}</span>
          <button onClick={clearState}>Change state</button>
        </div>
      </div>
      <UssdNote stateId={stateId} suffix="1" />
    </section>
  );
}
