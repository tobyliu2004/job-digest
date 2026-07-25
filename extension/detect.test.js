// Job-detection tests for the extension. Run with:
//   npm install jsdom        (one-time)
//   node extension/detect.test.js
//
// Verifies the extension detects job postings generically (structured data,
// og:type, or Apply-button heuristic) and does NOT fire on non-job pages.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const dir = path.join(__dirname, "/");
const canonical = fs.readFileSync(dir + "canonical.js", "utf8");
const keywords = fs.readFileSync(dir + "keywords.js", "utf8");
const content = fs.readFileSync(dir + "content.js", "utf8");

function run(name, url, bodyHtml, expectDetected) {
  const dom = new JSDOM(`<!DOCTYPE html><html><head></head><body>${bodyHtml}</body></html>`,
    { url, runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window;
  w.chrome = { storage: { local: {
    get(def, cb) { cb(typeof def === "object" ? { ...def } : {}); },
    set(o, cb) { cb && cb(); },
  } } };
  try {
    w.eval(canonical); w.eval(keywords); w.eval(content);
  } catch (e) { console.log(`  ERROR ${name}: ${e.message}`); return; }
  const detected = !!w.document.getElementById("job-digest-banner-host");
  const ok = detected === expectDetected;
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}: detected=${detected} (want ${expectDetected})`);
}

// A) JSON-LD JobPosting on an unknown company domain
run("company JSON-LD", "https://acme-startup.com/careers/swe",
  `<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting","title":"Software Engineer","url":"https://acme-startup.com/careers/swe"}</script><h1>SWE</h1>`,
  true);

// B) @graph nested JobPosting
run("@graph JSON-LD", "https://foo.io/jobs/123",
  `<script type="application/ld+json">{"@graph":[{"@type":"WebPage"},{"@type":"JobPosting","title":"Backend Engineer"}]}</script>`,
  true);

// C) Heuristic only: Apply button + two job sections, no structured data
run("heuristic apply+sections", "https://randomco.com/join/42",
  `<h1>Senior Engineer</h1><button>Apply</button><h2>Responsibilities</h2><p>build</p><h2>Qualifications</h2><p>stuff</p>`,
  true);

// D) NON-job page — a blog. Must NOT detect.
run("non-job blog", "https://blog.com/post/why-i-love-cats",
  `<h1>Why I Love Cats</h1><p>Cats are great. Read more articles.</p><a href="/next">Next</a>`,
  false);

// E) Known ATS URL with empty body (URL fast-path)
run("known ATS url", "https://boards.greenhouse.io/appian/jobs/8041237", `<div></div>`, true);

// F) gh_jid embed on company domain (Tower case)
run("gh_jid company embed", "https://tower-research.com/open-positions/?gh_jid=8044334", `<div>loading</div>`, true);

console.log("\n-- adversarial (must NOT detect) --");
// G) "Apply" button but it's a coupon, no job sections
run("apply-coupon page", "https://shop.com/cart",
  `<h1>Your Cart</h1><button>Apply coupon</button><p>Free shipping over $50</p>`, false);
// H) Apply button + only ONE job word (needs >=2)
run("one-section page", "https://x.com/p",
  `<button>Apply</button><h2>Requirements</h2><p>none</p>`, false);
// I) News article mentioning jobs, no apply
run("news article", "https://news.com/tech-hiring",
  `<h1>Tech hiring slows</h1><p>Companies posted fewer responsibilities and qualifications this quarter.</p>`, false);
