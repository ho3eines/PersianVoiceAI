"use strict";
(() => {
  const API = "/api/manage";
  const $ = (id) => document.getElementById(id);
  const state = { tab: "dashboard", settings: null, providers: [], activeProvider: "local", status: null, modelsKind: "llm", modelData: null, jobTimer: null, selectedDownload: "catalog", activeLog: "" };
  const titles = {
    dashboard: ["پیشخوان", "وضعیت دستیار محلی شما، یک‌جا"], settings: ["تنظیمات", "پیکربندی سرویس محلی بر اساس گزینه‌های پشتیبانی‌شده"],
    providers: ["ارائه‌دهنده‌ها", "انتخاب محلی یا اتصال سازگار با سرویس‌های دیگر"], models: ["مدل‌ها", "بررسی مدل‌های نصب‌شده و کاتالوگ‌های موجود"],
    services: ["سرویس‌ها", "وضعیت و کنترل سرویس‌های مدیریت‌پذیر"], help: ["راهنما", "نکات استفاده، ایمنی و عیب‌یابی"]
  };
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const faNumber = (n) => new Intl.NumberFormat("fa-IR").format(Number(n) || 0);
  const api = async (path, options = {}) => {
    let response;
    try {
      response = await fetch(API + path, { ...options, headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) } });
    } catch (error) { throw new Error("اتصال به سرور برقرار نشد. "+(error?.message || "")); }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.message || `خطای سرور (${response.status})`);
    return data;
  };
  function message(text, type = "success") { const el = $("globalMessage"); el.textContent = text || ""; el.className = text ? `global-message ${type}` : "global-message"; if (text) el.scrollIntoView({ behavior: "smooth", block: "nearest" }); }
  function badge(text, kind = "") { return `<span class="status-pill ${kind}">${esc(text)}</span>`; }
  function stateText(value) { return ({ ready: "آماده", running: "در حال اجرا", stopped: "متوقف", starting: "در حال شروع", failed: "خطا", error: "خطا", loading: "در حال بارگذاری", unconfigured: "تنظیم نشده", unknown: "نامشخص", idle: "آماده‌به‌کار" })[String(value || "unknown").toLowerCase()] || String(value || "نامشخص"); }
  function stateKind(value) { const s = String(value || "").toLowerCase(); return ["ready", "running", "ok", "healthy", "done"].includes(s) ? "ok" : ["failed", "error"].includes(s) ? "bad" : ["starting", "loading"].includes(s) ? "warn" : ""; }
  function showTab(tab) {
    if (!titles[tab]) return;
    state.tab = tab;
    document.querySelectorAll(".tab-view").forEach(el => el.classList.toggle("active", el.id === `tab-${tab}`));
    document.querySelectorAll(".nav-link[data-tab]").forEach(el => el.classList.toggle("active", el.dataset.tab === tab));
    $("pageTitle").textContent = titles[tab][0]; $("pageSubtitle").textContent = titles[tab][1];
    closeMenu();
    if (tab === "settings") loadSettings();
    if (tab === "providers") loadProviders();
    if (tab === "models") loadModels(state.modelsKind);
    if (tab === "services") loadServices();
  }
  document.querySelectorAll("[data-tab]").forEach(el => el.addEventListener("click", () => showTab(el.dataset.tab)));
  document.querySelectorAll("[data-go]").forEach(el => el.addEventListener("click", () => showTab(el.dataset.go)));
  const closeMenu = () => { $("sidebar").classList.remove("open"); $("scrim").classList.remove("show"); };
  $("menuToggle").addEventListener("click", () => { $("sidebar").classList.toggle("open"); $("scrim").classList.toggle("show"); });
  $("scrim").addEventListener("click", closeMenu);
  function renderStatus(data) {
    state.status = data;
    const ready = !!data.server?.ready;
    $("connectionDot").className = "dot " + (ready ? "ok" : "warn");
    $("connectionLabel").textContent = ready ? "سرویس محلی آماده" : "سرور محلی در دسترس";
    const comps = data.components || {};
    const items = [
      ["سرور برنامه", ready ? "آماده" : "در حال بررسی", `${data.server?.python ? "Python " + data.server.python : ""}${data.server?.uptime_s != null ? " · زمان اجرا " + faNumber(Math.round(data.server.uptime_s / 60)) + " دقیقه" : ""}`, ready ? "ok" : "warn"],
      ["مدل زبانی", comps.llm?.state, comps.llm?.model || comps.llm?.detail, stateKind(comps.llm?.state)],
      ["تشخیص گفتار · ASR", comps.asr?.state, comps.asr?.model || comps.asr?.detail, stateKind(comps.asr?.state)],
      ["تولید صدا · TTS", comps.tts?.state, comps.tts?.model || comps.tts?.detail, stateKind(comps.tts?.state)]
    ];
    $("statusCards").innerHTML = items.map(([label, val, detail, kind]) => `<article class="card stat"><small>${esc(label)}</small><b>${badge(stateText(val), kind)}</b><span class="muted">${esc(detail || "وضعیت گزارش‌شده توسط سرور")}</span></article>`).join("");
    const offline = $("offlineNotice");
    offline.className = "notice " + (data.offline_guarantee ? "success" : "warning");
    offline.textContent = data.offline_guarantee ? "✓ پردازش فعلی در حالت محلی است؛ داده‌ای به سرویس بیرونی فرستاده نمی‌شود." : `⚠ ارائه‌دهندهٔ فعال «${data.active_provider || "نامشخص"}» ممکن است داده را بیرون از رایانه بفرستد.`;
    renderServiceRows("dashboardServices", data.services || [], true);
    if (state.tab === "services") renderServiceRows("servicesList", data.services || [], false);
  }
  async function loadStatus() {
    try { renderStatus(await api("/status")); }
    catch (err) { $("connectionDot").className = "dot bad"; $("connectionLabel").textContent = "مدیریت در دسترس نیست"; $("statusCards").innerHTML = `<article class="card placeholder" style="grid-column:1/-1">وضعیت پنل از API خوانده نشد: ${esc(err.message)}</article>`; $("dashboardServices").innerHTML = `<div class="placeholder">${esc(err.message)}</div>`; if (state.tab === "services") $("servicesList").innerHTML = `<article class="card placeholder">${esc(err.message)}</article>`; }
  }
  function renderServiceRows(targetId, services, compact) {
    const root = $(targetId); if (!root) return;
    if (!services.length) { root.innerHTML = '<div class="placeholder">سرویسی از API مدیریت گزارش نشد.</div>'; return; }
    root.innerHTML = services.map(s => {
      const health = s.health?.detail ? ` · ${esc(s.health.detail)}` : "";
      const manageable = !!s.managed && ["llama-server", "voice", "api"].includes(s.id);
      const stateBadge = badge(stateText(s.state), stateKind(s.state));
      if (compact) return `<div class="service-row"><div class="item-info"><b>${esc(s.label_fa || s.id)}</b><small>${esc(s.health?.detail || (s.managed ? "تحت مدیریت این برنامه" : "فقط مشاهده؛ مدیریت این سرویس از این پنل در دسترس نیست"))}</small></div>${stateBadge}</div>`;
      const logsButton = `<button class="button quiet small" data-logs="${esc(s.id)}">گزارش‌ها</button>`;
      const running = s.state === "running";
      const actions = manageable
        ? `<div class="service-actions"><button class="button quiet small" data-service="${esc(s.id)}" data-action="start">شروع</button><button class="button quiet small" data-service="${esc(s.id)}" data-action="restart">راه‌اندازی دوباره</button><button class="button danger small" data-service="${esc(s.id)}" data-action="stop">توقف</button>${logsButton}</div>`
        : running
          ? `<div class="service-actions"><span class="muted">این سرویس بیرون از پنل اجرا شده است. برای کنترل کامل (توقف/راه‌اندازی دوباره)، آن را ببندید و از همین صفحه شروع کنید.</span>${logsButton}</div>`
          : `<div class="service-actions"><button class="button primary small" data-service="${esc(s.id)}" data-action="start">شروع از پنل</button>${logsButton}</div>`;
      return `<article class="card service-card"><div class="service-top"><div><h3>${esc(s.label_fa || s.id)}</h3><div class="muted">شناسه: <span class="ltr">${esc(s.id)}</span></div></div>${stateBadge}</div><div class="service-details"><span>مدیریت: ${s.managed ? "توسط این برنامه" : "فقط مشاهده"}</span><span>شناسهٔ فرایند: ${s.pid ?? "—"}</span><span>درگاه: ${s.port ?? "—"}</span><span>سلامت: ${s.health?.ok === true ? "سالم" : s.health?.ok === false ? "خطا" : "نامشخص"}${health}</span></div>${s.last_error ? `<div class="service-error">${esc(s.last_error)}</div>` : ""}${actions}${state.activeLog === s.id ? `<pre class="log-box" id="log-${esc(s.id)}">در حال دریافت گزارش…</pre>` : ""}</article>`;
    }).join("");
    root.querySelectorAll("[data-action]").forEach(btn => btn.addEventListener("click", () => serviceAction(btn.dataset.service, btn.dataset.action)));
    root.querySelectorAll("[data-logs]").forEach(btn => btn.addEventListener("click", () => loadLogs(btn.dataset.logs)));
    if (state.activeLog) loadLogs(state.activeLog, true);
  }
  async function serviceAction(id, action) {
    const s = (state.status?.services || []).find(x => x.id === id);
    if (!s) { message("وضعیت این سرویس در دسترس نیست؛ صفحه را دوباره بارگذاری کنید.", "warning"); return; }
    if (action !== "start" && !s.managed) {
      message("این سرویس بیرون از پنل اجرا شده است؛ پنل آن را متوقف نمی‌کند. برای کنترل کامل، آن را ببندید و از همین صفحه شروع کنید.", "warning");
      return;
    }
    if (action === "stop" && !confirm(`سرویس «${s.label_fa || id}» متوقف شود؟ درخواست‌های جاری ممکن است قطع شوند.`)) return;
    if (action === "restart" && !confirm(`سرویس «${s.label_fa || id}» دوباره راه‌اندازی شود؟ ممکن است درخواست‌های جاری قطع شوند.`)) return;
    try { const result = await api(`/services/${encodeURIComponent(id)}/${action}`, { method: "POST" }); message(result.detail || `درخواست ${action} برای سرویس ارسال شد.`); await loadStatus(); }
    catch (err) { message(`عملیات سرویس انجام نشد: ${err.message}`, "error"); }
  }
  async function loadServices() {
    await loadStatus();
    if (state.status) renderServiceRows("servicesList", state.status.services || [], false);
  }
  async function loadLogs(id, quiet = false) {
    state.activeLog = id;
    const holder = $(`log-${id}`);
    try { const data = await api(`/services/${encodeURIComponent(id)}/logs?tail=200`); const el = $(`log-${id}`); if (el) el.textContent = (data.lines || []).join("\n") || "گزارشی موجود نیست."; }
    catch (err) { const el = $(`log-${id}`); if (el) el.textContent = `خواندن گزارش ناموفق بود: ${err.message}`; else if (!quiet) message(err.message, "error"); }
  }
  const widgetOf = f => f.widget || (f.secret ? "secret" : f.type === "bool" ? "switch" : f.type === "int" || f.type === "float" ? "number" : "text");
  function inputFor(f) {
    const key = esc(f.key), id = "setting-" + key;
    const widget = widgetOf(f), value = f.value;
    const common = `id="${id}" data-setting-key="${key}" class="setting-control"`;
    if (widget === "switch") return `<input ${common} type="checkbox" ${value ? "checked" : ""}>`;
    if (widget === "secret") return `<input ${common} type="password" autocomplete="new-password" placeholder="${f.is_set ? "برای حفظ مقدار فعلی خالی بگذارید" : "در صورت نیاز وارد کنید"}"><small class="secret-set">${f.is_set ? "● مقدار محرمانه ذخیره شده است؛ هرگز نمایش داده نمی‌شود." : "مقداری ذخیره نشده است."}</small>`;
    if (widget === "textarea") return `<textarea ${common} rows="3">${esc(value ?? "")}</textarea>`;
    if (widget === "select") {
      const choices = Array.isArray(f.choices) ? f.choices : [];
      const options = choices.map(choice => {
        const v = choice && typeof choice === "object" ? (choice.value ?? choice.id ?? choice.key) : choice;
        const label = choice && typeof choice === "object" ? (choice.label_fa ?? choice.label ?? v) : v;
        return `<option value="${esc(v)}" ${String(v) === String(value) ? "selected" : ""}>${esc(label)}</option>`;
      }).join("");
      return `<select ${common}>${options || `<option value="${esc(value ?? "")}">${esc(value ?? "انتخاب کنید")}</option>`}</select>`;
    }
    const type = widget === "number" || ["int", "float", "number"].includes(f.type) ? "number" : "text";
    const isPath = widget === "path";
    return `<input ${common} type="${type}" value="${esc(value ?? "")}" ${f.minimum != null ? `min="${esc(f.minimum)}"` : ""} ${f.maximum != null ? `max="${esc(f.maximum)}"` : ""} ${f.type === "float" ? 'step="any"' : type === "number" ? 'step="1"' : ""} ${isPath ? 'spellcheck="false"' : ""}>`;
  }
  function renderSettings(data) {
    state.settings = data;
    const groups = data.groups || [];
    if (!groups.length) { $("settingsGroups").innerHTML = '<article class="card placeholder">سرور گروه تنظیماتی گزارش نکرد.</article>'; return; }
    $("settingsGroups").innerHTML = groups.map(group => `<section class="card settings-group"><div class="card-heading"><div><h3>${esc(group.title_fa || group.id)}</h3><p>${esc(group.description_fa || "")}</p></div><span class="tag">${(group.fields || []).length} گزینه</span></div><div class="settings-fields">${(group.fields || []).map(f => {
      const key = esc(f.key), widget = widgetOf(f), full = widget === "textarea" || widget === "secret";
      return `<div class="setting-field ${full ? "full" : ""}"><label class="setting-label" for="setting-${key}">${esc(f.label_fa || f.key)}${f.restart_required ? '<span class="tag">نیازمند راه‌اندازی مجدد</span>' : ""}${f.advanced ? '<span class="tag">پیشرفته</span>' : ""}</label>${inputFor(f)}${f.default != null && widget !== "secret" ? `<small class="setting-help">پیش‌فرض: ${esc(f.default)}</small>` : ""}<details class="setting-help"><summary>راهنمای این گزینه</summary><p>${esc(f.help_fa || "توضیحی از سرور برای این گزینه ارائه نشده است.")}</p></details></div>`;
    }).join("")}</div><button class="button quiet small" type="button" data-reset-group="${esc(group.id)}">بازنشانی گزینه‌های این بخش</button></section>`).join("");
    const issues = data.env_issues || [];
    $("envIssues").innerHTML = issues.map(issue => `<div class="issue">${esc(typeof issue === "string" ? issue : (issue.message_fa || issue.detail || JSON.stringify(issue)))}</div>`).join("");
    document.querySelectorAll("[data-reset-group]").forEach(btn => btn.addEventListener("click", () => resetGroup(btn.dataset.resetGroup)));
  }
  async function loadSettings() { $("settingsGroups").innerHTML = '<div class="card placeholder"><span class="spinner"></span>در حال دریافت تنظیمات…</div>'; try { renderSettings(await api("/settings")); } catch (err) { $("settingsGroups").innerHTML = `<div class="card placeholder">خواندن تنظیمات انجام نشد: ${esc(err.message)}</div>`; message(`تنظیمات در دسترس نیست: ${err.message}`, "error"); } }
  function parseSettingValue(el, field) {
    if (widgetOf(field) === "switch") return !!el.checked;
    if (widgetOf(field) === "secret") return el.value;
    if (el.type === "number") { if (el.value.trim() === "") return ""; const number = Number(el.value); if (!Number.isFinite(number)) throw new Error(`مقدار «${field.label_fa || field.key}» باید عددی باشد.`); return number; }
    return el.value;
  }
  $("settingsForm").addEventListener("submit", async event => {
    event.preventDefault(); const values = {};
    try {
      for (const group of state.settings?.groups || []) for (const field of group.fields || []) {
        const el = $(`setting-${field.key}`); if (!el) continue;
        const val = parseSettingValue(el, field);
        if (widgetOf(field) === "secret" && !val) continue; // blank secret means keep stored secret
        values[field.key] = val;
      }
    } catch (err) { message(err.message, "error"); return; }
    const btn = $("saveSettings"); btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>در حال ذخیره';
    try { const result = await api("/settings", { method: "PUT", body: JSON.stringify({ values, apply: true }) }); const restart = result.restart_required || []; $("settingsResult").textContent = restart.length ? `ذخیره شد؛ ${restart.length} گزینه نیازمند راه‌اندازی مجدد است.` : "تنظیمات ذخیره شد."; message(restart.length ? `تغییرات ثبت شدند. برای برخی تنظیمات راه‌اندازی مجدد لازم است: ${restart.join("، ")}` : "تنظیمات با موفقیت ذخیره شد.", restart.length ? "warning" : "success"); await loadSettings(); }
    catch (err) { $("settingsResult").textContent = "ذخیره ناموفق"; message(`ذخیرهٔ تنظیمات انجام نشد: ${err.message}`, "error"); }
    finally { btn.disabled = false; btn.textContent = "ذخیرهٔ تغییرات"; }
  });
  async function resetGroup(id) {
    const group = (state.settings?.groups || []).find(g => String(g.id) === String(id)); if (!group) return;
    const keys = (group.fields || []).map(f => f.key); if (!keys.length) return;
    if (!confirm(`همهٔ ${faNumber(keys.length)} گزینهٔ بخش «${group.title_fa || id}» به پیش‌فرض پروژه برگردند؟`)) return;
    try { const result = await api("/settings/reset", { method: "POST", body: JSON.stringify({ keys }) }); message(result.restart_required?.length ? "مقادیر بازنشانی شدند؛ راه‌اندازی مجدد لازم است." : "مقادیر به پیش‌فرض برگشتند.", result.restart_required?.length ? "warning" : "success"); await loadSettings(); }
    catch (err) { message(`بازنشانی انجام نشد: ${err.message}`, "error"); }
  }
  $("reloadSettings").addEventListener("click", loadSettings);
  function renderProviders(data) {
    state.providers = data.providers || []; state.activeProvider = data.active || "local";
    if (!state.providers.length) { $("providersList").innerHTML = '<div class="placeholder">ارائه‌دهنده‌ای از سرور گزارش نشد.</div>'; return; }
    $("providersList").innerHTML = state.providers.map(p => {
      const active = String(p.id) === String(state.activeProvider);
      const cloud = !p.offline && p.kind !== "llama_server";
      return `<article class="provider-row"><div class="item-info"><b>${esc(p.label_fa || p.id)} ${active ? badge("فعال", "ok") : ""} ${p.offline ? badge("محلی", "local") : badge("بیرونی", "warn")}</b><small>${esc(p.kind)} · ${esc(p.model || "بدون مدل")}</small><small class="ltr">${esc(p.base_url || "")}</small><small>${p.api_key_set ? "کلید API ذخیره‌شده · مقدار مخفی" : "کلید API ثبت نشده"}${p.enabled ? " · فعال برای استفاده" : " · غیرفعال"}</small>${p.last_test ? `<small>آزمون: ${p.last_test.ok ? "موفق" : "ناموفق"}${p.last_test.latency_ms != null ? " · " + faNumber(p.last_test.latency_ms) + "ms" : ""} · ${esc(p.last_test.detail || "")}</small>` : ""}</div><div class="model-actions"><button class="button quiet small" data-provider-edit="${esc(p.id)}">ویرایش</button><button class="button quiet small" data-provider-test="${esc(p.id)}">آزمون اتصال</button>${active ? "" : `<button class="button primary small" data-provider-active="${esc(p.id)}" data-cloud="${cloud}">فعال‌سازی</button>`}${p.id === "local" ? "" : `<button class="button danger small" data-provider-delete="${esc(p.id)}">حذف</button>`}</div></article>`;
    }).join("");
    document.querySelectorAll("[data-provider-edit]").forEach(b => b.addEventListener("click", () => editProvider(b.dataset.providerEdit)));
    document.querySelectorAll("[data-provider-test]").forEach(b => b.addEventListener("click", () => testProvider(b.dataset.providerTest)));
    document.querySelectorAll("[data-provider-active]").forEach(b => b.addEventListener("click", () => activateProvider(b.dataset.providerActive, b.dataset.cloud === "true")));
    document.querySelectorAll("[data-provider-delete]").forEach(b => b.addEventListener("click", () => deleteProvider(b.dataset.providerDelete)));
  }
  async function loadProviders() { $("providersList").innerHTML = '<div class="placeholder"><span class="spinner"></span>در حال دریافت ارائه‌دهنده‌ها…</div>'; try { renderProviders(await api("/providers")); } catch (err) { $("providersList").innerHTML = `<div class="placeholder">فهرست ارائه‌دهنده‌ها در دسترس نیست: ${esc(err.message)}</div>`; } }
  function resetProviderForm() { $("providerForm").reset(); $("providerId").value = ""; $("providerFormTitle").textContent = "تنظیم ارائه‌دهنده"; $("providerEnabled").checked = false; $("providerResult").textContent = ""; $("providerKey").value = ""; }
  function editProvider(id) { const p = state.providers.find(item => String(item.id) === String(id)); if (!p) return; $("providerId").value = p.id; $("providerKind").value = p.kind; $("providerLabel").value = p.label_fa || ""; $("providerUrl").value = p.base_url || ""; $("providerModel").value = p.model || ""; $("providerKey").value = ""; $("providerKey").placeholder = p.api_key_set ? "برای حفظ کلید فعلی خالی بگذارید" : "در صورت نیاز وارد کنید"; $("providerEnabled").checked = !!p.enabled; $("providerFormTitle").textContent = `ویرایش ${p.label_fa || p.id}`; updateProviderPrivacy(); $("providerForm").scrollIntoView({ behavior: "smooth", block: "center" }); }
  function updateProviderPrivacy() { const kind = $("providerKind").value; const external = kind !== "llama_server"; $("externalConsentRow").classList.toggle("hidden", !external); if (!external) $("externalConsent").checked = false; }
  $("providerKind").addEventListener("change", updateProviderPrivacy);
  $("providerForm").addEventListener("submit", async event => {
    event.preventDefault(); const id = $("providerId").value.trim(); const kind = $("providerKind").value; const external = kind !== "llama_server";
    if (external && !$("externalConsent").checked) { message("برای ذخیرهٔ سرویس بیرونی، تأیید آگاهانهٔ احتمال خروج داده را فعال کنید.", "warning"); return; }
    if (external && !confirm("این تنظیم به یک ارائه‌دهندهٔ بیرونی متصل است. متن گفتگو و رونویسی ممکن است خارج از رایانه ارسال شود. تنظیمات را ذخیره می‌کنید؟")) return;
    const body = { kind, label_fa: $("providerLabel").value.trim(), base_url: $("providerUrl").value.trim(), model: $("providerModel").value.trim(), enabled: $("providerEnabled").checked };
    const key = $("providerKey").value; if (key) body.api_key = key; // Never fetch or expose a saved key.
    const btn = $("saveProvider"); btn.disabled = true;
    try { await api(`/providers/${encodeURIComponent(id || providerSlug(body.label_fa))}`, { method: "PUT", body: JSON.stringify(body) }); $("providerKey").value = ""; message("ارائه‌دهنده ذخیره شد؛ مقدار کلید از رابط خوانده یا نمایش داده نمی‌شود."); resetProviderForm(); await loadProviders(); }
    catch (err) { $("providerResult").textContent = `ذخیره ناموفق: ${err.message}`; message(`ذخیرهٔ ارائه‌دهنده انجام نشد: ${err.message}`, "error"); }
    finally { btn.disabled = false; }
  });
  function providerSlug(label) { const normalized = label.toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-|-$/g, ""); return normalized || `provider-${Date.now()}`; }
  $("cancelProvider").addEventListener("click", resetProviderForm);
  $("reloadProviders").addEventListener("click", loadProviders);
  async function testProvider(id) { try { const result = await api(`/providers/${encodeURIComponent(id)}/test`, { method: "POST", body: JSON.stringify({}) }); message(result.ok ? `آزمون اتصال موفق بود (${result.latency_ms ?? "—"}ms). ${result.detail || ""}` : `آزمون اتصال ناموفق بود: ${result.detail || ""}`, result.ok ? "success" : "error"); await loadProviders(); } catch (err) { message(`آزمون اتصال ناموفق بود: ${err.message}`, "error"); } }
  async function activateProvider(id, cloud) { if (cloud && !confirm("با فعال‌سازی، متن گفتگو و در مسیر صوتی رونویسی ممکن است از این رایانه خارج شود. مطمئن هستید؟")) return; try { const result = await api("/providers/active", { method: "POST", body: JSON.stringify({ id }) }); if (result.warning_fa) message(result.warning_fa, "warning"); else message("ارائه‌دهندهٔ انتخاب‌شده فعال شد."); await loadProviders(); await loadStatus(); } catch (err) { message(`فعال‌سازی ارائه‌دهنده ناموفق بود: ${err.message}`, "error"); } }
  async function deleteProvider(id) { if (!confirm("این ارائه‌دهنده از فهرست حذف شود؟ این کار ممکن است تنظیمات و کلید ذخیره‌شدهٔ آن را پاک کند.")) return; try { await api(`/providers/${encodeURIComponent(id)}`, { method: "DELETE" }); message("ارائه‌دهنده حذف شد."); await loadProviders(); } catch (err) { message(`حذف ارائه‌دهنده انجام نشد: ${err.message}`, "error"); } }
  $("modelKinds").addEventListener("click", event => { const btn = event.target.closest("[data-kind]"); if (!btn) return; state.modelsKind = btn.dataset.kind; document.querySelectorAll("#modelKinds .filter").forEach(x => x.classList.toggle("active", x === btn)); loadModels(state.modelsKind); });
  async function loadModels(kind = state.modelsKind) {
    $("installedModels").innerHTML = '<div class="placeholder"><span class="spinner"></span>در حال دریافت فهرست…</div>'; $("catalogModels").innerHTML = '<div class="placeholder"><span class="spinner"></span>در حال دریافت کاتالوگ…</div>';
    try { const data = await api(`/models?kind=${encodeURIComponent(kind)}`); state.modelData = data; renderModels(data); }
    catch (err) { $("installedModels").innerHTML = `<div class="placeholder">مدل‌ها در دسترس نیستند: ${esc(err.message)}</div>`; $("catalogModels").innerHTML = `<div class="placeholder">${esc(err.message)}</div>`; }
  }
  function sizeLabel(bytes) { if (bytes == null) return ""; const n = Number(bytes); if (!Number.isFinite(n)) return ""; const units = ["B", "KB", "MB", "GB", "TB"]; let v = n, i = 0; while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; } return `${new Intl.NumberFormat("fa-IR", { maximumFractionDigits: 1 }).format(v)} ${units[i]}`; }
  function renderModels(data) {
    $("modelsDir").textContent = data.models_dir ? `پوشه: ${data.models_dir}` : "فهرست گزارش‌شده توسط سرور";
    const installed = data.installed || [], catalog = data.catalog || [];
    $("installedModels").innerHTML = installed.length ? installed.map(m => `<div class="model-row"><div class="item-info"><b>${esc(m.name || m.id)} ${m.active ? badge("فعال", "ok") : ""} ${m.valid === false ? badge("نیازمند بررسی", "bad") : badge("موجود", "local")}</b><small>${esc(m.path || "")} ${m.size_bytes != null ? " · " + sizeLabel(m.size_bytes) : ""}</small>${m.detail ? `<small>${esc(m.detail)}</small>` : ""}</div><div class="model-actions">${m.active ? "" : `<button class="button primary small" data-model-activate="${esc(m.id)}">فعال‌سازی</button><button class="button danger small" data-model-delete="${esc(m.id)}">حذف</button>`}</div></div>`).join("") : '<div class="placeholder">مدل نصب‌شده‌ای گزارش نشد.</div>';
    $("catalogModels").innerHTML = catalog.length ? catalog.map(m => `<div class="model-row"><div class="item-info"><b>${esc(m.label_fa || m.id)} ${m.installed ? badge("نصب‌شده", "ok") : ""}</b><small>${esc(m.approx_size || "اندازهٔ نامشخص")}${m.needs ? " · " + esc(m.needs) : ""}</small></div>${m.installed ? "" : `<button class="button primary small" data-download-id="${esc(m.id)}">دریافت</button>`}</div>`).join("") : '<div class="placeholder">گزینه‌ای در کاتالوگ این نوع مدل گزارش نشد.</div>';
    const select = $("catalogSelect"); select.innerHTML = '<option value="">یک مدل را انتخاب کنید</option>' + catalog.filter(m => !m.installed).map(m => `<option value="${esc(m.id)}">${esc(m.label_fa || m.id)}${m.approx_size ? " · " + esc(m.approx_size) : ""}</option>`).join("");
    document.querySelectorAll("[data-download-id]").forEach(btn => btn.addEventListener("click", () => downloadModel({ kind: data.kind || state.modelsKind, catalog_id: btn.dataset.downloadId })));
    document.querySelectorAll("[data-model-activate]").forEach(btn => btn.addEventListener("click", () => activateModel(data.kind || state.modelsKind, btn.dataset.modelActivate)));
    document.querySelectorAll("[data-model-delete]").forEach(btn => btn.addEventListener("click", () => deleteModel(data.kind || state.modelsKind, btn.dataset.modelDelete)));
  }
  $("reloadModels").addEventListener("click", () => loadModels(state.modelsKind));
  $("catalogDownloadForm").addEventListener("submit", event => { event.preventDefault(); const id = $("catalogSelect").value; if (!id) { message("ابتدا یک مدل از کاتالوگ انتخاب کنید.", "warning"); return; } downloadModel({ kind: state.modelsKind, catalog_id: id }); });
  async function downloadModel(body) { const label = body.catalog_id || body.name || "مدل"; if (!confirm(`دریافت «${label}» به اینترنت وصل می‌شود و ممکن است فایل بزرگی بگیرد. ادامه دهید؟`)) return; try { const result = await api("/models/download", { method: "POST", body: JSON.stringify(body) }); if (!result.job_id) throw new Error("سرور شناسهٔ کار دریافت را برنگرداند."); message("درخواست دریافت شروع شد؛ وضعیت را همین‌جا می‌توانید ببینید."); startJob(result.job_id); } catch (err) { message(`شروع دریافت انجام نشد: ${err.message}`, "error"); } }
  $("customDownloadForm").addEventListener("submit", event => { event.preventDefault(); const raw = $("customUrls").value.split(/\r?\n/).map(s => s.trim()).filter(Boolean); if (!raw.length) { message("نشانی فایل لازم است.", "warning"); return; } let files; try { files = raw.map(url => { const parsed = new URL(url); if (parsed.protocol !== "https:") throw new Error("برای دریافت دلخواه، فقط HTTPS پذیرفته می‌شود."); const name = decodeURIComponent(parsed.pathname.split("/").filter(Boolean).pop() || "model-file"); return { url: parsed.href, name }; }); } catch (err) { message(err.message || "نشانی فایل نامعتبر است.", "error"); return; } if (!confirm("فایل(ها) از نشانی(های) واردشده روی میزبان محلی دریافت می‌شوند. مطمئن هستید؟")) return; downloadModel({ kind: $("customKind").value, name: $("customName").value.trim(), files }); });
  document.querySelectorAll("[data-download]").forEach(btn => btn.addEventListener("click", () => { state.selectedDownload = btn.dataset.download; document.querySelectorAll("[data-download]").forEach(x => x.classList.toggle("active", x === btn)); $("catalogDownloadForm").classList.toggle("hidden", state.selectedDownload !== "catalog"); $("customDownloadForm").classList.toggle("hidden", state.selectedDownload !== "custom"); }));
  async function startJob(id) { clearInterval(state.jobTimer); $("jobBox").classList.remove("hidden"); $("jobTitle").textContent = `دریافت در حال پیگیری · ${id}`; $("jobDetail").textContent = "در انتظار وضعیت از سرور…"; let terminal = false; const poll = async () => { try { const job = await api(`/jobs/${encodeURIComponent(id)}`); const percent = Math.max(0, Math.min(100, Number(job.percent || 0))); $("jobProgress").style.width = `${percent}%`; $("jobPercent").textContent = `${faNumber(Math.round(percent))}٪`; $("jobTitle").textContent = job.state === "done" ? "دریافت کامل شد" : job.state === "failed" ? "دریافت ناموفق بود" : `دریافت: ${stateText(job.state)}`; $("jobDetail").textContent = [job.error, job.path, job.total ? `${sizeLabel(job.bytes)} از ${sizeLabel(job.total)}` : sizeLabel(job.bytes), job.speed_bps ? `${sizeLabel(job.speed_bps)}/s` : ""].filter(Boolean).join(" · "); if (["done", "failed", "cancelled"].includes(job.state)) { terminal = true; clearInterval(state.jobTimer); state.jobTimer = null; if (job.state === "done") { message("مدل دریافت شد؛ فهرست مدل‌ها را بازبینی کنید."); await loadModels(state.modelsKind); } else if (job.state === "failed") message(`دریافت ناموفق بود: ${job.error || "خطای نامشخص"}`, "error"); } } catch (err) { terminal = true; clearInterval(state.jobTimer); state.jobTimer = null; $("jobDetail").textContent = `پیگیری کار ناموفق شد: ${err.message}`; } }; await poll(); if (!terminal) state.jobTimer = setInterval(poll, 1500); }
  async function activateModel(kind, id) { if (!confirm(`مدل «${id}» برای ${kind} فعال شود؟ ممکن است راه‌اندازی مجدد لازم باشد.`)) return; try { const result = await api("/models/activate", { method: "POST", body: JSON.stringify({ kind, id }) }); message((result.restart_required || []).length ? "مدل فعال شد؛ برای اعمال تغییر، راه‌اندازی مجدد سرویس لازم است." : "مدل فعال شد.", (result.restart_required || []).length ? "warning" : "success"); await loadModels(kind); await loadStatus(); } catch (err) { message(`فعال‌سازی مدل انجام نشد: ${err.message}`, "error"); } }
  async function deleteModel(kind, id) { if (!confirm(`مدل «${id}» از حافظهٔ میزبان حذف شود؟ این کار قابل بازگشت نیست.`)) return; try { await api(`/models/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`, { method: "DELETE" }); message("درخواست حذف مدل انجام شد."); await loadModels(kind); } catch (err) { message(`حذف مدل انجام نشد: ${err.message}`, "error"); } }
  $("reloadServices").addEventListener("click", loadServices);
  $("refreshButton").addEventListener("click", async () => { message(""); await loadStatus(); if (state.tab === "settings") await loadSettings(); if (state.tab === "providers") await loadProviders(); if (state.tab === "models") await loadModels(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeMenu(); });
  loadStatus(); setInterval(() => { if (state.tab === "dashboard" || state.tab === "services") loadStatus(); }, 7000);
})();
