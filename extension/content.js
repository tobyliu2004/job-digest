// Runs on every page. Instead of matching a hard-coded list of job sites
// (endless whack-a-mole), it DETECTS whether the current page is a job posting
// from the page itself, so it works on sites it has never seen.
//
// Detection signals, strongest first:
//   1. A known ATS URL pattern (greenhouse, lever, workday, gh_jid, ...) — instant.
//   2. Schema.org JobPosting structured data (<script type=ld+json>). This is
//      the standard Google requires to list a job in search, so a huge share of
//      job pages — including company-hosted ones — emit it.
//   3. og:type = job.
//   4. Heuristic: an "Apply" button + at least two job-description sections
//      (responsibilities / qualifications / requirements / ...).
//
// Because job boards often load content asynchronously, if nothing is detected
// at first we watch the DOM for a few seconds and re-check.

(function () {
  const HOST_ID = "job-digest-banner-host";
  const PANEL_ID = "job-digest-keywords-host";

  let jobKey = null; // canonical id for the detected job; set by detect()
  let started = false;

  // ---- Detection ---------------------------------------------------------

  function jsonLdJob() {
    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
    for (const s of scripts) {
      let data;
      try { data = JSON.parse(s.textContent); } catch (e) { continue; }
      const nodes = [];
      const push = (d) => {
        if (!d) return;
        if (Array.isArray(d)) d.forEach(push);
        else if (typeof d === "object") {
          nodes.push(d);
          if (d["@graph"]) push(d["@graph"]);
        }
      };
      push(data);
      for (const n of nodes) {
        const t = n["@type"];
        const isJob = t === "JobPosting" || (Array.isArray(t) && t.includes("JobPosting"));
        if (isJob) {
          const ident =
            (n.identifier && (n.identifier.value || n.identifier)) || null;
          return {
            url: n.url || (n.hiringOrganization && n.hiringOrganization.sameAs) || null,
            identifier: ident ? String(ident) : null,
            title: n.title || null,
          };
        }
      }
    }
    return null;
  }

  function heuristicJob() {
    const els = document.querySelectorAll(
      'a,button,input[type="submit"],input[type="button"],[role="button"]'
    );
    let hasApply = false;
    for (const e of els) {
      const t = (e.innerText || e.textContent || e.value || e.getAttribute("aria-label") || "")
        .trim().toLowerCase();
      if (!t || t.length > 40) continue;
      if (/^apply\b/.test(t) || t === "apply now" || t === "submit application" ||
          /apply (for|to) this (job|position|role)/.test(t)) {
        hasApply = true;
        break;
      }
    }
    if (!hasApply) return false;

    const text = ((document.body && (document.body.innerText || document.body.textContent)) || "").toLowerCase();
    const sections = [
      "responsibilities", "qualifications", "requirements", "what you'll do",
      "what you will do", "about the role", "about the job", "about this role",
      "job description", "who you are", "preferred qualifications",
      "minimum qualifications", "basic qualifications", "what we're looking for",
      "equal opportunity", "what you'll bring",
    ];
    let n = 0;
    for (const w of sections) if (text.includes(w)) { if (++n >= 2) return true; }
    return false;
  }

  // Returns {key, title} if this page is a job posting, else null.
  function detect() {
    const href = window.location.href;
    const urlKey = window.JobCanonical.canonicalUrlKey(href);

    // 1. Known ATS URL — most reliable, gives a stable cross-site id.
    if (urlKey && !urlKey.startsWith("url:")) {
      return { key: urlKey, title: document.title };
    }

    // 2. Schema.org JobPosting.
    const jl = jsonLdJob();
    if (jl) {
      let key = urlKey;
      if (jl.url) {
        const k2 = window.JobCanonical.canonicalUrlKey(jl.url);
        if (k2 && !k2.startsWith("url:")) key = k2;
      } else if (jl.identifier) {
        key = "job:" + jl.identifier.toLowerCase();
      }
      return { key, title: jl.title || document.title };
    }

    // 3. Open Graph job type.
    const og = document.querySelector('meta[property="og:type"]');
    if (og && /job/i.test(og.getAttribute("content") || "")) {
      return { key: urlKey, title: document.title };
    }

    // 4. Content heuristic.
    if (heuristicJob()) {
      return { key: urlKey, title: document.title };
    }

    return null;
  }

  function tryStart() {
    if (started) return true;
    if (document.getElementById(HOST_ID)) { started = true; return true; }
    const res = detect();
    if (!res) return false;
    started = true;
    jobKey = res.key;
    chrome.storage.local.get({ applied: {} }, (data) => render(data.applied[jobKey] || null));
    return true;
  }

  // Initial attempt, then watch for async-loaded job content for ~10s.
  if (!tryStart()) {
    let ticks = 0;
    const obs = new MutationObserver(() => {
      if (tryStart() || ++ticks > 60) obs.disconnect();
    });
    obs.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(() => obs.disconnect(), 10000);
  }

  // ---- Applied banner ----------------------------------------------------

  function fmtDate(iso) {
    try {
      return new Date(iso).toLocaleDateString(undefined, {
        year: "numeric", month: "short", day: "numeric",
      });
    } catch (e) { return iso; }
  }

  function render(record) {
    const old = document.getElementById(HOST_ID);
    if (old) old.remove();

    const host = document.createElement("div");
    host.id = HOST_ID;
    host.style.cssText = "position:fixed;top:16px;right:16px;z-index:2147483647;";
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
        .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;}
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
    root.getElementById("toggle").onclick = () => (applied ? unmark() : mark());
    root.getElementById("kw").onclick = showKeywords;

    (document.body || document.documentElement).appendChild(host);
  }

  function mark() {
    chrome.storage.local.get({ applied: {} }, (data) => {
      data.applied[jobKey] = {
        applied_at: new Date().toISOString(),
        url: window.location.href,
        title: (document.title || "").slice(0, 200),
      };
      chrome.storage.local.set({ applied: data.applied }, () => render(data.applied[jobKey]));
    });
  }

  function unmark() {
    chrome.storage.local.get({ applied: {} }, (data) => {
      delete data.applied[jobKey];
      chrome.storage.local.set({ applied: data.applied }, () => render(null));
    });
  }

  // ---- Keyword panel -----------------------------------------------------

  function showKeywords() {
    const old = document.getElementById(PANEL_ID);
    if (old) { old.remove(); return; }

    const text = ((document.body && (document.body.innerText || document.body.textContent)) || "");
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
})();
