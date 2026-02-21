import { api } from '../lib/api';
import { usePolling } from '../hooks/usePolling';
import { Card, CardHeader, CardTitle } from '../components/Card';
import { Badge } from '../components/Badge';
import { ACTION_COLORS, ACTION_BG, tsToFull, formatUSD, cn } from '../lib/utils';
import { Clock, AlertTriangle, RefreshCw, Download } from 'lucide-react';

function ActionCell({ action }) {
  const color = ACTION_COLORS[action] || 'text-gray-500';
  const bg = ACTION_BG[action] || '';
  return (
    <Badge className={cn(bg, color, 'text-xs')}>
      {action?.replace(/_/g, ' ') || '—'}
    </Badge>
  );
}

export default function Timeline() {
  const { data, loading, error, refresh } = usePolling(
    () => api.getTimeline(500),
    10000
  );

  const entries = data?.entries || [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-lg font-bold">
          <Clock size={18} className="text-amber-400" />
          Signal Timeline
          <span className="text-sm font-normal text-[var(--text-secondary)]">
            ({entries.length} entries)
          </span>
        </h2>
        <div className="flex items-center gap-2">
          <button
            onClick={() => api.exportCSV(30)}
            className="flex items-center gap-1 rounded-lg bg-amber-500/15 border border-amber-500/30 px-3 py-1.5 text-xs font-medium text-amber-400 hover:bg-amber-500/25 transition"
          >
            <Download size={13} /> Export CSV (30d)
          </button>
          <button
            onClick={refresh}
            className="flex items-center gap-1 rounded-lg bg-[var(--bg-card)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
          >
            <RefreshCw size={13} /> Refresh
          </button>
        </div>
      </div>

      {loading && !data && (
        <div className="flex h-40 items-center justify-center text-[var(--text-secondary)]">
          Loading timeline...
        </div>
      )}

      {error && (
        <div className="flex h-40 items-center justify-center text-red-400">
          <AlertTriangle className="mr-2" size={16} /> {error}
        </div>
      )}

      {entries.length > 0 && (
        <Card className="overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--border)] bg-[var(--bg-primary)]/50 text-left text-xs uppercase tracking-wider text-[var(--text-secondary)]">
                  <th className="px-4 py-3">Time</th>
                  <th className="px-4 py-3">Price</th>
                  <th className="px-4 py-3">Regime</th>
                  <th className="px-4 py-3">Gate</th>
                  <th className="px-4 py-3">Conf</th>
                  <th className="px-4 py-3">MGC Action</th>
                  <th className="px-4 py-3">MGC Reason</th>
                  <th className="px-4 py-3">GLD Action</th>
                  <th className="px-4 py-3">GLD Reason</th>
                  <th className="px-4 py-3">ATR</th>
                  <th className="px-4 py-3">Bucket</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e, i) => {
                  const hasBlocks = e.blocks && Object.values(e.blocks).some(Boolean);
                  return (
                    <tr
                      key={`${e.ts}-${i}`}
                      className={cn(
                        'border-b border-[var(--border)]/50 transition-colors hover:bg-[var(--bg-card-hover)]',
                        hasBlocks && 'bg-red-500/5'
                      )}
                    >
                      <td className="whitespace-nowrap px-4 py-2.5 font-mono text-xs">{tsToFull(e.ts)}</td>
                      <td className="px-4 py-2.5 font-mono">{formatUSD(e.price)}</td>
                      <td className="px-4 py-2.5">{e.regime}</td>
                      <td className="px-4 py-2.5 text-xs">{e.oracle_gate}</td>
                      <td className="px-4 py-2.5 font-mono">{(e.confidence * 100).toFixed(0)}%</td>
                      <td className="px-4 py-2.5"><ActionCell action={e.mgc_action} /></td>
                      <td className="px-4 py-2.5 text-xs text-[var(--text-secondary)]">{e.mgc_reason?.replace(/_/g, ' ')}</td>
                      <td className="px-4 py-2.5"><ActionCell action={e.gld_action} /></td>
                      <td className="px-4 py-2.5 text-xs text-[var(--text-secondary)]">{e.gld_reason?.replace(/_/g, ' ')}</td>
                      <td className="px-4 py-2.5 font-mono">{e.atr?.toFixed(2)}</td>
                      <td className="px-4 py-2.5">
                        <Badge className={cn('text-xs',
                          e.bucket === 'extreme' ? 'bg-red-500/15 border-red-500/30 text-red-400' :
                            e.bucket === 'high' ? 'bg-orange-500/15 border-orange-500/30 text-orange-400' :
                              e.bucket === 'mid' ? 'bg-yellow-500/15 border-yellow-500/30 text-yellow-400' :
                                'bg-green-500/15 border-green-500/30 text-green-400'
                        )}>
                          {e.bucket?.toUpperCase()}
                        </Badge>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
