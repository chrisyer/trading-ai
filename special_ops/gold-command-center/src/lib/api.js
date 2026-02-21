const BASE = '/gold';

async function fetchJSON(path) {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const api = {
  getState: () => fetchJSON('/state'),
  getTimeline: (limit = 500) => fetchJSON(`/timeline?limit=${limit}`),
  getPositions: () => fetchJSON('/positions'),
  getSettings: () => fetchJSON('/settings'),
  updatePositions: (data) =>
    fetch(`${BASE}/positions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }).then((r) => r.json()),
  exportCSV: (days = 30) => {
    const a = document.createElement('a');
    a.href = `${BASE}/export?days=${days}`;
    a.download = `gold_signals_${days}d.csv`;
    a.click();
  },
};
