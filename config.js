const isLocalBackend = ["localhost", "127.0.0.1", "::1"].includes(location.hostname);

window.CONFIG = Object.freeze({
  API_BASE_URL: isLocalBackend
    ? `${location.protocol}//${location.host}`
    : "https://indirim-radari-backend.onrender.com"
});
