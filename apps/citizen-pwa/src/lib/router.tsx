// Tiny hash router (no dependency). Routes: #/, #/services, #/request/:code,
// #/requests, #/pay, #/transparency, #/verify-deed, #/profile

import { useEffect, useState, type AnchorHTMLAttributes, type ReactNode } from 'react';

export function useHashRoute(): string {
  const [hash, setHash] = useState(() => window.location.hash.replace(/^#/, '') || '/');
  useEffect(() => {
    const onChange = () => setHash(window.location.hash.replace(/^#/, '') || '/');
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  return hash;
}

export function navigate(to: string): void {
  window.location.hash = to;
}

interface LinkProps extends AnchorHTMLAttributes<HTMLAnchorElement> {
  to: string;
  children: ReactNode;
}

export function Link({ to, children, ...rest }: LinkProps) {
  return (
    <a href={`#${to}`} {...rest}>
      {children}
    </a>
  );
}
