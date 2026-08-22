from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .. import models
from ..services import bug_report_email
from .auth import get_current_user, rate_limit


router = APIRouter(prefix="/bug-reports", tags=["bug-reports"])
logger = logging.getLogger(__name__)


class BugReportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=120)
    content: str = Field(min_length=10, max_length=5_000)
    page_url: str | None = Field(default=None, max_length=2_048)
    user_agent: str | None = Field(default=None, max_length=512)
    viewport: str | None = Field(default=None, max_length=64)
    image_filename: str | None = Field(default=None, max_length=255)
    image_media_type: Literal["image/jpeg", "image/png", "image/webp"] | None = None
    image_base64: str | None = Field(
        default=None,
        max_length=bug_report_email.MAX_IMAGE_BASE64_CHARS,
    )

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        if any(ord(char) < 32 and char not in "\r\n\t" for char in value) or "\x7f" in value:
            raise ValueError("title contains invalid control characters")
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("title must contain at least 2 characters")
        return normalized

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        if any(ord(char) < 32 and char not in "\r\n\t" for char in value) or "\x7f" in value:
            raise ValueError("content contains invalid control characters")
        normalized = value.strip()
        if len(normalized) < 10:
            raise ValueError("content must contain at least 10 characters")
        return normalized

    @field_validator("page_url", "user_agent", "viewport")
    @classmethod
    def normalize_optional_metadata(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(ord(char) < 32 and char not in "\r\n\t" for char in value) or "\x7f" in value:
            raise ValueError("metadata contains invalid control characters")
        normalized = value.strip()
        return normalized or None

    @field_validator("image_filename")
    @classmethod
    def validate_image_filename(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or any(char in normalized for char in "\r\n\0"):
            raise ValueError("image_filename is invalid")
        return normalized

    @field_validator("image_base64")
    @classmethod
    def validate_image_base64_presence(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("image_base64 must not be empty")
        return value

    @model_validator(mode="after")
    def validate_complete_image(self):
        image_fields = (self.image_filename, self.image_media_type, self.image_base64)
        if any(value is not None for value in image_fields) and not all(value is not None for value in image_fields):
            raise ValueError("image_filename, image_media_type, and image_base64 must be provided together")
        return self


class BugReportResponse(BaseModel):
    message: str


@router.post(
    "",
    response_model=BugReportResponse,
    dependencies=[Depends(rate_limit("bug-report", 3, 3_600))],
)
def create_bug_report(
    payload: BugReportPayload,
    request: Request,
    user: models.User = Depends(get_current_user),
) -> BugReportResponse:
    request_id = str(getattr(request.state, "request_id", "") or "unknown")
    image = None
    if payload.image_base64 is not None:
        try:
            image = bug_report_email.validate_bug_report_image(
                filename=payload.image_filename or "",
                media_type=payload.image_media_type or "",
                data_base64=payload.image_base64,
            )
        except bug_report_email.BugReportImageError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid bug report image",
            ) from None

    try:
        bug_report_email.send_bug_report_email(
            title=payload.title,
            content=payload.content,
            reporter_id=str(user.id),
            reporter_email=str(user.email),
            request_id=request_id,
            page_url=payload.page_url,
            user_agent=payload.user_agent,
            viewport=payload.viewport,
            image=image,
        )
    except bug_report_email.BugReportConfigurationError:
        logger.error("Bug report email is not configured request_id=%s", request_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bug report service unavailable",
        ) from None
    except bug_report_email.BugReportDeliveryError:
        logger.warning("Bug report email delivery failed request_id=%s", request_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bug report service unavailable",
        ) from None

    logger.info("Bug report delivered request_id=%s user_id=%s", request_id, user.id)
    return BugReportResponse(message="버그 제보가 전송되었습니다.")
