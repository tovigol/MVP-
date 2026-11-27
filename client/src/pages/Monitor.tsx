import { useEffect, useMemo, useState } from 'react';
import { FixedSizeList } from 'react-window';
import { useTransactionStore, selectFilteredTransactions, type StatusFilter } from '../store/transactionStore';
import { useTransactionSocket } from '../hooks/useTransactionSocket';
import { ConnectionPulse } from '../components/ConnectionPulse';
import { FilterBar } from '../components/FilterBar';
import { TransactionRow } from '../components/TransactionRow';

const ROW_HEIGHT = 52;
const HEADER_COLS = ['Transaction ID', 'Amount', 'Currency', 'Status', 'Time'];

export function Monitor() {
  useTransactionSocket();

  const connectionState = useTransactionStore((s) => s.connectionState);
  const statusFilter = useTransactionStore((s) => s.statusFilter);
  const setStatusFilter = useTransactionStore((s) => s.setStatusFilter);
  const lastReceivedId = useTransactionStore((s) => s.lastReceivedId);
  const transactions = useTransactionStore(selectFilteredTransactions);
  const allTransactions = useTransactionStore((s) => s.transactions);

  const counts = useMemo(() => {
    const base: Record<StatusFilter, number> = { All: allTransactions.length, Pending: 0, Completed: 0, Failed: 0 };
    for (const t of allTransactions) base[t.status]++;
    return base;
  }, [allTransactions]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '20px 24px',
          borderBottom: '1px solid var(--border)'
        }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>Live Dashboard</h1>
          <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--text-muted)' }}>
            Streaming transactions as they're ingested, across every backend replica.
          </p>
        </div>
        <ConnectionPulse state={connectionState} />
      </div>

      <div style={{ padding: '16px 24px', borderBottom: '1px solid var(--border)' }}>
        <FilterBar value={statusFilter} onChange={setStatusFilter} counts={counts} />
      </div>

      <div
        className="mono"
        style={{
          display: 'grid',
          gridTemplateColumns: '2fr 1fr 1fr 1.2fr 1fr',
          gap: 12,
          padding: '10px 16px 10px 19px',
          fontSize: 11,
          letterSpacing: 0.6,
          color: 'var(--text-faint)',
          borderBottom: '1px solid var(--border)',
          background: 'var(--panel)'
        }}
      >
        {HEADER_COLS.map((c, i) => (
          <span key={c} style={{ textAlign: i === HEADER_COLS.length - 1 ? 'right' : 'left' }}>
            {c.toUpperCase()}
          </span>
        ))}
      </div>

      <div style={{ flex: 1, minHeight: 0 }}>
        {transactions.length === 0 ? (
          <EmptyState filtered={statusFilter !== 'All'} />
        ) : (
          <AutoSizedList transactions={transactions} lastReceivedId={lastReceivedId} />
        )}
      </div>
    </div>
  );
}

function AutoSizedList({
  transactions,
  lastReceivedId
}: {
  transactions: ReturnType<typeof selectFilteredTransactions>;
  lastReceivedId: string | null;
}) {
  // A lightweight resize-aware wrapper keeps react-window filling its
  // parent without pulling in a separate auto-sizer dependency.
  const [height, setHeight] = useState(() => window.innerHeight - 260);

  useEffect(() => {
    const onResize = () => setHeight(window.innerHeight - 260);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  return (
    <div style={{ height: '100%' }}>
      <FixedSizeList height={height} itemCount={transactions.length} itemSize={ROW_HEIGHT} width="100%" overscanCount={12}>
        {({ index, style }) => {
          const tx = transactions[index];
          return <TransactionRow key={tx.transactionId} transaction={tx} style={style} isNew={tx.transactionId === lastReceivedId} />;
        }}
      </FixedSizeList>
    </div>
  );
}

function EmptyState({ filtered }: { filtered: boolean }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-muted)' }}>
      <p style={{ fontSize: 14, margin: 0 }}>
        {filtered ? 'No transactions match this filter yet.' : 'Waiting for the first transaction to arrive.'}
      </p>
      <p style={{ fontSize: 13, margin: '6px 0 0', color: 'var(--text-faint)' }}>
        Open the Simulator tab to send one.
      </p>
    </div>
  );
}
