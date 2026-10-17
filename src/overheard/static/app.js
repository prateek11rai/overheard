(function () {
  "use strict";
  var toastTimer = null;

  function closePopovers(except) {
    document.querySelectorAll("details.pd[open]").forEach(function (d) {
      if (d !== except && !d.contains(except)) d.removeAttribute("open");
    });
  }

  function showToast(detail) {
    var t = document.getElementById("toast");
    if (!t) return;
    var text = typeof detail === "string" ? detail : detail.text;
    t.querySelector("[data-toast-text]").textContent = text || "";
    var undo = t.querySelector("[data-toast-undo]");
    if (detail && detail.undo) {
      undo.hidden = false;
      undo.setAttribute("data-url", detail.undo);
    } else {
      undo.hidden = true;
      undo.removeAttribute("data-url");
    }
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove("show"); }, 6000);
  }
  window.overheardToast = showToast;

  var main = document.getElementById("main");
  if (main && main.getAttribute("data-toast")) {
    showToast({ text: main.getAttribute("data-toast") });
    if (window.history && window.history.replaceState) {
      var url = new URL(window.location.href);
      url.searchParams.delete("done");
      window.history.replaceState(null, "", url.pathname + url.search + url.hash);
    }
  }

  document.body.addEventListener("toast", function (e) { showToast(e.detail); });
  document.body.addEventListener("title", function (e) {
    if (e.detail && e.detail.value) document.title = e.detail.value;
  });
  document.body.addEventListener("closePopovers", function () { closePopovers(null); });
  document.body.addEventListener("htmx:responseError", function () {
    showToast({ text: "That didn't work. Check the terminal for details." });
  });
  document.body.addEventListener("htmx:afterRequest", function (e) {
    var el = e.detail.elt;
    if (el && el.closest && el.closest(".pop")) closePopovers(null);
  });

  document.addEventListener("click", function (e) {
    var undo = e.target.closest("[data-toast-undo]");
    if (undo) {
      e.preventDefault();
      var url = undo.getAttribute("data-url");
      if (url) htmx.ajax("POST", url, { target: "#main", swap: "innerHTML" });
      document.getElementById("toast").classList.remove("show");
      return;
    }
    var close = e.target.closest("[data-toast-close]");
    if (close) {
      e.preventDefault();
      document.getElementById("toast").classList.remove("show");
      return;
    }
    var copy = e.target.closest("[data-copy]");
    if (copy) {
      e.preventDefault();
      var src = document.querySelector(copy.getAttribute("data-copy"));
      var value = src ? (src.value !== undefined ? src.value : src.textContent) : "";
      var done = function () { showToast({ text: copy.getAttribute("data-copied") || "Copied" }); };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(value).then(done, done);
      } else {
        if (src && src.select) src.select();
        try { document.execCommand("copy"); } catch (err) { /* ignore */ }
        done();
      }
      return;
    }
    var opener = e.target.closest("[data-dialog-open]");
    if (opener) {
      e.preventDefault();
      closePopovers(null);
      var dlg = document.getElementById(opener.getAttribute("data-dialog-open"));
      if (dlg && dlg.showModal) dlg.showModal();
      return;
    }
    var closer = e.target.closest("[data-dialog-close]");
    if (closer) {
      e.preventDefault();
      var d = closer.closest("dialog");
      if (d) d.close();
      return;
    }
    var summary = e.target.closest("details.pd > summary");
    closePopovers(summary ? summary.parentElement : e.target);
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { closePopovers(null); return; }
    var t = e.target;
    if (t && t.matches && t.matches(".chip.add input") && e.key === "Enter") {
      e.preventDefault();
      htmx.trigger(t, "add");
      return;
    }
    if (!document.body.hasAttribute("data-review")) return;
    if (/INPUT|TEXTAREA|SELECT/.test(t.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
    var key = e.key === "?" ? "?" : e.key.toLowerCase();
    var target = document.querySelector('[data-key="' + key + '"]');
    if (!target) return;
    e.preventDefault();
    if (target.tagName === "DETAILS") {
      if (target.hasAttribute("open")) target.removeAttribute("open");
      else target.setAttribute("open", "");
    } else {
      target.click();
    }
  });
})();
