import '@testing-library/jest-dom';

// This jsdom setup doesn't expose Web Storage — Node logs "localStorage is not
// available because --localstorage-file was not provided" and `localStorage` is
// undefined, so component tests that persist exam progress crash in beforeEach.
// Provide a minimal in-memory Storage, but only when the environment hasn't
// already supplied one (so a real jsdom/CI localStorage is never clobbered).
class MemoryStorage implements Storage {
  private store = new Map<string, string>();
  get length(): number { return this.store.size; }
  clear(): void { this.store.clear(); }
  getItem(key: string): string | null {
    return this.store.has(key) ? (this.store.get(key) as string) : null;
  }
  setItem(key: string, value: string): void { this.store.set(key, String(value)); }
  removeItem(key: string): void { this.store.delete(key); }
  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null;
  }
}

for (const name of ['localStorage', 'sessionStorage'] as const) {
  const existing = (globalThis as Record<string, unknown>)[name];
  if (existing == null) {
    Object.defineProperty(globalThis, name, {
      value: new MemoryStorage(),
      configurable: true,
      writable: true,
    });
  }
}
