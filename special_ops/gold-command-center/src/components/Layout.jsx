import { NavLink, Outlet } from 'react-router-dom';
import { Activity, Clock, Wallet, Settings } from 'lucide-react';
import { cn } from '../lib/utils';

const NAV = [
  { to: '/', label: 'Command Center', icon: Activity },
  { to: '/timeline', label: 'Signal Timeline', icon: Clock },
  { to: '/positions', label: 'Positions & Risk', icon: Wallet },
  { to: '/settings', label: 'Settings', icon: Settings },
];

export default function Layout() {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-50 border-b border-[var(--border)] bg-[var(--bg-primary)]/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1440px] items-center gap-6 px-4">
          <div className="flex items-center gap-2">
            <span className="text-2xl">🥇</span>
            <span className="text-base font-bold tracking-tight text-amber-400">
              Gold Command Center
            </span>
          </div>

          <nav className="flex items-center gap-1">
            {NAV.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                className={({ isActive }) =>
                  cn(
                    'flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors',
                    isActive
                      ? 'bg-amber-500/15 text-amber-400'
                      : 'text-[var(--text-secondary)] hover:bg-[var(--bg-card)] hover:text-[var(--text-primary)]'
                  )
                }
              >
                <Icon size={15} />
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-[1440px] p-4">
        <Outlet />
      </main>
    </div>
  );
}
