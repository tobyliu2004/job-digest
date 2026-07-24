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
    [/([a-z0-9-]+)\.applytojob\.com\/apply\/([A-Za-z0-9]+)/i,
      (m) => `atj:${m[1].toLowerCase()}:${m[2]}`],
    [/ats\.rippling\.com\/(?:[a-z-]+\/)?([a-z0-9_.-]+)\/jobs\/([0-9a-f-]{36})/i,
      (m) => `rip:${m[1].toLowerCase()}:${m[2].toLowerCase()}`],
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

  // Broad job-application-host allowlist. Enumerating every ATS is whack-a-mole,
  // so beyond the specific patterns above we also show the banner on any page
  // whose host is a known ATS/job platform, or whose path clearly names an
  // application. Better to occasionally show where not wanted (you can Dismiss)
  // than to silently miss a real application page.
  const JOB_HOST_RE = new RegExp(
    [
      "applytojob", "jazzhr", "myworkdayjobs", "myworkday", "workday",
      "greenhouse", "lever\\.co", "ashbyhq", "rippling", "workable",
      "bamboohr", "jobvite", "icims", "smartrecruiters", "taleo",
      "successfactors", "paylocity", "paycomonline", "dayforce", "ultipro",
      "breezy\\.hr", "recruitee", "teamtailor", "eightfold", "phenom",
      "avature", "oraclecloud", "adp\\.com", "gr8people", "jobs\\.",
      "careers\\.", "career\\.", "\\.wd\\d", "smartrecruiters",
    ].join("|"),
    "i"
  );
  // True on a recognised ATS/job page. The manifest already restricts this
  // script to load ONLY on job/ATS hosts, so we no longer match on arbitrary
  // paths — just a real per-job ATS id, or (as a backstop) a known job host.
  function isJobPage(url) {
    const key = canonicalUrlKey(url);
    if (key && !key.startsWith("url:")) return true; // known ATS pattern
    try {
      return JOB_HOST_RE.test(new URL(url).hostname);
    } catch (e) {
      return false;
    }
  }

  window.JobCanonical = { canonicalUrlKey, isJobPage };
})();
