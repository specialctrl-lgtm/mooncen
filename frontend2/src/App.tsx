import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { RotateCcw, X } from 'lucide-react';
import type { Branch, Course, ProviderMeta, UserNotification } from './api';
import {
  fetchCourseMarks,
  fetchCourses,
  fetchMarkedCourses,
  fetchNearbyBranches,
  fetchProviders,
  fetchUserNotifications,
  removeCourseMark,
  requestCourseUpdate,
  saveCourseMark,
} from './api';
import Header from './components/Header';
import Sidebar from './components/Sidebar';
import MapSection from './components/MapSection';
import ClassCard from './components/ClassCard';
import PopularClassCard from './components/PopularClassCard';
import CourseDetailModal from './components/CourseDetailModal';
import LoginModal from './components/LoginModal';
import NotificationPanel from './components/NotificationPanel';
import NearbyCenterMap from './components/NearbyCenterMap';
import LocationPickerModal from './components/LocationPickerModal';
import CourseLocationModal from './components/CourseLocationModal';
import ProviderIcon from './components/ProviderIcon';
import AccountModal from './components/AccountModal';
import BugReportModal from './components/BugReportModal';
import { useDialogAccessibility } from './hooks/useDialogAccessibility';
import { useMooncenSeo } from './seo';
import { branchDisplayName } from './utils/branchDisplay';
import { firstSafeExternalUrl, openExternalUrl } from './utils/safeUrl';
import { sortCourseItems, type CourseSort } from './utils/courseSort';
import {
  categoryFilterValuesForMode,
  defaultCategoryLabelsForMode,
} from './utils/categoryTaxonomy';
import {
  clearStoredUser,
  deleteStoredAccount,
  finishOAuthLoginFromUrl,
  getStoredUser,
  logoutStoredUser,
  refreshStoredUser,
  startOAuthLogin,
  type AuthProvider,
  type AuthUser,
} from './auth';
import { type ClassItem, toClassItem, toPopularItems } from './data/mockData';
import CourseComparePanel from './components/CourseComparePanel';
import QuickDateCalendar from './components/QuickDateCalendar';
import { fallbackProviderMetas } from './data/fallbackProviders';
import { useAppRouting } from './hooks/useAppRouting';
import { useCourseRoute } from './hooks/useCourseRoute';
import { useMaxViewportWidth } from './hooks/useMaxViewportWidth';
import { useUserLocation } from './hooks/useUserLocation';
import { monthFromDateValue } from './utils/calendar';
import {
  COURSE_PAGE_SIZE,
  MAX_COMPARE_ITEMS,
  allDays,
  allFees,
  allStatuses,
  allTimes,
  apiDays,
  applyMultiValueFilter,
  applyStatusFilter,
  branchSubFilterActive,
  buildAgeFilterOptions,
  buildFilterOptions,
  classItemAgeValues,
  classItemCategoryValues,
  debugMode,
  defaultStatusFilters,
  expandedStatusFilters,
  expandAgeFilterValues,
  inferTimeBuckets,
  interleaveItemsByBranch,
  matchesBranchSubFilters,
  matchesChildAge,
  normalizedScheduleDays,
  quickTimeLabels,
  sameStringSet,
  toggleSpecificFilterValue,
  type BranchSubFilters,
} from './utils/courseFilters';
import { writeCourseToUrl } from './utils/courseRouting';
import {
  DEFAULT_MAP_DIAMETER_KM,
  diameterToRadiusKm,
  distanceKm,
  filterBranchesWithinRadius,
  type MapDiameterKm,
  type UserLocation,
} from './utils/location';
import {
  branchIdMatches,
  branchMatchesMapMode,
  branchSourceIds,
  courseMatchesMapMode,
  expandBranchIds,
  isCultureProviderOnlySelection,
  nearbyScopeTitle,
  resultScopeLabelFor,
  scopeModeLabel,
  type MapMode,
} from './utils/mapScope';
import {
  reconcileMapBranches,
  replaceNearbyBranchSnapshot,
  selectMapMarkerBranches,
} from './utils/mapBranches';

type SortBy = CourseSort;
type ViewMode = 'all' | 'favorites' | 'applied';
type BranchCourseGroup = {
  key: string;
  center: string;
  provider: string;
  providerLabel: string;
  source: ClassItem['source'];
  items: ClassItem[];
  totalCount?: number;
  aggregate?: boolean;
};
type BranchGroupInitialSnapshot = {
  queryKey: string;
  totalCount: number;
  hiddenCount: number;
};
type ResultFilterChip = {
  key: string;
  label: string;
  onRemove: () => void;
};

function useStableValueByKey<T>(value: T, key: string): T {
  const stableRef = useRef({ key, value });
  if (stableRef.current.key !== key) {
    stableRef.current = { key, value };
  }
  return stableRef.current.value;
}

const feeFilterLabels: Record<string, string> = {
  free: '무료',
  under50000: '5만원 이하',
  under100000: '10만원 이하',
  over100000: '10만원 초과',
};
const statusFilterLabels: Record<string, string> = {
  OPEN: '접수중',
  SCHEDULED: '접수예정',
  DEADLINE: '마감임박',
  WAITING: '대기접수',
  CLOSED: '접수마감',
};

function summarizeFilterValues(values: string[], labels: Record<string, string>) {
  const displayValues = values.map((value) => labels[value] || value);
  if (displayValues.length <= 2) return displayValues.join('·');
  return `${displayValues[0]} 외 ${displayValues.length - 1}개`;
}

function MooncenHomeApp() {
  const isMobileViewport = useMaxViewportWidth(760);
  const [activeCategory, setActiveCategory] = useState('all');
  const [keyword, setKeyword] = useState('');
  const [searchKeyword, setSearchKeyword] = useState('');
  const [courses, setCourses] = useState<Course[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [providerMetas, setProviderMetas] = useState<ProviderMeta[]>(fallbackProviderMetas);
  const [total, setTotal] = useState(0);
  const [categoryFilters, setCategoryFilters] = useState<string[] | null>(null);
  const [ageFilters, setAgeFilters] = useState<string[] | null>(null);
  const [branchFilters, setBranchFilters] = useState<string[] | null>(null);
  const [childAgeMonths, setChildAgeMonths] = useState('');
  const [childAgeYears, setChildAgeYears] = useState('');
  const [selectedDate, setSelectedDate] = useState('');
  const [dayFilters, setDayFilters] = useState<string[]>(allDays);
  const [timeFilters, setTimeFilters] = useState<string[]>(allTimes);
  const [providerFilters, setProviderFilters] = useState<string[]>(() =>
    fallbackProviderMetas.map((provider) => provider.provider),
  );
  const [feeFilters, setFeeFilters] = useState<string[]>(allFees);
  const [statusFilters, setStatusFilters] = useState<string[]>(debugMode ? allStatuses : defaultStatusFilters);
  const [mobileFilterOpen, setMobileFilterOpen] = useState(false);
  const [locationPickerOpen, setLocationPickerOpen] = useState(false);
  const [sortBy, setSortBy] = useState<SortBy>('popular');
  const [viewMode, setViewMode] = useState<ViewMode>('all');
  const [mapMode, setMapMode] = useState<MapMode>('provider');
  const [nearbyDiameterKm, setNearbyDiameterKm] = useState<MapDiameterKm>(DEFAULT_MAP_DIAMETER_KM);
  const nearbyRadiusKm = diameterToRadiusKm(nearbyDiameterKm);
  const [favoriteIds, setFavoriteIds] = useState<Set<string>>(() => new Set());
  const [appliedIds, setAppliedIds] = useState<Set<string>>(() => new Set());
  const [savedCourses, setSavedCourses] = useState<Course[]>([]);
  const [savedTotal, setSavedTotal] = useState(0);
  const [savedLoading, setSavedLoading] = useState(false);
  const [authUser, setAuthUser] = useState<AuthUser | null>(() => getStoredUser());
  const [loginOpen, setLoginOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [bugReportOpen, setBugReportOpen] = useState(false);
  const [accountDeleting, setAccountDeleting] = useState(false);
  const [notificationOpen, setNotificationOpen] = useState(false);
  const [filterCloseSignal, setFilterCloseSignal] = useState(0);
  const [notifications, setNotifications] = useState<UserNotification[]>([]);
  const [notificationCount, setNotificationCount] = useState(0);
  const [notificationsLoading, setNotificationsLoading] = useState(false);
  const [missingLoginConfig, setMissingLoginConfig] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [courseRetrySignal, setCourseRetrySignal] = useState(0);
  const [coursePage, setCoursePage] = useState(1);
  const [hasMoreCourses, setHasMoreCourses] = useState(true);
  const [branchGroupVisibleCounts, setBranchGroupVisibleCounts] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);
  const [selectedBranch, setSelectedBranch] = useState<Branch | null>(null);
  const [hoveredMapBranchId, setHoveredMapBranchId] = useState<string | null>(null);
  const [selectedCourse, setSelectedCourse] = useState<ClassItem | null>(null);
  const [locationCourse, setLocationCourse] = useState<ClassItem | null>(null);
  const [compareItems, setCompareItems] = useState<ClassItem[]>([]);
  const [mapSearchCenter, setMapSearchCenter] = useState<UserLocation | null>(null);
  const [visibleMapBranchIds, setVisibleMapBranchIds] = useState<string[]>([]);
  const [branchQuickFilterOpen, setBranchQuickFilterOpen] = useState<string | null>(null);
  const [branchSubFilters, setBranchSubFilters] = useState<Record<string, BranchSubFilters>>({});
  const [quickCalendarMonth, setQuickCalendarMonth] = useState(() => monthFromDateValue(''));
  const mobileFilterDialogRef = useDialogAccessibility<HTMLDivElement>(mobileFilterOpen, () => setMobileFilterOpen(false));
  const closeTransientFilters = useCallback(() => {
    setBranchQuickFilterOpen(null);
    setMobileFilterOpen(false);
    setLocationPickerOpen(false);
    setLocationCourse(null);
    setFilterCloseSignal((value) => value + 1);
  }, []);
  const { routePath, navigateToPage } = useAppRouting(closeTransientFilters);
  const resetLocationContext = useCallback(() => {
    setMapSearchCenter(null);
    setSelectedBranch(null);
  }, []);
  const {
    userLocation,
    usingCurrentLocation: useCurrentLocation,
    locating,
    locationReady,
    locationError,
    setLocationError,
    requestCurrentLocation,
    stopUsingCurrentLocation,
  } = useUserLocation({
    onContextReset: resetLocationContext,
    onNotice: setNotice,
  });
  const distanceReferenceLocation = mapSearchCenter ?? userLocation;
  useCourseRoute({
    onSelectCourse: setSelectedCourse,
    onNotice: setNotice,
  });

  useMooncenSeo({ keyword: searchKeyword, total, selectedBranch, selectedCourse });
  const providerOptions = useMemo(
    () => providerMetas.map((provider) => ({ value: provider.provider, label: provider.label || provider.provider })),
    [providerMetas],
  );
  const allProviders = useMemo(() => providerOptions.map((provider) => provider.value), [providerOptions]);
  const providerFilterActiveForMode = useMemo(
    () => !sameStringSet(providerFilters, allProviders)
      && !(mapMode !== 'provider' && isCultureProviderOnlySelection(providerFilters)),
    [allProviders, mapMode, providerFilters],
  );
  const effectiveProviderFiltersForMode = providerFilterActiveForMode ? providerFilters : allProviders;
  const mapBranches = useMemo(
    () => reconcileMapBranches(branches, courses),
    [branches, courses],
  );
  const lastCourseQueryKey = useRef('');
  const lastCourseRequestKey = useRef('');
  const branchGroupInitialSnapshotRef = useRef<Record<string, BranchGroupInitialSnapshot>>({});
  const branchGroupStableOrderRef = useRef<Record<string, string[]>>({});
  const handleVisibleBranchIdsChange = useCallback((branchIds: string[]) => {
    const normalized = [...new Set(branchIds)].sort();
    setVisibleMapBranchIds((prev) => (prev.join(',') === normalized.join(',') ? prev : normalized));
  }, []);
  const handleMapCenterChange = useCallback((lat: number, lon: number) => {
    setMapSearchCenter((prev) => {
      const reference = prev ?? userLocation;
      const movedKm = distanceKm(reference, { id: 'map-center', name: 'map-center', provider: 'MAP', lat, lon });
      if (movedKm < 0.05) return prev;
      return { lat, lon, label: '지도 중심 기준', detected: false };
    });
  }, [userLocation]);

  const updateBranchSubFilter = useCallback((groupKey: string, patch: BranchSubFilters) => {
    setBranchSubFilters((prev) => {
      const nextFilters: BranchSubFilters = { ...(prev[groupKey] ?? {}), ...patch };
      Object.entries(nextFilters).forEach(([key, value]) => {
        if (!value) delete nextFilters[key as keyof BranchSubFilters];
      });

      const next = { ...prev };
      if (branchSubFilterActive(nextFilters)) next[groupKey] = nextFilters;
      else delete next[groupKey];
      return next;
    });
  }, []);

  useEffect(() => {
    if (!branchQuickFilterOpen) return undefined;

    const closeQuickFilter = (event: MouseEvent | TouchEvent) => {
      const target = event.target;
      if (target instanceof Element && target.closest('.branch-quick-filter')) return;
      setBranchQuickFilterOpen(null);
    };

    document.addEventListener('mousedown', closeQuickFilter);
    document.addEventListener('touchstart', closeQuickFilter);
    return () => {
      document.removeEventListener('mousedown', closeQuickFilter);
      document.removeEventListener('touchstart', closeQuickFilter);
    };
  }, [branchQuickFilterOpen]);

  useEffect(() => {
    let alive = true;
    const fallbackValues = fallbackProviderMetas.map((provider) => provider.provider);

    fetchProviders()
      .then((providers) => {
        if (!alive) return;
        const nextProviders = providers.length ? providers : fallbackProviderMetas;
        const nextValues = nextProviders.map((provider) => provider.provider);
        setProviderMetas(nextProviders);
        setProviderFilters((prev) => {
          const prevWasFallbackAll =
            prev.length === fallbackValues.length && fallbackValues.every((provider) => prev.includes(provider));
          if (!prev.length || prevWasFallbackAll) return nextValues;
          const kept = prev.filter((provider) => nextValues.includes(provider));
          return kept.length ? kept : nextValues;
        });
      })
      .catch(() => {
        if (!alive) return;
        setProviderMetas(fallbackProviderMetas);
      });

    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    finishOAuthLoginFromUrl()
      .then((user) => {
        if (!alive || !user) return;
        setAuthUser(user);
        setNotice(`${user.provider === 'google' ? 'Google' : 'Naver'} 로그인되었습니다.`);
      })
      .catch((err: unknown) => {
        if (!alive) return;
        setNotice(err instanceof Error ? err.message : '소셜 로그인 처리에 실패했습니다.');
        window.history.replaceState({}, document.title, window.location.pathname);
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    refreshStoredUser()
      .then((user) => {
        if (alive) setAuthUser(user);
      })
      .catch(() => {});

    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const handleExpiredAuth = () => {
      setAuthUser(null);
      setFavoriteIds(new Set());
      setAppliedIds(new Set());
      setSavedCourses([]);
      setSavedTotal(0);
      setNotifications([]);
      setNotificationCount(0);
      setNotificationOpen(false);
      setBugReportOpen(false);
      setNotice('로그인이 만료되었습니다. 다시 로그인해 주세요.');
    };
    window.addEventListener('mooncen:auth-expired', handleExpiredAuth);
    return () => window.removeEventListener('mooncen:auth-expired', handleExpiredAuth);
  }, []);

  const applyCourseMarksResponse = useCallback((marks: { favorite_course_ids: string[]; applied_course_ids: string[] }) => {
    setFavoriteIds(new Set(marks.favorite_course_ids));
    setAppliedIds(new Set(marks.applied_course_ids));
  }, []);

  const requireLoginForUserCourse = useCallback(() => {
    closeTransientFilters();
    setNotice('로그인이 필요한 기능입니다.');
    setMissingLoginConfig(null);
    setLoginOpen(true);
  }, [closeTransientFilters]);

  const handleShowBugReport = useCallback(() => {
    closeTransientFilters();
    if (!authUser) {
      setMissingLoginConfig(null);
      setNotice('버그 제보는 로그인 후 이용할 수 있습니다.');
      setLoginOpen(true);
      return;
    }
    setBugReportOpen(true);
  }, [authUser, closeTransientFilters]);

  const refreshCourseMarks = useCallback(async () => {
    if (!authUser) {
      setFavoriteIds(new Set());
      setAppliedIds(new Set());
      setSavedCourses([]);
      setSavedTotal(0);
      return;
    }

    try {
      const marks = await fetchCourseMarks();
      applyCourseMarksResponse(marks);
    } catch (err) {
      setFavoriteIds(new Set());
      setAppliedIds(new Set());
      setNotice(err instanceof Error ? err.message : '저장된 강좌를 불러오지 못했습니다.');
    }
  }, [applyCourseMarksResponse, authUser]);

  const refreshNotifications = useCallback(async () => {
    if (!authUser) {
      setNotifications([]);
      setNotificationCount(0);
      setNotificationsLoading(false);
      return;
    }

    setNotificationsLoading(true);
    try {
      const response = await fetchUserNotifications(50);
      setNotifications(response.items);
      setNotificationCount(response.unread_count);
    } catch (err) {
      setNotifications([]);
      setNotificationCount(0);
      if (notificationOpen) {
        setNotice(err instanceof Error ? err.message : '알림을 불러오지 못했습니다.');
      }
    } finally {
      setNotificationsLoading(false);
    }
  }, [authUser, notificationOpen]);

  useEffect(() => {
    refreshCourseMarks();
  }, [refreshCourseMarks]);

  useEffect(() => {
    refreshNotifications();
  }, [refreshNotifications]);

  const branchOptions = useMemo(() => {
    const visibleSet = new Set(visibleMapBranchIds);
    const pinnedSet = new Set(branchFilters ?? []);
    if (selectedBranch) pinnedSet.add(selectedBranch.id);
    return mapBranches
      .filter((branch) => visibleSet.has(branch.id) || pinnedSet.has(branch.id))
      .sort((a, b) => branchDisplayName(a).localeCompare(branchDisplayName(b), 'ko-KR'))
      .map((branch) => {
        const displayName = branchDisplayName(branch);
        const providerName = branch.provider_label || branch.provider;
        return {
          value: branch.id,
          label: providerName && providerName !== displayName ? `${displayName} (${providerName})` : displayName,
        };
      });
  }, [branchFilters, mapBranches, selectedBranch, visibleMapBranchIds]);

  const effectiveBranchFilters = useMemo(
    () => branchFilters ?? branchOptions.map((option) => option.value),
    [branchFilters, branchOptions],
  );
  const effectiveMapBranchIds = useMemo(
    () => {
      if (selectedBranch) return [];
      return branchFilters ?? [];
    },
    [branchFilters, selectedBranch],
  );
  const keywordSearchActive = Boolean(searchKeyword);
  const courseBranchIds = effectiveMapBranchIds;
  const selectedBranchSourceIds = useMemo(() => branchSourceIds(selectedBranch), [selectedBranch]);
  const expandedCourseBranchIds = useMemo(
    () => expandBranchIds(courseBranchIds, mapBranches),
    [courseBranchIds, mapBranches],
  );
  const effectiveMapBranchKey = expandedCourseBranchIds.join(',');

  const courseQueryKey = useMemo(
    () => JSON.stringify({
      activeCategory,
      categoryFilters,
      dayFilters,
      feeFilters,
      ageFilters,
      childAgeMonths,
      childAgeYears,
      keyword: searchKeyword,
      locationReady,
      mapMode,
      providerFilters: providerFilterActiveForMode ? [...providerFilters].sort() : null,
      selectedDate,
      sortBy,
      statusFilters,
      timeFilters,
      selectedBranchIds: selectedBranchSourceIds,
      effectiveMapBranchKey,
      mapCenterLat: distanceReferenceLocation.lat,
      mapCenterLon: distanceReferenceLocation.lon,
      nearbyRadiusKm,
    }),
    [activeCategory, ageFilters, categoryFilters, childAgeMonths, childAgeYears, dayFilters, distanceReferenceLocation.lat, distanceReferenceLocation.lon, effectiveMapBranchKey, feeFilters, locationReady, mapMode, nearbyRadiusKm, providerFilterActiveForMode, providerFilters, searchKeyword, selectedDate, selectedBranchSourceIds, sortBy, statusFilters, timeFilters],
  );
  const apiCategoryFilters = useMemo(
    () => (categoryFilters ? categoryFilterValuesForMode(categoryFilters, mapMode) : undefined),
    [categoryFilters, mapMode],
  );
  const apiAgeFilters = useMemo(
    () => (ageFilters ? expandAgeFilterValues(ageFilters) : undefined),
    [ageFilters],
  );
  const courseRequestEnabled = debugMode
    || ((locationReady || keywordSearchActive) && providerFilters.length > 0 && feeFilters.length > 0);
  const courseRequestQueryCandidate = useMemo(
    () => debugMode
      ? {
          page: coursePage,
          size: COURSE_PAGE_SIZE,
          keyword: searchKeyword,
          courseDate: selectedDate,
          includeInactive: true,
        }
      : {
          page: coursePage,
          size: COURSE_PAGE_SIZE,
          keyword: searchKeyword,
          category: activeCategory === 'all' ? undefined : activeCategory,
          collectionCategories: apiCategoryFilters,
          scope: mapMode,
          provider: providerFilterActiveForMode ? providerFilters.join(',') : undefined,
          branchIds: selectedBranch
            ? selectedBranchSourceIds
            : expandedCourseBranchIds.length
              ? expandedCourseBranchIds
              : undefined,
          lat: distanceReferenceLocation.lat,
          lon: distanceReferenceLocation.lon,
          radiusKm: nearbyRadiusKm,
          feeGroups: feeFilters.length === allFees.length ? undefined : feeFilters,
          ageGroups: apiAgeFilters,
          timeGroups: timeFilters.length === allTimes.length ? undefined : timeFilters,
          statuses: statusFilters.length === allStatuses.length ? undefined : statusFilters,
          childAgeMonths: childAgeMonths ? Number(childAgeMonths) : childAgeYears ? Number(childAgeYears) * 12 : undefined,
          days: dayFilters.length === allDays.length ? undefined : dayFilters.filter((day) => apiDays.includes(day)),
          courseDate: selectedDate,
          sort: sortBy === 'priceAsc' ? 'price_asc' as const : sortBy === 'priceDesc' ? 'price_desc' as const : sortBy,
        },
    [activeCategory, apiAgeFilters, apiCategoryFilters, childAgeMonths, childAgeYears, coursePage, dayFilters, distanceReferenceLocation.lat, distanceReferenceLocation.lon, expandedCourseBranchIds, feeFilters, mapMode, nearbyRadiusKm, providerFilterActiveForMode, providerFilters, searchKeyword, selectedBranch, selectedBranchSourceIds, selectedDate, sortBy, statusFilters, timeFilters],
  );
  const courseRequestQueryKey = useMemo(
    () => JSON.stringify(courseRequestQueryCandidate),
    [courseRequestQueryCandidate],
  );
  const courseRequestQuery = useStableValueByKey(courseRequestQueryCandidate, courseRequestQueryKey);

  useEffect(() => {
    let alive = true;
    let requestSettled = false;
    const isNewQuery = lastCourseQueryKey.current !== courseQueryKey;

    if (isNewQuery) {
      lastCourseQueryKey.current = courseQueryKey;
      setBranchGroupVisibleCounts({});
      branchGroupStableOrderRef.current = {};
      setCourses([]);
      setTotal(0);
      setHasMoreCourses(true);
      if (coursePage !== 1) {
        setCoursePage(1);
        setLoading(true);
        setLoadingMore(false);
        return () => {
          alive = false;
        };
      }
    }

    if (!courseRequestEnabled) {
      lastCourseRequestKey.current = '';
      setCourses([]);
      setTotal(0);
      setHasMoreCourses(false);
      setLoading(false);
      setLoadingMore(false);
      return () => {
        alive = false;
      };
    }

    const requestKey = `${courseQueryKey}\u001f${coursePage}\u001f${courseRetrySignal}`;
    if (lastCourseRequestKey.current === requestKey) {
      return () => {
        alive = false;
      };
    }
    lastCourseRequestKey.current = requestKey;

    if (coursePage === 1 && isNewQuery) setLoading(true);
    if (coursePage > 1) setLoadingMore(true);
    setError(null);

    const controller = new AbortController();
    // Let mount-time state reconciliation finish before issuing the request.
    // This also prevents React StrictMode's discarded effect pass from sending
    // an identical API call that is immediately aborted.
    const startRequestTimer = window.setTimeout(() => {
      const courseRequest = fetchCourses(courseRequestQuery, { signal: controller.signal });

      courseRequest
        .then((courseResponse) => {
          if (!alive) return;
          setCourses((prev) => {
            if (coursePage === 1) return courseResponse.items;
            const byId = new Map(prev.map((course) => [course.id, course]));
            courseResponse.items.forEach((course) => byId.set(course.id, course));
            return [...byId.values()];
          });
          setTotal(courseResponse.total);
          setHasMoreCourses(coursePage * COURSE_PAGE_SIZE < courseResponse.total);
        })
        .catch((err: unknown) => {
          if (!alive) return;
          if (err instanceof DOMException && err.name === 'AbortError') return;
          setError(err instanceof Error ? err.message : '데이터를 불러오지 못했습니다.');
        })
        .finally(() => {
          requestSettled = true;
          if (alive) {
            setLoading(false);
            setLoadingMore(false);
          }
        });
    }, 0);

    return () => {
      alive = false;
      window.clearTimeout(startRequestTimer);
      controller.abort();
      if (!requestSettled && lastCourseRequestKey.current === requestKey) {
        lastCourseRequestKey.current = '';
      }
    };
  }, [coursePage, courseQueryKey, courseRequestEnabled, courseRequestQuery, courseRetrySignal]);

  useEffect(() => {
    let alive = true;

    if (!debugMode && !locationReady) {
      setBranches([]);
      setVisibleMapBranchIds([]);
      return () => {
        alive = false;
      };
    }

    const searchCenter = mapSearchCenter ?? userLocation;

    fetchNearbyBranches(searchCenter.lat, searchCenter.lon, nearbyRadiusKm, mapMode !== 'provider')
      .then((branchResponse) => {
        if (!alive) return;
        const nearbyBranches = filterBranchesWithinRadius(searchCenter, branchResponse, nearbyRadiusKm);
        setBranches((prevBranches) => {
          const selectedIds = new Set(branchFilters ?? []);
          branchSourceIds(selectedBranch).forEach((branchId) => selectedIds.add(branchId));
          return replaceNearbyBranchSnapshot(prevBranches, nearbyBranches, selectedIds);
        });
      })
      .catch((err: unknown) => {
        if (!alive) return;
        setLocationError(err instanceof Error ? err.message : '지점 데이터를 불러오지 못했습니다.');
      });

    return () => {
      alive = false;
    };
  }, [branchFilters, locationReady, mapMode, mapSearchCenter, nearbyRadiusKm, selectedBranch, setLocationError, useCurrentLocation, userLocation]);

  useEffect(() => {
    if (!notice) return undefined;
    const timer = window.setTimeout(() => setNotice(null), 2600);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const handleToggleFavorite = useCallback(async (item: ClassItem) => {
    if (!authUser) {
      requireLoginForUserCourse();
      return;
    }

    const wasFavorite = favoriteIds.has(item.id);
    setFavoriteIds((prev) => {
      const next = new Set(prev);
      if (wasFavorite) next.delete(item.id);
      else next.add(item.id);
      return next;
    });

    try {
      const marks = wasFavorite
        ? await removeCourseMark(item.id, 'favorite')
        : await saveCourseMark(item.id, 'favorite');
      applyCourseMarksResponse(marks);
      refreshNotifications();
      setNotice(wasFavorite ? '찜한 강좌에서 제거했습니다.' : '찜한 강좌에 추가했습니다.');
    } catch (err) {
      setFavoriteIds((prev) => {
        const next = new Set(prev);
        if (wasFavorite) next.add(item.id);
        else next.delete(item.id);
        return next;
      });
      setNotice(err instanceof Error ? err.message : '찜 저장에 실패했습니다.');
    }
  }, [applyCourseMarksResponse, authUser, favoriteIds, refreshNotifications, requireLoginForUserCourse]);

  const addMyCourse = useCallback(async (item: ClassItem) => {
    if (!authUser) {
      requireLoginForUserCourse();
      return;
    }

    setAppliedIds((prev) => new Set(prev).add(item.id));

    try {
      const marks = await saveCourseMark(item.id, 'applied');
      applyCourseMarksResponse(marks);
      refreshNotifications();
      setNotice('내강좌에 등록했습니다.');
    } catch (err) {
      setAppliedIds((prev) => {
        const next = new Set(prev);
        next.delete(item.id);
        return next;
      });
      setNotice(err instanceof Error ? err.message : '내강좌 등록에 실패했습니다.');
    }
  }, [applyCourseMarksResponse, authUser, refreshNotifications, requireLoginForUserCourse]);

  const removeMyCourse = useCallback(async (item: ClassItem) => {
    if (!authUser) {
      requireLoginForUserCourse();
      return;
    }

    const wasApplied = appliedIds.has(item.id);
    if (!wasApplied) {
      setNotice('이미 내강좌에 등록되어 있지 않습니다.');
      return;
    }

    setAppliedIds((prev) => {
      const next = new Set(prev);
      next.delete(item.id);
      return next;
    });

    try {
      const marks = await removeCourseMark(item.id, 'applied');
      applyCourseMarksResponse(marks);
      refreshNotifications();
      setNotice('내강좌 등록을 취소했습니다.');
    } catch (err) {
      setAppliedIds((prev) => new Set(prev).add(item.id));
      setNotice(err instanceof Error ? err.message : '내강좌 등록 취소에 실패했습니다.');
    }
  }, [appliedIds, applyCourseMarksResponse, authUser, refreshNotifications, requireLoginForUserCourse]);

  const toggleCompareCourse = useCallback((item: ClassItem) => {
    if (compareItems.some((course) => course.id === item.id)) {
      setCompareItems((prev) => prev.filter((course) => course.id !== item.id));
      setNotice('비교 목록에서 제거했습니다.');
      return;
    }
    if (compareItems.length >= MAX_COMPARE_ITEMS) {
      setNotice(`비교는 최대 ${MAX_COMPARE_ITEMS}개까지 가능합니다.`);
      return;
    }
    setCompareItems((prev) => [...prev, item]);
    setNotice('비교 목록에 추가했습니다.');
  }, [compareItems]);

  const removeCompareCourse = useCallback((id: string) => {
    setCompareItems((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const clearCompareCourses = useCallback(() => {
    setCompareItems([]);
  }, []);

  const queueCourseUpdateForItem = useCallback((item: ClassItem, reason: 'detail' | 'source' | 'apply') => {
    const evidenceUrl = reason === 'apply'
      ? firstSafeExternalUrl(item.applicationUrl, item.rawUrl)
      : firstSafeExternalUrl(item.rawUrl, item.applicationUrl);
    void requestCourseUpdate(item.id, reason, evidenceUrl).catch(() => undefined);
  }, []);

  const openCourseApplication = useCallback((item: ClassItem) => {
    const url = firstSafeExternalUrl(item.applicationUrl, item.rawUrl);
    if (!openExternalUrl(url)) {
      setNotice('신청 또는 공식 상세 링크가 없는 강좌입니다.');
      return;
    }
    queueCourseUpdateForItem(item, 'apply');
  }, [queueCourseUpdateForItem]);

  const openCourseDetail = useCallback((item: ClassItem) => {
    closeTransientFilters();
    queueCourseUpdateForItem(item, 'detail');
    setSelectedCourse(item);
    writeCourseToUrl(item, 'push');
  }, [closeTransientFilters, queueCourseUpdateForItem]);

  const closeCourseDetail = useCallback(() => {
    setSelectedCourse(null);
    writeCourseToUrl(null, 'replace');
  }, []);

  const openCourseLocation = useCallback((item: ClassItem) => {
    closeTransientFilters();
    setLocationCourse(item);
  }, [closeTransientFilters]);

  const selectBranch = useCallback((branch: Branch | null) => {
    if (!branch) {
      setSelectedBranch(null);
      return;
    }

    const removing = branchFilters?.includes(branch.id) ?? false;
    setBranchFilters((filters) => {
      if (!filters) return [branch.id];
      const next = filters.includes(branch.id)
        ? filters.filter((branchId) => branchId !== branch.id)
        : [...filters, branch.id];
      return next.length ? next : null;
    });
    setSelectedBranch(null);
    setViewMode('all');
    setNotice(`${branchDisplayName(branch)} 지점을 필터에서 ${removing ? '제거했습니다.' : '추가했습니다.'}`);
  }, [branchFilters]);

  const resetFilters = useCallback(() => {
    setActiveCategory('all');
    setCategoryFilters(null);
    setAgeFilters(null);
    setBranchFilters(null);
    setChildAgeMonths('');
    setChildAgeYears('');
    setSelectedDate('');
    setDayFilters(allDays);
    setTimeFilters(allTimes);
    setProviderFilters(allProviders);
    setFeeFilters(allFees);
    setStatusFilters(debugMode ? allStatuses : defaultStatusFilters);
    setSortBy('popular');
    setViewMode('all');
    setSelectedBranch(null);
    setKeyword('');
    setSearchKeyword('');
    setNotice('필터를 초기화했습니다.');
  }, [allProviders]);

  const handleSocialLogin = useCallback(async (provider: AuthProvider, privacyNoticeVersion: string) => {
    setMissingLoginConfig(null);
    try {
      const result = await startOAuthLogin(provider, privacyNoticeVersion);
      if (!result.started) {
        setMissingLoginConfig(
          result.missingConfig || (provider === 'google' ? 'GOOGLE_OAUTH_CLIENT_ID' : 'NAVER_OAUTH_CLIENT_ID'),
        );
      }
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '로그인 설정을 확인해 주세요.');
    }
  }, []);

  const handleLoginButton = useCallback(async () => {
    closeTransientFilters();
    if (authUser) {
      try {
        await logoutStoredUser();
      } catch (err) {
        setNotice(err instanceof Error ? err.message : '로그아웃 요청에 실패했습니다.');
      }
      clearStoredUser();
      setAuthUser(null);
      setFavoriteIds(new Set());
      setAppliedIds(new Set());
      setSavedCourses([]);
      setSavedTotal(0);
      setNotifications([]);
      setNotificationCount(0);
      setNotificationOpen(false);
      setNotice('로그아웃했습니다.');
      return;
    }
    setMissingLoginConfig(null);
    setLoginOpen(true);
  }, [authUser, closeTransientFilters]);

  const handleDeleteAccount = useCallback(async () => {
    if (!authUser || accountDeleting) return;
    const confirmed = window.confirm('회원 탈퇴를 진행할까요? 저장한 강좌와 알림 설정이 모두 삭제됩니다.');
    if (!confirmed) return;

    setAccountDeleting(true);
    try {
      await deleteStoredAccount();
      setAuthUser(null);
      setFavoriteIds(new Set());
      setAppliedIds(new Set());
      setSavedCourses([]);
      setSavedTotal(0);
      setNotifications([]);
      setNotificationCount(0);
      setNotificationOpen(false);
      setAccountOpen(false);
      setNotice('회원 탈퇴가 완료되었습니다.');
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '회원 탈퇴 처리에 실패했습니다.');
    } finally {
      setAccountDeleting(false);
    }
  }, [accountDeleting, authUser]);

  const handleShowNotifications = useCallback(() => {
    closeTransientFilters();
    if (!authUser) {
      requireLoginForUserCourse();
      return;
    }
    setNotificationOpen(true);
    refreshNotifications();
  }, [authUser, closeTransientFilters, refreshNotifications, requireLoginForUserCourse]);

  const openNotificationCourse = useCallback((notification: UserNotification) => {
    openCourseDetail(toClassItem(notification.course));
    setNotificationOpen(false);
  }, [openCourseDetail]);

  const branchScopedCourses = useMemo(() => {
    const modeScopedCourses = debugMode
      ? courses.filter((course) => courseMatchesMapMode(course, mapMode))
      : courses;
    if (debugMode) return modeScopedCourses;
    if (!selectedBranch) return modeScopedCourses;
    return modeScopedCourses.filter((course) => branchIdMatches(course, selectedBranch));
  }, [courses, mapMode, selectedBranch]);

  const savedModeScopedCourses = useMemo(
    () =>
      savedCourses.filter((course) => courseMatchesMapMode(course, mapMode)),
    [mapMode, savedCourses],
  );

  const cardBranchById = useMemo(() => {
    const result = new Map<string, Branch>();
    mapBranches.forEach((branch) => {
      branchSourceIds(branch).forEach((branchId) => result.set(branchId, branch));
    });
    return result;
  }, [mapBranches]);
  const mapCourseToClassItem = useCallback((course: Course) => {
    const item = toClassItem(course);
    const branch = course.branch ?? (course.branch_id ? cardBranchById.get(course.branch_id) : null);
    if (!branch) return item;
    return {
      ...item,
      branch,
      distanceKm: branch.lat != null && branch.lon != null
        ? distanceKm(distanceReferenceLocation, branch)
        : item.distanceKm,
    };
  }, [cardBranchById, distanceReferenceLocation]);
  const classItems = useMemo(
    () => branchScopedCourses.map(mapCourseToClassItem),
    [branchScopedCourses, mapCourseToClassItem],
  );
  const savedClassItems = useMemo(
    () => savedModeScopedCourses.map(mapCourseToClassItem),
    [mapCourseToClassItem, savedModeScopedCourses],
  );

  useEffect(() => {
    let alive = true;

    if (viewMode === 'all') {
      setSavedCourses([]);
      setSavedTotal(0);
      setSavedLoading(false);
      return () => {
        alive = false;
      };
    }

    if (!authUser) {
      setSavedCourses([]);
      setSavedTotal(0);
      setSavedLoading(false);
      requireLoginForUserCourse();
      return () => {
        alive = false;
      };
    }

    setSavedLoading(true);
    fetchMarkedCourses(viewMode === 'favorites' ? 'favorite' : 'applied', 1, 500)
      .then((response) => {
        if (!alive) return;
        setSavedCourses(response.items);
        setSavedTotal(response.total);
      })
      .catch((err: unknown) => {
        if (!alive) return;
        setSavedCourses([]);
        setSavedTotal(0);
        setNotice(err instanceof Error ? err.message : '저장된 강좌 목록을 불러오지 못했습니다.');
      })
      .finally(() => {
        if (alive) setSavedLoading(false);
      });

    return () => {
      alive = false;
    };
  }, [authUser, requireLoginForUserCourse, viewMode]);

  const categoryOptions = useMemo(
    () => {
      const scopedCourseCategories = mapMode === 'provider'
        ? [
            ...classItems.map((item) => item.category),
            ...savedClassItems.map((item) => item.category),
          ]
        : [
            ...mapBranches
              .filter((branch) => branchMatchesMapMode(branch, mapMode))
              .flatMap((branch) => branch.service_groups || []),
            ...mapBranches
              .filter((branch) => branchMatchesMapMode(branch, mapMode))
              .flatMap((branch) => branch.collection_categories || []),
            ...classItems.flatMap(classItemCategoryValues),
            ...savedClassItems.flatMap(classItemCategoryValues),
          ];
      return buildFilterOptions([
        ...defaultCategoryLabelsForMode(mapMode),
        ...scopedCourseCategories,
      ]);
    },
    [classItems, mapBranches, mapMode, savedClassItems],
  );

  const ageOptions = useMemo(
    () => buildAgeFilterOptions(
      [...classItems, ...savedClassItems].map((item) => item.ageGroup),
      ageFilters ?? [],
    ),
    [ageFilters, classItems, savedClassItems],
  );

  const effectiveCategoryFilters = categoryFilters ?? categoryOptions.map((option) => option.value);
  const expandedEffectiveCategoryFilters = useMemo(
    () => categoryFilterValuesForMode(effectiveCategoryFilters, mapMode),
    [effectiveCategoryFilters, mapMode],
  );
  const categoryFilterActiveForMode = categoryFilters !== null
    && !sameStringSet(effectiveCategoryFilters, categoryOptions.map((option) => option.value));
  const effectiveAgeFilters = ageFilters ?? ageOptions.map((option) => option.value);
  const expandedEffectiveAgeFilters = useMemo(
    () => expandAgeFilterValues(effectiveAgeFilters),
    [effectiveAgeFilters],
  );
  const resultScopeLabel = resultScopeLabelFor(mapMode);

  const changeSearchType = useCallback((nextMode: MapMode) => {
    setMapMode(nextMode);
    setSelectedBranch(null);
    setVisibleMapBranchIds([]);
    setProviderFilters(allProviders);
    setViewMode('all');
    setCategoryFilters(null);
  }, [allProviders]);

  const visibleItems = useMemo(() => {
    // The list API is the single filtering and ordering authority for the main
    // result view. Reapplying similar client predicates can turn a non-zero API
    // total into an empty card list when normalization rules differ.
    if (viewMode === 'all') return classItems;

    let items = applyMultiValueFilter(savedClassItems, expandedEffectiveCategoryFilters, classItemCategoryValues);
    items = applyMultiValueFilter(items, expandedEffectiveAgeFilters, classItemAgeValues);
    items = items.filter((item) => matchesChildAge(item, childAgeMonths, childAgeYears));
    items = applyMultiValueFilter(items, dayFilters, normalizedScheduleDays);
    items = applyMultiValueFilter(items, timeFilters, inferTimeBuckets);
    items = applyStatusFilter(items, statusFilters);
    if (viewMode === 'favorites') items = items.filter((item) => favoriteIds.has(item.id));
    if (viewMode === 'applied') items = items.filter((item) => appliedIds.has(item.id));
    return sortCourseItems(items, sortBy);
  }, [appliedIds, childAgeMonths, childAgeYears, classItems, dayFilters, expandedEffectiveAgeFilters, expandedEffectiveCategoryFilters, favoriteIds, savedClassItems, sortBy, statusFilters, timeFilters, viewMode]);

  const branchMarkerStats = useMemo(() => {
    const courseCounts: Record<string, number> = {};
    const openCounts: Record<string, number> = {};
    const urgentCounts: Record<string, number> = {};
    const favoriteBranchIds = new Set<string>();

    visibleItems.forEach((item) => {
      if (!item.branchId) return;
      courseCounts[item.branchId] = (courseCounts[item.branchId] ?? 0) + 1;

      const statusText = `${item.statusCode ?? ''} ${item.status ?? ''} ${item.statusLabel ?? ''}`;
      if (item.statusCode === 'OPEN' || /접수중|OPEN/i.test(statusText)) {
        openCounts[item.branchId] = (openCounts[item.branchId] ?? 0) + 1;
      }
      if (item.statusCode === 'DEADLINE' || /마감임박|임박|DEADLINE/i.test(statusText)) {
        urgentCounts[item.branchId] = (urgentCounts[item.branchId] ?? 0) + 1;
      }
      if (favoriteIds.has(item.id)) {
        favoriteBranchIds.add(item.branchId);
      }
    });

    return {
      courseCounts,
      openCounts,
      urgentCounts,
      favoriteBranchIds: [...favoriteBranchIds],
    };
  }, [favoriteIds, visibleItems]);

  const branchCountById = useMemo(() => {
    const counts = new Map<string, number>();
    Object.entries(branchMarkerStats.courseCounts).forEach(([branchId, count]) => counts.set(branchId, count));
    return counts;
  }, [branchMarkerStats.courseCounts]);
  const branchFilterIsAllSelected = useMemo(
    () => branchFilters === null || sameStringSet(effectiveBranchFilters, branchOptions.map((option) => option.value)),
    [branchFilters, branchOptions, effectiveBranchFilters],
  );
  const shouldAggregateCourseGroups = !selectedBranch && viewMode === 'all' && branchFilterIsAllSelected;

  const groupedVisibleItems = useMemo(() => {
    if (shouldAggregateCourseGroups) {
      const firstItem = visibleItems[0];
      return [{
        key: 'all-courses',
        center: '전체 강좌',
        provider: 'PUBLIC',
        providerLabel: scopeModeLabel(mapMode),
        source: firstItem?.source ?? 'P',
        items: interleaveItemsByBranch(visibleItems),
        totalCount: Math.max(total, visibleItems.length),
        aggregate: true,
      }];
    }

    const groups = new Map<string, BranchCourseGroup>();
    visibleItems.forEach((item) => {
      const key = item.branchId || `${item.provider}:${item.center}`;
      const totalCount = item.branchId ? branchCountById.get(item.branchId) : undefined;
      const group = groups.get(key);
      if (group) {
        group.items.push(item);
        if (typeof totalCount === 'number' && (group.totalCount === undefined || totalCount > group.totalCount)) {
          group.totalCount = totalCount;
        }
        return;
      }
      groups.set(key, {
        key,
        center: item.center || '지점 미정',
        provider: item.provider,
        providerLabel: item.providerLabel,
        source: item.source,
        items: [item],
        totalCount,
      });
    });
    const orderStore = branchGroupStableOrderRef.current;
    return [...groups.values()].map((group) => {
      const orderKey = `${courseQueryKey}:${sortBy}:${viewMode}:${group.key}`;
      const previousOrder = orderStore[orderKey] ?? [];
      const currentIds = new Set(group.items.map((item) => item.id));
      const stableOrder = [
        ...previousOrder.filter((id) => currentIds.has(id)),
        ...group.items.map((item) => item.id).filter((id) => !previousOrder.includes(id)),
      ];
      orderStore[orderKey] = stableOrder;

      const orderIndex = new Map(stableOrder.map((id, index) => [id, index]));
      return {
        ...group,
        items: [...group.items].sort((left, right) => {
          const leftIndex = orderIndex.get(left.id) ?? Number.MAX_SAFE_INTEGER;
          const rightIndex = orderIndex.get(right.id) ?? Number.MAX_SAFE_INTEGER;
          return leftIndex - rightIndex;
        }),
      };
    });
  }, [branchCountById, courseQueryKey, mapMode, shouldAggregateCourseGroups, sortBy, total, viewMode, visibleItems]);
  const compareIds = useMemo(() => new Set(compareItems.map((item) => item.id)), [compareItems]);

  const defaultResultStatusFilters = debugMode ? allStatuses : defaultStatusFilters;
  const shouldExpandAllBranchGroups = Boolean(searchKeyword)
    || activeCategory !== 'all'
    || viewMode !== 'all'
    || Boolean(selectedBranch)
    || providerFilterActiveForMode
    || (branchFilters !== null && !sameStringSet(effectiveBranchFilters, branchOptions.map((option) => option.value)))
    || (categoryFilters !== null && !sameStringSet(effectiveCategoryFilters, categoryOptions.map((option) => option.value)))
    || (ageFilters !== null && !sameStringSet(effectiveAgeFilters, ageOptions.map((option) => option.value)))
    || Boolean(childAgeMonths)
    || Boolean(childAgeYears)
    || Boolean(selectedDate)
    || !sameStringSet(dayFilters, allDays)
    || !sameStringSet(timeFilters, allTimes)
    || !sameStringSet(feeFilters, allFees)
    || !sameStringSet(statusFilters, defaultResultStatusFilters);
  const collapsedBranchPreviewCount = 10;
  const expandedBranchPreviewCount = 20;
  const loadedResultCount = useMemo(
    () => groupedVisibleItems.reduce((count, group) => {
      const groupFilters = branchSubFilters[group.key] ?? {};
      const filteredItems = branchSubFilterActive(groupFilters)
        ? group.items.filter((item) => matchesBranchSubFilters(item, groupFilters))
        : group.items;
      if (shouldExpandAllBranchGroups || branchSubFilterActive(groupFilters)) {
        return count + filteredItems.length;
      }
      const manualVisibleCount = branchGroupVisibleCounts[group.key] ?? collapsedBranchPreviewCount;
      return count + Math.min(manualVisibleCount, filteredItems.length);
    }, 0),
    [
      branchGroupVisibleCounts,
      branchSubFilters,
      collapsedBranchPreviewCount,
      groupedVisibleItems,
      shouldExpandAllBranchGroups,
    ],
  );
  const resultTotalCount = viewMode === 'all'
    ? Math.max(total, visibleItems.length)
    : Math.max(savedTotal, visibleItems.length);
  const remainingResultCount = Math.max(resultTotalCount - loadedResultCount, 0);
  const resultLoading = viewMode === 'all' ? loading : savedLoading;
  const resultHeadingLabel = selectedBranch ? `${branchDisplayName(selectedBranch)} 강좌` : '전체 강좌';
  const resultContextLabel = selectedBranch
    ? `${branchDisplayName(selectedBranch)}에서 ${loadedResultCount.toLocaleString('ko-KR')}개 표시`
    : null;
  const detailFilterCount = [
    providerFilterActiveForMode,
    effectiveCategoryFilters.length !== categoryOptions.length,
    Boolean(selectedDate),
    feeFilters.length !== allFees.length,
    !sameStringSet(statusFilters, defaultResultStatusFilters),
  ].filter(Boolean).length;
  const openOnlyActive = sameStringSet(statusFilters, ['OPEN', 'DEADLINE']);
  const ageLabelByValue = useMemo(
    () => Object.fromEntries(ageOptions.map((option) => [option.value, option.label])),
    [ageOptions],
  );

  const popularItems = useMemo(() => {
    const visibleKeys = new Set<string>();
    visibleItems.forEach((item) => {
      visibleKeys.add(`id:${item.id}`);
      if (item.applicationUrl) visibleKeys.add(`url:${item.applicationUrl}`);
      if (item.rawUrl) visibleKeys.add(`url:${item.rawUrl}`);
    });
    const uniqueRecommendations = branchScopedCourses.filter((course) => {
      if (visibleKeys.has(`id:${course.id}`)) return false;
      if (course.application_url && visibleKeys.has(`url:${course.application_url}`)) return false;
      if (course.raw_url && visibleKeys.has(`url:${course.raw_url}`)) return false;
      return true;
    });
    return toPopularItems(uniqueRecommendations);
  }, [branchScopedCourses, visibleItems]);
  const nearbyCenterBranches = useMemo(
    () =>
      selectMapMarkerBranches({
        branches: mapBranches,
        userLocation: distanceReferenceLocation,
        radiusKm: nearbyRadiusKm,
        providerFilters: effectiveProviderFiltersForMode,
        categoryFilters: expandedEffectiveCategoryFilters,
        categoryFilterActive: categoryFilterActiveForMode,
        mapMode,
        resultCourseCounts: branchMarkerStats.courseCounts,
      })
        .map((branch) => ({ branch, distance: distanceKm(distanceReferenceLocation, branch) }))
        .sort((left, right) => left.distance - right.distance)
        .map(({ branch }) => branch),
    [branchMarkerStats.courseCounts, categoryFilterActiveForMode, distanceReferenceLocation, effectiveProviderFiltersForMode, expandedEffectiveCategoryFilters, mapBranches, mapMode, nearbyRadiusKm],
  );
  const handleNearbyDiameterChange = useCallback((diameterKm: MapDiameterKm) => {
    const radiusKm = diameterToRadiusKm(diameterKm);
    setNearbyDiameterKm(diameterKm);
    setSelectedBranch((branch) => (
      branch && distanceKm(distanceReferenceLocation, branch) > radiusKm ? null : branch
    ));
    setBranchFilters((filters) => {
      if (!filters) return filters;
      const branchById = new Map(mapBranches.flatMap((branch) => branchSourceIds(branch).map((branchId) => [branchId, branch] as const)));
      const nextFilters = filters.filter((branchId) => {
        const branch = branchById.get(branchId);
        return branch ? distanceKm(distanceReferenceLocation, branch) <= radiusKm : false;
      });
      return nextFilters.length ? nextFilters : null;
    });
    setVisibleMapBranchIds([]);
  }, [distanceReferenceLocation, mapBranches]);

  useEffect(() => {
    setSelectedBranch((branch) => (
      branch && distanceKm(distanceReferenceLocation, branch) > nearbyRadiusKm ? null : branch
    ));
    setBranchFilters((filters) => {
      if (!filters) return filters;
      const branchById = new Map(mapBranches.flatMap((branch) => branchSourceIds(branch).map((branchId) => [branchId, branch] as const)));
      const nextFilters = filters.filter((branchId) => {
        const branch = branchById.get(branchId);
        return branch ? distanceKm(distanceReferenceLocation, branch) <= nearbyRadiusKm : false;
      });
      if (nextFilters.length === filters.length && nextFilters.every((branchId, index) => branchId === filters[index])) {
        return filters;
      }
      return nextFilters.length ? nextFilters : null;
    });
  }, [distanceReferenceLocation, mapBranches, nearbyRadiusKm]);
  const routeSearch = routePath.includes('?') ? routePath.slice(routePath.indexOf('?') + 1) : '';
  const routePage = new URLSearchParams(routeSearch).get('page');
  const isBranchFinderPage =
    routePage === 'branches' ||
    routePath.startsWith('/branches') ||
    routePath.startsWith('/branch-search') ||
    routePath.startsWith('/map');
  const isExplicitMobilePage = routePage === 'mobile' || routePath.startsWith('/mobile');
  const showMobileHomePage = !isBranchFinderPage && (isExplicitMobilePage || isMobileViewport);
  const singleLocationBranch =
    branchFilters?.length === 1
      ? mapBranches.find((branch) => branchSourceIds(branch).includes(branchFilters[0])) ?? null
      : null;
  const locationFilterLabel = branchFilters?.length
    ? singleLocationBranch
      ? branchDisplayName(singleLocationBranch)
      : `${branchFilters.length.toLocaleString('ko-KR')}개 지점`
      : mapSearchCenter
      ? '지도 기준'
      : useCurrentLocation
        ? userLocation.source === 'network'
          ? userLocation.label
          : '현재 위치'
        : '기본 위치';
  const handleResetPickerLocation = useCallback(() => {
    setMapSearchCenter(null);
    if (useCurrentLocation) stopUsingCurrentLocation();
    setNotice('기본 위치로 변경했습니다.');
  }, [stopUsingCurrentLocation, useCurrentLocation]);
  const categoryLabelMap = Object.fromEntries(categoryOptions.map((option) => [option.value, option.label]));
  const ageFilterLabelMap = Object.fromEntries(ageOptions.map((option) => [option.value, option.label]));
  const providerLabelMap = Object.fromEntries(providerOptions.map((option) => [option.value, option.label]));
  const resultFilterChips: ResultFilterChip[] = [
    searchKeyword
      ? {
          key: 'keyword',
          label: `검색: ${searchKeyword}`,
          onRemove: () => {
            setKeyword('');
            setSearchKeyword('');
          },
        }
      : null,
    branchFilters?.length
      ? {
          key: 'branch',
          label: `위치: ${locationFilterLabel}`,
          onRemove: () => {
            setBranchFilters(null);
            setSelectedBranch(null);
          },
        }
      : null,
    providerFilterActiveForMode
      ? {
          key: 'provider',
          label: `기관: ${summarizeFilterValues(providerFilters, providerLabelMap)}`,
          onRemove: () => setProviderFilters(allProviders),
        }
      : null,
    categoryFilters !== null && !sameStringSet(effectiveCategoryFilters, categoryOptions.map((option) => option.value))
      ? {
          key: 'category',
          label: `분야: ${summarizeFilterValues(effectiveCategoryFilters, categoryLabelMap)}`,
          onRemove: () => {
            setCategoryFilters(null);
            setActiveCategory('all');
          },
        }
      : null,
    ageFilters !== null && !sameStringSet(effectiveAgeFilters, ageOptions.map((option) => option.value))
      ? {
          key: 'age',
          label: `연령: ${summarizeFilterValues(effectiveAgeFilters, ageFilterLabelMap)}`,
          onRemove: () => setAgeFilters(null),
        }
      : null,
    childAgeMonths
      ? {
          key: 'child-age-months',
          label: `연령: ${childAgeMonths}개월`,
          onRemove: () => setChildAgeMonths(''),
        }
      : null,
    childAgeYears
      ? {
          key: 'child-age-years',
          label: `연령: 만 ${childAgeYears}세`,
          onRemove: () => setChildAgeYears(''),
        }
      : null,
    selectedDate
      ? {
          key: 'date',
          label: `날짜: ${selectedDate}`,
          onRemove: () => setSelectedDate(''),
        }
      : null,
    !sameStringSet(dayFilters, allDays)
      ? {
          key: 'day',
          label: `요일: ${summarizeFilterValues(dayFilters, {})}`,
          onRemove: () => setDayFilters(allDays),
        }
      : null,
    !sameStringSet(timeFilters, allTimes)
      ? {
          key: 'time',
          label: `시간: ${summarizeFilterValues(timeFilters, quickTimeLabels)}`,
          onRemove: () => setTimeFilters(allTimes),
        }
      : null,
    !sameStringSet(feeFilters, allFees)
      ? {
          key: 'fee',
          label: `수강료: ${summarizeFilterValues(feeFilters, feeFilterLabels)}`,
          onRemove: () => setFeeFilters(allFees),
        }
      : null,
    !sameStringSet(statusFilters, defaultResultStatusFilters)
      ? {
          key: 'status',
          label: `접수: ${summarizeFilterValues(statusFilters, statusFilterLabels)}`,
          onRemove: () => setStatusFilters(defaultResultStatusFilters),
        }
      : null,
  ].filter((chip): chip is ResultFilterChip => Boolean(chip));
  const activeFilterCount = resultFilterChips.length;

  return (
    <div className={`app ${compareItems.length ? 'has-compare-dock' : ''}`}>
      <Header
        keyword={keyword}
        favoriteCount={favoriteIds.size}
        appliedCount={appliedIds.size}
        notificationCount={notificationCount}
        loggedIn={Boolean(authUser)}
        userName={authUser?.name}
        onHomeClick={() => {
          resetFilters();
          navigateToPage('/');
        }}
        onKeywordChange={(value) => {
          setKeyword(value);
          if (!value.trim()) setSearchKeyword('');
          setViewMode('all');
        }}
        onSubmitSearch={() => {
          const cleanedKeyword = keyword.trim();
          if (cleanedKeyword.length === 1) {
            setNotice('검색어는 두 글자 이상 입력해 주세요.');
            return;
          }
          setKeyword(cleanedKeyword);
          setSearchKeyword(cleanedKeyword);
          setViewMode('all');
          setNotice(cleanedKeyword ? '검색 조건이 적용되었습니다.' : '검색어를 지웠습니다.');
        }}
        onShowBugReport={handleShowBugReport}
        onShowFavorites={() => {
          if (!authUser) {
            requireLoginForUserCourse();
            return;
          }
          setViewMode('favorites');
            setNotice('찜한 강좌만 표시합니다.');
        }}
        onShowApplied={() => {
          if (!authUser) {
            requireLoginForUserCourse();
            return;
          }
          setViewMode('applied');
          setNotice('내 강좌만 표시합니다.');
        }}
        onShowNotifications={handleShowNotifications}
        onShowAccount={() => setAccountOpen(true)}
        onToggleLogin={handleLoginButton}
      />

      <LoginModal
        open={loginOpen}
        missingConfig={missingLoginConfig}
        onClose={() => setLoginOpen(false)}
        onLogin={handleSocialLogin}
      />

      <AccountModal
        open={accountOpen}
        user={authUser}
        deleting={accountDeleting}
        onClose={() => setAccountOpen(false)}
        onDeleteAccount={handleDeleteAccount}
      />

      <BugReportModal
        open={bugReportOpen}
        onClose={() => setBugReportOpen(false)}
        onSubmitted={() => setNotice('버그 제보를 보냈습니다. 확인 후 개선하겠습니다.')}
      />

      {notice && <div className="toast" role="status" aria-live="polite">{notice}</div>}

      <NotificationPanel
        open={notificationOpen}
        loading={notificationsLoading}
        notifications={notifications}
        onClose={() => setNotificationOpen(false)}
        onRefresh={refreshNotifications}
        onOpenCourse={openNotificationCourse}
      />

      <LocationPickerModal
        open={locationPickerOpen}
        branches={nearbyCenterBranches}
        providerFilters={effectiveProviderFiltersForMode}
        categoryFilters={expandedEffectiveCategoryFilters}
        categoryFilterActive={categoryFilterActiveForMode}
        mapMode={mapMode}
        branchCourseCounts={branchMarkerStats.courseCounts}
        branchOpenCounts={branchMarkerStats.openCounts}
        branchUrgentCounts={branchMarkerStats.urgentCounts}
        favoriteBranchIds={branchMarkerStats.favoriteBranchIds}
        selectedBranchIds={branchFilters ?? []}
        userLocation={distanceReferenceLocation}
        myLocation={userLocation.detected ? userLocation : null}
        locationLabel={locationFilterLabel}
        locationError={locationError}
        diameterKm={nearbyDiameterKm}
        locating={locating}
        usingCurrentLocation={useCurrentLocation}
        debugMode={debugMode}
        onClose={() => setLocationPickerOpen(false)}
        onBranchToggle={selectBranch}
        onClearBranches={() => {
          setBranchFilters(null);
          setSelectedBranch(null);
          setViewMode('all');
          setNotice('선택 지점 필터를 해제했습니다.');
        }}
        onDiameterChange={handleNearbyDiameterChange}
        onRequestCurrentLocation={requestCurrentLocation}
        onResetLocation={handleResetPickerLocation}
        onMapCenterChange={handleMapCenterChange}
        onVisibleBranchIdsChange={handleVisibleBranchIdsChange}
      />

      <CourseLocationModal
        item={locationCourse}
        onClose={() => setLocationCourse(null)}
      />

      <div className={`layout ${showMobileHomePage ? 'mobile-home-layout' : ''}`}>
        <div className="mobile-filter-bar" aria-label="모바일 필터">
          <span>{activeFilterCount ? `필터 ${activeFilterCount}개 적용 중` : '필터를 열어 조건을 바꿀 수 있습니다.'}</span>
          <button type="button" onClick={() => setMobileFilterOpen(true)}>필터 열기</button>
        </div>

        {mobileFilterOpen && (
          <button
            className="filter-scrim"
            type="button"
            aria-label="필터 닫기"
            onClick={() => setMobileFilterOpen(false)}
          />
        )}

        <div
          ref={mobileFilterDialogRef}
          className={`sidebar-shell ${mobileFilterOpen ? 'open' : ''}`}
          role={mobileFilterOpen ? 'dialog' : undefined}
          aria-modal={mobileFilterOpen ? 'true' : undefined}
          aria-label={mobileFilterOpen ? '강좌 필터' : undefined}
          tabIndex={mobileFilterOpen ? -1 : undefined}
        >
          <button className="mobile-filter-close" type="button" onClick={() => setMobileFilterOpen(false)}>
            닫기
          </button>
          <Sidebar
            mapMode={mapMode}
            categoryOptions={categoryOptions}
            ageOptions={ageOptions}
            providerOptions={providerOptions}
            categoryFilters={effectiveCategoryFilters}
            ageFilters={ageFilters}
            branchFilterActive={Boolean(branchFilters?.length)}
            childAgeMonths={childAgeMonths}
            childAgeYears={childAgeYears}
            selectedDate={selectedDate}
            dayFilters={dayFilters}
            timeFilters={timeFilters}
            providerFilters={providerFilters}
            feeFilters={feeFilters}
            statusFilters={statusFilters}
            selectedBranch={selectedBranch}
            locationValue={locationFilterLabel}
            locationPickerOpen={locationPickerOpen}
            detailFilterCount={detailFilterCount}
            onClearSelectedBranch={() => selectBranch(null)}
            onBranchSelectAll={() => {
              setBranchFilters(null);
              setSelectedBranch(null);
              setViewMode('all');
            }}
            onOpenLocationPicker={() => {
              setLocationPickerOpen(true);
              setMobileFilterOpen(false);
            }}
            onCategoryToggle={(value) => {
              const base = categoryFilters ?? categoryOptions.map((option) => option.value);
              const allCategories = categoryOptions.map((option) => option.value);
              const isAllSelected = sameStringSet(base, allCategories);
              const next = isAllSelected
                ? [value]
                : base.includes(value)
                  ? base.filter((category) => category !== value)
                  : [...base, value];
              setCategoryFilters(next.length ? next : null);
              setActiveCategory('all');
              setViewMode('all');
            }}
            onCategorySelectAll={() => {
              setCategoryFilters(null);
              setActiveCategory('all');
              setViewMode('all');
            }}
            onAgeToggle={(value) => {
              const allAges = ageOptions.map((option) => option.value);
              const base = ageFilters ?? allAges;
              const next = sameStringSet(base, allAges)
                ? [value]
                : base.includes(value)
                  ? base.filter((age) => age !== value)
                  : [...base, value];
              setAgeFilters(next.length ? next : null);
              setViewMode('all');
            }}
            onAgeSelectAll={() => {
              setAgeFilters(null);
              setViewMode('all');
            }}
            onChildAgeMonthsChange={(value) => {
              setChildAgeMonths(value.replace(/[^\d]/g, '').slice(0, 3));
              setChildAgeYears('');
              setViewMode('all');
            }}
            onChildAgeYearsChange={(value) => {
              setChildAgeYears(value.replace(/[^\d]/g, '').slice(0, 3));
              setChildAgeMonths('');
              setViewMode('all');
            }}
            onSelectedDateChange={(value) => {
              setSelectedDate(value);
              setViewMode('all');
            }}
            onDayToggle={(value) => {
              setDayFilters((prev) => toggleSpecificFilterValue(prev, value, allDays));
              setViewMode('all');
            }}
            onDaySelectAll={() => {
              setDayFilters(allDays);
              setViewMode('all');
            }}
            onTimeToggle={(value) => {
              setTimeFilters((prev) => toggleSpecificFilterValue(prev, value, allTimes));
              setViewMode('all');
            }}
            onTimeSelectAll={() => {
              setTimeFilters(allTimes);
              setViewMode('all');
            }}
            onProviderToggle={(value) => {
              setProviderFilters((prev) =>
                prev.includes(value) ? prev.filter((provider) => provider !== value) : [...prev, value],
              );
              setViewMode('all');
            }}
            onProviderSelectAll={(checked) => setProviderFilters(checked ? allProviders : [])}
            onFeeToggle={(value) => {
              setFeeFilters((prev) => toggleSpecificFilterValue(prev, value, allFees));
              setViewMode('all');
            }}
            onFeeSelectAll={() => setFeeFilters(allFees)}
            onStatusToggle={(value) => {
              setStatusFilters((prev) => toggleSpecificFilterValue(prev, value, allStatuses));
              setViewMode('all');
            }}
            onStatusSelectAll={() => setStatusFilters(allStatuses)}
            onMapModeChange={changeSearchType}
            onResetFilters={() => {
              resetFilters();
              setMobileFilterOpen(false);
            }}
            closeSignal={filterCloseSignal}
          />
        </div>

        <main className="main-content" aria-label="MoonCen 강좌 검색 결과">
          <h1 className="sr-only">전국 문화센터·공공강좌 검색</h1>
          {isBranchFinderPage && (
            <NearbyCenterMap
              branches={nearbyCenterBranches}
              branchCourseCounts={branchMarkerStats.courseCounts}
              branchOpenCounts={branchMarkerStats.openCounts}
              branchUrgentCounts={branchMarkerStats.urgentCounts}
              userLocation={distanceReferenceLocation}
              selectedBranch={selectedBranch}
              selectedBranchIds={branchFilters ?? []}
              hoveredBranchId={hoveredMapBranchId}
              diameterKm={nearbyDiameterKm}
              mapMode={mapMode}
              onDiameterChange={handleNearbyDiameterChange}
              onBranchSelect={selectBranch}
              onBranchHover={setHoveredMapBranchId}
              locating={locating}
              usingCurrentLocation={useCurrentLocation}
              onRequestCurrentLocation={requestCurrentLocation}
              onStopUsingCurrentLocation={stopUsingCurrentLocation}
              locationError={locationError}
              title={nearbyScopeTitle(mapMode)}
            >
              <MapSection
                branches={mapBranches}
                providerFilters={effectiveProviderFiltersForMode}
                categoryFilters={expandedEffectiveCategoryFilters}
                categoryFilterActive={categoryFilterActiveForMode}
                mapMode={mapMode}
                branchCourseCounts={branchMarkerStats.courseCounts}
                branchOpenCounts={branchMarkerStats.openCounts}
                branchUrgentCounts={branchMarkerStats.urgentCounts}
                favoriteBranchIds={branchMarkerStats.favoriteBranchIds}
                userLocation={distanceReferenceLocation}
                myLocation={userLocation.detected ? userLocation : null}
                locationError={null}
                selectedBranch={selectedBranch}
                selectedBranchIds={branchFilters ?? []}
                hoveredBranchId={hoveredMapBranchId}
                viewDiameterKm={nearbyDiameterKm}
                debugMode={debugMode}
                onBranchSelect={selectBranch}
                onBranchHover={setHoveredMapBranchId}
                onMapCenterChange={handleMapCenterChange}
                onVisibleBranchIdsChange={handleVisibleBranchIdsChange}
              />
            </NearbyCenterMap>
          )}

          <section className="section results-section" aria-labelledby="results-heading">
            <div className="course-result-header">
              <div className="course-result-summary">
                <p className="eyebrow">
                  {viewMode === 'favorites' ? '찜한 강좌' : viewMode === 'applied' ? '내 강좌' : resultScopeLabel}
                </p>
                <h2 id="results-heading">
                  {resultHeadingLabel} <strong>{resultTotalCount.toLocaleString('ko-KR')}개</strong>
                </h2>
                {resultContextLabel && <p className="result-selected-context">{resultContextLabel}</p>}
                {resultTotalCount > 0 && (
                  <p
                    className="result-progress"
                    aria-label={`${resultTotalCount.toLocaleString('ko-KR')}개 중 ${loadedResultCount.toLocaleString('ko-KR')}개 표시, ${remainingResultCount.toLocaleString('ko-KR')}개 남음`}
                  >
                    {resultTotalCount.toLocaleString('ko-KR')}개 중 {loadedResultCount.toLocaleString('ko-KR')}개 표시
                  </p>
                )}
              </div>

              <div className="course-result-controls">
                <label className="open-only-switch">
                  <input
                    type="checkbox"
                    role="switch"
                    checked={openOnlyActive}
                    onChange={(event) => {
                      setStatusFilters(
                        event.currentTarget.checked
                          ? defaultStatusFilters
                          : debugMode
                            ? allStatuses
                            : expandedStatusFilters,
                      );
                      setViewMode('all');
                    }}
                  />
                  <span aria-hidden="true" />
                  <b>접수중만 보기</b>
                </label>
                <label className="result-sort-control">
                  <span>정렬</span>
                  <select
                    aria-label="정렬 방식"
                    value={sortBy}
                    onChange={(event) => setSortBy(event.target.value as SortBy)}
                  >
                    <option value="popular">추천순</option>
                    <option value="latest">최신순</option>
                    <option value="deadline">마감임박순</option>
                    <option value="priceAsc">낮은 가격순</option>
                    <option value="priceDesc">높은 가격순</option>
                  </select>
                </label>
              </div>
            </div>

            {resultFilterChips.length > 0 && (
              <div className="mooncen-active-filter-strip result-active-filter-strip" aria-label="적용된 필터">
                <div className="active-filter-chip-list">
                  {resultFilterChips.map((chip) => (
                    <button key={chip.key} type="button" title={`${chip.label} 제거`} onClick={chip.onRemove}>
                      <span>{chip.label}</span>
                      <X size={14} strokeWidth={2.2} aria-hidden="true" />
                    </button>
                  ))}
                </div>
                <button className="active-filter-reset" type="button" onClick={resetFilters}>
                  <RotateCcw size={15} strokeWidth={2} aria-hidden="true" />
                  전체 초기화
                </button>
              </div>
            )}

            <CourseComparePanel
              items={compareItems}
              onRemove={removeCompareCourse}
              onClear={clearCompareCourses}
              onOpenDetails={openCourseDetail}
            />

            {resultLoading && <div className="state-panel" role="status" aria-live="polite">강좌 정보를 불러오는 중입니다.</div>}
            {error && (
              <div className="state-panel error course-error-state" role="alert">
                <strong>강좌 정보를 불러오지 못했어요.</strong>
                <span>{error}</span>
                <button type="button" onClick={() => setCourseRetrySignal((value) => value + 1)}>다시 시도</button>
              </div>
            )}
            {!resultLoading && !error && visibleItems.length === 0 && (
              <div className="state-panel course-empty-state">
                <strong>조건에 맞는 강좌를 찾지 못했어요.</strong>
                <span>필터를 줄이거나 다른 검색어를 입력해 보세요.</span>
                <button type="button" onClick={resetFilters}>필터 초기화</button>
              </div>
            )}

            <div className="branch-course-groups">
              {visibleItems.length > 0 && groupedVisibleItems.map((group) => {
                const groupSubFilters = branchSubFilters[group.key] ?? {};
                const groupSubFilterIsActive = branchSubFilterActive(groupSubFilters);
                const rawGroupItems = groupSubFilterIsActive
                  ? group.items.filter((item) => matchesBranchSubFilters(item, groupSubFilters))
                  : group.items;
                const shouldShowAllGroupItems = shouldExpandAllBranchGroups || groupSubFilterIsActive;
                const manualVisibleCount = branchGroupVisibleCounts[group.key] ?? collapsedBranchPreviewCount;
                const isExpanded = shouldShowAllGroupItems || manualVisibleCount > collapsedBranchPreviewCount;
                const currentGroupTotalCount = Math.max(group.totalCount ?? group.items.length, group.items.length);
                const existingSnapshot = branchGroupInitialSnapshotRef.current[group.key];
                const initialSnapshot = existingSnapshot?.queryKey === courseQueryKey
                  ? {
                    ...existingSnapshot,
                    totalCount: Math.max(existingSnapshot.totalCount, currentGroupTotalCount),
                    hiddenCount: Math.max(
                      Math.max(existingSnapshot.totalCount, currentGroupTotalCount) -
                        Math.min(group.items.length, collapsedBranchPreviewCount),
                      0,
                    ),
                  }
                  : {
                    queryKey: courseQueryKey,
                    totalCount: currentGroupTotalCount,
                    hiddenCount: Math.max(currentGroupTotalCount - Math.min(group.items.length, collapsedBranchPreviewCount), 0),
                  };
                if (branchGroupInitialSnapshotRef.current[group.key] !== initialSnapshot) {
                  branchGroupInitialSnapshotRef.current[group.key] = initialSnapshot;
                }
                const groupItems = !isExpanded && !groupSubFilterIsActive && initialSnapshot.totalCount > rawGroupItems.length
                  ? rawGroupItems.concat(new Array(initialSnapshot.totalCount - rawGroupItems.length))
                  : rawGroupItems;
                const visibleGroupItems = shouldShowAllGroupItems
                  ? rawGroupItems
                  : rawGroupItems.slice(0, Math.min(manualVisibleCount, rawGroupItems.length));
                const groupQuickAgeLabel = groupSubFilters.age
                  ? ageLabelByValue[groupSubFilters.age] || groupSubFilters.age
                  : '연령 전체';
                const groupQuickDateLabel = groupSubFilters.date || '날짜 전체';
                const groupQuickDayLabel = groupSubFilters.day || '요일 전체';
                const groupQuickTimeLabel = groupSubFilters.time
                  ? quickTimeLabels[groupSubFilters.time] || groupSubFilters.time
                  : '시간 전체';
                const groupTotalCount = groupSubFilterIsActive ? currentGroupTotalCount : initialSnapshot.totalCount;
                const groupCountLabel = groupSubFilterIsActive
                  ? `${groupItems.length.toLocaleString('ko-KR')}/${groupTotalCount.toLocaleString('ko-KR')}개`
                  : `${groupTotalCount.toLocaleString('ko-KR')}개`;
                const canToggleGroup = !shouldExpandAllBranchGroups
                  && !groupSubFilterIsActive
                  && initialSnapshot.totalCount > collapsedBranchPreviewCount;
                const collapsedHiddenCount = Math.max(initialSnapshot.totalCount - visibleGroupItems.length, 0);
                const canShowMoreGroup = canToggleGroup && collapsedHiddenCount > 0;
                const canCollapseGroup = canToggleGroup && manualVisibleCount > collapsedBranchPreviewCount;
                const displayCenterName = group.aggregate
                  ? '전체 강좌'
                  : branchDisplayName({
                    name: group.center,
                    provider: group.provider,
                    provider_label: group.providerLabel,
                    primary_collection_category: mapMode === 'provider' ? 'CULTURE_CENTER' : mapMode === 'experience' ? 'EXPERIENCE' : 'EDUCATION',
                  });
                return (
                <section
                  className={`branch-course-group ${isExpanded ? 'is-expanded' : ''} ${group.aggregate ? 'aggregate-course-group' : ''}`}
                  key={group.key}
                  aria-label={`${displayCenterName} 강좌`}
                >
                  <div className="branch-course-group-header">
                    <div className="branch-course-group-title">
                      {group.aggregate ? (
                        <span className="aggregate-course-group-icon" aria-hidden="true">전체</span>
                      ) : (
                        <ProviderIcon
                          providerName={group.providerLabel || group.source}
                          providerType={group.provider}
                          centerName={group.center}
                          size="small"
                          className="provider-badge-icon"
                        />
                      )}
                      <h3>{displayCenterName}</h3>
                      <strong>{groupCountLabel}</strong>
                    </div>
                    {!isMobileViewport && (
                    <div className="branch-course-quick-filters" aria-label="간이 필터">
                      <div className="branch-quick-filter">
                        <button
                          className="branch-quick-filter-chip"
                          type="button"
                          onClick={() => setBranchQuickFilterOpen(branchQuickFilterOpen === `${group.key}:age` ? null : `${group.key}:age`)}
                        >
                          {groupQuickAgeLabel}
                        </button>
                        {branchQuickFilterOpen === `${group.key}:age` && (
                          <div className="branch-quick-filter-menu">
                            <button type="button" onClick={() => { updateBranchSubFilter(group.key, { age: undefined }); setBranchQuickFilterOpen(null); }}>전체</button>
                            {ageOptions.map((option) => (
                              <button
                                key={option.value}
                                className={groupSubFilters.age === option.value ? 'active' : undefined}
                                type="button"
                                onClick={() => { updateBranchSubFilter(group.key, { age: option.value }); setBranchQuickFilterOpen(null); }}
                              >
                                {option.label}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="branch-quick-filter">
                        <button
                          className="branch-quick-filter-chip"
                          type="button"
                          onClick={() => {
                            const nextKey = `${group.key}:date`;
                            const willOpen = branchQuickFilterOpen !== nextKey;
                            if (willOpen) setQuickCalendarMonth(monthFromDateValue(groupSubFilters.date || ''));
                            setBranchQuickFilterOpen(willOpen ? nextKey : null);
                          }}
                        >
                          {groupQuickDateLabel}
                        </button>
                        {branchQuickFilterOpen === `${group.key}:date` && (
                          <QuickDateCalendar
                            month={quickCalendarMonth}
                            selectedDate={groupSubFilters.date || ''}
                            onMonthChange={setQuickCalendarMonth}
                            onSelect={(value) => {
                              updateBranchSubFilter(group.key, { date: value });
                              setBranchQuickFilterOpen(null);
                            }}
                            onClear={() => {
                              updateBranchSubFilter(group.key, { date: undefined });
                              setBranchQuickFilterOpen(null);
                            }}
                          />
                        )}
                      </div>
                      <div className="branch-quick-filter">
                        <button
                          className="branch-quick-filter-chip"
                          type="button"
                          onClick={() => setBranchQuickFilterOpen(branchQuickFilterOpen === `${group.key}:day` ? null : `${group.key}:day`)}
                        >
                          {groupQuickDayLabel}
                        </button>
                        {branchQuickFilterOpen === `${group.key}:day` && (
                          <div className="branch-quick-filter-menu">
                            <button type="button" onClick={() => { updateBranchSubFilter(group.key, { day: undefined }); setBranchQuickFilterOpen(null); }}>전체</button>
                            {allDays.map((day) => (
                              <button
                                key={day}
                                className={groupSubFilters.day === day ? 'active' : undefined}
                                type="button"
                                onClick={() => { updateBranchSubFilter(group.key, { day }); setBranchQuickFilterOpen(null); }}
                              >
                                {day}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="branch-quick-filter">
                        <button
                          className="branch-quick-filter-chip"
                          type="button"
                          onClick={() => setBranchQuickFilterOpen(branchQuickFilterOpen === `${group.key}:time` ? null : `${group.key}:time`)}
                        >
                          {groupQuickTimeLabel}
                        </button>
                        {branchQuickFilterOpen === `${group.key}:time` && (
                          <div className="branch-quick-filter-menu">
                            <button type="button" onClick={() => { updateBranchSubFilter(group.key, { time: undefined }); setBranchQuickFilterOpen(null); }}>전체</button>
                            {allTimes.map((time) => (
                              <button
                                key={time}
                                className={groupSubFilters.time === time ? 'active' : undefined}
                                type="button"
                                onClick={() => { updateBranchSubFilter(group.key, { time }); setBranchQuickFilterOpen(null); }}
                              >
                                {quickTimeLabels[time] || time}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                    )}
                  </div>
                  <div className="class-grid branch-class-grid" aria-label={`${displayCenterName} 강좌 목록`}>
                    {visibleGroupItems.length > 0 ? visibleGroupItems.map((item) => (
                      <ClassCard
                        key={item.id}
                        item={item}
                        isFavorite={favoriteIds.has(item.id)}
                        isCompared={compareIds.has(item.id)}
                        onToggleFavorite={handleToggleFavorite}
                        onApply={openCourseApplication}
                        onToggleCompare={toggleCompareCourse}
                        onOpenDetails={openCourseDetail}
                        onOpenLocation={openCourseLocation}
                      />
                    )) : (
                      <div className="branch-subfilter-empty">이 지점에는 조건에 맞는 강좌가 없습니다.</div>
                    )}
                  </div>
                  {canToggleGroup && (
                    <div className="branch-course-group-footer">
                      {canCollapseGroup && (
                        <button
                          className="branch-course-group-toggle branch-course-group-toggle-bottom branch-course-group-collapse"
                          type="button"
                          aria-expanded={isExpanded}
                          onClick={() => {
                            setBranchGroupVisibleCounts((prev) => {
                              const next = { ...prev };
                              delete next[group.key];
                              return next;
                            });
                          }}
                        >
                          접기
                        </button>
                      )}
                      {canShowMoreGroup && (
                        <button
                          className="branch-course-group-toggle branch-course-group-toggle-bottom"
                          type="button"
                          aria-expanded={isExpanded}
                          disabled={loadingMore}
                          onClick={() => {
                            if (loadingMore) return;
                            const currentVisibleCount = branchGroupVisibleCounts[group.key] ?? collapsedBranchPreviewCount;
                            const nextVisibleCount = Math.min(
                              currentVisibleCount + expandedBranchPreviewCount,
                              initialSnapshot.totalCount,
                            );
                            setBranchGroupVisibleCounts((prev) => {
                              return {
                                ...prev,
                                [group.key]: nextVisibleCount,
                              };
                            });
                            if (nextVisibleCount > rawGroupItems.length && hasMoreCourses && !loadingMore) {
                              setCoursePage((page) => page + 1);
                            }
                          }}
                        >
                          {loadingMore
                            ? '강좌를 불러오는 중'
                            : `더 많은 강좌 보기 (${collapsedHiddenCount.toLocaleString('ko-KR')}개 더)`}
                        </button>
                      )}
                    </div>
                  )}
                </section>
                );
              })}
            </div>
            {loadingMore && <div className="state-panel" role="status" aria-live="polite">다음 강좌를 불러오는 중입니다.</div>}
          </section>

          {popularItems.length > 0 && (
            <section className="section popular-section" aria-labelledby="popular-heading">
              <div className="section-header">
                <div>
                  <h2 id="popular-heading">함께 볼 강좌</h2>
                  <p className="section-subtitle">현재 목록과 겹치지 않는 강좌입니다.</p>
                </div>
                <button className="text-button" type="button" onClick={resetFilters}>
                  전체보기
                </button>
              </div>

              <div className="popular-grid">
                {popularItems.map((item) => (
                  <PopularClassCard key={item.id} item={item} onSelect={openCourseDetail} />
                ))}
              </div>
            </section>
          )}
        </main>
      </div>

      <CourseDetailModal
        item={selectedCourse}
        isFavorite={selectedCourse ? favoriteIds.has(selectedCourse.id) : false}
        isApplied={selectedCourse ? appliedIds.has(selectedCourse.id) : false}
        onClose={closeCourseDetail}
        onAddMyCourse={addMyCourse}
        onRemoveMyCourse={removeMyCourse}
        onApply={openCourseApplication}
        onToggleFavorite={handleToggleFavorite}
      />
    </div>
  );
}

export default function App() {
  return <MooncenHomeApp />;
}
