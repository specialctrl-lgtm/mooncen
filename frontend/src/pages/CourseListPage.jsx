import React, { useEffect, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import CourseListItem from '../components/CourseListItem';
import { searchCourses } from '../api';
import './CourseListPage.css';

const CourseListPage = () => {
    const navigate = useNavigate();
    const [courses, setCourses] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetchCourses = async () => {
            try {
                setLoading(true);
                const response = await searchCourses({ size: 100 });
                setCourses(response.items || []);
            } catch (error) {
                console.error(error);
            } finally {
                setLoading(false);
            }
        };

        fetchCourses();
    }, []);

    return (
        <div className="course-list-page">
            <header className="list-header glass-panel">
                <div className="active-filters">
                    <span className="filter-text">전체 강좌 목록</span>
                </div>
                <div className="sort-bar">
                    <button className="sort-dropdown">
                        최신순 <ChevronDown size={14} />
                    </button>
                </div>
            </header>

            <div className="list-content">
                {loading && <div style={{ padding: '24px', textAlign: 'center' }}>로딩 중입니다.</div>}
                {!loading && courses.map((course, index) => (
                    <React.Fragment key={course.id}>
                        <div onClick={() => navigate(`/courses/${course.id}`)}>
                            <CourseListItem course={course} />
                        </div>
                        {(index + 1) % 5 === 0 && (
                            <div className="ad-banner">
                                <span>안내</span>
                                <p>관심 강좌는 찜해두고 나중에 다시 볼 수 있어요.</p>
                            </div>
                        )}
                    </React.Fragment>
                ))}
            </div>
        </div>
    );
};

export default CourseListPage;
