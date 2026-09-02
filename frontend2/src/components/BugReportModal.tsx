import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import { ImagePlus, Send, X } from 'lucide-react';
import { submitBugReport } from '../api';
import { useDialogAccessibility } from '../hooks/useDialogAccessibility';

const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const ALLOWED_IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);

type BugReportModalProps = {
  open: boolean;
  onClose: () => void;
  onSubmitted: () => void;
};

function fileAsBase64(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('이미지를 읽지 못했습니다. 다른 이미지를 선택해 주세요.'));
    reader.onload = () => {
      const result = typeof reader.result === 'string' ? reader.result : '';
      const separatorIndex = result.indexOf(',');
      if (separatorIndex < 0) {
        reject(new Error('이미지를 읽지 못했습니다. 다른 이미지를 선택해 주세요.'));
        return;
      }
      resolve(result.slice(separatorIndex + 1));
    };
    reader.readAsDataURL(file);
  });
}

function imageSizeLabel(size: number) {
  return `${(size / (1024 * 1024)).toFixed(1)}MB`;
}

export default function BugReportModal({ open, onClose, onSubmitted }: BugReportModalProps) {
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [image, setImage] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useDialogAccessibility<HTMLElement>(open, onClose);

  useEffect(() => {
    if (open) return;
    setTitle('');
    setContent('');
    setImage(null);
    setSubmitting(false);
    setError(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  }, [open]);

  const handleImageChange = (event: ChangeEvent<HTMLInputElement>) => {
    const selectedImage = event.currentTarget.files?.[0] || null;
    setError(null);

    if (!selectedImage) {
      setImage(null);
      return;
    }
    if (!ALLOWED_IMAGE_TYPES.has(selectedImage.type)) {
      setImage(null);
      event.currentTarget.value = '';
      setError('PNG, JPG, WebP 이미지만 첨부할 수 있습니다.');
      return;
    }
    if (selectedImage.size > MAX_IMAGE_BYTES) {
      setImage(null);
      event.currentTarget.value = '';
      setError('이미지는 5MB 이하로 첨부해 주세요.');
      return;
    }
    setImage(selectedImage);
  };

  const removeImage = () => {
    setImage(null);
    setError(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;

    const cleanedTitle = title.trim();
    const cleanedContent = content.trim();
    if (!cleanedTitle || !cleanedContent) {
      setError('제목과 내용을 모두 입력해 주세요.');
      return;
    }
    if (cleanedTitle.length < 2) {
      setError('제목은 2자 이상 입력해 주세요.');
      return;
    }
    if (cleanedContent.length < 10) {
      setError('내용은 10자 이상 입력해 주세요.');
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const imageBase64 = image ? await fileAsBase64(image) : null;
      await submitBugReport({
        title: cleanedTitle,
        content: cleanedContent,
        page_url: window.location.href.slice(0, 2048),
        user_agent: window.navigator.userAgent.slice(0, 512),
        viewport: `${window.innerWidth}x${window.innerHeight}`,
        image_filename: image?.name || null,
        image_media_type: image?.type || null,
        image_base64: imageBase64,
      });
      onSubmitted();
      onClose();
    } catch (submitError) {
      const message = submitError instanceof Error ? submitError.message : '';
      if (/^401\s/.test(message) || message.includes('로그인이 만료')) {
        setError('로그인이 만료되었습니다. 다시 로그인한 뒤 제보해 주세요.');
      } else if (/^429\s/.test(message)) {
        setError('제보 횟수가 많습니다. 잠시 후 다시 시도해 주세요.');
      } else if (/^503\s/.test(message)) {
        setError('제보 메일 서비스를 잠시 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.');
      } else {
        setError(message && !/^\d{3}\s/.test(message)
          ? message
          : '버그 제보를 보내지 못했습니다. 잠시 후 다시 시도해 주세요.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;

  return (
    <div className="modal-backdrop bug-report-backdrop" role="presentation" onMouseDown={() => !submitting && onClose()}>
      <section
        ref={dialogRef}
        className="bug-report-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="bug-report-title"
        aria-describedby="bug-report-description"
        aria-busy={submitting}
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="bug-report-modal-header">
          <div>
            <h2 id="bug-report-title">버그 제보</h2>
            <p id="bug-report-description">발견한 문제를 알려주시면 확인 후 개선하겠습니다.</p>
          </div>
          <button type="button" aria-label="버그 제보 닫기" disabled={submitting} onClick={onClose}>
            <X size={19} strokeWidth={2} aria-hidden="true" />
          </button>
        </header>

        <form className="bug-report-form" aria-label="버그 제보 양식" onSubmit={handleSubmit}>
          <label className="bug-report-field" htmlFor="bug-report-subject">
            <span>제목 <em aria-hidden="true">*</em></span>
            <input
              id="bug-report-subject"
              type="text"
              value={title}
              required
              minLength={2}
              maxLength={120}
              placeholder="어떤 문제가 발생했나요?"
              disabled={submitting}
              onChange={(event) => setTitle(event.currentTarget.value)}
            />
            <small>{title.length}/120</small>
          </label>

          <label className="bug-report-field" htmlFor="bug-report-content">
            <span>내용 <em aria-hidden="true">*</em></span>
            <textarea
              id="bug-report-content"
              value={content}
              required
              minLength={10}
              maxLength={5000}
              rows={7}
              placeholder="문제가 발생한 화면과 동작 순서를 자세히 적어주세요."
              disabled={submitting}
              onChange={(event) => setContent(event.currentTarget.value)}
            />
            <small>{content.length}/5,000</small>
          </label>

          <div className="bug-report-image-field">
            <span>이미지 첨부 <small>선택 · 최대 5MB</small></span>
            <label className="bug-report-image-picker" htmlFor="bug-report-image">
              <ImagePlus size={18} strokeWidth={2} aria-hidden="true" />
              <span>{image ? '다른 이미지 선택' : '스크린샷 선택'}</span>
              <input
                ref={fileInputRef}
                id="bug-report-image"
                type="file"
                aria-label="이미지 첨부"
                accept="image/png,image/jpeg,image/webp"
                disabled={submitting}
                onChange={handleImageChange}
              />
            </label>
            {image && (
              <div className="bug-report-image-summary">
                <span title={image.name}>{image.name}</span>
                <small>{imageSizeLabel(image.size)}</small>
                <button type="button" disabled={submitting} onClick={removeImage}>삭제</button>
              </div>
            )}
          </div>

          <p className="bug-report-diagnostics">현재 페이지 주소와 브라우저 정보가 문제 확인을 위해 함께 전달됩니다.</p>
          {error && <p className="bug-report-error" role="alert">{error}</p>}

          <div className="bug-report-actions">
            <button type="button" disabled={submitting} onClick={onClose}>취소</button>
            <button className="bug-report-submit" type="submit" disabled={submitting}>
              <Send size={17} strokeWidth={2} aria-hidden="true" />
              {submitting ? '보내는 중' : '제보 보내기'}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
