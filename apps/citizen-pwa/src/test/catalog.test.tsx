import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { Home } from '../screens/Home';
import { AppStoreProvider } from '../lib/store';

function renderHome() {
  return render(
    <AppStoreProvider>
      <Home stateId="lagos" />
    </AppStoreProvider>,
  );
}

describe('service catalog', () => {
  beforeEach(() => window.localStorage.clear());

  it('renders all 11 category tiles in demo mode', async () => {
    renderHome();
    await waitFor(() => expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/what do you need/i));
    for (const label of [
      'Revenue & tax', 'Land & titles', 'Mining', 'Agriculture', 'Transport', 'Markets',
      'Health', 'Education', 'Environment', 'Forestry', 'Investment & PPP',
    ]) {
      expect(screen.getByRole('button', { name: new RegExp(label, 'i') })).toBeInTheDocument();
    }
  });

  it('drills into a category and lists seeded services with naira fees', async () => {
    renderHome();
    await waitFor(() => screen.getByRole('button', { name: /land & titles/i }));
    fireEvent.click(screen.getByRole('button', { name: /land & titles/i }));
    expect(screen.getByRole('heading', { name: /land & titles/i })).toBeInTheDocument();
    expect(screen.getByText('Certificate of Occupancy')).toBeInTheDocument();
    expect(screen.getByText(/₦5,000/)).toBeInTheDocument(); // 500_000 kobo = ₦5,000
    expect(screen.getByRole('link', { name: /apply/i })).toHaveAttribute('href', '#/request/LAND-COFO');
  });

  it('shows the USSD feature-phone alternative', async () => {
    renderHome();
    await waitFor(() => screen.getByRole('button', { name: /mining/i }));
    expect(screen.getByRole('note')).toHaveTextContent(/\*347\*11#/);
  });
});
