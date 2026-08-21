const ALLOWED_EXTERNAL_PROTOCOLS = new Set(['https:', 'http:']);

function hasAsciiControlCharacter(value: string): boolean {
  return Array.from(value).some((character) => {
    const code = character.charCodeAt(0);
    return code <= 0x20 || code === 0x7f;
  });
}

export function safeExternalUrl(value?: string | null): string | null {
  const raw = (value || '').trim();
  if (!raw || raw.length > 4096 || hasAsciiControlCharacter(raw)) return null;

  try {
    const parsed = new URL(raw);
    return ALLOWED_EXTERNAL_PROTOCOLS.has(parsed.protocol)
      && Boolean(parsed.hostname)
      && !parsed.username
      && !parsed.password
      ? parsed.href
      : null;
  } catch {
    return null;
  }
}

export function firstSafeExternalUrl(...values: Array<string | null | undefined>): string | null {
  for (const value of values) {
    const url = safeExternalUrl(value);
    if (url) return url;
  }
  return null;
}

export function openExternalUrl(value?: string | null): boolean {
  const url = safeExternalUrl(value);
  if (!url) return false;
  window.open(url, '_blank', 'noopener,noreferrer');
  return true;
}
