// Injected on every page. Bails immediately unless the current URL is a
// recognised job posting, so it's silent everywhere else.
//
// When it IS a job page, it checks your locally-stored applied list and shows
// a small banner in the top-right: already applied (with date) or not yet,
// plus a one-click mark/unmark button.

(function () {
  const url = window.location.href;
  if (!window.JobCanonical || !window.JobCanonical.isJobPage(url)) return;

  const key = window.JobCanonical.canonicalUrlKey(url);
  const HOST_ID = "job-digest-banner-host";
  if (document.getElementById(HOST_ID)) return; // already injected

  function fmtDate(iso) {
    try {
      return new Date(iso).toLocaleDateString(undefined, {
        year: "numeric", month: "short", day: "numeric",
      });
    } catch (e) { return iso; }
  }

  function render(record) {
    // Remove any previous banner.
    const old = document.getElementById(HOST_ID);
    if (old) old.remove();

    const host = document.createElement("div");
    host.id = HOST_ID;
    host.style.cssText =
      "position:fixed;top:16px;right:16px;z-index:2147483647;";
    const root = host.attachShadow({ mode: "open" });

    const applied = !!record;
    const bg = applied ? "#b91c1c" : "#1f2937";
    const accent = applied ? "#fecaca" : "#93c5fd";
    const heading = applied ? "✗ Already applied" : "○ Not applied yet";
    const detail = applied
      ? `You applied on ${fmtDate(record.applied_at)}. Don't apply again.`
      : "You haven't marked this job as applied.";
    const btnLabel = applied ? "Unmark" : "Mark as applied";

    root.innerHTML = `
      <style>
        .card{font:400 13px/1.4 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
              background:${bg};color:#fff;border-radius:10px;padding:12px 14px;
              box-shadow:0 6px 24px rgba(0,0,0,.28);max-width:280px;}
        .h{font-weight:700;font-size:14px;margin-bottom:3px;}
        .d{opacity:.92;margin-bottom:9px;}
        .row{display:flex;gap:8px;align-items:center;}
        button{font:600 12px/1 inherit;border:0;border-radius:6px;padding:8px 12px;cursor:pointer;}
        .primary{background:#fff;color:${bg};}
        .x{background:transparent;color:${accent};padding:8px 6px;}
      </style>
      <div class="card">
        <div class="h">${heading}</div>
        <div class="d">${detail}</div>
        <div class="row">
          <button class="primary" id="toggle">${btnLabel}</button>
          <button class="x" id="dismiss">Dismiss</button>
        </div>
      </div>`;

    root.getElementById("dismiss").onclick = () => host.remove();
    root.getElementById("toggle").onclick = () => {
      if (applied) unmark();
      else mark();
    };

    (document.body || document.documentElement).appendChild(host);
  }

  function mark() {
    chrome.storage.local.get({ applied: {} }, (data) => {
      data.applied[key] = {
        applied_at: new Date().toISOString(),
        url,
        title: (document.title || "").slice(0, 200),
      };
      chrome.storage.local.set({ applied: data.applied }, () =>
        render(data.applied[key])
      );
    });
  }

  function unmark() {
    chrome.storage.local.get({ applied: {} }, (data) => {
      delete data.applied[key];
      chrome.storage.local.set({ applied: data.applied }, () => render(null));
    });
  }

  chrome.storage.local.get({ applied: {} }, (data) => {
    render(data.applied[key] || null);
  });
})();
