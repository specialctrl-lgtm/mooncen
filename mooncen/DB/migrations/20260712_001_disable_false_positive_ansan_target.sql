-- This provider was generated from one municipal notice URL and was proven to
-- collect passport guidance/navigation as active courses. The canonical
-- ANSAN lifelong-learning target is MUNI_LLL_ANSAN_GO_KR_691646BE.
UPDATE courses
   SET is_active = FALSE,
       removed_at = COALESCE(removed_at, CURRENT_TIMESTAMP),
       updated_at = CURRENT_TIMESTAMP
 WHERE provider = 'MUNI_WWW_ANSAN_GO_KR_6D59A725'
   AND is_active IS TRUE;

-- The legacy generic Galleria collector treated global navigation, store
-- information, and personal payment pages as courses. Keep the predicate
-- narrow so a later dedicated collector can store real classes normally.
UPDATE courses
   SET is_active = FALSE,
       removed_at = COALESCE(removed_at, CURRENT_TIMESTAMP),
       updated_at = CURRENT_TIMESTAMP
 WHERE provider = 'GALLERIA'
   AND is_active IS TRUE
   AND (
       title ~ '(로그인|신규 카드 신청|영업정보|개인결제창)'
       OR raw_url = 'https://dept.galleria.co.kr/g-culture/culture-center/branch'
   );
