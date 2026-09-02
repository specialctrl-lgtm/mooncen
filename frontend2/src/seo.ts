import { useEffect } from 'react';
import type { Branch } from './api';
import type { ClassItem } from './data/mockData';
import { runtimeConfig } from './runtimeConfig';

const SITE_NAME = '문센';
const DEFAULT_SITE_URL = 'https://mooncen.kr';
const SITE_URL = (runtimeConfig.siteUrl || DEFAULT_SITE_URL).replace(/\/+$/, '');
const DEFAULT_TITLE = '문센 - 전국 문화센터·공공강좌 검색';
const DEFAULT_DESCRIPTION =
  '문센은 문화센터, 공공기관, 평생학습, 도서관, 체험 강좌를 지역, 연령, 날짜, 요일, 시간별로 검색하고 비교하는 강좌 검색 서비스입니다.';
const DEFAULT_KEYWORDS =
  '문센, 문화센터, 문화센터 강좌, 평생학습, 공공강좌, 도서관 강좌, 체험 예약, 아이 강좌, 성인 강좌';

type SeoInput = {
  keyword?: string;
  total?: number;
  selectedBranch?: Branch | null;
  selectedCourse?: ClassItem | null;
};

function absoluteUrl(path = '/') {
  if (/^https?:\/\//i.test(path)) return path;
  return `${SITE_URL}${path.startsWith('/') ? path : `/${path}`}`;
}

function slugifyCourseText(value: string) {
  return (
    value
      .normalize('NFKC')
      .toLowerCase()
      .replace(/[^\p{Letter}\p{Number}]+/gu, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 80) || 'course'
  );
}

function coursePath(course: ClassItem) {
  const slug = slugifyCourseText([course.title, course.center].filter(Boolean).join(' '));
  return `/course/${encodeURIComponent(course.id)}/${encodeURIComponent(slug)}`;
}

function setMeta(name: string, content: string, property = false) {
  const attr = property ? 'property' : 'name';
  let element = document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${name}"]`);
  if (!element) {
    element = document.createElement('meta');
    element.setAttribute(attr, name);
    document.head.appendChild(element);
  }
  element.content = content;
}

function setCanonical(url: string) {
  let element = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]');
  if (!element) {
    element = document.createElement('link');
    element.rel = 'canonical';
    document.head.appendChild(element);
  }
  element.href = url;
}

function setJsonLd(id: string, payload: unknown) {
  let element = document.head.querySelector<HTMLScriptElement>(`script[data-seo-jsonld="${id}"]`);
  if (!element) {
    element = document.createElement('script');
    element.type = 'application/ld+json';
    element.dataset.seoJsonld = id;
    document.head.appendChild(element);
  }
  element.textContent = JSON.stringify(payload);
}

function removeJsonLd(id: string) {
  document.head.querySelector<HTMLScriptElement>(`script[data-seo-jsonld="${id}"]`)?.remove();
}

export function courseJsonLd(course: ClassItem) {
  const provider = course.providerLabel || course.provider;
  const locationName = [provider, course.center].filter(Boolean).join(' ');
  const pageUrl = absoluteUrl(coursePath(course));
  const offer: Record<string, unknown> = {
    '@type': 'Offer',
    availability: course.statusCode === 'CLOSED' ? 'https://schema.org/SoldOut' : 'https://schema.org/InStock',
    url: pageUrl,
  };
  if (course.priceKnown && Number.isFinite(course.price) && course.price >= 0) {
    offer.price = course.price;
    offer.priceCurrency = 'KRW';
  }
  const location = course.center
    ? {
        '@type': 'Place',
        name: locationName,
      }
    : undefined;
  const description = course.aiSummary || course.description || `${locationName}에서 진행하는 ${course.title} 강좌`;
  return {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'Course',
        '@id': `${pageUrl}#course`,
        url: pageUrl,
        name: course.title,
        description,
        provider: {
          '@type': 'Organization',
          name: provider || SITE_NAME,
        },
        offers: offer,
        location,
        startDate: course.startDate || undefined,
        endDate: course.endDate || undefined,
        inLanguage: 'ko-KR',
      },
      {
        '@type': 'Event',
        '@id': `${pageUrl}#event`,
        url: pageUrl,
        name: course.title,
        description,
        eventStatus: 'https://schema.org/EventScheduled',
        eventAttendanceMode: 'https://schema.org/OfflineEventAttendanceMode',
        organizer: {
          '@type': 'Organization',
          name: provider || SITE_NAME,
        },
        offers: offer,
        location,
        image: course.imageUrl ? [course.imageUrl] : undefined,
        startDate: course.startDate || undefined,
        endDate: course.endDate || undefined,
        performer: course.instructor ? { '@type': 'Person', name: course.instructor } : undefined,
        inLanguage: 'ko-KR',
      },
    ],
  };
}

function pageKeywords(parts: Array<string | undefined | null>) {
  return [...parts.filter(Boolean), DEFAULT_KEYWORDS].join(', ');
}

export function useMooncenSeo({ keyword, total, selectedBranch, selectedCourse }: SeoInput) {
  useEffect(() => {
    const cleanedKeyword = (keyword || '').trim();
    const branchName = selectedBranch?.name?.trim();
    const resultLabel = typeof total === 'number' && total > 0 ? `${total.toLocaleString('ko-KR')}개 강좌` : '강좌';

    let title = DEFAULT_TITLE;
    let description = DEFAULT_DESCRIPTION;
    let keywords = DEFAULT_KEYWORDS;

    if (selectedCourse) {
      title = `${selectedCourse.title} | ${selectedCourse.center || selectedCourse.providerLabel || SITE_NAME}`;
      description =
        selectedCourse.aiSummary ||
        selectedCourse.description ||
        `${selectedCourse.center || selectedCourse.providerLabel || SITE_NAME} ${selectedCourse.title} 강좌 정보를 확인하세요.`;
      keywords = pageKeywords([
        selectedCourse.title,
        selectedCourse.center,
        selectedCourse.providerLabel,
        selectedCourse.category,
        selectedCourse.age,
      ]);
    } else if (cleanedKeyword && branchName) {
      title = `${branchName} ${cleanedKeyword} 강좌 검색 | ${SITE_NAME}`;
      description = `${branchName}에서 ${cleanedKeyword} 관련 ${resultLabel}를 연령, 날짜, 요일, 시간별로 비교해보세요.`;
      keywords = pageKeywords([branchName, cleanedKeyword, `${branchName} 강좌`]);
    } else if (cleanedKeyword) {
      title = `${cleanedKeyword} 문화센터·공공강좌 검색 | ${SITE_NAME}`;
      description = `${cleanedKeyword} 관련 ${resultLabel}를 지역, 연령, 날짜, 요일, 시간별로 검색하고 비교하세요.`;
      keywords = pageKeywords([cleanedKeyword, `${cleanedKeyword} 강좌`]);
    } else if (branchName) {
      title = `${branchName} 강좌 검색 | ${SITE_NAME}`;
      description = `${branchName}에서 진행하는 ${resultLabel}를 연령, 날짜, 요일, 시간별로 확인하세요.`;
      keywords = pageKeywords([branchName, `${branchName} 문화센터`, `${branchName} 강좌`]);
    }

    const canonical = selectedCourse ? absoluteUrl(coursePath(selectedCourse)) : absoluteUrl('/');
    document.title = title;
    setMeta('description', description.slice(0, 155));
    setMeta('keywords', keywords);
    setMeta('robots', 'index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1');
    setMeta('googlebot', 'index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1');
    setMeta('application-name', SITE_NAME);
    setMeta('og:type', selectedCourse ? 'article' : 'website', true);
    setMeta('og:locale', 'ko_KR', true);
    setMeta('og:site_name', SITE_NAME, true);
    setMeta('og:title', title, true);
    setMeta('og:description', description.slice(0, 155), true);
    setMeta('og:url', canonical, true);
    setMeta('og:image', absoluteUrl('/logo-header.png'), true);
    setMeta('og:image:alt', title, true);
    setMeta('twitter:card', 'summary_large_image');
    setMeta('twitter:title', title);
    setMeta('twitter:description', description.slice(0, 155));
    setMeta('twitter:image', absoluteUrl('/logo-header.png'));
    setCanonical(canonical);

    if (selectedCourse) {
      setJsonLd('selected-course', courseJsonLd(selectedCourse));
    } else {
      removeJsonLd('selected-course');
    }
  }, [keyword, selectedBranch, selectedCourse, total]);
}
