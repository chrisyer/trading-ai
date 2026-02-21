import { useState } from 'react';
import { api } from '../lib/api';
import { usePolling } from '../hooks/usePolling';
import { Card, CardHeader, CardTitle } from '../components/Card';
import { formatUSD, cn } from '../lib/utils';
import { Wallet, AlertTriangle, Send, RefreshCw } from 'lucide-react';

function StatBox({ label, value, sub, accent }) {
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-primary)] p-4">
      <div className="text-xs uppercase tracking-wider text-[var(--text-secondary)]">{label}</div>
      <div className={cn('mt-1 text-xl font-bold font-mono', accent)}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-[var(--text-secondary)]">{sub}</div>}
    </div>
  );
}

function InputField({ label, value, onChange, type = 'number', step = '1' }) {
  return (
    <div>
      <label className="mb-1 block text-xs text-[var(--text-secondary)]">{label}</label>
      <input
        type={type}
        step={step}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-primary)] px-3 py-2 text-sm font-mono text-[var(--text-primary)] outline-none focus:border-amber-500/50"
      />
    </div>
  );
}

export default function Positions() {
  const { data, loading, error, refresh } = usePolling(api.getPositions, 5000);
  const [submitting, setSubmitting] = useState(false);
  const [submitMsg, setSubmitMsg] = useState('');

  const [mgcContracts, setMgcContracts] = useState('');
  const [mgcAvg, setMgcAvg] = useState('');
  const [mgcUpl, setMgcUpl] = useState('');
  const [gldContracts, setGldContracts] = useState('');
  const [gldDelta, setGldDelta] = useState('');
  const [gldPremium, setGldPremium] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setSubmitMsg('');
    try {
      const payload = {};
      if (mgcContracts || mgcAvg || mgcUpl) {
        payload.mgc = {};
        if (mgcContracts) payload.mgc.contracts = parseInt(mgcContracts);
        if (mgcAvg) payload.mgc.avg = parseFloat(mgcAvg);
        if (mgcUpl) payload.mgc.upl = parseFloat(mgcUpl);
      }
      if (gldContracts || gldDelta || gldPremium) {
        payload.gld = {};
        if (gldContracts) payload.gld.contracts = parseInt(gldContracts);
        if (gldDelta) payload.gld.delta_oz_equiv = parseFloat(gldDelta);
        if (gldPremium) payload.gld.premium_at_risk = parseFloat(gldPremium);
      }
      await api.updatePositions(payload);
      setSubmitMsg('Positions updated');
      setMgcContracts(''); setMgcAvg(''); setMgcUpl('');
      setGldContracts(''); setGldDelta(''); setGldPremium('');
      refresh();
    } catch (err) {
      setSubmitMsg(`Error: ${err.message}`);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading && !data) {
    return <div className="flex h-40 items-center justify-center text-[var(--text-secondary)]">Loading...</div>;
  }
  if (error && !data) {
    return <div className="flex h-40 items-center justify-center text-red-400"><AlertTriangle className="mr-2" size={16} />{error}</div>;
  }

  const { mgc, gld, exposure } = data || {};

  return (
    <div className="space-y-4">
      <h2 className="flex items-center gap-2 text-lg font-bold">
        <Wallet size={18} className="text-amber-400" />
        Positions & Risk
      </h2>

      {/* Exposure overview */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <StatBox label="MGC oz-equiv" value={exposure?.mgc_oz_equivalent ?? 0} sub={`${mgc?.contracts ?? 0} contracts × 10 oz`} />
        <StatBox label="GLD oz-equiv" value={(exposure?.gld_oz_equivalent ?? 0).toFixed(1)} sub={`${gld?.contracts ?? 0} contracts`} />
        <StatBox label="XAUUSD oz-equiv" value={(exposure?.xauusd_oz_equivalent ?? 0).toFixed(1)} sub={`${exposure?.xauusd_lots ?? 0} lots × 100 oz`} />
        <StatBox
          label="Total oz Exposure"
          value={(exposure?.total_oz_equivalent ?? 0).toFixed(1)}
          accent="text-amber-400"
        />
        <StatBox label="Premium at Risk" value={formatUSD(gld?.premium_at_risk ?? 0, 0)} />
      </div>

      {/* Position detail cards */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>MGC (Micro Gold Futures)</CardTitle></CardHeader>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-[var(--text-secondary)]">Contracts</span>
              <span className="font-mono font-semibold">{mgc?.contracts ?? 0}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--text-secondary)]">Avg Price</span>
              <span className="font-mono">{formatUSD(mgc?.avg)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--text-secondary)]">Unrealized P&L</span>
              <span className={cn('font-mono font-semibold', (mgc?.upl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400')}>
                {formatUSD(mgc?.upl, 0)}
              </span>
            </div>
          </div>
        </Card>

        <Card>
          <CardHeader><CardTitle>GLD (ETF Options)</CardTitle></CardHeader>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-[var(--text-secondary)]">Contracts</span>
              <span className="font-mono font-semibold">{gld?.contracts ?? 0}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--text-secondary)]">Delta oz-equiv</span>
              <span className="font-mono">{(gld?.delta_oz_equiv ?? 0).toFixed(1)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--text-secondary)]">Premium at Risk</span>
              <span className="font-mono">{formatUSD(gld?.premium_at_risk ?? 0, 0)}</span>
            </div>
          </div>
        </Card>
      </div>

      {/* Manual position update form */}
      <Card>
        <CardHeader>
          <CardTitle>Update Positions Manually</CardTitle>
          <button onClick={refresh} className="flex items-center gap-1 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
            <RefreshCw size={12} /> Refresh
          </button>
        </CardHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-3">
              <h4 className="text-xs font-semibold uppercase text-[var(--text-secondary)]">MGC</h4>
              <InputField label="Contracts" value={mgcContracts} onChange={setMgcContracts} />
              <InputField label="Avg Price" value={mgcAvg} onChange={setMgcAvg} step="0.01" />
              <InputField label="Unrealized P&L" value={mgcUpl} onChange={setMgcUpl} step="0.01" />
            </div>
            <div className="space-y-3">
              <h4 className="text-xs font-semibold uppercase text-[var(--text-secondary)]">GLD</h4>
              <InputField label="Contracts" value={gldContracts} onChange={setGldContracts} />
              <InputField label="Delta oz-equiv" value={gldDelta} onChange={setGldDelta} step="0.1" />
              <InputField label="Premium at Risk" value={gldPremium} onChange={setGldPremium} step="0.01" />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={submitting}
              className="flex items-center gap-2 rounded-lg bg-amber-500 px-4 py-2 text-sm font-semibold text-black transition hover:bg-amber-400 disabled:opacity-50"
            >
              <Send size={14} /> {submitting ? 'Sending...' : 'Update Positions'}
            </button>
            {submitMsg && (
              <span className={cn('text-sm', submitMsg.startsWith('Error') ? 'text-red-400' : 'text-green-400')}>
                {submitMsg}
              </span>
            )}
          </div>
        </form>
      </Card>
    </div>
  );
}
