(function () {
  const EQUITY_ORDER_TYPES = [
    { label: 'Market', value: 'Market' },
    { label: 'Limit', value: 'Limit' },
    { label: 'Stop', value: 'Stop' },
    { label: 'Stop Limit', value: 'StopLimit' },
  ];
  const EQUITY_TIF = [
    { label: 'Day', value: 'Day' },
    { label: 'Good Till Cancelled (GTC)', value: 'GTC' },
    { label: 'Fill or Kill (FOK)', value: 'FOK' },
    { label: 'Immediate or Cancel (IOC)', value: 'IOC' },
  ];
  const EXTENDED_HOURS_TIF = [
    { label: 'Day', value: 'Day' },
    { label: 'Good Till Cancelled (GTC)', value: 'GTC' },
  ];
  const CRYPTO_ORDER_TYPES = [
    { label: 'Market', value: 'MARKET' },
    { label: 'Limit', value: 'LIMIT' },
    { label: 'Stop-Loss Market', value: 'STOP_LOSS_MARKET' },
    { label: 'Stop-Loss Limit', value: 'STOP_LOSS_LIMIT' },
  ];
  const CRYPTO_TIF = [
    { label: 'Good Till Cancelled (GTC)', value: 'GTC' },
    { label: 'Fill or Kill (FOK)', value: 'FOK' },
    { label: 'Immediate or Cancel (IOC)', value: 'IOC' },
  ];
  const OPTION_ORDER_TYPES = [
    { label: 'Market', value: 'MARKET' },
    { label: 'Limit', value: 'LIMIT' },
    { label: 'Stop-Loss Market', value: 'STOP_LOSS_MARKET' },
    { label: 'Stop-Loss Limit', value: 'STOP_LOSS_LIMIT' },
  ];
  const OPTION_TIF = [
    { label: 'Day', value: 'Day' },
    { label: 'Good Till Cancelled (GTC)', value: 'GTC' },
    { label: 'Fill or Kill (FOK)', value: 'FOK' },
    { label: 'Immediate or Cancel (IOC)', value: 'IOC' },
  ];

  const state = {
    accounts: [],
    selectedAccount: null,
    assetClass: 'equity',
    selectedSymbol: null,
    side: 'BUY',
    lastImpact: null,
    lastTradeId: null,
    searchTimer: null,
    armTimer: null,
    legs: [],
  };

  function $(id) { return document.getElementById(id); }

  function workspaceHeaders() {
    const headers = {};
    const match = String(window.location.pathname || '').match(/\/s\/([0-9a-f]+:[0-9]+:[0-9]+:[0-9a-f]+)(?:[\/?#]|$)/i);
    if (match) {
      headers['Authorization'] = 'Bearer ' + match[1];
    }
    return headers;
  }

  async function api(path, options) {
    const merged = Object.assign({}, workspaceHeaders(), (options && options.headers) || {});
    const opts = Object.assign({}, options || {});
    if (Object.keys(merged).length) opts.headers = merged;
    const res = await fetch(path, opts);
    let data = null;
    try { data = await res.json(); } catch (_e) { data = null; }
    return { res, data };
  }

  function setStatus(text, kind) {
    const el = $('status');
    el.textContent = text || '';
    el.className = 'status' + (kind ? ' status-' + kind : '');
  }

  function fmtNumber(value, digits) {
    if (value === null || value === undefined || value === '') return '—';
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    return n.toLocaleString(undefined, { minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 4 });
  }

  function fmtMoney(value) {
    if (value === null || value === undefined || value === '') return '—';
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    return n.toLocaleString(undefined, { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function fillSelect(select, items, placeholder) {
    select.innerHTML = '';
    if (placeholder) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = placeholder;
      opt.disabled = true;
      opt.selected = true;
      select.appendChild(opt);
    }
    for (const it of items) {
      const opt = document.createElement('option');
      opt.value = it.value;
      opt.textContent = it.label;
      select.appendChild(opt);
    }
  }

  function buildOcc(underlying, expiryISO, callPut, strike) {
    if (!underlying || !expiryISO || !callPut || !strike) return '';
    const sym = String(underlying).toUpperCase().padEnd(6, ' ').slice(0, 6);
    const dt = new Date(expiryISO + 'T00:00:00Z');
    if (Number.isNaN(dt.getTime())) return '';
    const yy = String(dt.getUTCFullYear() % 100).padStart(2, '0');
    const mm = String(dt.getUTCMonth() + 1).padStart(2, '0');
    const dd = String(dt.getUTCDate()).padStart(2, '0');
    const strikeInt = Math.round(Number(strike) * 1000);
    if (!Number.isFinite(strikeInt) || strikeInt <= 0) return '';
    const strikeStr = String(strikeInt).padStart(8, '0');
    return `${sym}${yy}${mm}${dd}${callPut.toUpperCase()}${strikeStr}`;
  }

  async function loadAccounts() {
    setStatus('Loading accounts...');
    const { res, data } = await api('/snaptrade/trade/accounts');
    if (!res.ok || !Array.isArray(data)) {
      setStatus((data && (data.detail || data.error)) || 'Failed to load accounts.', 'error');
      if (window.OpenBBIframe) {
        window.OpenBBIframe.publish('snaptrade-trade-accounts', [], 'table');
      }
      return;
    }
    state.accounts = data;
    const items = data.map((a) => ({ value: a.id, label: a.label || a.name || a.id }));
    fillSelect($('account'), items, data.length ? 'Select account...' : 'No accounts available');
    if (data.length === 1) {
      $('account').value = data[0].id;
      onAccountChange();
    }
    if (window.OpenBBIframe) {
      window.OpenBBIframe.publish('snaptrade-trade-accounts', data, 'table');
    }
    setStatus(data.length + ' account' + (data.length === 1 ? '' : 's') + ' loaded.', 'ok');
  }

  function onAccountChange() {
    const id = $('account').value;
    const acct = state.accounts.find((a) => a.id === id) || null;
    state.selectedAccount = acct;
    const meta = $('account-meta');
    if (!acct) {
      meta.textContent = '';
      return;
    }
    const classes = acct.asset_classes || [];
    const bits = [];
    if (acct.brokerage_name) bits.push(acct.brokerage_name);
    if (!acct.allows_trading) bits.push('Trading disabled by brokerage');
    if (classes.length) bits.push('Supports: ' + classes.join(', '));
    if (acct.allows_fractional_units) bits.push('Fractional supported');
    meta.textContent = bits.join(' · ');

    const sel = $('asset-class');
    const wanted = new Set(classes);
    Array.from(sel.options).forEach((opt) => {
      opt.disabled = wanted.size ? !wanted.has(opt.value) : false;
    });
    if (wanted.size && !wanted.has(sel.value)) {
      const firstAllowed = Array.from(sel.options).find((o) => !o.disabled);
      if (firstAllowed) sel.value = firstAllowed.value;
    }
    onAssetClassChange();
    clearSelectedSymbol();
  }

  function onAssetClassChange() {
    state.assetClass = $('asset-class').value;
    const isCrypto = state.assetClass === 'crypto';
    const isOption = state.assetClass === 'option';
    const isEquity = state.assetClass === 'equity';

    $('single-symbol-section').hidden = isOption;
    $('options-section').hidden = !isOption;
    $('side-row').hidden = isOption;
    $('quantity-field').hidden = isOption;
    $('notional-row').hidden = isOption || isCrypto;
    $('extended-hours-row').hidden = !isEquity;
    $('post-only-row').hidden = !isCrypto;

    if (isOption) {
      fillSelect($('order-type'), OPTION_ORDER_TYPES);
      fillSelect($('time-in-force'), OPTION_TIF);
      $('order-type').value = 'MARKET';
      $('time-in-force').value = 'Day';
      if (!state.legs.length) addLeg();
      updatePriceEffectVisibility();
    } else if (isCrypto) {
      fillSelect($('order-type'), CRYPTO_ORDER_TYPES);
      fillSelect($('time-in-force'), CRYPTO_TIF);
      $('order-type').value = 'MARKET';
      $('time-in-force').value = 'GTC';
      $('quantity-label').textContent = 'Amount (base units)';
    } else {
      const extended = $('extended-hours').checked;
      fillSelect($('order-type'), EQUITY_ORDER_TYPES);
      fillSelect($('time-in-force'), extended ? EXTENDED_HOURS_TIF : EQUITY_TIF);
      $('order-type').value = extended ? 'Limit' : 'Market';
      $('time-in-force').value = 'Day';
      $('quantity-label').textContent = 'Quantity (units)';
    }

    const acct = state.selectedAccount;
    const supportsNotional = !!(acct && acct.allows_fractional_units) && isEquity && !$('extended-hours').checked;
    if (!supportsNotional) {
      $('use-notional').checked = false;
      $('notional-field').hidden = true;
    }
    $('notional-row').hidden = !supportsNotional;

    onOrderTypeChange();
    clearPreview();
  }

  function onExtendedHoursToggle() {
    if (state.assetClass !== 'equity') return;
    const extended = $('extended-hours').checked;
    fillSelect($('time-in-force'), extended ? EXTENDED_HOURS_TIF : EQUITY_TIF);
    if (extended) {
      $('order-type').value = 'Limit';
      Array.from($('order-type').options).forEach((opt) => {
        opt.disabled = opt.value !== 'Limit';
      });
      $('use-notional').checked = false;
      $('notional-row').hidden = true;
    } else {
      Array.from($('order-type').options).forEach((opt) => { opt.disabled = false; });
      const acct = state.selectedAccount;
      $('notional-row').hidden = !(acct && acct.allows_fractional_units);
    }
    onOrderTypeChange();
  }

  function onOrderTypeChange() {
    const type = $('order-type').value;
    const ac = state.assetClass;
    let showLimit = false;
    let showStop = false;
    if (ac === 'crypto' || ac === 'option') {
      showLimit = type === 'LIMIT' || type === 'STOP_LOSS_LIMIT';
      showStop = type === 'STOP_LOSS_MARKET' || type === 'STOP_LOSS_LIMIT';
    } else {
      showLimit = type === 'Limit' || type === 'StopLimit';
      showStop = type === 'Stop' || type === 'StopLimit';
    }
    $('price-row').hidden = !(showLimit || showStop);
    $('limit-field').hidden = !showLimit;
    $('stop-field').hidden = !showStop;
  }

  function setSide(side) {
    state.side = side;
    document.querySelectorAll('.seg-btn[data-side]').forEach((btn) => {
      btn.classList.toggle('active', btn.getAttribute('data-side') === side);
    });
  }

  function clearPreview() {
    state.lastImpact = null;
    state.lastTradeId = null;
    $('preview-panel').hidden = true;
    $('result-panel').hidden = true;
    $('btn-place').disabled = true;
    disarmPlaceButton();
  }

  function clearSelectedSymbol() {
    state.selectedSymbol = null;
    $('symbol-input').value = '';
    $('symbol-results').hidden = true;
    $('symbol-results').innerHTML = '';
    $('symbol-selected').hidden = true;
    $('symbol-selected').innerHTML = '';
    $('quote-panel').hidden = true;
    clearPreview();
  }

  function pickSymbol(sym) {
    state.selectedSymbol = sym;
    $('symbol-results').hidden = true;
    $('symbol-results').innerHTML = '';
    const pill = $('symbol-selected');
    pill.innerHTML = '';
    const left = document.createElement('div');
    left.innerHTML = '<span class="sym">' + escapeHtml(sym.symbol) + '</span>'
      + (sym.description ? ' <span style="color:var(--muted)">— ' + escapeHtml(sym.description) + '</span>' : '');
    const btn = document.createElement('button');
    btn.className = 'clear';
    btn.type = 'button';
    btn.textContent = '×';
    btn.addEventListener('click', clearSelectedSymbol);
    pill.appendChild(left);
    pill.appendChild(btn);
    pill.hidden = false;
    refreshQuote();
  }

  async function searchSymbols(query) {
    const acct = state.selectedAccount;
    if (!acct || !query || query.length < 1) {
      $('symbol-results').hidden = true;
      $('symbol-results').innerHTML = '';
      return;
    }
    const params = new URLSearchParams({
      accountId: acct.id,
      q: query,
      assetClass: state.assetClass,
    });
    const { res, data } = await api('/snaptrade/trade/symbol-search?' + params.toString());
    if (!res.ok || !Array.isArray(data)) {
      $('symbol-results').hidden = true;
      return;
    }
    const box = $('symbol-results');
    box.innerHTML = '';
    if (!data.length) {
      box.innerHTML = '<div class="search-result"><div class="desc">No matches</div></div>';
      box.hidden = false;
      return;
    }
    data.slice(0, 25).forEach((s) => {
      const row = document.createElement('div');
      row.className = 'search-result';
      const meta = [s.exchange, s.currency, s.type].filter(Boolean).join(' · ');
      row.innerHTML = '<div class="sym">' + escapeHtml(s.symbol) + '</div>'
        + (s.description ? '<div class="desc">' + escapeHtml(s.description) + '</div>' : '')
        + (meta ? '<div class="meta">' + escapeHtml(meta) + '</div>' : '');
      row.addEventListener('click', () => pickSymbol(s));
      box.appendChild(row);
    });
    box.hidden = false;
  }

  async function refreshQuote() {
    const acct = state.selectedAccount;
    const sym = state.selectedSymbol;
    if (!acct || !sym) return;
    const params = new URLSearchParams({
      accountId: acct.id,
      symbol: sym.symbol,
      assetClass: state.assetClass,
    });
    const { res, data } = await api('/snaptrade/trade/quote?' + params.toString());
    if (!res.ok || !data) {
      $('quote-panel').hidden = true;
      return;
    }
    $('quote-bid').textContent = fmtMoney(data.bid_price);
    $('quote-ask').textContent = fmtMoney(data.ask_price);
    $('quote-last').textContent = fmtMoney(data.last_trade_price);
    $('quote-panel').hidden = false;
  }

  function addLeg() {
    const tpl = $('leg-template');
    const node = tpl.content.firstElementChild.cloneNode(true);
    const legId = 'leg-' + Math.random().toString(36).slice(2, 8);
    node.dataset.legId = legId;
    node.querySelector('.leg-remove').addEventListener('click', () => removeLeg(legId));
    node.querySelector('.leg-build-btn').addEventListener('click', () => {
      const u = node.querySelector('.leg-underlying').value.trim();
      const e = node.querySelector('.leg-expiry').value;
      const cp = node.querySelector('.leg-cp').value;
      const k = node.querySelector('.leg-strike').value;
      const occ = buildOcc(u, e, cp, k);
      if (!occ) {
        setStatus('Builder: fill underlying, expiry, type, and strike.', 'error');
        return;
      }
      node.querySelector('.leg-symbol').value = occ;
      setStatus('Built OCC: ' + occ, 'ok');
      syncLegsFromDOM();
    });
    ['leg-symbol', 'leg-units', 'leg-action'].forEach((cls) => {
      node.querySelector('.' + cls).addEventListener('change', syncLegsFromDOM);
      node.querySelector('.' + cls).addEventListener('input', syncLegsFromDOM);
    });
    state.legs.push({ id: legId });
    $('legs-list').appendChild(node);
    renumberLegs();
    updatePriceEffectVisibility();
    clearPreview();
  }

  function removeLeg(legId) {
    state.legs = state.legs.filter((l) => l.id !== legId);
    const node = $('legs-list').querySelector('[data-leg-id="' + legId + '"]');
    if (node) node.remove();
    if (!state.legs.length) addLeg();
    renumberLegs();
    updatePriceEffectVisibility();
    clearPreview();
  }

  function renumberLegs() {
    Array.from($('legs-list').children).forEach((node, idx) => {
      node.querySelector('.leg-index').textContent = 'Leg ' + (idx + 1);
    });
  }

  function updatePriceEffectVisibility() {
    $('price-effect-field').hidden = state.legs.length < 2;
  }

  function syncLegsFromDOM() {
    clearPreview();
  }

  function readLegsFromDOM() {
    const nodes = Array.from($('legs-list').children);
    return nodes.map((node) => ({
      symbol: node.querySelector('.leg-symbol').value.trim(),
      action: node.querySelector('.leg-action').value,
      units: Number(node.querySelector('.leg-units').value),
      instrumentType: 'OPTION',
    }));
  }

  function gatherFormPayload() {
    const acct = state.selectedAccount;
    if (!acct) throw new Error('Select an account first.');

    const orderType = $('order-type').value;
    const tif = $('time-in-force').value;
    const limit = $('limit-price').value;
    const stop = $('stop-price').value;

    if (state.assetClass === 'option') {
      const legs = readLegsFromDOM();
      if (!legs.length) throw new Error('Add at least one leg.');
      for (let i = 0; i < legs.length; i += 1) {
        const lg = legs[i];
        if (!lg.symbol) throw new Error('Leg ' + (i + 1) + ': option symbol is required.');
        if (!Number.isFinite(lg.units) || lg.units <= 0) throw new Error('Leg ' + (i + 1) + ': contracts must be > 0.');
      }
      const payload = {
        accountId: acct.id,
        orderType,
        timeInForce: tif,
        legs,
      };
      if (orderType === 'LIMIT' || orderType === 'STOP_LOSS_LIMIT') {
        if (!limit) throw new Error('Limit price required.');
        payload.limitPrice = String(Number(limit));
      }
      if (orderType === 'STOP_LOSS_MARKET' || orderType === 'STOP_LOSS_LIMIT') {
        if (!stop) throw new Error('Stop price required.');
        payload.stopPrice = String(Number(stop));
      }
      if (legs.length > 1) {
        const pe = $('price-effect').value;
        if (pe) payload.priceEffect = pe;
      }
      return { kind: 'option', payload };
    }

    const sym = state.selectedSymbol;
    if (!sym) throw new Error('Select a symbol first.');
    const qty = $('quantity').value;
    const useNotional = $('use-notional').checked;
    const notional = $('notional-value').value;

    if (state.assetClass === 'crypto') {
      const amount = Number(qty);
      if (!Number.isFinite(amount) || amount <= 0) throw new Error('Enter a positive amount.');
      const payload = {
        accountId: acct.id,
        instrumentSymbol: sym.symbol,
        instrumentType: sym.type || 'CRYPTOCURRENCY_PAIR',
        side: state.side,
        orderType,
        timeInForce: tif,
        amount: String(amount),
      };
      if (orderType === 'LIMIT' || orderType === 'STOP_LOSS_LIMIT') {
        if (!limit) throw new Error('Limit price required.');
        payload.limitPrice = String(Number(limit));
      }
      if (orderType === 'STOP_LOSS_MARKET' || orderType === 'STOP_LOSS_LIMIT') {
        if (!stop) throw new Error('Stop price required.');
        payload.stopPrice = String(Number(stop));
      }
      if ($('post-only').checked) payload.postOnly = true;
      return { kind: 'crypto', payload };
    }

    const extended = $('extended-hours').checked;
    if (extended) {
      if (orderType !== 'Limit') throw new Error('Extended hours requires a Limit order.');
      if (!['Day', 'GTC'].includes(tif)) throw new Error('Extended hours TIF must be Day or GTC.');
    }
    const universalSymbolId = sym.id || sym.universal_symbol_id || '';
    if (!universalSymbolId) throw new Error('Selected symbol has no universal_symbol_id; pick another.');

    if (extended) {
      const payload = {
        accountId: acct.id,
        action: state.side,
        orderType,
        timeInForce: tif,
        tradingSession: 'EXTENDED',
        universalSymbolId,
      };
      const n = Number(qty);
      if (!Number.isFinite(n) || n <= 0) throw new Error('Enter a quantity.');
      payload.units = n;
      if (!limit) throw new Error('Limit price required.');
      payload.price = Number(limit);
      return { kind: 'force', payload };
    }

    const payload = {
      accountId: acct.id,
      action: state.side,
      orderType,
      timeInForce: tif,
      universalSymbolId,
    };
    if (useNotional) {
      const n = Number(notional);
      if (!Number.isFinite(n) || n <= 0) throw new Error('Enter a notional amount.');
      payload.notionalValue = n;
    } else {
      const n = Number(qty);
      if (!Number.isFinite(n) || n <= 0) throw new Error('Enter a quantity.');
      payload.units = n;
    }
    if (orderType === 'Limit' || orderType === 'StopLimit') {
      if (!limit) throw new Error('Limit price required.');
      payload.price = Number(limit);
    }
    if (orderType === 'Stop' || orderType === 'StopLimit') {
      if (!stop) throw new Error('Stop price required.');
      payload.stop = Number(stop);
    }
    return { kind: 'equity', payload };
  }

  function renderImpact(panel, data) {
    panel.innerHTML = '';
    panel.hidden = false;
    panel.classList.remove('ok', 'error');

    const trade = (data && (data.trade || data.preview || data)) || {};
    const warnings = (data && (data.warnings || data.trade_warnings)) || [];
    const rows = [];
    if (trade.action) rows.push(['Action', trade.action]);
    if (trade.symbol && typeof trade.symbol === 'object') {
      const sym = trade.symbol.symbol || trade.symbol.raw_symbol;
      if (sym) rows.push(['Symbol', sym]);
    } else if (trade.symbol) rows.push(['Symbol', trade.symbol]);
    if (Array.isArray(trade.legs)) {
      trade.legs.forEach((lg, i) => {
        const instr = lg.instrument || {};
        rows.push(['Leg ' + (i + 1), [lg.action, instr.symbol, lg.units + ' ct'].filter(Boolean).join(' · ')]);
      });
    }
    if (trade.units !== undefined && trade.units !== null) rows.push(['Units', fmtNumber(trade.units, 4)]);
    if (trade.notional_value !== undefined && trade.notional_value !== null) {
      rows.push(['Notional', fmtMoney(trade.notional_value)]);
    }
    if (trade.price !== undefined && trade.price !== null) rows.push(['Price', fmtMoney(trade.price)]);
    if (trade.limit_price !== undefined && trade.limit_price !== null) rows.push(['Limit Price', fmtMoney(trade.limit_price)]);
    if (trade.stop_price !== undefined && trade.stop_price !== null) rows.push(['Stop Price', fmtMoney(trade.stop_price)]);
    if (trade.price_effect) rows.push(['Price Effect', trade.price_effect]);
    if (trade.order_type) rows.push(['Order Type', trade.order_type]);
    if (trade.time_in_force) rows.push(['TIF', trade.time_in_force]);
    if (trade.trading_session) rows.push(['Session', trade.trading_session]);
    if (data && data.estimated_commissions !== undefined) {
      rows.push(['Est. Commissions', fmtMoney(data.estimated_commissions)]);
    }
    const remaining = data && (data.remaining_balance || data.estimated_remaining_balance);
    if (remaining !== undefined && remaining !== null) {
      const amt = (typeof remaining === 'object') ? remaining.amount : remaining;
      rows.push(['Est. Remaining Balance', fmtMoney(amt)]);
    }
    if (trade.id) rows.push(['Trade ID', trade.id]);

    if (!rows.length) {
      const pre = document.createElement('pre');
      pre.style.cssText = 'margin:0;white-space:pre-wrap;word-break:break-word;font-size:11px;color:var(--muted)';
      pre.textContent = JSON.stringify(data, null, 2);
      panel.appendChild(pre);
    } else {
      rows.forEach(([k, v]) => {
        const row = document.createElement('div');
        row.className = 'preview-row';
        row.innerHTML = '<span class="k">' + escapeHtml(k) + '</span><span class="v">' + escapeHtml(String(v)) + '</span>';
        panel.appendChild(row);
      });
    }

    if (Array.isArray(warnings) && warnings.length) {
      const list = document.createElement('ul');
      list.className = 'warning-list';
      warnings.forEach((w) => {
        const li = document.createElement('li');
        li.textContent = typeof w === 'string' ? w : ((w && (w.message || w.detail)) || JSON.stringify(w));
        list.appendChild(li);
      });
      panel.appendChild(list);
    }
    panel.classList.add('ok');
    state.lastTradeId = (trade && trade.id) ? trade.id : ((data && data.trade_id) || null);
  }

  function renderError(panel, message) {
    panel.hidden = false;
    panel.classList.remove('ok');
    panel.classList.add('error');
    panel.textContent = message;
  }

  async function previewOrder() {
    $('preview-panel').hidden = true;
    $('result-panel').hidden = true;
    $('btn-place').disabled = true;
    state.lastImpact = null;
    state.lastTradeId = null;
    disarmPlaceButton();

    let formData;
    try { formData = gatherFormPayload(); }
    catch (e) { renderError($('preview-panel'), e.message); return; }

    setStatus('Requesting impact preview...');
    let url;
    if (formData.kind === 'crypto') url = '/snaptrade/trade/crypto/preview';
    else if (formData.kind === 'option') url = '/snaptrade/trade/options/impact';
    else if (formData.kind === 'force') {
      $('preview-panel').hidden = false;
      $('preview-panel').classList.remove('ok', 'error');
      $('preview-panel').classList.add('ok');
      $('preview-panel').innerHTML = '<div class="preview-row"><span class="k">Extended-hours session</span><span class="v">No preview available — place to submit.</span></div>';
      state.lastImpact = { kind: 'force', payload: formData.payload };
      $('btn-place').dataset.kind = 'force';
      $('btn-place').disabled = false;
      setStatus('Ready to submit extended-hours order.', 'ok');
      return;
    }
    else url = '/snaptrade/trade/impact';

    const { res, data } = await api(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(formData.payload),
    });
    console.log('[TRADE] preview response:', data);
    if (!res.ok) {
      const msg = (data && (data.detail || data.error)) || 'Preview failed.';
      renderError($('preview-panel'), typeof msg === 'string' ? msg : JSON.stringify(msg));
      setStatus('Preview failed.', 'error');
      return;
    }
    state.lastImpact = { kind: formData.kind, response: data, payload: formData.payload };
    renderImpact($('preview-panel'), data);
    if (formData.kind === 'crypto' || formData.kind === 'option') {
      $('btn-place').disabled = false;
      $('btn-place').dataset.kind = formData.kind;
      setStatus('Preview complete. Review and Place when ready.', 'ok');
    } else if (formData.kind === 'equity') {
      if (state.lastTradeId) {
        $('btn-place').disabled = false;
        $('btn-place').dataset.kind = 'equity';
        setStatus('Preview complete. Trade ID ' + state.lastTradeId, 'ok');
      } else {
        setStatus('Preview returned no trade id; cannot place.', 'error');
      }
    }
  }

  function armPlaceButton() {
    const btn = $('btn-place');
    btn.classList.add('btn-confirm');
    btn.textContent = 'Confirm & Submit';
    btn.dataset.armed = '1';
    clearTimeout(state.armTimer);
    state.armTimer = setTimeout(disarmPlaceButton, 6000);
  }

  function disarmPlaceButton() {
    const btn = $('btn-place');
    btn.classList.remove('btn-confirm');
    btn.textContent = 'Place Order';
    delete btn.dataset.armed;
    clearTimeout(state.armTimer);
  }

  async function placeOrder() {
    const btn = $('btn-place');
    const kind = btn.dataset.kind;
    if (!kind) return;
    if (btn.dataset.armed !== '1') {
      armPlaceButton();
      setStatus('Click again to confirm submission.', 'ok');
      return;
    }
    disarmPlaceButton();
    btn.disabled = true;
    setStatus('Submitting order...');

    let res, data;
    if (kind === 'equity') {
      if (!state.lastTradeId) {
        setStatus('Missing trade id from preview.', 'error');
        btn.disabled = false;
        return;
      }
      const r = await api('/snaptrade/trade/place', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tradeId: state.lastTradeId, waitToConfirm: true }),
      });
      res = r.res; data = r.data;
    } else if (kind === 'force') {
      if (!state.lastImpact || !state.lastImpact.payload) {
        setStatus('Missing payload; re-run preview.', 'error');
        btn.disabled = false;
        return;
      }
      const r = await api('/snaptrade/trade/force', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(state.lastImpact.payload),
      });
      res = r.res; data = r.data;
    } else if (kind === 'option') {
      let formData;
      try { formData = gatherFormPayload(); }
      catch (e) { renderError($('result-panel'), e.message); btn.disabled = false; return; }
      const r = await api('/snaptrade/trade/options/place', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData.payload),
      });
      res = r.res; data = r.data;
    } else {
      let formData;
      try { formData = gatherFormPayload(); }
      catch (e) { renderError($('result-panel'), e.message); btn.disabled = false; return; }
      const r = await api('/snaptrade/trade/crypto/place', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData.payload),
      });
      res = r.res; data = r.data;
    }

    if (!res || !res.ok) {
      const status = res ? res.status : '?';
      const detail = (data && (data.detail || data.error)) || ('HTTP ' + status);
      const msg = typeof detail === 'string' ? detail : JSON.stringify(detail);
      renderError($('result-panel'), 'Order failed: ' + msg);
      setStatus('Order failed.', 'error');
      btn.disabled = false;
      return;
    }
    const panel = $('result-panel');
    panel.hidden = false;
    panel.classList.remove('error');
    panel.classList.add('ok');
    panel.innerHTML = '<div style="font-weight:700;margin-bottom:6px;color:var(--accent)">Order accepted</div>';
    const pre = document.createElement('pre');
    pre.style.cssText = 'margin:0;white-space:pre-wrap;word-break:break-word;font-size:11px;color:var(--muted)';
    pre.textContent = JSON.stringify(data, null, 2);
    panel.appendChild(pre);
    setStatus('Order submitted.', 'ok');
    state.lastImpact = null;
    state.lastTradeId = null;
  }

  function attach() {
    $('refresh').addEventListener('click', loadAccounts);
    $('account').addEventListener('change', onAccountChange);
    $('asset-class').addEventListener('change', onAssetClassChange);
    $('order-type').addEventListener('change', onOrderTypeChange);
    $('extended-hours').addEventListener('change', onExtendedHoursToggle);
    $('use-notional').addEventListener('change', () => {
      $('notional-field').hidden = !$('use-notional').checked;
      $('quantity-field').hidden = $('use-notional').checked;
    });
    document.querySelectorAll('.seg-btn[data-side]').forEach((btn) => {
      btn.addEventListener('click', () => setSide(btn.getAttribute('data-side')));
    });
    $('symbol-input').addEventListener('input', (e) => {
      const q = e.target.value.trim();
      if (state.searchTimer) clearTimeout(state.searchTimer);
      state.searchTimer = setTimeout(() => searchSymbols(q), 220);
    });
    $('symbol-input').addEventListener('focus', () => {
      if ($('symbol-results').innerHTML) $('symbol-results').hidden = false;
    });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.search-wrap')) $('symbol-results').hidden = true;
    });
    $('btn-refresh-quote').addEventListener('click', refreshQuote);
    $('btn-preview').addEventListener('click', previewOrder);
    $('btn-place').addEventListener('click', placeOrder);
    $('btn-add-leg').addEventListener('click', addLeg);

    fillSelect($('order-type'), EQUITY_ORDER_TYPES);
    fillSelect($('time-in-force'), EQUITY_TIF);
    $('order-type').value = 'Market';
    $('time-in-force').value = 'Day';
    onOrderTypeChange();
  }

  function declareSubWidgets() {
    if (!window.OpenBBIframe) return;
    window.OpenBBIframe.declare({
      widgets: [
        {
          widgetId: 'snaptrade-trade-accounts',
          name: 'Tradable Accounts',
          description: 'Accounts available for trading (with brokerage slug, type, and supported asset classes).',
          category: 'Brokerage',
          dataType: 'table',
        },
      ],
      params: [],
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { attach(); declareSubWidgets(); loadAccounts(); });
  } else {
    attach();
    declareSubWidgets();
    loadAccounts();
  }
})();
