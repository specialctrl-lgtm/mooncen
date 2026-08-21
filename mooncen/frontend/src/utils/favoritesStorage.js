const FAVORITES_KEY = 'favorites';
const MAX_FAVORITES = 500;

export function readFavoriteIds() {
    try {
        const parsed = JSON.parse(localStorage.getItem(FAVORITES_KEY) || '[]');
        if (!Array.isArray(parsed)) return [];
        return [...new Set(
            parsed
                .filter((value) => typeof value === 'string')
                .map((value) => value.trim())
                .filter((value) => value.length > 0 && value.length <= 128)
                .slice(0, MAX_FAVORITES)
        )];
    } catch {
        localStorage.removeItem(FAVORITES_KEY);
        return [];
    }
}

export function writeFavoriteIds(courseIds) {
    const normalized = [...new Set(
        (Array.isArray(courseIds) ? courseIds : [])
            .filter((value) => typeof value === 'string')
            .map((value) => value.trim())
            .filter((value) => value.length > 0 && value.length <= 128)
            .slice(0, MAX_FAVORITES)
    )];
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(normalized));
    return normalized;
}
