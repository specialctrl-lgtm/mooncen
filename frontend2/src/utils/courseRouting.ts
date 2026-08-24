import type { ClassItem } from '../data/mockData';

const COURSE_QUERY_PARAM = 'course';
export const COURSE_PATH_PREFIX = '/course/';

export function slugifyCourseText(value: string) {
  return value
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[^\p{Letter}\p{Number}]+/gu, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80) || 'course';
}

export function coursePath(item: Pick<ClassItem, 'id' | 'title' | 'center'>) {
  const slug = slugifyCourseText([item.title, item.center].filter(Boolean).join(' '));
  return `${COURSE_PATH_PREFIX}${encodeURIComponent(item.id)}/${encodeURIComponent(slug)}`;
}

export function currentCourseIdFromUrl() {
  const pathMatch = window.location.pathname.match(/^\/course\/([^/?#]+)/);
  if (pathMatch) return decodeURIComponent(pathMatch[1]);
  return new URLSearchParams(window.location.search).get(COURSE_QUERY_PARAM);
}

export function writeCourseToUrl(item: ClassItem | null, mode: 'push' | 'replace' = 'replace') {
  const url = new URL(window.location.href);
  if (item) {
    url.pathname = coursePath(item);
    url.searchParams.delete(COURSE_QUERY_PARAM);
  } else {
    url.pathname = '/';
    url.searchParams.delete(COURSE_QUERY_PARAM);
  }
  const nextUrl = `${url.pathname}${url.search}${url.hash}`;
  if (nextUrl === `${window.location.pathname}${window.location.search}${window.location.hash}`) return;
  window.history[mode === 'push' ? 'pushState' : 'replaceState']({}, '', nextUrl);
}

export function preferredScrollBehavior(): ScrollBehavior {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
}
