import { useCallback, useEffect, useRef, useState } from "react";

/** Load data, optionally re-polling. `deps` re-runs the load; `reload` re-runs it on demand. */
export function useLoad<T>(load: () => Promise<T>, deps: readonly unknown[], pollMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loadRef = useRef(load);
  loadRef.current = load;

  const reload = useCallback(async () => {
    try {
      setData(await loadRef.current());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    void reload();
    if (!pollMs) return;
    const t = setInterval(() => void reload(), pollMs);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, pollMs, reload]);

  return { data, error, loading, reload };
}

/** Seconds since the epoch, refreshed every `everyMs`, so "12s ago" labels keep moving. */
export function useNow(everyMs = 10_000): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), everyMs);
    return () => clearInterval(t);
  }, [everyMs]);
  return now;
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
