import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { GoogleMap, MarkerClustererF, MarkerF, useJsApiLoader } from '@react-google-maps/api';
import { Calendar, Folder, Search } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import CourseListItem from '../components/CourseListItem';
import FilterModal from '../components/FilterModal';
import { searchCourses } from '../api';
import './HomePage.css';

const mapContainerStyle = {
    width: '100%',
    height: '100%',
};

const defaultCenter = {
    lat: 37.5,
    lng: 127.0,
};

const DAY_TO_API = {
    월: 'Mon',
    화: 'Tue',
    수: 'Wed',
    목: 'Thu',
    금: 'Fri',
    토: 'Sat',
    일: 'Sun',
};

const FEE_FILTERS = {
    무료: { max_fee: 0 },
    '1만원 이하': { max_fee: 10000 },
    '3만원 이하': { max_fee: 30000 },
    '5만원 이하': { max_fee: 50000 },
    '10만원 이하': { max_fee: 100000 },
};

const HomePage = () => {
    const navigate = useNavigate();
    const [selectedCourse, setSelectedCourse] = useState(null);
    const [map, setMap] = useState(null);
    const [courses, setCourses] = useState([]);
    const [loading, setLoading] = useState(false);
    const [mapBounds, setMapBounds] = useState(null);
    const [openFilter, setOpenFilter] = useState(null);
    const [filters, setFilters] = useState({
        day: [],
        category: [],
        fee: [],
    });

    const { isLoaded, loadError } = useJsApiLoader({
        googleMapsApiKey: import.meta.env.VITE_GOOGLE_MAPS_API_KEY,
        language: 'ko',
        region: 'KR',
    });

    const handleBoundsChange = useCallback(() => {
        if (map) {
            const bounds = map.getBounds();
            if (bounds) {
                setMapBounds(bounds);
            }
        }
    }, [map]);

    const onMapLoad = useCallback((mapInstance) => {
        setMap(mapInstance);
    }, []);

    useEffect(() => {
        const fetchData = async () => {
            setLoading(true);
            try {
                const params = {
                    size: 200,
                    days: filters.day.length > 0 ? filters.day.map((day) => DAY_TO_API[day]).join(',') : undefined,
                    category: filters.category[0],
                    ...FEE_FILTERS[filters.fee[0]],
                };

                const response = await searchCourses(params);
                setCourses(response.items || []);
            } catch (error) {
                console.error(error);
            } finally {
                setLoading(false);
            }
        };

        fetchData();
    }, [filters]);

    const coursesWithCoords = useMemo(
        () => courses.filter((course) => course.branch?.lat && course.branch?.lon),
        [courses],
    );

    const coursesWithoutCoords = useMemo(
        () => courses.filter((course) => !course.branch?.lat || !course.branch?.lon),
        [courses],
    );

    const visibleCourses = useMemo(() => {
        const mappedCourses = mapBounds
            ? coursesWithCoords.filter((course) =>
                mapBounds.contains({ lat: course.branch.lat, lng: course.branch.lon }),
            )
            : coursesWithCoords;
        return [...mappedCourses, ...coursesWithoutCoords];
    }, [coursesWithCoords, coursesWithoutCoords, mapBounds]);

    const handleFilterApply = (type, values) => {
        setFilters((prev) => ({ ...prev, [type]: values }));
    };

    if (loadError) {
        return <div className="home-page">지도를 불러오지 못했습니다.</div>;
    }

    if (!isLoaded) {
        return <div className="home-page">지도를 불러오는 중입니다.</div>;
    }

    return (
        <div className="home-page">
            <FilterModal
                isOpen={!!openFilter}
                onClose={() => setOpenFilter(null)}
                type={openFilter}
                initValue={filters[openFilter] || []}
                onApply={(values) => handleFilterApply(openFilter, values)}
            />

            <div className="sidebar-list">
                <div className="sidebar-header">강좌 목록 ({visibleCourses.length})</div>
                <div className="sidebar-content">
                    {loading && <div style={{ padding: '20px', textAlign: 'center' }}>로딩 중입니다.</div>}
                    {!loading && visibleCourses.map((course) => (
                        <div key={course.id} onClick={() => navigate(`/courses/${course.id}`)} style={{ cursor: 'pointer' }}>
                            <CourseListItem course={course} />
                        </div>
                    ))}
                    {!loading && visibleCourses.length === 0 && (
                        <div style={{ padding: '20px', textAlign: 'center', color: '#999' }}>
                            {coursesWithCoords.length === 0
                                ? '표시할 강좌가 없습니다.'
                                : '현재 지도 영역에는 강좌가 없습니다. 지도를 이동해 보세요.'}
                        </div>
                    )}
                </div>
            </div>

            <div className="map-container-wrapper">
                <div className="map-header">
                    <div className="search-container">
                        <div className="search-input-wrapper">
                            <Search size={20} color="var(--text-secondary)" />
                            <input
                                type="text"
                                placeholder="어떤 강좌를 찾고 있나요? 예: 요리, 발레"
                                readOnly
                                onClick={() => navigate('/list')}
                            />
                        </div>
                    </div>
                    <div className="filter-chips no-scrollbar">
                        <div className={`filter-chip ${filters.day.length > 0 ? 'active' : ''}`} onClick={() => setOpenFilter('day')}>
                            <Calendar size={14} /> 요일 {filters.day.length > 0 && `(${filters.day.length})`}
                        </div>
                        <div className={`filter-chip ${filters.category.length > 0 ? 'active' : ''}`} onClick={() => setOpenFilter('category')}>
                            <Folder size={14} /> 카테고리 {filters.category.length > 0 && `(${filters.category.length})`}
                        </div>
                        <div className={`filter-chip ${filters.fee.length > 0 ? 'active' : ''}`} onClick={() => setOpenFilter('fee')}>
                            <span style={{ fontWeight: 700 }}>₩</span> 수강료
                        </div>
                    </div>
                </div>

                <GoogleMap
                    mapContainerStyle={mapContainerStyle}
                    center={defaultCenter}
                    zoom={11}
                    onLoad={onMapLoad}
                    onIdle={handleBoundsChange}
                    onClick={() => setSelectedCourse(null)}
                    options={{
                        zoomControl: true,
                        streetViewControl: false,
                        mapTypeControl: false,
                        fullscreenControl: false,
                        styles: [
                            { featureType: 'poi', elementType: 'all', stylers: [{ visibility: 'off' }] },
                            { featureType: 'transit', elementType: 'all', stylers: [{ visibility: 'off' }] },
                        ],
                    }}
                >
                    <MarkerClustererF>
                        {(clusterer) =>
                            coursesWithCoords.map((course) => (
                                <MarkerF
                                    key={course.id}
                                    position={{ lat: course.branch.lat, lng: course.branch.lon }}
                                    clusterer={clusterer}
                                    onClick={() => setSelectedCourse(course)}
                                />
                            ))
                        }
                    </MarkerClustererF>
                </GoogleMap>

                <div className={`bottom-sheet ${selectedCourse ? 'open' : ''}`}>
                    {selectedCourse && (
                        <>
                            <div className="sheet-header">
                                <h3 className="sheet-title">{selectedCourse.title}</h3>
                                <div className="sheet-subtitle">
                                    {selectedCourse.branch?.name} | {selectedCourse.instructor || '강사 미정'}
                                </div>
                            </div>
                            <button className="sheet-action" onClick={() => navigate(`/courses/${selectedCourse.id}`)}>
                                상세 보기
                            </button>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
};

export default HomePage;
