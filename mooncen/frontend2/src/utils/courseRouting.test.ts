import { afterEach, describe, expect, it } from 'vitest';
import type { ClassItem } from '../data/mockData';
import {
  coursePath,
  currentCourseIdFromUrl,
  slugifyCourseText,
  writeCourseToUrl,
} from './courseRouting';

const originalUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;

afterEach(() => {
  window.history.replaceState({}, '', originalUrl || '/');
});

describe('course routing helpers', () => {
  it('creates a readable path while preserving the course id', () => {
    expect(slugifyCourseText('  우리 아이 미술!  ')).toBe('우리-아이-미술');
    expect(coursePath({ id: 'id/한글', title: '우리 아이 미술', center: '강남점' }))
      .toBe('/course/id%2F%ED%95%9C%EA%B8%80/%EC%9A%B0%EB%A6%AC-%EC%95%84%EC%9D%B4-%EB%AF%B8%EC%88%A0-%EA%B0%95%EB%82%A8%EC%A0%90');
  });

  it('reads both canonical course paths and the legacy query parameter', () => {
    window.history.replaceState({}, '', '/course/course%2F42/title');
    expect(currentCourseIdFromUrl()).toBe('course/42');

    window.history.replaceState({}, '', '/?course=legacy-7');
    expect(currentCourseIdFromUrl()).toBe('legacy-7');
  });

  it('writes and clears the selected course without dropping unrelated query values', () => {
    window.history.replaceState({}, '', '/?page=branches&course=old#results');
    writeCourseToUrl({
      id: 'course-9',
      title: '도예 수업',
      center: '본점',
    } as ClassItem, 'push');

    expect(window.location.pathname).toBe('/course/course-9/%EB%8F%84%EC%98%88-%EC%88%98%EC%97%85-%EB%B3%B8%EC%A0%90');
    expect(window.location.search).toBe('?page=branches');
    expect(window.location.hash).toBe('#results');

    writeCourseToUrl(null);
    expect(`${window.location.pathname}${window.location.search}${window.location.hash}`)
      .toBe('/?page=branches#results');
  });
});
