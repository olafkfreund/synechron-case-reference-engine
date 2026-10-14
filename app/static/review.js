// Review page: live word count, unsaved marker, Ctrl/Cmd+S, Revert and Preview for the case-study text. The page works without it.
(function () {
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }
  document.querySelectorAll("form.writer").forEach(function (f) {
    var t = f.querySelector("textarea"), foot = f.querySelector(".writer-foot"), save = foot.querySelector("button");
    var min = parseInt(t.dataset.min, 10) || 0;
    var count = el("span", "", ""); count.setAttribute("aria-live", "polite"); var dirty = el("span", "", "Unsaved changes"), rev = el("button", "btn ghost", "Revert");
    var pv = el("button", "btn ghost", "Preview"), box = el("div", "preview");
    rev.type = pv.type = "button"; pv.setAttribute("aria-pressed", "false"); box.hidden = true; dirty.hidden = true;
    foot.insertBefore(count, save); foot.insertBefore(dirty, save); foot.appendChild(rev); foot.appendChild(pv); f.insertBefore(box, foot);
    function sync() {
      var n = t.value.trim().split(/\s+/).filter(Boolean).length;
      count.textContent = n ? n + (n === 1 ? " word" : " words") + (min ? " · minimum " + min : "") : "";  // empty: the checker counts the sourced points instead
      count.className = min && n >= min ? "ok" : "";
      dirty.hidden = t.value === t.defaultValue; rev.disabled = dirty.hidden;
    }
    t.addEventListener("input", sync);
    f.addEventListener("keydown", function (e) { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); f.requestSubmit(save); } });
    rev.addEventListener("click", function () { t.value = t.defaultValue; if (!box.hidden) pv.click(); sync(); });  // back to the editor: a preview must never show unsaved text
    pv.addEventListener("click", function () {  // textContent only: the text is never parsed as HTML
      var show = box.hidden;
      box.textContent = "";
      if (show) {  // paragraphs split exactly as the outputs do
        var ps = t.value.split("\n\n").map(function (p) { return p.trim(); }).filter(Boolean);
        if (!ps.length) box.appendChild(el("p", "muted", "Nothing to preview yet."));
        ps.forEach(function (p) { box.appendChild(el("p", "", p)); });
      }
      box.hidden = !show; t.hidden = show; pv.setAttribute("aria-pressed", show ? "true" : "false");
    });
    sync();
  });
})();
