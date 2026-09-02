from __future__ import annotations

import html
import json
import os
import re
import unicodedata
import uuid
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from .. import models
from ..database import get_db
from DB.course_lifecycle import effective_course_status
from utils.fee_semantics import fee_status
from utils.seo_quality import is_indexable_category_value
from utils.url_security import safe_external_http_url

try:
    from title_cleaner import clean_course_title
except Exception:
    clean_course_title = None


router = APIRouter(tags=["seo"])

SITE_NAME = "문센"
DEFAULT_SITE_URL = "https://mooncen.kr"
DEFAULT_KEYWORDS = "문센, 문화센터, 문화센터 강좌, 평생학습, 공공강좌, 도서관 강좌, 체험 예약, 아이 강좌, 성인 강좌"
PROVIDER_LABELS = {
    "HOMEPLUS": "홈플러스",
    "LOTTE": "롯데백화점",
    "EMART": "이마트",
    "HYUNDAI_DEPT": "현대백화점",
    "GALLERIA": "갤러리아",
    "AK_PLAZA": "AK PLAZA",
    "ELAND_RETAIL": "이랜드리테일",
    "SHINSEGAE_ACADEMY": "신세계 아카데미",
    "LOTTE_MART": "롯데마트",
}
STATUS_LABELS = {
    "OPEN": "접수중",
    "SCHEDULED": "접수예정",
    "CLOSED": "마감",
    "WAITING": "대기접수",
    "DEADLINE": "마감임박",
}


def site_url() -> str:
    return (os.getenv("VITE_SITE_URL") or os.getenv("SITE_URL") or DEFAULT_SITE_URL).rstrip("/")


def slugify(value: Any, fallback: str = "page") -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = re.sub(r"[^\w가-힣]+", "-", text, flags=re.UNICODE)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:80] or fallback


def absolute_url(path: str) -> str:
    return f"{site_url()}{path if path.startswith('/') else f'/{path}'}"


def text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, Decimal):
        return f"{int(value):,}"
    return str(value).strip() or fallback


def safe_external_url(value: Any) -> str | None:
    return safe_external_http_url(text(value)) or None


def won(value: Any) -> str:
    if value is None:
        return "수강료 확인"
    try:
        amount = int(float(value))
    except Exception:
        return text(value, "수강료 확인")
    return "무료" if amount <= 0 else f"{amount:,}원"


def date_text(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else text(value)


def display_date_text(value: Any) -> str:
    raw = date_text(value)
    if not raw:
        return ""
    return raw.replace("-", ".")


def provider_label(provider: str | None) -> str:
    if not provider:
        return "운영기관"
    return PROVIDER_LABELS.get(provider, provider.replace("_", " ").title())


def provider_short_label(provider: str | None) -> str:
    labels = {
        "HOMEPLUS": "홈플",
        "EMART": "이마트",
        "LOTTE": "롯데",
        "LOTTE_MART": "롯데마트",
        "AK_PLAZA": "AK",
        "HYUNDAI_DEPT": "현대",
        "SHINSEGAE_ACADEMY": "신세계",
        "GALLERIA": "갤러리아",
        "ELAND_RETAIL": "이랜드",
    }
    return labels.get(provider or "", provider_label(provider))


def numeric_amount(value: Any) -> int:
    if value is None:
        return 0
    try:
        return max(0, int(float(value)))
    except Exception:
        return 0


def total_fee_text(course: models.Course) -> str:
    total = numeric_amount(course.fee) + numeric_amount(course.material_fee)
    return "확인 필요" if total <= 0 else f"{total:,}원"


def session_text(course: models.Course) -> str:
    if course.sessions and course.sessions > 0:
        return f"{course.sessions}회"
    if isinstance(course.schedule_dates, list) and len(course.schedule_dates) > 1:
        return f"{len(course.schedule_dates)}회"
    if course.start_date and course.end_date and course.start_date == course.end_date:
        return "1회"
    return "횟수 확인 필요"


def capacity_text(course: models.Course) -> str:
    if course.capacity_remaining is not None:
        return f"{course.capacity_remaining:,}명 남음"
    if course.capacity_total is not None:
        return f"정원 {course.capacity_total:,}명"
    return "제공 정보 없음"


def seo_status_class(status: str | None) -> str:
    value = status or ""
    if value == "DEADLINE":
        return "deadline"
    if value == "CLOSED":
        return "closed"
    if value == "SCHEDULED":
        return "scheduled"
    return "open"


def seo_primary_cta_label(status: str | None) -> str:
    status_class = seo_status_class(status)
    if status_class == "closed":
        return "마감"
    if status_class == "scheduled":
        return "알림 신청"
    return "수강신청"


def display_title(course: models.Course) -> str:
    result = course.ai_title_result if isinstance(course.ai_title_result, dict) else {}
    clean_title = str(result.get("clean_title") or "").strip()
    if course.ai_title_processed and clean_title:
        if clean_course_title:
            cleaned, _removed = clean_course_title(clean_title)
            return cleaned or clean_title
        return clean_title
    if clean_course_title:
        cleaned, _removed = clean_course_title(course.title or "")
        return cleaned or course.title or "강좌명 미정"
    return course.title or "강좌명 미정"


def branch_name(course: models.Course) -> str:
    return course.branch.name if course.branch else course.venue_name or provider_label(course.provider)


def branch_slug(branch: models.Branch) -> str:
    return slugify(" ".join(part for part in (provider_label(branch.provider), branch.name) if part), "branch")


def course_slug(course: models.Course) -> str:
    return slugify(" ".join(part for part in (display_title(course), branch_name(course)) if part), "course")


def category_slug(category: str) -> str:
    return slugify(category, "category")


def course_path(course: models.Course) -> str:
    return f"/course/{course.id}/{quote(course_slug(course), safe='')}"


def branch_path(branch: models.Branch) -> str:
    return f"/branch/{branch.id}/{quote(branch_slug(branch), safe='')}"


def category_path(category: str) -> str:
    return f"/category/{quote(category_slug(category), safe='')}"


def period_text(course: models.Course) -> str:
    start = display_date_text(course.start_date)
    end = display_date_text(course.end_date)
    if start and end and start != end:
        if start[:4] == end[:4]:
            return f"{start} ~ {end[5:]}"
        return f"{start} ~ {end}"
    return start or end or ""


def schedule_text(course: models.Course) -> str:
    parts = [period_text(course), course.schedule_raw]
    return " / ".join(dict.fromkeys(part for part in parts if part))


def class_time_text(course: models.Course) -> str:
    raw = text(course.schedule_raw)
    if raw:
        return raw
    return "일정 확인 필요"


def course_category(course: models.Course) -> str:
    return text(
        course.standard_category_label
        or course.service_group
        or course.collection_category
        or course.domain_category
        or course.ai_category
        or course.category_raw,
        "기타",
    )


def normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(tag).lstrip("#") for tag in value if str(tag).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(tag).lstrip("#") for tag in parsed if str(tag).strip()]
        except Exception:
            pass
        return [part.strip().lstrip("#") for part in value.split(",") if part.strip()]
    return []


def keywords(*parts: Any) -> str:
    values: list[str] = []
    for part in parts:
        if isinstance(part, (list, tuple, set)):
            values.extend(text(item) for item in part if text(item))
        elif text(part):
            values.append(text(part))
    values.append(DEFAULT_KEYWORDS)
    return ", ".join(dict.fromkeys(values))


def course_meta_description(course: models.Course) -> str:
    summary = text(course.ai_summary or course.description)
    base = summary or f"{branch_name(course)}에서 진행하는 {display_title(course)} 강좌입니다."
    details = " ".join(
        part
        for part in (text(course.target or course.eligibility_raw), schedule_text(course), won(course.fee))
        if part
    )
    return f"{base} {details}".strip()[:155]


def active_course_query(db: Session):
    return db.query(models.Course).filter(
        models.Course.is_active.is_(True),
        models.Course.title.isnot(None),
        or_(models.Course.end_date.is_(None), models.Course.end_date >= func.current_date()),
    )


def find_course(db: Session, course_id: str) -> models.Course:
    try:
        parsed_id = uuid.UUID(course_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Course not found") from None
    course = (
        active_course_query(db)
        .options(joinedload(models.Course.branch))
        .filter(models.Course.id == parsed_id)
        .first()
    )
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


def find_branch(db: Session, branch_id: str) -> models.Branch:
    try:
        parsed_id = uuid.UUID(branch_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Branch not found") from None
    branch = db.query(models.Branch).filter(models.Branch.id == parsed_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    return branch


def find_category_by_slug(db: Session, slug: str) -> str:
    category_expr = func.coalesce(
        func.nullif(models.Course.standard_category_label, ""),
        func.nullif(models.Course.service_group, ""),
        func.nullif(models.Course.collection_category, ""),
        func.nullif(models.Course.domain_category, ""),
        func.nullif(models.Course.ai_category, ""),
        func.nullif(models.Course.category_raw, ""),
    )
    rows = (
        active_course_query(db)
        .with_entities(category_expr.label("category"), func.count(models.Course.id).label("course_count"))
        .filter(category_expr.isnot(None))
        .group_by(category_expr)
        .all()
    )
    for row in rows:
        category = text(row.category)
        if is_indexable_category_value(category, row.course_count) and category_slug(category) == slug:
            return category
    raise HTTPException(status_code=404, detail="Category not found")


def json_ld_script_data(payload: dict[str, Any]) -> str:
    """Serialize JSON for an HTML script element without allowing tag breakout."""
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_shell(
    *,
    page_title: str,
    description: str,
    canonical: str,
    page_keywords: str,
    og_type: str,
    json_ld: dict[str, Any],
    body: str,
    image: str | None = None,
    extra_meta: str = "",
) -> str:
    image_url = image or absolute_url("/logo-header.png")
    return f"""<!doctype html>
<html lang="ko">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="theme-color" content="#18b7aa" />
    <meta name="color-scheme" content="light" />
    <meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1" />
    <meta name="googlebot" content="index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1" />
    <meta name="description" content="{html.escape(description)}" />
    <meta name="keywords" content="{html.escape(page_keywords)}" />
    <meta name="author" content="MoonCen" />
    <meta name="publisher" content="MoonCen" />
    <meta name="application-name" content="{SITE_NAME}" />
    <meta name="format-detection" content="telephone=no" />
    <link rel="canonical" href="{html.escape(canonical)}" />
    <meta property="og:type" content="{html.escape(og_type)}" />
    <meta property="og:locale" content="ko_KR" />
    <meta property="og:site_name" content="{SITE_NAME}" />
    <meta property="og:title" content="{html.escape(page_title)}" />
    <meta property="og:description" content="{html.escape(description)}" />
    <meta property="og:url" content="{html.escape(canonical)}" />
    <meta property="og:image" content="{html.escape(image_url)}" />
    <meta property="og:image:alt" content="{html.escape(page_title)}" />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="{html.escape(page_title)}" />
    <meta name="twitter:description" content="{html.escape(description)}" />
    <meta name="twitter:image" content="{html.escape(image_url)}" />
    {extra_meta}
    <script type="application/ld+json">{json_ld_script_data(json_ld)}</script>
    <title>{html.escape(page_title)} | {SITE_NAME}</title>
    <style>
      body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #172026; background: #f6f8f7; }}
      a {{ color: inherit; }}
      main {{ max-width: 980px; margin: 0 auto; padding: 32px 18px 56px; }}
      .breadcrumb {{ color: #607078; font-size: 14px; margin-bottom: 18px; }}
      .panel {{ background: #fff; border: 1px solid #e2e8e5; border-radius: 18px; overflow: hidden; box-shadow: 0 12px 34px rgba(25, 39, 45, .08); }}
      .hero {{ padding: 28px; background: #eaf8f5; }}
      .eyebrow {{ display: inline-flex; padding: 5px 9px; border-radius: 999px; background: #0f766e; color: #fff; font-size: 13px; font-weight: 800; }}
      h1 {{ margin: 14px 0 8px; font-size: clamp(28px, 4vw, 42px); line-height: 1.18; }}
      h2 {{ font-size: 20px; margin: 26px 0 12px; }}
      .summary {{ margin: 0; color: #405058; line-height: 1.6; }}
      .content {{ padding: 24px 28px 30px; }}
      dl {{ display: grid; grid-template-columns: 118px 1fr; gap: 10px 18px; margin: 0 0 20px; }}
      dt {{ color: #65757c; font-weight: 800; }}
      dd {{ margin: 0; color: #172026; }}
      p {{ line-height: 1.7; }}
      .list {{ display: grid; gap: 10px; padding: 0; margin: 14px 0 0; list-style: none; }}
      .item {{ border: 1px solid #e6ece9; border-radius: 12px; background: #fff; }}
      .item a {{ display: grid; gap: 5px; padding: 14px 16px; text-decoration: none; }}
      .item strong {{ font-size: 16px; line-height: 1.35; }}
      .meta {{ color: #607078; font-size: 13px; line-height: 1.45; }}
      .tags {{ display: flex; flex-wrap: wrap; gap: 8px; padding: 0; margin: 12px 0 0; list-style: none; }}
      .tags li {{ padding: 6px 10px; border-radius: 999px; background: #f1f5f3; color: #34444b; font-size: 13px; font-weight: 700; }}
      .actions {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 24px; }}
      .button {{ min-height: 42px; padding: 0 16px; display: inline-flex; align-items: center; border-radius: 999px; background: #0f766e; color: #fff; text-decoration: none; font-weight: 850; }}
      .button.secondary {{ background: #fff; color: #0f766e; border: 1px solid #badbd5; }}
      .button.disabled {{ color: #94a3b8; background: #e2e8f0; pointer-events: none; }}
      .detail-page {{ max-width: 1040px; }}
      .detail-page-panel {{ display: flex; flex-direction: column; min-height: 0; border-radius: 24px; overflow: hidden; }}
      .detail-page-header {{ height: 56px; padding: 0 18px; display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 12px; border-bottom: 1px solid #edf2f2; background: #fff; }}
      .detail-page-header a {{ color: #0f766e; font-weight: 900; text-decoration: none; }}
      .detail-page-header strong {{ color: #111827; font-size: 16px; font-weight: 950; }}
      .detail-page-header-actions {{ display: inline-flex; gap: 8px; }}
      .detail-page-body {{ padding: 18px; display: grid; grid-template-columns: minmax(330px, 42%) minmax(0, 1fr); gap: 18px; background: #f8fafc; }}
      .detail-left, .detail-right {{ min-width: 0; display: grid; align-content: start; gap: 12px; }}
      .detail-image {{ position: relative; aspect-ratio: 16 / 9; max-height: 250px; overflow: hidden; border-radius: 18px; background: #eef7f5; }}
      .detail-image img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
      .detail-image-placeholder {{ width: 100%; height: 100%; display: grid; place-items: center; color: #0f766e; font-size: 32px; font-weight: 950; background: linear-gradient(135deg, #ccfbf1, #fff); }}
      .detail-status {{ position: absolute; top: 12px; left: 12px; height: 30px; padding: 0 12px; display: inline-flex; align-items: center; border-radius: 999px; color: #fff; font-size: 13px; font-weight: 900; background: #0baf9f; }}
      .detail-status.deadline {{ background: #ef4444; }}
      .detail-status.closed {{ background: #64748b; }}
      .detail-status.scheduled {{ background: #f59e0b; }}
      .detail-title-card, .detail-cost-card, .detail-intro-card, .detail-ai-card {{ border: 1px solid #e2ecea; border-radius: 16px; background: #fff; }}
      .detail-title-card {{ padding: 16px; }}
      .detail-provider-line {{ min-width: 0; display: flex; align-items: center; gap: 8px; color: #475569; font-size: 13px; font-weight: 850; }}
      .detail-brand-chip {{ min-width: 42px; padding: 5px 9px; display: inline-flex; align-items: center; justify-content: center; border: 1px solid #99f6e4; border-radius: 999px; color: #0f766e; font-size: 12px; font-weight: 950; background: #ccfbf1; white-space: nowrap; }}
      .detail-title-card h1 {{ margin: 10px 0 0; color: #111827; font-size: clamp(24px, 3vw, 34px); line-height: 1.22; letter-spacing: 0; word-break: keep-all; }}
      .detail-tags {{ display: flex; flex-wrap: wrap; gap: 6px; padding: 0; margin: 10px 0 0; list-style: none; }}
      .detail-tags li {{ min-height: 26px; padding: 4px 9px; display: inline-flex; align-items: center; border: 1px solid #cdece6; border-radius: 999px; color: #0f766e; font-size: 12px; font-weight: 850; background: #f0fdfa; }}
      .detail-summary-card {{ display: grid; grid-template-columns: minmax(220px, 1.55fr) minmax(118px, .95fr) minmax(70px, .62fr) minmax(88px, .8fr); overflow: hidden; border: 1px solid #cdeee9; border-radius: 16px; background: #f2fcfa; }}
      .detail-summary-card > div {{ min-width: 0; padding: 10px; display: grid; grid-template-columns: auto auto minmax(0, 1fr); column-gap: 6px; row-gap: 0; align-items: center; align-content: center; border-right: 1px solid #d7f0ec; }}
      .detail-summary-card > div:nth-child(4n) {{ border-right: 0; }}
      .detail-summary-icon {{ grid-row: auto; width: 26px; height: 26px; display: inline-flex; align-items: center; justify-content: center; border-radius: 999px; color: #0f766e; font-size: 11px; font-weight: 950; background: #ccfbf1; }}
      .detail-summary-card > div > span:not(.detail-summary-icon), .detail-cost-card span {{ color: #64748b; font-size: 12px; font-weight: 850; }}
      .detail-summary-card > div > span:not(.detail-summary-icon) {{ min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
      .detail-summary-card strong {{ grid-column: auto; min-width: 0; color: #111827; font-size: 14px; line-height: 1.35; font-weight: 900; overflow: hidden; overflow-wrap: normal; text-overflow: ellipsis; white-space: nowrap; word-break: keep-all; }}
      .detail-summary-card strong.price {{ color: #0f766e; font-size: 15px; }}
      .detail-compact-list {{ margin: 0; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0; overflow: hidden; border: 1px solid #e2ecea; border-radius: 16px; background: #fff; }}
      .detail-compact-list > div {{ min-width: 0; padding: 10px 11px; border-right: 1px solid #edf2f2; border-bottom: 1px solid #edf2f2; background: #fff; }}
      .detail-compact-list > div:nth-child(4n) {{ border-right: 0; }}
      .detail-compact-list > div:nth-last-child(-n + 4) {{ border-bottom: 0; }}
      .detail-compact-list dt {{ margin: 0 0 4px; color: #64748b; font-size: 11.5px; font-weight: 900; }}
      .detail-compact-list dd {{ margin: 0; color: #111827; font-size: 13px; line-height: 1.35; font-weight: 800; word-break: keep-all; }}
      .detail-cost-card {{ padding: 14px; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 14px; align-items: center; }}
      .detail-cost-card strong {{ color: #0f766e; font-size: 25px; line-height: 1.1; font-weight: 950; }}
      .detail-cost-card dl {{ margin: 0; display: grid; gap: 5px; }}
      .detail-cost-card dl div {{ display: flex; justify-content: flex-end; gap: 8px; color: #475569; font-size: 13px; font-weight: 850; white-space: nowrap; }}
      .detail-cost-card dt, .detail-cost-card dd {{ margin: 0; }}
      .detail-material-note {{ grid-column: 1 / -1; margin: -4px 0 0; color: #64748b; font-size: 11.5px; line-height: 1.45; }}
      .detail-intro-card {{ padding: 14px; }}
      .detail-intro-card h2 {{ margin: 0 0 8px; font-size: 15px; }}
      .detail-intro-card p, .detail-ai-card p {{ margin: 0; color: #475569; font-size: 13px; line-height: 1.55; white-space: pre-wrap; }}
      .detail-intro-summary {{ display: -webkit-box; overflow: hidden; -webkit-box-orient: vertical; -webkit-line-clamp: 3; }}
      .detail-intro-card details {{ margin-top: 8px; }}
      .detail-intro-card summary {{ color: #0f766e; font-size: 12px; font-weight: 900; cursor: pointer; }}
      .detail-ai-card {{ padding: 12px; background: #fffdf7; }}
      .detail-extra-row {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }}
      .detail-extra-row span {{ min-height: 38px; padding: 8px 10px; display: grid; justify-items: start; gap: 3px; border: 1px solid #dbe8e6; border-radius: 12px; color: #334155; background: #fff; }}
      .detail-extra-row strong {{ color: #111827; font-size: 12.5px; font-weight: 950; white-space: nowrap; }}
      .detail-extra-row small {{ max-width: 100%; overflow: hidden; color: #64748b; font-size: 11px; font-weight: 800; text-overflow: ellipsis; white-space: nowrap; }}
      .detail-page-footer {{ padding: 14px 18px; display: grid; grid-template-columns: 1fr 1fr 1.45fr; gap: 10px; border-top: 1px solid #edf2f2; background: #fff; }}
      .detail-page-footer .button {{ min-height: 46px; justify-content: center; font-size: 15px; font-weight: 950; }}
      .detail-page-footer .button.primary {{ background: linear-gradient(135deg, #14b8a6, #0f766e); box-shadow: 0 12px 28px rgba(20, 184, 166, .24); }}
      @media (max-width: 640px) {{ dl {{ grid-template-columns: 1fr; gap: 5px; }} .hero, .content {{ padding: 22px 18px; }} }}
      @media (max-width: 980px) {{ .detail-page-body {{ grid-template-columns: 1fr; }} .detail-compact-list {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} .detail-compact-list > div:nth-child(2n) {{ border-right: 0; }} .detail-compact-list > div:nth-last-child(-n + 4) {{ border-bottom: 1px solid #edf2f2; }} .detail-compact-list > div:nth-last-child(-n + 2) {{ border-bottom: 0; }} }}
      @media (max-width: 640px) {{ html, body, main {{ max-width: 100%; overflow-x: hidden; }} main {{ padding: 0 0 74px; }} .detail-page-panel {{ width: 100%; max-width: 100%; border-radius: 0; }} .detail-page-header {{ grid-template-columns: auto minmax(0, 1fr) auto; padding: 0 12px; }} .detail-page-header-actions a {{ max-width: 72px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }} .detail-page-body {{ padding: 12px; display: flex; flex-direction: column; }} .detail-left, .detail-right {{ display: contents; }} .detail-left, .detail-right, .detail-title-card, .detail-cost-card, .detail-intro-card {{ max-width: 100%; }} .detail-image {{ order: 1; max-height: 180px; }} .detail-title-card {{ order: 2; }} .detail-summary-card {{ order: 3; }} .detail-cost-card {{ order: 4; }} .detail-intro-card {{ order: 5; }} .detail-compact-list {{ order: 6; }} .detail-extra-row {{ order: 7; }} .detail-summary-card, .detail-extra-row {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} .detail-summary-card > div:first-child {{ grid-column: 1 / -1; }} .detail-summary-card > div:nth-child(2) {{ grid-column: 1 / -1; border-right: 0; }} .detail-summary-card > div:nth-child(2n), .detail-compact-list > div:nth-child(2n) {{ border-right: 0; }} .detail-summary-card > div:nth-child(-n + 2) {{ border-bottom: 1px solid #d7f0ec; }} .detail-compact-list > div:nth-last-child(-n + 4) {{ border-bottom: 1px solid #edf2f2; }} .detail-compact-list > div:nth-last-child(-n + 2) {{ border-bottom: 0; }} .detail-cost-card {{ grid-template-columns: 1fr !important; }} .detail-cost-card dl div {{ justify-content: flex-start; gap: 10px; }} .detail-cost-card strong {{ font-size: 23px; }} .detail-page-footer {{ position: fixed; left: 0; right: 0; bottom: 0; width: 100vw; max-width: 100vw; box-sizing: border-box; z-index: 20; padding: 10px 12px; grid-template-columns: .65fr .85fr 1.6fr; box-shadow: 0 -10px 24px rgba(15, 23, 42, .08); }} .detail-page-footer .button {{ min-width: 0; min-height: 44px; padding: 0 6px; overflow: hidden; font-size: 13px; white-space: nowrap; }} }}
    </style>
  </head>
  <body>
    <main>{body}</main>
  </body>
</html>"""


def course_offer_json_ld(course: models.Course, canonical: str) -> dict[str, Any]:
    course_status = effective_course_status(course)
    payload: dict[str, Any] = {
        "@type": "Offer",
        "availability": "https://schema.org/SoldOut" if course_status == "CLOSED" else "https://schema.org/InStock",
        "url": canonical,
    }
    if fee_status(course.fee) != "UNKNOWN":
        payload["price"] = float(course.fee)
        payload["priceCurrency"] = "KRW"
    if course.apply_start:
        payload["validFrom"] = date_text(course.apply_start)
    if course.apply_end:
        payload["availabilityEnds"] = date_text(course.apply_end)
    return payload


def course_place_json_ld(course: models.Course) -> dict[str, Any] | None:
    branch = course.branch
    place_name = branch.name if branch else course.venue_name
    address = branch.address if branch else course.venue_address
    if not place_name and not address:
        return None
    payload: dict[str, Any] = {
        "@type": "Place",
        "name": place_name or branch_name(course),
    }
    if address:
        payload["address"] = address
    return payload


def course_json_ld(course: models.Course, canonical: str) -> dict[str, Any]:
    name = display_title(course)
    description = course_meta_description(course)
    provider = provider_label(course.provider)
    offer = course_offer_json_ld(course, canonical)
    place = course_place_json_ld(course)
    course_node: dict[str, Any] = {
        "@type": "Course",
        "@id": f"{canonical}#course",
        "name": name,
        "description": description,
        "url": canonical,
        "provider": {"@type": "Organization", "name": provider},
        "inLanguage": "ko-KR",
        "offers": offer,
    }
    event_node: dict[str, Any] = {
        "@type": "Event",
        "@id": f"{canonical}#event",
        "name": name,
        "description": description,
        "url": canonical,
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "organizer": {"@type": "Organization", "name": provider},
        "offers": offer,
        "inLanguage": "ko-KR",
    }
    safe_image_url = safe_external_url(course.image_url)
    if safe_image_url:
        event_node["image"] = [safe_image_url]
    if place:
        course_node["location"] = place
        event_node["location"] = place
    if course.start_date:
        course_node["startDate"] = date_text(course.start_date)
        event_node["startDate"] = date_text(course.start_date)
    if course.end_date:
        course_node["endDate"] = date_text(course.end_date)
        event_node["endDate"] = date_text(course.end_date)
    if course.instructor:
        event_node["performer"] = {"@type": "Person", "name": course.instructor}
    return {"@context": "https://schema.org", "@graph": [course_node, event_node]}


def course_list_items(courses: list[models.Course]) -> str:
    items = []
    for course in courses:
        meta = " · ".join(
            part
            for part in (branch_name(course), schedule_text(course), text(course.target or course.eligibility_raw), won(course.fee))
            if part
        )
        items.append(
            f'<li class="item"><a href="{html.escape(course_path(course))}">'
            f"<strong>{html.escape(display_title(course))}</strong>"
            f'<span class="meta">{html.escape(meta)}</span></a></li>'
        )
    return "\n".join(items)


def collection_json_ld(page_type: str, name: str, description: str, canonical: str, courses: list[models.Course]) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": page_type,
        "name": name,
        "description": description,
        "url": canonical,
        "inLanguage": "ko-KR",
        "mainEntity": {
            "@type": "ItemList",
            "itemListElement": [
                {"@type": "ListItem", "position": index + 1, "url": absolute_url(course_path(course)), "name": display_title(course)}
                for index, course in enumerate(courses)
            ],
        },
    }


def course_html_page(course: models.Course, canonical: str) -> str:
    category = course_category(course)
    raw_title = display_title(course)
    title = raw_title if raw_title and raw_title != "강좌명 미정" else f"{category} 강좌"
    center = branch_name(course)
    description = course_meta_description(course)
    course_status = effective_course_status(course)
    status = STATUS_LABELS.get(course_status or "", course_status or "상태 미정")
    tags = normalize_tags(course.ai_tags) + normalize_tags(course.target_tags)
    source_url = safe_external_url(course.application_url or course.raw_url)
    image_url = safe_external_url(course.image_url)
    body_description = text(course.ai_summary or course.description, "강좌 소개가 아직 등록되지 않았습니다.")
    schedule = class_time_text(course)
    target = text(course.target or course.eligibility_raw, "대상 확인 필요")
    instructor = text(course.instructor, "강사 미정")
    branch_link = branch_path(course.branch) if course.branch else "/"
    provider = provider_label(course.provider)
    short_provider = provider_short_label(course.provider)
    period = period_text(course) or "기간 확인 필요"
    rounds = session_text(course)
    capacity = capacity_text(course)
    status_class = seo_status_class(course_status)
    primary_cta_label = seo_primary_cta_label(course_status)
    source_cta = (
        f'<span class="button primary disabled" aria-disabled="true">{html.escape(primary_cta_label)}</span>'
        if status_class == "closed"
        else (
            f'<a class="button primary" href="{html.escape("/?course=" + str(course.id))}">{html.escape(primary_cta_label)}</a>'
            if status_class == "scheduled"
            else (
                f'<a class="button primary" href="{html.escape(source_url)}" rel="nofollow noopener noreferrer">{html.escape(primary_cta_label)}</a>'
                if source_url
                else f'<a class="button primary" href="{html.escape("/?course=" + str(course.id))}">앱에서 보기</a>'
            )
        )
    )
    material_fee = won(course.material_fee) if numeric_amount(course.material_fee) else "없음"
    material_note_html = (
        '<p class="detail-material-note">※ 재료비는 센터 사정에 따라 변경될 수 있습니다.</p>'
        if numeric_amount(course.material_fee)
        else ""
    )
    image_html = (
        f'<img src="{html.escape(image_url)}" alt="{html.escape(title)} 이미지" loading="lazy" referrerpolicy="no-referrer" />'
        if image_url
        else '<div class="detail-image-placeholder">mooncen</div>'
    )
    app_link = html.escape("/?course=" + str(course.id))
    detail_tag_html = "".join(
        f"<li>#{html.escape(tag)}</li>"
        for tag in dict.fromkeys([category, target, *(tags[:6])])
        if tag
    )
    extra_meta = "\n".join(
        [
            f'<meta property="article:section" content="{html.escape(category)}" />',
            *(f'<meta property="article:tag" content="{html.escape(tag)}" />' for tag in dict.fromkeys(tags[:8])),
        ]
    )
    body = f"""
      <nav class="breadcrumb"><a href="/">문센</a> / <a href="{html.escape(category_path(category))}">{html.escape(category)}</a> / <a href="{html.escape(branch_link)}">{html.escape(center)}</a></nav>
      <article class="panel detail-page-panel">
        <header class="detail-page-header">
          <a href="/">← 문센</a>
          <strong>강좌 상세</strong>
          <div class="detail-page-header-actions">
            <a href="{app_link}">앱에서 보기</a>
          </div>
        </header>
        <section class="detail-page-body">
          <div class="detail-left">
            <div class="detail-image">
              {image_html}
              <span class="detail-status {html.escape(status_class)}">{html.escape(status)}</span>
            </div>
            <section class="detail-cost-card" aria-label="비용 정보">
              <div><span>총 예상 비용</span><strong>{html.escape(total_fee_text(course))}</strong></div>
              <dl>
                <div><dt>수강료</dt><dd>{html.escape(won(course.fee))}</dd></div>
                <div><dt>재료비</dt><dd>{html.escape(material_fee)}</dd></div>
              </dl>
              {material_note_html}
            </section>
            <section class="detail-intro-card">
              <h2>강좌 소개</h2>
              <p class="detail-intro-summary">{html.escape(body_description)}</p>
              <details><summary>자세히 보기</summary><p>{html.escape(body_description)}</p></details>
            </section>
          </div>
          <div class="detail-right">
            <section class="detail-title-card">
              <div class="detail-provider-line"><span class="detail-brand-chip">{html.escape(short_provider)}</span><span>{html.escape(provider)} {html.escape(center)}</span></div>
              <h1>{html.escape(title)}</h1>
              {f'<ul class="detail-tags">{detail_tag_html}</ul>' if detail_tag_html else ''}
            </section>
            <section class="detail-summary-card">
              <div><span class="detail-summary-icon">시</span><span>수업일시</span><strong>{html.escape(schedule)}</strong></div>
              <div><span class="detail-summary-icon">기</span><span>기간</span><strong>{html.escape(period)}</strong></div>
              <div><span class="detail-summary-icon">회</span><span>횟수</span><strong>{html.escape(rounds)}</strong></div>
              <div><span class="detail-summary-icon">원</span><span>수강료</span><strong class="price">{html.escape(won(course.fee))}</strong></div>
            </section>
            <dl class="detail-compact-list">
              <div><dt>대상</dt><dd>{html.escape(target)}</dd></div>
              <div><dt>강사</dt><dd>{html.escape(instructor)}</dd></div>
              <div><dt>정원</dt><dd>{html.escape(capacity)}</dd></div>
              <div><dt>카테고리</dt><dd>{html.escape(category)}</dd></div>
              <div><dt>준비물</dt><dd>수강신청 페이지 확인</dd></div>
              <div><dt>재료비</dt><dd>{html.escape(material_fee)}</dd></div>
              <div><dt>모집상태</dt><dd>{html.escape(status)}</dd></div>
              <div><dt>위치</dt><dd>{html.escape(center)}</dd></div>
            </dl>
            <div class="detail-extra-row">
              <span><strong>환불 안내</strong><small>원문 기준 확인</small></span>
              <span><strong>유의사항</strong><small>접수 조건 확인</small></span>
              <span><strong>주차 안내</strong><small>지점별 상이</small></span>
              <span><strong>문의</strong><small>{html.escape(center)}</small></span>
            </div>
          </div>
        </section>
        <footer class="detail-page-footer">
          <a class="button secondary" href="{app_link}">찜하기</a>
          <a class="button secondary" href="{app_link}">내 강좌 등록</a>
          {source_cta}
        </footer>
      </article>
    """
    return render_shell(
        page_title=f"{title} | {center}",
        description=description,
        canonical=canonical,
        page_keywords=keywords(title, center, provider_label(course.provider), category, target, tags),
        og_type="article",
        json_ld=course_json_ld(course, canonical),
        body=body,
        image=image_url,
        extra_meta=extra_meta,
    )


@router.get("/course/{course_id}", response_class=HTMLResponse)
def redirect_course_seo_page(course_id: str, db: Session = Depends(get_db)):
    return RedirectResponse(course_path(find_course(db, course_id)), status_code=301)


@router.get("/course/{course_id}/{slug:path}", response_class=HTMLResponse)
def course_seo_page(course_id: str, slug: str = "", db: Session = Depends(get_db)):
    course = find_course(db, course_id)
    canonical_path = course_path(course)
    if slug != course_slug(course):
        return RedirectResponse(canonical_path, status_code=301)
    return HTMLResponse(course_html_page(course, absolute_url(canonical_path)))


@router.get("/category/{slug}", response_class=HTMLResponse)
def category_seo_page(slug: str, db: Session = Depends(get_db)):
    category = find_category_by_slug(db, slug)
    query = active_course_query(db).options(joinedload(models.Course.branch)).filter(
        or_(
            models.Course.standard_category_label == category,
            models.Course.service_group == category,
            models.Course.collection_category == category,
            models.Course.domain_category == category,
            models.Course.ai_category == category,
            models.Course.category_raw == category,
        )
    )
    count = query.count()
    courses = (
        query.order_by(models.Course.start_date.asc().nullslast(), func.coalesce(models.Course.updated_at, models.Course.last_seen_at, models.Course.first_seen_at).desc())
        .limit(40)
        .all()
    )
    canonical_path = category_path(category)
    canonical = absolute_url(canonical_path)
    title = f"{category} 강좌"
    description = f"{category} 분야의 모집중인 강좌 {count:,}건을 지점, 일정, 대상, 수강료와 함께 확인할 수 있습니다."
    body = f"""
      <nav class="breadcrumb"><a href="/">문센</a> / 카테고리</nav>
      <section class="panel">
        <div class="hero"><span class="eyebrow">카테고리</span><h1>{html.escape(title)}</h1><p class="summary">{html.escape(description)}</p></div>
        <div class="content"><h2>대표 강좌</h2><ul class="list">{course_list_items(courses)}</ul><div class="actions"><a class="button" href="{html.escape('/?category=' + quote(category, safe=''))}">앱에서 전체 보기</a></div></div>
      </section>
    """
    return HTMLResponse(
        render_shell(
            page_title=title,
            description=description,
            canonical=canonical,
            page_keywords=keywords(category, f"{category} 강좌", f"{category} 문화센터", f"{category} 평생학습"),
            og_type="website",
            json_ld=collection_json_ld("CollectionPage", title, description, canonical, courses),
            body=body,
        )
    )


@router.get("/branch/{branch_id}", response_class=HTMLResponse)
def redirect_branch_seo_page(branch_id: str, db: Session = Depends(get_db)):
    return RedirectResponse(branch_path(find_branch(db, branch_id)), status_code=301)


@router.get("/branch/{branch_id}/{slug:path}", response_class=HTMLResponse)
def branch_seo_page(branch_id: str, slug: str = "", db: Session = Depends(get_db)):
    branch = find_branch(db, branch_id)
    canonical_path = branch_path(branch)
    if slug != branch_slug(branch):
        return RedirectResponse(canonical_path, status_code=301)
    query = active_course_query(db).options(joinedload(models.Course.branch)).filter(models.Course.branch_id == branch.id)
    count = query.count()
    courses = (
        query.order_by(models.Course.start_date.asc().nullslast(), func.coalesce(models.Course.updated_at, models.Course.last_seen_at, models.Course.first_seen_at).desc())
        .limit(40)
        .all()
    )
    categories = sorted({course_category(course) for course in courses if course_category(course)})
    category_tags = "".join(f"<li>{html.escape(category)}</li>" for category in categories[:12])
    canonical = absolute_url(canonical_path)
    title = f"{branch.name} 강좌"
    provider = provider_label(branch.provider)
    description = f"{provider} {branch.name}에서 진행하는 모집중인 강좌 {count:,}건을 일정, 대상, 수강료와 함께 확인할 수 있습니다."
    body = f"""
      <nav class="breadcrumb"><a href="/">문센</a> / 지점</nav>
      <section class="panel">
        <div class="hero"><span class="eyebrow">{html.escape(provider)}</span><h1>{html.escape(title)}</h1><p class="summary">{html.escape(description)}</p></div>
        <div class="content">
          <dl>
            <dt>운영기관</dt><dd>{html.escape(provider)}</dd>
            <dt>지점</dt><dd>{html.escape(branch.name)}</dd>
            <dt>주소</dt><dd>{html.escape(text(branch.address, "주소 확인 필요"))}</dd>
            <dt>전화</dt><dd>{html.escape(text(branch.phone, "전화번호 확인 필요"))}</dd>
            <dt>강좌 수</dt><dd>{count:,}건</dd>
          </dl>
          {f'<h2>분야</h2><ul class="tags">{category_tags}</ul>' if category_tags else ''}
          <h2>대표 강좌</h2><ul class="list">{course_list_items(courses)}</ul>
          <div class="actions"><a class="button" href="{html.escape('/?branch=' + str(branch.id))}">앱에서 전체 보기</a></div>
        </div>
      </section>
    """
    json_ld = collection_json_ld("CollectionPage", title, description, canonical, courses)
    json_ld["about"] = {"@type": "Place", "name": branch.name, "address": branch.address, "telephone": branch.phone}
    return HTMLResponse(
        render_shell(
            page_title=title,
            description=description,
            canonical=canonical,
            page_keywords=keywords(branch.name, provider, f"{branch.name} 강좌", f"{branch.name} 문화센터", categories),
            og_type="website",
            json_ld=json_ld,
            body=body,
        )
    )
