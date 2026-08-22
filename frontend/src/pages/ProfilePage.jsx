import React, { useCallback, useEffect, useState } from 'react';
import { User, Heart, BookOpen, MapPin, ChevronRight, Bell, Globe, Info, Shield } from 'lucide-react';
import { readFavoriteIds } from '../utils/favoritesStorage';
import './ProfilePage.css';

const ProfilePage = () => {
    const [favCount, setFavCount] = useState(0);
    const [nickname, setNickname] = useState('');
    const [editingNickname, setEditingNickname] = useState(false);
    const [region, setRegion] = useState('');
    const [notifications, setNotifications] = useState(false);

    const updateFavCount = useCallback(() => {
        const stored = readFavoriteIds();
        setFavCount(stored.length);
    }, []);

    useEffect(() => {
        // Load settings from localStorage
        setNickname(localStorage.getItem('user_nickname') || '');
        setRegion(localStorage.getItem('user_region') || '');
        setNotifications(localStorage.getItem('user_notifications') === 'true');
        updateFavCount();

        const handleFavUpdate = () => updateFavCount();
        window.addEventListener('favorites-updated', handleFavUpdate);
        return () => window.removeEventListener('favorites-updated', handleFavUpdate);
    }, [updateFavCount]);

    const saveNickname = () => {
        localStorage.setItem('user_nickname', nickname);
        setEditingNickname(false);
    };

    const saveRegion = (value) => {
        setRegion(value);
        localStorage.setItem('user_region', value);
    };

    const toggleNotifications = () => {
        const newVal = !notifications;
        setNotifications(newVal);
        localStorage.setItem('user_notifications', String(newVal));
    };

    const regions = [
        '서울', '경기', '인천', '부산', '대구', '대전', '광주', '울산', '세종',
        '강원', '충북', '충남', '전북', '전남', '경북', '경남', '제주'
    ];

    return (
        <div className="profile-page">
            {/* Profile Header */}
            <div className="profile-header glass-panel">
                <div className="avatar-circle">
                    <User size={40} strokeWidth={1.5} />
                </div>
                <div className="profile-info">
                    {editingNickname ? (
                        <div className="nickname-edit">
                            <input
                                type="text"
                                value={nickname}
                                onChange={(e) => setNickname(e.target.value)}
                                placeholder="닉네임 입력"
                                className="nickname-input"
                                autoFocus
                                maxLength={20}
                                onKeyDown={(e) => e.key === 'Enter' && saveNickname()}
                            />
                            <button className="save-btn" onClick={saveNickname}>저장</button>
                        </div>
                    ) : (
                        <h2
                            className="profile-name"
                            onClick={() => setEditingNickname(true)}
                        >
                            {nickname || '닉네임을 설정해주세요'}
                            <ChevronRight size={16} />
                        </h2>
                    )}
                    <p className="profile-sub">
                        {region ? `📍 ${region}` : '지역을 선택해주세요'}
                    </p>
                </div>
            </div>

            {/* Stats */}
            <div className="stats-row">
                <div className="stat-card glass-panel">
                    <Heart size={20} className="stat-icon heart" />
                    <span className="stat-value">{favCount}</span>
                    <span className="stat-label">찜한 강좌</span>
                </div>
                <div className="stat-card glass-panel">
                    <BookOpen size={20} className="stat-icon book" />
                    <span className="stat-value">0</span>
                    <span className="stat-label">수강 완료</span>
                </div>
                <div className="stat-card glass-panel">
                    <MapPin size={20} className="stat-icon pin" />
                    <span className="stat-value">0</span>
                    <span className="stat-label">방문 지점</span>
                </div>
            </div>

            {/* Settings */}
            <div className="settings-section">
                <h3 className="section-title">설정</h3>

                <div className="setting-group glass-panel">
                    <div className="setting-item">
                        <div className="setting-left">
                            <Globe size={18} />
                            <span>관심 지역</span>
                        </div>
                        <select
                            className="region-select"
                            value={region}
                            onChange={(e) => saveRegion(e.target.value)}
                        >
                            <option value="">선택</option>
                            {regions.map(r => (
                                <option key={r} value={r}>{r}</option>
                            ))}
                        </select>
                    </div>

                    <div className="setting-divider" />

                    <div className="setting-item" onClick={toggleNotifications}>
                        <div className="setting-left">
                            <Bell size={18} />
                            <span>알림 받기</span>
                        </div>
                        <div className={`toggle ${notifications ? 'active' : ''}`}>
                            <div className="toggle-knob" />
                        </div>
                    </div>
                </div>
            </div>

            {/* Info Section */}
            <div className="settings-section">
                <h3 className="section-title">정보</h3>

                <div className="setting-group glass-panel">
                    <div className="setting-item">
                        <div className="setting-left">
                            <Info size={18} />
                            <span>앱 버전</span>
                        </div>
                        <span className="setting-value">1.0.0</span>
                    </div>

                    <div className="setting-divider" />

                    <div className="setting-item">
                        <div className="setting-left">
                            <Shield size={18} />
                            <span>개인정보 처리방침</span>
                        </div>
                        <ChevronRight size={16} className="setting-arrow" />
                    </div>
                </div>
            </div>

            <p className="footer-text">MoonCen (문센) v1.0 &copy; 2026</p>
        </div>
    );
};

export default ProfilePage;
