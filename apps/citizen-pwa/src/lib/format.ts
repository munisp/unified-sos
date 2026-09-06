/** Kobo → naira formatting. 100 kobo = ₦1. */
export function koboToNaira(kobo: number): number {
  return kobo / 100;
}

export function formatNaira(kobo: number): string {
  const naira = koboToNaira(kobo);
  return new Intl.NumberFormat('en-NG', {
    style: 'currency',
    currency: 'NGN',
    currencyDisplay: 'narrowSymbol',
    minimumFractionDigits: naira % 1 === 0 ? 0 : 2,
    maximumFractionDigits: 2,
  }).format(naira);
}

/** Pseudonymised hashes from mod-transparency are displayed truncated. */
export function truncateHash(hash: string, chars = 10): string {
  if (hash.length <= chars) return hash;
  return `${hash.slice(0, chars)}…`;
}

export function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-NG', { day: 'numeric', month: 'short', year: 'numeric' });
}
