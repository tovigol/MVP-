import { NavLink, Navigate, Route, Routes } from 'react-router-dom';
import { Monitor } from './pages/Monitor';
import { AddTransaction } from './pages/AddTransaction';

const navLinkStyle = ({ isActive }: { isActive: boolean }): React.CSSProperties => ({
  padding: '8px 14px',
  borderRadius: 8,
  fontSize: 13,
  fontWeight: 600,
  textDecoration: 'none',
  color: isActive ? 'var(--text-primary)' : 'var(--text-muted)',
  background: isActive ? 'var(--panel-raised)' : 'transparent'
});

export default function App() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <nav
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '14px 24px',
          borderBottom: '1px solid var(--border)',
          background: 'var(--panel)'
        }}
      >
        <span className="mono" style={{ fontSize: 14, fontWeight: 700, marginRight: 16, color: 'var(--live)' }}>
          ● PULSE
        </span>
        <NavLink to="/monitor" style={navLinkStyle}>
          Live Dashboard
        </NavLink>
        <NavLink to="/add" style={navLinkStyle}>
          Simulator
        </NavLink>
      </nav>

      <main style={{ flex: 1, minHeight: 0 }}>
        <Routes>
          <Route path="/monitor" element={<Monitor />} />
          <Route path="/add" element={<AddTransaction />} />
          <Route path="*" element={<Navigate to="/monitor" replace />} />
        </Routes>
      </main>
    </div>
  );
}
