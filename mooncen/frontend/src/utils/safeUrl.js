function hasAsciiControlCharacter(value) {
    return Array.from(value).some((character) => {
        const code = character.charCodeAt(0);
        return code <= 0x20 || code === 0x7f;
    });
}

export function safeExternalUrl(value) {
    if (typeof value !== 'string') return '';
    const raw = value.trim();
    if (!raw || raw.length > 4096 || hasAsciiControlCharacter(raw)) return '';
    try {
        const url = new URL(raw);
        const allowedProtocol = url.protocol === 'http:' || url.protocol === 'https:';
        return allowedProtocol && url.hostname && !url.username && !url.password ? url.href : '';
    } catch {
        return '';
    }
}
