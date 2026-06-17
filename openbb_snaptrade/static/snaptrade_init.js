if (window.self !== window.top) {
  const params = new URLSearchParams(window.location.search);
  const status = params.get('status');
  if (status) {
    window.parent.postMessage({ type: 'portal-status', status }, '*');
    console.log('[IFRAME] Sent status to parent:', status);
  }
}
