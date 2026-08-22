import { useCallback, useEffect, useState } from 'react';
import { preferredScrollBehavior } from '../utils/courseRouting';

export function useAppRouting(onBeforeNavigate: () => void) {
  const [routePath, setRoutePath] = useState(() => `${window.location.pathname}${window.location.search}`);

  const navigateToPage = useCallback((path: string) => {
    onBeforeNavigate();
    const currentPath = `${window.location.pathname}${window.location.search}`;
    if (currentPath !== path) {
      window.history.pushState({}, '', path);
    }
    setRoutePath(path);
    window.scrollTo({ top: 0, behavior: preferredScrollBehavior() });
  }, [onBeforeNavigate]);

  useEffect(() => {
    const syncPath = () => setRoutePath(`${window.location.pathname}${window.location.search}`);
    window.addEventListener('popstate', syncPath);
    return () => window.removeEventListener('popstate', syncPath);
  }, []);

  return { routePath, navigateToPage };
}
