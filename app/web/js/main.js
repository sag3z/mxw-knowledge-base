/* main.js — каркас: роутинг по hash, боковая панель, стример-мод, точка входа */
import { $, nav, initUI, setAnon, isAnon, setGraph, setAuthorLUT, parseHash, fmtDate, ensureGraph, drawerClose } from "./ui.js";
import { convInit, openConv } from "./conv.js";
import { mountMap, mapInit } from "./map.js";
import { mountEvents } from "./events.js";
import { mountPeople, openPerson } from "./people.js";
import { mountTopics } from "./topics.js";
import { mountArticles } from "./articles.js";
import { chatInit, chatRoute, chatNew } from "./chat.js";
import { settingsInit } from "./settings.js";
import { fillIcons } from "./icons.js";

const ROUTE_TITLE = { chat: "Чат", map: "Карта", events: "Активности", people: "Люди", topics: "Темы", articles: "Статьи" };

/* ---------- разделы ---------- */
function showView(route, params) {
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("on", v.dataset.view === route));
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", a.dataset.route === route));
  $("#mTitle").textContent = ROUTE_TITLE[route] || "MXW";
  document.body.dataset.route = route;
  document.body.classList.remove("side-over");   // на телефоне меню закрывается при переходе
  if (route === "chat") chatRoute(params);
  else if (route === "map") mountMap();
  else if (route === "events") mountEvents();
  else if (route === "people") mountPeople(params);
  else if (route === "topics") mountTopics();
  else if (route === "articles") mountArticles(params);
}

/* ---------- роутер: hash → раздел + открытый разговор/статья/человек в drawer ---------- */
function onHash() {
  const { route, params } = parseHash();
  showView(route, params);
  const conv = params.get("conv");
  const article = params.get("article");
  /* старые ссылки вида #/map?article=… — статья теперь отдельная страница */
  if (article && route !== "articles") { nav("articles", { article }, false); onHash(); return; }
  const person = params.get("person");
  if (conv) openConv(conv, { q: params.get("q"), focus: params.get("focus") });
  else if (person) openPerson(person, { push: false });
  else drawerClose();   // параметров нет — панель закрываем (и чистим ?q/?focus из URL)
}

/* ---------- боковая панель ---------- */
function wireSidebar() {
  const narrow = matchMedia("(max-width: 900px)");
  $("#sideBurger").addEventListener("click", () => {
    if (narrow.matches) document.body.classList.toggle("side-over");
    else {
      const min = document.body.classList.toggle("side-min");
      localStorage.setItem("mxw_side_min", min ? "1" : "0");
    }
  });
  $("#sideVeil").addEventListener("click", () => document.body.classList.remove("side-over"));
  $("#mBurger").addEventListener("click", () => document.body.classList.add("side-over"));
  $("#mNew").addEventListener("click", () => $("#newChat").click());
  $("#nav").addEventListener("click", () => document.body.classList.remove("side-over"));
  document.addEventListener("keydown", e => { if (e.key === "Escape") document.body.classList.remove("side-over"); });
  /* свайп влево по открытому меню — закрыть */
  let sx = null;
  $("#side").addEventListener("touchstart", e => { sx = e.touches[0].clientX; }, { passive: true });
  $("#side").addEventListener("touchend", e => {
    if (sx != null && sx - e.changedTouches[0].clientX > 60) document.body.classList.remove("side-over");
    sx = null;
  });
  if (!narrow.matches && localStorage.getItem("mxw_side_min") === "1") document.body.classList.add("side-min");
  $("#newChat").addEventListener("click", () => {
    if (parseHash().route === "chat") chatNew();   // уже в чате — просто начинаем новый
    else location.hash = "#/chat";
  });
  $("#anonBtn").addEventListener("click", () => setAnon(!isAnon()));
  /* обводка фокуса — только с клавиатуры: клик мышью/тачем снимает фокус с кнопки панели
     (после Tab/Enter Chromium показывает :focus-visible и на клик — см. скриншот пользователя) */
  $("#side").addEventListener("click", e => {
    const b = e.target.closest("button");
    if (b && e.detail) b.blur();   // e.detail>0 — клик мышью/тачем; Enter с клавиатуры даёт 0
  });
}

/* ---------- boot ---------- */
// каждый шаг отдельно: ошибка в одном разделе (например, испорченные данные в localStorage) не должна гасить весь сайт
for (const [name, init] of [["icons", () => fillIcons()], ["ui", initUI], ["conv", convInit], ["map", mapInit], ["chat", chatInit],
                            ["sidebar", wireSidebar], ["settings", settingsInit], ["anon", () => setAnon(isAnon())]]) {
  try { init(); } catch (e) { console.error(`init ${name} failed`, e); }
}

ensureGraph().then(g => {
  setGraph(g);
  setAuthorLUT(g.authors);                                   // псевдонимы «переслано от …»
  const last = g.hist.length ? g.hist[g.hist.length - 1][0] : "";
  $("#slice").textContent = last ? "срез чата: " + fmtDate(last) : "";
});

window.addEventListener("hashchange", onHash);
onHash();
