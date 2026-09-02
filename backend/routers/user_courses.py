from datetime import date, datetime
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, joinedload

from utils.url_security import safe_course_reference, safe_external_http_url
from DB.course_lifecycle import effective_course_status

from .. import models, schemas
from ..database import get_db
from .auth import get_current_user
from .courses import _serialize_course


router = APIRouter(prefix="/users/me", tags=["user-courses"])
api_router = APIRouter(prefix="/api/user-courses", tags=["user-courses"])

MarkType = Literal["favorite", "applied"]
AlertType = Literal["registration_open", "registration_closing", "seat_available", "new_course"]
AlertStatus = Literal["pending", "sent", "skipped", "failed"]


class CourseMarkRequest(BaseModel):
    mark_type: MarkType


class FavoriteCourseRequest(BaseModel):
    course_id: Optional[str] = None
    course_url: Optional[str] = Field(default=None, max_length=4096)


class FavoriteCourseItem(BaseModel):
    id: int
    course_id: Optional[str] = None
    course_url: str
    created_at: Optional[str] = None
    alert_badges: list[str] = []
    course: Optional[dict[str, Any]] = None

    @field_validator("course_url", mode="before")
    @classmethod
    def sanitize_course_url(cls, value):
        return safe_course_reference(value)


class FavoriteCourseListResponse(BaseModel):
    total: int
    items: list[FavoriteCourseItem]


class CourseAlertRequest(BaseModel):
    course_id: Optional[str] = None
    course_url: Optional[str] = Field(default=None, max_length=4096)
    alert_type: AlertType
    scheduled_at: Optional[datetime] = None


class CourseAlertItem(BaseModel):
    id: int
    user_id: str
    course_id: Optional[str] = None
    course_url: Optional[str] = None
    alert_type: AlertType
    alert_status: AlertStatus
    scheduled_at: Optional[str] = None
    sent_at: Optional[str] = None
    created_at: Optional[str] = None
    course: Optional[dict[str, Any]] = None

    @field_validator("course_url", mode="before")
    @classmethod
    def sanitize_course_url(cls, value):
        return safe_course_reference(value) or None


class CourseAlertListResponse(BaseModel):
    total: int
    items: list[CourseAlertItem]


class CourseMarkIdsResponse(BaseModel):
    favorite_course_ids: list[str]
    applied_course_ids: list[str]


class UserNotificationItem(BaseModel):
    id: str
    course_id: str
    notification_type: str
    title: str
    message: str
    priority: int
    event_date: Optional[str] = None
    mark_type: MarkType
    course: schemas.Course


class UserNotificationsResponse(BaseModel):
    total: int
    unread_count: int
    items: list[UserNotificationItem]


class CourseNotificationSettingsRequest(BaseModel):
    start_alarm_enabled: bool = True
    start_alarm_minutes_before: int = Field(default=1440, ge=0, le=43200)
    registration_alarm_enabled: bool = True
    registration_alarm_minutes_before: int = Field(default=1440, ge=0, le=43200)


class CourseNotificationSettingsItem(CourseNotificationSettingsRequest):
    course_id: str
    start_event_date: Optional[str] = None
    registration_event_date: Optional[str] = None
    updated_at: Optional[str] = None


class CourseNotificationSettingsResponse(BaseModel):
    total: int
    items: list[CourseNotificationSettingsItem]


def _ensure_course(db: Session, course_id: str) -> models.Course:
    course = db.query(models.Course).filter(models.Course.id == course_id).first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


def _course_url(course: Optional[models.Course], course_id: Optional[str] = None, course_url: Optional[str] = None) -> str:
    supplied = (course_url or "").strip()
    if supplied:
        safe_url = safe_external_http_url(supplied)
        if not safe_url:
            raise HTTPException(status_code=400, detail="course_url must be a valid HTTP(S) URL")
        return safe_url
    if course:
        stored_url = safe_external_http_url(course.raw_url or course.application_url)
        return stored_url or f"course:{course.id}"
    if course_id:
        return f"course:{course_id}"
    raise HTTPException(status_code=400, detail="course_id or course_url is required")


def _course_by_id_or_url(db: Session, course_id: Optional[str], course_url: Optional[str]) -> Optional[models.Course]:
    if course_id:
        return _ensure_course(db, course_id)
    url = (course_url or "").strip()
    if not url:
        return None
    safe_url = safe_external_http_url(url)
    if not safe_url:
        raise HTTPException(status_code=400, detail="course_url must be a valid HTTP(S) URL")
    return (
        db.query(models.Course)
        .filter((models.Course.raw_url == safe_url) | (models.Course.application_url == safe_url))
        .first()
    )


def _favorite_alert_badges(course: Optional[models.Course], today: Optional[date] = None) -> list[str]:
    if not course:
        return []
    today = today or datetime.now(ZoneInfo("Asia/Seoul")).date()
    badges: list[str] = []
    if course.apply_start:
        days_left = (course.apply_start - today).days
        if 0 <= days_left <= 1:
            badges.append("registration_open")
    if course.apply_end:
        days_left = (course.apply_end - today).days
        if 0 <= days_left <= 1:
            badges.append("registration_closing")
    return badges


def _upsert_user_favorite(
    db: Session,
    user: models.User,
    course: Optional[models.Course],
    course_url: Optional[str] = None,
) -> models.UserFavoriteCourse:
    url = _course_url(course, str(course.id) if course else None, course_url)
    stmt = (
        insert(models.UserFavoriteCourse)
        .values(user_id=user.id, course_id=course.id if course else None, course_url=url)
        .on_conflict_do_update(
            index_elements=["user_id", "course_url"],
            set_={"course_id": course.id if course else None},
        )
        .returning(models.UserFavoriteCourse.id)
    )
    favorite_id = db.execute(stmt).scalar()
    return (
        db.query(models.UserFavoriteCourse)
        .options(joinedload(models.UserFavoriteCourse.course).joinedload(models.Course.branch))
        .filter(models.UserFavoriteCourse.id == favorite_id)
        .one()
    )


def _sync_favorite_table_from_marks(db: Session, user: models.User) -> None:
    marks = (
        db.query(models.UserCourseMark)
        .options(joinedload(models.UserCourseMark.course))
        .filter(
            models.UserCourseMark.user_id == user.id,
            models.UserCourseMark.mark_type == "favorite",
        )
        .all()
    )
    for mark in marks:
        if mark.course:
            _upsert_user_favorite(db, user, mark.course)


def _serialize_favorite(row: models.UserFavoriteCourse) -> FavoriteCourseItem:
    course = row.course
    return FavoriteCourseItem(
        id=int(row.id),
        course_id=str(row.course_id) if row.course_id else None,
        course_url=row.course_url,
        created_at=row.created_at.isoformat() if row.created_at else None,
        alert_badges=_favorite_alert_badges(course),
        course=_serialize_course(course) if course else None,
    )


def _serialize_alert(row: models.CourseAlert) -> CourseAlertItem:
    return CourseAlertItem(
        id=int(row.id),
        user_id=str(row.user_id),
        course_id=str(row.course_id) if row.course_id else None,
        course_url=row.course_url,
        alert_type=row.alert_type,
        alert_status=row.alert_status,
        scheduled_at=row.scheduled_at.isoformat() if row.scheduled_at else None,
        sent_at=row.sent_at.isoformat() if row.sent_at else None,
        created_at=row.created_at.isoformat() if row.created_at else None,
        course=_serialize_course(row.course) if row.course else None,
    )


def _format_event_date(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value else None


def _ensure_applied_mark(db: Session, user: models.User, course_id: str) -> None:
    exists = (
        db.query(models.UserCourseMark.id)
        .filter(
            models.UserCourseMark.user_id == user.id,
            models.UserCourseMark.course_id == course_id,
            models.UserCourseMark.mark_type == "applied",
        )
        .first()
    )
    if not exists:
        raise HTTPException(status_code=404, detail="My course mark not found")


def _default_notification_setting(course: models.Course) -> CourseNotificationSettingsItem:
    return CourseNotificationSettingsItem(
        course_id=str(course.id),
        start_alarm_enabled=True,
        start_alarm_minutes_before=1440,
        registration_alarm_enabled=True,
        registration_alarm_minutes_before=1440,
        start_event_date=_format_event_date(course.start_date),
        registration_event_date=_format_event_date(course.apply_start),
        updated_at=None,
    )


def _serialize_notification_setting(
    course: models.Course,
    setting: Optional[models.UserCourseNotificationSetting],
) -> CourseNotificationSettingsItem:
    if not setting:
        return _default_notification_setting(course)
    return CourseNotificationSettingsItem(
        course_id=str(course.id),
        start_alarm_enabled=bool(setting.start_alarm_enabled),
        start_alarm_minutes_before=int(setting.start_alarm_minutes_before or 0),
        registration_alarm_enabled=bool(setting.registration_alarm_enabled),
        registration_alarm_minutes_before=int(setting.registration_alarm_minutes_before or 0),
        start_event_date=_format_event_date(course.start_date),
        registration_event_date=_format_event_date(course.apply_start),
        updated_at=setting.updated_at.isoformat() if setting.updated_at else None,
    )


def _build_notification(
    mark: models.UserCourseMark,
    today: date,
    setting: Optional[models.UserCourseNotificationSetting] = None,
) -> list[UserNotificationItem]:
    course = mark.course
    if not course:
        return []

    serialized = _serialize_course(course)
    course_status = effective_course_status(course, reference_date=today)
    branch_name = course.branch.name if course.branch else "문화센터"
    course_title = serialized.get("title") or course.title
    items: list[UserNotificationItem] = []

    if mark.mark_type == "favorite":
        if course_status in {"OPEN", "DEADLINE", "WAITING"}:
            label = "마감임박" if course_status == "DEADLINE" else "수강신청 가능"
            items.append(
                UserNotificationItem(
                    id=f"favorite-open-{course.id}",
                    course_id=str(course.id),
                    notification_type="OPEN",
                    title=label,
                    message=f"{branch_name} '{course_title}' 강좌를 지금 신청할 수 있습니다.",
                    priority=1 if course_status == "DEADLINE" else 2,
                    event_date=_format_event_date(course.apply_end or course.start_date),
                    mark_type="favorite",
                    course=serialized,
                )
            )
        elif course_status == "SCHEDULED" and course.apply_start:
            days_left = (course.apply_start - today).days
            if 0 <= days_left <= 7:
                day_text = "오늘" if days_left == 0 else f"{days_left}일 후"
                items.append(
                    UserNotificationItem(
                        id=f"favorite-scheduled-{course.id}",
                        course_id=str(course.id),
                        notification_type="START",
                        title="접수 예정",
                        message=f"{branch_name} '{course_title}' 강좌 접수가 {day_text} 시작됩니다.",
                        priority=3,
                        event_date=_format_event_date(course.apply_start),
                        mark_type="favorite",
                        course=serialized,
                    )
                )

        if course.apply_end and course_status in {"OPEN", "DEADLINE", "WAITING"}:
            days_left = (course.apply_end - today).days
            if 0 <= days_left <= 3:
                day_text = "오늘" if days_left == 0 else f"{days_left}일 후"
                items.append(
                    UserNotificationItem(
                        id=f"favorite-deadline-{course.id}",
                        course_id=str(course.id),
                        notification_type="DEADLINE",
                        title="접수 마감 임박",
                        message=f"{branch_name} '{course_title}' 접수가 {day_text} 마감됩니다.",
                        priority=1,
                        event_date=_format_event_date(course.apply_end),
                        mark_type="favorite",
                        course=serialized,
                    )
                )

    if mark.mark_type == "applied" and course.apply_start:
        registration_enabled = True if not setting else bool(setting.registration_alarm_enabled)
        days_left = (course.apply_start - today).days
        if registration_enabled and 0 <= days_left <= 7:
            day_text = "today" if days_left == 0 else f"{days_left} days later"
            items.append(
                UserNotificationItem(
                    id=f"applied-registration-{course.id}",
                    course_id=str(course.id),
                    notification_type="REGISTRATION_START",
                    title="Registration starts soon",
                    message=f"{branch_name} '{course_title}' registration starts {day_text}.",
                    priority=2 if days_left <= 1 else 4,
                    event_date=_format_event_date(course.apply_start),
                    mark_type="applied",
                    course=serialized,
                )
            )

    if mark.mark_type == "applied" and course.start_date:
        start_enabled = True if not setting else bool(setting.start_alarm_enabled)
        days_left = (course.start_date - today).days
        if start_enabled and 0 <= days_left <= 7:
            day_text = "오늘" if days_left == 0 else f"{days_left}일 후"
            items.append(
                UserNotificationItem(
                    id=f"applied-start-{course.id}",
                    course_id=str(course.id),
                    notification_type="COURSE_START",
                    title="내 강좌 알림",
                    message=f"{branch_name} '{course_title}' 수업이 {day_text} 시작됩니다.",
                    priority=2 if days_left <= 1 else 4,
                    event_date=_format_event_date(course.start_date),
                    mark_type="applied",
                    course=serialized,
                )
            )

    return items


@router.get("/course-marks", response_model=CourseMarkIdsResponse)
def get_course_marks(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    _sync_favorite_table_from_marks(db, user)
    db.commit()
    rows = (
        db.query(models.UserCourseMark.course_id, models.UserCourseMark.mark_type)
        .filter(models.UserCourseMark.user_id == user.id)
        .all()
    )
    favorite_ids = []
    applied_ids = []
    for course_id, mark_type in rows:
        if mark_type == "favorite":
            favorite_ids.append(str(course_id))
        elif mark_type == "applied":
            applied_ids.append(str(course_id))
    extra_favorite_ids = (
        db.query(models.UserFavoriteCourse.course_id)
        .filter(
            models.UserFavoriteCourse.user_id == user.id,
            models.UserFavoriteCourse.course_id.isnot(None),
        )
        .all()
    )
    favorite_ids.extend(str(course_id) for (course_id,) in extra_favorite_ids if course_id)
    favorite_ids = list(dict.fromkeys(favorite_ids))
    applied_ids = list(dict.fromkeys(applied_ids))
    return CourseMarkIdsResponse(favorite_course_ids=favorite_ids, applied_course_ids=applied_ids)


@router.put("/course-marks/{course_id}", response_model=CourseMarkIdsResponse)
def add_course_mark(
    course_id: str,
    payload: CourseMarkRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    course = _ensure_course(db, course_id)
    stmt = (
        insert(models.UserCourseMark)
        .values(user_id=user.id, course_id=course_id, mark_type=payload.mark_type)
        .on_conflict_do_nothing(index_elements=["user_id", "course_id", "mark_type"])
    )
    db.execute(stmt)
    if payload.mark_type == "favorite":
        _upsert_user_favorite(db, user, course)
    db.commit()
    return get_course_marks(db=db, user=user)


@router.get("/course-notification-settings", response_model=CourseNotificationSettingsResponse)
def get_course_notification_settings(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    marks = (
        db.query(models.UserCourseMark)
        .options(joinedload(models.UserCourseMark.course).joinedload(models.Course.branch))
        .filter(
            models.UserCourseMark.user_id == user.id,
            models.UserCourseMark.mark_type == "applied",
        )
        .order_by(models.UserCourseMark.created_at.desc())
        .all()
    )
    course_ids = [mark.course_id for mark in marks]
    settings = {}
    if course_ids:
        settings = {
            setting.course_id: setting
            for setting in db.query(models.UserCourseNotificationSetting)
            .filter(
                models.UserCourseNotificationSetting.user_id == user.id,
                models.UserCourseNotificationSetting.course_id.in_(course_ids),
            )
            .all()
        }

    items = [
        _serialize_notification_setting(mark.course, settings.get(mark.course_id))
        for mark in marks
        if mark.course
    ]
    return CourseNotificationSettingsResponse(total=len(items), items=items)


@router.get("/course-notification-settings/{course_id}", response_model=CourseNotificationSettingsItem)
def get_course_notification_setting(
    course_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    course = _ensure_course(db, course_id)
    _ensure_applied_mark(db, user, course_id)
    setting = (
        db.query(models.UserCourseNotificationSetting)
        .filter(
            models.UserCourseNotificationSetting.user_id == user.id,
            models.UserCourseNotificationSetting.course_id == course_id,
        )
        .first()
    )
    return _serialize_notification_setting(course, setting)


@router.put("/course-notification-settings/{course_id}", response_model=CourseNotificationSettingsItem)
def update_course_notification_setting(
    course_id: str,
    payload: CourseNotificationSettingsRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    course = _ensure_course(db, course_id)
    _ensure_applied_mark(db, user, course_id)
    values = {
        "user_id": user.id,
        "course_id": course_id,
        "start_alarm_enabled": payload.start_alarm_enabled,
        "start_alarm_minutes_before": payload.start_alarm_minutes_before,
        "registration_alarm_enabled": payload.registration_alarm_enabled,
        "registration_alarm_minutes_before": payload.registration_alarm_minutes_before,
        "updated_at": datetime.now(ZoneInfo("Asia/Seoul")),
    }
    stmt = (
        insert(models.UserCourseNotificationSetting)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["user_id", "course_id"],
            set_={
                "start_alarm_enabled": payload.start_alarm_enabled,
                "start_alarm_minutes_before": payload.start_alarm_minutes_before,
                "registration_alarm_enabled": payload.registration_alarm_enabled,
                "registration_alarm_minutes_before": payload.registration_alarm_minutes_before,
                "updated_at": values["updated_at"],
            },
        )
    )
    db.execute(stmt)
    db.commit()
    setting = (
        db.query(models.UserCourseNotificationSetting)
        .filter(
            models.UserCourseNotificationSetting.user_id == user.id,
            models.UserCourseNotificationSetting.course_id == course_id,
        )
        .first()
    )
    return _serialize_notification_setting(course, setting)


@router.delete("/course-marks/{course_id}", response_model=CourseMarkIdsResponse)
def remove_course_mark(
    course_id: str,
    mark_type: MarkType = Query(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    deleted = (
        db.query(models.UserCourseMark)
        .filter(
            models.UserCourseMark.user_id == user.id,
            models.UserCourseMark.course_id == course_id,
            models.UserCourseMark.mark_type == mark_type,
        )
        .delete(synchronize_session=False)
    )
    if not deleted:
        _ensure_course(db, course_id)
    elif mark_type == "applied":
        db.query(models.UserCourseNotificationSetting).filter(
            models.UserCourseNotificationSetting.user_id == user.id,
            models.UserCourseNotificationSetting.course_id == course_id,
        ).delete(synchronize_session=False)
    elif mark_type == "favorite":
        db.query(models.UserFavoriteCourse).filter(
            models.UserFavoriteCourse.user_id == user.id,
            models.UserFavoriteCourse.course_id == course_id,
        ).delete(synchronize_session=False)
    db.commit()
    return get_course_marks(db=db, user=user)


@api_router.get("/favorites", response_model=FavoriteCourseListResponse)
def list_favorite_courses(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    _sync_favorite_table_from_marks(db, user)
    db.commit()
    rows = (
        db.query(models.UserFavoriteCourse)
        .options(joinedload(models.UserFavoriteCourse.course).joinedload(models.Course.branch))
        .filter(models.UserFavoriteCourse.user_id == user.id)
        .order_by(models.UserFavoriteCourse.created_at.desc())
        .all()
    )
    return FavoriteCourseListResponse(total=len(rows), items=[_serialize_favorite(row) for row in rows])


@api_router.post("/favorites", response_model=FavoriteCourseItem, status_code=status.HTTP_201_CREATED)
def create_favorite_course(
    payload: FavoriteCourseRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    course = _course_by_id_or_url(db, payload.course_id, payload.course_url)
    if not course and not payload.course_url:
        raise HTTPException(status_code=404, detail="Course not found")
    favorite = _upsert_user_favorite(db, user, course, payload.course_url)
    if course:
        stmt = (
            insert(models.UserCourseMark)
            .values(user_id=user.id, course_id=course.id, mark_type="favorite")
            .on_conflict_do_nothing(index_elements=["user_id", "course_id", "mark_type"])
        )
        db.execute(stmt)
    db.commit()
    db.refresh(favorite)
    return _serialize_favorite(favorite)


@api_router.delete("/favorites/{favorite_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_favorite_course(
    favorite_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    favorite = (
        db.query(models.UserFavoriteCourse)
        .filter(
            models.UserFavoriteCourse.id == favorite_id,
            models.UserFavoriteCourse.user_id == user.id,
        )
        .first()
    )
    if not favorite:
        raise HTTPException(status_code=404, detail="Favorite not found")
    course_id = favorite.course_id
    db.delete(favorite)
    if course_id:
        db.query(models.UserCourseMark).filter(
            models.UserCourseMark.user_id == user.id,
            models.UserCourseMark.course_id == course_id,
            models.UserCourseMark.mark_type == "favorite",
        ).delete(synchronize_session=False)
    db.commit()
    return None


@api_router.get("/alerts", response_model=CourseAlertListResponse)
def list_course_alerts(
    status_filter: Optional[AlertStatus] = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    query = (
        db.query(models.CourseAlert)
        .options(joinedload(models.CourseAlert.course).joinedload(models.Course.branch))
        .filter(models.CourseAlert.user_id == user.id)
    )
    if status_filter:
        query = query.filter(models.CourseAlert.alert_status == status_filter)
    rows = query.order_by(
        models.CourseAlert.scheduled_at.asc().nullslast(),
        models.CourseAlert.created_at.desc(),
    ).all()
    return CourseAlertListResponse(total=len(rows), items=[_serialize_alert(row) for row in rows])


@api_router.post("/alerts", response_model=CourseAlertItem, status_code=status.HTTP_201_CREATED)
def create_course_alert(
    payload: CourseAlertRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    course = _course_by_id_or_url(db, payload.course_id, payload.course_url)
    if not course and not payload.course_url:
        raise HTTPException(status_code=404, detail="Course not found")
    url = _course_url(course, payload.course_id, payload.course_url)
    existing = (
        db.query(models.CourseAlert)
        .options(joinedload(models.CourseAlert.course).joinedload(models.Course.branch))
        .filter(
            models.CourseAlert.user_id == user.id,
            models.CourseAlert.course_url == url,
            models.CourseAlert.alert_type == payload.alert_type,
        )
        .first()
    )
    if existing:
        return _serialize_alert(existing)
    alert = models.CourseAlert(
        user_id=user.id,
        course_id=course.id if course else None,
        course_url=url,
        alert_type=payload.alert_type,
        alert_status="pending",
        scheduled_at=payload.scheduled_at,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return _serialize_alert(alert)


@api_router.delete("/alerts/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_course_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    deleted = (
        db.query(models.CourseAlert)
        .filter(models.CourseAlert.id == alert_id, models.CourseAlert.user_id == user.id)
        .delete(synchronize_session=False)
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Alert not found")
    db.commit()
    return None


@router.get("/courses", response_model=schemas.CourseListResponse)
def get_marked_courses(
    mark_type: MarkType = Query(...),
    page: int = Query(1, ge=1, le=1_000),
    size: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    query = (
        db.query(models.Course)
        .join(models.UserCourseMark, models.UserCourseMark.course_id == models.Course.id)
        .options(joinedload(models.Course.branch))
        .filter(
            models.UserCourseMark.user_id == user.id,
            models.UserCourseMark.mark_type == mark_type,
        )
    )
    total = query.count()
    items = (
        query.order_by(models.UserCourseMark.created_at.desc(), models.Course.updated_at.desc().nullslast())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return {"total": total, "page": page, "size": size, "items": [_serialize_course(item) for item in items]}


@router.get("/notifications", response_model=UserNotificationsResponse)
def get_user_notifications(
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    marks = (
        db.query(models.UserCourseMark)
        .options(joinedload(models.UserCourseMark.course).joinedload(models.Course.branch))
        .filter(models.UserCourseMark.user_id == user.id)
        .order_by(models.UserCourseMark.updated_at.desc().nullslast(), models.UserCourseMark.created_at.desc())
        .all()
    )

    notifications: list[UserNotificationItem] = []
    seen_ids: set[str] = set()
    settings = (
        {
            setting.course_id: setting
            for setting in db.query(models.UserCourseNotificationSetting)
            .filter(
                models.UserCourseNotificationSetting.user_id == user.id,
                models.UserCourseNotificationSetting.course_id.in_([mark.course_id for mark in marks]),
            )
            .all()
        }
        if marks
        else {}
    )
    for mark in marks:
        for item in _build_notification(mark, today, settings.get(mark.course_id)):
            if item.id in seen_ids:
                continue
            seen_ids.add(item.id)
            notifications.append(item)

    notifications.sort(key=lambda item: (item.priority, item.event_date or "9999-12-31", item.title))
    limited = notifications[:limit]
    return UserNotificationsResponse(total=len(notifications), unread_count=len(notifications), items=limited)


@router.post("/course-marks/{course_id}/apply", status_code=status.HTTP_204_NO_CONTENT)
def add_my_course_shortcut(
    course_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    _ensure_course(db, course_id)
    stmt = (
        insert(models.UserCourseMark)
        .values(user_id=user.id, course_id=course_id, mark_type="applied")
        .on_conflict_do_nothing(index_elements=["user_id", "course_id", "mark_type"])
    )
    db.execute(stmt)
    db.commit()
    return None
