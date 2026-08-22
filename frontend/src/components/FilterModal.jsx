import React, { useEffect, useState } from 'react';
import { Check, X } from 'lucide-react';
import './FilterModal.css';

const OPTIONS = {
    day: {
        title: '요일 선택',
        items: ['월', '화', '수', '목', '금', '토', '일'],
    },
    category: {
        title: '카테고리 선택',
        items: [
            '공공예약',
            '교육/강좌',
            '교육·강좌',
            '체험·견학',
            'Cooking',
            'Art',
            'Fitness',
            'Language',
            'Kids',
            'Music',
            'Technology',
            'Lifestyle',
            'Beauty',
            'Other',
        ],
    },
    fee: {
        title: '수강료 선택',
        items: ['무료', '1만원 이하', '3만원 이하', '5만원 이하', '10만원 이하'],
    },
};

const FilterModal = ({ isOpen, onClose, type, initValue = [], onApply }) => {
    const [tempValue, setTempValue] = useState(initValue);

    useEffect(() => {
        if (isOpen) {
            setTempValue(Array.isArray(initValue) ? initValue : []);
        }
    }, [isOpen, initValue]);

    if (!isOpen || !OPTIONS[type]) return null;

    const { title, items } = OPTIONS[type];

    const handleToggle = (value) => {
        if (tempValue.includes(value)) {
            setTempValue(tempValue.filter((item) => item !== value));
            return;
        }

        if (type === 'category' || type === 'fee') {
            setTempValue([value]);
            return;
        }

        setTempValue([...tempValue, value]);
    };

    const handleApply = () => {
        onApply(tempValue);
        onClose();
    };

    return (
        <div className="filter-modal-overlay">
            <div className="filter-modal-content">
                <div className="filter-header">
                    <h3>{title}</h3>
                    <button className="close-btn" onClick={onClose}>
                        <X size={20} />
                    </button>
                </div>

                <div className="filter-body">
                    <div className="option-grid">
                        {items.map((option) => {
                            const selected = tempValue.includes(option);
                            return (
                                <button
                                    key={option}
                                    className={`filter-option ${selected ? 'selected' : ''}`}
                                    onClick={() => handleToggle(option)}
                                >
                                    {option}
                                    {selected && <Check size={14} />}
                                </button>
                            );
                        })}
                    </div>
                </div>

                <div className="filter-footer">
                    <button className="apply-full-btn" onClick={handleApply}>
                        적용하기
                    </button>
                </div>
            </div>
        </div>
    );
};

export default FilterModal;
