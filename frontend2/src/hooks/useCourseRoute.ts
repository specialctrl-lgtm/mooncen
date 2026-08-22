import { useEffect } from 'react';
import { fetchCourse } from '../api';
import { toClassItem, type ClassItem } from '../data/mockData';
import {
  COURSE_PATH_PREFIX,
  currentCourseIdFromUrl,
  writeCourseToUrl,
} from '../utils/courseRouting';

type UseCourseRouteOptions = {
  onSelectCourse: (item: ClassItem | null) => void;
  onNotice: (message: string) => void;
};

export function useCourseRoute({ onSelectCourse, onNotice }: UseCourseRouteOptions) {
  useEffect(() => {
    let alive = true;
    let loadedCourseId = '';

    const loadCourseFromUrl = () => {
      const courseId = currentCourseIdFromUrl();
      if (!courseId) {
        loadedCourseId = '';
        onSelectCourse(null);
        return;
      }
      if (loadedCourseId === courseId) return;
      loadedCourseId = courseId;
      fetchCourse(courseId)
        .then((course) => {
          if (!alive || loadedCourseId !== courseId) return;
          const item = toClassItem(course);
          onSelectCourse(item);
          if (!window.location.pathname.startsWith(COURSE_PATH_PREFIX)) {
            writeCourseToUrl(item, 'replace');
          }
        })
        .catch((error: unknown) => {
          if (!alive || loadedCourseId !== courseId) return;
          onNotice(error instanceof Error ? error.message : '강좌 상세를 불러오지 못했습니다.');
          writeCourseToUrl(null);
          loadedCourseId = '';
        });
    };

    loadCourseFromUrl();
    window.addEventListener('popstate', loadCourseFromUrl);
    return () => {
      alive = false;
      window.removeEventListener('popstate', loadCourseFromUrl);
    };
  }, [onNotice, onSelectCourse]);
}
