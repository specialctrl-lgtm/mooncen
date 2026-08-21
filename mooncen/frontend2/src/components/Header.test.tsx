import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Header from './Header';

afterEach(() => cleanup());

function renderHeader(onShowBugReport = vi.fn(), onSubmitSearch = vi.fn()) {
  render(
    <Header
      keyword="미술"
      favoriteCount={0}
      appliedCount={0}
      notificationCount={0}
      loggedIn={false}
      onHomeClick={vi.fn()}
      onKeywordChange={vi.fn()}
      onSubmitSearch={onSubmitSearch}
      onShowBugReport={onShowBugReport}
      onShowFavorites={vi.fn()}
      onShowApplied={vi.fn()}
      onShowNotifications={vi.fn()}
      onShowAccount={vi.fn()}
      onToggleLogin={vi.fn()}
    />,
  );
  return { onShowBugReport, onSubmitSearch };
}

describe('Header actions', () => {
  it('opens bug reporting without changing the search behavior', () => {
    const { onShowBugReport, onSubmitSearch } = renderHeader();

    fireEvent.click(screen.getByRole('button', { name: '버그 제보' }));
    fireEvent.submit(screen.getByRole('search'));

    expect(onShowBugReport).toHaveBeenCalledOnce();
    expect(onSubmitSearch).toHaveBeenCalledOnce();
  });

  it('closes the account dropdown before opening account information', () => {
    const onShowAccount = vi.fn();
    render(
      <Header
        keyword=""
        favoriteCount={0}
        appliedCount={0}
        notificationCount={0}
        loggedIn
        userName="테스트 사용자"
        onHomeClick={vi.fn()}
        onKeywordChange={vi.fn()}
        onSubmitSearch={vi.fn()}
        onShowBugReport={vi.fn()}
        onShowFavorites={vi.fn()}
        onShowApplied={vi.fn()}
        onShowNotifications={vi.fn()}
        onShowAccount={onShowAccount}
        onToggleLogin={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByTitle('테스트 사용자'));
    expect(screen.getByRole('menu')).toBeTruthy();

    fireEvent.click(screen.getByRole('menuitem', { name: '계정 정보' }));
    expect(screen.queryByRole('menu')).toBeNull();
    expect(onShowAccount).toHaveBeenCalledOnce();
  });
});
