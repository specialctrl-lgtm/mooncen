import React from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Home, MapPin, Heart, User } from 'lucide-react';
import './MainLayout.css';

const MainLayout = () => {
    const navigate = useNavigate();
    const location = useLocation();

    const tabs = [
        { id: 'home', icon: Home, label: '홈', path: '/' },
        { id: 'nearby', icon: MapPin, label: '내 주변', path: '/nearby' },
        { id: 'favorites', icon: Heart, label: '찜목록', path: '/favorites' },
        { id: 'profile', icon: User, label: '내 정보', path: '/profile' },
    ];

    return (
        <div className="main-layout">
            <div className="content-container">
                <Outlet />
            </div>

            <nav className="bottom-nav glass-panel">
                {tabs.map((tab) => {
                    const Icon = tab.icon;
                    const isActive = location.pathname === tab.path;
                    return (
                        <button
                            key={tab.id}
                            className={`nav-item ${isActive ? 'active' : ''}`}
                            onClick={() => navigate(tab.path)}
                        >
                            <Icon size={24} strokeWidth={isActive ? 2.5 : 2} />
                            <span className="nav-label">{tab.label}</span>
                        </button>
                    );
                })}
            </nav>
        </div>
    );
};

export default MainLayout;
