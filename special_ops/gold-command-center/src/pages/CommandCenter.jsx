import { api } from '../lib/api';
import { usePolling } from '../hooks/usePolling';
import { Card, CardHeader, CardTitle } from '../components/Card';
import { Badge } from '../components/Badge';
import {
  formatUSD, formatPct, tsToDate, ACTION_COLORS, ACTION_BG,
  REGIME_COLORS, cn
} from '../lib/utils';
import {
  TrendingUp, Shield, Zap, AlertTriangle, BarChart3, Eye
} from 'lucide-react';

function ScoreBar({ label, value, max = 1, color = 'bg-amber-500' }) {
  const pct = Math.min(Math.max((value / max) * 100, 0), 100);
  return (
    <div className="flex items-center gap-3">
      <span className="w-28 shrink-0 text-xs text-[var(--text-secondary)]">{label}</span>
      <div className="h-2 flex-1 rounded-full bg-[var(--border)]">
        <div className={cn('h-2 rounded-full transition-all', color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-10 text-right text-xs font-mono">{typeof value === 'number' ? value.toFixed(2) : value}</span>
    </div>
  );
}

function RecCard({ instrument, rec }) {
  const action = rec?.action || 'NO_TRADE';
  const reason = rec?.reason || '';
  const colorClass = ACTION_COLORS[action] || 'text-gray-500';
  const bgClass = ACTION_BG[action] || 'bg-gray-500/10 border-gray-500/20';

  return (
    <div className={cn('rounded-lg border p-4', bgClass)}>
      <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
        {instrument}
      </div>
      <div className={cn('text-xl font-bold', colorClass)}>{action.replace('_', ' ')}</div>
      <div className="mt-1 text-xs text-[var(--text-secondary)]">{reason.replace('_', ' ')}</div>
      {rec?.contracts != null && (
        <div className="mt-2 text-sm">Contracts: <span className="font-semibold">{rec.contracts}</span></div>
      )}
      {rec?.structure && (
        <div className="mt-2 text-sm">Structure: <span className="font-semibold">{rec.structure}</span></div>
      )}
    </div>
  );
}

function BlockIndicator({ blocks }) {
  if (!blocks) return null;
  const active = Object.entries(blocks).filter(([, v]) => v);
  if (active.length === 0) {
    return <Badge className="bg-green-500/15 border-green-500/30 text-green-400">All Clear</Badge>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {active.map(([k]) => (
        <Badge key={k} className="bg-red-500/15 border-red-500/30 text-red-400">
          {k.replace(/_/g, ' ')}
        </Badge>
      ))}
    </div>
  );
}

export default function CommandCenter() {
  const { data: state, loading, error } = usePolling(api.getState, 5000);

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center text-[var(--text-secondary)]">
        Loading Gold Mirror state...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-64 items-center justify-center text-red-400">
        <AlertTriangle className="mr-2" size={18} /> {error}
      </div>
    );
  }

  const { price, oracle_gate, regime, confidence, scores, volatility, blocks, recommendation, xauusd, control } = state;
  const regimeColor = REGIME_COLORS[regime] || 'text-gray-400';

  return (
    <div className="space-y-4">
      {/* Top bar: price + regime + gate */}
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-2">
          <span className="text-3xl font-bold text-amber-400">{formatUSD(price)}</span>
          <span className="text-sm text-[var(--text-secondary)]">XAUUSD</span>
        </div>
        <Badge className={cn('border-transparent text-sm', regimeColor, 'bg-current/10')}>
          {regime}
        </Badge>
        <Badge className="border-amber-500/30 bg-amber-500/10 text-amber-400">
          Gate: {oracle_gate}
        </Badge>
        <Badge className="border-blue-500/30 bg-blue-500/10 text-blue-400">
          Confidence: {formatPct(confidence)}
        </Badge>
        <span className="ml-auto text-xs text-[var(--text-secondary)]">
          Updated: {tsToDate(state.ts)}
        </span>
      </div>

      {/* Recommendations row */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <RecCard instrument="MGC (Micro Gold Futures)" rec={recommendation?.mgc} />
        <RecCard instrument="GLD (Gold ETF Options)" rec={recommendation?.gld} />
      </div>

      {/* Scoring + Volatility + Blocks */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle><BarChart3 size={14} className="mr-1 inline" />Scoring</CardTitle>
          </CardHeader>
          <div className="space-y-2.5">
            <ScoreBar label="RSI" value={scores?.rsi ?? 0} max={1} color="bg-blue-500" />
            <ScoreBar label="ATR Deviation" value={scores?.atr_deviation ?? 0} max={1} color="bg-orange-500" />
            <ScoreBar label="Calm" value={scores?.calm ?? 0} max={1} color="bg-green-500" />
            <ScoreBar label="Sentiment" value={scores?.sentiment ?? 0} max={1} color="bg-purple-500" />
            <ScoreBar label="ML Boost" value={scores?.ml_boost ?? 0} max={1} color="bg-cyan-500" />
          </div>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle><Zap size={14} className="mr-1 inline" />Volatility</CardTitle>
          </CardHeader>
          <div className="space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-[var(--text-secondary)]">ATR (H1)</span>
              <span className="font-mono">{volatility?.atr_h1?.toFixed(2) ?? '—'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--text-secondary)]">ATR Percentile</span>
              <span className="font-mono">{formatPct(volatility?.atr_percentile)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--text-secondary)]">Bucket</span>
              <Badge className={cn('text-xs',
                volatility?.bucket === 'extreme' ? 'bg-red-500/15 border-red-500/30 text-red-400' :
                  volatility?.bucket === 'high' ? 'bg-orange-500/15 border-orange-500/30 text-orange-400' :
                    'bg-green-500/15 border-green-500/30 text-green-400'
              )}>{volatility?.bucket?.toUpperCase() ?? '—'}</Badge>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--text-secondary)]">Spike Ratio</span>
              <span className="font-mono">{volatility?.spike_ratio?.toFixed(2) ?? '—'}</span>
            </div>
          </div>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle><Shield size={14} className="mr-1 inline" />Safety Blocks</CardTitle>
          </CardHeader>
          <BlockIndicator blocks={blocks} />
          <div className="mt-4 space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-[var(--text-secondary)]">DCA Multiplier</span>
              <span className="font-mono">{control?.dca_step_multiplier?.toFixed(1) ?? '—'}x</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--text-secondary)]">Max Layers</span>
              <span className="font-mono">{control?.max_layers_cap ?? '—'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--text-secondary)]">New Entries</span>
              <span className={control?.disable_new_entries ? 'text-red-400' : 'text-green-400'}>
                {control?.disable_new_entries ? 'BLOCKED' : 'ENABLED'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--text-secondary)]">Risk Bias</span>
              <span className="font-mono">{control?.risk_bias?.toFixed(1) ?? '—'}</span>
            </div>
          </div>
        </Card>
      </div>

      {/* XAUUSD Account */}
      <Card>
        <CardHeader>
          <CardTitle><Eye size={14} className="mr-1 inline" />XAUUSD Account</CardTitle>
        </CardHeader>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-7 text-sm">
          {[
            ['Equity', formatUSD(xauusd?.equity, 0)],
            ['Balance', formatUSD(xauusd?.balance, 0)],
            ['Float P&L', formatUSD(xauusd?.floating_pnl, 0)],
            ['Layers', xauusd?.layers ?? 0],
            ['Net Lots', xauusd?.net_lots?.toFixed(2) ?? '0'],
            ['WAVG', formatUSD(xauusd?.wavg)],
            ['RSI', xauusd?.rsi?.toFixed(1) ?? '—'],
          ].map(([label, val]) => (
            <div key={label}>
              <div className="text-xs text-[var(--text-secondary)]">{label}</div>
              <div className="mt-0.5 font-mono font-semibold">{val}</div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
