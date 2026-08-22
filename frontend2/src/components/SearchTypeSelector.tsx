type SearchType = 'provider' | 'education' | 'experience';

type SearchTypeOption = {
  value: SearchType;
  title: string;
  description: string;
  icon: string;
};

type SearchTypeSelectorProps = {
  value: SearchType;
  onChange: (value: SearchType) => void;
};

const options: SearchTypeOption[] = [
  {
    value: 'provider',
    title: '문화센터',
    description: '백화점·마트 문화센터 강좌',
    icon: '문',
  },
  {
    value: 'experience',
    title: '체험',
    description: '도서관·박물관·체험 시설',
    icon: '체',
  },
  {
    value: 'education',
    title: '교육',
    description: '공공강좌·평생학습 프로그램',
    icon: '교',
  },
];

export default function SearchTypeSelector({ value, onChange }: SearchTypeSelectorProps) {
  return (
    <section className="search-type-section" aria-labelledby="search-type-heading">
      <div className="compact-section-heading">
        <h2 id="search-type-heading">탐색 범위</h2>
      </div>
      <div className="search-type-grid" role="tablist" aria-label="탐색 범위 선택">
        {options.map((option) => {
          const active = value === option.value;
          return (
            <button
              key={option.value}
              className={active ? 'active' : undefined}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => onChange(option.value)}
            >
              <span className="search-type-icon" aria-hidden="true">{option.icon}</span>
              <span className="search-type-copy">
                <strong>{option.title}</strong>
                <small>{option.description}</small>
              </span>
              {active && <span className="search-type-check" aria-hidden="true">✓</span>}
            </button>
          );
        })}
      </div>
    </section>
  );
}
