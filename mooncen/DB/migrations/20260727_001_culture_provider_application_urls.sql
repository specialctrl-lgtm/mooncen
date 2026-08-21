UPDATE courses
SET application_url = raw_url,
    updated_at = CURRENT_TIMESTAMP
WHERE provider IN (
    'HOMEPLUS',
    'EMART',
    'LOTTE',
    'HYUNDAI_DEPT',
    'GALLERIA',
    'AK_PLAZA',
    'ELAND_RETAIL',
    'SHINSEGAE_ACADEMY',
    'LOTTE_MART'
)
  AND (application_url IS NULL OR btrim(application_url) = '')
  AND raw_url IS NOT NULL
  AND btrim(raw_url) <> '';
