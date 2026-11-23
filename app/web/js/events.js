/* events.js — доска активностей: карточки событий по группам «Идёт сейчас / Скоро / Позже / Прошло / Без даты» */
import { icon } from "./icons.js";
import { $, esc, escAttr, EVTYPE, fmtDate, fmtDateShort, MONS, ensureGraph, nodeTitle, act } from "./ui.js";
import * as api from "./api.js";

const EVGROUPS = [
  ["active", "Идёт сейчас"], ["soon", "Скоро"], ["future", "Позже"],
  ["past", "Прошло"], ["undated", "Без даты"],
];

let mounted = false, evAll = [];

export function mountEvents() {
  if (mounted) return Promise.resolve();
  mounted = true;
  const root = document.querySelector('.view[data-view="events"]');
  root.innerHTML = `
    <div class="view-head">
      <div class="view-title">
        <h1>Активности</h1>
        <p class="sub">События с датами из чата: минты, клеймы, вайтлисты, дедлайны</p>
      </div>
    </div>
    <div class="ev-filters">
      <select id="evtype" title="Тип события"></select>
      <select id="evchain" title="Сеть"></select>
      <div class="field ev-q"><span class="f-ic">${icon("search", 17)}</span><input type="text" id="evq" placeholder="Поиск по проекту…" title="Поиск по названию проекта и описанию события"></div>
    </div>
    <div class="view-scroll">
      <div class="dsub" id="evinfo" style="margin-bottom:12px"><span class="loading">Загружаю</span></div>
      <div id="evlist">${'<div class="evcard skel"><div class="evdate"></div><div class="evbody"></div></div>'.repeat(4)}</div>
    </div>`;
  return ensureGraph().then(async () => {   // заголовки разговоров для «источник: …»
    let r;
    try { r = await api.getEvents(); }
    catch (err) {
      $("#evinfo").textContent = "";
      $("#evlist").innerHTML = `<div class="empty"><div class="e-ic">${icon("calendar", 26)}</div><div class="e-t">Не удалось загрузить активности</div><div class="e-h">${esc(err.message)}</div></div>`;
      return;
    }
    evAll = r.events || [];
    $("#evinfo").textContent = `${evAll.length} событий · данные чата по ${fmtDate(r.last_msg || "")}`;
    $("#evtype").innerHTML = `<option value="">все типы</option>` + Object.entries(EVTYPE)
      .map(([k, v]) => `<option value="${k}">${v[0]}</option>`).join("");
    const chains = [...new Map(evAll.filter(e => e.chain).map(e => [e.chain.toLowerCase(), e.chain])).values()]
      .sort((a, b) => a.localeCompare(b));
    $("#evchain").innerHTML = `<option value="">все сети</option>` + chains
      .map(c => `<option value="${escAttr(c)}">${esc(c)}</option>`).join("");
    const render = () => {
      const t = $("#evtype").value, ch = $("#evchain").value.toLowerCase(), qq = $("#evq").value.trim().toLowerCase();
      const flt = evAll.filter(e => (!t || e.type === t)
        && (!ch || (e.chain || "").toLowerCase() === ch)
        && (!qq || `${e.project} ${e.title} ${e.details || ""}`.toLowerCase().includes(qq)));
      let h = "";
      for (const [st, label] of EVGROUPS) {
        const items = flt.filter(e => e.status === st);
        if (!items.length) continue;
        const rows = items.map(evCard).join("");
        h += (st === "past" || st === "undated")   // прошлое и без даты — свёрнуты
          ? `<details class="evgrp"><summary>${label} · ${items.length}</summary><div class="ev-list">${rows}</div></details>`
          : `<div class="ev-group-h">${label} · ${items.length}</div><div class="ev-list">${rows}</div>`;
      }
      $("#evlist").innerHTML = h || `<div class="empty"><div class="e-ic">${icon("calendar", 26)}</div><div class="e-t">Ничего не найдено</div><div class="e-h">Попробуй изменить фильтры</div></div>`;
    };
    $("#evtype").addEventListener("change", render);
    $("#evchain").addEventListener("change", render);
    $("#evq").addEventListener("input", render);
    render();
  });
}

function evCard(e) {
  const t = EVTYPE[e.type] || EVTYPE.other;
  const d = e.date || e.end_date;
  const dt = d ? new Date(d.slice(0, 10) + "T12:00:00") : null;
  let d1 = "·", d2 = "без даты", d3 = "";
  if (dt) {
    d1 = String(dt.getDate());
    d2 = MONS[dt.getMonth()] + (dt.getFullYear() !== 2026 ? " " + dt.getFullYear() : "");
    if (e.date && e.end_date && e.end_date !== e.date) d3 = "→ " + fmtDateShort(e.end_date);
    else if (!e.date && e.end_date) d3 = "до " + fmtDateShort(e.end_date);
  }
  const src = e.sources || [];
  const meta = [
    e.time ? e.time + " UTC" : "",
    e.chain ? "сеть " + e.chain : "",
  ].filter(Boolean).join(" · ");
  return `<div class="evcard">
    <div class="evdate"><div class="d1">${esc(d1)}</div><div class="d2">${esc(d2)}</div>${d3 ? `<div class="d3">${esc(d3)}</div>` : ""}</div>
    <div class="evbody">
      <div class="evhead"><span class="evbadge" style="background:${t[1]}">${esc(t[0])}</span><span class="evproj">${esc(e.project)}</span></div>
      <div class="evtitle">${esc(e.title)}</div>
      ${e.details ? `<div class="dsub" style="margin-top:4px">${esc(e.details)}</div>` : ""}
      ${meta ? `<div class="dsub" style="margin-top:6px">${esc(meta)}</div>` : ""}
      <div class="evsrc">${src.length ? "источник: " + src.map(s =>
        `<a data-act="conv" data-arg="${escAttr(s.id)}">${esc(nodeTitle(s.id))}</a>`).join(" · ") : ""}</div>
    </div>
  </div>`;
}
