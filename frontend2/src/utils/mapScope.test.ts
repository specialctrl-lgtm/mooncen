import { describe, expect, it } from 'vitest';
import type { Branch, Course } from '../api';
import {
  branchCategories,
  branchMapModeCourseCount,
  branchMatchesMapMode,
  branchSourceIds,
  courseMatchesMapMode,
  expandBranchIds,
  isLocalGovernmentEducationBranch,
} from './mapScope';

describe('map scope helpers', () => {
  it('keeps culture-center, public education, and experience scopes exclusive', () => {
    const culture = { provider: 'LOTTE' } as Course;
    const education = {
      provider: 'PUBLIC',
      service_group: '공공강좌',
      branch: { id: 'city-hall', name: '광주광역시청', provider: 'PUBLIC' },
    } as Course;
    const experience = { provider: 'CULTURE_FACILITY' } as Course;

    expect(courseMatchesMapMode(culture, 'provider')).toBe(true);
    expect(courseMatchesMapMode(culture, 'education')).toBe(false);
    expect(courseMatchesMapMode(education, 'education')).toBe(true);
    expect(courseMatchesMapMode(experience, 'experience')).toBe(true);
    expect(courseMatchesMapMode(experience, 'education')).toBe(false);
  });

  it('does not classify metadata-only municipal records as culture centers', () => {
    const course = {
      provider: 'MUNI_CULTURE_CENTER',
      service_group: '문화센터',
      collection_category: '문화센터',
      domain_category: '문화센터',
    } as Course;
    const branch = {
      id: 'municipal-culture-center',
      name: '구미시근로자문화센터',
      provider: 'MUNI_CULTURE_CENTER',
      facility_service_group: '문화센터',
      facility_collection_category: '문화센터',
      service_groups: ['문화센터'],
      collection_categories: ['문화센터'],
      open_course_count: 3,
    } as Branch;

    expect(courseMatchesMapMode(course, 'provider')).toBe(false);
    expect(branchMatchesMapMode(branch, 'provider')).toBe(false);
  });

  it('keeps an aggregated branch when one source uses a fixed culture provider', () => {
    const branch = {
      id: 'aggregate',
      name: '공유 지점',
      provider: 'MUNI_PUBLIC',
      providers: ['MUNI_PUBLIC', 'LOTTE'],
      open_course_count: 2,
    } as Branch;

    expect(branchMatchesMapMode(branch, 'provider')).toBe(true);
  });

  it('expands aggregate branches into stable, unique source ids', () => {
    const aggregate = { id: 'group', branch_ids: ['a', 'b', 'a'] } as Branch;

    expect(branchSourceIds(aggregate)).toEqual(['a', 'b']);
    expect(expandBranchIds(['group', 'standalone'], [aggregate])).toEqual(['a', 'b', 'standalone']);
  });

  it.each(['체험', '견학', '탐방', '전시', '공연', '관람', '캠프'])(
    'classifies the %s program type as experience',
    (programType) => {
      const course = { provider: 'PUBLIC', program_type: programType } as Course;

      expect(courseMatchesMapMode(course, 'experience')).toBe(true);
      expect(courseMatchesMapMode(course, 'education')).toBe(false);
    },
  );

  it('does not treat a generic event label as experience by itself', () => {
    const course = {
      provider: 'MUNI_PUBLIC',
      service_group: '공공강좌',
      program_type: '행사',
      title: '플로리스트 자격 취득반',
      category_raw: '문화 > 문화',
      branch: { id: 'district-office', name: '동작구청', provider: 'MUNI_PUBLIC' },
    } as Course;

    expect(courseMatchesMapMode(course, 'experience')).toBe(false);
    expect(courseMatchesMapMode(course, 'education')).toBe(true);
  });

  it('routes ordinary library courses out of public education', () => {
    const course = {
      provider: 'MUNI_LIBRARY',
      source_group: 'library',
      service_group: '공공강좌',
      collection_category: '도서관',
      program_type: '강좌',
      title: '어린이 독서교실',
      branch: {
        id: 'library',
        provider: 'MUNI_LIBRARY',
        name: '중앙도서관',
        facility_category: null,
        facility_type: null,
      },
    } as Course;

    expect(courseMatchesMapMode(course, 'education')).toBe(false);
    expect(courseMatchesMapMode(course, 'experience')).toBe(true);
  });

  it('keeps a normalized public course at a district office in education', () => {
    const course = {
      provider: 'MUNI_PUBLIC',
      service_group: '공공강좌',
      collection_category: '공공예약',
      program_type: '공연',
      title: '길 위의 인문학 콘서트',
      branch: { id: 'district-office', name: '동작구청', provider: 'MUNI_PUBLIC' },
    } as Course;

    expect(courseMatchesMapMode(course, 'education')).toBe(true);
    expect(courseMatchesMapMode(course, 'experience')).toBe(false);
  });

  it('keeps explicit library experiences in the experience scope', () => {
    const course = {
      provider: 'MUNI_LIBRARY',
      source_group: 'library',
      service_group: '체험',
      collection_category: '도서관',
      program_type: '체험',
      title: '그림책 미술 체험',
    } as Course;

    expect(courseMatchesMapMode(course, 'experience')).toBe(true);
    expect(courseMatchesMapMode(course, 'education')).toBe(false);
  });

  it('keeps interpreter training and non-program reservations out of experience', () => {
    const training = {
      provider: 'PUBLIC',
      program_type: '해설',
      title: '생태해설사 양성과정',
      branch: { id: 'community-office', name: '초평동 행정복지센터', provider: 'PUBLIC' },
    } as Course;
    const lodging = {
      provider: 'PUBLIC',
      service_group: '체험',
      program_type: '숙박',
      category_raw: '생태체험교육장 캠핑장',
    } as Course;

    expect(courseMatchesMapMode(training, 'education')).toBe(true);
    expect(courseMatchesMapMode(lodging, 'experience')).toBe(false);
  });

  it('shows a mixed public branch in each scope with its own course count', () => {
    const branch = {
      id: 'public-experience',
      name: '중구청 체험교육장',
      provider: 'PUBLIC',
      service_group_counts: { 체험: 7, 공공강좌: 12 },
    } as Branch;

    expect(branchMatchesMapMode(branch, 'experience')).toBe(true);
    expect(branchMatchesMapMode(branch, 'education')).toBe(true);
    expect(branchMapModeCourseCount(branch, 'experience')).toBe(7);
    expect(branchMapModeCourseCount(branch, 'education')).toBe(12);
  });

  it('routes a merged library public-course count into experience', () => {
    const branch = {
      id: 'merged-library',
      name: '손기정문화도서관',
      provider: 'CULTURE_FACILITY',
      providers: ['CULTURE_FACILITY', 'MUNI_LIBRARY'],
      facility_source: 'culture-facilities.xlsx',
      service_group_counts: { 체험: 0, 공공강좌: 4 },
      collection_categories: ['공공예약', '체험'],
      open_course_count: 3,
    } as Branch;

    expect(branchMatchesMapMode(branch, 'education')).toBe(false);
    expect(branchMapModeCourseCount(branch, 'education')).toBe(0);
    expect(branchMatchesMapMode(branch, 'experience')).toBe(true);
    expect(branchMapModeCourseCount(branch, 'experience')).toBe(4);
  });

  it.each([
    '광주광역시청',
    '평창군청',
    '동작구청',
    '수택3동 주민자치센터',
    '초평동 행정복지센터',
    '반포2동 자치회관',
    '신림동사무소',
    '동구청 8층 시청각실',
  ])('includes the administrative facility %s in education', (name) => {
    const branch = {
      id: name,
      name,
      provider: 'PUBLIC',
      service_group_counts: { 공공강좌: 3 },
    } as Branch;

    expect(isLocalGovernmentEducationBranch(branch)).toBe(true);
    expect(branchMatchesMapMode(branch, 'education')).toBe(true);
    expect(branchMapModeCourseCount(branch, 'education')).toBe(3);
  });

  it.each([
    '중앙도서관',
    '김세중미술관',
    '서대문문화체육회관',
    '군포시청소년수련관',
    '포천시청년센터',
    '시청자미디어센터',
    '도서관 시청각실',
    '화성시청 도서관정책과',
  ])('excludes the non-administrative facility %s from education', (name) => {
    const branch = {
      id: name,
      name,
      provider: 'MUNI_PUBLIC',
      service_group_counts: { 공공강좌: 3 },
    } as Branch;

    expect(isLocalGovernmentEducationBranch(branch)).toBe(false);
    expect(branchMatchesMapMode(branch, 'education')).toBe(false);
    expect(branchMapModeCourseCount(branch, 'education')).toBe(0);
  });

  it('uses verified operator-office metadata for a generic municipal branch', () => {
    const branch = {
      id: 'suwon-city',
      name: '수원시',
      provider: 'SUWON_RESERV_EDUCATION',
      basic_info: {
        location_role: 'operating_organization',
        operator_address_backfill: {
          target_name: '수원시청',
          matched_name: '수원시청',
        },
      },
      service_group_counts: { 공공강좌: 4 },
    } as Branch;

    expect(isLocalGovernmentEducationBranch(branch)).toBe(true);
    expect(branchMatchesMapMode(branch, 'education')).toBe(true);
    expect(branchMapModeCourseCount(branch, 'education')).toBe(4);
  });

  it('classifies by the education institution while keeping the physical venue', () => {
    const branch = {
      id: 'suwon-bugukwon',
      name: '부국원 3층 교육실',
      provider: 'SUWON_RESERV_EDUCATION',
      address: '경기도 수원시 팔달구 향교로 130',
      basic_info: {
        location_role: 'course_venue',
        education_institution: '수원시',
      },
      service_group_counts: { 공공강좌: 4 },
    } as Branch;

    expect(isLocalGovernmentEducationBranch(branch)).toBe(true);
    expect(branchMatchesMapMode(branch, 'education')).toBe(true);
    expect(branchMapModeCourseCount(branch, 'education')).toBe(4);
  });

  it('still excludes a named facility owned by a municipality', () => {
    const branch = {
      id: 'suwon-arboretum',
      name: '일월수목원',
      provider: 'SUWON_RESERV_EDUCATION',
      basic_info: {
        location_role: 'course_venue',
        education_institution: '수원시',
      },
      service_group_counts: { 공공강좌: 2 },
    } as Branch;

    expect(isLocalGovernmentEducationBranch(branch)).toBe(false);
    expect(branchMatchesMapMode(branch, 'education')).toBe(false);
  });

  it('uses backend scope counts before facility-name heuristics', () => {
    const branch = {
      id: 'youngheung-arboretum',
      name: '영흥수목원',
      provider: 'SUWON_RESERV_EDUCATION',
      service_group_counts: { 공공강좌: 2 },
      scope_course_counts: { education: 2, experience: 0, provider: 0 },
    } as Branch;

    expect(branchMatchesMapMode(branch, 'education')).toBe(true);
    expect(branchMapModeCourseCount(branch, 'education')).toBe(2);
    expect(branchMatchesMapMode(branch, 'experience')).toBe(false);
    expect(branchMapModeCourseCount(branch, 'experience')).toBe(0);
  });

  it('exposes arboretum names as a natural-ecology map category', () => {
    const branch = {
      id: 'youngheung-arboretum',
      name: '영흥수목원',
      provider: 'SUWON_RESERV_EDUCATION',
    } as Branch;

    expect(branchCategories(branch)).toContain('자연·생태');
  });

  it('keeps excluded facilities out even when their operator is a city hall', () => {
    const branch = {
      id: 'suwon-library',
      name: '슬기샘도서관',
      provider: 'SUWON_LIBRARY_MD',
      basic_info: {
        operator_address_backfill: {
          target_name: '수원시청',
          matched_name: '수원시청',
        },
      },
      service_group_counts: { 공공강좌: 1 },
    } as Branch;

    expect(isLocalGovernmentEducationBranch(branch)).toBe(false);
    expect(branchMatchesMapMode(branch, 'education')).toBe(false);
  });

  it('falls back to course totals for a facility without service-group counts', () => {
    const branch = {
      id: 'facility',
      name: '체험 시설',
      provider: 'CULTURE_FACILITY',
      course_count: 5,
      active_course_count: 4,
      open_course_count: 3,
    } as Branch;

    expect(branchMapModeCourseCount(branch, 'experience')).toBe(3);
  });
});
