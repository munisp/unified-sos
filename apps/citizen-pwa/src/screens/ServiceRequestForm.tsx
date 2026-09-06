import { useEffect, useState, type FormEvent } from 'react';
import { listServices, submitServiceRequest } from '../lib/api';
import { formatNaira } from '../lib/format';
import { navigate } from '../lib/router';
import { useAppStore } from '../lib/store';
import { UssdNote } from '../components/UssdNote';
import type { Priority, ServiceCatalogEntry, StateId } from '../lib/types';

export function ServiceRequestForm({ stateId, serviceCode }: { stateId: StateId; serviceCode: string }) {
  const { wallet, trackRequest } = useAppStore();
  const [service, setService] = useState<ServiceCatalogEntry | null>(null);
  const [fullName, setFullName] = useState('');
  const [phone, setPhone] = useState('');
  const [details, setDetails] = useState('');
  const [priority, setPriority] = useState<Priority>('STANDARD');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ id: string; queued: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    listServices(stateId).then(({ data }) => {
      if (live) setService(data.find((s) => s.service_code === serviceCode) ?? null);
    });
    return () => {
      live = false;
    };
  }, [stateId, serviceCode]);

  const feeKobo = service
    ? priority === 'EXPEDITED' && service.expedited_fee_kobo > 0
      ? service.expedited_fee_kobo
      : service.base_fee_kobo
    : 0;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!service) return;
    setBusy(true);
    setError(null);
    try {
      const { request, queued } = await submitServiceRequest({
        state_id: stateId,
        wallet_id: wallet?.wallet_id ?? 'WLT-ANON',
        service_code: service.service_code,
        form_payload: { full_name: fullName, phone, details },
        priority,
      });
      trackRequest({ ...request, fee_kobo: feeKobo });
      setDone({ id: request.request_id, queued });
    } catch {
      setError('Submission failed and could not be queued. Please try again.');
    } finally {
      setBusy(false);
    }
  }

  if (!service) return <p role="status">Loading service…</p>;

  if (done) {
    return (
      <section aria-labelledby="done-heading">
        <h1 id="done-heading">{done.queued ? 'Request queued' : 'Request submitted'}</h1>
        <div className="card">
          <p>
            Reference: <strong className="hash">{done.id}</strong>
          </p>
          {done.queued ? (
            <p className="notice notice-warn">
              You appear to be offline. Your request is saved on this device and will be sent
              automatically when connectivity returns.
            </p>
          ) : (
            <p className="muted">You will receive updates as the MDA reviews your request.</p>
          )}
          <button className="btn-primary" onClick={() => navigate('/requests')}>
            Track my requests
          </button>
        </div>
        <UssdNote stateId={stateId} />
      </section>
    );
  }

  return (
    <section aria-labelledby="form-heading">
      <h1 id="form-heading">{service.name}</h1>
      <p className="muted">
        {service.mda} · SLA {service.sla_days} days · Fee{' '}
        <span className="fee">{feeKobo ? formatNaira(feeKobo) : 'Free'}</span>
      </p>
      {error && <p className="notice notice-bad" role="alert">{error}</p>}
      <form onSubmit={onSubmit}>
        <div className="field">
          <label htmlFor="full-name">Full name</label>
          <input id="full-name" required autoComplete="name" value={fullName} onChange={(e) => setFullName(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="phone">Phone number</label>
          <input id="phone" required type="tel" autoComplete="tel" inputMode="tel" value={phone} onChange={(e) => setPhone(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="details">Details</label>
          <textarea id="details" rows={4} value={details} onChange={(e) => setDetails(e.target.value)} />
        </div>
        <fieldset className="field" style={{ border: 0, padding: 0, margin: '0 0 1.1rem' }}>
          <legend style={{ fontWeight: 600, marginBottom: '0.35rem' }}>Priority</legend>
          <label style={{ fontWeight: 400, display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <input type="radio" name="priority" style={{ width: 'auto', minHeight: 0 }} checked={priority === 'STANDARD'} onChange={() => setPriority('STANDARD')} />
            Standard — {service.base_fee_kobo ? formatNaira(service.base_fee_kobo) : 'Free'}
          </label>
          {service.expedited_fee_kobo > 0 && (
            <label style={{ fontWeight: 400, display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <input type="radio" name="priority" style={{ width: 'auto', minHeight: 0 }} checked={priority === 'EXPEDITED'} onChange={() => setPriority('EXPEDITED')} />
              Expedited — {formatNaira(service.expedited_fee_kobo)}
            </label>
          )}
        </fieldset>
        <button className="btn-primary" type="submit" disabled={busy}>
          {busy ? 'Submitting…' : 'Submit request'}
        </button>
      </form>
      <UssdNote stateId={stateId} />
    </section>
  );
}
