const API_BASE = (window.__SNAPTRADE_API_BASE__ || '').trim();
let CURRENT_CONTEXT = null;
let EMPTY_PORTAL_BOOTSTRAPPED = false;

// Request caching
const _requestCache = new Map();
const _inFlightRequests = new Map();
const _CACHE_TTL_MS = 300000; // 5 minutes

function _getCacheKey(path, options) {
  return path + '::' + JSON.stringify(options || {});
}

function _isCacheValid(cacheEntry) {
  if (!cacheEntry) return false;
  const age = Date.now() - cacheEntry.ts;
  return age < _CACHE_TTL_MS;
}

function _shouldSkipCache() {
  const params = new URLSearchParams(window.location.search);
  return params.has('skip-cache') || params.get('skip-cache') === '1';
}

// Capture console logs for debugging
window.__portalLogs__ = [];
const originalLog = console.log;
const originalError = console.error;
const originalWarn = console.warn;
console.log = function(...args) {
  window.__portalLogs__.push({ level: 'log', msg: args.join(' '), ts: Date.now() });
  originalLog.apply(console, args);
};
console.error = function(...args) {
  window.__portalLogs__.push({ level: 'error', msg: args.join(' '), ts: Date.now() });
  originalError.apply(console, args);
};
console.warn = function(...args) {
  window.__portalLogs__.push({ level: 'warn', msg: args.join(' '), ts: Date.now() });
  originalWarn.apply(console, args);
};

function fitPortalViewport() {
  const host = document.getElementById('portal-host');
  const frame = document.getElementById('portal-frame');
  if (!host || !frame || host.hidden) return;

  frame.style.width = '100%';
  frame.style.height = '100%';
  frame.style.transformOrigin = '';
  frame.style.marginLeft = '0';
  frame.style.marginTop = '0';
  frame.style.transform = 'none';
}

function showScreen(name) {
  document.querySelectorAll('.screen').forEach((s) => s.classList.remove('active'));
  const el = document.getElementById('screen-' + name);
  if (el) el.classList.add('active');
}

function loading(msg) {
  document.getElementById('loading-msg').textContent = msg || 'Loading...';
  showScreen('loading');
}

function showError(msg) {
  const el = document.getElementById('login-error');
  el.textContent = msg;
  el.style.display = 'block';
  showScreen('login');
}

function clearError() {
  const el = document.getElementById('login-error');
  if (!el) return;
  el.textContent = '';
  el.style.display = 'none';
}

function setPortalResult(kind, message) {
  const el = document.getElementById('portal-result');
  if (!el) return;
  el.className = 'portal-result';
  if (!message) {
    el.textContent = '';
    return;
  }
  el.textContent = message;
  if (kind === 'success') el.classList.add('portal-result-success');
  if (kind === 'error') el.classList.add('portal-result-error');
}

function pickFirstString(values, fallback) {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }
  return fallback;
}

function connectionBrokerLabel(connection) {
  const brokerage = connection && typeof connection.brokerage === 'object' ? connection.brokerage : null;
  return pickFirstString(
    [
      connection.brokerage_display_name,
      connection.brokerage_name,
      brokerage && brokerage.display_name,
      brokerage && brokerage.name,
      brokerage && brokerage.slug,
      connection.broker_name,
      connection.institution_name,
      connection.broker,
    ],
    'Unknown broker'
  );
}

function connectionTypeLabel(connection) {
  return pickFirstString(
    [
      connection.account_type,
      connection.connection_type,
      connection.type,
      connection.authorization_type,
      connection.mode,
    ],
    'Type unavailable'
  );
}

function asNumber(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function moneyFromValue(value) {
  if (value && typeof value === 'object') {
    const amount = asNumber(value.amount ?? value.value ?? value.total ?? value.balance);
    if (amount === null) return null;
    return {
      amount,
      currency: pickFirstString([value.currency, value.currency_code, value.iso_currency_code], 'USD'),
    };
  }

  const directAmount = asNumber(value);
  if (directAmount === null) return null;
  return { amount: directAmount, currency: 'USD' };
}

function pickBalanceMetric(account, keys) {
  const balance = account && typeof account.balance === 'object' ? account.balance : null;
  if (!balance) return null;

  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(balance, key)) {
      const metric = moneyFromValue(balance[key]);
      if (metric) return metric;
    }
  }

  return null;
}

function accountSummaryMetrics(account) {
  const total = pickBalanceMetric(account, ['total', 'total_value', 'portfolio_value', 'net_worth', 'balance']);
  const cash = pickBalanceMetric(account, ['cash', 'cash_balance', 'available_cash']);
  const buyingPower = pickBalanceMetric(account, ['buying_power', 'buyingPower']);

  return { total, cash, buyingPower };
}

function formatMoney(money) {
  if (!money || typeof money.amount !== 'number') return 'N/A';
  const code = pickFirstString([money.currency], 'USD').toUpperCase();
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: code,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(money.amount);
  } catch (_e) {
    return money.amount.toFixed(2) + ' ' + code;
  }
}

function metricToMoney(value, currency) {
  const amount = asNumber(value);
  if (amount === null) return null;
  return { amount, currency: pickFirstString([currency], 'USD') };
}

function applyPortalStatusParams(params) {
  const status = params.get('status');
  if (!status) return false;

  if (status === 'SUCCESS') {
    setPortalResult('success', 'Connection successful');
  } else if (status === 'ERROR') {
    const code = params.get('error_code') || 'unknown_error';
    const httpCode = params.get('status_code');
    const detail = httpCode ? code + ' (status: ' + httpCode + ')' : code;
    setPortalResult('error', 'Connection failed: ' + detail);
  } else if (status === 'ABANDONED') {
    setPortalResult('error', 'Connection flow abandoned');
  }

  return true;
}

async function handlePortalFrameLoad() {
  console.log('[PORTAL] Frame load event fired');
  if (!document.body.classList.contains('portal-open')) {
    console.log('[PORTAL] Portal not open, ignoring');
    return;
  }

  const frame = document.getElementById('portal-frame');
  if (!frame || !frame.contentWindow) {
    console.log('[PORTAL] No frame or contentWindow');
    return;
  }

  try {
    const url = new URL(frame.contentWindow.location.href);
    console.log('[PORTAL] Frame URL (same-origin):', url.href);
    if (url.origin !== window.location.origin) {
      console.log('[PORTAL] Cross-origin frame, skipping same-origin check');
      return;
    }

    const normalizedPath = url.pathname.replace(/\/+$/, '') || '/';
    if (normalizedPath !== '/widget' && normalizedPath !== '/') {
      console.log('[PORTAL] Path mismatch:', normalizedPath);
      return;
    }

    const hasStatus = applyPortalStatusParams(url.searchParams);
    if (!hasStatus) {
      console.log('[PORTAL] No status params');
      return;
    }

    console.log('[PORTAL] Status found, closing');
    hidePortalInWidget();
    await checkStatus();
  } catch (_e) {
    console.log('[PORTAL] Error reading iframe URL (expected for cross-origin):', _e.message);
    return;
  }
}

let _portalExitCheckInterval = null;
function startPortalExitMonitor() {
  if (_portalExitCheckInterval) return;
  console.log('[PORTAL] Starting exit monitor');
  _portalExitCheckInterval = setInterval(() => {
    const frame = document.getElementById('portal-frame');
    if (!frame || !document.body.classList.contains('portal-open')) {
      if (_portalExitCheckInterval) {
        clearInterval(_portalExitCheckInterval);
        _portalExitCheckInterval = null;
      }
      return;
    }
    try {
      const url = new URL(frame.contentWindow.location.href);
      console.log('[PORTAL] iframe URL:', url.href);
      if (url.hostname === window.location.hostname && url.pathname.match(/\/(widget)?$/)) {
        const status = url.searchParams.get('status');
        console.log('[PORTAL] iframe status param:', status);
        if (status === 'ABANDONED' || status === 'SUCCESS' || status === 'ERROR') {
          console.log('[PORTAL] Status detected, closing portal');
          applyPortalStatusParams(url.searchParams);
          hidePortalInWidget();
          void checkStatus();
          if (_portalExitCheckInterval) {
            clearInterval(_portalExitCheckInterval);
            _portalExitCheckInterval = null;
          }
        }
      }
    } catch (_e) {
      console.log('[PORTAL] Error reading iframe URL:', _e.message);
    }
  }, 500);
}

function stopPortalExitMonitor() {
  if (_portalExitCheckInterval) {
    clearInterval(_portalExitCheckInterval);
    _portalExitCheckInterval = null;
  }
}

function openPortalInWidget(redirectUri) {
  console.log('[PORTAL] openPortalInWidget called with:', redirectUri.substring(0, 100));
  const host = document.getElementById('portal-host');
  const frame = document.getElementById('portal-frame');
  if (!host || !frame) {
    console.log('[PORTAL] No portal elements, cannot open embedded portal');
    setPortalResult('error', 'Portal container is missing.');
    return;
  }
  showScreen('connected');
  document.body.classList.add('portal-open');
  setPortalResult('', '');
  frame.src = redirectUri;
  host.hidden = false;

  requestAnimationFrame(fitPortalViewport);
  setTimeout(fitPortalViewport, 100);
  startPortalExitMonitor();
  console.log('[PORTAL] Portal opened');
}

function hidePortalInWidget() {
  const host = document.getElementById('portal-host');
  const frame = document.getElementById('portal-frame');
  if (!host || !frame) return;
  document.body.classList.remove('portal-open');
  frame.removeAttribute('src');
  frame.style.width = '';
  frame.style.height = '';
  frame.style.marginLeft = '';
  frame.style.marginTop = '';
  frame.style.transformOrigin = '';
  frame.style.transform = '';
  host.hidden = true;
  stopPortalExitMonitor();
}

function setConnectionsMode(hasConnections) {
  const connectionsSection = document.getElementById('connections-section');
  const addButton = document.getElementById('btn-add-connection');
  if (connectionsSection) {
    connectionsSection.style.display = hasConnections ? 'block' : 'none';
  }
  if (addButton) {
    addButton.style.display = hasConnections ? 'block' : 'none';
  }
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

async function apiJson(path, options) {
  const url = API_BASE ? API_BASE + path : path;
  const cacheKey = _getCacheKey(path, options);
  const skipCache = _shouldSkipCache();
  
  // Check if request is already in flight
  if (_inFlightRequests.has(cacheKey)) {
    return _inFlightRequests.get(cacheKey);
  }
  
  // Check if cached response is still valid
  if (!skipCache && _requestCache.has(cacheKey)) {
    const cacheEntry = _requestCache.get(cacheKey);
    if (_isCacheValid(cacheEntry)) {
      return { response: cacheEntry.response, data: cacheEntry.data };
    }
  }
  
  // Create fetch promise
  const fetchPromise = (async () => {
    const mergedHeaders = Object.assign({}, workspaceHeaders(), (options && options.headers) || {});
    const fetchOptions = Object.assign({}, options || {});
    if (Object.keys(mergedHeaders).length > 0) {
      fetchOptions.headers = mergedHeaders;
    }
    try {
      const response = await fetch(url, fetchOptions);
      const data = await readJsonSafe(response);
      
      // Cache successful responses
      if (response.ok) {
        _requestCache.set(cacheKey, {
          response: { ok: true, status: response.status },
          data: data,
          ts: Date.now()
        });
      }
      
      return { response, data };
    } catch (error) {
      const detail = error && error.message ? error.message : 'Network error';
      throw new Error('Unable to reach SnapTrade service (' + detail + ').');
    } finally {
      _inFlightRequests.delete(cacheKey);
    }
  })();
  
  // Track in-flight request
  _inFlightRequests.set(cacheKey, fetchPromise);
  
  return fetchPromise;
}

function requireContextOrThrow() {
  if (!CURRENT_CONTEXT) {
    throw new Error('Workspace headers are required before starting Connection Portal.');
  }
}

async function requestPortalLink(payload) {
  const { response, data } = await apiJson('/snaptrade/portal', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
  if (response.ok && data && data.redirect_uri) {
    return data.redirect_uri;
  }
  throw new Error(readErrorMessage(data, 'Could not open portal'));
}

function readErrorMessage(data, fallback) {
  if (data && data.detail) return data.detail;
  if (data && data.error) return data.error;
  return fallback;
}

async function loadConnections() {
  if (!CURRENT_CONTEXT) {
    renderConnections([]);
    publishConnectionsData([], [], []);
    return;
  }

  try {
    const { response, data } = await apiJson('/snaptrade/connections');
    if (!response.ok || !Array.isArray(data)) {
      renderConnections([]);
      publishConnectionsData([], [], []);
      return;
    }

    let accounts = [];
    let summaries = [];
    try {
      const { response: acctResp, data: acctData } = await apiJson('/snaptrade/accounts');
      if (acctResp.ok && Array.isArray(acctData)) {
        accounts = acctData;
      }
      const { response: summaryResp, data: summaryData } = await apiJson('/snaptrade/account-summaries');
      if (summaryResp.ok && Array.isArray(summaryData)) {
        summaries = summaryData;
      }
    } catch (_e) {
      accounts = [];
      summaries = [];
    }

    renderConnections(data, accounts, summaries);
    publishConnectionsData(data, accounts, summaries);
  } catch (_e) {
    renderConnections([]);
    publishConnectionsData([], [], []);
  }
}

function publishConnectionsData(connections, accounts, summaries) {
  if (!window.OpenBBIframe) return;
  window.OpenBBIframe.publish('snaptrade-connections', connections || [], 'table');
  window.OpenBBIframe.publish('snaptrade-connection-accounts', accounts || [], 'table');
  window.OpenBBIframe.publish('snaptrade-connection-account-summaries', summaries || [], 'table');
}

function renderConnections(connections, accounts, summaries) {
  accounts = accounts || [];
  summaries = summaries || [];

  const list = document.getElementById('connections-list');
  if (!list) return;

  if (!connections || connections.length === 0) {
    setConnectionsMode(false);
    list.innerHTML = '';
    if (!EMPTY_PORTAL_BOOTSTRAPPED && CURRENT_CONTEXT) {
      EMPTY_PORTAL_BOOTSTRAPPED = true;
      requestPortalLink({})
        .then((redirectUri) => {
          openPortalInWidget(redirectUri);
          setPortalResult('', '');
        })
        .catch((error) => {
          setPortalResult('error', error.message || 'Could not open portal');
        });
    }
    return;
  }

  const accountsByConnection = {};
  for (const account of accounts) {
    const connId = String(account.brokerage_authorization || account.connection_id || account.connectionId || '');
    if (!connId) continue;
    if (!accountsByConnection[connId]) {
      accountsByConnection[connId] = [];
    }
    accountsByConnection[connId].push(account);
  }

  const summariesByAccount = {};
  for (const summary of summaries) {
    if (!summary || typeof summary !== 'object') continue;
    const accountId = String(summary.account_id || '');
    if (!accountId) continue;
    summariesByAccount[accountId] = summary;
  }

  EMPTY_PORTAL_BOOTSTRAPPED = false;
  setConnectionsMode(true);
  if (!document.body.classList.contains('portal-open')) {
    hidePortalInWidget();
  }
  list.innerHTML = '';

  for (const connection of connections) {
    const card = document.createElement('div');
    card.className = 'connection-card';

    const info = document.createElement('div');
    info.className = 'connection-card-info';

    const name = document.createElement('div');
    name.className = 'connection-name';
    name.textContent = connection.display_name || connection.name || 'Unknown';

    const meta = document.createElement('div');
    meta.className = 'connection-meta';
    const status = pickFirstString([connection.status], 'connected');
    const broker = connectionBrokerLabel(connection);
    const accountType = connectionTypeLabel(connection);
    meta.textContent = status + ' • ' + broker + ' • ' + accountType;

    info.appendChild(name);
    info.appendChild(meta);

    const connectionId = String(connection.id || '');
    const connAccounts = accountsByConnection[connectionId] || [];
    if (connAccounts.length > 0) {
      const summary = document.createElement('div');
      summary.className = 'connection-summary';

      const summaryTitle = document.createElement('div');
      summaryTitle.className = 'summary-title';
      summaryTitle.textContent = 'Account Summary';
      summary.appendChild(summaryTitle);

      let totalAmount = 0;
      let totalCurrency = 'USD';
      let hasAnyTotal = false;

      for (const account of connAccounts) {
        const accountId = String(account.id || '');
        const summaryData = summariesByAccount[accountId] || {};
        const fallbackMetrics = accountSummaryMetrics(account);
        const totalMetric = metricToMoney(summaryData.total_value, summaryData.currency) || fallbackMetrics.total;
        if (totalMetric) {
          totalAmount += totalMetric.amount;
          totalCurrency = totalMetric.currency || totalCurrency;
          hasAnyTotal = true;
        }
      }

      const overview = document.createElement('div');
      overview.className = 'summary-overview';

      const countBadge = document.createElement('div');
      countBadge.className = 'summary-badge';
      countBadge.textContent = 'Accounts: ' + connAccounts.length;
      overview.appendChild(countBadge);

      const totalBadge = document.createElement('div');
      totalBadge.className = 'summary-badge summary-badge-value';
      totalBadge.textContent = 'Total Value: ' + (hasAnyTotal ? formatMoney({ amount: totalAmount, currency: totalCurrency }) : 'N/A');
      overview.appendChild(totalBadge);

      summary.appendChild(overview);

      const accountsList = document.createElement('div');
      accountsList.className = 'accounts-list';

      for (const account of connAccounts) {
        const accountId = String(account.id || '');
        const summaryData = summariesByAccount[accountId] || {};
        const fallbackMetrics = accountSummaryMetrics(account);
        const metrics = {
          total: metricToMoney(summaryData.total_value, summaryData.currency) || fallbackMetrics.total,
          cash: metricToMoney(summaryData.cash, summaryData.currency) || fallbackMetrics.cash,
          buyingPower: metricToMoney(summaryData.buying_power, summaryData.currency) || fallbackMetrics.buyingPower,
          marketValue: metricToMoney(summaryData.market_value, summaryData.currency),
          costBasis: metricToMoney(summaryData.cost_basis, summaryData.currency),
          openPnl: metricToMoney(summaryData.open_pnl, summaryData.currency),
          positionsCount: asNumber(summaryData.positions_count),
        };

        const accountItem = document.createElement('div');
        accountItem.className = 'account-item';

        const accountMain = document.createElement('div');
        accountMain.className = 'account-item-main';

        const accountName = document.createElement('span');
        accountName.className = 'account-item-name';
        const acctName = account.name || account.display_name || 'Unknown Account';
        const acctType = (account.meta && account.meta.type) || account.account_type || account.type || 'Unknown Type';
        accountName.textContent = acctName + ' (' + acctType + ')';
        accountMain.appendChild(accountName);

        const accountTotal = document.createElement('span');
        accountTotal.className = 'account-item-value';
        accountTotal.textContent = formatMoney(metrics.total);
        accountMain.appendChild(accountTotal);

        accountItem.appendChild(accountMain);

        const accountSub = document.createElement('div');
        accountSub.className = 'account-item-sub';
        const positionsCount = metrics.positionsCount === null ? 'N/A' : String(metrics.positionsCount);
        accountSub.textContent = 'Cash: ' + formatMoney(metrics.cash) + ' • Buying Power: ' + formatMoney(metrics.buyingPower) + ' • Positions: ' + positionsCount;
        accountItem.appendChild(accountSub);

        const accountSub2 = document.createElement('div');
        accountSub2.className = 'account-item-sub';
        accountSub2.textContent = 'Market Value: ' + formatMoney(metrics.marketValue) + ' • Cost Basis: ' + formatMoney(metrics.costBasis) + ' • Open P/L: ' + formatMoney(metrics.openPnl);
        accountItem.appendChild(accountSub2);

        accountsList.appendChild(accountItem);
      }

      summary.appendChild(accountsList);
      info.appendChild(summary);
    }

    const actions = document.createElement('div');
    actions.className = 'connection-actions';

    const reconnectButton = document.createElement('button');
    reconnectButton.className = 'btn-reconnect';
    reconnectButton.type = 'button';
    reconnectButton.textContent = 'Reconnect';

    const deleteButton = document.createElement('button');
    deleteButton.className = 'btn-delete';
    deleteButton.type = 'button';
    deleteButton.textContent = 'Delete';

    reconnectButton.addEventListener('click', () => {
      void reconnectConnection(connectionId);
    });
    deleteButton.addEventListener('click', () => {
      void deleteConnection(connectionId);
    });

    actions.appendChild(reconnectButton);
    actions.appendChild(deleteButton);

    card.appendChild(info);
    card.appendChild(actions);
    list.appendChild(card);
  }
}

function reconnectConnection(connectionId) {
  showScreen('connected');
  setPortalResult('', 'Opening connection portal...');
  (async () => {
    try {
      if (!CURRENT_CONTEXT) {
        await checkStatus();
      }
      requireContextOrThrow();
      const redirectUri = await requestPortalLink({ reconnect: connectionId });
      openPortalInWidget(redirectUri);
    } catch (error) {
      setPortalResult('error', error.message || 'Network error');
      showScreen('connected');
    }
  })();
}

function deleteConnection(connectionId) {
  if (!confirm('Delete this connection? This action cannot be undone.')) {
    showScreen('connected');
    return;
  }
  loading('Deleting connection...');
  (async () => {
    try {
      const { response, data } = await apiJson('/snaptrade/connections/' + encodeURIComponent(connectionId), {
        method: 'DELETE',
      });
      if (response.ok) {
        setPortalResult('success', 'Connection deleted.');
        setTimeout(() => { void checkStatus(); }, 1000);
      } else {
        setPortalResult('error', readErrorMessage(data, 'Failed to delete connection'));
      }
      showScreen('connected');
    } catch (_e) {
      setPortalResult('error', 'Network error');
      showScreen('connected');
    }
  })();
}

async function checkStatus() {
  clearError();
  const { response, data } = await apiJson('/snaptrade/context');
  if (!response.ok || !data) {
    CURRENT_CONTEXT = null;
    const detail = data && data.detail ? data.detail : 'Workspace headers are required to initialize SnapTrade.';
    showError(detail);
    showScreen('login');
    return;
  }

  CURRENT_CONTEXT = data;
  document.getElementById('user-name').textContent = 'OpenBB User';
  document.getElementById('user-avatar').textContent = 'W';
  document.getElementById('token-status').textContent = 'Connection portal ready';
  await loadConnections();
  showScreen('connected');
}

function applyPortalRedirectResult() {
  const params = new URLSearchParams(window.location.search);
  const hasStatus = applyPortalStatusParams(params);
  if (!hasStatus) return;

  params.delete('status');
  params.delete('connection_id');
  params.delete('connectionId');
  params.delete('error_code');
  params.delete('status_code');
  const nextQuery = params.toString();
  const nextUrl = nextQuery ? window.location.pathname + '?' + nextQuery : window.location.pathname;
  window.history.replaceState({}, '', nextUrl);
}

function attachHandlers() {
  window.addEventListener('resize', fitPortalViewport);

  window.addEventListener('message', (event) => {
    console.log('[PORTAL] Received message:', event.data);
    
    // SnapTrade sends CLOSE_MODAL when user exits the portal
    if (event.data === 'CLOSE_MODAL' || (event.data && event.data.type === 'CLOSE_MODAL')) {
      console.log('[PORTAL] Close modal request from SnapTrade');
      hidePortalInWidget();
      void checkStatus();
      return;
    }
    
    // Handle status from iframe redirect (if it ever happens)
    if (event.data && event.data.type === 'portal-status' && event.data.status) {
      console.log('[PORTAL] Portal status from iframe:', event.data.status);
      if (event.data.status === 'ABANDONED' || event.data.status === 'SUCCESS' || event.data.status === 'ERROR') {
        const params = new URLSearchParams();
        params.set('status', event.data.status);
        applyPortalStatusParams(params);
        hidePortalInWidget();
        void checkStatus();
      }
    }
  });

  const frame = document.getElementById('portal-frame');
  if (frame) {
    frame.addEventListener('load', () => {
      void handlePortalFrameLoad();
    });
  }

  document.getElementById('btn-login').addEventListener('click', () => {
    loading('Initializing Workspace context...');
    void checkStatus();
  });

  document.getElementById('btn-add-connection').addEventListener('click', async () => {
    showScreen('connected');
    setPortalResult('', 'Opening connection portal...');
    try {
      if (!CURRENT_CONTEXT) {
        await checkStatus();
      }
      requireContextOrThrow();

      const redirectUri = await requestPortalLink({});
      openPortalInWidget(redirectUri);
    } catch (error) {
      setPortalResult('error', error.message || 'Network error');
      showScreen('connected');
    }
  });

  document.getElementById('btn-disconnect').addEventListener('click', async () => {
    loading('Ending session...');
    try {
      const logoutPath = '/snaptrade/logout';
      const logoutUrl = API_BASE ? API_BASE + logoutPath : logoutPath;
      await fetch(logoutUrl, { method: 'POST', headers: workspaceHeaders() });
    } catch (_e) {}
    CURRENT_CONTEXT = null;
    EMPTY_PORTAL_BOOTSTRAPPED = false;
    hidePortalInWidget();
    setPortalResult('', '');
    renderConnections([]);
    showScreen('login');
  });
}

function boot() {
  if (window.OpenBBIframe) {
    window.OpenBBIframe.declare({
      widgets: [
        {
          widgetId: 'snaptrade-connections',
          name: 'Brokerage Connections',
          description: 'Active SnapTrade brokerage connections for the current user.',
          category: 'Brokerage',
          dataType: 'table',
        },
        {
          widgetId: 'snaptrade-connection-accounts',
          name: 'Connected Accounts',
          description: 'Accounts exposed by the active brokerage connections.',
          category: 'Brokerage',
          dataType: 'table',
        },
        {
          widgetId: 'snaptrade-connection-account-summaries',
          name: 'Connected Account Summaries',
          description: 'Per-account financial summaries for the connected accounts.',
          category: 'Brokerage',
          dataType: 'table',
        },
      ],
      params: [],
    });
  }
  attachHandlers();
  applyPortalRedirectResult();
  void checkStatus();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot, { once: true });
} else {
  boot();
}
