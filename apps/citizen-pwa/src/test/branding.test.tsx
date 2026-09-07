import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';

import {
  applyBranding,
  fetchBranding,
  fixtureBranding,
  monogram,
  useBranding,
} from '../lib/branding';
import { AppStoreProvider, useAppStore } from '../lib/store';
import { STATES, type StateId } from '../lib/types';
import type { ReactNode } from 'react';

describe('branding fixtures', () => {
  it('are deterministic per state', () => {
    for (const s of STATES) {
      expect(fixtureBranding(s.id)).toEqual(fixtureBranding(s.id));
      expect(fixtureBranding(s.id).tenant_state_id).toBe(s.id);
    }
  });

  it('give each state a distinct muted primary color and gov.ng domain', () => {
    const primaries = new Set(STATES.map((s) => fixtureBranding(s.id).primary_color));
    expect(primaries.size).toBe(STATES.length);
    expect(fixtureBranding('lagos').primary_color).toBe('#8c5230');
    for (const s of STATES) {
      expect(fixtureBranding(s.id).custom_domain).toBe(`sos.${s.id}state.gov.ng`);
      expect(fixtureBranding(s.id).display_name).toContain('One-Gov Portal');
    }
  });
});

describe('monogram', () => {
  it('renders initials from the display name', () => {
    expect(monogram('Lagos State One-Gov Portal')).toBe('LS');
    expect(monogram('Benue State One-Gov Portal')).toBe('BS');
  });
});

describe('applyBranding', () => {
  beforeEach(() => {
    document.documentElement.removeAttribute('style');
    document.title = '';
  });

  it('sets brand CSS custom properties on the document root', () => {
    applyBranding(fixtureBranding('ogun'));
    const style = document.documentElement.style;
    expect(style.getPropertyValue('--brand-primary')).toBe('#4f6b4a');
    expect(style.getPropertyValue('--brand-secondary')).toBe('#3c5438');
  });

  it('updates document.title to the portal title', () => {
    applyBranding(fixtureBranding('benue'));
    expect(document.title).toBe('Benue State One-Gov Portal');
  });
});

describe('fetchBranding', () => {
  afterEach(() => vi.unstubAllEnvs());

  it('falls back to the deterministic fixture when the fetch fails', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.test');
    vi.resetModules();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network down')));
    const { fetchBranding: fetchWithBase } = await import('../lib/branding');
    const res = await fetchWithBase('lagos');
    expect(res.source).toBe('fixture');
    expect(res.branding).toEqual(fixtureBranding('lagos'));
    vi.unstubAllGlobals();
  });

  it('returns fixture in demo mode (no API base configured)', async () => {
    const res = await fetchBranding('taraba');
    expect(res.source).toBe('fixture');
    expect(res.branding).toEqual(fixtureBranding('taraba'));
  });
});

describe('useBranding', () => {
  beforeEach(() => {
    document.documentElement.removeAttribute('style');
  });

  it('resolves and applies branding for the selected state', async () => {
    const { result } = renderHook(() => useBranding('nasarawa'));
    await waitFor(() => expect(result.current).not.toBeNull());
    expect(result.current!.branding.portal_title).toBe('Nasarawa State One-Gov Portal');
    expect(document.documentElement.style.getPropertyValue('--brand-primary')).toBe('#3f5a6b');
    expect(document.title).toBe('Nasarawa State One-Gov Portal');
  });
});

describe('locale default', () => {
  function wrapper({ children }: { children: ReactNode }) {
    return <AppStoreProvider>{children}</AppStoreProvider>;
  }

  beforeEach(() => window.localStorage.clear());

  it('honors branding default_locale when the user has not chosen a language', () => {
    const { result } = renderHook(() => useAppStore(), { wrapper });
    expect(result.current.locale).toBe('en');
    act(() => result.current.applyDefaultLocale(fixtureBranding('osun').default_locale));
    expect(result.current.locale).toBe('yo');
  });

  it('keeps an explicit user choice over the tenant default', () => {
    const { result } = renderHook(() => useAppStore(), { wrapper });
    act(() => result.current.setLocale('ig'));
    act(() => result.current.applyDefaultLocale('ha'));
    expect(result.current.locale).toBe('ig');
  });
});
