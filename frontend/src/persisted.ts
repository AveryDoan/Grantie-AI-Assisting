// A selection the officer made (a filter, a tab, ticked rows, form fields) is kept in this browser tab's session storage, so it
// survives a reload and coming back to the screen. It holds choices and references only, never application text or personal details,
// and it is cleared when the tab closes. Storage can be blocked: everything still works without it.
import { useCallback, useEffect, useState } from "react";

const PREFIX = "grantie.sel.";

export function readSelection<T>(key: string, fallback: T): T {
  try {
    const raw = sessionStorage.getItem(PREFIX + key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch { return fallback; }
}

export function usePersisted<T>(key: string, initial: T): [T, (v: T | ((p: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => readSelection(key, initial));
  useEffect(() => {
    try { sessionStorage.setItem(PREFIX + key, JSON.stringify(value)); } catch { /* a remembered choice is only a convenience */ }
  }, [key, value]);
  return [value, useCallback((v) => setValue(v), [])];
}

/** Sets are stored as arrays. */
export function usePersistedSet(key: string): [Set<string>, (v: Set<string>) => void] {
  const [arr, setArr] = usePersisted<string[]>(key, []);
  return [new Set(arr), (s) => setArr([...s])];
}
