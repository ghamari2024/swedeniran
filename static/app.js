const state = {
  activePage: "people",
  peopleView: "main",
  searches: [],
  names: [],
  stats: {},
  people: [],
  peopleTotal: 0,
  peopleOffset: 0,
  peopleLimit: 50,
  filterOptions: null,
  commandPeople: [],
  selectedName: null,
  selectedNames: new Set(),
  workerPaused: false,
  sortKey: "latest_revenue_ksek",
  sortDir: "desc",
  modalPersonId: null,
  modalPerson: null,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
let peopleRefreshTimer = null;

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtMoney(ksek) {
  if (ksek == null || ksek === "") return "—";
  const n = Number(ksek);
  if (Number.isNaN(n)) return "—";
  if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(1)} MSEK`;
  return `${n.toLocaleString()} kSEK`;
}

function fmtList(arr, max = 3) {
  if (!Array.isArray(arr) || !arr.length) return "—";
  const shown = arr.slice(0, max).map((x) => `<span class="pill">${esc(x)}</span>`).join("");
  const extra = arr.length > max ? `<span class="pill muted-pill">+${arr.length - max}</span>` : "";
  return shown + extra;
}

function statusText(status) {
  return {
    idle: "Idle",
    queued: "Queued",
    listing: "Listing",
    listed: "Listed",
    enriching: "Enriching",
    done: "Done",
    stopped: "Stopped",
    error: "Error",
  }[status] || status || "Idle";
}

function genderText(gender) {
  if (gender === "M") return "Male";
  if (gender === "F") return "Female";
  return "Unknown";
}

function unifiedNames() {
  const byName = new Map();
  for (const item of state.names) {
    byName.set(item.name.toLowerCase(), { ...item, source: "library", error: null, fuzzy_suggestions: [] });
  }
  for (const s of state.searches) {
    byName.set(s.query.toLowerCase(), {
      name: s.query,
      search_id: s.id,
      status: s.status,
      persons_listed: s.persons_listed || 0,
      details_done: s.details_done || 0,
      total_persons: s.total_persons || 0,
      source: s.source || "manual",
      scan_mode: s.scan_mode || "fast",
      scan_completed_mode: s.scan_completed_mode || null,
      scanned_pages: s.scanned_pages || 0,
      error: s.error,
      fuzzy_suggestions: s.fuzzy_suggestions || [],
    });
  }
  return Array.from(byName.values()).sort((a, b) => a.name.localeCompare(b.name));
}

async function refreshAll() {
  try {
    const [searchData, nameData, statsData] = await Promise.all([
      api("/api/searches"),
      api("/api/names"),
      api("/api/stats"),
    ]);
    state.searches = searchData.searches;
    state.workerPaused = searchData.worker_paused;
    state.names = nameData.names;
    state.stats = statsData;

    if (state.activePage === "people") {
      if (!state.filterOptions) state.filterOptions = await api("/api/people/enriched/options");
      await refreshPeopleData(false);
      if (state.peopleView === "favorites") refreshCompanyDeepStatus();
    } else {
      renderCommandCenter();
      if (state.selectedName) await renderCommandDetail(state.selectedName.name);
    }
    renderGlobal();
  } catch (err) {
    console.warn("refresh failed", err);
  }
}

function renderGlobal() {
  $("#pauseBtn").textContent = state.workerPaused ? "Resume worker" : "Pause worker";
  $("#peopleTab").classList.toggle("active", state.activePage === "people");
  $("#commandTab").classList.toggle("active", state.activePage === "command");
  $("#peoplePage").classList.toggle("active", state.activePage === "people");
  $("#commandPage").classList.toggle("active", state.activePage === "command");
}

function filtersAreActive() {
  const el = document.activeElement;
  if (!el) return false;
  if (el.id === "textFilter") return true;
  return el.matches?.(
    "#yearFilter, #industryFilter, #countyFilter, #typeFilter, #genderFilter, " +
    "#revMin, #revMax, #empMin, #empMax, #ageMin, #ageMax, " +
    "#hasRevenue, #activeOnly, #hasEmployees"
  );
}

function renderPeoplePage() {
  if (!filtersAreActive()) rebuildFilterOptions();
  renderPeopleTable();
}

async function refreshPeopleData(resetPage = false) {
  if (resetPage) state.peopleOffset = 0;
  const keepScroll = !resetPage;
  if (keepScroll) savePeopleScroll();
  const params = peopleQueryParams();
  const data = await api(`/api/people/enriched?${params.toString()}`);
  state.people = data.persons || [];
  state.peopleTotal = data.total || 0;
  state.peopleLimit = data.limit || state.peopleLimit;
  state.peopleOffset = data.offset || 0;
  renderPeoplePage();
  if (keepScroll) restorePeopleScroll();
}

function peopleQueryParams() {
  const f = filters();
  const params = new URLSearchParams({
    limit: String(state.peopleLimit),
    offset: String(state.peopleOffset),
    sort_key: state.sortKey,
    sort_dir: state.sortDir,
  });
  const add = (key, value) => {
    if (value !== null && value !== undefined && value !== "") params.set(key, String(value));
  };
  add("rev_min", f.revMin);
  add("rev_max", f.revMax);
  add("emp_min", f.empMin);
  add("emp_max", f.empMax);
  add("age_min", f.ageMin);
  add("age_max", f.ageMax);
  add("year", f.year);
  add("industry", f.industry);
  add("county", f.county);
  add("company_type", f.type);
  add("gender", f.gender);
  add("text", f.text);
  if (f.hasRevenue) params.set("has_revenue", "true");
  if (f.activeOnly) params.set("active_only", "true");
  if (f.hasEmployees) params.set("has_employees", "true");
  params.set("view", state.peopleView || "main");
  return params;
}

function rebuildFilterOptions() {
  const opts = state.filterOptions || {};
  fillSelect("#yearFilter", "Any year", opts.years || []);
  fillSelect("#industryFilter", "Any industry", opts.industries || []);
  fillSelect("#countyFilter", "Any county", opts.counties || []);
  fillSelect("#typeFilter", "Any type", opts.company_types || []);
}

function fillSelect(sel, label, values) {
  const el = $(sel);
  const signature = JSON.stringify(values);
  if (el.dataset.optionsSignature === signature) return;
  const previous = el.value;
  const options = values.map((v) => {
    if (v && typeof v === "object") {
      return `<option value="${esc(v.value)}">${esc(v.label)}</option>`;
    }
    return `<option value="${esc(v)}">${esc(v)}</option>`;
  });
  el.innerHTML = `<option value="">${label}</option>` + options.join("");
  el.dataset.optionsSignature = signature;
  const valid = values.map((v) => String(v && typeof v === "object" ? v.value : v));
  if (valid.includes(previous)) el.value = previous;
}

function filters() {
  const number = (id) => {
    const v = $(id).value;
    return v === "" ? null : Number(v);
  };
  return {
    revMin: number("#revMin"),
    revMax: number("#revMax"),
    empMin: number("#empMin"),
    empMax: number("#empMax"),
    ageMin: number("#ageMin"),
    ageMax: number("#ageMax"),
    year: $("#yearFilter").value,
    industry: $("#industryFilter").value,
    county: $("#countyFilter").value,
    type: $("#typeFilter").value,
    gender: $("#genderFilter").value,
    hasRevenue: $("#hasRevenue").checked,
    activeOnly: $("#activeOnly").checked,
    hasEmployees: $("#hasEmployees").checked,
    text: $("#textFilter").value.trim().toLowerCase(),
  };
}

function clearAllFilters() {
  $("#textFilter").value = "";
  $("#revMin").value = "";
  $("#revMax").value = "";
  $("#yearFilter").value = "";
  $("#industryFilter").value = "";
  $("#countyFilter").value = "";
  $("#typeFilter").value = "";
  $("#empMin").value = "";
  $("#empMax").value = "";
  $("#ageMin").value = "";
  $("#ageMax").value = "";
  $("#genderFilter").value = "";
  $("#hasRevenue").checked = false;
  $("#activeOnly").checked = false;
  $("#hasEmployees").checked = false;
  refreshPeopleData(true);
}

function renderFilterSummary(f = filters()) {
  const items = [];
  if (f.revMin != null) items.push(`Revenue >= ${f.revMin}M`);
  if (f.revMax != null) items.push(`Revenue <= ${f.revMax}M`);
  if (f.year) items.push(`Year ${f.year}`);
  if (f.industry) items.push(f.industry);
  if (f.county) items.push(f.county);
  if (f.type) items.push(f.type);
  if (f.empMin != null) items.push(`Employees >= ${f.empMin}`);
  if (f.empMax != null) items.push(`Employees <= ${f.empMax}`);
  if (f.ageMin != null) items.push(`Age >= ${f.ageMin}`);
  if (f.ageMax != null) items.push(`Age <= ${f.ageMax}`);
  if (f.gender) items.push(f.gender === "M" ? "Male" : "Female");
  if (f.hasRevenue) items.push("Has revenue");
  if (f.activeOnly) items.push("Active");
  if (f.hasEmployees) items.push("Has employees");
  if (f.text) items.push(`Text: ${f.text}`);
  $("#activeFilterCount").textContent = items.length;
  $("#filterSummary").textContent = items.length ? items.slice(0, 4).join(" · ") + (items.length > 4 ? ` +${items.length - 4}` : "") : "No filters active";
  $("#clearFiltersBtn")?.classList.toggle("hidden", items.length === 0);
}

function filteredPeople() {
  renderFilterSummary(filters());
  return state.people;
}

function googleSearchUrl(name) {
  return `https://www.google.com/search?q=${encodeURIComponent(name || "")}`;
}

function scoreBadge(score) {
  if (score == null) return "";
  const tier = score >= 70 ? "hi" : score >= 45 ? "mid" : "lo";
  return `<sup class="iran-score ${tier}" title="Iranian likelihood score">${score}</sup>`;
}

function auditorBadge(p) {
  if (!p || !p.is_auditor) return "";
  return `<span class="tag-badge auditor-badge" title="Holds an auditor role">Auditor</span>`;
}

function renderPeopleActions(p) {
  const view = state.peopleView || "main";
  const favClass = p.is_favorite ? "row-action active-favorite" : "row-action";
  const parts = [];
  if (view === "main") {
    parts.push(`<button type="button" class="row-action spam-action" data-action="spam" data-id="${esc(p.person_id)}" title="Mark as spam">🚫</button>`);
  }
  if (view === "spam") {
    parts.push(`<button type="button" class="row-action restore-action" data-action="restore" data-id="${esc(p.person_id)}" title="Restore to main">↩</button>`);
  }
  parts.push(`<button type="button" class="${favClass}" data-action="favorite" data-id="${esc(p.person_id)}" data-favorite="${p.is_favorite ? "1" : "0"}" title="${p.is_favorite ? "Remove favorite" : "Add favorite"}">${p.is_favorite ? "♥" : "♡"}</button>`);
  parts.push(`<a class="row-action google-action" href="${googleSearchUrl(p.name)}" target="_blank" rel="noopener" title="Google search">G</a>`);
  return `<div class="row-actions">${parts.join("")}</div>`;
}

function renderPeopleTable() {
  const rows = filteredPeople();
  const start = state.peopleTotal ? state.peopleOffset + 1 : 0;
  const end = Math.min(state.peopleOffset + rows.length, state.peopleTotal);
  const page = Math.floor(state.peopleOffset / state.peopleLimit) + 1;
  const pages = Math.max(1, Math.ceil(state.peopleTotal / state.peopleLimit));
  $("#visibleCount").textContent = `Showing ${start}-${end} of ${state.peopleTotal} people`;
  $("#pageStatus").textContent = `Page ${page} / ${pages}`;
  $("#prevPageBtn").disabled = state.peopleOffset <= 0;
  $("#firstPageBtn").disabled = state.peopleOffset <= 0;
  $("#nextPageBtn").disabled = state.peopleOffset + state.peopleLimit >= state.peopleTotal;
  $("#lastPageBtn").disabled = state.peopleOffset + state.peopleLimit >= state.peopleTotal;
  $("#pageSize").value = String(state.peopleLimit);
  const body = $("#peopleBody");
  if (!rows.length) {
    const emptyLabel = { main: "No matching people.", spam: "No spam people.", favorites: "No favorites yet.", auditor: "No auditors found." }[state.peopleView] || "No matching people.";
    body.innerHTML = `<tr><td colspan="7" class="empty">${emptyLabel}</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((p) => `
    <tr class="rich-row person-row${p.is_favorite ? " favorite-row" : ""}" data-person-id="${esc(p.person_id)}">
      <td class="person-cell rich-person">
        <span class="person-open">${esc(p.name)}</span>${scoreBadge(p.iranian_score)}${auditorBadge(p)}
        <small>${p.age ?? "—"} yrs · ${genderText(p.gender)} · ${p.number_of_roles ?? "—"} roles · source ${esc(p.search_query || "")}</small>
        <small class="id-line">${p.person_id}</small>
      </td>
      <td class="quick-actions-cell">${renderPeopleActions(p)}</td>
      <td class="metric-cell">
        <strong>${fmtMoney(p.latest_revenue_ksek)}</strong>
        <small>${p.total_profit_ksek == null ? "profit —" : `profit ${fmtMoney(p.total_profit_ksek)}`}</small>
      </td>
      <td class="metric-cell">
        <strong>${p.latest_year || "—"}</strong>
        <small><span class="status ${p.detail_status}">${statusText(p.detail_status)}</span></small>
      </td>
      <td class="metric-cell">
        <strong>${p.employees_total ?? "—"} emp</strong>
        <small>${p.company_count ?? "—"} companies · ${p.active_company_count ?? 0} active · max ${p.employees_max ?? "—"}</small>
      </td>
      <td class="chips-cell">${fmtList(p.industries, 4)}<small>${fmtList(p.company_types, 2)}</small></td>
      <td class="chips-cell">${fmtList(p.counties, 3)}<small>${fmtList(p.municipalities, 2)}</small></td>
    </tr>
  `).join("");
  body.querySelectorAll("[data-action]").forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      handlePersonAction(btn.dataset.action, btn.dataset.id, btn.dataset.favorite === "1");
    };
  });
  body.querySelectorAll("tr.person-row").forEach((row) => {
    row.onclick = (e) => {
      if (e.target.closest(".quick-actions-cell, .row-actions, a, button")) return;
      openPersonModal(row.dataset.personId);
    };
  });
}

function peopleTableScrollEl() {
  return document.querySelector(".people-table-wrap") || document.documentElement;
}

function savePeopleScroll() {
  state.peopleScrollTop = window.scrollY || peopleTableScrollEl().scrollTop || 0;
}

function restorePeopleScroll() {
  if (state.peopleScrollTop == null) return;
  window.scrollTo(0, state.peopleScrollTop);
  peopleTableScrollEl().scrollTop = state.peopleScrollTop;
}

function renderModalActions(p) {
  const view = state.peopleView || "main";
  const favLabel = p.is_favorite ? "Remove favorite" : "Add favorite";
  const parts = [];
  if (view === "main" || view === "auditor") {
    parts.push(`<button type="button" class="ghost small modal-action" data-modal-action="spam">Mark spam</button>`);
  }
  if (view === "spam") {
    parts.push(`<button type="button" class="ghost small modal-action" data-modal-action="restore">Restore to main</button>`);
  }
  parts.push(`<button type="button" class="ghost small modal-action${p.is_favorite ? " active-favorite" : ""}" data-modal-action="favorite">${p.is_favorite ? "♥ Favorited" : "♡ Favorite"}</button>`);
  if (p.person_url) {
    parts.push(`<a class="ghost small modal-action" href="${esc(p.person_url)}" target="_blank" rel="noopener">Open on allabolag</a>`);
  }
  parts.push(`<a class="ghost small modal-action" href="${googleSearchUrl(p.name)}" target="_blank" rel="noopener">Google person</a>`);
  return `<div class="person-modal-actions">${parts.join("")}</div>`;
}

function confidenceBadge(level) {
  if (!level) return "";
  const cls = level === "high" ? "hi" : level === "medium" ? "mid" : "lo";
  return `<span class="conf-badge ${cls}" title="Confidence: ${esc(level)}">${esc(level)}</span>`;
}

function renderCompanyIntel(intel) {
  if (!intel) return "";
  const rows = [];
  if (intel.website) {
    rows.push(`<div class="intel-row"><span class="intel-k">Website</span><a href="${esc(intel.website)}" target="_blank" rel="noopener">${esc(intel.website)}</a>${confidenceBadge(intel.website_confidence)}</div>`);
  }
  if (intel.linkedin_url) {
    rows.push(`<div class="intel-row"><span class="intel-k">LinkedIn</span><a href="${esc(intel.linkedin_url)}" target="_blank" rel="noopener">Company page</a>${confidenceBadge(intel.linkedin_confidence)}</div>`);
  }
  const socials = intel.socials || {};
  const socialLinks = Object.entries(socials)
    .filter(([k]) => k !== "linkedin")
    .map(([k, v]) => `<a class="intel-social" href="${esc(v)}" target="_blank" rel="noopener">${esc(k)}</a>`)
    .join("");
  if (socialLinks) {
    rows.push(`<div class="intel-row"><span class="intel-k">Social</span><span class="intel-socials">${socialLinks}</span></div>`);
  }
  const email = intel.email || (intel.website_emails && intel.website_emails[0]);
  if (email) {
    rows.push(`<div class="intel-row"><span class="intel-k">Email</span><a href="mailto:${esc(email)}">${esc(email)}</a></div>`);
  }
  if (intel.phone) {
    rows.push(`<div class="intel-row"><span class="intel-k">Phone</span><a href="tel:${esc(intel.phone)}">${esc(intel.phone)}</a></div>`);
  }
  if (intel.address) {
    rows.push(`<div class="intel-row"><span class="intel-k">Address</span><span>${esc(intel.address)}</span></div>`);
  }
  const about = intel.description || intel.purpose;
  if (about) {
    rows.push(`<div class="intel-row intel-about"><span class="intel-k">About</span><span>${esc(about)}</span></div>`);
  }
  if (Array.isArray(intel.certifications) && intel.certifications.length) {
    rows.push(`<div class="intel-row"><span class="intel-k">Certs</span><span>${esc(intel.certifications.join(", "))}</span></div>`);
  }
  if (Array.isArray(intel.news) && intel.news.length) {
    const items = intel.news.slice(0, 5).map((n) => `<li>${esc(n.title)}${n.date ? ` <small>${esc(n.date)}</small>` : ""}</li>`).join("");
    rows.push(`<div class="intel-row intel-about"><span class="intel-k">Registry events</span><ul class="intel-news">${items}</ul></div>`);
  }
  if (!rows.length) return "";
  return `<div class="company-intel"><div class="company-intel-head">Deep intel${intel.search_provider ? ` <small>via ${esc(intel.search_provider)}</small>` : ""}</div>${rows.join("")}</div>`;
}

function renderRolePills(c) {
  const roles = Array.isArray(c.roles) && c.roles.length ? c.roles : c.role ? [c.role] : [];
  if (!roles.length) return `<span class="pill role-pill">—</span>`;
  const primary = c.role || roles[0];
  const multi = roles.length > 1;
  const pills = roles.map((r) => {
    const isPrimary = r === primary;
    const label = isPrimary && multi ? ` <small>Current</small>` : "";
    return `<span class="pill role-pill${isPrimary ? " role-current" : ""}">${esc(r)}${label}</span>`;
  });
  return `<div class="role-pills">${pills.join("")}</div>`;
}

function renderCompanyCard(c) {
  const url = c.allabolag_url || (c.orgnr ? `https://www.allabolag.se/${c.orgnr}` : "");
  return `
    <article class="company-card">
      <div class="company-card-head">
        <div class="company-card-title">
          <strong>${esc(c.company_name || "—")}</strong>
          <a class="row-action google-action" href="${googleSearchUrl(c.company_name)}" target="_blank" rel="noopener" title="Google company">G</a>
        </div>
        ${renderRolePills(c)}
      </div>
      <div class="company-card-metrics">
        <span><strong>Revenue</strong> ${fmtMoney(c.revenue_ksek)}${c.revenue_year ? ` <small>(${esc(c.revenue_year)})</small>` : ""}</span>
        <span><strong>Profit</strong> ${c.profit_ksek == null ? "—" : fmtMoney(c.profit_ksek)}</span>
        <span><strong>Status</strong> ${esc(c.status || "—")}</span>
        <span><strong>Employees</strong> ${esc(c.employees ?? "—")}</span>
      </div>
      <div class="company-card-meta">
        ${c.municipality || c.county ? `<span>${esc([c.municipality, c.county].filter(Boolean).join(", "))}</span>` : ""}
        ${c.company_type ? `<span>${esc(c.company_type)}</span>` : ""}
        ${c.orgnr ? `<span>Org ${esc(c.orgnr)}</span>` : ""}
      </div>
      ${fmtList(c.industries, 4) !== "—" ? `<div class="company-card-chips">${fmtList(c.industries, 4)}</div>` : ""}
      ${renderCompanyIntel(c.intel)}
      ${url ? `<a class="company-link" href="${esc(url)}" target="_blank" rel="noopener">View company on allabolag</a>` : ""}
    </article>
  `;
}

function renderPersonModalContent(p) {
  const source = p.search_query || (state.people.find((x) => x.person_id === p.person_id) || {}).search_query || "";
  const companies = Array.isArray(p.companies) ? [...p.companies].sort((a, b) => (b.revenue_ksek || 0) - (a.revenue_ksek || 0)) : [];
  const isDone = p.detail_status === "done";

  $("#personModalTitleWrap").innerHTML = `
    <div class="eyebrow">Person profile</div>
    <h2 id="personModalTitle">${esc(p.name)}${scoreBadge(p.iranian_score)}${auditorBadge(p)}</h2>
    <p class="muted person-modal-sub">
      ${p.age ?? "—"} yrs · ${genderText(p.gender)} · ${p.number_of_roles ?? "—"} roles
      ${source ? ` · source ${esc(source)}` : ""}
      · <span class="status ${p.detail_status}">${statusText(p.detail_status)}</span>
    </p>
    <small class="id-line">${esc(p.person_id)}</small>
  `;

  let body = renderModalActions(p);

  if (!isDone) {
    if (p.detail_status === "error") {
      body += `<div class="person-modal-notice error-notice"><strong>Enrichment failed</strong><p>${esc(p.error || "Unknown error")}</p></div>`;
    } else {
      body += `<div class="person-modal-notice"><strong>Enrichment in progress</strong><p>Full company details will appear here once enrichment completes.</p></div>`;
    }
    $("#personModalBody").innerHTML = body;
    wireModalActions(p);
    return;
  }

  body += `
    <section class="person-modal-section">
      <h3>Overview</h3>
      <div class="person-stats-grid">
        <div class="stat-box"><small>Total revenue</small><strong>${fmtMoney(p.latest_revenue_ksek)}</strong></div>
        <div class="stat-box"><small>Total profit</small><strong>${p.total_profit_ksek == null ? "—" : fmtMoney(p.total_profit_ksek)}</strong></div>
        <div class="stat-box"><small>Latest year</small><strong>${p.latest_year || "—"}</strong></div>
        <div class="stat-box"><small>Companies</small><strong>${p.company_count ?? companies.length} <small>(${p.active_company_count ?? 0} active)</small></strong></div>
        <div class="stat-box"><small>Employees</small><strong>${p.employees_total ?? "—"} <small>max ${p.employees_max ?? "—"}</small></strong></div>
      </div>
      <div class="person-modal-chips">
        ${fmtList(p.industries, 6)}
        ${fmtList(p.counties, 4)}
        ${fmtList(p.municipalities, 3)}
        ${fmtList(p.company_types, 3)}
      </div>
    </section>
    <section class="person-modal-section">
      <h3>Companies (${companies.length})</h3>
      <div class="company-list">
        ${companies.length ? companies.map(renderCompanyCard).join("") : `<p class="muted">No company records stored.</p>`}
      </div>
    </section>
  `;
  $("#personModalBody").innerHTML = body;
  wireModalActions(p);
}

function wireModalActions(p) {
  $$("#personModalBody .modal-action[data-modal-action]").forEach((btn) => {
    btn.onclick = () => handleModalPersonAction(btn.dataset.modalAction, p);
  });
}

async function handleModalPersonAction(action, person) {
  const personId = person.person_id;
  try {
    if (action === "spam") {
      await api(`/api/persons/${encodeURIComponent(personId)}/spam`, { method: "POST" });
      closePersonModal();
      savePeopleScroll();
      await refreshPeopleData(false);
      restorePeopleScroll();
      return;
    }
    if (action === "restore") {
      await api(`/api/persons/${encodeURIComponent(personId)}/restore`, { method: "POST" });
      closePersonModal();
      savePeopleScroll();
      await refreshPeopleData(false);
      restorePeopleScroll();
      return;
    }
    if (action === "favorite") {
      await api(`/api/persons/${encodeURIComponent(personId)}/favorite`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ favorite: !person.is_favorite }),
      });
      const updated = await api(`/api/persons/${encodeURIComponent(personId)}`);
      const row = state.people.find((x) => x.person_id === personId);
      if (row) {
        row.is_favorite = updated.is_favorite;
      }
      state.modalPerson = updated;
      renderPersonModalContent(updated);
      savePeopleScroll();
      await refreshPeopleData(false);
      restorePeopleScroll();
    }
  } catch (err) {
    console.error(err);
  }
}

function openPersonModal(personId) {
  savePeopleScroll();
  state.modalPersonId = personId;
  $("#personModalOverlay").classList.remove("hidden");
  $("#personModalOverlay").setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
  $("#personModalBody").innerHTML = `<p class="empty">Loading profile…</p>`;
  $("#personModalTitleWrap").innerHTML = `<h2 id="personModalTitle">Loading…</h2>`;
  api(`/api/persons/${encodeURIComponent(personId)}`)
    .then((person) => {
      if (state.modalPersonId !== personId) return;
      const row = state.people.find((x) => x.person_id === personId);
      if (row?.search_query) person.search_query = row.search_query;
      state.modalPerson = person;
      renderPersonModalContent(person);
    })
    .catch((err) => {
      $("#personModalBody").innerHTML = `<p class="empty">Failed to load profile: ${esc(err.message)}</p>`;
    });
}

function closePersonModal() {
  state.modalPersonId = null;
  state.modalPerson = null;
  $("#personModalOverlay").classList.add("hidden");
  $("#personModalOverlay").setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
  restorePeopleScroll();
}

async function handlePersonAction(action, personId, isFavorite) {
  try {
    savePeopleScroll();
    if (action === "spam") {
      await api(`/api/persons/${encodeURIComponent(personId)}/spam`, { method: "POST" });
    } else if (action === "restore") {
      await api(`/api/persons/${encodeURIComponent(personId)}/restore`, { method: "POST" });
    } else if (action === "favorite") {
      await api(`/api/persons/${encodeURIComponent(personId)}/favorite`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ favorite: !isFavorite }),
      });
    }
    await refreshPeopleData(false);
  } catch (err) {
    console.error(err);
  }
}

function setPeopleView(view) {
  state.peopleView = view;
  $$(".view-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === view));
  $("#favoritesActionBar")?.classList.toggle("hidden", view !== "favorites");
  if (view === "favorites") refreshCompanyDeepStatus();
  refreshPeopleData(true);
}

async function refreshCompanyDeepStatus() {
  try {
    const s = await api("/api/company-deep/status");
    const el = $("#companyDeepStatus");
    if (!el) return;
    const done = s.done || 0;
    const queued = s.queued || 0;
    const running = s.running || 0;
    const error = s.error || 0;
    const total = s.favorites_total || 0;
    const parts = [`${done}/${total} done`];
    if (running) parts.push(`${running} running`);
    if (queued) parts.push(`${queued} queued`);
    if (error) parts.push(`${error} error`);
    el.textContent = parts.join(" · ");
  } catch (err) {
    /* ignore */
  }
}

function comparePeople(a, b) {
  const key = state.sortKey;
  const av = sortValue(a[key]);
  const bv = sortValue(b[key]);
  const result = av > bv ? 1 : av < bv ? -1 : 0;
  return state.sortDir === "asc" ? result : -result;
}

function sortValue(v) {
  if (Array.isArray(v)) return v.join(", ").toLowerCase();
  if (v == null || v === "") return -Infinity;
  if (typeof v === "string") return v.toLowerCase();
  return v;
}

function renderCommandCenter() {
  renderMetrics();
  renderCommandTable();
}

function renderMetrics() {
  const s = state.stats || {};
  const ps = s.person_statuses || {};
  const all = unifiedNames();
  const byStatus = (status) => all.filter((x) => x.status === status).length;
  const cards = [
    ["Total names", all.length],
    ["New / unlisted", byStatus("idle")],
    ["Listing", byStatus("listing")],
    ["Listed", byStatus("listed")],
    ["Enriching", byStatus("enriching")],
    ["Done names", byStatus("done")],
    ["Stopped", byStatus("stopped")],
    ["Errors", all.filter((x) => x.error || x.status === "error").length],
    ["Fast scanned", all.filter((x) => (x.scan_completed_mode || x.scan_mode || "fast") === "fast").length],
    ["Full scanned", all.filter((x) => (x.scan_completed_mode || x.scan_mode) === "full").length],
    ["People listed", s.total_people_listed || 0],
    ["People enriched", s.total_people_enriched || 0],
    ["Pending enrich", s.pending_enrich || ps.pending || 0],
    ["Worker", state.workerPaused ? "Paused" : "Running"],
  ];
  $("#metricsGrid").innerHTML = cards.map(([label, value]) => `
    <div class="metric-card">
      <small>${label}</small>
      <strong>${value}</strong>
    </div>
  `).join("");
}

function commandNames() {
  const query = $("#commandNameFilter").value.trim().toLowerCase();
  const status = $("#commandStatusFilter").value;
  const source = $("#commandSourceFilter").value;
  const scan = $("#commandScanFilter").value;
  const errorsOnly = $("#commandErrorsOnly").checked;
  const listedOnly = $("#commandListedOnly").checked;
  return unifiedNames().filter((item) => {
    if (query && !item.name.toLowerCase().includes(query)) return false;
    if (status && item.status !== status) return false;
    if (source && item.source !== source) return false;
    if (scan && (item.scan_completed_mode || item.scan_mode || "fast") !== scan) return false;
    if (errorsOnly && !item.error) return false;
    if (listedOnly && !item.persons_listed) return false;
    return true;
  });
}

function scanBadge(item) {
  const mode = item.scan_completed_mode || item.scan_mode || "fast";
  const label = mode === "full" ? "Full" : "Fast";
  const pages = item.scanned_pages ? ` · ${item.scanned_pages}p` : "";
  return `<span class="status ${mode === "full" ? "done" : "listed"}">${label}</span><small>${pages}</small>`;
}

function renderCommandTable() {
  const rows = commandNames();
  $("#commandVisibleCount").textContent = `${rows.length} names`;
  const body = $("#commandBody");
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="9" class="empty">No names match.</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((item) => `
    <tr class="${state.selectedName?.name === item.name ? "selected-row" : ""}">
      <td><input class="name-check" type="checkbox" value="${esc(item.name)}" ${state.selectedNames.has(item.name) ? "checked" : ""}></td>
      <td><button class="link-button name-open" data-name="${esc(item.name)}">${esc(item.name)}</button><small>${esc(item.source || "")}</small></td>
      <td><span class="status ${item.status}">${statusText(item.status)}</span></td>
      <td>${scanBadge(item)}</td>
      <td>${item.persons_listed || 0}</td>
      <td>${item.details_done || 0}</td>
      <td>${(item.fuzzy_suggestions || []).length}</td>
      <td class="error-cell">${esc(item.error || "—")}</td>
      <td class="row-actions">
        ${item.search_id ? `<button data-action="open" data-name="${esc(item.name)}" class="ghost small">Open</button>` : ""}
        <button data-action="list" data-name="${esc(item.name)}" class="ghost small">${item.status === "stopped" ? "Resume" : "List"}</button>
        <button data-action="full-scan" data-name="${esc(item.name)}" class="ghost small">Full scan</button>
        <button data-action="enrich" data-name="${esc(item.name)}" class="ghost small">Enrich</button>
        <button data-action="stop" data-name="${esc(item.name)}" class="danger small">Stop</button>
        ${item.search_id ? `<button data-action="delete" data-name="${esc(item.name)}" class="danger small">Delete</button>` : ""}
      </td>
    </tr>
  `).join("");
  $$(".name-check").forEach((cb) => {
    cb.onchange = () => {
      if (cb.checked) state.selectedNames.add(cb.value);
      else state.selectedNames.delete(cb.value);
    };
  });
  $$(".name-open").forEach((btn) => {
    btn.onclick = () => openCommandName(btn.dataset.name);
  });
  $$(".row-actions button").forEach((btn) => {
    btn.onclick = () => handleNameAction(btn.dataset.action, btn.dataset.name);
  });
}

async function openCommandName(name) {
  const item = unifiedNames().find((x) => x.name === name);
  state.selectedName = item || { name };
  if (item?.search_id) {
    const data = await api(`/api/searches/${item.search_id}/persons`);
    state.commandPeople = data.persons;
  } else {
    state.commandPeople = [];
  }
  renderCommandDetail(name);
  renderCommandTable();
}

async function renderCommandDetail(name) {
  const item = unifiedNames().find((x) => x.name === name) || state.selectedName;
  if (!item) {
    $("#commandDetail").innerHTML = `<p class="empty">Select a name to inspect listed people and fuzzy suggestions.</p>`;
    return;
  }
  if (item.search_id && !state.commandPeople.length) {
    try {
      state.commandPeople = (await api(`/api/searches/${item.search_id}/persons`)).persons;
    } catch {
      state.commandPeople = [];
    }
  }
  const existing = new Set(unifiedNames().map((x) => x.name.toLowerCase()));
  const fuzzy = (item.fuzzy_suggestions || []).filter((x) => !existing.has(x.toLowerCase()));
  $("#commandDetail").innerHTML = `
    <div class="detail-head">
      <div>
        <div class="eyebrow">Selected name</div>
        <h3>${esc(item.name)}</h3>
        <p class="muted">${item.persons_listed || 0} listed · ${item.details_done || 0} enriched · ${statusText(item.status)} · ${(item.scan_completed_mode || item.scan_mode || "fast").toUpperCase()} scan · ${item.scanned_pages || 0} pages</p>
      </div>
      <div class="row-actions">
        <button data-action="list" data-name="${esc(item.name)}" class="ghost small">List</button>
        <button data-action="full-scan" data-name="${esc(item.name)}" class="ghost small">Full scan</button>
        <button data-action="enrich" data-name="${esc(item.name)}" class="primary small">Enrich</button>
        <button data-action="stop" data-name="${esc(item.name)}" class="danger small">Stop</button>
        ${item.search_id ? `<button data-action="delete" data-name="${esc(item.name)}" class="danger small">Delete</button>` : ""}
      </div>
    </div>
    <h4>Related fuzzy names</h4>
    <div class="fuzzy-chips">
      ${fuzzy.length ? fuzzy.map((x) => `<button class="fuzzy-chip" data-name="${esc(x)}"><span>${esc(x)}</span><small>List exact matches</small></button>`).join("") : `<span class="muted">No unused fuzzy suggestions.</span>`}
    </div>
    <h4>Listed people</h4>
    <div class="mini-people">
      ${state.commandPeople.length ? state.commandPeople.slice(0, 80).map((p) => `
        <a href="${esc(p.person_url || "#")}" target="_blank" rel="noopener">
          <strong>${esc(p.name)}</strong>
          <span>${p.age ?? "—"} yrs · ${genderText(p.gender)} · ${statusText(p.detail_status)}</span>
        </a>
      `).join("") : `<p class="muted">No people listed yet.</p>`}
    </div>
  `;
  $$("#commandDetail .row-actions button").forEach((btn) => {
    btn.onclick = () => handleNameAction(btn.dataset.action, btn.dataset.name);
  });
  $$("#commandDetail .fuzzy-chip").forEach((btn) => {
    btn.onclick = async () => {
      await queueName(btn.dataset.name, "fuzzy");
      await openCommandName(item.name);
    };
  });
}

async function queueName(name, source = "manual") {
  const search = await api("/api/searches", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query: name, source }),
  });
  state.selectedName = { name: search.query, search_id: search.id };
  await refreshAll();
}

async function handleNameAction(action, name) {
  const item = unifiedNames().find((x) => x.name === name);
  if (action === "open") {
    $("#textFilter").value = name;
    return switchPage("people");
  }
  if (action === "list") {
    if (item?.search_id && item.status === "stopped") {
      await api(`/api/searches/${item.search_id}/resume`, { method: "POST" });
      return refreshAll();
    }
    return queueName(name, item?.source || "manual");
  }
  if (action === "full-scan") {
    await bulkNames("/api/searches/full-scan-bulk", [name]);
    return;
  }
  if (!item?.search_id) return queueName(name, "manual");
  if (action === "enrich") await api(`/api/searches/${item.search_id}/enrich`, { method: "POST" });
  if (action === "stop") await api(`/api/searches/${item.search_id}/stop`, { method: "POST" });
  if (action === "delete") {
    if (!confirmDeleteCode(`Delete all listed people and company data for "${name}"?`)) return;
    await api("/api/searches/delete-bulk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ names: [name], source: "delete" }),
    });
    state.selectedNames.delete(name);
    if (state.selectedName?.name === name) state.selectedName = null;
  }
  await refreshAll();
}

async function bulkAddNames() {
  const names = $("#bulkNamesInput").value.split(/\n|,/).map((x) => x.trim()).filter(Boolean);
  if (!names.length) return;
  await api("/api/searches/bulk", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ names, source: "manual" }),
  });
  $("#bulkNamesInput").value = "";
  await refreshAll();
}

async function bulkNames(endpoint, names = [...state.selectedNames]) {
  if (!names.length) return;
  await api(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ names, source: "bulk" }),
  });
  await refreshAll();
}

async function deleteSelected() {
  const names = [...state.selectedNames];
  if (!names.length) return;
  if (!confirmDeleteCode(`Delete all data for ${names.length} selected names?`)) return;
  await api("/api/searches/delete-bulk", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ names, source: "delete" }),
  });
  state.selectedNames.clear();
  if (state.selectedName && names.includes(state.selectedName.name)) state.selectedName = null;
  await refreshAll();
}

function confirmDeleteCode(message) {
  const code = Math.random().toString(36).slice(2, 8).toUpperCase();
  const entered = window.prompt(`${message}\n\nThis cannot be undone.\nType this code to confirm: ${code}`);
  return entered === code;
}

function switchPage(page) {
  state.activePage = page;
  if (page === "people") state.commandPeople = [];
  refreshAll();
}

function schedulePeopleRefresh() {
  window.clearTimeout(peopleRefreshTimer);
  peopleRefreshTimer = window.setTimeout(() => refreshPeopleData(true), 250);
}

$("#peopleTab").onclick = () => switchPage("people");
$("#commandTab").onclick = () => switchPage("command");
$$(".view-tab").forEach((tab) => {
  tab.onclick = () => setPeopleView(tab.dataset.view);
});
$("#autoSpamBtn").onclick = async () => {
  const threshold = Number($("#autoSpamThreshold").value) || 40;
  const btn = $("#autoSpamBtn");
  btn.disabled = true;
  btn.textContent = "Scoring...";
  try {
    const res = await api("/api/people/auto-spam", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ threshold, rescore: true }),
    });
    alert(`Scored ${res.scored} people · moved ${res.spammed} to Spam (score < ${threshold}).`);
    await refreshAll();
  } catch (err) {
    alert("Auto-spam failed: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Score & auto-spam";
  }
};
$("#pauseBtn").onclick = async () => {
  await api(state.workerPaused ? "/api/worker/resume" : "/api/worker/pause", { method: "POST" });
  await refreshAll();
};
$("#filterToggle").onclick = () => $("#filterPanel").classList.toggle("hidden");
$("#clearFiltersBtn").onclick = clearAllFilters;
$("#textFilter").addEventListener("input", schedulePeopleRefresh);
$("#textFilter").addEventListener("change", schedulePeopleRefresh);
$$(".filter-grid input, .filter-grid select, .quick-filters input").forEach((el) => {
  el.addEventListener("input", schedulePeopleRefresh);
  el.addEventListener("change", schedulePeopleRefresh);
});
$$("th[data-sort]").forEach((th) => {
  th.onclick = () => {
    const key = th.dataset.sort;
    if (state.sortKey === key) state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
    else {
      state.sortKey = key;
      state.sortDir = key === "name" ? "asc" : "desc";
    }
    refreshPeopleData(true);
  };
});
$("#firstPageBtn").onclick = () => {
  state.peopleOffset = 0;
  refreshPeopleData(false);
};
$("#prevPageBtn").onclick = () => {
  state.peopleOffset = Math.max(0, state.peopleOffset - state.peopleLimit);
  refreshPeopleData(false);
};
$("#nextPageBtn").onclick = () => {
  if (state.peopleOffset + state.peopleLimit < state.peopleTotal) {
    state.peopleOffset += state.peopleLimit;
    refreshPeopleData(false);
  }
};
$("#lastPageBtn").onclick = () => {
  state.peopleOffset = Math.max(0, (Math.ceil(state.peopleTotal / state.peopleLimit) - 1) * state.peopleLimit);
  refreshPeopleData(false);
};
$("#pageSize").onchange = () => {
  state.peopleLimit = Number($("#pageSize").value) || 50;
  refreshPeopleData(true);
};
$("#personModalClose").onclick = closePersonModal;
$("#personModalOverlay").onclick = (e) => {
  if (e.target === $("#personModalOverlay")) closePersonModal();
};
$("#personModalCard").onclick = (e) => e.stopPropagation();
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && state.modalPersonId) closePersonModal();
});
$("#bulkAddBtn").onclick = bulkAddNames;
$("#listAllNewBtn").onclick = () => {
  const names = unifiedNames().filter((x) => x.status === "idle").map((x) => x.name);
  api("/api/searches/bulk", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ names, source: "library" }),
  }).then(refreshAll);
};
$("#enrichSelectedBtn").onclick = () => bulkNames("/api/searches/enrich-bulk");
$("#fullScanSelectedBtn").onclick = () => bulkNames("/api/searches/full-scan-bulk");
$("#stopSelectedBtn").onclick = () => bulkNames("/api/searches/stop-bulk");
$("#resumeSelectedBtn").onclick = () => bulkNames("/api/searches/resume-bulk");
$("#deleteSelectedBtn").onclick = deleteSelected;
$("#selectAllVisible").onchange = (e) => {
  const names = commandNames().map((x) => x.name);
  for (const name of names) {
    if (e.target.checked) state.selectedNames.add(name);
    else state.selectedNames.delete(name);
  }
  renderCommandTable();
};
["#commandNameFilter", "#commandStatusFilter", "#commandSourceFilter", "#commandScanFilter", "#commandErrorsOnly", "#commandListedOnly"].forEach((sel) => {
  $(sel).addEventListener("input", renderCommandTable);
  $(sel).addEventListener("change", renderCommandTable);
});

refreshAll();
setInterval(refreshAll, 2500);
