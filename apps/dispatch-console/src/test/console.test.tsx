import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import App from '../App';
import { fixtureUnits } from '../lib/fixtures';

afterEach(cleanup);

async function renderApp() {
  render(<App />);
  await screen.findByText(/Incident queue/);
}

describe('dispatch console (demo mode)', () => {
  it('renders incident queue, roster, streams and trust-fund panels', async () => {
    await renderApp();
    expect(screen.getByLabelText('Incident queue')).toBeInTheDocument();
    expect(screen.getByLabelText('Unit roster')).toBeInTheDocument();
    expect(screen.getByLabelText('Live streams')).toBeInTheDocument();
    expect(screen.getByLabelText('Trust-fund transparency')).toBeInTheDocument();
    expect(screen.getByLabelText('Map')).toBeInTheDocument();
    expect(screen.getByText('demo mode')).toBeInTheDocument();
  });

  it('shows the SLO banner with p95 vs the 30s target', async () => {
    await renderApp();
    const banner = screen.getByRole('status');
    expect(banner).toHaveTextContent(/p95 dispatch latency/);
    expect(banner).toHaveTextContent(/30s target/);
    // demo fixtures include a 41s dispatch → breach
    expect(banner).toHaveTextContent(/SLO breach/);
  });

  it('assign action dispatches an incident and marks the unit enroute', async () => {
    await renderApp();
    const assignButtons = screen.getAllByRole('button', { name: 'Assign unit' });
    fireEvent.click(assignButtons[0]);
    const select = screen.getByLabelText(/assign unit for/);
    const firstOption = select.querySelectorAll('option')[1] as HTMLOptionElement;
    fireEvent.change(select, { target: { value: firstOption.value } });
    await waitFor(() => {
      expect(screen.getAllByText('dispatched').length).toBeGreaterThan(0);
    });
    // an available unit flipped to enroute in the roster
    expect(screen.getAllByText('enroute').length).toBeGreaterThan(1);
  });

  it('escalate bumps an incident to escalated/P1', async () => {
    await renderApp();
    const escalateButtons = screen.getAllByRole('button', { name: 'Escalate' });
    fireEvent.click(escalateButtons[0]);
    await waitFor(() => {
      expect(screen.getByText('escalated')).toBeInTheDocument();
    });
  });

  it('close moves the incident out of the open queue', async () => {
    await renderApp();
    const before = screen.getAllByRole('button', { name: 'Close' }).length;
    fireEvent.click(screen.getAllByRole('button', { name: 'Close' })[0]);
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: 'Close' }).length).toBe(before - 1);
    });
  });

  it('shows biometric enrolment badges on the roster', async () => {
    await renderApp();
    const units = fixtureUnits('lagos');
    const enrolled = units.filter((u) => u.biometric_enrolled).length;
    expect(screen.getAllByText('biometric ✓')).toHaveLength(enrolled);
    expect(screen.getAllByText('not enrolled')).toHaveLength(units.length - enrolled);
  });

  it('tenant selector re-scopes all data to the chosen state', async () => {
    await renderApp();
    expect(screen.getAllByText(/^INC-LA-/).length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText('State tenant'), { target: { value: 'benue' } });
    await waitFor(() => {
      expect(screen.getAllByText(/^INC-BE-/).length).toBeGreaterThan(0);
    });
    expect(screen.queryByText(/^INC-LA-/)).not.toBeInTheDocument();
    expect(screen.getByTestId('roster-UNIT-BE-1')).toBeInTheDocument();
    expect(screen.queryByTestId('roster-UNIT-LA-1')).not.toBeInTheDocument();
  });

  it('trust-fund feed shows an intact hash-chain badge', async () => {
    await renderApp();
    expect(screen.getByTestId('hash-chain-badge')).toHaveTextContent('hash chain intact');
  });
});
