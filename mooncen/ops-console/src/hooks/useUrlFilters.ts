import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router';

export function useUrlFilters<T extends Record<string, string>>(namespace: string, defaults: T) {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => {
    const remembered = localStorage.getItem(`mooncen.ops.filters.${namespace}`);
    let parsed: Partial<T> = {};
    if (remembered) {
      try {
        parsed = JSON.parse(remembered) as Partial<T>;
      } catch {
        localStorage.removeItem(`mooncen.ops.filters.${namespace}`);
      }
    }
    return Object.fromEntries(
      Object.entries(defaults).map(([key, fallback]) => [key, params.get(key) ?? parsed[key as keyof T] ?? fallback]),
    ) as T;
  }, [defaults, namespace, params]);

  const update = useCallback(
    (changes: Partial<T>) => {
      const next = { ...filters, ...changes };
      const query = new URLSearchParams(params);
      for (const [key, value] of Object.entries(next)) {
        if (value && value !== defaults[key]) query.set(key, value);
        else query.delete(key);
      }
      localStorage.setItem(`mooncen.ops.filters.${namespace}`, JSON.stringify(next));
      setParams(query, { replace: true });
    },
    [defaults, filters, namespace, params, setParams],
  );
  return [filters, update] as const;
}
