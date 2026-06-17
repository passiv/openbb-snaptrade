(function () {
  if (window.OpenBBIframe) return;

  var target = window.top || window.parent;
  var isEmbedded = target && target !== window;
  var manifests = [];
  var paramDefs = [];
  var cache = {};
  var pendingAll = [];
  var pendingById = {};
  var announced = false;

  function send(message, origin) {
    if (!isEmbedded) return;
    try {
      target.postMessage(message, origin || '*');
    } catch (_e) {
      // no-op
    }
  }

  function announce() {
    send({ type: 'openbb-connect', widgets: manifests, params: paramDefs });
    announced = true;
  }

  function declare(config) {
    manifests = (config && Array.isArray(config.widgets)) ? config.widgets : [];
    paramDefs = (config && Array.isArray(config.params)) ? config.params : [];
    announce();
  }

  function buildMessage(widgetId, entry) {
    return {
      type: 'openbb-data',
      widgetId: widgetId,
      dataType: entry.dataType,
      data: entry.data,
    };
  }

  function flushFor(widgetId) {
    var entry = cache[widgetId];
    if (!entry) return;
    var message = buildMessage(widgetId, entry);

    var perWidget = pendingById[widgetId];
    if (perWidget && perWidget.length) {
      perWidget.forEach(function (origin) { send(message, origin); });
      pendingById[widgetId] = [];
    }

    if (pendingAll.length) {
      pendingAll.forEach(function (origin) { send(message, origin); });
    }
  }

  function publish(widgetId, data, dataType) {
    if (!widgetId) return;
    var type = (dataType === 'markdown') ? 'markdown' : 'table';
    cache[widgetId] = { dataType: type, data: data };
    flushFor(widgetId);
  }

  window.addEventListener('message', function (event) {
    var data = event.data;
    if (!data || typeof data !== 'object' || !data.type) return;

    if (data.type === 'openbb-request') {
      var requested = data.widgetId;
      var origin = event.origin || '*';

      if (requested === null || requested === undefined) {
        var ids = Object.keys(cache);
        if (ids.length) {
          ids.forEach(function (id) { send(buildMessage(id, cache[id]), origin); });
        }
        pendingAll.push(origin);
        return;
      }

      if (cache[requested]) {
        send(buildMessage(requested, cache[requested]), origin);
        return;
      }

      if (!pendingById[requested]) pendingById[requested] = [];
      pendingById[requested].push(origin);
      return;
    }

    if (data.type === 'openbb-params-update') {
      try {
        window.dispatchEvent(new CustomEvent('openbb:params-update', { detail: data }));
      } catch (_e) {
        // no-op
      }
    }
  });

  window.OpenBBIframe = {
    declare: declare,
    publish: publish,
    announce: announce,
    isEmbedded: function () { return !!isEmbedded; },
    hasAnnounced: function () { return announced; },
  };
})();
