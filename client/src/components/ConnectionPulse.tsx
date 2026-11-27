import { motion } from 'framer-motion';
import type { ConnectionState } from '../store/transactionStore';

const COPY: Record<ConnectionState, { label: string; color: string }> = {
  connecting: { label: 'Connecting', color: 'var(--text-muted)' },
  connected: { label: 'Live', color: 'var(--live)' },
  reconnecting: { label: 'Reconnecting', color: 'var(--status-pending)' },
  disconnected: { label: 'Offline', color: 'var(--status-failed)' }
};

export function ConnectionPulse({ state }: { state: ConnectionState }) {
  const { label, color } = COPY[state];
  const isLive = state === 'connected';

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ position: 'relative', width: 10, height: 10, display: 'inline-block' }}>
        <span
          style={{
            position: 'absolute',
            inset: 0,
            borderRadius: '50%',
            background: color
          }}
        />
        {isLive && (
          <motion.span
            style={{
              position: 'absolute',
              inset: 0,
              borderRadius: '50%',
              background: color
            }}
            animate={{ scale: [1, 2.4], opacity: [0.6, 0] }}
            transition={{ duration: 1.6, repeat: Infinity, ease: 'easeOut' }}
          />
        )}
      </span>
      <span className="mono" style={{ fontSize: 12, color: 'var(--text-muted)', letterSpacing: 0.4 }}>
        {label.toUpperCase()}
      </span>
    </div>
  );
}
