import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Heart, Share2 } from 'lucide-react';
import { getCourseDetail } from '../api';
import MapComponent from '../components/Map';
import { readFavoriteIds, writeFavoriteIds } from '../utils/favoritesStorage';
import { safeExternalUrl } from '../utils/safeUrl';
import './CourseDetailPage.css';

const CourseDetailPage = () => {
    const { id } = useParams();
    const navigate = useNavigate();
    const [course, setCourse] = useState(null);
    const [loading, setLoading] = useState(true);
    const [activeTab, setActiveTab] = useState('intro');
    const [isFav, setIsFav] = useState(false);

    useEffect(() => {
        const stored = readFavoriteIds();
        setIsFav(stored.includes(id));
    }, [id]);

    useEffect(() => {
        const loadData = async () => {
            try {
                setLoading(true);
                const data = await getCourseDetail(id);
                setCourse(data);
            } catch (error) {
                console.error('Fetch failed', error);
            } finally {
                setLoading(false);
            }
        };
        loadData();
    }, [id]);

    const tags = useMemo(() => (Array.isArray(course?.ai_tags) ? course.ai_tags : []), [course]);

    const toggleFavorite = () => {
        const stored = readFavoriteIds();
        const updated = stored.includes(id) ? stored.filter((value) => value !== id) : [...stored, id];
        const saved = writeFavoriteIds(updated);
        setIsFav(saved.includes(id));
        window.dispatchEvent(new Event('favorites-updated'));
    };

    const handleApplyClick = () => {
        const applyUrl = safeExternalUrl(course?.application_url);
        if (applyUrl) {
            window.open(applyUrl, '_blank', 'noopener,noreferrer');
            return;
        }
        alert('신청 페이지 정보가 없습니다.');
    };

    if (loading) return <div className="loading-state">불러오는 중입니다.</div>;
    if (!course) return <div className="error-state">강좌를 찾을 수 없습니다.</div>;

    return (
        <div className="detail-page-v2">
            <header className="detail-header-sticky glass-panel">
                <button className="icon-btn" onClick={() => navigate(-1)}><ArrowLeft /></button>
                <span className="header-title">{course.title}</span>
                <div style={{ display: 'flex', gap: '0.25rem' }}>
                    <button className="icon-btn" onClick={toggleFavorite}>
                        <Heart size={22} fill={isFav ? 'var(--accent-primary)' : 'none'} stroke={isFav ? 'var(--accent-primary)' : 'currentColor'} />
                    </button>
                    <button className="icon-btn share-btn"><Share2 /></button>
                </div>
            </header>

            <div className="detail-scroll-content">
                <section className="detail-hero">
                    <div className="status-badge-row">
                        <span className="badge-status">{course.status_label || course.status || '상태 미정'}</span>
                    </div>
                    <h1 className="hero-title">{course.title}</h1>

                    <div className="price-info">
                        <span className="total-price">{course.fee ? parseInt(course.fee, 10).toLocaleString() : 0}원</span>
                        {course.material_fee ? <span className="per-price">재료비 {course.material_fee.toLocaleString()}원 별도</span> : null}
                    </div>

                    <div className="schedule-info-box">
                        <div className="schedule-label">일정</div>
                        <div className="schedule-value">
                            {course.start_date || '시작일 미정'} ~ {course.end_date || '종료일 미정'}<br />
                            {course.schedule_summary || course.schedule_raw || '상세 일정 문의'}
                        </div>
                    </div>
                </section>

                <div className="divider-thick" />

                <div className="detail-tabs">
                    {['intro', 'curriculum', 'instructor', 'notice'].map((tab) => (
                        <button
                            key={tab}
                            className={`tab-item ${activeTab === tab ? 'active' : ''}`}
                            onClick={() => setActiveTab(tab)}
                        >
                            {tab === 'intro' && '강좌 소개'}
                            {tab === 'curriculum' && '커리큘럼'}
                            {tab === 'instructor' && '강사 정보'}
                            {tab === 'notice' && '안내'}
                        </button>
                    ))}
                </div>

                <section className="tab-content">
                    {activeTab === 'intro' && (
                        <div className="intro-content">
                            <h3>AI 요약</h3>
                            <p className="ai-summary-box">{course.ai_summary || '아직 AI 요약이 없습니다.'}</p>
                            <div className="detail-tags">
                                {tags.map((tag) => <span key={tag}>#{tag}</span>)}
                            </div>
                            <p className="desc-text">{course.description || '강좌 소개 문구가 아직 등록되지 않았습니다.'}</p>
                        </div>
                    )}
                    {activeTab === 'curriculum' && <div className="placeholder-content">{course.schedule_raw || '커리큘럼 정보가 없습니다.'}</div>}
                    {activeTab === 'instructor' && <div className="placeholder-content">강사: {course.instructor || '미정'}</div>}
                    {activeTab === 'notice' && <div className="placeholder-content">대상: {course.target || course.target_age_group || '미정'}</div>}
                </section>

                <div className="divider-thick" />
                <section className="location-section">
                    <h3>위치 정보</h3>
                    <p className="location-name">{course.branch?.name || course.provider}</p>
                    <div className="map-wrapper">
                        {course.branch && <MapComponent lat={course.branch.lat} lon={course.branch.lon} popupText={course.branch.name} />}
                    </div>
                </section>
            </div>

            <div className="bottom-cta-bar glass-panel">
                <button className="bell-btn" onClick={toggleFavorite}>
                    <Heart size={20} fill={isFav ? 'var(--accent-primary)' : 'none'} stroke={isFav ? 'var(--accent-primary)' : 'currentColor'} />
                    <span>{isFav ? '찜 완료' : '찜하기'}</span>
                </button>
                <button className="apply-btn" onClick={handleApplyClick} disabled={!course?.application_url}>
                    {course?.application_url ? '수강 신청하러 가기' : '신청 링크 준비 중'}
                </button>
            </div>
        </div>
    );
};

export default CourseDetailPage;
