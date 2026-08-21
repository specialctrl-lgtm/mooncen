import React from 'react';
import { Calendar, Heart, User } from 'lucide-react';
import './CourseListItem.css';

const CourseListItem = ({ course }) => {
    const tags = Array.isArray(course.ai_tags) ? course.ai_tags : [];
    const schedule = course.day_schedule || course.schedule_summary || course.schedule_raw || '일정 미정';

    return (
        <div className="course-list-item">
            <div className="item-thumbnail">
                <div className="thumb-placeholder">
                    {course.ai_category ? course.ai_category[0] : 'C'}
                </div>
                {course.status === 'DEADLINE' && <span className="badge-deadline">마감임박</span>}
            </div>

            <div className="item-content">
                <div className="item-header">
                    <h3 className="item-title">{course.title}</h3>
                    <button className="like-button">
                        <Heart size={18} color="#ccc" />
                    </button>
                </div>

                <div className="item-info">
                    <span>{course.provider} · {course.instructor || '강사 미정'}</span>
                    <span><Calendar size={14} /> {schedule}</span>
                    <span><User size={14} /> {course.target_age_group || '대상 미정'}</span>
                </div>

                <div className="item-price">
                    {course.fee ? `${parseInt(course.fee, 10).toLocaleString()}원` : '무료'}
                </div>

                <div className="item-tags">
                    {tags.map((tag, index) => (
                        <span key={`${tag}-${index}`} className="mini-tag">#{tag}</span>
                    ))}
                </div>
            </div>
        </div>
    );
};

export default CourseListItem;
