import type { ClassItem } from '../data/mockData';

export type CourseSort = 'popular' | 'latest' | 'deadline' | 'priceAsc' | 'priceDesc';

function itemIsActionable(item: ClassItem) {
  return item.statusCode !== 'CLOSED' && !/^마감$/.test(item.status.trim());
}

export function sortCourseItems(items: ClassItem[], sortBy: CourseSort, now = Date.now()) {
  const copy = [...items];
  const timestamp = (...values: Array<string | null | undefined>) => {
    for (const value of values) {
      if (!value) continue;
      const parsed = Date.parse(value);
      if (Number.isFinite(parsed)) return parsed;
    }
    return 0;
  };
  const latestTimestamp = (item: ClassItem) => timestamp(item.updatedAt, item.createdAt, item.applyStart, item.startDate);
  const deadlineTimestamp = (item: ClassItem) => timestamp(item.applyEnd);
  const popularityScore = (item: ClassItem) => (
    item.popularityScore ?? ((item.favoriteCount ?? 0) * 3 + (item.viewCount ?? 0))
  );
  const statusPriority = (item: ClassItem) => {
    if (item.statusCode === 'DEADLINE') return 3;
    if (item.statusCode === 'OPEN') return 2;
    if (item.statusCode === 'SCHEDULED' || item.statusCode === 'WAITING') return 1;
    return 0;
  };
  const byLatest = (a: ClassItem, b: ClassItem) => latestTimestamp(b) - latestTimestamp(a) || a.id.localeCompare(b.id);

  if (sortBy === 'popular') {
    return copy.sort((a, b) => popularityScore(b) - popularityScore(a) || statusPriority(b) - statusPriority(a) || byLatest(a, b));
  }
  if (sortBy === 'latest') return copy.sort(byLatest);
  if (sortBy === 'priceAsc') return copy.sort((a, b) => a.price - b.price || byLatest(a, b));
  if (sortBy === 'priceDesc') return copy.sort((a, b) => b.price - a.price || byLatest(a, b));
  if (sortBy === 'deadline') {
    return copy.sort((a, b) => {
      const aDeadline = deadlineTimestamp(a);
      const bDeadline = deadlineTimestamp(b);
      const aActionable = aDeadline >= now && itemIsActionable(a);
      const bActionable = bDeadline >= now && itemIsActionable(b);
      if (aActionable !== bActionable) return aActionable ? -1 : 1;
      if (aActionable && bActionable && aDeadline !== bDeadline) return aDeadline - bDeadline;
      return statusPriority(b) - statusPriority(a) || byLatest(a, b);
    });
  }
  return copy.sort(byLatest);
}
