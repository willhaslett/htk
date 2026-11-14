// A browser connection, rather than a timer lease, owns this document's window.
// Hidden/background tabs can throttle timers without losing their binding.
(() => {
  const base = new URL(".", location.href);
  let events;
  let page;

  function connect() {
    page = crypto.randomUUID();
    const url = new URL("events", base);
    url.searchParams.set("page", page);
    events = new EventSource(url);
    events.addEventListener("focus", () => {
      // Reuse may not produce a browser focus event (notably in a native host).
      window.refresh?.();
      const native = window.webkit?.messageHandlers?.htkPreview;
      if (native) native.postMessage("focus");
      else window.focus();
    });
  }

  window.addEventListener("pagehide", () => {
    events.close();
    const url = new URL("leave", base);
    url.searchParams.set("page", page);
    // Closing the stream also releases the binding if pagehide isn't delivered
    // (for example, if the browser crashes). The beacon avoids that extra delay.
    navigator.sendBeacon(url);
  });
  window.addEventListener("pageshow", event => {
    if (event.persisted) connect(); // Restored from the back/forward cache.
  });
  connect();
})();
