import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Heart, Trash2, Search } from 'lucide-react';
import { getCourseDetail } from '../api';
import { readFavoriteIds, writeFavoriteIds } from '../utils/favoritesStorage';
import './FavoritesPage.css';

const FavoritesPage = () => {
    const navigate = useNavigate();
    const [favorites, setFavorites] = useState([]);
    const [loading, setLoading] = useState(true);

    const loadFavorites = useCallback(async () => {
        setLoading(true);
        try {
            const stored = readFavoriteIds();
            // Fetch latest data for each favorited course
            const courses = await Promise.allSettled(
                stored.map(id => getCourseDetail(id))
            );
            const validCourses = courses
                .filter(r => r.status === 'fulfilled' && r.value)
                .map(r => r.value);
            setFavorites(validCourses);
        } catch (err) {
            console.error('Failed to load favorites:', err);
            setFavorites([]);
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        loadFavorites();
    }, [loadFavorites]);

    const removeFavorite = (e, courseId) => {
        e.stopPropagation();
        const stored = readFavoriteIds();
        const updated = stored.filter(id => id !== courseId);
        writeFavoriteIds(updated);
        setFavorites(prev => prev.filter(c => c.id !== courseId));
        // Dispatch event so other components can listen
        window.dispatchEvent(new Event('favorites-updated'));
    };

    const clearAll = () => {
        writeFavoriteIds([]);
        setFavorites([]);
        window.dispatchEvent(new Event('favorites-updated'));
    };

    if (loading) {
        return (
            <div className="favorites-page">
                <div className="fav-header">
                    <h1>찜목록</h1>
                </div>
                <div className="fav-loading">
                    <div className="loading-spinner"></div>
                    <p>불러오는 중...</p>
                </div>
            </div>
        );
    }

    return (
        <div className="favorites-page">
            <div className="fav-header">
                <div className="fav-title-row">
                    <h1>
                        <Heart size={28} fill="var(--accent-primary)" stroke="var(--accent-primary)" />
                        찜목록
                    </h1>
                    <span className="fav-count">{favorites.length}개</span>
                </div>
                {favorites.length > 0 && (
                    <button className="clear-all-btn" onClick={clearAll}>
                        <Trash2 size={14} />
                        전체 삭제
                    </button>
                )}
            </div>

            {favorites.length === 0 ? (
                <div className="fav-empty">
                    <div className="empty-icon">
                        <Heart size={64} strokeWidth={1} />
                    </div>
                    <h2>찜한 강좌가 없어요</h2>
                    <p>관심 있는 강좌를 하트를 눌러 저장해보세요</p>
                    <button className="browse-btn" onClick={() => navigate('/')}>
                        <Search size={18} />
                        강좌 찾아보기
                    </button>
                </div>
            ) : (
                <div className="fav-list">
                    {favorites.map(course => (
                        <div
                            key={course.id}
                            className="fav-card glass-panel"
                            onClick={() => navigate(`/courses/${course.id}`)}
                        >
                            <div className="fav-card-content">
                                <div className="fav-card-badges">
                                    <span className="fav-category">{course.ai_category || 'General'}</span>
                                    <span className="fav-provider">{course.provider}</span>
                                </div>
                                <h3 className="fav-card-title">{course.title}</h3>
                                <div className="fav-card-meta">
                                    {course.instructor && <span>{course.instructor}</span>}
                                    {course.schedule_raw && <span>{course.schedule_raw}</span>}
                                </div>
                                <div className="fav-card-bottom">
                                    <span className="fav-price">
                                        {course.fee ? `${parseInt(course.fee).toLocaleString()}원` : '무료'}
                                    </span>
                                    {course.branch && (
                                        <span className="fav-branch">{course.branch.name}</span>
                                    )}
                                </div>
                            </div>
                            <button
                                className="fav-remove-btn"
                                onClick={(e) => removeFavorite(e, course.id)}
                                title="찜 해제"
                            >
                                <Heart size={22} fill="var(--accent-primary)" stroke="var(--accent-primary)" />
                            </button>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};

export default FavoritesPage;
