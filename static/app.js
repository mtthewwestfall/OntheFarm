"use strict";
const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const money = (n) => (Number(n) || 0).toLocaleString(undefined, {style: "currency", currency: "USD"});
const today = () => new Date().toISOString().slice(0, 10);
const fmtDate = (d) => d ? new Date(d + "T00:00:00").toLocaleDateString(undefined, {month: "short", day: "numeric", year: "numeric"}) : "";

async function api(path, opts = {}) {
  const o = {credentials: "same-origin", headers: {}, ...opts};
  if (o.body && typeof o.body !== "string") { o.body = JSON.stringify(o.body); o.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, o);
  let data = null;
  try { data = await r.json(); } catch (_) { /* empty body */ }
  if (r.status === 401 && path !== "/api/login") { showAuth(); throw new Error("Please sign in"); }
  if (!r.ok) throw new Error((data && data.error) || `Request failed (${r.status})`);
  return data;
}

function toast(msg, bad = false) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast" + (bad ? " bad" : "");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 3500);
}
const fail = (e) => toast(e.message || String(e), true);

const S = {me: null, tab: localStorage.getItem("otf_tab") || "tasks", tabLabels: {}, taskFilter: "open", saleFilter: "all", animals: [], projects: []};

const TABS = [
  ["tasks", "Tasks"], ["crops", "Crops"], ["animals", "Animals"], ["feeding", "Feeding"],
  ["expenses", "Expenses"], ["sales", "Gracie's sales corner"], ["goals", "Goals"], ["projects", "Projects"],
  ["shop", "Leroy's feed/parts store"], ["offthefarm", "OfftheFARM"], ["farm", "Farm & Members"],
];
const tabLabel = (k) => S.tabLabels[k] || (TABS.find((t) => t[0] === k) || [k, k])[1];
const isGuest = () => S.me && S.me.user.role === "guest";
const visibleTabs = () => isGuest() ? TABS.filter(([k]) => k === "offthefarm") : TABS;

/* ---------- form dialog ---------- */
function field(f, v) {
  const val = v ?? f.default ?? "";
  const req = f.required ? " required" : "";
  const lbl = esc(f.label) + (f.required ? " *" : "");
  if (f.type === "select") {
    const opts = (typeof f.options === "function" ? f.options() : f.options)
      .map(([k, t]) => `<option value="${esc(k)}"${String(k) === String(val) ? " selected" : ""}>${esc(t)}</option>`).join("");
    return `<label>${lbl}<select name="${f.name}"${req}>${opts}</select></label>`;
  }
  if (f.type === "textarea") return `<label>${lbl}<textarea name="${f.name}"${req} placeholder="${esc(f.placeholder || "")}">${esc(val)}</textarea></label>`;
  if (f.type === "checkbox") return `<label><input type="checkbox" name="${f.name}" style="display:inline;width:auto"${val ? " checked" : ""}> ${lbl}</label>`;
  const step = f.type === "number" ? ` step="${f.step || "any"}" min="0"` : "";
  return `<label>${lbl}<input name="${f.name}" type="${f.type || "text"}" value="${esc(val)}"${req}${step} placeholder="${esc(f.placeholder || "")}"></label>`;
}

function openForm(title, fields, values = {}, onSubmit, extraHtml = "") {
  const dlg = $("#dlg"), form = $("#dlgForm");
  const rows = []; let pair = [];
  for (const f of fields) {
    if (f.half) { pair.push(field(f, values[f.name])); if (pair.length === 2) { rows.push(`<div class="row">${pair.join("")}</div>`); pair = []; } }
    else { if (pair.length) { rows.push(`<div class="row">${pair.join("")}</div>`); pair = []; } rows.push(field(f, values[f.name])); }
  }
  if (pair.length) rows.push(`<div class="row">${pair.join("")}</div>`);
  form.innerHTML = `<h3>${esc(title)}</h3>${extraHtml}${rows.join("")}<p class="error" id="dlgErr"></p>
    <div class="dlg-actions"><button value="cancel" formnovalidate>Cancel</button><button class="primary" value="ok" id="dlgOk">Save</button></div>`;
  form.onsubmit = async (ev) => {
    if (ev.submitter && ev.submitter.value === "cancel") return;
    ev.preventDefault();
    const data = {};
    for (const f of fields) {
      const el = form.elements[f.name];
      data[f.name] = f.type === "checkbox" ? el.checked : el.value;
    }
    $("#dlgOk").disabled = true;
    try { await onSubmit(data); dlg.close(); render(); }
    catch (e) { $("#dlgErr").textContent = e.message; }
    finally { const b = $("#dlgOk"); if (b) b.disabled = false; }
  };
  dlg.showModal();
}

function confirmDelete(what, path) {
  if (!confirm(`Delete ${what}?`)) return;
  api(path, {method: "DELETE"}).then(render).catch(fail);
}

function showImage(url) {
  const form = $("#dlgForm");
  form.innerHTML = `<div class="lightbox"><img src="${esc(url)}"></div><div class="dlg-actions"><a href="${esc(url)}" download><button type="button">Download</button></a><button value="cancel">Close</button></div>`;
  form.onsubmit = null;
  $("#dlg").showModal();
}

/* ---------- resource definitions ---------- */
const projectOptions = () => [["", "— none —"], ...S.projects.map((p) => [p.id, p.name])];
const animalOptions = () => S.animals.map((a) => [a.id, animalName(a)]);
const animalKind = (a) => [a.species, a.breed].filter(Boolean).join(" – ");
const animalName = (a) => (a.name ? `${a.name} (${animalKind(a)})` : animalKind(a)) + (a.location ? ` @ ${a.location}` : "");
const multiline = (t) => esc(t).replace(/\n/g, "<br>");

const F = {
  tasks: [
    {name: "title", label: "Task", required: true},
    {name: "due_date", label: "Due", type: "date", half: true},
    {name: "priority", label: "Priority", type: "select", options: [["high", "High"], ["medium", "Medium"], ["low", "Low"]], default: "medium", half: true},
    {name: "project_id", label: "Project", type: "select", options: projectOptions},
    {name: "notes", label: "Notes", type: "textarea"},
  ],
  crops: [
    {name: "crop", label: "Crop", required: true, half: true, placeholder: "Tomatoes"},
    {name: "variety", label: "Variety", half: true, placeholder: "Brandywine"},
    {name: "planted_date", label: "Planted", type: "date", half: true},
    {name: "expected_harvest", label: "Expected harvest", type: "date", half: true},
    {name: "area", label: "Area / field", half: true, placeholder: "Bed 3, 0.25 acre"},
    {name: "status", label: "Status", type: "select", half: true, default: "planned",
     options: [["planned", "Planned"], ["planted", "Planted"], ["growing", "Growing"], ["harvested", "Harvested"], ["failed", "Failed"]]},
    {name: "notes", label: "Notes", type: "textarea"},
  ],
  animals: [
    {name: "name", label: "Name", half: true, placeholder: "Daisy, or “Laying flock”"},
    {name: "species", label: "Species", required: true, half: true, placeholder: "Chickens"},
    {name: "breed", label: "Breed", half: true, placeholder: "Buff Orpington"},
    {name: "head_count", label: "Head count", type: "number", step: "1", default: 1, half: true},
    {name: "location", label: "Location / pasture", placeholder: "North coop"},
    {name: "special_instructions", label: "Special instructions (shown on OfftheFARM)", type: "textarea", placeholder: "Lock coop at dusk. Daisy kicks — approach from the left."},
    {name: "health_notes", label: "Health notes", type: "textarea"},
  ],
  feedings: [
    {name: "animal_id", label: "Animal", type: "select", required: true, options: animalOptions},
    {name: "feed_type", label: "Feed type", required: true, half: true, placeholder: "Layer pellets"},
    {name: "amount", label: "Amount per feeding", required: true, half: true, placeholder: "2 scoops / 1.5 lb"},
    {name: "times", label: "Times of day (comma separated)", half: true, placeholder: "7:00 am, 5:30 pm"},
    {name: "times_per_day", label: "…or times per day", type: "number", step: "1", half: true, default: 1},
    {name: "special_instructions", label: "Special instructions (shown on OfftheFARM)", type: "textarea", placeholder: "Soak beet pulp 1 hr first; top up water; collect eggs"},
  ],
  expenses: [
    {name: "date", label: "Date", type: "date", required: true, half: true, default: today},
    {name: "amount", label: "Amount ($)", type: "number", step: "0.01", required: true, half: true},
    {name: "category", label: "Category", required: true, placeholder: "Feed, Seed, Fuel, Vet, Repairs…"},
    {name: "description", label: "Description", type: "textarea"},
  ],
  sales: [
    {name: "date", label: "Date", type: "date", required: true, half: true, default: today},
    {name: "item", label: "What you sold", required: true, half: true, placeholder: "Eggs"},
    {name: "quantity", label: "How many", type: "number", step: "1", default: 1, half: true},
    {name: "unit", label: "Unit", half: true, placeholder: "carton", default: "carton"},
    {name: "per_unit", label: "Count per unit (e.g. eggs per carton)", type: "number", step: "1", half: true, placeholder: "12"},
    {name: "amount", label: "Amount for this sale ($)", type: "number", step: "0.01", required: true, half: true},
    {name: "customer", label: "Customer", half: true},
    {name: "status", label: "Payment", type: "select", half: true, default: "paid", options: [["paid", "Paid"], ["pending", "Pending — still owes"]]},
    {name: "notes", label: "Notes", type: "textarea"},
  ],
  goals: [
    {name: "title", label: "Goal", required: true, placeholder: "Save for new barn roof"},
    {name: "kind", label: "Type", type: "select", half: true, default: "progress", options: [["progress", "Progress (%)"], ["savings", "Money saved ($)"]]},
    {name: "project_id", label: "Project", type: "select", half: true, options: projectOptions},
    {name: "current", label: "Current (% done or $ saved)", type: "number", half: true, default: 0},
    {name: "target", label: "Target (100% or $ goal)", type: "number", half: true},
    {name: "due_date", label: "Target date", type: "date"},
    {name: "notes", label: "Notes", type: "textarea"},
  ],
  projects: [
    {name: "name", label: "Project", required: true, placeholder: "Goat shed by the creek"},
    {name: "description", label: "What you want to build", type: "textarea", placeholder: "12x16 shed with lean-to, metal roof"},
    {name: "plan", label: "Your build plan", type: "textarea", placeholder: "Pole frame on gravel pad, salvaged lumber, red metal roof…"},
  ],
};
F.expenses[0].default = today(); F.sales[0].default = today();

function addBtn(label, res, defaults = {}) {
  return `<button class="primary" data-add="${res}" data-def='${esc(JSON.stringify(defaults))}'>+ ${esc(label)}</button>`;
}
const LABEL = {tasks: "task", crops: "planting", animals: "animal group", feedings: "feeding", expenses: "expense", sales: "sale", goals: "goal", projects: "project"};

function bindCrud(root, items) {
  root.querySelectorAll("[data-add]").forEach((b) => b.onclick = () => {
    const res = b.dataset.add, def = JSON.parse(b.dataset.def || "{}");
    if (res === "feedings" && !S.animals.length) return toast("Add an animal group first (Animals tab).", true);
    openForm(`Add ${LABEL[res]}`, F[res], def, (d) => api(`/api/${res}`, {method: "POST", body: d}));
  });
  root.querySelectorAll("[data-edit]").forEach((b) => b.onclick = () => {
    const [res, id] = b.dataset.edit.split(":");
    const it = items[res].find((x) => String(x.id) === id);
    openForm(`Edit ${LABEL[res]}`, F[res], it, (d) => api(`/api/${res}/${id}`, {method: "PUT", body: d}));
  });
  root.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => {
    const [res, id] = b.dataset.del.split(":");
    confirmDelete(`this ${LABEL[res]}`, `/api/${res}/${id}`);
  });
  root.querySelectorAll("[data-toggle]").forEach((b) => b.onchange = () => {
    api(`/api/tasks/${b.dataset.toggle}`, {method: "PUT", body: {done: b.checked}}).then(render).catch(fail);
  });
}
const rowActions = (res, id) => `<td class="actions"><button class="small" data-edit="${res}:${id}">Edit</button> <button class="small danger" data-del="${res}:${id}">Delete</button></td>`;
const empty = (msg) => `<div class="empty">${msg}</div>`;

/* ---------- views ---------- */
function taskTable(tasks, showProject = true) {
  if (!tasks.length) return empty("No tasks here.");
  const pname = (id) => (S.projects.find((p) => p.id === id) || {}).name || "";
  return `<div class="wrap"><table class="list"><thead><tr><th></th><th>Task</th><th>Due</th><th>Priority</th>${showProject ? "<th>Project</th>" : ""}<th></th></tr></thead><tbody>
  ${tasks.map((t) => {
    const late = !t.done && t.due_date && t.due_date < today();
    return `<tr class="${t.done ? "done" : ""}"><td><input type="checkbox" data-toggle="${t.id}" ${t.done ? "checked" : ""} style="width:auto;margin:0" aria-label="Done"></td>
    <td><span class="t">${esc(t.title)}</span>${t.notes ? `<div class="muted">${esc(t.notes)}</div>` : ""}</td>
    <td class="${late ? "overdue" : ""}">${fmtDate(t.due_date)}${late ? " (overdue)" : ""}</td>
    <td><span class="badge ${t.priority}">${esc(t.priority)}</span></td>${showProject ? `<td>${esc(pname(t.project_id))}</td>` : ""}${rowActions("tasks", t.id)}</tr>`;
  }).join("")}</tbody></table></div>`;
}

const VIEWS = {
  async tasks(v) {
    const tasks = await api("/api/tasks");
    const f = S.taskFilter;
    const shown = tasks.filter((t) => f === "all" || (f === "open" ? !t.done : t.done));
    v.innerHTML = `<div class="bar"><h2>${esc(tabLabel("tasks"))}</h2><div class="filters">
      ${["open", "done", "all"].map((k) => `<button data-f="${k}" class="${f === k ? "active" : ""}">${k[0].toUpperCase() + k.slice(1)}</button>`).join("")}
      ${addBtn("Task", "tasks")}</div></div>${taskTable(shown)}`;
    v.querySelectorAll("[data-f]").forEach((b) => b.onclick = () => { S.taskFilter = b.dataset.f; render(); });
    bindCrud(v, {tasks});
  },
  async crops(v) {
    const crops = await api("/api/crops");
    v.innerHTML = `<div class="bar"><h2>${esc(tabLabel("crops"))}</h2>${addBtn("Planting", "crops")}</div>` + (crops.length ? `<div class="wrap"><table class="list"><thead><tr>
      <th>Crop</th><th>Planted</th><th>Expected harvest</th><th>Status</th><th>Area</th><th>Notes</th><th></th></tr></thead><tbody>
      ${crops.map((c) => `<tr><td><b>${esc(c.crop)}</b>${c.variety ? `<div class="muted">${esc(c.variety)}</div>` : ""}</td><td>${fmtDate(c.planted_date)}</td>
      <td>${fmtDate(c.expected_harvest)}</td><td><span class="badge">${esc(c.status)}</span></td><td>${esc(c.area)}</td><td>${esc(c.notes)}</td>${rowActions("crops", c.id)}</tr>`).join("")}
      </tbody></table></div>` : empty("No plantings yet. Add what you've planted or plan to plant."));
    bindCrud(v, {crops});
  },
  async animals(v) {
    const animals = S.animals;
    const total = animals.reduce((n, a) => n + (a.head_count || 0), 0);
    v.innerHTML = `<div class="bar"><h2>${esc(tabLabel("animals"))} <span class="muted" style="font-size:15px">${total} head</span></h2>${addBtn("Animal group", "animals")}</div>` + (animals.length ? `<div class="wrap"><table class="list"><thead><tr>
      <th>Animal</th><th>Head</th><th>Location / pasture</th><th>Special instructions</th><th>Health notes</th><th></th></tr></thead><tbody>
      ${animals.map((a) => `<tr><td><b>${esc(a.name || a.species)}</b><div class="muted">${esc(a.name ? animalKind(a) : a.breed)}</div></td><td>${a.head_count ?? ""}</td>
      <td>${esc(a.location)}</td><td>${multiline(a.special_instructions)}</td><td>${multiline(a.health_notes)}</td>${rowActions("animals", a.id)}</tr>`).join("")}</tbody></table></div>` : empty("No animals yet."));
    bindCrud(v, {animals});
  },
  async feeding(v) {
    const [feedings, sched] = await Promise.all([api("/api/feedings"), api("/api/schedule")]);
    const byId = Object.fromEntries(S.animals.map((a) => [a.id, a]));
    const tl = sched.timed.map((t) => `<tr><td><b>${esc(fmtTime(t.time))}</b></td><td>${esc(t.animal)}</td><td>${esc(t.feed_type)}</td><td>${esc(t.amount)}</td></tr>`).join("");
    v.innerHTML = `<div class="bar"><h2>${esc(tabLabel("feeding"))}</h2><div class="filters">
        <a href="/schedule/print" target="_blank"><button>Print / PDF</button></a>${addBtn("Feeding", "feedings")}</div></div>
      <p class="muted">Going out of town? Print the schedule, or invite your sitter free as a guest — they'll see the OfftheFARM instructions in the app.</p>
      ${feedings.length ? `<div class="wrap"><table class="list"><thead><tr><th>Animal</th><th>Feed</th><th>Amount</th><th>When</th><th>Special instructions</th><th></th></tr></thead><tbody>
      ${feedings.map((f) => `<tr><td>${esc(byId[f.animal_id] ? animalName(byId[f.animal_id]) : "?")}</td><td>${esc(f.feed_type)}</td><td>${esc(f.amount)}</td>
        <td>${f.times ? esc(f.times.split(", ").map(fmtTime).join(", ")) : `${f.times_per_day || 1}× a day`}</td><td>${multiline(f.special_instructions)}</td>${rowActions("feedings", f.id)}</tr>`).join("")}
      </tbody></table></div>` : empty(S.animals.length ? "No feedings set yet." : "Add your animals first, then set what each group eats.")}
      ${tl ? `<h3>Daily timeline</h3><div class="wrap"><table class="list"><thead><tr><th>Time</th><th>Animal</th><th>Feed</th><th>Amount</th></tr></thead><tbody>${tl}</tbody></table></div>` : ""}`;
    bindCrud(v, {feedings});
  },
  async expenses(v) {
    const [items, months] = await Promise.all([api("/api/expenses"), api("/api/expense-summary")]);
    const thisMonth = today().slice(0, 7);
    const cur = months.find((m) => m.month === thisMonth);
    v.innerHTML = `<div class="bar"><h2>${esc(tabLabel("expenses"))}</h2>${addBtn("Expense", "expenses")}</div>
      <div class="stats">${months.slice(0, 6).map((m) => `<div class="stat"><div class="k">${esc(monthName(m.month))}${m.month === thisMonth ? " (this month)" : ""}</div>
        <div class="v">${money(m.total)}</div><div class="s">${Object.entries(m.by_category).slice(0, 3).map(([k, x]) => `${esc(k)} ${money(x)}`).join(" · ")}</div></div>`).join("") ||
        `<div class="stat"><div class="k">This month</div><div class="v">${money(0)}</div></div>`}</div>
      ${!cur && months.length ? `<p class="muted">Nothing logged for ${esc(monthName(thisMonth))} yet.</p>` : ""}
      ${items.length ? `<div class="wrap"><table class="list"><thead><tr><th>Date</th><th>Category</th><th>Amount</th><th>Description</th><th></th></tr></thead><tbody>
      ${items.map((e) => `<tr><td>${fmtDate(e.date)}</td><td>${esc(e.category)}</td><td><b>${money(e.amount)}</b></td><td>${esc(e.description)}</td>${rowActions("expenses", e.id)}</tr>`).join("")}
      </tbody></table></div>` : empty("No expenses logged yet.")}`;
    bindCrud(v, {expenses: items});
  },
  async sales(v) {
    const [items, sum] = await Promise.all([api("/api/sales"), api(`/api/sales-summary?today=${today()}`)]);
    const f = S.saleFilter;
    const shown = items.filter((s) => f === "all" || s.status === f);
    const stat = (k, p) => `<div class="stat"><div class="k">${k}</div><div class="v">${money(p.total)}</div>
      <div class="s">${money(p.paid)} paid · <span class="${p.pending ? "overdue" : ""}">${money(p.pending)} pending</span> · ${p.count} sale${p.count === 1 ? "" : "s"}</div></div>`;
    v.innerHTML = `<div class="bar"><h2>${esc(tabLabel("sales"))}</h2>${addBtn("Sale", "sales")}</div>
      <div class="stats">${stat("This week", sum.week)}${stat("This month", sum.month)}${stat("This year", sum.year)}
      <div class="stat"><div class="k">Still owed</div><div class="v ${sum.owed_total ? "overdue" : ""}">${money(sum.owed_total)}</div><div class="s">${sum.owed.length} customer${sum.owed.length === 1 ? "" : "s"}</div></div></div>
      ${sum.owed.length ? `<div class="card"><b>Who still owes</b><table class="list" style="margin-top:8px"><thead><tr><th>Customer</th><th>Owes</th><th>Sales</th><th>Oldest</th></tr></thead><tbody>
        ${sum.owed.map((o) => `<tr><td>${esc(o.customer)}</td><td class="overdue">${money(o.amount)}</td><td>${o.count}</td><td>${fmtDate(o.oldest)}</td></tr>`).join("")}</tbody></table></div>` : ""}
      <div class="bar"><div class="filters">${[["all", "All"], ["pending", "Pending"], ["paid", "Paid"]].map(([k, t]) => `<button data-sf="${k}" class="${f === k ? "active" : ""}">${t}</button>`).join("")}</div></div>
      ${shown.length ? `<div class="wrap"><table class="list"><thead><tr><th>Date</th><th>Item</th><th>Qty</th><th>Amount</th><th>Customer</th><th>Status</th><th></th></tr></thead><tbody>
      ${shown.map((s) => `<tr><td>${fmtDate(s.date)}</td><td><b>${esc(s.item)}</b>${s.notes ? `<div class="muted">${esc(s.notes)}</div>` : ""}</td>
        <td>${s.quantity ?? ""} ${esc(s.unit || "")}${s.per_unit ? ` <span class="muted">(${s.per_unit} each)</span>` : ""}</td><td><b>${money(s.amount)}</b></td><td>${esc(s.customer)}</td>
        <td><span class="badge ${s.status}">${s.status}</span>${s.status === "pending" ? ` <button class="small" data-paid="${s.id}">Mark paid</button>` : s.paid_date ? `<div class="muted">${fmtDate(s.paid_date)}</div>` : ""}</td>${rowActions("sales", s.id)}</tr>`).join("")}
      </tbody></table></div>` : empty(items.length ? "No sales match this filter." : "No sales yet. Log eggs, produce, meat, anything you sell.")}`;
    v.querySelectorAll("[data-sf]").forEach((b) => b.onclick = () => { S.saleFilter = b.dataset.sf; render(); });
    v.querySelectorAll("[data-paid]").forEach((b) => b.onclick = () => api(`/api/sales/${b.dataset.paid}`, {method: "PUT", body: {status: "paid", paid_date: today()}}).then(render).catch(fail));
    bindCrud(v, {sales: items});
  },
  async goals(v) {
    const goals = await api("/api/goals");
    v.innerHTML = `<div class="bar"><h2>${esc(tabLabel("goals"))}</h2>${addBtn("Goal", "goals")}</div>` + (goals.length ? `<div class="grid">${goals.map(goalCard).join("")}</div>` : empty("No goals yet. Track progress on a project or money saved toward one."));
    bindGoals(v, goals);
  },
  async projects(v) {
    if (S.projectId) return projectDetail(v, S.projectId);
    const projects = S.projects;
    v.innerHTML = `<div class="bar"><h2>${esc(tabLabel("projects"))}</h2>${addBtn("Project", "projects")}</div>` + (projects.length ? `<div class="grid">${projects.map((p) => `<div class="card">
      <h3 style="margin:0 0 6px">${esc(p.name)}</h3><p class="muted" style="margin:0 0 10px">${esc((p.description || "").slice(0, 140))}</p>
      <button class="primary small" data-open="${p.id}">Open</button></div>`).join("")}</div>` : empty("No projects yet. Snap a photo of where you want to build and see what it'll look like."));
    v.querySelectorAll("[data-open]").forEach((b) => b.onclick = () => { S.projectId = Number(b.dataset.open); render(); });
    bindCrud(v, {projects});
  },
  async shop(v) {
    const s = S.shop || {query: "", category: "feed"};
    v.innerHTML = `<div class="bar"><h2>${esc(tabLabel("shop"))}</h2></div>
      <div class="card"><form id="shopForm"><div class="row">
        <label>What do you need?<input name="query" required value="${esc(s.query)}" placeholder="50 lb layer feed, PTO shaft for a 5 ft bush hog, T-posts…"></label>
        <label>Category<select name="category">${[["feed", "Feed & hay"], ["parts", "Parts"], ["tools", "Tools & equipment"], ["materials", "Building materials"]].map(([k, t]) => `<option value="${k}"${k === s.category ? " selected" : ""}>${t}</option>`).join("")}</select></label></div>
        <label><input type="checkbox" name="nearby" style="display:inline;width:auto" ${S.me.location ? "checked" : "disabled"}> Prefer stores near ${S.me.location ? esc(S.me.location) : "my farm (set your location in Farm &amp; Members)"}</label>
        <button class="primary" id="shopGo">Search the web</button></form></div><div id="shopOut">${s.html || ""}</div>`;
    $("#shopForm").onsubmit = async (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target), btn = $("#shopGo");
      const q = {query: fd.get("query"), category: fd.get("category"), nearby: fd.get("nearby") === "on"};
      btn.disabled = true; btn.textContent = "Searching…"; $("#shopOut").innerHTML = `<div class="empty">Searching stores and listings…</div>`;
      try {
        const r = await api("/api/shop/search", {method: "POST", body: q});
        const html = shopResults(r);
        S.shop = {...q, html}; $("#shopOut").innerHTML = html; bindShop($("#shopOut"));
      } catch (e) { $("#shopOut").innerHTML = `<div class="empty error">${esc(e.message)}</div>`; }
      finally { btn.disabled = false; btn.textContent = "Search the web"; }
    };
    bindShop(v);
  },
  async offthefarm(v) {
    const gd = await api("/api/guide");
    const when = (f) => f.times ? f.times.split(", ").map(fmtTime).join(", ") : `${f.times_per_day || 1}× a day`;
    v.innerHTML = `<div class="bar"><h2>${esc(tabLabel("offthefarm"))} instructions</h2><a href="/schedule/print" target="_blank"><button>Print / PDF</button></a></div>
      <p class="muted">Everything whoever watches ${esc(gd.farm_name)} needs: who eats what, when, and anything special.${gd.owner ? ` Questions? Contact ${esc(gd.owner.name || "the owner")} at <a href="mailto:${esc(gd.owner.email)}">${esc(gd.owner.email)}</a>.` : ""}</p>
      ${!isGuest() ? `<p class="muted">Built from your Animals and Feeding entries. Invite a guest for free from Farm &amp; Members so they can view this in the app.</p>` : ""}
      ${gd.sitter_notes ? `<div class="advice" style="margin-bottom:12px"><b>Notes from the farm</b>\n${esc(gd.sitter_notes)}</div>` : ""}
      ${gd.timed.length ? `<div class="card"><b>Today's feeding timeline</b><table class="list" style="margin-top:8px"><thead><tr><th>Time</th><th>Animal</th><th>Feed</th><th>Amount</th><th>Where</th></tr></thead><tbody>
        ${gd.timed.map((t) => `<tr><td><b>${esc(fmtTime(t.time))}</b></td><td>${esc(t.animal)}</td><td>${esc(t.feed_type)}</td><td>${esc(t.amount)}</td><td>${esc(t.location)}</td></tr>`).join("")}</tbody></table></div>` : ""}
      ${gd.animals.length ? `<div class="grid">${gd.animals.map((a) => `<div class="card">
        <div style="display:flex;justify-content:space-between;gap:8px"><b>${esc(a.name || a.species)}</b><span class="badge">${a.head_count ?? 1} head</span></div>
        <div class="muted">${esc(a.name ? animalKind(a) : a.breed)}${a.location ? ` · ${esc(a.location)}` : ""}</div>
        ${a.special_instructions ? `<div class="advice" style="margin-top:8px"><b>Special instructions</b>\n${esc(a.special_instructions)}</div>` : ""}
        ${a.feedings.length ? `<ul style="padding-left:18px;margin:8px 0 0">${a.feedings.map((f) => `<li><b>${esc(f.feed_type)}</b> — ${esc(f.amount)}, ${esc(when(f))}${f.special_instructions ? `<div class="muted">${multiline(f.special_instructions)}</div>` : ""}</li>`).join("")}</ul>` : `<p class="muted">No feeding set.</p>`}
        ${a.health_notes ? `<p class="muted" style="margin-bottom:0"><b>Health:</b> ${multiline(a.health_notes)}</p>` : ""}</div>`).join("")}</div>`
        : empty(isGuest() ? "The farm hasn't added instructions yet." : "Add animals (with names and special instructions) and feedings to build this list.")}`;
  },
  async farm(v) {
    const owner = S.me.user.role === "owner";
    const [members, invites] = await Promise.all([api("/api/members"), owner ? api("/api/invites") : Promise.resolve([])]);
    v.innerHTML = `<div class="bar"><h2>${esc(S.me.user.farm_name)}</h2></div>
      <div class="card"><form id="farmForm">
        ${owner ? `<label>Farm name<input name="name" value="${esc(S.me.user.farm_name)}" required></label>` : ""}
        <label>Location (town or ZIP — used by the supplies search to find nearby stores)<input name="location" value="${esc(S.me.location)}" placeholder="Medina, OH 44256"></label>
        <label>Notes for the farm sitter (printed on the feeding schedule)<textarea name="sitter_notes" placeholder="Vet: Dr. Lee 555-0100. Gate code 1234. Water trough heater plug is by the barn door.">${esc(S.me.sitter_notes)}</textarea></label>
        <button class="primary">Save</button></form></div>
      <div class="card"><h3 style="margin-top:0">Tab names</h3>
        <p class="muted">Rename any tab — new names show for everyone on this farm. Clear a name to reset it.</p>
        <button class="small" id="renameTabsBtn">Rename tabs</button></div>
      <div class="card"><h3 style="margin-top:0">Farm members</h3>
        <p class="muted">Inviting is free. Members see and update everything on this farm. Guests only see the OfftheFARM instructions.</p>
        <table class="list"><tbody>${members.map((m) => `<tr><td>${esc(m.name || m.email)}<div class="muted">${esc(m.email)}</div></td><td><span class="badge">${m.role}</span></td>
          <td class="actions">${owner && m.role !== "owner" ? `<button class="small danger" data-rm="${m.id}">Remove</button>` : ""}</td></tr>`).join("")}</tbody></table>
        ${owner ? `<form id="inviteForm" style="margin-top:12px"><div class="row"><label>Email (optional)<input name="email" type="email" placeholder="sitter@example.com"></label>
          <label>Access<select name="role"><option value="member">Farm member — full access</option><option value="guest">Guest — OfftheFARM instructions only</option></select></label></div>
          <button class="primary">Create free invite link</button></form>
          ${invites.length ? `<h4>Pending invites</h4>${invites.map((i) => `<div class="copy" style="margin-bottom:6px"><input readonly value="${esc(i.link)}" onclick="this.select()">
            <button type="button" class="small" data-copy="${esc(i.link)}">Copy</button><button type="button" class="small danger" data-revoke="${esc(i.token)}">Revoke</button></div>
            <div class="muted" style="margin:-2px 0 8px">${i.role === "guest" ? "Guest (OfftheFARM only)" : "Farm member"}${i.email ? ` · for ${esc(i.email)}` : ""}</div>`).join("")}` : ""}` : `<p class="muted">Only the farm owner can invite people.</p>`}
      </div>`;
    $("#farmForm").onsubmit = async (ev) => {
      ev.preventDefault();
      const body = Object.fromEntries(new FormData(ev.target));
      try { await api("/api/farm", {method: "PUT", body}); await loadMe(); toast("Saved"); render(); } catch (e) { fail(e); }
    };
    $("#renameTabsBtn").onclick = () => {
      const vals = {};
      TABS.forEach(([k]) => { vals[`tab_${k}`] = tabLabel(k); });
      openForm("Rename tabs",
        TABS.map(([k, t]) => ({name: `tab_${k}`, label: `“${t}” shows as`})),
        vals,
        async (d) => {
          const labels = {};
          TABS.forEach(([k]) => {
            const v = String(d[`tab_${k}`] || "").trim();
            if (v !== tabLabel(k)) labels[k] = v;
          });
          S.tabLabels = await api("/api/tab-labels", {method: "PUT", body: {labels}});
          toast("Tab names saved");
        });
    };
    const inv = $("#inviteForm");
    if (inv) inv.onsubmit = async (ev) => {
      ev.preventDefault();
      try { const r = await api("/api/invites", {method: "POST", body: Object.fromEntries(new FormData(ev.target))}); await copy(r.link); render(); } catch (e) { fail(e); }
    };
    v.querySelectorAll("[data-copy]").forEach((b) => b.onclick = () => copy(b.dataset.copy));
    v.querySelectorAll("[data-revoke]").forEach((b) => b.onclick = () => confirmDelete("this invite link", `/api/invites/${b.dataset.revoke}`));
    v.querySelectorAll("[data-rm]").forEach((b) => b.onclick = () => confirmDelete("this member from the farm", `/api/members/${b.dataset.rm}`));
  },
};

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Invite link copied — send it to your farm member"); }
  catch (_) { toast("Invite link created — copy it below"); }
}

const fmtTime = (hm) => { const [h, m] = hm.split(":").map(Number); return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`; };
const monthName = (ym) => new Date(ym + "-01T00:00:00").toLocaleDateString(undefined, {month: "long", year: "numeric"});

function goalCard(gl) {
  const target = gl.target || (gl.kind === "progress" ? 100 : 0);
  const pct = target ? Math.min(100, Math.round((gl.current || 0) / target * 100)) : 0;
  const proj = (S.projects.find((p) => p.id === gl.project_id) || {}).name;
  const val = gl.kind === "savings" ? `${money(gl.current)} of ${target ? money(target) : "—"}` : `${Math.round(gl.current || 0)}% of ${Math.round(target)}%`;
  return `<div class="card"><div style="display:flex;justify-content:space-between;gap:8px"><b>${esc(gl.title)}</b><span class="badge">${gl.kind === "savings" ? "Savings" : "Progress"}</span></div>
    ${proj ? `<div class="muted">Project: ${esc(proj)}</div>` : ""}
    <div class="progress"><div style="width:${pct}%"></div></div><div>${val} <span class="muted">(${pct}%)</span></div>
    ${gl.due_date ? `<div class="muted ${gl.due_date < today() && pct < 100 ? "overdue" : ""}">By ${fmtDate(gl.due_date)}</div>` : ""}
    ${gl.notes ? `<p class="muted">${esc(gl.notes)}</p>` : ""}
    <div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap"><button class="small primary" data-bump="${gl.id}">${gl.kind === "savings" ? "+ Add savings" : "Update progress"}</button>
    <button class="small" data-edit="goals:${gl.id}">Edit</button><button class="small danger" data-del="goals:${gl.id}">Delete</button></div></div>`;
}

function bindGoals(root, goals, items = {}) {
  bindCrud(root, {...items, goals});
  root.querySelectorAll("[data-bump]").forEach((b) => b.onclick = () => {
    const gl = goals.find((x) => String(x.id) === b.dataset.bump);
    if (gl.kind === "savings") {
      openForm(`Add savings to “${gl.title}”`, [{name: "add", label: "Amount saved ($)", type: "number", step: "0.01", required: true}], {},
        (d) => api(`/api/goals/${gl.id}`, {method: "PUT", body: {current: (gl.current || 0) + Number(d.add || 0)}}));
    } else {
      openForm(`Progress on “${gl.title}”`, [{name: "current", label: "Percent done", type: "number", step: "1", required: true}], {current: gl.current},
        (d) => api(`/api/goals/${gl.id}`, {method: "PUT", body: {current: Math.min(Number(d.current), gl.target || 100)}}));
    }
  });
}

function shopResults(r) {
  const items = r.items.map((it) => `<div class="card"><b>${esc(it.name)}</b>
    <div>${esc(it.store)}${it.price ? ` · <b>${esc(it.price)}</b>` : ""}</div>${it.note ? `<div class="muted">${esc(it.note)}</div>` : ""}
    <div style="display:flex;gap:6px;margin-top:8px">${it.url ? `<a href="${esc(it.url)}" target="_blank" rel="noopener"><button class="small primary" type="button">View</button></a>` : ""}
    <button class="small" type="button" data-buy="${esc(`Buy ${it.name}${it.store ? ` (${it.store})` : ""}`)}">Add to tasks</button></div></div>`).join("");
  const src = r.sources.length ? `<div class="card"><b>Sources</b><ul>${r.sources.map((s) => `<li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a></li>`).join("")}</ul></div>` : "";
  return (items ? `<div class="grid">${items}</div>` : `<div class="empty">No clear listings came back${r.sources.length ? " — check the sources below" : ". Try different words"}.</div>`) + `<div style="margin-top:12px">${src}</div>`;
}
function bindShop(root) {
  root.querySelectorAll("[data-buy]").forEach((b) => b.onclick = () => api("/api/tasks", {method: "POST", body: {title: b.dataset.buy, priority: "medium"}}).then(() => toast("Added to Tasks")).catch(fail));
}

/* ---------- project detail ---------- */
async function projectDetail(v, pid) {
  let d;
  try { d = await api(`/api/projects/${pid}/detail`); } catch (e) { S.projectId = null; return render(); }
  const p = d.project, sites = d.photos.filter((x) => x.kind === "site"), renders = d.photos.filter((x) => x.kind === "render");
  const adviceBlock = p.advice_status === "given"
    ? `<details><summary>Build recommendation (given once)</summary><div class="advice">${esc(p.advice)}</div><p class="muted">Your plan above is what we go with.</p></details>`
    : p.advice_status === "declined" || p.advice_status === "pending" ? ""
    : `<div class="card" style="background:#fbfcf9"><b>Want a one-time build recommendation?</b>
       <p class="muted" style="margin:4px 0 8px">You'll get one suggestion for this project, then it's your call — we won't bring it up again.</p>
       <button class="small primary" id="adviceGo">Get recommendation</button> <button class="small" id="adviceNo">No thanks</button></div>`;
  v.innerHTML = `<div class="bar"><div><button class="small" id="back">← Projects</button></div>
      <div class="filters"><button data-edit="projects:${p.id}">Edit project</button><button class="danger" data-del="projects:${p.id}">Delete</button></div></div>
    <h2 style="margin:0 0 6px">${esc(p.name)}</h2>${p.description ? `<p>${esc(p.description)}</p>` : ""}
    <div class="card"><b>Your plan</b><p style="white-space:pre-wrap;margin:6px 0 0">${p.plan ? esc(p.plan) : `<span class="muted">No plan written yet — click Edit project to add it.</span>`}</p></div>
    ${adviceBlock}
    <div class="card"><div class="bar"><b>Site photos</b><div class="filters">
      <button type="button" class="primary small" id="takePhoto">Take photo</button>
      <button type="button" class="small" id="uploadPhoto">Upload photo</button>
      <input type="file" id="camInput" accept="image/*" capture="environment" class="hidden"><input type="file" id="fileInput" accept="image/*" multiple class="hidden"></div></div>
      ${sites.length ? `<div class="photos">${sites.map((ph) => `<div class="photo"><img src="${ph.url}" data-zoom="${ph.url}" alt="site photo">
        <div class="cap"><button class="small primary" data-vis="${ph.id}">Visualize build here</button><button class="small danger" data-delphoto="${ph.id}">✕</button></div></div>`).join("")}</div>`
        : `<p class="muted">Take or upload a photo of where you want to build.</p>`}</div>
    <div class="card"><b>What it'll look like</b>
      ${renders.length ? `<div class="photos" style="margin-top:8px">${renders.slice().reverse().map((ph) => `<div class="photo"><img src="${ph.url}" data-zoom="${ph.url}" alt="visualization">
        <div class="cap"><span class="muted">${esc(ph.prompt || "From your plan")}</span><button class="small danger" data-delphoto="${ph.id}">✕</button></div></div>`).join("")}</div>`
        : `<p class="muted">Pick a site photo and click “Visualize build here” to see the finished build in place, based on your plan.</p>`}</div>
    <div class="card"><div class="bar"><b>Project tasks</b>${addBtn("Task", "tasks", {project_id: p.id})}</div>${taskTable(d.tasks, false)}</div>
    <div class="card"><div class="bar"><b>Project goals</b>${addBtn("Goal", "goals", {project_id: p.id})}</div>
      ${d.goals.length ? `<div class="grid">${d.goals.map(goalCard).join("")}</div>` : `<p class="muted">Track % done or money saved for this build.</p>`}</div>`;
  $("#back").onclick = () => { S.projectId = null; render(); };
  bindGoals(v, d.goals, {projects: [p], tasks: d.tasks});
  v.querySelectorAll('[data-del^="projects:"]').forEach((b) => b.onclick = () => {
    if (!confirm("Delete this project, its photos and tasks?")) return;
    api(`/api/projects/${p.id}`, {method: "DELETE"}).then(() => { S.projectId = null; return refreshShared(); }).then(render).catch(fail);
  });
  v.querySelectorAll("[data-zoom]").forEach((im) => im.onclick = () => showImage(im.dataset.zoom));
  v.querySelectorAll("[data-delphoto]").forEach((b) => b.onclick = () => confirmDelete("this photo", `/api/photos/${b.dataset.delphoto}`));
  $("#takePhoto").onclick = () => $("#camInput").click();
  $("#uploadPhoto").onclick = () => $("#fileInput").click();
  const onFiles = async (ev) => {
    const files = [...ev.target.files];
    for (const file of files) {
      try { toast("Uploading photo…"); await api(`/api/projects/${p.id}/photos`, {method: "POST", body: {data: await shrink(file)}}); }
      catch (e) { fail(e); }
    }
    render();
  };
  $("#camInput").onchange = onFiles; $("#fileInput").onchange = onFiles;
  v.querySelectorAll("[data-vis]").forEach((b) => b.onclick = () => {
    openForm("Visualize the finished build", [{name: "details", label: "Anything to add for this picture? (optional)", type: "textarea", placeholder: "Red siding, door facing the pond"}], {},
      async (f) => {
        toast("Generating — this can take up to a minute…");
        await api(`/api/projects/${p.id}/visualize`, {method: "POST", body: {photo_id: Number(b.dataset.vis), details: f.details}});
        toast("Visualization ready");
      }, `<p class="muted">We'll draw the finished build into this photo using your plan.</p>`);
  });
  const go = $("#adviceGo");
  if (go) {
    go.onclick = async () => { go.disabled = true; go.textContent = "Thinking…"; try { await api(`/api/projects/${p.id}/advice`, {method: "POST", body: {}}); } catch (e) { fail(e); } render(); };
    $("#adviceNo").onclick = () => api(`/api/projects/${p.id}/advice`, {method: "POST", body: {decline: true}}).then(render).catch(fail);
  }
}

function shrink(file, max = 1600) {
  return new Promise((resolve, reject) => {
    const rd = new FileReader();
    rd.onerror = () => reject(new Error("Couldn't read that photo"));
    rd.onload = () => {
      const img = new Image();
      img.onload = () => {
        const s = Math.min(1, max / Math.max(img.width, img.height));
        const c = document.createElement("canvas"); c.width = Math.round(img.width * s); c.height = Math.round(img.height * s);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        resolve(c.toDataURL("image/jpeg", 0.85));
      };
      img.onerror = () => resolve(rd.result);
      img.src = rd.result;
    };
    rd.readAsDataURL(file);
  });
}

/* ---------- shell ---------- */
async function refreshShared() {
  [S.animals, S.projects] = await Promise.all([api("/api/animals"), api("/api/projects")]);
}
async function loadMe() {
  S.me = await api("/api/me");
  $("#farmName").textContent = S.me.user.farm_name;
  try { S.tabLabels = await api("/api/tab-labels"); } catch (_) { S.tabLabels = {}; }
}

async function render() {
  const v = $("#view");
  const tabs = visibleTabs();
  if (!tabs.some(([k]) => k === S.tab)) S.tab = tabs[0][0];
  $("#tabs").innerHTML = tabs.map(([k]) => `<button data-tab="${k}" class="${S.tab === k ? "active" : ""}">${esc(tabLabel(k))}</button>`).join("");
  $("#tabs").querySelectorAll("[data-tab]").forEach((b) => b.onclick = () => { S.tab = b.dataset.tab; S.projectId = null; localStorage.setItem("otf_tab", S.tab); render(); });
  try { if (!isGuest()) await refreshShared(); await (VIEWS[S.tab] || VIEWS.tasks)(v); }
  catch (e) { if (S.me) v.innerHTML = `<div class="empty error">${esc(e.message)}</div>`; }
}

let authMode = "login", inviteToken = new URLSearchParams(location.search).get("invite");
function setMode(m) {
  authMode = m;
  document.querySelectorAll(".seg button").forEach((b) => b.classList.toggle("active", b.dataset.mode === m));
  document.querySelectorAll(".signup-only").forEach((el) => el.classList.toggle("hidden", m !== "signup"));
  document.querySelectorAll(".owner-only").forEach((el) => el.classList.toggle("hidden", m !== "signup" || !!inviteToken));
  $("#authSubmit").textContent = m === "signup" ? (inviteToken ? "Join farm" : "Create account") : "Sign in";
  $("#authForm").elements.password.autocomplete = m === "signup" ? "new-password" : "current-password";
}
function showAuth() {
  S.me = null; $("#app").classList.add("hidden"); $("#logoutBtn").classList.add("hidden"); $("#auth").classList.remove("hidden"); $("#farmName").textContent = "";
}
async function showApp() {
  await loadMe();
  $("#auth").classList.add("hidden"); $("#app").classList.remove("hidden"); $("#logoutBtn").classList.remove("hidden");
  render();
}

document.querySelectorAll(".seg button").forEach((b) => b.onclick = () => setMode(b.dataset.mode));
$("#authForm").onsubmit = async (ev) => {
  ev.preventDefault();
  const body = Object.fromEntries(new FormData(ev.target));
  if (authMode === "signup" && inviteToken) body.invite = inviteToken;
  $("#authError").textContent = "";
  try {
    await api(authMode === "signup" ? "/api/signup" : "/api/login", {method: "POST", body});
    if (inviteToken) { history.replaceState(null, "", "/"); inviteToken = null; }
    await showApp();
  } catch (e) { $("#authError").textContent = e.message; }
};
$("#logoutBtn").onclick = () => api("/api/logout", {method: "POST"}).finally(showAuth);

(async function boot() {
  if (inviteToken) {
    try {
      const inv = await api(`/api/invites/${encodeURIComponent(inviteToken)}`);
      const b = $("#inviteBanner"); b.classList.remove("hidden");
      b.innerHTML = inv.role === "guest"
        ? `You've been invited as a guest on <b>${esc(inv.farm_name)}</b>. Create a free account to see the OfftheFARM instructions.`
        : `You've been invited to join <b>${esc(inv.farm_name)}</b>. Create your free account to get access.`;
      if (inv.email) $("#authForm").elements.email.value = inv.email;
    } catch (e) { inviteToken = null; const b = $("#inviteBanner"); b.classList.remove("hidden"); b.textContent = e.message; }
  }
  setMode(inviteToken ? "signup" : "login");
  try { await showApp(); } catch (_) { showAuth(); }
})();
