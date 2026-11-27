import { memo } from 'react';
import { motion } from 'framer-motion';
import type { CSSProperties } from 'react';
import type { Transaction } from '../types/transaction';
import { StatusBadge } from './StatusBadge';

const STATUS_BORDER: Record<Transaction['status'], string> = {
  Pending: 'var(--status-pending)',
  Completed: 'var(--status-completed)',
  Failed: 'var(--status-failed)'
};

interface Props {
  transaction: Transaction;
  style: CSSProperties;
  isNew: boolean;
}

function formatTime(iso: string) {
  const d = new Date(iso);
  return d.toLocaleTimeString(undefined, { hour12: false });
}

function formatAmount(amount: number, currency: string) {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount);
  } catch {
    return `${amount.toFixed(2)} ${currency}`;
  }
}

function TransactionRowBase({ transaction, style, isNew }: Props) {
  return (
    <div style={style}>
      <motion.div
        initial={isNew ? { opacity: 0, x: -12, backgroundColor: 'rgba(79,168,255,0.14)' } : false}
        animate={{ opacity: 1, x: 0, backgroundColor: 'rgba(79,168,255,0)' }}
        transition={{ duration: 0.5, ease: 'easeOut' }}
        style={{
          display: 'grid',
          gridTemplateColumns: '2fr 1fr 1fr 1.2fr 1fr',
          alignItems: 'center',
          gap: 12,
          height: '100%',
          padding: '0 16px',
          borderLeft: `3px solid ${STATUS_BORDER[transaction.status]}`,
          borderBottom: '1px solid var(--border)'
        }}
      >
        <span className="mono" style={{ color: 'var(--text-faint)', fontSize: 13 }}>
          {transaction.transactionId}
        </span>
        <span className="mono" style={{ fontSize: 14, fontWeight: 600 }}>
          {formatAmount(transaction.amount, transaction.currency)}
        </span>
        <span className="mono" style={{ fontSize: 13, color: 'var(--text-muted)' }}>
          {transaction.currency}
        </span>
        <StatusBadge status={transaction.status} />
        <span className="mono" style={{ fontSize: 13, color: 'var(--text-muted)', textAlign: 'right' }}>
          {formatTime(transaction.timestamp)}
        </span>
      </motion.div>
    </div>
  );
}

export const TransactionRow = memo(TransactionRowBase);
