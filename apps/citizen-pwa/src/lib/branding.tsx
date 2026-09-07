// Whitelabel branding for per-state deployments.
//
// Contract (backend control plane, built in parallel):
//   GET {VITE_API_BASE_URL}/cp/v1/tenants/{state}/branding → Branding
// Fail-soft: in demo mode or on any fetch error the deterministic per-state
// fixture below is used, so a state's portal always renders branded even
// without the backend.

import { useEffect, useState } from 'react';
import type { StateId } from './types';
import type { Locale } from './i18n';

export interface Branding {
  tenant_state_id: StateId;
  display_name: string;
  portal_title: string;
  tagline: string;
  primary_color: string;
  secondary_color: string;
  logo_url: string;
  favicon_url: string;
  support_email: string;
  support_phone: string;
  custom_domain: string;
  locales: Locale[];
  default_locale: Locale;
  pwa_theme_color: string;
  pwa_name: string;
}

const FIXTURES: Record<StateId, Branding> = {
  lagos: {
    tenant_state_id: 'lagos',
    display_name: 'Lagos State One-Gov Portal',
    portal_title: 'Lagos State One-Gov Portal',
    tagline: 'Eko for all — every service, one portal.',
    primary_color: '#8c5230',
    secondary_color: '#6f3e22',
    logo_url: '/branding/lagos/logo.svg',
    favicon_url: '/branding/lagos/favicon.svg',
    support_email: 'support@lagosstate.gov.ng',
    support_phone: '+234 700 0000 011',
    custom_domain: 'sos.lagosstate.gov.ng',
    locales: ['en', 'yo'],
    default_locale: 'en',
    pwa_theme_color: '#8c5230',
    pwa_name: 'Lagos One-Gov',
  },
  ogun: {
    tenant_state_id: 'ogun',
    display_name: 'Ogun State One-Gov Portal',
    portal_title: 'Ogun State One-Gov Portal',
    tagline: 'The Gateway State, at your fingertips.',
    primary_color: '#4f6b4a',
    secondary_color: '#3c5438',
    logo_url: '/branding/ogun/logo.svg',
    favicon_url: '/branding/ogun/favicon.svg',
    support_email: 'support@ogunstate.gov.ng',
    support_phone: '+234 700 0000 012',
    custom_domain: 'sos.ogunstate.gov.ng',
    locales: ['en', 'yo'],
    default_locale: 'en',
    pwa_theme_color: '#4f6b4a',
    pwa_name: 'Ogun One-Gov',
  },
  osun: {
    tenant_state_id: 'osun',
    display_name: 'Osun State One-Gov Portal',
    portal_title: 'Osun State One-Gov Portal',
    tagline: 'State of the Living Spring — services made simple.',
    primary_color: '#7a5c2e',
    secondary_color: '#5d4622',
    logo_url: '/branding/osun/logo.svg',
    favicon_url: '/branding/osun/favicon.svg',
    support_email: 'support@osunstate.gov.ng',
    support_phone: '+234 700 0000 013',
    custom_domain: 'sos.osunstate.gov.ng',
    locales: ['en', 'yo'],
    default_locale: 'yo',
    pwa_theme_color: '#7a5c2e',
    pwa_name: 'Osun One-Gov',
  },
  benue: {
    tenant_state_id: 'benue',
    display_name: 'Benue State One-Gov Portal',
    portal_title: 'Benue State One-Gov Portal',
    tagline: 'Food Basket of the Nation, served digitally.',
    primary_color: '#5b4a6b',
    secondary_color: '#463852',
    logo_url: '/branding/benue/logo.svg',
    favicon_url: '/branding/benue/favicon.svg',
    support_email: 'support@benuestate.gov.ng',
    support_phone: '+234 700 0000 014',
    custom_domain: 'sos.benuestate.gov.ng',
    locales: ['en', 'ha', 'ig'],
    default_locale: 'en',
    pwa_theme_color: '#5b4a6b',
    pwa_name: 'Benue One-Gov',
  },
  nasarawa: {
    tenant_state_id: 'nasarawa',
    display_name: 'Nasarawa State One-Gov Portal',
    portal_title: 'Nasarawa State One-Gov Portal',
    tagline: 'Home of Solid Minerals — one portal, every service.',
    primary_color: '#3f5a6b',
    secondary_color: '#2f4553',
    logo_url: '/branding/nasarawa/logo.svg',
    favicon_url: '/branding/nasarawa/favicon.svg',
    support_email: 'support@nasarawastate.gov.ng',
    support_phone: '+234 700 0000 015',
    custom_domain: 'sos.nasarawastate.gov.ng',
    locales: ['en', 'ha'],
    default_locale: 'ha',
    pwa_theme_color: '#3f5a6b',
    pwa_name: 'Nasarawa One-Gov',
  },
  taraba: {
    tenant_state_id: 'taraba',
    display_name: 'Taraba State One-Gov Portal',
    portal_title: 'Taraba State One-Gov Portal',
    tagline: "Nature's gift to the nation, online for you.",
    primary_color: '#6b5a3f',
    secondary_color: '#52452f',
    logo_url: '/branding/taraba/logo.svg',
    favicon_url: '/branding/taraba/favicon.svg',
    support_email: 'support@tarabastate.gov.ng',
    support_phone: '+234 700 0000 016',
    custom_domain: 'sos.tarabastate.gov.ng',
    locales: ['en', 'ha'],
    default_locale: 'en',
    pwa_theme_color: '#6b5a3f',
    pwa_name: 'Taraba One-Gov',
  },
};

/** Deterministic fixture branding for a state (demo mode / fallback). */
export function fixtureBranding(stateId: StateId): Branding {
  return FIXTURES[stateId];
}

const BASE_URL: string = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '';

/** Live fetch from the control plane; null when unavailable/failed. */
async function fetchLiveBranding(stateId: StateId): Promise<Branding | null> {
  if (!BASE_URL) return null;
  try {
    const res = await fetch(`${BASE_URL}/cp/v1/tenants/${stateId}/branding`);
    if (!res.ok) return null;
    return (await res.json()) as Branding;
  } catch {
    return null;
  }
}

/**
 * Resolve branding for a state: live control-plane record when reachable,
 * otherwise the deterministic fixture (demo mode never depends on the backend).
 */
export async function fetchBranding(stateId: StateId): Promise<BrandingResult> {
  const live = await fetchLiveBranding(stateId);
  return live
    ? { branding: live, source: 'live' }
    : { branding: fixtureBranding(stateId), source: 'fixture' };
}

/** Initials monogram from the display name, used when logo_url is a placeholder. */
export function monogram(displayName: string): string {
  const words = displayName.split(/\s+/).filter(Boolean);
  return words
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join('');
}

/** True when the logo is a placeholder path (fixture logos ship no assets). */
export function isPlaceholderLogo(branding: Branding): boolean {
  return branding.logo_url.startsWith('/branding/');
}

/**
 * Apply branding to the document: CSS custom properties (the stylesheet
 * palette consumes --brand-primary / --brand-secondary), document.title,
 * favicon link, and the PWA theme-color meta. The static web app manifest
 * itself is build-time only; see README ("Whitelabeling").
 */
export function applyBranding(b: Branding, doc: Document = document): void {
  const root = doc.documentElement;
  root.style.setProperty('--brand-primary', b.primary_color);
  root.style.setProperty('--brand-secondary', b.secondary_color);
  doc.title = b.portal_title;

  // Placeholder fixture logos ship no asset; only swap when a real favicon is set.
  const favicon = doc.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (favicon && !isPlaceholderLogo(b)) favicon.href = b.favicon_url;

  const themeMeta = doc.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (themeMeta) themeMeta.content = b.pwa_theme_color;
}

export interface BrandingResult {
  branding: Branding;
  /** 'live' when served by the control plane, 'fixture' otherwise. */
  source: 'live' | 'fixture';
}

/**
 * Resolve (and apply) branding for the selected state. Live mode hits the
 * control-plane endpoint and fail-softs to fixtures; demo mode is fixture-only.
 */
export function useBranding(stateId: StateId | null): BrandingResult | null {
  const [result, setResult] = useState<BrandingResult | null>(null);

  useEffect(() => {
    if (!stateId) {
      setResult(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      const res = await fetchBranding(stateId);
      if (cancelled) return;
      applyBranding(res.branding);
      setResult(res);
    })();
    return () => {
      cancelled = true;
    };
  }, [stateId]);

  return result;
}
