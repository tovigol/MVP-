import type { TransactionStatus } from '../types/transaction';

const CONFIG: Record<TransactionStatus, { label: string; fg: string; bg: string; dot: string }> = {
  Pending: { label: 'Pending', fg: 'var(--status-pending)', bg: 'var(--status-pending-bg)', dot: '●' },
  Completed: { label: 'Completed', fg: 'var(--status-completed)', bg: 'var(--status-completed-bg)', dot: '●' },
  Failed: { label: 'Failed', fg: 'var(--status-failed)', bg: 'var(--status-failed-bg)', dot: '●' }
};

export function StatusBadge({ status }: { status: TransactionStatus }) {
  const c = CONFIG[status];
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '3px 10px',
        borderRadius: 999,
        fontSize: 12,
        fontWeight: 600,
        color: c.fg,
        background: c.bg,
        whiteSpace: 'nowrap'
      }}
    >
      <span style={{ fontSize: 8 }}>{c.dot}</span>
      {c.label}
    </span>
  );
}
