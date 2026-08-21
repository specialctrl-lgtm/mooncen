import { describe, expect, it } from 'vitest';
import type { Branch, Course } from '../api';
import { branchCoordinates } from './branchCoordinates';
import {
  mapBranchCourseCount,
  reconcileMapBranches,
  replaceNearbyBranchSnapshot,
  selectMapMarkerBranches,
} from './mapBranches';

const suwonCenter = {
  lat: 37.252,
  lon: 127.071,
  label: '수원',
  detected: false,
};

function courseAt(branch: Branch, overrides: Partial<Course> = {}): Course {
  return {
    id: overrides.id || 'course-1',
    provider: 'SUWON_RESERV_EDUCATION',
    title: '수목원 생태 체험',
    branch_id: branch.id,
    branch,
    service_group: '공공강좌',
    collection_category: '공공예약',
    domain_category: '수목원/생태',
    program_type: '교육',
    status: 'OPEN',
    ...overrides,
  };
}

describe('map branch reconciliation', () => {
  it('adds a searched course branch omitted by the nearby feed and makes it a marker', () => {
    const arboretum = {
      id: 'youngheung',
      name: '영흥수목원',
      provider: 'SUWON_RESERV_EDUCATION',
      address: '경기도 수원시 영통구 영통로 435',
      lat: 37.2521,
      lon: 127.0712,
    } as Branch;
    const branches = reconcileMapBranches([], [courseAt(arboretum)]);

    expect(branches).toHaveLength(1);
    expect(branches[0]).toMatchObject({
      id: 'youngheung',
      course_count: 1,
      open_course_count: 1,
      service_group_counts: { 공공강좌: 1 },
    });
    expect(branches[0].collection_categories).toContain('수목원/생태');

    const markers = selectMapMarkerBranches({
      branches,
      userLocation: suwonCenter,
      radiusKm: 10,
      providerFilters: ['SUWON_RESERV_EDUCATION'],
      categoryFilters: ['자연·생태', '수목원/생태'],
      categoryFilterActive: true,
      mapMode: 'experience',
      resultCourseCounts: { youngheung: 1 },
    });
    expect(markers.map((branch) => branch.id)).toEqual(['youngheung']);
  });

  it('matches a course source id to an already merged nearby branch without duplicating it', () => {
    const nearby = {
      id: 'canonical',
      branch_ids: ['canonical', 'source-youngheung'],
      providers: ['CULTURE_FACILITY', 'SUWON_RESERV_EDUCATION'],
      name: '영흥수목원',
      provider: 'CULTURE_FACILITY',
      lat: 37.2521,
      lon: 127.0712,
      facility_source: 'registry.xlsx',
    } as Branch;
    const source = {
      ...nearby,
      id: 'source-youngheung',
      branch_ids: undefined,
      provider: 'SUWON_RESERV_EDUCATION',
      facility_source: undefined,
    } as Branch;
    const branches = reconcileMapBranches([nearby], [courseAt(source)]);

    expect(branches).toHaveLength(1);
    expect(branches[0].branch_ids).toEqual(expect.arrayContaining(['canonical', 'source-youngheung']));
    expect(mapBranchCourseCount(branches[0], 'experience', { 'source-youngheung': 1 })).toBeGreaterThan(0);
  });

  it('uses current nearby data instead of keeping a stale object and retains only absent pinned rows', () => {
    const stale = {
      id: 'same', name: '기존 이름', provider: 'PUBLIC', lat: 37.25, lon: 127.07, course_count: 1,
    } as Branch;
    const current = {
      ...stale, name: '갱신된 이름', course_count: 9,
    } as Branch;
    const pinned = {
      id: 'pinned', name: '선택 지점', provider: 'PUBLIC', lat: 37.26, lon: 127.08,
    } as Branch;
    const dropped = {
      id: 'dropped', name: '오래된 지점', provider: 'PUBLIC', lat: 37.27, lon: 127.09,
    } as Branch;

    const result = replaceNearbyBranchSnapshot([stale, pinned, dropped], [current], ['pinned']);
    expect(result.map((branch) => branch.id)).toEqual(['same', 'pinned']);
    expect(result[0]).toMatchObject({ name: '갱신된 이름', course_count: 9 });
  });

  it('normalizes decimal-string coordinates and rejects malformed or out-of-range values', () => {
    expect(branchCoordinates({ lat: '37.2521', lon: '127.0712' } as unknown as Branch)).toEqual({
      lat: 37.2521,
      lon: 127.0712,
    });
    expect(branchCoordinates({ lat: 'NaN', lon: '127.0712' } as unknown as Branch)).toBeNull();
    expect(branchCoordinates({ lat: 127.0712, lon: 37.2521 } as Branch)).toBeNull();
  });

  it('trusts current scoped-result evidence when sparse branch metadata would otherwise hide a marker', () => {
    const sparse = {
      id: 'sparse',
      name: '운영기관 표기 없는 장소',
      provider: 'UNKNOWN_PUBLIC',
      lat: 37.2521,
      lon: 127.0712,
      course_count: 0,
      service_groups: [],
      collection_categories: [],
    } as Branch;

    expect(selectMapMarkerBranches({
      branches: [sparse],
      userLocation: suwonCenter,
      radiusKm: 10,
      providerFilters: [],
      categoryFilters: [],
      categoryFilterActive: true,
      mapMode: 'experience',
      resultCourseCounts: { sparse: 1 },
    })).toEqual([sparse]);
  });

  it('never creates marker candidates from courses with unusable coordinates', () => {
    const invalid = {
      id: 'invalid', name: '좌표 오류 지점', provider: 'PUBLIC', lat: null, lon: null,
    } as Branch;
    expect(reconcileMapBranches([], [courseAt(invalid)])).toEqual([]);
  });
});
