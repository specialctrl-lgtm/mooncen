import axios from 'axios';

// Use the same API prefix in development and behind the production reverse proxy.
const api = axios.create({
    baseURL: '/api',
    timeout: 10_000,
    headers: {
        'Content-Type': 'application/json',
    },
});

export const searchCourses = async (params) => {
    try {
        const response = await api.get('/courses/', { params });
        return response.data;
    } catch (error) {
        console.error("API Search Error:", error);
        throw error;
    }
};

export const getCourseDetail = async (id) => {
    try {
        const response = await api.get(`/courses/${id}`);
        return response.data;
    } catch (error) {
        console.error("API Detail Error:", error);
        throw error;
    }
};
