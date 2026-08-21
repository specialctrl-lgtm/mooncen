-- ==========================================
-- MoonCen DB 스키마 개선 사항
-- ==========================================

-- 1. courses 테이블에 description 컬럼 추가 (AI 요약 생성에 필요)
ALTER TABLE courses ADD COLUMN IF NOT EXISTS description TEXT;

-- 2. courses 테이블에 apply_start, apply_end 컬럼 추가 (접수 기간 관리)
ALTER TABLE courses ADD COLUMN IF NOT EXISTS apply_start DATE;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS apply_end DATE;

-- 3. courses 테이블에 full-text 검색 인덱스 추가 (제목 검색 최적화)
-- Note: 한글 검색 최적화를 위해서는 pg_trgm 확장 사용 권장
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_courses_title_trgm ON courses USING gin(title gin_trgm_ops);

-- 4. branches 테이블에 운영 시간 컬럼 추가
ALTER TABLE branches ADD COLUMN IF NOT EXISTS operating_hours TEXT;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS website_url TEXT;

-- 5. courses 테이블에 이미지 URL 컬럼 추가 (강좌 썸네일)
ALTER TABLE courses ADD COLUMN IF NOT EXISTS image_url TEXT;

-- 6. 강좌 조회수 추적을 위한 컬럼
ALTER TABLE courses ADD COLUMN IF NOT EXISTS view_count INTEGER DEFAULT 0;

-- 7. 사용자 찜 기능을 위한 테이블 (향후 확장)
CREATE TABLE IF NOT EXISTS user_favorites (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(100) NOT NULL,  -- 향후 사용자 시스템 연동
    course_id UUID REFERENCES courses(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT unique_user_favorite UNIQUE(user_id, course_id)
);

CREATE INDEX IF NOT EXISTS idx_user_favorites_user ON user_favorites(user_id);

-- 8. 검색 로그 테이블 (검색 패턴 분석용)
CREATE TABLE IF NOT EXISTS search_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    search_query TEXT NOT NULL,
    filters JSONB,  -- 적용된 필터 정보
    result_count INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_search_logs_created ON search_logs(created_at DESC);

-- 9. 강좌 상태에 대한 CHECK 제약조건 추가
ALTER TABLE courses 
DROP CONSTRAINT IF EXISTS chk_course_status,
ADD CONSTRAINT chk_course_status 
CHECK (status IN ('OPEN', 'SCHEDULED', 'CLOSED', 'WAITING'));

-- 10. 알림 타입에 대한 CHECK 제약조건
ALTER TABLE notifications 
DROP CONSTRAINT IF EXISTS chk_notification_type,
ADD CONSTRAINT chk_notification_type 
CHECK (notification_type IN ('OPEN', 'START', 'DEADLINE'));

COMMENT ON TABLE branches IS '문화센터 지점 정보';
COMMENT ON TABLE courses IS '문화센터 강좌 정보';
COMMENT ON TABLE notifications IS '사용자 알림 예약';
COMMENT ON TABLE user_favorites IS '사용자 찜 목록';
COMMENT ON TABLE search_logs IS '검색 로그 (분석용)';