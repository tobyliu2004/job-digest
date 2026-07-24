// Popup: lists jobs you've marked applied, with export/import so you can back
// up the list or move it between machines (and share it with the CLI checker's
// applied.json — same shape: { "<key>": {applied_at, url, title} }).

function fmtDate(iso) {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: "numeric", month: "short", day: "numeric",
    });
  } catch (e) { return iso; }
}

function render() {
  chrome.storage.local.get({ applied: {} }, (data) => {
    const entries = Object.entries(data.applied).sort(
      (a, b) => (b[1].applied_at || "").localeCompare(a[1].applied_at || "")
    );
    document.getElementById("count").textContent =
      `${entries.length} job${entries.length === 1 ? "" : "s"} tracked`;

    const list = document.getElementById("list");
    if (!entries.length) {
      list.innerHTML =
        '<div class="empty">Nothing yet. Open a job posting and click ' +
        '"Mark as applied" on the banner.</div>';
      return;
    }

    list.innerHTML = "";
    for (const [key, rec] of entries) {
      const item = document.createElement("div");
      item.className = "item";
      const title = (rec.title || rec.url || key).replace(/</g, "&lt;");
      item.innerHTML =
        `<a href="${rec.url}" target="_blank" title="${rec.url}">${title}</a>` +
        `<div class="meta"><span>${fmtDate(rec.applied_at)}</span>` +
        `<span class="rm" data-key="${key}">remove</span></div>`;
      list.appendChild(item);
    }
    list.querySelectorAll(".rm").forEach((el) => {
      el.onclick = () => {
        chrome.storage.local.get({ applied: {} }, (d) => {
          delete d.applied[el.dataset.key];
          chrome.storage.local.set({ applied: d.applied }, render);
        });
      };
    });
  });
}

document.getElementById("export").onclick = () => {
  chrome.storage.local.get({ applied: {} }, (data) => {
    const blob = new Blob([JSON.stringify({ jobs: data.applied }, null, 1)], {
      type: "application/json",
    });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "applied.json";
    a.click();
  });
};

document.getElementById("import").onclick = () =>
  document.getElementById("file").click();

document.getElementById("file").onchange = (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const parsed = JSON.parse(reader.result);
      // Accept both {jobs:{...}} (our export / CLI applied.json) and a bare map.
      const incoming = parsed.jobs || parsed;
      chrome.storage.local.get({ applied: {} }, (data) => {
        Object.assign(data.applied, incoming);
        chrome.storage.local.set({ applied: data.applied }, render);
      });
    } catch (err) {
      alert("Could not read that file: " + err.message);
    }
  };
  reader.readAsText(file);
};

render();
