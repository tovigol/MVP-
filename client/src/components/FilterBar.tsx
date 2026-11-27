import type { StatusFilter } from '../store/transactionStore';

const OPTIONS: StatusFilter[] = ['All', 'Pending', 'Completed', 'Failed'];

interface Props {
  value: StatusFilter;
  onChange: (value: StatusFilter) => void;
  counts: Record<StatusFilter, number>;
}

export function FilterBar({ value, onChange, counts }: Props) {
  return (
    <div role="tablist" aria-label="Filter transactions by status" style={{ display: 'flex', gap: 6 }}>
      {OPTIONS.map((opt) => {
        const active = opt === value;
        return (
          <button
            key={opt}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(opt)}
            style={{
              padding: '7px 14px',
              borderRadius: 8,
              border: `1px solid ${active ? 'var(--live)' : 'var(--border)'}`,
              background: active ? 'rgba(79, 168, 255, 0.12)' : 'var(--panel-raised)',
              color: active ? 'var(--live)' : 'var(--text-muted)',
              fontSize: 13,
              fontWeight: 600,
              cursor: 'pointer',
              transition: 'border-color 120ms ease, color 120ms ease, background 120ms ease'
            }}
          >
            {opt}
            <span className="mono" style={{ marginLeft: 8, opacity: 0.7, fontSize: 12 }}>
              {counts[opt]}
            </span>
          </button>
        );
      })}
    </div>
  );
}
