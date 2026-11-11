if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/service-worker.js").catch(() => {
    // The online application stays available when service workers are disabled.
  });
}
