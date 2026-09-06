// Minimal i18n seam. English is complete; yo/ha/ig are stubbed locales that
// fall back to English keys. Real translations plug in by replacing the stubs.

export type Locale = 'en' | 'yo' | 'ha' | 'ig';

const en = {
  appName: 'SOS Citizen',
  chooseState: 'Choose your state',
  home: 'Home',
  catalog: 'Services',
  myRequests: 'My requests',
  payments: 'Payments',
  transparency: 'Transparency',
  verifyDeed: 'Verify C-of-O',
  profile: 'Profile',
  submit: 'Submit request',
  offlineNotice: 'You are offline. Submissions will be queued and sent when you reconnect.',
  demoMode: 'Demo mode — live services unreachable, showing sample data.',
  ussdAlt: 'No smartphone? Use USSD:',
  wallet: 'Wallet',
  kycStatus: 'KYC status',
} as const;

export type MessageKey = keyof typeof en;

// Stubbed locales: partial maps; missing keys fall back to English.
const yo: Partial<Record<MessageKey, string>> = {};
const ha: Partial<Record<MessageKey, string>> = {};
const ig: Partial<Record<MessageKey, string>> = {};

export const LOCALES: Record<Locale, Partial<Record<MessageKey, string>>> = { en, yo, ha, ig };

export function t(key: MessageKey, locale: Locale = 'en'): string {
  return LOCALES[locale][key] ?? en[key];
}
