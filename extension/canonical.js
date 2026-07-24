// Canonical job-identity logic, ported from src/canonical.py.
//
// This MUST stay in sync with the Python version: the same URL has to produce
// the same key in both places, so a job you marked applied (here) is recognised
// even when you later open it at a slightly different URL — tracking params,
// path slugs, or Workday host shards.

(function () {
  // [regex, formatter] — most specific first. Mirrors _ATS_PATTERNS in canonical.py.
  const PATTERNS = [
    [/greenhouse\.io\/(?:embed\/job_app\?for=)?([a-z0-9_.-]+)\/jobs\/(\d+)/i,
      (m) => `gh:${m[1].toLowerCase()}:${m[2]}`],
    [/lever\.co\/([a-z0-9_.-]+)\/([0-9a-f-]{36})/i,
      (m) => `lv:${m[1].toLowerCase()}:${m[2].toLowerCase()}`],
    [/ashbyhq\.com\/([a-z0-9_.-]+)\/([0-9a-f-]{36})/i,
      (m) => `ab:${m[1].toLowerCase()}:${m[2].toLowerCase()}`],
    [/myworkdayjobs\.com\/.*?_((?:JR|R|REQ)[-_]?\d[\w-]*)/i,
      (m) => `wd:${m[1].toLowerCase().replace(/[-_]/g, "")}`],
    [/google\.com\/about\/careers\/applications\/jobs\/results\/(\d+)|careers\.google\.com\/jobs\/results\/(\d+)/i,
      (m) => `goog:${m[1] || m[2]}`],
    [/jobs\.apple\.com\/[a-z-]+\/details\/(\d+)/i, (m) => `appl:${m[1]}`],
    [/metacareers\.com\/jobs\/(\d+)/i, (m) => `meta:${m[1]}`],
    [/smartrecruiters\.com\/[^/]+\/(\d+)/i, (m) => `sr:${m[1]}`],
    [/amazon\.jobs\/(?:[a-z-]+\/)?jobs\/(\d+)/i, (m) => `amzn:${m[1]}`],
    [/lifeattiktok\.com\/(?:search|position)\/(\d+)/i, (m) => `tt:${m[1]}`],
    [/jobs\.jobvite\.com\/[^/]+\/job\/([a-zA-Z0-9]+)/i, (m) => `jv:${m[1]}`],
    [/icims\.com\/jobs\/(\d+)/i, (m) => `icims:${m[1]}`],
    [/workable\.com\/[^/]*j(?:obs)?\/([A-Z0-9]{8,})/i, (m) => `wk:${m[1].toUpperCase()}`],
    [/linkedin\.com\/jobs\/view\/(?:[^/]*-)?(\d{6,})/i, (m) => `li:${m[1]}`],
  ];

  function canonicalUrlKey(url) {
    if (!url) return "";
    for (const [re, fmt] of PATTERNS) {
      const m = url.match(re);
      if (m) return fmt(m);
    }
    try {
      const u = new URL(url);
      const host = u.hostname.toLowerCase().replace(/^www\./, "");
      const path = u.pathname.replace(/\/+$/, "").toLowerCase();
      return `url:${host}${path}`;
    } catch (e) {
      return "";
    }
  }

  // True only for a recognised ATS/job pattern — NOT the generic url: fallback.
  // The banner appears only on real job pages, never on google.com etc.
  function isJobPage(url) {
    const key = canonicalUrlKey(url);
    return key !== "" && !key.startsWith("url:");
  }

  window.JobCanonical = { canonicalUrlKey, isJobPage };
})();
