import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

const emptyCoursePage = {
  total: 0,
  page: 1,
  size: 24,
  items: [],
};

const courseCardPage = {
  total: 2,
  page: 1,
  size: 24,
  items: [
    {
      id: 'course-card-fixture',
      provider: 'LOTTE',
      provider_label: '롯데문화센터',
      provider_course_id: 'course-card-fixture',
      title: '이미지와 정보를 나란히 보여주는 창의 미술 강좌',
      fee: 120000,
      material_fee: 15000,
      sessions: 8,
      start_date: '2026-07-20',
      end_date: '2026-09-07',
      apply_start: '2026-07-15',
      apply_end: '2026-07-19',
      status: 'OPEN',
      status_label: '접수중',
      target: '5~7세',
      target_age_group: 'CHILD',
      category_raw: '미술',
      service_group: '문화센터',
      program_type: '창의미술',
      schedule_raw: '월 10:00 ~ 10:50',
      schedule_days: ['월'],
      application_url: 'https://example.com/courses/course-card-fixture',
      raw_url: 'https://example.com/courses/course-card-fixture',
      image_url: 'data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%22320%22 height=%22480%22%3E%3Crect width=%22320%22 height=%22480%22 fill=%22%23ddd6fe%22/%3E%3C/svg%3E',
      branch: {
        id: 'branch-card-fixture',
        name: '잠실점',
        provider: 'LOTTE',
      },
    },
    {
      id: 'course-card-no-image-fixture',
      provider: 'LOTTE',
      provider_label: '롯데문화센터',
      provider_course_id: 'course-card-no-image-fixture',
      title: '짧은 강좌',
      fee: 90000,
      material_fee: 0,
      sessions: 6,
      start_date: '2026-08-03',
      end_date: '2026-09-07',
      apply_start: null,
      apply_end: null,
      status: 'SCHEDULED',
      status_label: '접수예정',
      target: '24~36개월',
      target_age_group: 'TODDLER',
      category_raw: '영유아·놀이',
      service_group: '문화센터',
      program_type: '오감놀이',
      schedule_raw: '월 11:00 ~ 11:50',
      schedule_days: ['월'],
      image_url: null,
      branch: {
        id: 'branch-card-fixture',
        name: '잠실점',
        provider: 'LOTTE',
      },
    },
  ],
};

const expandableCoursePage = {
  ...courseCardPage,
  total: 31,
  size: 40,
  items: Array.from({ length: 31 }, (_, index) => {
    const template = courseCardPage.items[0];
    return {
      ...template,
      id: `expandable-course-${index + 1}`,
      provider_course_id: `expandable-course-${index + 1}`,
      title: `${index + 1}번째 ${template.title}`,
    };
  }),
};

const serverScopedEducationCoursePage = {
  ...courseCardPage,
  total: 153,
  items: [
    {
      ...courseCardPage.items[0],
      id: 'server-scoped-education-course',
      provider: 'SEOUL_PUBLIC_SERVICE',
      provider_label: 'Seoul Public Service',
      provider_course_id: 'server-scoped-education-course',
      title: 'Server scoped education course',
      service_group: '\uacf5\uacf5\uac15\uc88c',
      program_type: '\uad50\uc721',
      collection_category: '\uacf5\uacf5\uc608\uc57d',
      branch: {
        id: 'server-scoped-education-branch',
        name: 'Youth Center',
        provider: 'SEOUL_PUBLIC_SERVICE',
      },
    },
  ],
};

async function mockApi(page: Page, coursePage = emptyCoursePage) {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path === '/api/auth/me') {
      await route.fulfill({
        status: 204,
      });
      return;
    }

    if (path === '/api/branches/providers' || path === '/api/branches/nearby') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      return;
    }

    if (path === '/api/courses/' || path === '/api/users/me/courses') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(coursePage),
      });
      return;
    }

    await route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Not found in E2E fixture' }),
    });
  });
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });
  await mockApi(page);
});

test('server-scoped education courses remain visible without local branch-token filtering', async ({ page }) => {
  await page.unroute('**/api/**');
  await mockApi(page, serverScopedEducationCoursePage);
  await page.goto('/');

  await page.locator('.filter-mode-selector [role="tab"]').nth(2).click();

  await expect(page.locator('.result-progress')).toContainText('153');
  await expect(page.locator('.branch-class-grid .class-card')).toHaveCount(1);
  await expect(page.getByText('Server scoped education course')).toBeVisible();
  await expect(page.locator('.course-empty-state')).toHaveCount(0);
});

test('기본 화면은 접근 가능하고 가로 넘침이나 런타임 오류가 없다', async ({ page }, testInfo) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];

  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await page.goto('/');
  await expect(page.getByRole('main')).toBeVisible();
  await expect(page.locator('h1')).toHaveCount(1);

  const isMobile = testInfo.project.name.startsWith('mobile-');
  await expect(page.locator('.mobile-home-page')).toHaveCount(0);
  await expect(page.locator('.layout')).toHaveCount(1);
  await expect(page.locator('.layout.mobile-home-layout')).toHaveCount(isMobile ? 1 : 0);

  if (isMobile) {
    await expect(page.locator('.sidebar-shell')).toBeVisible();
    const scopeTabs = page.locator('.filter-mode-selector [role="tab"]');
    await expect(scopeTabs).toHaveCount(3);
    const scopeTabMetrics = await scopeTabs.evaluateAll((tabs) => tabs.map((tab) => {
      const tabBox = tab.getBoundingClientRect();
      const label = tab.querySelector('strong');
      return {
        y: Math.round(tabBox.y),
        labelFits: !label
          || (label.scrollWidth <= label.clientWidth + 1 && label.scrollHeight <= label.clientHeight + 1),
      };
    }));
    expect(new Set(scopeTabMetrics.map(({ y }) => y)).size).toBe(1);
    expect(scopeTabMetrics.every(({ labelFits }) => labelFits)).toBe(true);
    await expect(page.locator('.mooncen-filter-controls .main-quick-filter')).toHaveCount(4);
    await expect(page.locator('.results-section')).toBeVisible();
    await expect(page.locator('.course-empty-state')).toBeVisible();
    await expect(page.locator('.popular-section')).toHaveCount(0);
    await expect(page.locator('.mobile-filter-bar')).toBeHidden();
  }

  const viewportSize = page.viewportSize();
  expect(viewportSize).not.toBeNull();
  const pageWidth = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(pageWidth.clientWidth).toBe(viewportSize?.width);
  expect(pageWidth.scrollWidth).toBeLessThanOrEqual(pageWidth.clientWidth + 1);

  const accessibility = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa'])
    .analyze();
  const seriousViolations = accessibility.violations.filter(
    (violation) => violation.impact === 'critical' || violation.impact === 'serious',
  );
  expect(seriousViolations).toEqual([]);

  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
});

test('새로고침 초기화 중 강좌 로딩 상태는 한 번만 표시된다', async ({ page }) => {
  await page.unroute('**/api/**');
  let courseRequestCount = 0;

  await page.addInitScript(() => {
    const loadingHistory: boolean[] = [];
    Object.defineProperty(window, '__mooncenCourseLoadingHistory', {
      value: loadingHistory,
      configurable: true,
    });

    window.addEventListener('DOMContentLoaded', () => {
      let previous = false;
      const recordLoadingState = () => {
        const current = Array.from(document.querySelectorAll('[role="status"]'))
          .some((element) => element.textContent?.includes('강좌 정보를 불러오는 중입니다.'));
        if (current === previous) return;
        loadingHistory.push(current);
        previous = current;
      };

      new MutationObserver(recordLoadingState).observe(document.documentElement, {
        childList: true,
        subtree: true,
        characterData: true,
      });
      recordLoadingState();
    });
  });

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/auth/me') {
      await route.fulfill({ status: 204 });
      return;
    }
    if (url.pathname === '/api/branches/nearby') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      return;
    }
    if (url.pathname === '/api/branches/providers') {
      await new Promise((resolve) => setTimeout(resolve, 600));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ provider: 'LOTTE', label: '롯데문화센터', branch_count: 1 }]),
      });
      return;
    }
    if (url.pathname === '/api/courses/') {
      courseRequestCount += 1;
      await new Promise((resolve) => setTimeout(resolve, 120));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(emptyCoursePage),
      });
      return;
    }
    await route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Not found in loading E2E fixture' }),
    });
  });

  await page.goto('/');
  await page.waitForTimeout(900);

  const loadingHistory = await page.evaluate(() => (
    (window as typeof window & { __mooncenCourseLoadingHistory?: boolean[] })
      .__mooncenCourseLoadingHistory ?? []
  ));
  expect(loadingHistory).toEqual([true, false]);
  expect(courseRequestCount).toBe(1);
});

test('강좌 카드는 모든 화면 폭에서 동일한 정보 구조를 유지한다', async ({ page }, testInfo) => {
  const isMobile = testInfo.project.name.startsWith('mobile-');
  await page.unroute('**/api/**');
  await mockApi(page, courseCardPage);
  await page.goto('/?page=mobile');

  const cards = page.locator('.branch-class-grid .class-card');
  const card = cards.first();
  const shortTitleCard = cards.nth(1);
  const overview = card.locator('.course-card-overview');
  const thumbnail = card.locator('.course-card-thumbnail');
  const heading = card.locator('.course-card-heading');
  const facts = card.locator('.course-card-facts');
  const actions = card.locator('.course-card-actions');
  await expect(card).toBeVisible();
  await expect(shortTitleCard).toBeVisible();
  await expect(page.locator('.results-section > .course-result-header #results-heading')).toBeVisible();
  await expect(
    page.locator('.aggregate-course-group > .branch-course-group-header'),
  ).toBeHidden();

  const [cardBox, overviewBox, thumbnailBox, headingBox, factsBox, actionsBox] = await Promise.all([
    card.boundingBox(),
    overview.boundingBox(),
    thumbnail.boundingBox(),
    heading.boundingBox(),
    facts.boundingBox(),
    actions.boundingBox(),
  ]);
  expect(cardBox).not.toBeNull();
  expect(overviewBox).not.toBeNull();
  expect(thumbnailBox).not.toBeNull();
  expect(headingBox).not.toBeNull();
  expect(factsBox).not.toBeNull();
  expect(actionsBox).not.toBeNull();
  expect(thumbnailBox!.x).toBeLessThan(headingBox!.x);
  expect(thumbnailBox!.y).toBeLessThan(headingBox!.y + headingBox!.height);
  expect(headingBox!.y).toBeLessThan(thumbnailBox!.y + thumbnailBox!.height);
  expect(factsBox!.y).toBeGreaterThanOrEqual(overviewBox!.y + overviewBox!.height - 1);
  expect(actionsBox!.y).toBeGreaterThanOrEqual(factsBox!.y + factsBox!.height - 1);

  await expect(card.locator('.course-card-tags')).toBeVisible();
  await expect(card.locator('.course-age-tag')).toBeVisible();
  await expect(facts.locator(':scope > div')).toHaveCount(3);
  await expect(card.locator('.course-location-button')).toBeVisible();
  await expect(card.locator('.compare-check')).toBeVisible();
  await expect(card.locator('.course-apply-action')).toBeVisible();
  if (isMobile) {
    await expect(page.locator('.branch-course-quick-filters')).toHaveCount(0);
  }
  await expect(card.locator('.course-title-button')).toHaveCSS('text-align', 'left');
  const titleLayout = await card.locator('.course-title-button h3').evaluate((title) => {
    const titleStyle = getComputedStyle(title);
    return {
      titleHeight: title.getBoundingClientRect().height,
      lineHeight: Number.parseFloat(titleStyle.lineHeight),
      lineClamp: titleStyle.webkitLineClamp,
      overflow: titleStyle.overflow,
    };
  });
  expect(titleLayout.titleHeight).toBeGreaterThan(titleLayout.lineHeight + 1);
  expect(titleLayout.titleHeight).toBeCloseTo(isMobile ? 40 : 42, 0);
  expect(titleLayout.lineClamp).toBe('2');
  expect(titleLayout.overflow).toBe('hidden');

  const [
    shortOverviewBox,
    longTitleBox,
    shortTitleBox,
    longTagsBox,
    shortTagsBox,
    longPriceBox,
    shortPriceBox,
    courseFeeBox,
    materialFeeBox,
  ] = await Promise.all([
    shortTitleCard.locator('.course-card-overview').boundingBox(),
    card.locator('.course-title-button h3').boundingBox(),
    shortTitleCard.locator('.course-title-button h3').boundingBox(),
    card.locator('.course-card-tags').boundingBox(),
    shortTitleCard.locator('.course-card-tags').boundingBox(),
    card.locator('.course-price').boundingBox(),
    shortTitleCard.locator('.course-price').boundingBox(),
    card.locator('.course-price strong').boundingBox(),
    card.locator('.course-material-fee').boundingBox(),
  ]);
  for (const box of [
    shortOverviewBox,
    longTitleBox,
    shortTitleBox,
    longTagsBox,
    shortTagsBox,
    longPriceBox,
    shortPriceBox,
    courseFeeBox,
    materialFeeBox,
  ]) {
    expect(box).not.toBeNull();
  }
  expect(shortTitleBox!.height).toBeCloseTo(longTitleBox!.height, 0);
  expect(longTagsBox!.height).toBeCloseTo(isMobile ? 20 : 22, 0);
  expect(shortTagsBox!.height).toBeCloseTo(longTagsBox!.height, 0);
  expect(longTagsBox!.y - overviewBox!.y).toBeCloseTo(
    shortTagsBox!.y - shortOverviewBox!.y,
    0,
  );
  expect(longPriceBox!.height).toBeCloseTo(isMobile ? 36 : 38, 0);
  expect(shortPriceBox!.height).toBeCloseTo(longPriceBox!.height, 0);
  expect(longPriceBox!.y - overviewBox!.y).toBeCloseTo(
    shortPriceBox!.y - shortOverviewBox!.y,
    0,
  );
  expect(materialFeeBox!.y).toBeGreaterThanOrEqual(courseFeeBox!.y + courseFeeBox!.height);

  const compareAction = card.locator('.compare-check');
  const applyAction = card.locator('.course-apply-action');
  await expect(compareAction).toBeVisible();
  await expect(applyAction).toBeVisible();
  const [compareActionBox, applyActionBox] = await Promise.all([
    compareAction.boundingBox(),
    applyAction.boundingBox(),
  ]);
  expect(compareActionBox).not.toBeNull();
  expect(applyActionBox).not.toBeNull();
  expect(compareActionBox!.height).toBeGreaterThanOrEqual(isMobile ? 44 : 42);
  expect(applyActionBox!.height).toBeGreaterThanOrEqual(isMobile ? 44 : 42);

  const overflow = await card.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 1);
  expect(overflow.scrollHeight).toBeLessThanOrEqual(overflow.clientHeight + 1);

  const cardAccessibility = await new AxeBuilder({ page })
    .include('.branch-class-grid')
    .withTags(['wcag2a', 'wcag2aa'])
    .analyze();
  expect(cardAccessibility.violations.filter(
    (violation) => violation.impact === 'critical' || violation.impact === 'serious',
  )).toEqual([]);

  if (process.env.MOBILE_VISUAL_AUDIT === '1' && testInfo.project.name === 'mobile-390') {
    await page.screenshot({ path: 'test-results/mobile-common-home-390.png', fullPage: true });
  }
  if (process.env.CARD_VISUAL_AUDIT === '1') {
    await page.screenshot({
      path: `test-results/unified-course-card-${testInfo.project.name}.png`,
      fullPage: true,
    });
  }

  if (isMobile) {
    await card.locator('.course-title-button').click();
    await expect(page.locator('.course-detail-modal')).toBeVisible();
    await expect(page.locator('.course-detail-summary-schedule')).toBeVisible();
    await expect(page.locator('.course-detail-summary-period')).toBeVisible();
    await expect(page.locator('.course-detail-summary-apply')).toBeVisible();
    await expect(page.locator('.course-detail-summary-sessions')).toBeHidden();
    await expect(page.locator('.course-detail-summary-price')).toBeHidden();
    await expect(page.locator('.course-detail-info-target')).toBeVisible();
    await expect(page.locator('.course-detail-info-instructor')).toBeVisible();
    await expect(page.locator('.course-detail-info-capacity')).toBeVisible();
    await expect(page.locator('.course-detail-info-category')).toBeHidden();
    await expect(page.locator('.course-detail-extra-row')).toBeHidden();
    await expect(page.locator('.detail-favorite-action')).toBeHidden();
    const detailAccessibility = await new AxeBuilder({ page })
      .include('.course-detail-modal')
      .withTags(['wcag2a', 'wcag2aa'])
      .analyze();
    expect(detailAccessibility.violations.filter(
      (violation) => violation.impact === 'critical' || violation.impact === 'serious',
    )).toEqual([]);
    if (process.env.MOBILE_VISUAL_AUDIT === '1' && testInfo.project.name === 'mobile-390') {
      await page.screenshot({ path: 'test-results/mobile-simplified-detail-390.png', fullPage: false });
    }
    await page.keyboard.press('Escape');
    await expect(page.locator('.course-detail-modal')).toHaveCount(0);
  }
});

test('상세 필터는 분야와 상세 조건을 화면 폭에 맞게 정렬한다', async ({ page }, testInfo) => {
  await page.unroute('**/api/**');
  await mockApi(page, courseCardPage);
  await page.goto('/');
  await page.locator('.filter-detail-button').click();

  const panel = page.locator('.mooncen-filter-extra-panel');
  const categoryRow = panel.locator('.top-category-filter-group');
  const detailRow = panel.locator('.top-detail-filter-row');
  const detailControls = detailRow.locator('.top-detail-filter-controls .main-quick-filter-chip');
  const resetButton = detailRow.locator('.filter-reset-button');
  await expect(panel).toBeVisible();
  await expect(categoryRow).toBeVisible();
  await expect(detailRow).toBeVisible();
  await expect(detailControls).toHaveCount(3);
  await expect(resetButton).toBeVisible();

  const [panelBox, categoryBox, detailBox, controlBoxes] = await Promise.all([
    panel.boundingBox(),
    categoryRow.boundingBox(),
    detailRow.boundingBox(),
    detailControls.evaluateAll((elements) => elements.map((element) => {
      const rect = element.getBoundingClientRect();
      return { x: rect.x, y: rect.y, height: rect.height };
    })),
  ]);
  expect(panelBox).not.toBeNull();
  expect(categoryBox).not.toBeNull();
  expect(detailBox).not.toBeNull();
  if (testInfo.project.name === 'desktop') {
    expect(categoryBox!.x + categoryBox!.width).toBeLessThanOrEqual(detailBox!.x);
    expect(new Set(controlBoxes.map(({ y }) => Math.round(y))).size).toBe(1);
  } else {
    expect(categoryBox!.y + categoryBox!.height).toBeLessThanOrEqual(detailBox!.y);
    expect(new Set(controlBoxes.map(({ x }) => Math.round(x))).size).toBe(2);
    expect(new Set(controlBoxes.map(({ y }) => Math.round(y))).size).toBe(2);
  }
  expect(controlBoxes.every(({ height }) => height >= 44)).toBe(true);

  const overflow = await panel.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }));
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 1);

  if (process.env.FILTER_VISUAL_AUDIT === '1' && ['desktop', 'mobile-390'].includes(testInfo.project.name)) {
    await page.screenshot({
      path: `test-results/${testInfo.project.name}-expanded-filter.png`,
      fullPage: false,
    });
  }
});

test('접힌 데스크톱 강좌 그룹도 공통 카드 구조를 유지한다', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', '데스크톱 강좌 그룹 전용 검사');

  await page.unroute('**/api/**');
  await mockApi(page, courseCardPage);
  await page.goto('/');

  const group = page.locator('.branch-course-group').first();
  const card = group.locator('.class-card').first();
  const overview = card.locator('.course-card-overview');
  const thumbnail = card.locator('.course-card-thumbnail');
  const heading = card.locator('.course-card-heading');
  const facts = card.locator('.course-card-facts');
  const actions = card.locator('.course-card-actions');
  const resultsSection = page.locator('.results-section');
  const recommendedSection = page.locator('.popular-section');
  await expect(group).not.toHaveClass(/is-expanded/);
  await expect(card).toBeVisible();
  // The recommendation section deliberately excludes every course already in
  // the result list, so this two-course fixture must not render duplicates.
  await expect(recommendedSection).toHaveCount(0);

  const [
    display,
    cardBox,
    overviewBox,
    thumbnailBox,
    headingBox,
    factsBox,
    actionsBox,
    resultsSectionBox,
  ] = await Promise.all([
    card.evaluate((element) => getComputedStyle(element).display),
    card.boundingBox(),
    overview.boundingBox(),
    thumbnail.boundingBox(),
    heading.boundingBox(),
    facts.boundingBox(),
    actions.boundingBox(),
    resultsSection.boundingBox(),
  ]);

  expect(display).toBe('grid');
  expect(cardBox).not.toBeNull();
  expect(overviewBox).not.toBeNull();
  expect(thumbnailBox).not.toBeNull();
  expect(headingBox).not.toBeNull();
  expect(factsBox).not.toBeNull();
  expect(actionsBox).not.toBeNull();
  expect(resultsSectionBox).not.toBeNull();
  expect(thumbnailBox!.x).toBeLessThan(headingBox!.x);
  expect(factsBox!.y).toBeGreaterThanOrEqual(overviewBox!.y + overviewBox!.height - 1);
  expect(actionsBox!.y).toBeGreaterThanOrEqual(factsBox!.y + factsBox!.height - 1);
  expect(actionsBox!.y - (factsBox!.y + factsBox!.height)).toBeLessThanOrEqual(16);

  const resultColumns = await group.locator('.branch-class-grid')
    .evaluate((element) => getComputedStyle(element).gridTemplateColumns);
  expect(resultColumns.split(' ')).toHaveLength(3);
});

test('더보기는 기존 카드 위치를 유지하고 접기 왼쪽에서 확장한다', async ({ page }) => {
  await page.unroute('**/api/**');
  await mockApi(page, expandableCoursePage);
  await page.goto('/');

  const group = page.locator('.aggregate-course-group').first();
  const visibleCards = group.locator('.class-card:visible');
  const moreButton = group.getByRole('button', { name: /더 많은 강좌 보기/ });

  await expect(visibleCards).toHaveCount(10);
  await moreButton.scrollIntoViewIfNeeded();

  const scrollBefore = await page.evaluate(() => window.scrollY);
  const positionsBefore = await visibleCards.evaluateAll((cards) => cards.map((card) => {
    const box = card.getBoundingClientRect();
    return { x: box.x, y: box.y, width: box.width, height: box.height };
  }));

  await moreButton.click();
  await expect(visibleCards).toHaveCount(30);

  const footerButtons = group.locator('.branch-course-group-footer > button');
  await expect(footerButtons).toHaveCount(2);
  await expect(footerButtons.nth(0)).toHaveText('접기');
  await expect(footerButtons.nth(1)).toContainText('더 많은 강좌 보기');
  const [collapseBox, moreBox] = await Promise.all([
    footerButtons.nth(0).boundingBox(),
    footerButtons.nth(1).boundingBox(),
  ]);
  expect(collapseBox).not.toBeNull();
  expect(moreBox).not.toBeNull();
  expect(
    collapseBox!.y < moreBox!.y
      || (Math.abs(collapseBox!.y - moreBox!.y) < 1 && collapseBox!.x < moreBox!.x),
  ).toBe(true);

  const scrollAfter = await page.evaluate(() => window.scrollY);
  const positionsAfter = await visibleCards.evaluateAll((cards) => cards.slice(0, 10).map((card) => {
    const box = card.getBoundingClientRect();
    return { x: box.x, y: box.y, width: box.width, height: box.height };
  }));

  expect(scrollAfter).toBeCloseTo(scrollBefore, 0);
  positionsBefore.forEach((before, index) => {
    expect(positionsAfter[index].x).toBeCloseTo(before.x, 0);
    expect(positionsAfter[index].y).toBeCloseTo(before.y, 0);
    expect(positionsAfter[index].width).toBeCloseTo(before.width, 0);
    expect(positionsAfter[index].height).toBeCloseTo(before.height, 0);
  });
});

test('모달과 필터·지도 동적 상태의 레이아웃이 유지된다', async ({ page }, testInfo) => {
  await page.goto('/');
  await page.getByRole('button', { name: '로그인' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  const dialogBox = await page.getByRole('dialog').boundingBox();
  expect(dialogBox).not.toBeNull();
  expect(dialogBox?.width).toBeLessThanOrEqual(page.viewportSize()?.width ?? Number.POSITIVE_INFINITY);
  expect(dialogBox?.height).toBeLessThanOrEqual(page.viewportSize()?.height ?? Number.POSITIVE_INFINITY);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);

  if (testInfo.project.name.startsWith('mobile-')) {
    await page.goto('/?page=branches');
    const filterBar = page.locator('.mobile-filter-bar');
    const sidebar = page.locator('.sidebar-shell');
    await expect(filterBar).toBeVisible();
    await expect(sidebar).toBeHidden();
    const [filterBarBox, mapBox] = await Promise.all([
      filterBar.boundingBox(),
      page.locator('.google-map-card').boundingBox(),
    ]);
    expect(filterBarBox).not.toBeNull();
    expect(mapBox).not.toBeNull();
    expect(filterBarBox!.y).toBeLessThan(mapBox!.y);

    if (process.env.MOBILE_VISUAL_AUDIT === '1' && testInfo.project.name === 'mobile-390') {
      await page.screenshot({ path: 'test-results/mobile-simplified-branches-closed-390.png', fullPage: false });
    }

    await filterBar.getByRole('button', { name: '필터 열기' }).click();
    await expect(sidebar).toHaveClass(/\bopen\b/);
    await expect(sidebar).toBeVisible();
    await expect(sidebar).toHaveAttribute('role', 'dialog');
    const sidebarBox = await sidebar.boundingBox();
    expect(sidebarBox?.height).toBeLessThanOrEqual(page.viewportSize()?.height ?? Number.POSITIVE_INFINITY);

    if (process.env.MOBILE_VISUAL_AUDIT === '1' && testInfo.project.name === 'mobile-390') {
      await page.screenshot({ path: 'test-results/mobile-simplified-filter-open-390.png', fullPage: false });
    }

    await sidebar.getByRole('button', { name: '닫기' }).click();
    await expect(sidebar).not.toHaveClass(/\bopen\b/);
    await expect(sidebar).toBeHidden();

    await expect(page.locator('.google-map-card')).toBeVisible();
  } else {
    await page.goto('/?page=branches');
    const radiusButtons = page.locator('.nearby-radius-control [role="radio"]');
    await expect(radiusButtons).toHaveCount(3);
    await expect(radiusButtons).toHaveText(['5km', '10km', '20km']);
    await expect(page.getByRole('radio', { name: '지름 30km' })).toHaveCount(0);

    const branchRequest = page.waitForRequest((request) => {
      const url = new URL(request.url());
      return url.pathname === '/api/branches/nearby' && url.searchParams.get('radius_km') === '5';
    });
    const courseRequest = page.waitForRequest((request) => {
      const url = new URL(request.url());
      return url.pathname === '/api/courses/' && url.searchParams.get('radius_km') === '5';
    });
    await page.getByRole('radio', { name: '지름 10km' }).click();
    await Promise.all([branchRequest, courseRequest]);
    await expect(page.getByRole('radio', { name: '지름 10km' })).toHaveAttribute('aria-checked', 'true');
    await expect(page.locator('.nearby-list-heading')).toContainText('지름 10km');
    await page.locator('.main-quick-filter-location .main-quick-filter-chip').click();
    await expect(page.locator('.location-picker-modal')).toBeVisible();
  }

  const pageWidth = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(pageWidth.scrollWidth).toBeLessThanOrEqual(pageWidth.clientWidth + 1);
});

test('연령 필터는 영아·유아·아동의 기준 나이를 함께 표시한다', async ({ page }) => {
  await page.goto('/');

  await page.getByRole('button', { name: /^연령\s*전체$/ }).click();
  const menu = page.getByRole('group', { name: '연령 선택' });
  await expect(menu).toBeVisible();
  await expect(menu.getByText('0~23개월', { exact: true })).toBeVisible();
  await expect(menu.getByText('만 2~6세', { exact: true })).toBeVisible();
  await expect(menu.getByText('만 7~13세', { exact: true })).toBeVisible();

  const menuBox = await menu.boundingBox();
  const viewport = page.viewportSize();
  expect(menuBox).not.toBeNull();
  expect(viewport).not.toBeNull();
  expect(menuBox!.x).toBeGreaterThanOrEqual(0);
  expect(menuBox!.x + menuBox!.width).toBeLessThanOrEqual(viewport!.width + 1);
});

test('로그인 계정 메뉴는 본문 위에 표시되고 계정 모달보다 아래에 놓인다', async ({ page }) => {
  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'account-layer-user',
        provider: 'google',
        name: '테스트 사용자',
        email: 'layer@example.test',
      }),
    });
  });
  await page.goto('/');

  const accountTrigger = page.locator('.user-session-badge');
  await expect(accountTrigger).toBeVisible();
  await accountTrigger.click();

  const menu = page.getByRole('menu');
  const logoutItem = page.getByRole('menuitem', { name: '로그아웃' });
  await expect(menu).toBeVisible();
  await expect(logoutItem).toBeVisible();
  expect(await logoutItem.evaluate((item) => {
    const box = item.getBoundingClientRect();
    const topmost = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    return topmost === item || (topmost !== null && item.contains(topmost));
  })).toBe(true);

  await page.getByRole('menuitem', { name: '계정 정보' }).click();
  await expect(menu).toHaveCount(0);
  const accountDialog = page.getByRole('dialog', { name: '내정보' });
  await expect(accountDialog).toBeVisible();

  const layers = await page.evaluate(() => ({
    header: Number.parseInt(getComputedStyle(document.querySelector('.site-header') as Element).zIndex, 10),
    modal: Number.parseInt(getComputedStyle(document.querySelector('.modal-backdrop') as Element).zIndex, 10),
  }));
  expect(layers.header).toBeGreaterThan(7000);
  expect(layers.modal).toBeGreaterThan(layers.header);

  await accountDialog.getByRole('button', { name: '닫기' }).click();
  await page.getByRole('button', { name: '알림 보기' }).click();
  const notificationDialog = page.getByRole('dialog', { name: '알림' });
  await expect(notificationDialog).toBeVisible();
  expect(await page.locator('.notification-panel-backdrop').evaluate((backdrop) => {
    const header = document.querySelector('.site-header');
    if (!header) return false;
    return Number.parseInt(getComputedStyle(backdrop).zIndex, 10)
      > Number.parseInt(getComputedStyle(header).zIndex, 10);
  })).toBe(true);
});
