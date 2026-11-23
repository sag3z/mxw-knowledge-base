/* topics.js — раздел «Темы» (#/topics): о чём говорили в чате по неделям.
   Данные — /api/trends?by=category (кластеры не показываем): недельные ряды категорий,
   разговоры тем по неделям (drawer по клику), топ-теги месяцев («Главные проекты месяца»).
   График — stacked area (d3): baseline 0, кривая monotone, зазоры 1px цветом фона;
   категории вне топ-8 — в «Прочее» серым снизу. */
import { icon } from "./icons.js";
import * as api from "./api.js";
import {
  $, esc, escAttr, CAT, MONS, convCard, ensureGraph, nav, parseHash,
  drawerCur, drawerSetContent, drawerOpenUI, drawerSetTitle,
} from "./ui.js";

const d3 = window.d3;
const OTHER_KEY = "__other";
const OTHER_COLOR = "#5a5c66";
const PERIODS = [["all", "Всё время"], ["3m", "3 месяца"], ["1m", "Месяц"]];
const MON_N = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль",
  "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
const MON_P = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле",
  "августе", "сентябре", "октябре", "ноябре", "декабре"];
const fmtN = n => (n || 0).toLocaleString("ru-RU");
const plural = (n, one, few, many) => {
  const a = Math.abs(n) % 100, b = a % 10;
  return a > 10 && a < 20 ? many : b > 1 && b < 5 ? few : b === 1 ? one : many;
};
const fmtAxis = v => v >= 1000
  ? (v / 1000).toLocaleString("ru-RU", { maximumFractionDigits: 1 }) + " тыс" : String(v);

/* ---------- состояние ---------- */
let mounted = false;
let T = null;                 // /api/trends (кэш на сессию)
let tp = null;                // промис загрузки
let period = localStorage.getItem("mxw_tp_period") || "all";
let hidden = new Set();       // скрытые категории (клик по легенде)
let showTable = false;
let hoverI = -1;

const catName = k => (CAT[k] || [k])[0];
const catColor = k => (CAT[k] || CAT.other)[1];

/* ---------- неделя: «14–20 авг» ---------- */
function weekLabel(w) {
  const d0 = new Date(w + "T12:00:00");
  const d1 = new Date(d0.getTime() + 6 * 864e5);
  const a = `${d0.getDate()} ${MONS[d0.getMonth()]}`, b = `${d1.getDate()} ${MONS[d1.getMonth()]}`;
  return a === b ? a : `${a}–${b}`;
}
const weekDay = w => new Date(w + "T12:00:00");

/* ---------- загрузка ---------- */
function ensureTrends() {
  if (!tp) tp = api.getTrends("category").then(d => { T = d; return d; });
  return tp;
}

export function mountTopics() {
  if (!mounted) {
    mounted = true;
    buildDOM();
    wire();
  }
  return ensureTrends().then(render, err => {
    $("#tpStats").innerHTML = "";
    $("#tpChart").innerHTML = `<div class="empty"><div class="e-ic">${icon("trending", 26)}</div><div class="e-t">Не удалось загрузить темы</div><div class="e-h">${esc(err.message)}</div></div>`;
  });
}

/* ---------- DOM ---------- */
function buildDOM() {
  const root = document.querySelector('.view[data-view="topics"]');
  root.innerHTML = `
    <div class="view-head">
      <div class="view-title">
        <h1>О чём говорили в чате</h1>
        <p class="sub">Сколько сообщений в неделю было по каждой теме. Наведи, чтобы увидеть цифры; нажми на тему, чтобы прочитать разговоры этой недели</p>
      </div>
      <div class="seg" id="tpPeriod">${PERIODS.map(([v, l]) =>
        `<button data-v="${v}" title="Показать статистику за ${l.toLowerCase()}">${l}</button>`).join("")}</div>
    </div>
    <div class="tp-stats" id="tpStats">
      <div class="tp-stat skel"></div><div class="tp-stat skel"></div><div class="tp-stat skel"></div>
    </div>
    <div class="view-scroll">
      <div class="tp-chartcard">
        <div class="tp-legend" id="tpLegend"><span class="loading">Загружаю</span></div>
        <div id="tpChart"></div>
        <div id="tpTable" hidden></div>
        <button class="tp-ttable btn ghost sm" id="tpToggle" title="Переключить график и таблицу с цифрами">Показать таблицей</button>
      </div>
      <div class="tp-month" id="tpMonth"></div>
    </div>`;
}

function wire() {
  $("#tpPeriod").addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b || b.dataset.v === period) return;
    period = b.dataset.v;
    localStorage.setItem("mxw_tp_period", period);
    render();
  });
  $("#tpLegend").addEventListener("click", e => {
    const b = e.target.closest(".tp-lg");
    if (!b) return;
    const k = b.dataset.k;
    hidden.has(k) ? hidden.delete(k) : hidden.add(k);
    render();
  });
  $("#tpToggle").addEventListener("click", () => {
    showTable = !showTable;
    $("#tpToggle").textContent = showTable ? "Показать график" : "Показать таблицей";
    render();
  });
  let rt = 0;
  new ResizeObserver(() => {   // перестроить график под новую ширину
    if (mounted && T && !showTable) { clearTimeout(rt); rt = setTimeout(renderChart, 120); }
  }).observe($("#tpChart"));
}

/* ---------- данные ---------- */
function sliceInfo() {
  const weeksAll = T.weeks || [];
  let from = 0;
  if (period !== "all" && weeksAll.length) {
    const days = period === "1m" ? 31 : 92;
    const minDate = new Date(weekDay(weeksAll[weeksAll.length - 1]).getTime() - days * 864e5);
    from = weeksAll.findIndex(w => weekDay(w) >= minDate);
    if (from < 0) from = 0;
  }
  return { weeksAll, from, weeks: weeksAll.slice(from) };
}

/* видимые серии за период: топ-8 по сумме + «Прочее» (сумма мелких), по убыванию total */
function seriesData({ weeks, from }) {
  const defs = (T.series || [])
    .filter(s => !hidden.has(s.key))
    .map(s => ({
      key: s.key, name: catName(s.key), color: catColor(s.key),
      values: (s.values || []).slice(from),
      total: (s.values || []).slice(from).reduce((a, b) => a + (b || 0), 0),
    }))
    .filter(s => s.total > 0 || (s.values || []).some(v => v))
    .sort((a, b) => b.total - a.total);
  const top = defs.slice(0, 8).slice();
  const rest = defs.slice(8);
  if (rest.length) {
    const ov = new Array(weeks.length).fill(0);
    let total = 0;
    for (const r of rest) { r.values.forEach((v, i) => { ov[i] += v || 0; }); total += r.total; }
    if (total > 0) top.push({ key: OTHER_KEY, name: "Прочее", color: OTHER_COLOR, values: ov, total });
  }
  return top;
}

/* стек: «Прочее» снизу, дальше по убыванию total */
function stackKeys(sers) {
  const other = sers.find(s => s.key === OTHER_KEY);
  const rest = sers.filter(s => s.key !== OTHER_KEY);
  return other ? [other, ...rest] : rest;
}

/* ---------- рендер ---------- */
function render() {
  if (!T) return;
  const si = sliceInfo();
  const sers = seriesData(si);
  renderStats(si, sers);
  renderLegend(sers);
  if (showTable) { $("#tpChart").innerHTML = ""; renderTable(si, sers); }
  else { $("#tpTable").innerHTML = ""; renderChart(si, sers); }
  renderMonth();
}

/* карточки-итоги за выбранный период */
function renderStats({ weeksAll, weeks }, sers) {
  const total = sers.reduce((a, s) => a + s.total, 0);
  const best = sers.filter(s => s.key !== OTHER_KEY)[0] || sers[0];

  /* рост: последние 4 недели к предыдущим 4 (по всему ряду, не по срезу) */
  const last4 = new Map(), prev4 = new Map();
  for (const s of (T.series || [])) {
    const v = s.values || [];
    if (hidden.has(s.key)) continue;
    let a = 0, b = 0;
    for (let i = Math.max(0, v.length - 4); i < v.length; i++) a += v[i] || 0;
    for (let i = Math.max(0, v.length - 8); i < Math.max(0, v.length - 4); i++) b += v[i] || 0;
    last4.set(s.key, a); prev4.set(s.key, b);
  }
  let grow = null;
  for (const s of (T.series || [])) {
    if (hidden.has(s.key)) continue;
    const a = last4.get(s.key) || 0, b = prev4.get(s.key) || 0;
    if (a < 10) continue;                       // слишком тихая тема — не сравниваем
    const g = b > 0 ? (a - b) / b : 1;
    if (!grow || g > grow.g) grow = { key: s.key, g, a, b };
  }
  const growHtml = grow
    ? `<div class="ts-v">${grow.g >= 0 ? "+" : ""}${Math.round(grow.g * 100)}%</div>
       <div class="ts-l">Больше всего выросла</div>
       <div class="ts-s">${esc(catName(grow.key))} — ${fmtN(grow.a)} против ${fmtN(grow.b)} сообщений<br>(последние 4 недели к предыдущим 4)</div>`
    : `<div class="ts-v">—</div><div class="ts-l">Больше всего выросла</div>
       <div class="ts-s">мало данных за последние 8 недель</div>`;

  $("#tpStats").innerHTML = `
    <div class="tp-stat">
      <div class="ts-v">${best ? esc(best.name) : "—"}</div>
      <div class="ts-l">Самая обсуждаемая тема</div>
      <div class="ts-s">${best ? fmtN(best.total) : 0} ${plural(best ? best.total : 0, "сообщение", "сообщения", "сообщений")} за период</div>
    </div>
    <div class="tp-stat">${growHtml}</div>
    <div class="tp-stat">
      <div class="ts-v">${fmtN(total)}</div>
      <div class="ts-l">Всего сообщений</div>
      <div class="ts-s">${weeks.length} ${plural(weeks.length, "неделя", "недели", "недель")} в графике</div>
    </div>`;
}

/* легенда: квадрат + название + сумма; клик — скрыть/показать */
function renderLegend(sers) {
  const keys = stackKeys(sers);
  if (!keys.length) { $("#tpLegend").innerHTML = `<span class="dsub">Нет данных</span>`; return; }
  $("#tpLegend").innerHTML = keys.map(s => {
    const off = hidden.has(s.key) || (s.key === OTHER_KEY && sers.every(x => x.key !== OTHER_KEY));
    return `<button class="tp-lg${off ? " off" : ""}" data-k="${escAttr(s.key)}"
        title="${s.key === OTHER_KEY ? "Сумма мелких тем" : "Скрыть/показать тему на графике"}">
        <i style="background:${s.color}"></i>${esc(s.name)}<span>${fmtN(s.total)}</span></button>`;
  }).join("");
}

/* ---------- график (stacked area) ---------- */
function renderChart(si = sliceInfo(), sers = seriesData(si)) {   // без аргументов — ResizeObserver
  const box = $("#tpChart");
  const keys = stackKeys(sers);
  const { weeks } = si;
  box.innerHTML = "";
  if (!weeks.length || !keys.length) {
    box.innerHTML = `<div class="empty"><div class="e-ic">${icon("trending", 26)}</div><div class="e-t">Нет данных за период</div></div>`;
    return;
  }
  const W = Math.max(320, box.clientWidth), H = 360;
  const m = { t: 12, r: 14, b: 26, l: 48 };
  const svg = d3.create("svg").attr("width", W).attr("height", H).attr("class", "tp-svg");
  const g = svg.append("g");

  const data = weeks.map((w, i) => {
    const o = { week: w };
    for (const s of keys) o[s.key] = s.values[i] || 0;
    return o;
  });
  const stack = d3.stack().keys(keys.map(s => s.key)).order(d3.stackOrderNone);
  const layers = stack(data);
  const maxV = d3.max(layers, l => d3.max(l, d => d[1])) || 1;

  const x = d3.scaleLinear().domain([0, weeks.length - 1]).range([m.l, W - m.r]);
  const y = d3.scaleLinear().domain([0, maxV]).nice().range([H - m.b, m.t]);

  /* сетка Y + подписи вторичным цветом */
  y.ticks(4).forEach(t => {
    g.append("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y(t)).attr("y2", y(t))
      .attr("stroke", "#272b35").attr("stroke-width", 1);
    g.append("text").attr("x", m.l - 8).attr("y", y(t)).attr("dy", "0.32em")
      .attr("text-anchor", "end").attr("class", "tp-ax").text(fmtAxis(t));
  });
  /* ось X — месяцы по-русски (тик на первой неделе месяца) */
  weeks.forEach((w, i) => {
    const d = weekDay(w);
    if (i > 0 && weekDay(weeks[i - 1]).getMonth() === d.getMonth()) return;
    if (i > 0 && x(i) - (W - m.r) > -30 && weeks.length - 1 - i < 1) return;
    const label = MONS[d.getMonth()] + (d.getMonth() === 0 ? " " + String(d.getFullYear()).slice(2) : "");
    g.append("text").attr("x", Math.min(x(i) + 14, W - m.r)).attr("y", H - 8)
      .attr("text-anchor", i === 0 ? "start" : "middle").attr("class", "tp-ax").text(label);
  });
  /* базовая линия */
  g.append("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y(0)).attr("y2", y(0))
    .attr("stroke", "#3a4050").attr("stroke-width", 1);

  const area = d3.area()
    .x((d, i) => x(i))
    .y0(d => y(d[0]))
    .y1(d => y(d[1]))
    .curve(d3.curveMonotoneX);

  const byKey = new Map(keys.map(s => [s.key, s]));
  layers.forEach(ly => {
    const s = byKey.get(ly.key);
    g.append("path")
      .datum(ly)
      .attr("d", area)
      .attr("fill", s.color)
      .attr("fill-opacity", 0.88)
      .attr("stroke", "#0f1115")        // 1px зазор между слоями цветом фона
      .attr("stroke-width", 1)
      .attr("class", "tp-area")
      .style("cursor", "pointer");
  });

  /* вертикальная линия наведения */
  const vline = g.append("line")
    .attr("y1", m.t).attr("y2", H - m.b).attr("stroke", "#a3a8b3")
    .attr("stroke-width", 1).attr("stroke-dasharray", "3 3").style("display", "none");

  /* тултип-карточка (кликабельные строки); в DOM — ПОСЛЕ svg, чтобы ловить клики */
  const tip = document.createElement("div");
  tip.className = "tp-tip";
  tip.style.display = "none";

  const rowsFor = i => keys.map(s => ({ s, v: s.values[i] || 0 })).filter(r => r.v > 0)
    .sort((a, b) => b.v - a.v);
  const showTip = (i, mx) => {
    hoverI = i;
    vline.attr("x1", x(i)).attr("x2", x(i)).style("display", "");
    const rows = rowsFor(i);
    const sum = rows.reduce((a, r) => a + r.v, 0);
    tip.innerHTML = `<div class="tp-tip-w">неделя ${esc(weekLabel(weeks[i]))}</div>` +
      rows.map(({ s, v }) => s.key === OTHER_KEY
        ? `<div class="tp-tip-row muted"><i style="background:${s.color}"></i><span>${esc(s.name)}</span><b>${fmtN(v)}</b></div>`
        : `<div class="tp-tip-row" data-k="${escAttr(s.key)}" data-w="${escAttr(weeks[i])}"
             title="Показать разговоры темы за эту неделю"><i style="background:${s.color}"></i><span>${esc(s.name)}</span><b>${fmtN(v)}</b></div>`).join("") +
      `<div class="tp-tip-sum">Всего: ${fmtN(sum)}</div>`;
    tip.style.display = "block";
    const bw = box.clientWidth;
    const tw = tip.offsetWidth || 240;
    tip.style.left = Math.max(0, Math.min(mx + 16, bw - tw - 4)) + "px";
    tip.style.top = "10px";
  };
  const hideTip = () => { hoverI = -1; vline.style("display", "none"); tip.style.display = "none"; };

  const overlay = g.append("rect")
    .attr("x", m.l - 6).attr("y", m.t).attr("width", W - m.r - m.l + 12).attr("height", H - m.b - m.t)
    .attr("fill", "transparent").style("cursor", "crosshair");
  overlay
    .on("mousemove", ev => {
      const [mx] = d3.pointer(ev);
      const i = Math.max(0, Math.min(weeks.length - 1, Math.round(x.invert(mx))));
      showTip(i, x(i));
    })
    .on("mouseleave", ev => {          // уход в тултип (к строкам) — не прячем
      if (!tip.contains(ev.relatedTarget)) hideTip();
    })
    .on("click", ev => {
      const [mx, my] = d3.pointer(ev);
      const i = Math.max(0, Math.min(weeks.length - 1, Math.round(x.invert(mx))));
      const yv = y.invert(my);
      const row = rowsFor(i).find(({ s }) => {
        const l = layers.find(ly => ly.key === s.key);
        if (!l) return false;
        return yv >= l[i][0] && yv <= l[i][1];
      });
      if (row && row.s.key !== OTHER_KEY) showWeekTopic(row.s.key, weeks[i]);
    });
  tip.addEventListener("click", e => {
    const r = e.target.closest(".tp-tip-row");
    if (r && r.dataset.k) showWeekTopic(r.dataset.k, r.dataset.w);
  });
  tip.addEventListener("mouseleave", e => {       // наружу из тултипа — прячем
    if (!svg.node().contains(e.relatedTarget)) hideTip();
  });

  box.appendChild(svg.node());
  box.appendChild(tip);                            // поверх графика: строки кликабельны
}

/* ---------- таблица ---------- */
function renderTable({ weeks }, sers) {
  const keys = stackKeys(sers);
  const box = $("#tpTable");
  box.innerHTML = !weeks.length || !keys.length
    ? `<div class="empty"><div class="e-t">Нет данных за период</div></div>`
    : `<div class="tp-tablewrap"><table>
      <thead><tr><th class="st">Тема</th>${weeks.map(w =>
        `<th>${esc(weekLabel(w))}</th>`).join("")}<th class="st">Итого</th></tr></thead>
      <tbody>
        ${keys.map(s => `<tr>
          <td class="st"><i class="dot" style="background:${s.color}"></i>${esc(s.name)}</td>
          ${s.values.map(v => `<td>${v ? fmtN(v) : "·"}</td>`).join("")}
          <td class="st">${fmtN(s.total)}</td></tr>`).join("")}
        <tr class="tot"><td class="st">Всего</td>
          ${weeks.map((w, i) => {
      const sum = keys.reduce((a, s) => a + (s.values[i] || 0), 0);
      return `<td>${sum ? fmtN(sum) : "·"}</td>`;
    }).join("")}
          <td class="st">${fmtN(keys.reduce((a, s) => a + s.total, 0))}</td></tr>
      </tbody></table></div>`;
}

/* ---------- главные проекты месяца (топ-теги последнего месяца) ---------- */
function renderMonth() {
  const tt = T.top_tags || {};
  const months = Object.keys(tt).sort();
  const box = $("#tpMonth");
  if (!months.length) { box.innerHTML = ""; return; }
  const mon = months[months.length - 1];
  const [yy, mm] = mon.split("-").map(Number);
  const items = tt[mon] || [];
  box.innerHTML = `
    <div class="tp-month-h">Главные проекты месяца
      <span class="dsub">${esc(MON_N[mm - 1])} ${yy} · теги полезных разговоров</span></div>
    ${items.length ? `<div class="tp-tags">${items.map(t =>
      `<button class="chip tp-tag" data-tag="${escAttr(t.tag)}" title="Спросить ИИ: что обсуждали про ${escAttr(t.tag)} в ${escAttr(MON_P[mm - 1])} ${yy}">
        ${esc(t.tag)} <b>${t.n}</b></button>`).join("")}</div>`
      : `<div class="dsub">В ${esc(MON_P[mm - 1])} тегов у полезных разговоров не было</div>`}`;
  box.querySelectorAll(".tp-tag").forEach(b => b.addEventListener("click", () => {
    const t = b.dataset.tag || "";
    if (t) nav("chat", { q: `Что обсуждали про ${t} в ${MON_P[mm - 1]} ${yy}?` }, true);
  }));
}

/* ---------- drawer: разговоры темы за неделю ---------- */
function showWeekTopic(catKey, week, fromStack = false) {
  const name = catName(catKey);
  const label = weekLabel(week);
  const title = `${name} · неделя ${label}`;
  const arg = { kind: "weektopic", arg: `${catKey}|${week}`, restore: () => showWeekTopic(catKey, week, true) };
  const cur = !fromStack && drawerCur();
  if (cur && cur.kind === "weektopic" && cur.arg === arg.arg) return;
  drawerSetContent(arg);
  drawerOpenUI(title, null);
  $("#dbody").innerHTML = `<div class="loading">Ищу разговоры</div>`;
  const ids = ((T && T.convs && T.convs[catKey]) || {})[week] || [];
  ensureGraph().then(g => {
    if (drawerCur() !== arg) return;
    const byId = new Map((g.nodes || []).map(n => [n.id, n]));
    const nodes = ids.map(id => byId.get(id)).filter(n => n && n.v !== "drop")
      .sort((a, b) => b.n - a.n || (b.t || "").localeCompare(a.t || ""));
    drawerSetTitle(title);
    $("#dbody").innerHTML = `
      <div class="dsub" style="margin-bottom:12px">Тема «${esc(name)}», неделя ${esc(label)} ·
        ${nodes.length} ${plural(nodes.length, "разговор", "разговора", "разговоров")} ·
        ${fmtN(nodes.reduce((a, n) => a + (n.n || 0), 0))} ${plural(nodes.reduce((a, n) => a + (n.n || 0), 0), "сообщение", "сообщения", "сообщений")}</div>
      ${nodes.length
        ? nodes.map(n => convCard(n)).join("")
        : `<div class="empty"><div class="e-ic">${icon("chat", 26)}</div><div class="e-t">Полезных разговоров не нашлось</div>
            <div class="e-h">В этой неделе по теме только мусорные разговоры</div></div>`}`;
    $("#dbody").scrollTop = 0;
  }, () => {
    if (drawerCur() !== arg) return;
    $("#dbody").innerHTML = `<div class="empty"><div class="e-t">Не удалось загрузить разговоры</div></div>`;
  });
}
