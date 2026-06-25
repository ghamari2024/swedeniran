const state = {
  activePage: "people",
  peopleView: "main",
  category: "",
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
  crmView: "new",
  campaigns: [],
  crmDetail: null,
  crmRefineDrafts: {},
  crmAgentStatus: null,
  crmEmailStatus: null,
  crmEmailSimulation: null,
  crmDetailTab: "site",
  crmEmailMessages: [],
  crmEmailCounts: null,
  crmEmailRate: null,
  crmEmailRefineDrafts: {},
  emailCompose: null,
  crmCandidates: { total: 0, companies: [] },
  crmCategory: "",
  crmSelectedOrgnrs: new Set(),
  crmLoadedCampaignId: null,
  crmOffset: 0,
  crmLimit: 50,
  crmTotal: 0,
  crmSortKey: "revenue_ksek",
  crmSortDir: "desc",
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
let peopleRefreshTimer = null;
let crmRefreshTimer = null;

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
    } else if (state.activePage === "crm") {
      if (!state.filterOptions) state.filterOptions = await api("/api/people/enriched/options");
      await refreshCrmPage(false);
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
  $("#crmTab").classList.toggle("active", state.activePage === "crm");
  $("#peoplePage").classList.toggle("active", state.activePage === "people");
  $("#commandPage").classList.toggle("active", state.activePage === "command");
  $("#crmPage").classList.toggle("active", state.activePage === "crm");
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
  renderCategoryBar();
  renderPeopleTable();
}

const CATEGORY_EMOJI = {
  dental: "🦷", healthcare: "🏥", automotive: "🚗", food_restaurants: "🍽",
  beauty_cosmetics: "💄", construction: "🏗", transport_logistics: "🚚",
  it_tech: "💻", education: "🎓", media_creative: "🎬", agriculture_nature: "🌾",
  energy_environment: "⚡", sports_leisure: "⚽", hospitality_travel: "🏨",
  manufacturing_industry: "🏭", cleaning_facility: "🧹", staffing_hr: "👥",
  retail_ecommerce: "🛒", finance: "💰", consulting_professional: "📋",
  wholesale_distribution: "📦", real_estate: "🏢", public_orgs: "🏛", other: "❓",
};

function renderCategoryBar() {
  const bar = $("#categoryBar");
  if (!bar) return;
  const cats = (state.filterOptions && state.filterOptions.categories) || [];
  if (!cats.length) {
    bar.innerHTML = "";
    return;
  }
  const sorted = [...cats].sort((a, b) => (b.count || 0) - (a.count || 0));
  const total = sorted.reduce((sum, c) => sum + (c.count || 0), 0);
  const buttons = [
    `<button type="button" class="cat-chip${state.category === "" ? " active" : ""}" data-cat="">All <span class="cat-count">${total}</span></button>`,
  ];
  for (const c of sorted) {
    const emoji = CATEGORY_EMOJI[c.id] || "";
    buttons.push(
      `<button type="button" class="cat-chip${state.category === c.id ? " active" : ""}" data-cat="${esc(c.id)}" title="${esc(c.label)}">${emoji} ${esc(c.label)} <span class="cat-count">${c.count || 0}</span></button>`
    );
  }
  bar.innerHTML = buttons.join("");
  bar.querySelectorAll(".cat-chip").forEach((btn) => {
    btn.onclick = () => setCategory(btn.dataset.cat);
  });
}

function setCategory(cat) {
  state.category = cat || "";
  renderCategoryBar();
  refreshPeopleData(true);
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
  add("category", state.category);
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
  state.category = "";
  renderCategoryBar();
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
  parts.push(`<a class="ghost small modal-action" href="${googleSearchUrl((p.name || "") + " sweden")}" target="_blank" rel="noopener">Google person</a>`);
  return `<div class="person-modal-actions">${parts.join("")}</div>`;
}

function stripUrlScheme(url) {
  return String(url || "").replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");
}

function renderPersonIntel(intel) {
  const rows = [];
  if (intel.linkedin_url) {
    rows.push(`<div class="intel-row"><span class="intel-k">LinkedIn</span><a href="${esc(intel.linkedin_url)}" target="_blank" rel="noopener">${esc(stripUrlScheme(intel.linkedin_url))}</a>${confidenceBadge(intel.linkedin_confidence)}</div>`);
  }
  if (intel.instagram_url) {
    rows.push(`<div class="intel-row"><span class="intel-k">Instagram</span><a href="${esc(intel.instagram_url)}" target="_blank" rel="noopener">${esc(stripUrlScheme(intel.instagram_url))}</a>${confidenceBadge(intel.instagram_confidence)}</div>`);
  }
  const socials = intel.socials || {};
  const extra = Object.entries(socials)
    .filter(([k]) => k !== "linkedin" && k !== "instagram")
    .map(([k, v]) => `<a class="intel-social" href="${esc(v)}" target="_blank" rel="noopener">${esc(k)}</a>`)
    .join("");
  if (extra) {
    rows.push(`<div class="intel-row"><span class="intel-k">Social</span><span class="intel-socials">${extra}</span></div>`);
  }
  if (intel.headline) {
    rows.push(`<div class="intel-row intel-about"><span class="intel-k">Headline</span><span>${esc(intel.headline)}</span></div>`);
  }
  if (!rows.length) {
    return `<p class="muted">No personal profiles confirmed yet. Deep-enrichment keeps retrying favorites automatically.</p>`;
  }
  return `<div class="company-intel person-intel">${rows.join("")}</div>`;
}

function renderPersonIntelSection(p) {
  let inner;
  if (p.intel) {
    inner = renderPersonIntel(p.intel);
  } else if (p.is_favorite) {
    inner = `<p class="muted">Searching the web for personal profiles… deep-enrichment retries automatically.</p>`;
  } else {
    inner = `<p class="muted">Add this person to favorites to automatically extract their personal profiles (LinkedIn, Instagram, …).</p>`;
  }
  const via = p.intel && p.intel.search_provider ? ` <small>via ${esc(p.intel.search_provider)}</small>` : "";
  return `
    <section class="person-modal-section">
      <h3>Personal profiles${via}</h3>
      ${inner}
    </section>
  `;
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

  body += renderPersonIntelSection(p);

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
  $$(".people-view-tabs .view-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === view));
  $("#favoritesActionBar")?.classList.toggle("hidden", view !== "favorites");
  if (view === "favorites") refreshCompanyDeepStatus();
  refreshPeopleData(true);
}

async function refreshCompanyDeepStatus() {
  try {
    const [s, p] = await Promise.all([
      api("/api/company-deep/status"),
      api("/api/person-deep/status").catch(() => null),
    ]);
    const el = $("#companyDeepStatus");
    if (!el) return;
    const total = s.favorites_total || 0;
    const cParts = [`Companies ${s.done || 0}/${total}`];
    if (s.running) cParts.push(`${s.running} running`);
    if (s.queued) cParts.push(`${s.queued} queued`);
    if (s.retry) cParts.push(`${s.retry} retry`);
    let text = cParts.join(" · ");
    if (p) {
      const pParts = [`Persons ${p.done || 0}/${p.favorites_total || 0}`];
      if (p.running) pParts.push(`${p.running} running`);
      if (p.queued) pParts.push(`${p.queued} queued`);
      if (p.retry) pParts.push(`${p.retry} retry`);
      if (p.company_phase_pending) pParts.push(`waiting on companies (${p.company_phase_pending})`);
      text += "  |  " + pParts.join(" · ");
    }
    el.textContent = text;
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
  if (page === "crm" && state.crmView === "list" && !state.crmDetail) setCrmView("list");
  refreshAll();
}

function crmStatusLabel(status) {
  return {
    idle: "Idle",
    pending: "Queued",
    generating: "Generating",
    generated: "Generated",
    improve_requested: "Improve queued",
    improving: "Improving",
    error: "Error",
    draft: "Draft",
    ready: "Ready",
    running: "Running",
  }[status] || status || "—";
}

function renderCrmCategoryBar() {
  const bar = $("#crmCategoryBar");
  if (!bar) return;
  const cats = (state.filterOptions && state.filterOptions.categories) || [];
  if (!cats.length) {
    bar.innerHTML = "";
    return;
  }
  const sorted = [...cats].sort((a, b) => (b.count || 0) - (a.count || 0));
  const total = sorted.reduce((sum, c) => sum + (c.count || 0), 0);
  const buttons = [
    `<button type="button" class="cat-chip${state.crmCategory === "" ? " active" : ""}" data-cat="">All <span class="cat-count">${total}</span></button>`,
  ];
  for (const c of sorted) {
    const emoji = CATEGORY_EMOJI[c.id] || "";
    buttons.push(
      `<button type="button" class="cat-chip${state.crmCategory === c.id ? " active" : ""}" data-cat="${esc(c.id)}" title="${esc(c.label)}">${emoji} ${esc(c.label)} <span class="cat-count">${c.count || 0}</span></button>`
    );
  }
  bar.innerHTML = buttons.join("");
  bar.querySelectorAll(".cat-chip").forEach((btn) => {
    btn.onclick = () => setCrmCategory(btn.dataset.cat);
  });
}

function setCrmCategory(cat) {
  state.crmCategory = cat || "";
  state.crmSelectedOrgnrs.clear();
  state.crmOffset = 0;
  renderCrmCategoryBar();
  refreshCrmCandidates(true).catch(console.warn);
}

function crmFilters() {
  const number = (id) => {
    const v = $(id)?.value;
    return v === "" || v == null ? null : Number(v);
  };
  return {
    revMin: number("#crmRevMin"),
    revMax: number("#crmRevMax"),
    empMin: number("#crmEmpMin"),
    empMax: number("#crmEmpMax"),
    ageMin: number("#crmAgeMin"),
    ageMax: number("#crmAgeMax"),
    year: $("#crmYearFilter")?.value || "",
    industry: $("#crmIndustryFilter")?.value || "",
    county: $("#crmCountyFilter")?.value || "",
    type: $("#crmTypeFilter")?.value || "",
    gender: $("#crmGenderFilter")?.value || "",
    hasRevenue: $("#crmHasRevenue")?.checked || false,
    activeOnly: $("#crmActiveOnly")?.checked || false,
    hasEmployees: $("#crmHasEmployees")?.checked || false,
    text: ($("#crmTextFilter")?.value || "").trim().toLowerCase(),
  };
}

function crmQueryParams() {
  const f = crmFilters();
  const params = new URLSearchParams({
    limit: String(state.crmLimit),
    offset: String(state.crmOffset),
    sort_key: state.crmSortKey,
    sort_dir: state.crmSortDir,
    view: "main",
  });
  const add = (key, value) => {
    if (value !== null && value !== undefined && value !== "") params.set(key, String(value));
  };
  add("category", state.crmCategory);
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
  return params;
}

function rebuildCrmFilterOptions() {
  const opts = state.filterOptions || {};
  fillSelect("#crmYearFilter", "Any year", opts.years || []);
  fillSelect("#crmIndustryFilter", "Any industry", opts.industries || []);
  fillSelect("#crmCountyFilter", "Any county", opts.counties || []);
  fillSelect("#crmTypeFilter", "Any type", opts.company_types || []);
}

function renderCrmFilterSummary(f = crmFilters()) {
  const items = [];
  if (state.crmCategory) {
    const cat = (state.filterOptions?.categories || []).find((c) => c.id === state.crmCategory);
    if (cat) items.push(cat.label);
  }
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
  $("#crmActiveFilterCount").textContent = items.length;
  $("#crmFilterSummary").textContent = items.length
    ? items.slice(0, 4).join(" · ") + (items.length > 4 ? ` +${items.length - 4}` : "")
    : "No filters active";
  $("#crmClearFiltersBtn")?.classList.toggle("hidden", items.length === 0);
}

function clearCrmFilters() {
  $("#crmTextFilter").value = "";
  $("#crmRevMin").value = "";
  $("#crmRevMax").value = "";
  $("#crmYearFilter").value = "";
  $("#crmIndustryFilter").value = "";
  $("#crmCountyFilter").value = "";
  $("#crmTypeFilter").value = "";
  $("#crmEmpMin").value = "";
  $("#crmEmpMax").value = "";
  $("#crmAgeMin").value = "";
  $("#crmAgeMax").value = "";
  $("#crmGenderFilter").value = "";
  $("#crmHasRevenue").checked = false;
  $("#crmActiveOnly").checked = false;
  $("#crmHasEmployees").checked = false;
  refreshCrmCandidates(true).catch(console.warn);
}

function updateCrmSelectionUi() {
  const count = state.crmSelectedOrgnrs.size;
  if ($("#crmSelectedCount")) $("#crmSelectedCount").textContent = `${count} selected`;
  const allShown = state.crmCandidates.companies || [];
  const selectAll = $("#crmSelectAll");
  if (selectAll) {
    selectAll.checked = allShown.length > 0 && allShown.every((c) => state.crmSelectedOrgnrs.has(c.orgnr));
  }
}


function renderCrmCandidatesTable() {
  renderCrmFilterSummary();
  const rows = state.crmCandidates.companies || [];
  const start = state.crmTotal ? state.crmOffset + 1 : 0;
  const end = Math.min(state.crmOffset + rows.length, state.crmTotal);
  const page = Math.floor(state.crmOffset / state.crmLimit) + 1;
  const pages = Math.max(1, Math.ceil(state.crmTotal / state.crmLimit));
  $("#crmVisibleCount").textContent = `Showing ${start}-${end} of ${state.crmTotal} companies`;
  $("#crmPageStatus").textContent = `Page ${page} / ${pages}`;
  $("#crmPrevPageBtn").disabled = state.crmOffset <= 0;
  $("#crmFirstPageBtn").disabled = state.crmOffset <= 0;
  $("#crmNextPageBtn").disabled = state.crmOffset + state.crmLimit >= state.crmTotal;
  $("#crmLastPageBtn").disabled = state.crmOffset + state.crmLimit >= state.crmTotal;
  $("#crmPageSize").value = String(state.crmLimit);
  const body = $("#crmCompaniesPickBody");
  if (!body) return;
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty">No matching companies.</td></tr>';
    return;
  }
  body.innerHTML = rows.map((c) => {
    const checked = state.crmSelectedOrgnrs.has(c.orgnr) ? " checked" : "";
    const loc = [c.municipality, c.county].filter(Boolean).join(", ") || "—";
    return `
      <tr class="rich-row crm-pick-row${checked ? " selected-row" : ""}" data-orgnr="${esc(c.orgnr)}">
        <td><input type="checkbox" class="crm-pick-check" data-orgnr="${esc(c.orgnr)}"${checked}></td>
        <td class="person-cell rich-person">
          <strong>${esc(c.company_name || c.orgnr)}</strong>
          <small>${esc(c.orgnr)}${c.website ? ` · <a href="${esc(c.website)}" target="_blank" rel="noopener">website</a>` : ""}</small>
          <small class="id-line">${esc(c.company_type || c.status || "—")}</small>
        </td>
        <td class="metric-cell">
          <strong>${fmtMoney(c.revenue_ksek)}</strong>
          <small>${c.employees ? esc(c.employees) + " emp" : "employees —"}</small>
        </td>
        <td class="metric-cell"><strong>${esc(c.revenue_year || "—")}</strong></td>
        <td class="metric-cell"><strong>${fmtMoney(c.profit_ksek)}</strong></td>
        <td class="chips-cell">${fmtList(c.industries, 4)}</td>
        <td class="metric-cell"><strong>${esc(loc)}</strong></td>
      </tr>
    `;
  }).join("");
  body.querySelectorAll(".crm-pick-check").forEach((box) => {
    box.onchange = () => {
      if (box.checked) state.crmSelectedOrgnrs.add(box.dataset.orgnr);
      else state.crmSelectedOrgnrs.delete(box.dataset.orgnr);
      box.closest("tr")?.classList.toggle("selected-row", box.checked);
      updateCrmSelectionUi();
    };
  });
  body.querySelectorAll(".crm-pick-row").forEach((row) => {
    row.onclick = (e) => {
      if (e.target.closest("a, input, button")) return;
      const box = row.querySelector(".crm-pick-check");
      if (!box) return;
      box.checked = !box.checked;
      box.dispatchEvent(new Event("change"));
    };
  });
  updateCrmSelectionUi();
}

async function refreshCrmCandidates(resetPage = false) {
  if (resetPage) state.crmOffset = 0;
  rebuildCrmFilterOptions();
  renderCrmCategoryBar();
  const body = $("#crmCompaniesPickBody");
  if (body) body.innerHTML = '<tr><td colspan="7" class="empty">Loading companies...</td></tr>';
  try {
    const data = await api(`/api/campaigns/candidates?${crmQueryParams()}`);
    state.crmCandidates = data;
    state.crmTotal = data.total || 0;
    state.crmLimit = data.limit || state.crmLimit;
    state.crmOffset = data.offset || 0;
    renderCrmCandidatesTable();
  } catch (err) {
    if (body) body.innerHTML = `<tr><td colspan="7" class="empty">Could not load companies: ${esc(err.message)}</td></tr>`;
    updateCrmSelectionUi();
  }
}

function scheduleCrmRefresh() {
  window.clearTimeout(crmRefreshTimer);
  crmRefreshTimer = window.setTimeout(() => refreshCrmCandidates(true), 250);
}

async function refreshCrmPage(loadCandidates) {
  if (state.crmView === "new") {
    $("#crmNewPanel").classList.remove("hidden");
    $("#crmListPanel").classList.add("hidden");
    $("#crmDetailPanel").classList.add("hidden");
    if (loadCandidates !== false) await refreshCrmCandidates(false);
    else {
      renderCrmCategoryBar();
      renderCrmCandidatesTable();
    }
    return;
  }
  if (state.crmView === "detail" && state.crmDetail) {
    $("#crmNewPanel").classList.add("hidden");
    $("#crmListPanel").classList.add("hidden");
    $("#crmDetailPanel").classList.remove("hidden");
    saveCrmRefineDrafts();
    const skipTableRender = crmDetailInputsActive();
    state.crmDetail = await api(`/api/campaigns/${state.crmDetail.id}`);
    fillCrmPromptFields(state.crmDetail);
    await refreshCrmAgentStatus();
    await refreshCrmEmailStatus();
    await refreshCrmSimulation();
    await refreshCrmEmailData(false);
    renderCrmDetailMeta();
    renderCrmDetailTabPanels();
    if (!skipTableRender) {
      if (state.crmDetailTab === "email") renderCrmEmailMessagesTable();
      else renderCrmCompaniesTable();
    }
    return;
  }
  $("#crmNewPanel").classList.add("hidden");
  $("#crmListPanel").classList.remove("hidden");
  $("#crmDetailPanel").classList.add("hidden");
  const data = await api("/api/campaigns");
  state.campaigns = data.campaigns || [];
  renderCrmCampaignsTable();
}

function setCrmView(view) {
  state.crmView = view;
  if (view !== "detail") {
    state.crmDetail = null;
    state.crmLoadedCampaignId = null;
  }
  $$(".crm-view-tabs .view-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.crmView === view);
  });
  refreshCrmPage(view === "new");
}

function setCrmDetailTab(tab) {
  state.crmDetailTab = tab;
  $$(".crm-detail-tabs .view-tab").forEach((el) => {
    el.classList.toggle("active", el.dataset.crmDetailTab === tab);
  });
  renderCrmDetailTabPanels();
  if (tab === "email") {
    refreshCrmEmailData(true).then(() => renderCrmEmailMessagesTable());
  } else {
    renderCrmCompaniesTable();
  }
}

function renderCrmDetailTabPanels() {
  const site = state.crmDetailTab !== "email";
  $("#crmSitePanel")?.classList.toggle("hidden", !site);
  $("#crmEmailPanel")?.classList.toggle("hidden", site);
  const runBtn = $("#crmRunBtn");
  if (runBtn) runBtn.classList.toggle("hidden", !site);
}

function fillCrmPromptFields(c) {
  if (state.crmLoadedCampaignId === c.id) return;
  $("#crmDetailBasePrompt").value = c.base_prompt || "";
  $("#crmDetailSystemPrompt").value = c.agent_system_prompt || "";
  $("#crmDetailEmailPrompt").value = c.email_prompt || "";
  $("#crmDetailEmailSystemPrompt").value = c.email_system_prompt || "";
  state.crmLoadedCampaignId = c.id;
  updateCrmRunButton();
}

function saveCrmRefineDrafts() {
  $$("#crmCompaniesTable textarea[data-refine]").forEach((ta) => {
    const orgnr = ta.dataset.refine;
    if (orgnr) state.crmRefineDrafts[orgnr] = ta.value;
  });
}

function crmDetailInputsActive() {
  const el = document.activeElement;
  if (!el) return false;
  if (el.id === "crmDetailBasePrompt" || el.id === "crmDetailSystemPrompt") return true;
  if (el.id === "crmDetailEmailPrompt" || el.id === "crmDetailEmailSystemPrompt") return true;
  return Boolean(
    el.matches?.("#crmCompaniesTable textarea[data-refine]")
    || el.matches?.("#crmEmailMessagesTable textarea[data-email-refine]")
  );
}

async function refreshCrmAgentStatus() {
  try {
    state.crmAgentStatus = await api("/api/campaigns/agent-status");
  } catch {
    state.crmAgentStatus = { ready: false, issues: ["Could not check agent status"] };
  }
  renderCrmAgentStatus();
}

function renderCrmAgentStatus() {
  const el = $("#crmAgentStatus");
  if (!el) return;
  const s = state.crmAgentStatus;
  if (!s) {
    el.classList.add("hidden");
    return;
  }
  el.classList.remove("hidden");
  if (s.ready) {
    el.className = "crm-agent-status ready";
    el.innerHTML = `<strong>Composer agent ready</strong> — model <code>${esc(s.model || "composer-2")}</code>. Run uses Cursor SDK locally on this machine.`;
  } else {
    const issues = (s.issues || []).map((x) => esc(x)).join(" · ");
    el.className = "crm-agent-status error";
    el.innerHTML = `
      <strong>Composer agent not configured</strong>
      <p>1. Copy <code>.env.example</code> to <code>.env</code></p>
      <p>2. Add <code>CURSOR_API_KEY</code> from <a href="https://cursor.com/dashboard/integrations" target="_blank" rel="noopener">cursor.com/dashboard/integrations</a></p>
      <p>3. Restart the server, then click Run again</p>
      <p class="muted">${issues}</p>
    `;
  }
  updateCrmRunButton();
}

async function refreshCrmEmailStatus() {
  try {
    state.crmEmailStatus = await api("/api/campaigns/email-status?verify=1");
  } catch {
    state.crmEmailStatus = { ready: false, issues: ["Could not check email status"] };
  }
  renderCrmEmailStatus();
}

async function refreshCrmSimulation() {
  try {
    state.crmEmailSimulation = await api("/api/campaigns/email/simulation");
  } catch {
    state.crmEmailSimulation = { enabled: true, to: "ghamari2004@gmail.com", ready: true };
  }
  renderCrmSimulationToggle();
}

function renderCrmSimulationToggle() {
  const btn = $("#crmSimulationToggle");
  const sim = state.crmEmailSimulation;
  const on = Boolean(sim?.enabled);
  const reviewTo = sim?.to || "ghamari2004@gmail.com";
  if (btn) {
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.textContent = on ? "Simulation ON" : "Simulation OFF";
    btn.title = on
      ? `Simulation is ON — emails go to ${reviewTo}. Click to send to real company addresses.`
      : "Simulation is OFF — emails go to each company's real address. Click to turn simulation on.";
    btn.setAttribute("aria-label", on ? "Simulation on" : "Simulation off");
  }
  renderCrmEmailSimulation();
}

async function toggleCrmSimulation() {
  const sim = state.crmEmailSimulation || { enabled: true };
  try {
    state.crmEmailSimulation = await api("/api/campaigns/email/simulation", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !sim.enabled }),
    });
    renderCrmSimulationToggle();
    if (state.crmView === "detail" && state.crmDetailTab === "email") {
      await refreshCrmEmailData(true);
    }
  } catch (err) {
    alert("Could not update simulation mode: " + err.message);
  }
}

function renderCrmEmailSimulation() {
  const sim = state.crmEmailSimulation || state.crmEmailStatus?.simulation;
  const enabled = Boolean(sim?.enabled && sim?.to);
  const tabBadge = $("#crmEmailTabSimBadge");
  const banner = $("#crmEmailSimulationBanner");
  const toEl = $("#crmEmailSimulationTo");
  if (tabBadge) tabBadge.classList.toggle("hidden", !enabled);
  if (banner) banner.classList.toggle("hidden", !enabled);
  if (toEl && enabled) toEl.textContent = sim.to;
}

function renderCrmEmailStatus() {
  const el = $("#crmEmailSmtpStatus");
  if (!el) return;
  const s = state.crmEmailStatus;
  if (!s) {
    el.classList.add("hidden");
    return;
  }
  el.classList.remove("hidden");
  const sim = s.simulation;
  const simNote = sim?.enabled && sim?.to
    ? ` · <span class="crm-sim-badge">Simulation</span> → ${esc(sim.to)}`
    : "";
  if (s.ready) {
    el.className = "crm-agent-status ready";
    const verified = s.verified ? "Connected" : "Configured";
    const rate = s.rate || {};
    el.innerHTML = `<strong>Email ${verified}</strong> — <code>${esc(s.from || "")}</code> · daily ${esc(String(rate.sent_today || 0))}/${esc(String(rate.daily_limit || 40))} sent · min interval ${esc(String(rate.min_interval_seconds || 90))}s${simNote}`;
  } else {
    const issues = (s.issues || []).map((x) => esc(x)).join(" · ");
    el.className = "crm-agent-status error";
    el.innerHTML = `<strong>Email not connected</strong><p class="muted">${issues}</p>${simNote}`;
  }
  renderCrmEmailSimulation();
}

async function refreshCrmEmailData(renderTable) {
  const c = state.crmDetail;
  if (!c) return;
  try {
    const data = await api(`/api/campaigns/${c.id}/email/messages`);
    state.crmEmailMessages = data.messages || [];
    state.crmEmailCounts = data.counts || {};
    state.crmEmailRate = data.rate || {};
    state.crmEmailSimulation = data.simulation || null;
    renderCrmEmailQueueStats();
    renderCrmEmailSimulation();
    if (renderTable !== false) renderCrmEmailMessagesTable();
  } catch {
    state.crmEmailMessages = [];
  }
}

function renderCrmEmailQueueStats() {
  const el = $("#crmEmailQueueStats");
  if (!el) return;
  const counts = state.crmEmailCounts || {};
  const rate = state.crmEmailRate || {};
  el.innerHTML = [
    `Sent: ${counts.sent || 0}`,
    `Queued: ${counts.queued || 0}`,
    `Draft ready: ${counts.draft_ready || 0}`,
    `Generating: ${counts.draft_pending || 0}`,
    `Failed: ${counts.failed || 0}`,
    `Replied: ${counts.replied || 0}`,
    `Selected: ${counts.selected || 0}`,
    rate.can_send ? "Rate: OK" : `Rate: wait ${rate.wait_seconds || 0}s`,
  ].join(" · ");
}

function emailStatusLabel(status) {
  const map = {
    idle: "Idle",
    draft_pending: "Generating",
    draft_generating: "Generating",
    draft_ready: "Draft ready",
    draft_error: "Draft error",
    queued: "Queued",
    sending: "Sending",
    sent: "Sent",
    failed: "Failed",
    bounced: "Bounced",
    replied: "Replied",
    excluded: "Excluded",
  };
  return map[status] || status;
}

function renderCrmEmailMessagesTable() {
  const c = state.crmDetail;
  if (!c) return;
  $$("#crmEmailMessagesTable textarea[data-email-refine]").forEach((ta) => {
    if (ta.dataset.orgnr) state.crmEmailRefineDrafts[ta.dataset.orgnr] = ta.value;
  });
  const table = $("#crmEmailMessagesTable");
  const msgs = state.crmEmailMessages || [];
  if (!msgs.length) {
    table.innerHTML = '<div class="empty" style="padding:24px">No companies with generated sites yet. Run the website campaign first.</div>';
    return;
  }
  table.innerHTML = `
    <table>
      <thead>
        <tr>
          <th><input id="crmEmailSelectAll" type="checkbox" title="Select all"></th>
          <th>Company</th>
          <th>Recipient</th>
          <th>Subject</th>
          <th>Status</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        ${msgs.map((row) => {
          const dupClass = row.already_emailed ? "crm-email-row-dup" : "";
          const dupBadge = row.already_emailed ? '<span class="crm-dup-badge">Previously emailed</span>' : "";
          const canPreview = row.status === "draft_ready" || row.body_html;
          const simActive = Boolean(row.simulation?.enabled);
          const simTo = row.display_recipient || row.simulation?.to || "";
          const recipientCell = simActive
            ? `<span class="crm-recipient-sim">${esc(simTo || "review inbox")}</span><span class="crm-recipient-original muted">DB: ${esc(row.original_recipient || row.recipient_email || "—")}</span>`
            : esc(row.recipient_email || "—");
          const canSelect = row.status !== "sent";
          return `
            <tr class="${dupClass}">
              <td><input type="checkbox" data-email-select="${esc(row.orgnr)}" ${row.selected ? "checked" : ""} ${canSelect ? "" : "disabled"}></td>
              <td>${esc(row.company_name || row.orgnr)} ${dupBadge}</td>
              <td>${recipientCell}</td>
              <td>${esc(row.subject || "—")}</td>
              <td><span class="crm-status ${esc(row.status)}">${esc(emailStatusLabel(row.status))}</span>
                ${row.error ? `<div class="muted">${esc(row.error)}</div>` : ""}
              </td>
              <td>
                ${canPreview ? `<button type="button" class="ghost small" data-email-preview="${esc(row.orgnr)}">Preview</button>` : ""}
                <div class="crm-refine-box">
                  <textarea placeholder="Improve this email..." data-email-refine="${esc(row.orgnr)}">${esc(state.crmEmailRefineDrafts[row.orgnr] || "")}</textarea>
                  <button type="button" class="ghost small" data-email-refine-btn="${esc(row.orgnr)}">Improve</button>
                </div>
              </td>
            </tr>
          `;
        }).join("")}
      </tbody>
    </table>
  `;
  const selectAll = $("#crmEmailSelectAll");
  if (selectAll) {
    const selectable = msgs.filter((m) => m.status !== "sent");
    selectAll.checked = selectable.length > 0 && selectable.every((m) => m.selected);
    selectAll.onchange = async (e) => {
      await api(`/api/campaigns/${c.id}/email/select-bulk`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selected: e.target.checked }),
      });
      await refreshCrmEmailData(true);
    };
  }
  table.querySelectorAll("[data-email-select]").forEach((cb) => {
    cb.onchange = async () => {
      await api(`/api/campaigns/${c.id}/companies/${encodeURIComponent(cb.dataset.emailSelect)}/email/select`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selected: cb.checked }),
      });
      await refreshCrmEmailData(true);
    };
  });
  table.querySelectorAll("[data-email-preview]").forEach((btn) => {
    btn.onclick = () => openEmailPreview(c.id, btn.dataset.emailPreview);
  });
  table.querySelectorAll("[data-email-refine-btn]").forEach((btn) => {
    btn.onclick = () => submitEmailRefine(btn.dataset.emailRefineBtn);
  });
}

async function saveCrmEmailPrompt() {
  const c = state.crmDetail;
  if (!c) return;
  const email_prompt = $("#crmDetailEmailPrompt").value.trim();
  if (!email_prompt) {
    alert("Write an email prompt first.");
    return;
  }
  try {
    state.crmDetail = await api(`/api/campaigns/${c.id}/email-prompt`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email_prompt,
        email_system_prompt: $("#crmDetailEmailSystemPrompt").value.trim() || null,
      }),
    });
    alert("Email prompt saved.");
  } catch (err) {
    alert("Save failed: " + err.message);
  }
}

async function generateCrmEmailDrafts() {
  const c = state.crmDetail;
  if (!c) return;
  const prompt = ($("#crmDetailEmailPrompt").value || c.email_prompt || "").trim();
  if (!prompt) {
    alert("Save an email prompt first.");
    return;
  }
  if (prompt !== (c.email_prompt || "").trim()) {
    await saveCrmEmailPrompt();
  }
  try {
    const res = await api(`/api/campaigns/${c.id}/email/generate-drafts`, { method: "POST" });
    alert(`Queued ${res.queued} email drafts for generation.`);
    await refreshCrmEmailData(true);
  } catch (err) {
    alert("Generate failed: " + err.message);
  }
}

async function sendCrmEmailQueue() {
  const c = state.crmDetail;
  if (!c) return;
  const sim = state.crmEmailSimulation;
  const simMsg = sim?.enabled && sim?.to
    ? `\n\nSimulation mode: all emails go to ${sim.to} (company addresses unchanged).`
    : "";
  if (!confirm(`Start sending selected emails with rate limiting?${simMsg}`)) return;
  try {
    const res = await api(`/api/campaigns/${c.id}/email/send`, { method: "POST" });
    alert(`Queued ${res.queued} emails for sending.`);
    await refreshCrmEmailData(true);
  } catch (err) {
    alert("Send queue failed: " + err.message);
  }
}

function openEmailPreview(campaignId, orgnr) {
  const url = `/api/campaigns/${campaignId}/companies/${encodeURIComponent(orgnr)}/email/preview`;
  $("#emailPreviewTitle").textContent = `Email — ${orgnr}`;
  $("#emailPreviewFrame").src = url;
  $("#emailPreviewOverlay").classList.remove("hidden");
  $("#emailPreviewOverlay").setAttribute("aria-hidden", "false");
}

function closeEmailPreview() {
  $("#emailPreviewOverlay").classList.add("hidden");
  $("#emailPreviewOverlay").setAttribute("aria-hidden", "true");
  $("#emailPreviewFrame").src = "about:blank";
}

async function submitEmailRefine(orgnr) {
  const c = state.crmDetail;
  if (!c) return;
  const textarea = document.querySelector(`textarea[data-email-refine="${CSS.escape(orgnr)}"]`);
  const prompt = textarea?.value.trim();
  if (!prompt) {
    alert("Enter an improvement prompt first.");
    return;
  }
  try {
    await api(`/api/campaigns/${c.id}/companies/${encodeURIComponent(orgnr)}/email/refine`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    });
    delete state.crmEmailRefineDrafts[orgnr];
    if (textarea) textarea.value = "";
    await refreshCrmEmailData(true);
  } catch (err) {
    alert("Improve failed: " + err.message);
  }
}

function updateCrmRunButton() {
  const saved = (state.crmDetail?.base_prompt || "").trim();
  const agentReady = state.crmAgentStatus?.ready !== false;
  const btn = $("#crmRunBtn");
  if (btn) btn.disabled = !saved || !agentReady;
  const hint = $("#crmPromptHint");
  if (!hint) return;
  const draft = ($("#crmDetailBasePrompt")?.value || "").trim();
  if (!agentReady && state.crmAgentStatus) {
    hint.textContent = "Set CURSOR_API_KEY in .env and restart the server before running.";
  } else if (saved) {
    hint.textContent = "Prompt saved. You can run the campaign.";
  } else if (draft) {
    hint.textContent = "Click Save prompt before running.";
  } else {
    hint.textContent = "Write and save a site prompt before running.";
  }
}

function renderCrmDetailMeta() {
  const c = state.crmDetail;
  if (!c) return;
  const cat = c.filter_snapshot?.category;
  const catLabel = cat
    ? (state.filterOptions?.categories || []).find((x) => x.id === cat)?.label || cat
    : "";
  $("#crmDetailTitle").textContent = c.name;
  $("#crmDetailMeta").textContent = [
    crmStatusLabel(c.status),
    `${c.companies.length} companies`,
    catLabel,
  ].filter(Boolean).join(" · ");
  updateCrmRunButton();
}

function renderCrmCompaniesTable() {
  const c = state.crmDetail;
  if (!c) return;
  saveCrmRefineDrafts();
  const table = $("#crmCompaniesTable");
  table.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Company</th>
          <th>Org nr</th>
          <th>Status</th>
          <th>Version</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        ${c.companies.map((row) => {
          const snap = row.company_snapshot || {};
          const name = snap.company_name || row.orgnr;
          const previewUrl = row.current_version
            ? `/api/campaigns/${c.id}/companies/${encodeURIComponent(row.orgnr)}/site/index.html`
            : "";
          return `
            <tr>
              <td>${esc(name)}</td>
              <td>${esc(row.orgnr)}</td>
              <td><span class="crm-status ${esc(row.status)}">${esc(crmStatusLabel(row.status))}</span>
                ${row.error ? `<div class="muted">${esc(row.error)}</div>` : ""}
              </td>
              <td>${row.current_version ? `v${row.current_version}` : "—"}</td>
              <td>
                ${previewUrl ? `<button type="button" class="ghost small" data-preview="${esc(previewUrl)}" data-preview-title="${esc(name)}">Preview</button>` : ""}
                <button type="button" class="ghost small" data-events="${row.id}">History</button>
                <div class="crm-refine-box">
                  <textarea placeholder="Improve this site..." data-refine="${esc(row.orgnr)}">${esc(state.crmRefineDrafts[row.orgnr] || "")}</textarea>
                  <button type="button" class="ghost small" data-refine-btn="${esc(row.orgnr)}">Improve</button>
                </div>
                <ul class="crm-events" id="crm-events-${row.id}"></ul>
              </td>
            </tr>
          `;
        }).join("")}
      </tbody>
    </table>
  `;
  table.querySelectorAll("[data-preview]").forEach((btn) => {
    btn.onclick = () => openSitePreview(btn.dataset.preview, btn.dataset.previewTitle);
  });
  table.querySelectorAll("[data-events]").forEach((btn) => {
    btn.onclick = () => loadCrmEvents(btn.dataset.events);
  });
  table.querySelectorAll("[data-refine-btn]").forEach((btn) => {
    btn.onclick = () => submitCrmRefine(btn.dataset.refineBtn);
  });
}

function renderCrmCampaignsTable() {
  const el = $("#crmCampaignsTable");
  if (!state.campaigns.length) {
    el.innerHTML = '<div class="empty" style="padding:24px">No campaigns yet.</div>';
    return;
  }
  el.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Name</th>
          <th>Status</th>
          <th>Companies</th>
          <th>Generated</th>
          <th>Pending</th>
          <th>Errors</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        ${state.campaigns.map((c) => `
          <tr>
            <td>${esc(c.name)}</td>
            <td><span class="crm-status ${esc(c.status)}">${esc(crmStatusLabel(c.status))}</span></td>
            <td>${esc(c.company_count || 0)}</td>
            <td>${esc(c.generated_count || 0)}</td>
            <td>${esc(c.pending_count || 0)}</td>
            <td>${esc(c.error_count || 0)}</td>
            <td><button type="button" class="ghost small" data-open-campaign="${c.id}">Open</button></td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
  el.querySelectorAll("[data-open-campaign]").forEach((btn) => {
    btn.onclick = async () => {
      state.crmLoadedCampaignId = null;
      state.crmDetail = await api(`/api/campaigns/${btn.dataset.openCampaign}`);
      state.crmView = "detail";
      await refreshCrmPage(false);
    };
  });
}

async function loadCrmEvents(companyRowId) {
  const c = state.crmDetail;
  if (!c) return;
  const row = c.companies.find((x) => String(x.id) === String(companyRowId));
  if (!row) return;
  const data = await api(`/api/campaigns/${c.id}/companies/${encodeURIComponent(row.orgnr)}/events`);
  const el = $(`#crm-events-${companyRowId}`);
  if (!el) return;
  el.innerHTML = (data.events || []).map((ev) => {
    const when = ev.created_at ? new Date(ev.created_at * 1000).toLocaleString() : "";
    return `<li><strong>${esc(ev.type)}</strong> ${esc(ev.message || "")} <span class="muted">${esc(when)}</span></li>`;
  }).join("") || "<li>No events yet.</li>";
}

async function submitCrmRefine(orgnr) {
  const c = state.crmDetail;
  if (!c) return;
  const textarea = document.querySelector(`textarea[data-refine="${CSS.escape(orgnr)}"]`);
  const prompt = textarea?.value.trim();
  if (!prompt) {
    alert("Enter an improvement prompt first.");
    return;
  }
  try {
    await api(`/api/campaigns/${c.id}/companies/${encodeURIComponent(orgnr)}/refine`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    });
    delete state.crmRefineDrafts[orgnr];
    if (textarea) textarea.value = "";
    state.crmDetail = await api(`/api/campaigns/${c.id}`);
    renderCrmCompaniesTable();
  } catch (err) {
    alert("Improve request failed: " + err.message);
  }
}

async function createCrmCampaign() {
  const orgnrs = [...state.crmSelectedOrgnrs];
  if (!orgnrs.length) {
    alert("Select at least one company.");
    return;
  }
  const catLabel = state.crmCategory
    ? (state.filterOptions?.categories || []).find((c) => c.id === state.crmCategory)?.label
    : "All";
  const name = $("#crmCampaignName").value.trim() || catLabel || "New campaign";
  try {
    const campaign = await api("/api/campaigns", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, category: state.crmCategory || null, orgnrs }),
    });
    state.crmLoadedCampaignId = null;
    state.crmDetail = campaign;
    state.crmView = "detail";
    state.crmSelectedOrgnrs.clear();
    await refreshCrmPage(false);
  } catch (err) {
    alert("Create campaign failed: " + err.message);
  }
}

async function saveCrmPrompt() {
  const c = state.crmDetail;
  if (!c) return;
  const base_prompt = $("#crmDetailBasePrompt").value.trim();
  if (!base_prompt) {
    alert("Write a site prompt first.");
    return;
  }
  try {
    state.crmDetail = await api(`/api/campaigns/${c.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        base_prompt,
        agent_system_prompt: $("#crmDetailSystemPrompt").value.trim() || null,
      }),
    });
    state.crmLoadedCampaignId = c.id;
    fillCrmPromptFields(state.crmDetail);
    renderCrmDetailMeta();
    alert("Prompt saved.");
  } catch (err) {
    alert("Save failed: " + err.message);
  }
}

async function runCrmCampaign() {
  const c = state.crmDetail;
  if (!c) return;
  try {
    const res = await api(`/api/campaigns/${c.id}/run`, { method: "POST" });
    alert(`Queued ${res.queued} companies for generation.`);
    state.crmDetail = res.campaign;
    renderCrmDetailMeta();
    renderCrmCompaniesTable();
  } catch (err) {
    alert("Run failed: " + err.message);
  }
}

function openSitePreview(url, title) {
  $("#sitePreviewTitle").textContent = title || "Site preview";
  $("#sitePreviewFrame").src = url;
  $("#sitePreviewOverlay").classList.remove("hidden");
  $("#sitePreviewOverlay").setAttribute("aria-hidden", "false");
}

function closeSitePreview() {
  $("#sitePreviewOverlay").classList.add("hidden");
  $("#sitePreviewOverlay").setAttribute("aria-hidden", "true");
  $("#sitePreviewFrame").src = "about:blank";
}

function defaultEmailBody(companyName, previewUrl) {
  const origin = window.location.origin;
  const link = previewUrl ? `${origin}${previewUrl}` : "";
  const lines = [
    `Hej ${companyName || ""},`.trim(),
    "",
    "Jag hoppas att det här meddelandet når er väl.",
  ];
  if (link) {
    lines.push("", "Jag har tagit fram ett förslag på en uppdaterad webbplats för er:", link);
  }
  lines.push("", "Med vänliga hälsningar,", state.crmEmailStatus?.from || "Mechamey");
  return lines.join("\n");
}

function openEmailCompose(orgnr, companyName, toEmail) {
  const c = state.crmDetail;
  if (!c) return;
  const row = c.companies.find((x) => x.orgnr === orgnr);
  const previewUrl = row?.current_version
    ? `/api/campaigns/${c.id}/companies/${encodeURIComponent(orgnr)}/site/index.html`
    : "";
  state.emailCompose = { orgnr, companyName, previewUrl };
  $("#emailComposeTitle").textContent = `Email — ${companyName || orgnr}`;
  $("#emailComposeTo").value = toEmail || "";
  $("#emailComposeSubject").value = `Förslag på webbplats — ${companyName || orgnr}`;
  $("#emailComposeBody").value = defaultEmailBody(companyName, previewUrl);
  $("#emailComposeHint").textContent = "";
  $("#emailComposeOverlay").classList.remove("hidden");
  $("#emailComposeOverlay").setAttribute("aria-hidden", "false");
}

function closeEmailCompose() {
  state.emailCompose = null;
  $("#emailComposeOverlay").classList.add("hidden");
  $("#emailComposeOverlay").setAttribute("aria-hidden", "true");
  $("#emailComposeHint").textContent = "";
}

async function sendCampaignEmail() {
  const c = state.crmDetail;
  const ctx = state.emailCompose;
  if (!c || !ctx) return;
  const to = $("#emailComposeTo").value.trim();
  const subject = $("#emailComposeSubject").value.trim();
  const body = $("#emailComposeBody").value.trim();
  if (!to || !subject || !body) {
    $("#emailComposeHint").textContent = "Fill in To, Subject, and Message.";
    return;
  }
  const btn = $("#emailComposeSend");
  btn.disabled = true;
  $("#emailComposeHint").textContent = "Sending…";
  try {
    await api(`/api/campaigns/${c.id}/companies/${encodeURIComponent(ctx.orgnr)}/email`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to, subject, body }),
    });
    const rowId = c.companies.find((x) => x.orgnr === ctx.orgnr)?.id;
    closeEmailCompose();
    alert(`Email sent to ${to}`);
    if (rowId) await loadCrmEvents(rowId);
  } catch (err) {
    $("#emailComposeHint").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}

function schedulePeopleRefresh() {
  window.clearTimeout(peopleRefreshTimer);
  peopleRefreshTimer = window.setTimeout(() => refreshPeopleData(true), 250);
}

$("#peopleTab").onclick = () => switchPage("people");
$("#commandTab").onclick = () => switchPage("command");
$("#crmTab").onclick = () => switchPage("crm");
$$(".crm-view-tabs .view-tab").forEach((tab) => {
  tab.onclick = () => setCrmView(tab.dataset.crmView);
});
$("#crmCreateBtn").onclick = () => createCrmCampaign();
$("#crmSelectAll").onchange = (e) => {
  const checked = e.target.checked;
  for (const c of state.crmCandidates.companies || []) {
    if (checked) state.crmSelectedOrgnrs.add(c.orgnr);
    else state.crmSelectedOrgnrs.delete(c.orgnr);
  }
  renderCrmCandidatesTable();
};
$("#crmFilterToggle").onclick = () => $("#crmFilterPanel").classList.toggle("hidden");
$("#crmClearFiltersBtn").onclick = clearCrmFilters;
$("#crmTextFilter").addEventListener("input", scheduleCrmRefresh);
$("#crmTextFilter").addEventListener("change", scheduleCrmRefresh);
$$("#crmFilterPanel .filter-grid input, #crmFilterPanel .filter-grid select, #crmFilterPanel .quick-filters input").forEach((el) => {
  el.addEventListener("input", scheduleCrmRefresh);
  el.addEventListener("change", scheduleCrmRefresh);
});
$$("th[data-crm-sort]").forEach((th) => {
  th.onclick = () => {
    const key = th.dataset.crmSort;
    if (state.crmSortKey === key) state.crmSortDir = state.crmSortDir === "asc" ? "desc" : "asc";
    else {
      state.crmSortKey = key;
      state.crmSortDir = key === "company_name" ? "asc" : "desc";
    }
    refreshCrmCandidates(false).catch(console.warn);
  };
});
$("#crmFirstPageBtn").onclick = () => { state.crmOffset = 0; refreshCrmCandidates(false).catch(console.warn); };
$("#crmPrevPageBtn").onclick = () => {
  state.crmOffset = Math.max(0, state.crmOffset - state.crmLimit);
  refreshCrmCandidates(false).catch(console.warn);
};
$("#crmNextPageBtn").onclick = () => {
  if (state.crmOffset + state.crmLimit < state.crmTotal) {
    state.crmOffset += state.crmLimit;
    refreshCrmCandidates(false).catch(console.warn);
  }
};
$("#crmLastPageBtn").onclick = () => {
  state.crmOffset = Math.max(0, (Math.ceil(state.crmTotal / state.crmLimit) - 1) * state.crmLimit);
  refreshCrmCandidates(false).catch(console.warn);
};
$("#crmPageSize").onchange = () => {
  state.crmLimit = Number($("#crmPageSize").value) || 50;
  refreshCrmCandidates(true).catch(console.warn);
};
$("#crmBackBtn").onclick = () => setCrmView("list");
$("#crmSimulationToggle").onclick = () => toggleCrmSimulation();
$("#crmRunBtn").onclick = () => runCrmCampaign();
$("#crmSavePromptBtn").onclick = () => saveCrmPrompt();
$("#crmDetailTabSite").onclick = () => setCrmDetailTab("site");
$("#crmDetailTabEmail").onclick = () => setCrmDetailTab("email");
$("#crmSaveEmailPromptBtn").onclick = () => saveCrmEmailPrompt();
$("#crmGenerateEmailDraftsBtn").onclick = () => generateCrmEmailDrafts();
$("#crmSendEmailQueueBtn").onclick = () => sendCrmEmailQueue();
$("#crmDetailBasePrompt").addEventListener("input", updateCrmRunButton);
$("#crmCompaniesTable").addEventListener("input", (e) => {
  const ta = e.target.closest("textarea[data-refine]");
  if (ta?.dataset.refine) state.crmRefineDrafts[ta.dataset.refine] = ta.value;
});
$("#sitePreviewClose").onclick = closeSitePreview;
$("#sitePreviewOverlay").onclick = (e) => {
  if (e.target === $("#sitePreviewOverlay")) closeSitePreview();
};
$("#emailPreviewClose").onclick = closeEmailPreview;
$("#emailPreviewOverlay").onclick = (e) => {
  if (e.target === $("#emailPreviewOverlay")) closeEmailPreview();
};
$("#emailComposeClose").onclick = closeEmailCompose;
$("#emailComposeSend").onclick = () => sendCampaignEmail();
$("#emailComposeOverlay").onclick = (e) => {
  if (e.target === $("#emailComposeOverlay")) closeEmailCompose();
};
document.querySelector(".email-compose-card")?.addEventListener("click", (e) => e.stopPropagation());
$$(".people-view-tabs .view-tab").forEach((tab) => {
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
