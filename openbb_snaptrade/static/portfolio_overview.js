const API_BASE = (window.__SNAPTRADE_API_BASE__ || '').trim();

function asNumber(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function formatMoney(value, currency) {
  const amount = asNumber(value);
  if (amount === null) return 'N/A';
  const code = (currency || 'USD').toUpperCase();
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: code,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(amount);
  } catch (_e) {
    return amount.toFixed(2) + ' ' + code;
  }
}

function formatPct(value) {
  const number = asNumber(value);
  if (number === null) return 'N/A';
  return (number * 100).toFixed(1) + '%';
}

function formatAssetKind(value) {
  const text = String(value || 'unknown').trim();
  return text ? text.toUpperCase() : 'UNKNOWN';
}

async function readJsonSafe(response) {
  try {
    return await response.json();
  } catch (_e) {
    return null;
  }
}

function workspaceHeaders() {
  const headers = {};
  const match = String(window.location.pathname || '').match(/\/s\/([0-9a-f]+:[0-9]+:[0-9]+:[0-9a-f]+)(?:[\/?#]|$)/i);
  if (match) {
    headers['Authorization'] = 'Bearer ' + match[1];
  }
  return headers;
}

async function apiJson(path) {
  const url = API_BASE ? API_BASE + path : path;
  try {
    const response = await fetch(url, { headers: workspaceHeaders() });
    const data = await readJsonSafe(response);
    return { response, data };
  } catch (error) {
    throw new Error(error && error.message ? error.message : 'Network error');
  }
}

function sumMetric(rows, key) {
  let total = 0;
  let found = false;
  for (const row of rows) {
    const value = asNumber(row[key]);
    if (value === null) continue;
    total += value;
    found = true;
  }
  return found ? total : null;
}

function aggregatePortfolio(rows) {
  return {
    accounts: rows.length,
    totalValue: sumMetric(rows, 'total_value'),
    cash: sumMetric(rows, 'cash'),
    buyingPower: sumMetric(rows, 'buying_power'),
    marketValue: sumMetric(rows, 'market_value'),
    costBasis: sumMetric(rows, 'cost_basis'),
    openPnl: sumMetric(rows, 'open_pnl'),
    positions: rows.reduce((acc, row) => acc + (asNumber(row.positions_count) || 0), 0),
  };
}

function renderTotals(totals, currency) {
  const el = document.getElementById('totals');
  if (!el) return;
  const cards = [
    ['Accounts', String(totals.accounts)],
    ['Total Value', formatMoney(totals.totalValue, currency)],
    ['Cash', formatMoney(totals.cash, currency)],
    ['Buying Power', formatMoney(totals.buyingPower, currency)],
    ['Market Value', formatMoney(totals.marketValue, currency)],
    ['Cost Basis', formatMoney(totals.costBasis, currency)],
    ['Open P/L', formatMoney(totals.openPnl, currency)],
    ['Positions', String(totals.positions)],
  ];

  el.innerHTML = '';
  for (const [label, value] of cards) {
    const card = document.createElement('div');
    card.className = 'kpi';
    const l = document.createElement('div');
    l.className = 'kpi-label';
    l.textContent = label;
    const v = document.createElement('div');
    v.className = 'kpi-value';
    v.textContent = value;
    card.appendChild(l);
    card.appendChild(v);
    el.appendChild(card);
  }
}

function renderAccounts(accounts, summaries) {
  const el = document.getElementById('accounts');
  if (!el) return;

  const summariesByAccount = {};
  for (const row of summaries) {
    const id = String(row.account_id || '');
    if (id) summariesByAccount[id] = row;
  }

  if (!accounts.length) {
    el.innerHTML = '<div class="empty">No accounts connected.</div>';
    return;
  }

  const table = document.createElement('table');
  table.className = 'table';
  table.innerHTML = '<thead><tr><th>Account</th><th>Type</th><th>Total</th><th>Cash</th><th>Buying Power</th><th>Market Value</th><th>Cost Basis</th><th>Open P/L</th><th>Positions</th></tr></thead>';

  const body = document.createElement('tbody');
  for (const account of accounts) {
    const accountId = String(account.id || '');
    const summary = summariesByAccount[accountId] || {};
    const currency = summary.currency || (((account.balance || {}).total || {}).currency) || 'USD';
    const tr = document.createElement('tr');

    const name = account.name || account.display_name || 'Unknown Account';
    const type = (account.meta && account.meta.type) || account.type || account.account_type || 'Unknown';

    const cells = [
      name,
      type,
      formatMoney(summary.total_value, currency),
      formatMoney(summary.cash, currency),
      formatMoney(summary.buying_power, currency),
      formatMoney(summary.market_value, currency),
      formatMoney(summary.cost_basis, currency),
      formatMoney(summary.open_pnl, currency),
      String(asNumber(summary.positions_count) || 0),
    ];

    for (const value of cells) {
      const td = document.createElement('td');
      td.textContent = value;
      tr.appendChild(td);
    }

    body.appendChild(tr);
  }

  table.appendChild(body);
  el.innerHTML = '';
  el.appendChild(table);
}

function renderExposure(data) {
  const el = document.getElementById('exposure');
  if (!el) return;

  const rows = data && Array.isArray(data.exposures_by_kind) ? data.exposures_by_kind : [];
  const totals = data && data.totals ? data.totals : {};
  const gross = asNumber(totals.gross_exposure);
  const net = asNumber(totals.net_market_value);

  if (!rows.length) {
    el.innerHTML = '<div class="empty">No exposure data available.</div>';
    return;
  }

  const list = document.createElement('div');
  list.className = 'exposure-list';

  const meta = document.createElement('div');
  meta.className = 'exposure-meta';
  meta.textContent = 'Gross: ' + formatMoney(gross, 'USD') + '  |  Net: ' + formatMoney(net, 'USD');
  list.appendChild(meta);

  for (const row of rows) {
    const item = document.createElement('div');
    item.className = 'exposure-item';

    const label = document.createElement('div');
    label.className = 'exposure-label';
    label.textContent = formatAssetKind(row.asset_kind);

    const value = document.createElement('div');
    value.className = 'exposure-value';
    value.textContent = formatMoney(row.market_value, 'USD') + ' (' + formatPct(row.weight) + ')';

    const barWrap = document.createElement('div');
    barWrap.className = 'bar-wrap';
    const bar = document.createElement('div');
    const weight = asNumber(row.weight) || 0;
    bar.className = 'bar ' + (weight < 0 ? 'bar-negative' : 'bar-positive');
    const w = Math.min(100, Math.abs((asNumber(row.weight) || 0) * 100));
    bar.style.width = w + '%';
    barWrap.appendChild(bar);

    item.appendChild(label);
    item.appendChild(value);
    item.appendChild(barWrap);
    list.appendChild(item);
  }

  el.innerHTML = '';
  el.appendChild(list);
}

function renderTopPositions(data) {
  const el = document.getElementById('top-positions');
  if (!el) return;

  const rows = data && Array.isArray(data.top_positions) ? data.top_positions : [];
  if (!rows.length) {
    el.innerHTML = '<div class="empty">No positions available.</div>';
    return;
  }

  const table = document.createElement('table');
  table.className = 'table';
  table.innerHTML = '<thead><tr><th>Symbol</th><th>Account</th><th>Kind</th><th>Units</th><th>Price</th><th>Market Value</th><th>Cost Basis</th><th>Open P/L</th></tr></thead>';

  const body = document.createElement('tbody');
  for (const row of rows) {
    const cells = [
      row.symbol || 'Unknown',
      row.account_name || 'Unknown Account',
      formatAssetKind(row.asset_kind),
      String(asNumber(row.units) || 0),
      formatMoney(row.price, row.currency || 'USD'),
      formatMoney(row.market_value, row.currency || 'USD'),
      formatMoney(row.cost_basis_value, row.currency || 'USD'),
      formatMoney(row.open_pnl, row.currency || 'USD'),
    ];
    const tr = document.createElement('tr');
    for (const value of cells) {
      const td = document.createElement('td');
      td.textContent = value;
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }

  table.appendChild(body);
  el.innerHTML = '';
  el.appendChild(table);
}

function setStatus(text, isError) {
  const el = document.getElementById('status');
  if (!el) return;
  el.textContent = text;
  el.className = isError ? 'status status-error' : 'status';
}

async function loadPortfolioOverview() {
  setStatus('', false);
  try {
    const [{ response: acctResp, data: accounts }, { response: sumResp, data: summaries }, { response: expResp, data: exposure }] = await Promise.all([
      apiJson('/snaptrade/accounts'),
      apiJson('/snaptrade/account-summaries'),
      apiJson('/snaptrade/portfolio-exposure'),
    ]);

    if (!acctResp.ok || !Array.isArray(accounts)) {
      throw new Error((accounts && (accounts.detail || accounts.error)) || 'Failed to load accounts');
    }
    if (!sumResp.ok || !Array.isArray(summaries)) {
      throw new Error((summaries && (summaries.detail || summaries.error)) || 'Failed to load account summaries');
    }
    if (!expResp.ok || !exposure || typeof exposure !== 'object') {
      throw new Error((exposure && (exposure.detail || exposure.error)) || 'Failed to load exposure data');
    }

    const currency = summaries[0] && summaries[0].currency ? summaries[0].currency : 'USD';
    renderTotals(aggregatePortfolio(summaries), currency);
    renderAccounts(accounts, summaries);
    renderExposure(exposure);
    renderTopPositions(exposure);
    publishPortfolioData(accounts, summaries, exposure);
    setStatus('', false);
  } catch (error) {
    setStatus(error && error.message ? error.message : 'Failed to load portfolio stats.', true);
    renderTotals(aggregatePortfolio([]), 'USD');
    renderAccounts([], []);
    renderExposure({ exposures_by_kind: [], totals: {} });
    renderTopPositions({ top_positions: [] });
    publishPortfolioData([], [], { exposures_by_kind: [], top_positions: [], totals: {} });
  }
}

function publishPortfolioData(accounts, summaries, exposure) {
  if (!window.OpenBBIframe) return;
  const summariesByAccount = {};
  for (const row of summaries || []) {
    const id = String(row.account_id || '');
    if (id) summariesByAccount[id] = row;
  }
  const accountRows = (accounts || []).map((account) => {
    const accountId = String(account.id || '');
    const summary = summariesByAccount[accountId] || {};
    const currency = summary.currency || (((account.balance || {}).total || {}).currency) || 'USD';
    return {
      account_id: accountId,
      account_name: account.name || account.display_name || '',
      account_type: (account.meta && account.meta.type) || account.type || account.account_type || '',
      currency: currency,
      total_value: asNumber(summary.total_value),
      cash: asNumber(summary.cash),
      buying_power: asNumber(summary.buying_power),
      market_value: asNumber(summary.market_value),
      cost_basis: asNumber(summary.cost_basis),
      open_pnl: asNumber(summary.open_pnl),
      positions_count: asNumber(summary.positions_count) || 0,
    };
  });
  window.OpenBBIframe.publish('snaptrade-portfolio-accounts', accountRows, 'table');
  window.OpenBBIframe.publish('snaptrade-portfolio-summaries', summaries || [], 'table');
  window.OpenBBIframe.publish(
    'snaptrade-portfolio-exposure',
    (exposure && Array.isArray(exposure.exposures_by_kind)) ? exposure.exposures_by_kind : [],
    'table',
  );
  window.OpenBBIframe.publish(
    'snaptrade-portfolio-top-positions',
    (exposure && Array.isArray(exposure.top_positions)) ? exposure.top_positions : [],
    'table',
  );
}

function bootPortfolioOverview() {
  if (window.OpenBBIframe) {
    window.OpenBBIframe.declare({
      widgets: [
        {
          widgetId: 'snaptrade-portfolio-accounts',
          name: 'Connected Accounts',
          description: 'Per-account totals (cash, buying power, market value, cost basis, open P/L).',
          category: 'Brokerage',
          dataType: 'table',
        },
        {
          widgetId: 'snaptrade-portfolio-summaries',
          name: 'Account Summaries',
          description: 'Raw per-account financial summary rows.',
          category: 'Brokerage',
          dataType: 'table',
        },
        {
          widgetId: 'snaptrade-portfolio-exposure',
          name: 'Asset Class Exposure',
          description: 'Aggregate market value and weight by asset class.',
          category: 'Brokerage',
          dataType: 'table',
        },
        {
          widgetId: 'snaptrade-portfolio-top-positions',
          name: 'Top Positions',
          description: 'Largest positions across all connected accounts.',
          category: 'Brokerage',
          dataType: 'table',
        },
      ],
      params: [],
    });
  }
  const refreshBtn = document.getElementById('refresh');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => {
      void loadPortfolioOverview();
    });
  }
  void loadPortfolioOverview();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootPortfolioOverview, { once: true });
} else {
  bootPortfolioOverview();
}
