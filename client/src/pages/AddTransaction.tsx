import { useCallback, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { postTransaction, ApiError } from '../api/client';
import { CURRENCIES, randomTransaction } from '../types/transaction';
import type { Transaction, TransactionStatus } from '../types/transaction';
import { StatusBadge } from '../components/StatusBadge';

type LogEntry = { tx: Transaction; ok: boolean; error?: string };

export function AddTransaction() {
  const [amount, setAmount] = useState('250.00');
  const [currency, setCurrency] = useState<(typeof CURRENCIES)[number]>('USD');
  const [status, setStatus] = useState<TransactionStatus>('Pending');
  const [log, setLog] = useState<LogEntry[]>([]);
  const [autoFireCount, setAutoFireCount] = useState(0);
  const [sending, setSending] = useState(false);

  const send = useCallback(async (tx: Transaction) => {
    try {
      await postTransaction(tx);
      setLog((prev) => [{ tx, ok: true }, ...prev].slice(0, 25));
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Network error';
      setLog((prev) => [{ tx, ok: false, error: message }, ...prev].slice(0, 25));
    }
  }, []);

  const handleManualSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const tx: Transaction = {
      transactionId: crypto.randomUUID(),
      amount: Number(amount) || 0,
      currency,
      status,
      timestamp: new Date().toISOString()
    };
    setSending(true);
    await send(tx);
    setSending(false);
  };

  const fireRandom = async () => {
    await send(randomTransaction());
  };

  const fireBurst = async (count: number) => {
    setAutoFireCount(count);
    for (let i = 0; i < count; i++) {
      // Fire concurrently in small batches so this doubles as a quick
      // way to exercise the "100 transactions arriving fast" scenario.
      void send(randomTransaction());
      await new Promise((r) => setTimeout(r, 15));
    }
    setAutoFireCount(0);
  };

  return (
    <div style={{ padding: '24px', display: 'grid', gridTemplateColumns: '380px 1fr', gap: 24, height: '100%' }}>
      <div>
        <h1 style={{ margin: '0 0 4px', fontSize: 20, fontWeight: 700 }}>Transaction Simulator</h1>
        <p style={{ margin: '0 0 20px', fontSize: 13, color: 'var(--text-muted)' }}>
          Feeds mock transactions into the ingestion API, as if from an external system.
        </p>

        <form
          onSubmit={handleManualSubmit}
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 14,
            padding: 18,
            borderRadius: 12,
            background: 'var(--panel)',
            border: '1px solid var(--border)'
          }}
        >
          <Field label="Amount">
            <input
              className="mono"
              type="number"
              step="0.01"
              min="0"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              style={inputStyle}
            />
          </Field>

          <Field label="Currency">
            <select className="mono" value={currency} onChange={(e) => setCurrency(e.target.value as typeof currency)} style={inputStyle}>
              {CURRENCIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Status">
            <select
              className="mono"
              value={status}
              onChange={(e) => setStatus(e.target.value as TransactionStatus)}
              style={inputStyle}
            >
              <option value="Pending">Pending</option>
              <option value="Completed">Completed</option>
              <option value="Failed">Failed</option>
            </select>
          </Field>

          <button type="submit" disabled={sending} style={primaryButtonStyle}>
            {sending ? 'Sending…' : 'Send transaction'}
          </button>
        </form>

        <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <button onClick={fireRandom} style={secondaryButtonStyle}>
            Fire one random transaction
          </button>
          <button onClick={() => fireBurst(100)} disabled={autoFireCount > 0} style={secondaryButtonStyle}>
            {autoFireCount > 0 ? `Firing burst… (${autoFireCount} queued)` : 'Fire burst of 100'}
          </button>
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <h2 style={{ margin: '0 0 10px', fontSize: 14, fontWeight: 600, color: 'var(--text-muted)' }}>Send log</h2>
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            border: '1px solid var(--border)',
            borderRadius: 12,
            background: 'var(--panel)'
          }}
        >
          <AnimatePresence initial={false}>
            {log.map((entry) => (
              <motion.div
                key={entry.tx.transactionId}
                initial={{ opacity: 0, y: -8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.25 }}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '2fr 1fr 1fr 1fr',
                  alignItems: 'center',
                  gap: 12,
                  padding: '10px 16px',
                  borderBottom: '1px solid var(--border)',
                  fontSize: 13
                }}
              >
                <span className="mono" style={{ color: 'var(--text-faint)' }}>
                  {entry.tx.transactionId}
                </span>
                <span className="mono">
                  {entry.tx.amount.toFixed(2)} {entry.tx.currency}
                </span>
                <StatusBadge status={entry.tx.status} />
                <span
                  className="mono"
                  style={{ color: entry.ok ? 'var(--status-completed)' : 'var(--status-failed)', textAlign: 'right' }}
                >
                  {entry.ok ? 'sent' : entry.error ?? 'failed'}
                </span>
              </motion.div>
            ))}
          </AnimatePresence>
          {log.length === 0 && (
            <p style={{ padding: 16, fontSize: 13, color: 'var(--text-faint)' }}>Nothing sent yet.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)' }}>{label}</span>
      {children}
    </label>
  );
}

const inputStyle: React.CSSProperties = {
  background: 'var(--panel-raised)',
  border: '1px solid var(--border)',
  borderRadius: 8,
  color: 'var(--text-primary)',
  padding: '9px 12px',
  fontSize: 14
};

const primaryButtonStyle: React.CSSProperties = {
  marginTop: 4,
  padding: '11px 16px',
  borderRadius: 8,
  border: 'none',
  background: 'var(--live)',
  color: '#06121f',
  fontWeight: 700,
  fontSize: 14,
  cursor: 'pointer'
};

const secondaryButtonStyle: React.CSSProperties = {
  padding: '10px 16px',
  borderRadius: 8,
  border: '1px solid var(--border)',
  background: 'var(--panel-raised)',
  color: 'var(--text-primary)',
  fontSize: 13,
  fontWeight: 600,
  cursor: 'pointer',
  textAlign: 'left'
};
