// Theme toggle (light / dark / follow system), remembered per browser.
(function () {
  var KEY = "wishunify-docs-theme";
  var root = document.documentElement;
  function read() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function write(v) { try { v ? localStorage.setItem(KEY, v) : localStorage.removeItem(KEY); } catch (e) {} }
  var saved = read();
  if (saved) root.setAttribute("data-theme", saved);
  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.querySelector(".theme-toggle");
    if (!btn) return;
    function label() {
      var t = root.getAttribute("data-theme");
      btn.textContent = t === "dark" ? "Theme: dark" : t === "light" ? "Theme: light" : "Theme: system";
    }
    label();
    btn.addEventListener("click", function () {
      var t = root.getAttribute("data-theme");
      var next = t === null ? "light" : t === "light" ? "dark" : null;
      if (next) root.setAttribute("data-theme", next); else root.removeAttribute("data-theme");
      write(next);
      label();
    });
  });
})();
