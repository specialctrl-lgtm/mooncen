from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, Time, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base


class Branch(Base):
    __tablename__ = "branches"

    id = Column(UUID(as_uuid=True), primary_key=True, index=True)
    provider = Column(String(50), nullable=False)
    branch_code = Column(String(50), nullable=False)
    name = Column(String(100), nullable=False)
    address = Column(Text)
    phone = Column(String(50))
    lat = Column(Numeric(10, 7), nullable=True)
    lon = Column(Numeric(10, 7), nullable=True)
    operating_hours = Column(Text)
    website_url = Column(Text)
    facility_type = Column(String(80))
    facility_category = Column(String(80))
    facility_source = Column(Text)
    facility_source_sheet = Column(Text)
    facility_service_group = Column(String(50))
    facility_collection_category = Column(String(50))
    region_sido = Column(String(50))
    region_sigungu = Column(String(80))
    regular_holiday = Column(Text)
    admission_fee = Column(Text)
    basic_info = Column(JSONB)
    address_source = Column(Text)
    coordinate_source = Column(Text)
    location_confidence = Column(Integer, server_default=text("0"))
    location_verified = Column(Boolean, server_default=text("false"))
    location_checked_at = Column(DateTime(timezone=True))
    location_query = Column(Text)
    geocode_status = Column(String(32), nullable=False, server_default=text("'pending'"))
    geocode_reason_code = Column(String(100))
    geocode_attempt_count = Column(Integer, nullable=False, server_default=text("0"))
    geocode_candidates = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    geocode_next_retry_at = Column(DateTime(timezone=True))
    geocode_last_error = Column(Text)
    geocode_last_attempt_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Course(Base):
    __tablename__ = "courses"

    id = Column(UUID(as_uuid=True), primary_key=True, index=True)
    provider = Column(String(50), nullable=False)
    provider_course_id = Column(String(100), nullable=False)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id", ondelete="RESTRICT"))

    title = Column(String(255), nullable=False)
    title_raw = Column(String(255))
    title_prefix_removed = Column(Text)
    instructor = Column(String(100))
    target = Column(String(100))
    category_raw = Column(String(100))
    collection_category = Column(String(50))
    domain_category = Column(String(50))
    standard_category_key = Column(String(80))
    standard_category_label = Column(String(80))
    source_group = Column(String(50))
    operator_type = Column(String(50))
    service_group = Column(String(50))
    collection_type = Column(String(50))

    fee = Column(Numeric)
    material_fee = Column(Integer)
    sessions = Column(Integer)
    schedule_raw = Column(Text)
    schedule_days = Column(ARRAY(Text))
    schedule_dates = Column(JSONB)
    schedule_time_start = Column(Time)
    schedule_time_end = Column(Time)
    schedule_frequency = Column(String(20))
    schedule_duration_minutes = Column(Integer)

    start_date = Column(Date)
    end_date = Column(Date)
    apply_start = Column(Date)
    apply_end = Column(Date)
    apply_period_raw = Column(Text)

    capacity_total = Column(Integer)
    capacity_current = Column(Integer)
    capacity_remaining = Column(Integer)
    waitlist_total = Column(Integer)

    venue_name = Column(String(150))
    venue_address = Column(Text)
    application_url = Column(Text)
    application_type = Column(String(30))
    application_method_raw = Column(Text)
    reservation_available = Column(Boolean, default=False)
    discovery_status = Column(String(50))
    program_type = Column(String(50))
    eligibility_raw = Column(Text)
    raw_fields = Column(JSONB)

    status = Column(String(50))
    source_endpoint = Column(Text)
    raw_url = Column(Text)
    description = Column(Text)
    image_url = Column(Text)
    view_count = Column(Integer, server_default=text("0"))
    is_active = Column(Boolean, default=True, nullable=False)
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    removed_at = Column(DateTime(timezone=True))
    content_hash = Column(Text)
    change_detected_at = Column(DateTime(timezone=True))

    ai_category = Column(String(100))
    ai_tags = Column(Text)
    ai_summary = Column(Text)
    search_document = Column(TSVECTOR)
    is_ai_processed = Column(Boolean, default=False)
    ai_title_processed = Column(Boolean, default=False)
    ai_title_confidence = Column(Numeric(4, 3))
    ai_title_result = Column(JSONB)

    target_age_group = Column(String(20))
    target_min_age = Column(Integer)
    target_max_age = Column(Integer)
    target_with_parent = Column(Boolean, default=False)
    target_tags = Column(ARRAY(Text))
    target_age_is_explicit = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    branch = relationship("Branch")


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"), index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    password_hash = Column(Text)
    provider = Column(String(30), nullable=False, default="email")
    auth_token_version = Column(Integer, nullable=False, server_default=text("1"))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    oauth_accounts = relationship("OAuthAccount", back_populates="user", cascade="all, delete-orphan")
    privacy_acceptances = relationship(
        "UserPrivacyAcceptance",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class OAuthAccount(Base):
    __tablename__ = "oauth_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"), index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    provider = Column(String(30), nullable=False)
    provider_user_id = Column(String(255), nullable=False)
    email = Column(String(255))
    email_verified = Column(Boolean, nullable=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="oauth_accounts")


class PrivacyNoticeVersion(Base):
    __tablename__ = "privacy_notice_versions"

    version = Column(String(32), primary_key=True)
    notice_type = Column(String(32), nullable=False)
    legal_basis = Column(String(32), nullable=False)
    notice_hash = Column(String(64), nullable=False)
    notice_json = Column(JSONB, nullable=False)
    effective_date = Column(Date, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    acceptances = relationship("UserPrivacyAcceptance", back_populates="notice")


class UserPrivacyAcceptance(Base):
    __tablename__ = "user_privacy_acceptances"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "notice_version",
            "acceptance_type",
            name="uq_user_privacy_acceptances_notice",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    notice_version = Column(
        String(32),
        ForeignKey("privacy_notice_versions.version"),
        nullable=False,
    )
    acceptance_type = Column(String(32), nullable=False)
    acquisition_method = Column(String(32), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User", back_populates="privacy_acceptances")
    notice = relationship("PrivacyNoticeVersion", back_populates="acceptances")


class UserCourseMark(Base):
    __tablename__ = "user_course_marks"
    __table_args__ = (UniqueConstraint("user_id", "course_id", "mark_type", name="unique_user_course_mark"),)

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()"), index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id"), nullable=False)
    mark_type = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User")
    course = relationship("Course")


class UserCourseNotificationSetting(Base):
    __tablename__ = "user_course_notification_settings"
    __table_args__ = (
        UniqueConstraint("user_id", "course_id", name="unique_user_course_notification_setting"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()"), index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id"), nullable=False)
    start_alarm_enabled = Column(Boolean, nullable=False, server_default=text("true"))
    start_alarm_minutes_before = Column(Integer, nullable=False, server_default=text("1440"))
    registration_alarm_enabled = Column(Boolean, nullable=False, server_default=text("true"))
    registration_alarm_minutes_before = Column(Integer, nullable=False, server_default=text("1440"))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User")
    course = relationship("Course")


class UserFavoriteCourse(Base):
    __tablename__ = "user_favorite_courses"
    __table_args__ = (UniqueConstraint("user_id", "course_url", name="unique_user_favorite_course_url"),)

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id", ondelete="SET NULL"))
    course_url = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User")
    course = relationship("Course")


class CourseAlert(Base):
    __tablename__ = "course_alerts"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id", ondelete="SET NULL"))
    course_url = Column(Text)
    alert_type = Column(Text, nullable=False)
    alert_status = Column(Text, nullable=False, server_default=text("'pending'"))
    scheduled_at = Column(DateTime(timezone=True))
    sent_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User")
    course = relationship("Course")


class CourseUpdateRequest(Base):
    __tablename__ = "course_update_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()"), index=True)
    course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    reason = Column(String(40), nullable=False, server_default=text("'click'"))
    status = Column(String(24), nullable=False, server_default=text("'pending'"))
    source_url = Column(Text)
    request_count = Column(Integer, nullable=False, server_default=text("1"))
    requested_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    last_checked_at = Column(DateTime(timezone=True))
    check_result = Column(JSONB)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    course = relationship("Course")
