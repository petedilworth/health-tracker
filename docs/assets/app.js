/* Sleep Analytics chart driver.
   All shaping happens at build time in Python; this file only formats numbers,
   draws Plotly charts, and wires the view toggles. */
(function () {
  "use strict";
  var P = window.PAGE || {};
  var C = {
    page: "#0d0d0d", surface: "#1a1a19", ink: "#ffffff", ink2: "#c3c2b7",
    muted: "#898781", grid: "#2c2c2a",
    blue: "#3987e5", aqua: "#199e70", orange: "#d95926", red: "#e66767"
  };

  // --- formatters -----------------------------------------------------------
  function fmtClock(v) {
    if (v == null) return "—";
    var h = ((v % 24) + 24) % 24;
    var hh = Math.floor(h), mm = Math.round((h - hh) * 60);
    if (mm === 60) { hh = (hh + 1) % 24; mm = 0; }
    var ap = hh < 12 ? "am" : "pm", h12 = hh % 12 || 12;
    return h12 + ":" + String(mm).padStart(2, "0") + ap;
  }
  var FMT = {
    clock: fmtClock,
    h1: function (v) { return v == null ? "—" : v.toFixed(1) + "h"; },
    pct0: function (v) { return v == null ? "—" : Math.round(v) + "%"; },
    f0: function (v) { return v == null ? "—" : String(Math.round(v)); },
    f1: function (v) { return v == null ? "—" : v.toFixed(1); },
    f2s: function (v) { return v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(2) + "°C"; },
    int: function (v) { return v == null ? "—" : Math.round(v).toLocaleString(); }
  };
  function fmt(kind, v) { return (FMT[kind] || FMT.f1)(v); }

  function el(id) { return document.getElementById(id); }
  function fetchJSON(path) {
    return fetch(path).then(function (r) {
      if (!r.ok) throw new Error("fetch failed: " + path);
      return r.json();
    });
  }

  var BASE_LAYOUT = {
    paper_bgcolor: C.surface, plot_bgcolor: C.surface,
    font: { color: C.ink2, family: "system-ui, sans-serif", size: 12 },
    margin: { l: 64, r: 16, t: 8, b: 40 },
    hovermode: "x unified",
    hoverlabel: { bgcolor: C.page, bordercolor: C.grid, font: { color: C.ink2 } },
    dragmode: "pan",
    xaxis: {
      gridcolor: C.grid, linecolor: C.grid, zeroline: false,
      rangeselector: {
        x: 1, xanchor: "right", y: 1.02, yanchor: "bottom",
        bgcolor: "#232322", activecolor: C.page, bordercolor: C.grid,
        borderwidth: 1, font: { color: C.ink2, size: 11 },
        buttons: [
          { count: 3, label: "3m", step: "month", stepmode: "backward" },
          { count: 1, label: "1y", step: "year", stepmode: "backward" },
          { step: "all", label: "All" }
        ]
      }
    },
    yaxis: { gridcolor: C.grid, linecolor: C.grid, zeroline: false },
    legend: { orientation: "h", y: 1.06, x: 0, font: { size: 11.5 } },
    showlegend: true
  };
  var CONFIG = { displayModeBar: false, responsive: true, scrollZoom: false };

  function clockAxis(values) {
    var lo = Infinity, hi = -Infinity;
    values.forEach(function (v) { if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } });
    if (!isFinite(lo)) return {};
    var ticks = [], start = Math.floor(lo);
    for (var t = start; t <= hi + 0.5; t += 1) ticks.push(t);
    if (ticks.length > 8) ticks = ticks.filter(function (_, i) { return i % 2 === 0; });
    return { tickvals: ticks, ticktext: ticks.map(fmtClock) };
  }

  function deepMerge(a, b) {
    var out = JSON.parse(JSON.stringify(a));
    (function m(t, s) {
      Object.keys(s).forEach(function (k) {
        if (s[k] && typeof s[k] === "object" && !Array.isArray(s[k]) && t[k]) m(t[k], s[k]);
        else t[k] = s[k];
      });
    })(out, b);
    return out;
  }

  // --- metric page ----------------------------------------------------------
  function col(rows, i) { return rows.map(function (r) { return r[i]; }); }

  function dailyTraces(payload) {
    var m = payload.meta, rows = payload.series;
    var dates = col(rows, 0), v = col(rows, 1), a7 = col(rows, 2), a30 = col(rows, 3);
    var text = v.map(function (x) { return fmt(m.format, x); });
    var t7 = a7.map(function (x) { return fmt(m.format, x); });
    var t30 = a30.map(function (x) { return fmt(m.format, x); });
    var soft = m.confidence === "low";
    var traces = [];

    if (m.has_band) {
      // Plotly fills bridge null gaps even with connectgaps:false, smearing
      // polygons across data holes — so emit one fill pair per contiguous run.
      var lo = col(rows, 4), hi = col(rows, 5);
      var runStart = null;
      function pushRun(s, e) {
        if (e - s < 3) return;
        traces.push({ x: dates.slice(s, e), y: hi.slice(s, e), mode: "lines",
                      line: { width: 0 }, hoverinfo: "skip", showlegend: false });
        traces.push({ x: dates.slice(s, e), y: lo.slice(s, e), mode: "lines",
                      line: { width: 0 }, fill: "tonexty",
                      fillcolor: "rgba(217,89,38,0.10)",
                      hoverinfo: "skip", showlegend: false });
      }
      for (var bi = 0; bi <= lo.length; bi++) {
        var ok = bi < lo.length && lo[bi] != null && hi[bi] != null;
        if (ok && runStart === null) runStart = bi;
        if (!ok && runStart !== null) { pushRun(runStart, bi); runStart = null; }
      }
    }
    if (m.style === "scatter") {
      traces.push({ x: dates, y: v, name: "Nightly", mode: "markers",
                    marker: { color: C.blue, size: 3.5, opacity: soft ? 0.28 : 0.45 },
                    customdata: text, hovertemplate: "Nightly %{customdata}<extra></extra>" });
      traces.push({ x: dates, y: a7, name: "7-day", mode: "lines",
                    line: { color: C.aqua, width: 1.8 },
                    customdata: t7, hovertemplate: "7-day %{customdata}<extra></extra>" });
      traces.push({ x: dates, y: a30, name: "30-day", mode: "lines",
                    line: { color: C.orange, width: 2.2 }, opacity: soft ? 0.75 : 1,
                    customdata: t30, hovertemplate: "30-day %{customdata}<extra></extra>" });
    } else {
      traces.push({ x: dates, y: v, name: m.label, mode: "lines",
                    line: { color: C.blue, width: 2 },
                    customdata: text, hovertemplate: "%{customdata}<extra></extra>" });
      traces.push({ x: dates, y: a30, name: "30-day", mode: "lines",
                    line: { color: C.orange, width: 1.6, dash: "dot" },
                    customdata: t30, hovertemplate: "30-day %{customdata}<extra></extra>" });
      if (rows.length && rows[0].length > 4 && m.extra_label) {
        var ex = col(rows, rows[0].length - 1);
        traces.push({ x: dates, y: ex, name: m.extra_label, mode: "lines",
                      line: { color: C.aqua, width: 1.6 },
                      customdata: ex.map(function (x) { return fmt(m.format, x); }),
                      hovertemplate: m.extra_label + " %{customdata}<extra></extra>" });
      }
    }
    return { traces: traces, yvals: v };
  }

  function aggTraces(payload, view) {
    var m = payload.meta, rows = payload.agg[view] || [];
    var dates = col(rows, 0), v = col(rows, 1), n = col(rows, 2);
    var custom = rows.map(function (r) {
      return fmt(m.format, r[1]) + " · " + r[2] + " nights";
    });
    return {
      traces: [{ x: dates, y: v, name: view, mode: "lines+markers",
                 line: { color: C.blue, width: 2 }, marker: { size: 5, color: C.blue },
                 customdata: custom, hovertemplate: "%{customdata}<extra></extra>" }],
      yvals: v
    };
  }

  function render(chartEl, payload, view) {
    var built = view === "daily" ? dailyTraces(payload) : aggTraces(payload, view);
    var layout = deepMerge(BASE_LAYOUT, {});
    if (payload.meta.format === "clock") layout.yaxis = deepMerge(layout.yaxis, clockAxis(built.yvals));
    if (window.innerWidth < 640) {
      delete layout.xaxis.rangeselector;   // too cramped next to the legend
      layout.margin.l = 48;
    }
    Plotly.react(chartEl, built.traces, layout, CONFIG);
  }

  function statsHTML(payload) {
    var m = payload.meta, s = payload.stats || {};
    function block(lbl, val, pct) {
      var pctLine = pct == null ? "" :
        '<div class="pct">better than <b>' + pct.toFixed(0) + "%</b> of nights</div>";
      return '<div class="stat"><div class="lbl">' + lbl + '</div>' +
             '<div class="val">' + fmt(m.format, val) + "</div>" + pctLine + "</div>";
    }
    return block("Latest · " + (s.day || ""), s.value, s.pct) +
           block("7-day avg", s.avg7, s.pct7) +
           block("30-day avg", s.avg30, s.pct30);
  }

  function tbHTML(payload, period) {
    var m = payload.meta, tb = (payload.top_bottom || {})[period] || {};
    function table(rows, cls) {
      if (!rows || !rows.length) return "<p class='sub'>No data.</p>";
      return "<table class='" + cls + "'>" + rows.map(function (r) {
        return "<tr><td>" + r[0] + "</td><td>" + fmt(m.format, r[1]) + "</td></tr>";
      }).join("") + "</table>";
    }
    return "<div><h3>Best 10</h3>" + table(tb.top, "best") + "</div>" +
           "<div><h3>Worst 10</h3>" + table(tb.bottom, "worst") + "</div>";
  }

  function wireSeg(seg, attr, onPick) {
    if (!seg) return;
    seg.addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (!b) return;
      seg.querySelectorAll("button").forEach(function (x) { x.classList.remove("on"); });
      b.classList.add("on");
      onPick(b.dataset[attr]);
    });
  }

  function initMetric() {
    fetchJSON(P.root + "/data/m/" + P.slug + ".json").then(function (payload) {
      el("stats").innerHTML = statsHTML(payload);
      var contrib = el("contrib");
      if (contrib && payload.meta.latest_contribution != null) {
        contrib.textContent = "latest contribution " +
          payload.meta.latest_contribution.toFixed(0) + "/100";
      }
      render(el("chart"), payload, "daily");
      el("tb").innerHTML = tbHTML(payload, "all");
      wireSeg(el("view-toggle"), "view", function (v) { render(el("chart"), payload, v); });
      wireSeg(el("tb-toggle"), "period", function (p) { el("tb").innerHTML = tbHTML(payload, p); });
    });
  }

  // --- overview -------------------------------------------------------------
  function sparkSVG(values) {
    var pts = [];
    values.forEach(function (v, i) { if (v != null) pts.push([i, v]); });
    if (pts.length < 2) return "<svg class='spark'></svg>";
    var lo = Infinity, hi = -Infinity;
    pts.forEach(function (p) { lo = Math.min(lo, p[1]); hi = Math.max(hi, p[1]); });
    var span = (hi - lo) || 1, W = 120, H = 32, step = W / (values.length - 1);
    var d = pts.map(function (p, i) {
      return (i ? "L" : "M") + (p[0] * step).toFixed(1) + "," +
             (H - 3 - (p[1] - lo) / span * (H - 6)).toFixed(1);
    }).join(" ");
    return "<svg class='spark' viewBox='0 0 " + W + " " + H +
           "' preserveAspectRatio='none'><path d='" + d + "'/></svg>";
  }

  function initOverview() {
    fetchJSON(P.root + "/data/overview.json").then(function (ov) {
      el("ov-sub").textContent = (ov.latest_day ? "Latest night " + ov.latest_day + " · " : "") +
        ov.nights + " nights · " + ov.range;
      var flag = el("flag");
      if (ov.flag && ov.flag.raised) {
        flag.innerHTML = "<div class='flagbox'><b>Something's off:</b> " +
          ov.flag.detail + "</div>";
      } else {
        flag.innerHTML = "<p class='allclear'>No health flags — temperature and " +
          "respiratory rate are within their seasonal baselines.</p>";
      }
      el("cards").innerHTML = ov.cards.map(function (c) {
        var pct = c.pct == null ? "" : "<span>better than <b>" +
          c.pct.toFixed(0) + "%</b></span>";
        return "<a class='cardlet' href='metrics/" + c.slug + ".html'>" +
          "<div class='lbl'>" + c.label + "</div>" +
          "<div class='big'>" + fmt(c.format, c.value) + "</div>" +
          "<div class='mini'><span>7d <b>" + fmt(c.format, c.avg7) + "</b></span>" +
          "<span>30d <b>" + fmt(c.format, c.avg30) + "</b></span>" + pct + "</div>" +
          sparkSVG(c.spark) + "</a>";
      }).join("");
      return fetchJSON(P.root + "/data/m/sleep-score.json");
    }).then(function (payload) {
      render(el("chart"), payload, "daily");
    });
  }

  function initList() {
    document.querySelectorAll(".mval").forEach(function (span) {
      var raw = span.dataset.val;
      span.textContent = raw === "" ? "—" : fmt(span.dataset.fmt, parseFloat(raw));
    });
  }

  if (P.type === "metric") initMetric();
  else if (P.type === "overview") initOverview();
  else if (P.type === "list") initList();
})();
