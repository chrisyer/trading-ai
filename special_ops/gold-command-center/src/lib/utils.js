import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

export function formatUSD(n, decimals = 2) {
  if (n == null) return '—';
  return `$${Number(n).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}`;
}

export function formatPct(n) {
  if (n == null) return '—';
  return `${(Number(n) * 100).toFixed(0)}%`;
}

export function tsToDate(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true });
}

export function tsToFull(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true });
}

export const ACTION_COLORS = {
  BUY_STARTER: 'text-green-400',
  SELL_STARTER: 'text-red-400',
  ADD_DCA: 'text-blue-400',
  FLATTEN: 'text-gray-300',
  TRIM: 'text-yellow-400',
  DEPLOY_CALLS: 'text-emerald-400',
  DEPLOY_PUTS: 'text-rose-400',
  ROLL_SPREAD: 'text-purple-400',
  HEDGE: 'text-cyan-400',
  NO_TRADE: 'text-gray-500',
  HOLD: 'text-gray-500',
};

export const ACTION_BG = {
  BUY_STARTER: 'bg-green-500/15 border-green-500/30',
  SELL_STARTER: 'bg-red-500/15 border-red-500/30',
  ADD_DCA: 'bg-blue-500/15 border-blue-500/30',
  FLATTEN: 'bg-gray-500/15 border-gray-500/30',
  TRIM: 'bg-yellow-500/15 border-yellow-500/30',
  DEPLOY_CALLS: 'bg-emerald-500/15 border-emerald-500/30',
  DEPLOY_PUTS: 'bg-rose-500/15 border-rose-500/30',
  NO_TRADE: 'bg-gray-500/10 border-gray-500/20',
  HOLD: 'bg-gray-500/10 border-gray-500/20',
};

export const REGIME_COLORS = {
  EXPANSION: 'text-green-400',
  CONTRACTION: 'text-red-400',
  RANGE: 'text-yellow-400',
  RECOVERY: 'text-blue-400',
};
