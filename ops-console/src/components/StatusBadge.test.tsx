import '@testing-library/jest-dom/vitest';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import StatusBadge from './StatusBadge';

describe('StatusBadge', () => {
  it('uses the shared Korean status vocabulary', () => {
    render(<StatusBadge status="critical" />);

    expect(screen.getByText('장애')).toHaveClass('status-critical');
  });

  it('renders an unrecognized backend status without claiming success', () => {
    render(<StatusBadge status="not_connected" />);

    expect(screen.getByText('not_connected')).toHaveClass('status-not_connected');
  });
});
