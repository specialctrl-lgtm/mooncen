import json
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from utils.url_security import safe_external_http_url


class ScopeCourseCounts(BaseModel):
    provider: int = Field(default=0, ge=0)
    education: int = Field(default=0, ge=0)
    experience: int = Field(default=0, ge=0)


class BranchBase(BaseModel):
    id: str
    branch_ids: List[str] = Field(default_factory=list)
    providers: List[str] = Field(default_factory=list)
    name: str
    provider: str
    provider_label: Optional[str] = None
    branch_code: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    course_count: int = 0
    active_course_count: int = 0
    open_course_count: int = 0
    collection_categories: List[str] = Field(default_factory=list)
    category_counts: Dict[str, int] = Field(default_factory=dict)
    primary_collection_category: Optional[str] = None
    service_groups: List[str] = Field(default_factory=list)
    service_group_counts: Dict[str, int] = Field(default_factory=dict)
    primary_service_group: Optional[str] = None
    # Only /branches/nearby computes this aggregate. Embedded branches returned
    # by /courses must stay null so clients never mistake an uncomputed value
    # for authoritative all-zero counts.
    scope_course_counts: Optional[ScopeCourseCounts] = None
    website_url: Optional[str] = None
    favicon_url: Optional[str] = None
    operating_hours: Optional[str] = None
    regular_holiday: Optional[str] = None
    admission_fee: Optional[str] = None
    facility_type: Optional[str] = None
    facility_category: Optional[str] = None
    facility_source: Optional[str] = None
    facility_source_sheet: Optional[str] = None
    facility_service_group: Optional[str] = None
    facility_collection_category: Optional[str] = None
    region_sido: Optional[str] = None
    region_sigungu: Optional[str] = None
    basic_info: Optional[dict] = None

    @field_validator("website_url", "favicon_url", mode="before")
    @classmethod
    def sanitize_branch_urls(cls, value):
        return safe_external_http_url(value) or None


class Branch(BranchBase):
    model_config = ConfigDict(from_attributes=True)


class ProviderMeta(BaseModel):
    provider: str
    label: str
    marker_label: str
    marker_color: str
    branch_count: int = 0
    coordinate_count: int = 0
    course_count: int = 0
    active_course_count: int = 0
    open_course_count: int = 0


class CourseBase(BaseModel):
    id: str
    provider: str
    provider_label: Optional[str] = None
    provider_course_id: Optional[str] = None
    branch_id: Optional[str] = None
    title: str
    title_raw: Optional[str] = None
    title_prefix_removed: Optional[str] = None
    instructor: Optional[str] = None
    fee: Optional[float] = None
    fee_status: Literal["UNKNOWN", "FREE", "PAID"] = "UNKNOWN"
    material_fee: Optional[int] = None
    sessions: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    apply_start: Optional[str] = None
    apply_end: Optional[str] = None
    apply_period_raw: Optional[str] = None
    capacity_total: Optional[int] = None
    capacity_current: Optional[int] = None
    capacity_remaining: Optional[int] = None
    waitlist_total: Optional[int] = None
    venue_name: Optional[str] = None
    venue_address: Optional[str] = None
    application_url: Optional[str] = None
    application_type: Optional[str] = None
    application_method_raw: Optional[str] = None
    reservation_available: Optional[bool] = False
    discovery_status: Optional[str] = None
    program_type: Optional[str] = None
    eligibility_raw: Optional[str] = None
    status: Optional[str] = None
    status_label: Optional[str] = None
    target: Optional[str] = None
    target_age_group: Optional[str] = None
    target_min_age: Optional[int] = None
    target_max_age: Optional[int] = None
    target_age_is_explicit: Optional[bool] = False
    target_tags: List[str] = Field(default_factory=list)
    category_raw: Optional[str] = None
    collection_category: Optional[str] = None
    domain_category: Optional[str] = None
    standard_category: Optional[str] = None
    source_group: Optional[str] = None
    operator_type: Optional[str] = None
    service_group: Optional[str] = None
    collection_type: Optional[str] = None
    schedule_raw: Optional[str] = None
    schedule_days: List[str] = Field(default_factory=list)
    schedule_dates: List[str] = Field(default_factory=list)
    schedule_time_start: Optional[str] = None
    schedule_time_end: Optional[str] = None
    schedule_summary: Optional[str] = None
    day_schedule: Optional[str] = None
    session_label: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    view_count: Optional[int] = 0
    raw_url: Optional[str] = None
    is_active: Optional[bool] = True
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    removed_at: Optional[str] = None
    change_detected_at: Optional[str] = None
    created_at: Optional[str] = None
    ai_category: Optional[str] = None
    ai_tags: List[str] = Field(default_factory=list)
    ai_summary: Optional[str] = None
    ai_title_processed: Optional[bool] = False
    ai_title_confidence: Optional[float] = None
    ai_title_result: Optional[dict] = None
    branch: Optional[Branch] = None

    @field_validator("application_url", "image_url", "raw_url", mode="before")
    @classmethod
    def sanitize_external_urls(cls, value):
        return safe_external_http_url(value) or None

    @field_validator("ai_tags", "target_tags", mode="before")
    @classmethod
    def parse_tags(cls, value):
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else []
            except Exception:
                return [part.strip().lstrip("#") for part in value.split(",") if part.strip()]
        return []

    @field_validator("schedule_days", "schedule_dates", mode="before")
    @classmethod
    def parse_schedule_lists(cls, value):
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        return []


class Course(CourseBase):
    model_config = ConfigDict(from_attributes=True)


class CourseListResponse(BaseModel):
    total: int
    page: int
    size: int
    items: List[Course]
