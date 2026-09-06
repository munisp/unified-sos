import { STATES, type StateId } from '../lib/types';

/** USSD parity note: every flow shows a feature-phone alternative. */
export function UssdNote({ stateId, suffix }: { stateId: StateId; suffix?: string }) {
  const state = STATES.find((s) => s.id === stateId);
  if (!state) return null;
  const code = suffix ? state.ussd.replace('#', `*${suffix}#`) : state.ussd;
  return (
    <p className="ussd-note" role="note">
      No smartphone or data? Dial <strong>{code}</strong> on any phone to do this by USSD instead.
    </p>
  );
}
