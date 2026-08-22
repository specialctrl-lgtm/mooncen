type QuickCategoryChip = {
  id: string;
  label: string;
};

type QuickCategoryChipsProps = {
  activeId: string;
  chips: QuickCategoryChip[];
  onSelect: (chipId: string) => void;
};

export default function QuickCategoryChips({ activeId, chips, onSelect }: QuickCategoryChipsProps) {
  return (
    <section className="quick-category-section" aria-label="빠른 카테고리">
      <div className="quick-category-strip">
        {chips.map((chip) => {
          const active = activeId === chip.id;
          return (
            <button
              key={chip.id}
              className={active ? 'active' : undefined}
              type="button"
              onClick={() => onSelect(chip.id)}
            >
              <span>{chip.label}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
