-- Record explicit, versioned privacy consent for new membership enrollment.
-- Existing users are intentionally not backfilled: absence of evidence must
-- not be converted into an acceptance that did not occur.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '5min';

CREATE TABLE IF NOT EXISTS privacy_notice_versions (
    version VARCHAR(32) PRIMARY KEY,
    notice_type VARCHAR(32) NOT NULL DEFAULT 'membership',
    legal_basis VARCHAR(32) NOT NULL DEFAULT 'consent',
    notice_hash CHAR(64) NOT NULL,
    notice_json JSONB NOT NULL,
    effective_date DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_privacy_notice_versions_type
        CHECK (notice_type = 'membership'),
    CONSTRAINT chk_privacy_notice_versions_legal_basis
        CHECK (legal_basis = 'consent'),
    CONSTRAINT chk_privacy_notice_versions_hash
        CHECK (notice_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_privacy_notice_versions_json_object
        CHECK (jsonb_typeof(notice_json) = 'object'),
    CONSTRAINT chk_privacy_notice_versions_json_version
        CHECK ((notice_json ->> 'version') = version),
    CONSTRAINT chk_privacy_notice_versions_json_effective_date
        CHECK ((notice_json ->> 'effective_date') = effective_date::TEXT)
);

CREATE TABLE IF NOT EXISTS user_privacy_acceptances (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    notice_version VARCHAR(32) NOT NULL REFERENCES privacy_notice_versions(version),
    acceptance_type VARCHAR(32) NOT NULL DEFAULT 'consent_granted',
    acquisition_method VARCHAR(32) NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_user_privacy_acceptances_type
        CHECK (acceptance_type = 'consent_granted'),
    CONSTRAINT chk_user_privacy_acceptances_method
        CHECK (acquisition_method IN ('email_signup', 'google_signup', 'naver_signup')),
    CONSTRAINT uq_user_privacy_acceptances_notice
        UNIQUE (user_id, notice_version, acceptance_type)
);

CREATE INDEX IF NOT EXISTS ix_user_privacy_acceptances_notice_version
    ON user_privacy_acceptances(notice_version);

INSERT INTO privacy_notice_versions (
    version,
    notice_type,
    legal_basis,
    notice_hash,
    notice_json,
    effective_date
)
VALUES (
    '2026-08-10',
    'membership',
    'consent',
    '4c01b656b92713aa35bf24149a12ce45e3e17f856d54beb950da4a69ad6e9000',
    $membership_notice${
      "version": "2026-08-10",
      "effective_date": "2026-08-10",
      "title": "개인정보 수집·이용 안내",
      "purpose": "회원 식별 및 로그인, 찜·내 강좌·알림 등 회원 기능 제공",
      "items": [
        "이메일 주소",
        "이름(표시명)",
        "OAuth 제공자 식별자(소셜 가입 시)",
        "비밀번호(암호화하여 저장, 이메일 가입 시)"
      ],
      "retention": "회원 탈퇴 시까지. 다만 관계 법령에 따라 보존할 의무가 있는 경우 해당 기간 동안 보관합니다.",
      "refusal": "동의를 거부할 수 있으나, 거부하면 회원가입 및 회원 기능을 이용할 수 없습니다.",
      "consent_label": "[필수] 개인정보 수집·이용에 동의합니다."
    }$membership_notice$::JSONB,
    DATE '2026-08-10'
)
ON CONFLICT (version) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM privacy_notice_versions
        WHERE version = '2026-08-10'
          AND notice_type = 'membership'
          AND legal_basis = 'consent'
          AND notice_hash = '4c01b656b92713aa35bf24149a12ce45e3e17f856d54beb950da4a69ad6e9000'
          AND effective_date = DATE '2026-08-10'
          AND notice_json = $membership_notice${
            "version": "2026-08-10",
            "effective_date": "2026-08-10",
            "title": "개인정보 수집·이용 안내",
            "purpose": "회원 식별 및 로그인, 찜·내 강좌·알림 등 회원 기능 제공",
            "items": [
              "이메일 주소",
              "이름(표시명)",
              "OAuth 제공자 식별자(소셜 가입 시)",
              "비밀번호(암호화하여 저장, 이메일 가입 시)"
            ],
            "retention": "회원 탈퇴 시까지. 다만 관계 법령에 따라 보존할 의무가 있는 경우 해당 기간 동안 보관합니다.",
            "refusal": "동의를 거부할 수 있으나, 거부하면 회원가입 및 회원 기능을 이용할 수 없습니다.",
            "consent_label": "[필수] 개인정보 수집·이용에 동의합니다."
          }$membership_notice$::JSONB
    ) THEN
        RAISE EXCEPTION 'privacy notice version 2026-08-10 does not match its reviewed seed';
    END IF;
END $$;

COMMENT ON TABLE privacy_notice_versions IS
    'Versioned membership privacy disclosures; existing versions are never rewritten';
COMMENT ON TABLE user_privacy_acceptances IS
    'Explicit privacy consent recorded only for new signup completion; no historical backfill';
