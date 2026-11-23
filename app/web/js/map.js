/* map.js — карта базы: «По темам» (упаковка кругов d3.pack, SVG) и «По веткам» (force-graph, canvas) */
import { icon } from "./icons.js";
import { $, esc, CAT, convTitle, fmtDate, tipShow, tipHide, ensureGraph, navParam, parseHash } from "./ui.js";

const d3 = window.d3, ForceGraph = window.ForceGraph;
const catKey = n => (CAT[n.c] ? n.c : "other");
const catColor = n => CAT[catKey(n)][1];
const val = n => Math.min(30, 1 + Math.sqrt(n.n) * (n.v === "keep" ? 2 : 1));
const rad = n => Math.sqrt(val(n)) * 2;

let mounted = false;
let G = null, data = null;
let mode = "topics";                         // topics | branches
let shown = new Set(Object.keys(CAT));       // включённые категории
let cutoff = null;                           // «показывать разговоры до этой даты»
let titleByShort = {};
let branches = [], centers = {}, cr = {};    // раскладка по веткам
let semOk = false;                           // есть смысловые кластеры
let lastNodeClick = 0;
let PK = null;                               // вид «по темам»: SVG-упаковка кругов

export function mountMap() {
  if (mounted) return Promise.resolve();
  mounted = true;
  return ensureGraph().then(d => {
    data = d;
    const ml = document.getElementById("mapLoad");   // «Загружаю карту» — убрать
    if (ml) ml.remove();
    titleByShort = Object.fromEntries(data.topics.map(t => [t.short, t.title]));
    semOk = !!(data.clusters || []).length && data.nodes.some(n => n.k != null && n.v !== "drop");
    computeBranchLayout();
    buildGraph();
    buildLegend();
    buildTimeline();
    mode = semOk ? (localStorage.getItem("mxw_mapmode") || "topics") : "branches";
    applyMode();
    window.addEventListener("resize", onResize);
    if (mode === "branches") setTimeout(() => fitView(600), 350);   // повторно после устаканивания раскладки
  }, err => {                                        // карта не загрузилась — не молчим
    const ml = document.getElementById("mapLoad");
    if (ml) ml.innerHTML = `<div class="empty"><div class="e-ic">${icon("map", 26)}</div><div class="e-t">Не удалось загрузить карту</div><div class="e-h">${esc(err.message || "")}</div></div>`;
  });
}

function onResize() {
  sizeGraph();
  if (mode === "topics" && PK) resizePack();
  else if (mode === "branches") fitView(300);
}

/* canvas force-graph по размеру контейнера (сам он умеет только window.innerWidth) */
function sizeGraph() {
  if (!G) return;
  const box = $("#graph");
  G.width(box.clientWidth || window.innerWidth).height(box.clientHeight || window.innerHeight);
}

/* ---------- видимость (вид «по веткам») ---------- */
function visible(n) {
  if (n.v === "drop") return false;                       // мусор не показываем вообще
  if (mode !== "branches" || n.bx == null) return false;
  return shown.has(catKey(n)) && (!cutoff || n.t <= cutoff);
}
function refresh() {          // применить легенду/таймлайн к текущему виду
  if (mode === "topics") { if (PK) updateLeaves(); return; }
  if (G) G.nodeVisibility(visible);
}

/* ---------- раскладка «по веткам» (как в старом фронте) ---------- */
function computeBranchLayout() {
  const count = {};
  data.nodes.forEach(n => { count[n.b] = (count[n.b] || 0) + 1; });
  branches = data.topics.map(t => t.short).sort((a, b) => count[b] - count[a]);
  const area = b => data.nodes.filter(n => n.b === b).reduce((s, n) => s + (rad(n) + 1) ** 2 * 4, 0);
  for (const b of branches) cr[b] = Math.sqrt(area(b) / Math.PI) * 1.15;
  const ringLen = branches.reduce((s, b) => s + 2 * cr[b] + 60, 0), R = ringLen / (2 * Math.PI);
  let acc = 0;
  for (const b of branches) {
    const a = (acc + cr[b] + 30) / ringLen * 2 * Math.PI;
    acc += 2 * cr[b] + 60;
    centers[b] = [Math.cos(a) * R, Math.sin(a) * R];
  }
  data.nodes.forEach((n, i) => {
    const a = i * 2.4, r = Math.sqrt(Math.random()) * cr[n.b];
    n.x = centers[n.b][0] + Math.cos(a) * r;
    n.y = centers[n.b][1] + Math.sin(a) * r;
  });
  const sim = d3.forceSimulation(data.nodes)
    .force("x", d3.forceX(n => centers[n.b][0]).strength(0.12))
    .force("y", d3.forceY(n => centers[n.b][1]).strength(0.12))
    .force("collide", d3.forceCollide(n => rad(n) + 0.7).iterations(2)).stop();
  for (let i = 0; i < 260; i++) sim.tick();
  data.nodes.forEach(n => { n.bx = n.x; n.by = n.y; });
}

/* ---------- вид «по веткам»: сам граф (canvas force-graph) ---------- */
function buildGraph() {
  G = ForceGraph()($("#graph"))
    .graphData({ nodes: data.nodes, links: [] })
    .backgroundColor("#0f1115")
    .nodeRelSize(2)
    .nodeVal(val)
    .nodeCanvasObject((n, ctx) => {
      ctx.beginPath();
      ctx.arc(n.x, n.y, rad(n), 0, 2 * Math.PI);
      ctx.fillStyle = catColor(n);
      ctx.fill();
    })
    .nodeVisibility(visible)
    .cooldownTicks(0)
    .enableNodeDrag(false)
    .autoPauseRedraw(false)
    .onNodeClick(n => { lastNodeClick = performance.now(); openConvNav(n.id); })
    .onNodeHover(n => {
      if (!n) { tipHide(); return; }
      tipShow(`<b>${esc(convTitle(n.s))}</b><span class="m">${esc(fmtDate(n.t))} · ${n.n} сообщений</span>`,
        lastMouse.x, lastMouse.y);
    })
    .onRenderFramePost((ctx, scale) => {         // подписи веток над кругами
      if (mode !== "branches") return;
      window.__mxwLabels = { n: 0, total: branches.length, rects: [] };
      ctx.textAlign = "center";
      ctx.textBaseline = "alphabetic";
      ctx.font = `600 ${Math.max(12 / scale, 13 / scale)}px Inter, "Segoe UI", system-ui, sans-serif`;
      let drawn = 0;
      for (const b of branches) {
        if (!data.nodes.some(n => n.b === b && visible(n))) continue;
        ctx.fillStyle = "#e5397b";
        ctx.fillText((titleByShort[b] || b).toUpperCase(), centers[b][0], centers[b][1] - cr[b] - 14 / scale);
        drawn++;
      }
      window.__mxwLabels.n = drawn;
    });

  const cv = $("#graph canvas") || $("#graph").querySelector("canvas");
  if (cv) cv.addEventListener("mousemove", e => { lastMouse = { x: e.clientX, y: e.clientY }; });
}
let lastMouse = { x: 0, y: 0 };

function openConvNav(id) {
  navParam("conv", id, {}, true);   // push: назад браузера закроет панель разговора
}

/* ---------- переключатель видов ---------- */
export function setMapMode(m) {
  mode = m;
  localStorage.setItem("mxw_mapmode", m);
  applyMode();
}
function applyMode() {
  document.querySelectorAll("#mapSeg button").forEach(b => b.classList.toggle("on", b.dataset.v === mode));
  const topics = mode === "topics";
  $("#graph").style.display = topics ? "none" : "";
  if (topics) {
    if (G && G.pauseAnimation) G.pauseAnimation();      // canvas не нужен — не жжём CPU
    if (!PK) buildPack();
    else { PK.wrap.style.display = ""; resizePack(); }  // размеры могли измениться, пока вид был скрыт
  } else {
    if (PK) { PK.wrap.style.display = "none"; tipHide(); }
    if (G && G.resumeAnimation) G.resumeAnimation();
    for (const n of data.nodes) {
      if (n.bx != null) { n.x = n.fx = n.bx; n.y = n.fy = n.by; }
    }
    sizeGraph();
    refresh();
    fitView(600);
  }
}

/* вписать видимое в экран (вид «по веткам»): сверху шапка, снизу таймлайн, справа легенда */
function fitView(ms = 600) {
  if (!G || mode !== "branches") return;
  const nodes = data.nodes.filter(visible);
  if (!nodes.length) return;
  sizeGraph();
  const W = G.width(), H = G.height();
  const padT = 14, padB = 104, padL = 16;
  const padR = $("#legend") && !$("#legend").classList.contains("open") ? 150 : 246;
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const b of branches) {              // круги веток + подпись над кругом
    if (!data.nodes.some(n => n.b === b && visible(n))) continue;
    x0 = Math.min(x0, centers[b][0] - cr[b] - 12);
    y0 = Math.min(y0, centers[b][1] - cr[b] - 34);
    x1 = Math.max(x1, centers[b][0] + cr[b] + 12);
    y1 = Math.max(y1, centers[b][1] + cr[b] + 12);
  }
  if (x0 > x1) return;
  let k = Math.min((W - padL - padR) / (x1 - x0 || 1), (H - padT - padB) / (y1 - y0 || 1));
  k = Math.max(0.03, Math.min(12, k));
  const gx = (x0 + x1) / 2 + (padR - padL) / 2 / k;   // центр доступной области, не canvas
  const gy = (y0 + y1) / 2 + (padB - padT) / 2 / k;
  G.zoom(k, ms);
  G.centerAt(gx, gy, ms);
}

/* ====================== вид «по темам»: упаковка кругов (d3.pack, SVG) ====================== */

const FONT = `Inter, "Segoe UI", system-ui, sans-serif`;
const measCtx = document.createElement("canvas").getContext("2d");
const measCache = new Map();
function textW(t, fs, weight = 400) {       // ширина текста реальным шрифтом (кэш)
  const key = t + "|" + fs + "|" + weight;
  if (!measCache.has(key)) {
    measCtx.font = `${weight} ${fs}px ${FONT}`;
    measCache.set(key, measCtx.measureText(t).width);
  }
  return measCache.get(key);
}
const convPlural = n => {
  const a = Math.abs(n) % 100, b = a % 10;
  return a > 10 && a < 20 || b > 4 ? "разговоров" : b === 1 ? "разговор" : "разговора";
};

/* иерархия: корень → группы (категория, преобладающая в кластере) → кластеры → разговоры.
   Лист: значение = sqrt(n сообщений), чтобы большие разговоры не забивали всё. Мусор не показываем. */
function buildHierarchy() {
  const sem = data.nodes.filter(n => n.k != null && n.v !== "drop");
  if (!sem.length || !(data.clusters || []).length) return null;
  const byK = new Map();
  for (const n of sem) {
    if (!byK.has(n.k)) byK.set(n.k, []);
    byK.get(n.k).push(n);
  }
  const majority = k => {
    const c = {};
    for (const n of byK.get(k)) c[catKey(n)] = (c[catKey(n)] || 0) + 1;
    return Object.entries(c).sort((a, b) => b[1] - a[1])[0][0];
  };
  const clByK = new Map(data.clusters.map(c => [c.k, c]));
  const root = {
    children: Object.keys(CAT).map(cat => ({
      type: "group", cat,
      children: [...byK.keys()].filter(k => majority(k) === cat).map(k => ({
        type: "cluster", k,
        name: (clByK.get(k) || {}).name || "Тема",
        descr: (clByK.get(k) || {}).descr || "",
        n: byK.get(k).length,
        children: byK.get(k).map(n => ({ type: "leaf", node: n, value: Math.sqrt(n.n || 1) })),
      })),
    })).filter(g => g.children.length),
  };
  return d3.hierarchy(root).sum(d => d.value || 0);
}

/* доступная область: между верхней строкой и таймлайном, с учётом легенды справа */
function packArea() {
  const wrap = document.querySelector(".map-wrap");
  const W = wrap.clientWidth || 800, H = wrap.clientHeight || 600;
  const tl = document.getElementById("timeline");
  const legend = document.getElementById("legend");
  const offX = 14, offY = 10;
  const rightRes = legend && !legend.classList.contains("open") ? 140 : 254;
  const h = Math.max(60, (tl ? tl.offsetTop : H - 90) - offY - 6);
  const w = Math.max(60, W - offX - rightRes);
  return { W, H, offX, offY, w, h, min: Math.min(w, h) };
}

function buildPack() {
  const root = buildHierarchy();
  if (!root) { semOk = false; return; }
  const mapWrap = document.querySelector(".map-wrap");
  const wrap = document.createElement("div");
  wrap.id = "pack";
  mapWrap.insertBefore(wrap, document.getElementById("legend"));   // под легендой и таймлайном
  const svg = d3.select(wrap).append("svg")
    .on("click", () => zoomToPack(PK.root, 750));                  // клик по фону — общий вид
  const zoomG = svg.append("g");
  const lay = {
    grp: zoomG.append("g"),   // круги групп
    cl: zoomG.append("g"),    // кластеры (круг + листья внутри)
    lb: zoomG.append("g"),    // подписи кластеров — поверх листьев
    pill: zoomG.append("g"),  // «таблетки» групп (категорий)
  };
  const back = document.createElement("button");
  back.className = "pk-back";
  back.innerHTML = icon("arrow-left", 15) + "Все темы";
  back.title = "Вернуться к общему виду карты";
  back.addEventListener("click", () => zoomToPack(PK.root, 750));
  wrap.appendChild(back);

  PK = { wrap, svg, zoomG, lay, root, focus: root, view: null };
  PK.clusters = root.children.flatMap(g => g.children);
  layoutPack();
  renderPack();
  PK.view = [root.x, root.y, root.r * 2];
  applyView(PK.view, true);
  /* deep-link #/map?cluster=<k> — сразу приблизиться к кластеру */
  const cl0 = parseHash().params.get("cluster");
  if (cl0 != null) {
    const node = PK.clusters.find(c => String(c.data.k) === String(cl0));
    if (node) zoomToPack(node, 0);
  }
}

/* d3.pack: круги вплотную, отступы: группы 6, кластеры 3, листья 1 */
function layoutPack() {
  const a = packArea();
  d3.pack().size([a.w, a.h]).padding(d => (d.depth === 0 ? 6 : d.depth === 1 ? 3 : 1))(PK.root);
  Object.assign(PK, a);
}

function renderPack() {
  const root = PK.root, lay = PK.lay;

  /* группы — очень лёгкая заливка цвета категории + тонкая обводка того же цвета */
  PK.grpSel = lay.grp.selectAll("circle").data(root.children, d => d.data.cat)
    .join("circle")
    .attr("class", "grp-c")
    .attr("cx", d => d.x).attr("cy", d => d.y).attr("r", d => d.r)
    .attr("fill", d => CAT[d.data.cat][1]).attr("fill-opacity", 0.06)
    .attr("stroke", d => CAT[d.data.cat][1]).attr("stroke-opacity", 0.35)
    .attr("vector-effect", "non-scaling-stroke")
    .on("click", (e, d) => { e.stopPropagation(); zoomToPack(d, 750); });

  /* кластеры: круг-поверхность #171a21 + точки-разговоры внутри */
  PK.clGSel = lay.cl.selectAll("g").data(PK.clusters, d => d.data.k)
    .join("g")
    .attr("class", "cl-g");
  PK.clGSel.each(function (c) {
    const g = d3.select(this);
    g.selectAll("circle.cl-c").data([c])
      .join("circle")
      .attr("class", "cl-c")
      .attr("cx", c.x).attr("cy", c.y).attr("r", c.r)
      .attr("fill", "#171a21").attr("stroke", "#272b35")
      .attr("vector-effect", "non-scaling-stroke")
      .on("click", (e, d) => { e.stopPropagation(); zoomToPack(d, 750); })
      .on("pointerenter", (e, d) => {
        d3.select(e.target).classed("hov", true);
        tipShow(`<b>${esc(d.data.name)}</b><span class="m">${esc(d.data.descr || "")}</span>`, e.clientX, e.clientY);
      })
      .on("pointerleave", e => {
        d3.select(e.target).classed("hov", false);
        tipHide();
      });
    g.selectAll("circle.lf").data(c.children, d => d.data.node.id)
      .join("circle")
      .attr("class", "lf")
      .attr("cx", d => d.x).attr("cy", d => d.y).attr("r", d => d.r)
      .attr("fill", d => catColor(d.data.node))
      .on("click", (e, d) => {
        e.stopPropagation();
        if (PK.focus === d.parent) openConvNav(d.data.node.id);   // лист кликабелен, когда кластер приближен
      })
      .on("pointerenter", (e, d) => {
        if (PK.focus !== d.parent) return;
        const n = d.data.node;
        tipShow(`<b>${esc(convTitle(n.s))}</b><span class="m">${esc(fmtDate(n.t))} · ${n.n} сообщений</span>`,
          e.clientX, e.clientY);
      })
      .on("pointerleave", () => tipHide());
  });

  /* подписи кластеров — пустые <g>, содержимое подбирает updateLabels под радиус на экране */
  PK.lbSel = lay.lb.selectAll("g").data(PK.clusters, d => d.data.k)
    .join("g")
    .attr("class", "cl-lb")
    .attr("data-k", d => d.data.k)
    .attr("pointer-events", "none");

  /* «таблетки» групп (категорий) у верхнего края круга группы */
  PK.pillSel = lay.pill.selectAll("g").data(root.children, d => d.data.cat)
    .join("g")
    .attr("class", "grp-pill")
    .attr("pointer-events", "none");
  PK.pillSel.each(function (d) {
    const g = d3.select(this);
    const label = CAT[d.data.cat][0], color = CAT[d.data.cat][1];
    const W = textW(label, 11.5, 600) + 26, H = 20, cx = d.x, cy = d.y - d.r;
    g.html("");
    g.append("rect")
      .attr("x", cx - W / 2).attr("y", cy - H / 2).attr("width", W).attr("height", H)
      .attr("rx", H / 2).attr("fill", "#0f1115d9").attr("stroke", "#2b313d").attr("stroke-width", 1);
    g.append("circle").attr("cx", cx - W / 2 + 11).attr("cy", cy).attr("r", 3.5).attr("fill", color);
    g.append("text")
      .attr("x", cx + 4).attr("y", cy).attr("text-anchor", "middle").attr("dominant-baseline", "central")
      .attr("fill", "#e8e9ed").attr("font-weight", "600").attr("font-size", 11.5)
      .text(label);
  });

  updateLeaves();
}

/* подпись кластера: шрифт 11–16px под радиус круга на экране, перенос на 2 строки по словам.
   «Влезает» = каждая строка не шире хорды круга на своей высоте (запас 4%): у двух строк
   возле центра это ~1.9·r — по смыслу то же, что «~1.6·r» из плана, но по точной геометрии,
   чтобы подпись гарантированно не выходила за круг и не задевала соседей.
   «N разговоров» рисуется, когда влезает; если не влезает даже название в 2 строки по 11px —
   подпись скрыта (полная — в tooltip, появится при приближении), но остаётся в DOM. */
function bestSplit(name, fs) {         // разбиение на 2 строки с минимальной шириной строки
  const ws = name.split(/\s+/);
  let best = null;
  for (let i = 1; i < ws.length; i++) {
    const l1 = ws.slice(0, i).join(" "), l2 = ws.slice(i).join(" ");
    const w = Math.max(textW(l1, fs, 600), textW(l2, fs, 600));
    if (!best || w < best.w) best = { lines: [l1, l2], w };
  }
  return best;
}

function fitLabel(c, rS) {
  const key = Math.round(rS);
  if (c._fk === key) return c._fit;
  c._fk = key;
  const name = c.data.name || "";
  const R = rS * 0.96;
  const chord = y => 2 * Math.sqrt(Math.max(0, R * R - y * y));
  const cntTxt = `${c.data.n} ${convPlural(c.data.n)}`;
  let fit = null;
  for (let fs = 16; fs >= 11 && !fit; fs--) {
    const cntFs = fs >= 14 ? 12 : 11;
    const cntW = textW(cntTxt, cntFs, 400);
    const cands = [[name]];                      // одна строка
    const sp = bestSplit(name, fs);
    if (sp) cands.push(sp.lines);                // две строки, сбалансированно
    for (const withCnt of [true, false]) {       // сначала пробуем со строкой «N разговоров»
      if (fit) break;
      const cntH = withCnt ? cntFs + 3 : 0;
      for (const lines of cands) {
        const nL = lines.length;
        const H = nL * fs * 1.18 + cntH;
        const lineY = i => -H / 2 + (i + 0.5) * fs * 1.18;
        const yc = -H / 2 + nL * fs * 1.18 + 1.5 + cntFs / 2;
        if (withCnt && cntW > chord(Math.abs(yc))) continue;
        if (lines.some((l, i) => textW(l, fs, 600) > chord(Math.abs(lineY(i))))) continue;
        fit = { fs, cntFs, lines, cntTxt: withCnt ? cntTxt : null };
        break;
      }
    }
  }
  if (!fit) {                                    // мелкий круг: текст в DOM, но скрыт
    const sp = bestSplit(name, 11);
    fit = { fs: 11, cntFs: 11, lines: sp ? sp.lines : [name], cntTxt: null, hidden: true };
  }
  c._fit = fit;
  return fit;
}

/* подписи видны у кластеров «внутри» приближения (или у самого приближенного);
   локальный размер шрифта = экранный / k, чтобы на экране было 11–16px */
function updateLabels(k) {
  const focus = PK.focus;
  let vis = 0;
  PK.lbSel.each(function (c) {
    const g = d3.select(this);
    const inFocus = focus === PK.root || c === focus || c.ancestors().includes(focus);
    if (!inFocus) { g.classed("off", true); return; }   // вне приближения — скрыть, не трогая содержимое
    const fit = fitLabel(c, c.r * k);                   // всегда объект: hidden = «не влез даже в 2 строки»
    const show = !fit.hidden;
    if (show) vis++;
    g.classed("off", !show);
    const fs = fit.hidden ? 11 : fit.fs;                 // скрытая подпись хранит текст в DOM мелко
    const cntFs = fit.cntFs || 11;
    const cntTxt = fit.cntTxt || null;
    const same = c._cur && c._cur.k === k && c._cur.fs === fs && c._cur.cntFs === cntFs
      && c._cur.cntTxt === cntTxt && c._cur.lines.join("\n") === fit.lines.join("\n");
    if (same) return;
    c._cur = { k, fs, cntFs, cntTxt, lines: fit.lines };
    g.html("");
    const fsL = fs / k, cntL = cntFs / k;                // локальные размеры
    const nL = fit.lines.length;
    const H = (nL * fs * 1.22 + (cntTxt ? cntFs + 6 : 0)) / k;
    const nm = g.append("text")
      .attr("class", "cl-nm")
      .attr("text-anchor", "middle").attr("fill", "#e8e9ed").attr("font-weight", "600")
      .style("font-size", fsL + "px");
    fit.lines.forEach((line, i) => {
      nm.append("tspan").attr("x", c.x).attr("y", c.y - H / 2 + fsL * (i + 0.82)).text(line);
    });
    if (cntTxt) g.append("text")
      .attr("class", "cl-cnt")
      .attr("text-anchor", "middle").attr("fill", "#a3a8b3")
      .attr("x", c.x).attr("y", c.y - H / 2 + nL * fsL * 1.22 + cntL * 0.8)
      .style("font-size", cntL + "px")
      .text(cntTxt);
  });
  PK.wrap.dataset.labels = `${vis}/${PK.clusters.length}`;   // метрика для headless-проверки
  PK.wrap.dataset.area = `${Math.round(PK.W)}x${Math.round(PK.H)};${Math.round(PK.w)}x${Math.round(PK.h)}`;
}

/* листья: яркие/кликабельные — только в приближенном кластере (перерисовка на каждый кадр зума) */
function updateLeafStates() {
  if (!PK) return;
  PK.clGSel.classed("zoomed", d => d === PK.focus && PK.focus.depth >= 2);
}

/* скрыть листья позже cutoff или выключенной категорией — размеры кругов не пересчитываются */
function updateLeaves() {
  if (!PK) return;
  updateLeafStates();
  PK.lfSel = PK.lfSel || PK.lay.cl.selectAll("circle.lf");
  PK.lfSel.style("display", d => {
    const n = d.data.node;
    return shown.has(catKey(n)) && (!cutoff || n.t <= cutoff) ? null : "none";
  });
}

function updateStates(k) {
  updateLabels(k);
  updateLeafStates();
  PK.pillSel.classed("off", PK.focus !== PK.root);
  PK.wrap.classList.toggle("zoomed", PK.focus !== PK.root);
}

/* применить вид [x, y, диаметр]: центрировать в доступной области, k = min/w */
function applyView(v, withStates) {
  const k = PK.min / v[2];
  PK.zoomG.attr("transform",
    `translate(${PK.offX + PK.w / 2 - k * v[0]},${PK.offY + PK.h / 2 - k * v[1]}) scale(${k})`);
  if (withStates) updateStates(k);
}

/* zoomable circle packing: плавное (750 мс) приближение к узлу, назад — по фону/кнопке */
function zoomToPack(node, ms = 750) {
  if (!PK || !node || node === PK.focus) return;
  PK.focus = node;
  const v1 = [node.x, node.y, node.r * 2 * (node === PK.root ? 1 : 1.06)];
  syncClusterParam(node);
  if (!ms) {                                  // deep-link: сразу финальное состояние
    PK.view = v1.slice();
    applyView(v1, true);
    return;
  }
  const v0 = PK.view.slice();
  PK.zoomG.transition("pk").duration(ms)
    .tween("zoom", () => {
      const i = d3.interpolateZoom(v0, v1);
      return t => { const v = i(t); PK.view = v; applyView(v, true); };
    })
    .on("end", () => { PK.view = v1.slice(); });
}

/* ?cluster=<k> в URL вслед за приближением — без записи в историю */
function syncClusterParam(node) {
  const { route, params } = parseHash();
  const k = node.data.type === "cluster" ? String(node.data.k) : null;
  if (k) params.set("cluster", k); else params.delete("cluster");
  const qs = params.toString();
  history.replaceState(null, "", "#/" + route + (qs ? "?" + qs : ""));
}

/* пересчёт при изменении размеров/легенды: круги вплотную вписываются в новую область */
function resizePack() {
  if (!PK) return;
  PK.zoomG.interrupt("pk");
  layoutPack();
  renderPack();
  const f = PK.focus;
  const v = [f.x, f.y, f.r * 2 * (f === PK.root ? 1 : 1.06)];
  PK.view = v.slice();
  applyView(v, true);
}

/* ---------- легенда (сворачиваемая; клик по категории — скрыть/показать её листья) ---------- */
function buildLegend() {
  const counts = {};
  data.nodes.forEach(n => { if (n.v !== "drop") counts[catKey(n)] = (counts[catKey(n)] || 0) + 1; });
  const el = $("#legend");
  el.classList.toggle("open", localStorage.getItem("mxw_lg_open") === "1");
  el.innerHTML = `<button class="lg-head" title="Показать или скрыть список категорий">
      Категории<i class="lg-arrow">${icon("chev-down", 15)}</i></button>
    <div class="lg-body">` +
    Object.entries(CAT).map(([k, [l, c]]) =>
      `<div class="lg${shown.has(k) ? "" : " off"}" data-k="${k}" title="Клик — скрыть/показать категорию"><i style="background:${c}"></i>${esc(l)}<span class="n">${counts[k] || 0}</span></div>`).join("") +
    `</div>`;
  el.addEventListener("click", e => {
    const h = e.target.closest(".lg-head");
    if (h) {                                // свернуть/развернуть + переписать вид
      const open = el.classList.toggle("open");
      localStorage.setItem("mxw_lg_open", open ? "1" : "0");
      if (mode === "topics" && PK) resizePack();
      else setTimeout(() => fitView(300), 60);
      return;
    }
    const d = e.target.closest(".lg");
    if (!d) return;
    const k = d.dataset.k;
    shown.has(k) ? shown.delete(k) : shown.add(k);
    d.classList.toggle("off", !shown.has(k));
    refresh();
  });
}

/* ---------- таймлайн: листья позже выбранной даты скрываются, размеры не пересчитываются ---------- */
function buildTimeline() {
  const c = $("#hist"), ctx = c.getContext("2d");
  const h = data.hist, max = Math.max(...h.map(x => x[1]));
  const draw = upto => {
    const w = c.width / h.length;
    ctx.clearRect(0, 0, c.width, c.height);
    h.forEach(([d, n], i) => {
      ctx.fillStyle = i <= upto ? "#e5397b" : "#3a2a35";
      const bh = Math.max(1, n / max * c.height);
      ctx.fillRect(i * w, c.height - bh, Math.max(1, w - 1), bh);
    });
  };
  let upto = h.length;
  const apply = () => {
    const v = +$("#slider").value, i = Math.round(v / 1000 * (h.length - 1));
    cutoff = v >= 1000 ? null : h[i][0] + "T23:59";
    $("#tlabel").textContent = v >= 1000 ? "сейчас" : fmtDate(h[i][0]);
    upto = i; draw(i); refresh();
  };
  const size = () => { c.width = c.clientWidth * devicePixelRatio; c.height = c.clientHeight * devicePixelRatio; draw(upto); };
  $("#slider").addEventListener("input", apply);
  size();
  new ResizeObserver(size).observe(c);
  let timer = null;
  $("#play").addEventListener("click", () => {
    if (timer) { clearInterval(timer); timer = null; $("#play").innerHTML = icon("play", 16); return; }
    if (+$("#slider").value >= 1000) $("#slider").value = 0;
    $("#play").innerHTML = icon("pause", 16);
    timer = setInterval(() => {
      const s = $("#slider");
      s.value = Math.min(1000, +s.value + 4);
      apply();
      if (+s.value >= 1000) { clearInterval(timer); timer = null; $("#play").innerHTML = icon("play", 16); }
    }, 60);
  });
}

export function mapInit() {
  $("#play").innerHTML = icon("play", 16);
  $("#mapSeg").addEventListener("click", e => {
    const b = e.target.closest("button");
    if (b && b.dataset.v !== mode) setMapMode(b.dataset.v);
  });
}


