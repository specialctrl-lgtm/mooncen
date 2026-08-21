import { useState } from 'react';

type SearchHeroOption = {
  value: string;
  label: string;
};

type SearchHeroProps = {
  ageOptions: SearchHeroOption[];
  categoryOptions: SearchHeroOption[];
  selectedAge: string;
  selectedCategory: string;
  onAgeChange: (value: string) => void;
  onCategoryChange: (value: string) => void;
  onSearch: () => void;
  onOpenAdvancedSearch: () => void;
};

const characterImageCandidates = ['/assets/characters/moon-cen-main.png'];

export default function SearchHero({
  ageOptions,
  categoryOptions,
  selectedAge,
  selectedCategory,
  onAgeChange,
  onCategoryChange,
  onSearch,
  onOpenAdvancedSearch,
}: SearchHeroProps) {
  const [characterImageIndex, setCharacterImageIndex] = useState(0);
  const characterImageSrc = characterImageCandidates[characterImageIndex];
  const characterImageFailed = characterImageIndex >= characterImageCandidates.length;

  return (
    <section className="search-hero search-hero-compact" aria-labelledby="search-hero-heading">
      <div className="search-hero-main">
        <div className="search-hero-copy">
          <p className="search-hero-kicker">우리 동네 강좌를 빠르게 비교하세요</p>
          <h1 id="search-hero-heading" className="sr-only">강좌 빠른 검색</h1>
        </div>

        <div className="search-hero-controls" aria-label="빠른 강좌 검색">
          <label>
            <span>연령 선택</span>
            <select value={selectedAge} onChange={(event) => onAgeChange(event.target.value)}>
              <option value="">연령 선택</option>
              {ageOptions.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label>
            <span>카테고리 선택</span>
            <select value={selectedCategory} onChange={(event) => onCategoryChange(event.target.value)}>
              <option value="">카테고리 선택</option>
              {categoryOptions.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <button className="search-hero-primary" type="button" onClick={onSearch}>
            검색하기
          </button>
          <button className="search-hero-secondary" type="button" onClick={onOpenAdvancedSearch}>
            상세 검색
          </button>
        </div>
      </div>

      <div className="search-hero-character" aria-hidden="true">
        {characterImageSrc && !characterImageFailed ? (
          <img
            src={characterImageSrc}
            srcSet="/assets/characters/moon-cen-main-480.png 480w, /assets/characters/moon-cen-main-960.png 960w, /assets/characters/moon-cen-main.png 1536w"
            sizes="(max-width: 760px) 45vw, 420px"
            width="1536"
            height="1024"
            alt=""
            loading="eager"
            fetchPriority="high"
            decoding="async"
            onError={() => setCharacterImageIndex((index) => index + 1)}
          />
        ) : (
          <div className="mooncen-character-placeholder">
            <p>Moon &amp; Cen 캐릭터 이미지 영역</p>
          </div>
        )}
      </div>
    </section>
  );
}
