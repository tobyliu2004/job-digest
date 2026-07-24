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
          <button class="x" id="kw">🔑 Keywords</button>
          <button class="x" id="dismiss">Dismiss</button>
        </div>
      </div>`;

    root.getElementById("dismiss").onclick = () => host.remove();
    root.getElementById("toggle").onclick = () => {
      if (applied) unmark();
      else mark();
    };
    root.getElementById("kw").onclick = showKeywords;

    (document.body || document.documentElement).appendChild(host);
  }

  // --- Keyword extractor panel ---
  const PANEL_ID = "job-digest-keywords-host";

  function showKeywords() {
    const old = document.getElementById(PANEL_ID);
    if (old) { old.remove(); return; } // toggle off if already open

    const text = (document.body ? document.body.innerText : "") || "";
    const found = window.JobKeywords.extract(text);

    chrome.storage.local.get({ resumeText: "" }, (data) => {
      const { have, missing } = window.JobKeywords.splitByResume(found, data.resumeText);
      renderKeywords(found, have, missing, !!data.resumeText);
    });
  }

  function chips(list, color) {
    if (!list.length) return `<div class="none">none</div>`;
    return list
      .map((k) => `<span class="chip" style="border-color:${color};color:${color}">${k.name}</span>`)
      .join("");
  }

  function renderKeywords(found, have, missing, hasResume) {
    const host = document.createElement("div");
    host.id = PANEL_ID;
    host.style.cssText = "position:fixed;top:16px;right:16px;z-index:2147483647;";
    const root = host.attachShadow({ mode: "open" });

    const body = hasResume
      ? `
        <div class="sec"><div class="lbl" style="color:#b91c1c">➕ Missing from your resume (${missing.length})</div>
          <div class="wrap">${chips(missing, "#b91c1c")}</div>
          ${missing.length ? `<button id="copy" class="copy">Copy missing</button>` : ""}
        </div>
        <div class="sec"><div class="lbl" style="color:#15803d">✓ Already in your resume (${have.length})</div>
          <div class="wrap">${chips(have, "#15803d")}</div>
        </div>`
      : `
        <div class="sec"><div class="lbl">Keywords in this job (${found.length})</div>
          <div class="wrap">${chips(found, "#2557d6")}</div>
        </div>
        <div class="hint">Paste your resume text in the extension popup to see which of these you're missing.</div>`;

    root.innerHTML = `
      <style>
        .card{font:400 12px/1.4 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
              background:#fff;color:#1a1d21;border:1px solid #e3e8ee;border-radius:12px;
              padding:14px;box-shadow:0 10px 34px rgba(0,0,0,.22);width:320px;max-height:70vh;overflow-y:auto;}
        .top{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;}
        .title{font-weight:700;font-size:14px;}
        .close{cursor:pointer;color:#5c6670;border:0;background:none;font-size:16px;}
        .sec{margin-top:12px;}
        .lbl{font-weight:700;font-size:12px;margin-bottom:6px;}
        .wrap{display:flex;flex-wrap:wrap;gap:6px;}
        .chip{border:1px solid;border-radius:999px;padding:3px 9px;font-size:11px;font-weight:600;background:#fff;}
        .none{color:#9aa4af;font-style:italic;}
        .copy{margin-top:8px;font:600 11px/1 inherit;border:0;background:#b91c1c;color:#fff;border-radius:6px;padding:7px 11px;cursor:pointer;}
        .hint{margin-top:10px;color:#5c6670;font-size:11px;}
      </style>
      <div class="card">
        <div class="top"><div class="title">🔑 Job keywords</div>
          <button class="close" id="x">✕</button></div>
        ${body}
      </div>`;

    root.getElementById("x").onclick = () => host.remove();
    const copyBtn = root.getElementById("copy");
    if (copyBtn) {
      copyBtn.onclick = () => {
        navigator.clipboard.writeText(missing.map((k) => k.name).join(", "));
        copyBtn.textContent = "Copied!";
      };
    }
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
