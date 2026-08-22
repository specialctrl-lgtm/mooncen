import '@testing-library/jest-dom/vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it } from 'vitest';
import { OpsProvider } from '../context';
import Layout from './Layout';

describe('Layout', () => {
  it('shows the crawler lifecycle menus and the real environment', () => {
    render(
      <OpsProvider
        session={{
          user: { id: 'operator-id', email: 'operator@example.test', name: '운영자' },
          role: 'operator',
          environment: 'production',
        }}
      >
        <MemoryRouter initialEntries={['/dashboard']}>
          <Routes>
            <Route path="/" element={<Layout />}>
              <Route path="dashboard" element={<div>dashboard body</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </OpsProvider>,
    );

    expect(screen.getByText('PRODUCTION')).toBeInTheDocument();
    expect(screen.getByText('dashboard body')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '운영 콘솔 메뉴' }).querySelectorAll('a')).toHaveLength(11);
    expect(screen.getByRole('link', { name: /Crawler Studio/ })).toHaveAttribute('href', '/crawler-studio');
    expect(screen.getByRole('link', { name: /Crawler Improvements/ })).toHaveAttribute('href', '/crawler-improvements');
    expect(screen.getByRole('link', { name: /Crawler Releases/ })).toHaveAttribute('href', '/crawler-releases');
    expect(screen.getByRole('link', { name: /Crawler Analytics/ })).toHaveAttribute('href', '/crawler-analytics');
  });
});
