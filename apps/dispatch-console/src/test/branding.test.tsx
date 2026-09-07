import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';

import {
  applyBranding,
  fetchBranding,
  fixtureBranding,
  monogram,
} from '../lib/branding';
import App from '../App';
import { STATES } from '../lib/types';

afterEach(cleanup);

describe('branding fixtures', () => {
  it('are deterministic per state', () => {
    for (const s of STATES) {
      expect(fixtureBranding(s.id)).toEqual(fixtureBranding(s.id));
      expect(fixtureBranding(s.id).tenant_state_id).toBe(s.id);
    }
  });

  it('give each state a distinct primary color and gov.ng domain', () => {
    const primaries = new Set(STATES.map((s) => fixtureBranding(s.id).primary_color));
    expect(primaries.size).toBe(STATES.length);
    for (const s of STATES) {
      expect(fixtureBranding(s.id).custom_domain).toBe(`sos.${s.id}state.gov.ng`);
    }
  });
});

describe('monogram', () => {
  it('renders initials from the display name', () => {
    expect(monogram('Lagos State One-Gov Portal')).toBe('LS');
  });
});

describe('applyBranding', () => {
  beforeEach(() => {
    document.documentElement.removeAttribute('style');
    document.title = '';
  });

  it('sets brand CSS custom properties on the document root', () => {
    applyBranding(fixtureBranding('taraba'));
    const style = document.documentElement.style;
    expect(style.getPropertyValue('--brand-primary')).toBe('#6b5a3f');
    expect(style.getPropertyValue('--brand-secondary')).toBe('#52452f');
  });

  it('updates document.title to the portal title', () => {
    applyBranding(fixtureBranding('ogun'));
    expect(document.title).toBe('Ogun State One-Gov Portal');
  });
});

describe('fetchBranding', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it('falls back to the deterministic fixture when the fetch fails', async () => {
    vi.stubEnv('VITE_API_BASE', 'https://api.example.test');
    vi.resetModules();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network down')));
    const { fetchBranding: fetchWithBase } = await import('../lib/branding');
    const res = await fetchWithBase('benue');
    expect(res.source).toBe('fixture');
    expect(res.branding).toEqual(fixtureBranding('benue'));
  });

  it('returns fixture in demo mode (no API base configured)', async () => {
    const res = await fetchBranding('lagos');
    expect(res.source).toBe('fixture');
    expect(res.branding.display_name).toBe('Lagos State One-Gov Portal');
  });
});

describe('whitelabeled console chrome', () => {
  it('header shows monogram, portal title, tagline and Powered-by attribution', async () => {
    render(<App />);
    await screen.findByText(/Incident queue/);
    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
        'Lagos State One-Gov Portal',
      ),
    );
    expect(screen.getByTestId('brand-monogram')).toHaveTextContent('LS');
    expect(screen.getByText(/Eko for all/)).toBeInTheDocument();
    expect(screen.getAllByText(/Powered by SOS/).length).toBeGreaterThan(0);
  });

  it('footer shows support contacts and custom domain; rebrand on tenant switch', async () => {
    render(<App />);
    await screen.findByText(/Incident queue/);
    await waitFor(() =>
      expect(screen.getByText('support@lagosstate.gov.ng')).toBeInTheDocument(),
    );
    expect(screen.getByText(/sos\.lagosstate\.gov\.ng/)).toBeInTheDocument();
    expect(document.title).toBe('Lagos State One-Gov Portal');
    expect(document.documentElement.style.getPropertyValue('--brand-primary')).toBe('#8c5230');
  });
});
