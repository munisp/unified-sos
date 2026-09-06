import { useState, type FormEvent } from 'react';
import { confirmPayment, requestPaymentQuote } from '../lib/api';
import { formatNaira, formatDate } from '../lib/format';
import { UssdNote } from '../components/UssdNote';
import type { PaymentQuote, StateId } from '../lib/types';

/** Pay a ticket/assessment — stub FSPIOP quote → confirm flow. */
export function Payments({ stateId }: { stateId: StateId }) {
  const [ticketRef, setTicketRef] = useState('');
  const [amountNaira, setAmountNaira] = useState('');
  const [quote, setQuote] = useState<PaymentQuote | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onQuote(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const kobo = Math.round(parseFloat(amountNaira) * 100);
      if (!Number.isFinite(kobo) || kobo <= 0) throw new Error('bad-amount');
      setQuote(await requestPaymentQuote(ticketRef.trim(), kobo));
    } catch {
      setError('Could not get a quote. Check the ticket reference and amount.');
    } finally {
      setBusy(false);
    }
  }

  async function onPay() {
    if (!quote) return;
    setBusy(true);
    try {
      setQuote(await confirmPayment(quote));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="pay-heading">
      <h1 id="pay-heading">Pay a ticket or assessment</h1>
      <p className="muted">
        Enter the reference on your ticket or assessment notice. Payment is confirmed with a quote
        before any money moves.
      </p>
      {error && <p className="notice notice-bad" role="alert">{error}</p>}

      {!quote && (
        <form onSubmit={onQuote} className="card">
          <div className="field">
            <label htmlFor="ticket-ref">Ticket / assessment reference</label>
            <input id="ticket-ref" required value={ticketRef} onChange={(e) => setTicketRef(e.target.value)} placeholder="e.g. WIM-2026-00417" />
          </div>
          <div className="field">
            <label htmlFor="amount">Amount (₦)</label>
            <input id="amount" required inputMode="decimal" value={amountNaira} onChange={(e) => setAmountNaira(e.target.value)} placeholder="0.00" />
          </div>
          <button className="btn-primary" type="submit" disabled={busy}>
            {busy ? 'Getting quote…' : 'Get payment quote'}
          </button>
        </form>
      )}

      {quote && quote.status === 'QUOTED' && (
        <div className="card" role="group" aria-label="Payment quote">
          <div className="row"><span className="muted">Reference</span><strong>{quote.ticket_ref}</strong></div>
          <div className="row"><span className="muted">Amount</span><span className="fee">{formatNaira(quote.amount_kobo)}</span></div>
          <div className="row"><span className="muted">Quote</span><span className="hash">{quote.quote_id}</span></div>
          <div className="row"><span className="muted">Valid until</span><span>{formatDate(quote.expires_at)}</span></div>
          <div className="row" style={{ marginTop: '0.8rem' }}>
            <button onClick={() => setQuote(null)}>Cancel</button>
            <button className="btn-primary" onClick={() => void onPay()} disabled={busy}>
              {busy ? 'Paying…' : `Pay ${formatNaira(quote.amount_kobo)}`}
            </button>
          </div>
        </div>
      )}

      {quote && quote.status === 'COMPLETED' && (
        <div className="card" role="status">
          <p><span className="badge badge-ok">PAID</span></p>
          <p>
            {formatNaira(quote.amount_kobo)} paid against <strong>{quote.ticket_ref}</strong>. Keep
            quote <span className="hash">{quote.quote_id}</span> as your receipt.
          </p>
          <button onClick={() => { setQuote(null); setTicketRef(''); setAmountNaira(''); }}>Pay another</button>
        </div>
      )}
      <UssdNote stateId={stateId} suffix="3" />
    </section>
  );
}
