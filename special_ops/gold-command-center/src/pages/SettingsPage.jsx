import { api } from '../lib/api';
import { usePolling } from '../hooks/usePolling';
import { Card, CardHeader, CardTitle } from '../components/Card';
import { Badge } from '../components/Badge';
import { Settings, AlertTriangle } from 'lucide-react';

function SettingRow({ label, value, type }) {
  let display;
  if (typeof value === 'boolean') {
    display = value ? (
      <Badge className="bg-green-500/15 border-green-500/30 text-green-400">true</Badge>
    ) : (
      <Badge className="bg-gray-500/15 border-gray-500/30 text-gray-400">false</Badge>
    );
  } else if (Array.isArray(value)) {
    display = <span className="font-mono">[{value.join(', ')}]</span>;
  } else if (typeof value === 'object' && value !== null) {
    display = <span className="font-mono text-xs">{JSON.stringify(value)}</span>;
  } else {
    display = <span className="font-mono">{String(value)}</span>;
  }

  return (
    <div className="flex items-center justify-between border-b border-[var(--border)]/50 py-2.5 last:border-0">
      <span className="text-sm text-[var(--text-secondary)]">{label}</span>
      {display}
    </div>
  );
}

function SettingsSection({ title, data }) {
  if (!data || typeof data !== 'object') return null;
  return (
    <Card>
      <CardHeader><CardTitle>{title}</CardTitle></CardHeader>
      <div>
        {Object.entries(data).map(([k, v]) => (
          <SettingRow key={k} label={k.replace(/_/g, ' ')} value={v} />
        ))}
      </div>
    </Card>
  );
}

export default function SettingsPage() {
  const { data, loading, error } = usePolling(api.getSettings, 30000);

  if (loading && !data) {
    return <div className="flex h-40 items-center justify-center text-[var(--text-secondary)]">Loading settings...</div>;
  }
  if (error) {
    return <div className="flex h-40 items-center justify-center text-red-400"><AlertTriangle className="mr-2" size={16} />{error}</div>;
  }

  const {
    atr_buckets, circuit_breaker, safety, hysteresis,
    mgc_confidence_threshold, gld_confidence_threshold,
    gld_allowed_buckets, evaluation_cadence_seconds,
    publish_cadence_seconds, dca_step_multiplier_range,
    ...rest
  } = data || {};

  return (
    <div className="space-y-4">
      <h2 className="flex items-center gap-2 text-lg font-bold">
        <Settings size={18} className="text-amber-400" />
        System Settings
        <span className="text-sm font-normal text-[var(--text-secondary)]">(read-only from CRELLA)</span>
      </h2>

      {/* Top-level thresholds */}
      <Card>
        <CardHeader><CardTitle>Thresholds</CardTitle></CardHeader>
        <div>
          <SettingRow label="MGC Confidence Threshold" value={mgc_confidence_threshold} />
          <SettingRow label="GLD Confidence Threshold" value={gld_confidence_threshold} />
          <SettingRow label="GLD Allowed Buckets" value={gld_allowed_buckets} />
          <SettingRow label="DCA Step Multiplier Range" value={dca_step_multiplier_range} />
          <SettingRow label="Evaluation Cadence" value={`${evaluation_cadence_seconds}s`} />
          <SettingRow label="Publish Cadence" value={`${publish_cadence_seconds}s`} />
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <SettingsSection title="ATR Buckets" data={atr_buckets} />
        <SettingsSection title="Circuit Breaker" data={circuit_breaker} />
        <SettingsSection title="Safety Rules" data={safety} />
        <SettingsSection title="Hysteresis" data={hysteresis} />
      </div>

      {Object.keys(rest).length > 0 && (
        <SettingsSection title="Other" data={rest} />
      )}
    </div>
  );
}
