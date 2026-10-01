// Tiny path router (history API). Avoids a router dependency.
import { useEffect, useState } from 'react';

function current(): string {
  return location.pathname.replace(/\/+$/, '') || '/';
}

export function usePath(): string {
  const [path, setPath] = useState(current());
  useEffect(() => {
    const on = () => setPath(current());
    window.addEventListener('popstate', on);
    window.addEventListener('dawn:navigate', on);
    return () => { window.removeEventListener('popstate', on); window.removeEventListener('dawn:navigate', on); };
  }, []);
  return path;
}

export function navigate(to: string) {
  if (current() === to) return;
  history.pushState(null, '', to);
  window.dispatchEvent(new Event('dawn:navigate'));
}
