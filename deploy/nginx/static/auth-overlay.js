// Injected into every authenticated page by nginx's sub_filter (see
// deploy/nginx/app-ssl.conf). Lives entirely outside the jiuwenswarm repo so
// logout never requires touching upstream's own frontend code. If this
// script loaded at all, the page already passed nginx's auth_request gate -
// no extra "am I logged in" check needed before showing the button.
(function () {
  var btn = document.createElement('button');
  btn.textContent = 'Log out';
  btn.setAttribute('aria-label', 'Log out');
  btn.style.cssText =
    'position:fixed;bottom:16px;right:16px;z-index:2147483647;' +
    'padding:8px 14px;background:#1f2937;color:#fff;border:none;' +
    'border-radius:6px;cursor:pointer;font:14px -apple-system,sans-serif;' +
    'box-shadow:0 2px 8px rgba(0,0,0,.35);opacity:0.85;';
  btn.onmouseenter = function () { btn.style.opacity = '1'; };
  btn.onmouseleave = function () { btn.style.opacity = '0.85'; };
  btn.onclick = function () {
    btn.disabled = true;
    btn.textContent = 'Logging out…';
    fetch('/auth/logout', { method: 'POST' })
      .catch(function () {})
      .finally(function () {
        window.location.href = '/login';
      });
  };
  if (document.body) {
    document.body.appendChild(btn);
  } else {
    document.addEventListener('DOMContentLoaded', function () {
      document.body.appendChild(btn);
    });
  }
})();
