# My Course Cancel Notes

## 2026-06-02

- The course detail modal no longer disables the my-course button after registration.
- When a course is already registered as `applied`, the detail action shows `내강좌 등록 취소` and removes the `applied` mark.
- Result cards receive `isApplied` and switch their apply button to `등록 취소` for already registered courses.
- Cancelling uses the existing `DELETE /api/users/me/course-marks/{course_id}?mark_type=applied` API.
- Cancelling refreshes course marks and notifications so the my-course count and alarm state stay in sync.
