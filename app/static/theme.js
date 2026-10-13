// Sets data-theme before paint from localStorage.theme ("light" or "dark"); anything else follows the OS.
(function () {
  var d = document.documentElement;
  function stored() {
    try { var v = localStorage.getItem("theme"); return v === "light" || v === "dark" ? v : null; } catch (e) { return null; }
  }
  function current() {
    return d.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  }
  var s = stored();
  if (s) d.setAttribute("data-theme", s);
  document.addEventListener("DOMContentLoaded", function () {
    var b = document.querySelector(".theme-toggle");
    if (!b) return;
    function sync() { b.setAttribute("aria-pressed", current() === "dark" ? "true" : "false"); }
    sync();
    b.addEventListener("click", function () {
      var n = current() === "dark" ? "light" : "dark";
      d.setAttribute("data-theme", n);
      try { localStorage.setItem("theme", n); } catch (e) {}
      sync();
    });
  });
})();
