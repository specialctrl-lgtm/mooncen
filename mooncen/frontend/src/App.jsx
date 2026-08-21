import React, { lazy, Suspense } from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import MainLayout from './layout/MainLayout';

const HomePage = lazy(() => import('./pages/HomePage'));
const CourseListPage = lazy(() => import('./pages/CourseListPage'));
const CourseDetailPage = lazy(() => import('./pages/CourseDetailPage'));
const FavoritesPage = lazy(() => import('./pages/FavoritesPage'));
const ProfilePage = lazy(() => import('./pages/ProfilePage'));

function App() {
  return (
    <Router>
      <Suspense fallback={<div role="status" aria-live="polite">화면을 불러오는 중입니다.</div>}>
        <Routes>
          <Route element={<MainLayout />}>
            <Route path="/" element={<HomePage />} />
            <Route path="/nearby" element={<HomePage />} /> {/* Map view reuse */}
            <Route path="/list" element={<CourseListPage />} />
            <Route path="/favorites" element={<FavoritesPage />} />
            <Route path="/profile" element={<ProfilePage />} />
          </Route>
          <Route path="/courses/:id" element={<CourseDetailPage />} />
        </Routes>
      </Suspense>
    </Router>
  );
}

export default App;
