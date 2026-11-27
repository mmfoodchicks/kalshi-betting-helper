// The Research tab (2026-11-27): every registered test, read from /api/research/registry. Display only;
// nothing here can run a read. Dates arrive in UTC and show in Mountain time, the owner's clock.
(function () {
  "use strict";
  var _loaded = false;
  var LABEL = {readable: "Readable now", waiting: "Waiting on data", sealed: "Sealed", read: "Read"};
  var ORDER = ["readable", "waiting", "sealed", "read"];

  function h(x) {
    return String(x == null ? "" : x).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }

  function mt(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return h(iso);
    try {
      return d.toLocaleString("en-US", {timeZone: "America/Denver", month: "short", day: "numeric",
                                        year: "numeric", hour: "numeric", minute: "2-digit"}) + " MT";
    } catch (e) {
      return d.toISOString().slice(0, 16).replace("T", " ") + " UTC";
    }
  }

  function row(e) {
    var flag = e.overdue ? ' <span class="pill neg">overdue</span>'
      : (e.due_soon ? ' <span class="pill warn">due within 14 days</span>' : "");
    var when = e.not_before ? "Not before " + mt(e.not_before) : (e.due_by ? "Due by " + mt(e.due_by) : "");
    var parts = [];
    if (when) parts.push(when);
    if (e.waits_for) parts.push("waits for " + h(e.waits_for));
    var files = [e.registration ? "rule <code>" + h(e.registration) + "</code>" : "",
                 e.script ? "read <code>" + h(e.script) + "</code>" : "",
                 e.report && e.report !== e.registration ? "report <code>" + h(e.report) + "</code>" : ""]
      .filter(Boolean).join(" · ");
    return '<div class="card" style="margin:8px 0;padding:10px 12px">' +
      '<div class="row-between"><b>' + h(e.title) + "</b>" +
      '<span class="sub">' + h(e.area) + "</span></div>" + flag +
      '<p style="margin:6px 0">' + h(e.decides) + "</p>" +
      (parts.length ? '<p class="sub" style="margin:4px 0">' + parts.join("; ") + "</p>" : "") +
      '<p class="sub" style="margin:4px 0;word-break:break-word">' + files + "</p></div>";
  }

  function render(data) {
    var by = {};
    (data.entries || []).forEach(function (e) { (by[e.status] = by[e.status] || []).push(e); });
    var c = data.counts || {};
    document.getElementById("rsCounts").innerHTML = ORDER.filter(function (k) { return c[k]; })
      .map(function (k) { return '<span class="stat">' + h(LABEL[k]) + " <b>" + h(c[k]) + "</b></span>"; })
      .join(" ");
    document.getElementById("rsList").innerHTML = ORDER.filter(function (k) { return (by[k] || []).length; })
      .map(function (k) {
        return "<h3 style=\"margin:14px 0 4px\">" + h(LABEL[k]) + "</h3>" + by[k].map(row).join("");
      }).join("");
  }

  window.initResearch = function () {
    if (_loaded) return;
    _loaded = true;
    var el = document.getElementById("rsList");
    el.innerHTML = '<p class="sub">Loading…</p>';
    fetch("/api/research/registry").then(function (r) { return r.json(); }).then(render).catch(function (err) {
      _loaded = false;
      el.innerHTML = '<p class="sub">Could not load the registry (' + h(err && err.message) + ").</p>";
    });
  };
})();
