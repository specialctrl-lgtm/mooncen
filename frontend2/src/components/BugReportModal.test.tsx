import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { submitBugReport } from '../api';
import BugReportModal from './BugReportModal';

vi.mock('../api', () => ({
  submitBugReport: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(submitBugReport).mockReset();
  vi.mocked(submitBugReport).mockResolvedValue({ status: 'accepted' });
});

afterEach(() => cleanup());

function renderModal() {
  const onClose = vi.fn();
  const onSubmitted = vi.fn();
  render(<BugReportModal open onClose={onClose} onSubmitted={onSubmitted} />);
  return { onClose, onSubmitted };
}

function fillValidReport() {
  fireEvent.change(screen.getByLabelText(/제목/), { target: { value: '검색 결과가 보이지 않아요' } });
  fireEvent.change(screen.getByLabelText(/내용/), { target: { value: '검색 버튼을 누르면 결과 목록이 사라집니다.' } });
}

describe('BugReportModal', () => {
  it('validates trimmed title and content lengths before submitting', () => {
    renderModal();
    fireEvent.change(screen.getByLabelText(/제목/), { target: { value: '한' } });
    fireEvent.change(screen.getByLabelText(/내용/), { target: { value: '짧은 내용' } });

    fireEvent.submit(screen.getByRole('form', { name: '버그 제보 양식' }));

    expect(screen.getByRole('alert').textContent).toContain('제목은 2자 이상');
    expect(submitBugReport).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText(/제목/), { target: { value: '정상 제목' } });
    fireEvent.submit(screen.getByRole('form', { name: '버그 제보 양식' }));
    expect(screen.getByRole('alert').textContent).toContain('내용은 10자 이상');
  });

  it('rejects unsupported image formats', () => {
    renderModal();
    const imageInput = screen.getByLabelText('이미지 첨부');
    const unsupported = new File(['not-an-image'], 'capture.svg', { type: 'image/svg+xml' });

    fireEvent.change(imageInput, { target: { files: [unsupported] } });

    expect(screen.getByRole('alert').textContent).toContain('PNG, JPG, WebP');
    expect(submitBugReport).not.toHaveBeenCalled();
  });

  it('rejects images larger than 5MB', () => {
    renderModal();
    const imageInput = screen.getByLabelText('이미지 첨부');
    const oversized = new File([new Uint8Array(5 * 1024 * 1024 + 1)], 'large.png', { type: 'image/png' });

    fireEvent.change(imageInput, { target: { files: [oversized] } });

    expect(screen.getByRole('alert').textContent).toContain('5MB 이하');
    expect(submitBugReport).not.toHaveBeenCalled();
  });

  it('encodes an optional image and sends diagnostics with the report', async () => {
    const { onClose, onSubmitted } = renderModal();
    fillValidReport();
    const image = new File(['pixel'], '화면.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText('이미지 첨부'), { target: { files: [image] } });

    fireEvent.submit(screen.getByRole('form', { name: '버그 제보 양식' }));

    await waitFor(() => expect(submitBugReport).toHaveBeenCalledOnce());
    expect(submitBugReport).toHaveBeenCalledWith(expect.objectContaining({
      title: '검색 결과가 보이지 않아요',
      content: '검색 버튼을 누르면 결과 목록이 사라집니다.',
      page_url: window.location.href,
      user_agent: window.navigator.userAgent,
      viewport: `${window.innerWidth}x${window.innerHeight}`,
      image_filename: '화면.png',
      image_media_type: 'image/png',
      image_base64: 'cGl4ZWw=',
    }));
    expect(onSubmitted).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('shows a Korean recovery message when the mail service is unavailable', async () => {
    vi.mocked(submitBugReport).mockRejectedValue(new Error('503 Service Unavailable'));
    renderModal();
    fillValidReport();

    fireEvent.submit(screen.getByRole('form', { name: '버그 제보 양식' }));

    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('제보 메일 서비스를 잠시 사용할 수 없습니다'));
  });
});
