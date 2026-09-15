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
  // How far back each view opens, in days. null = all-time. Daily is dense
  // enough that four months fills the width; the aggregated views are
  // sparser but six years of weekly points is still a wall, so they open on
  // the last year and the buttons take you wider.
  var DEFAULT_WINDOW = { daily: 120, weekly: 365, quarterly: 365, annual: null };
  var RANGE_OPTIONS = {
    daily:     [["4m", 120], ["1y", 365], ["All", null]],
    weekly:    [["1y", 365], ["3y", 3 * 365], ["All", null]],
    quarterly: [["1y", 365], ["3y", 3 * 365], ["All", null]],
    annual:    []
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

  function ordinal(p) {
    if (p == null) return "—";
    var n = Math.round(p), r = n % 100;
    var suf = (r >= 11 && r <= 13) ? "th" : ({1: "st", 2: "nd", 3: "rd"})[n % 10] || "th";
    return n + suf + " percentile";
  }

  // "38 min later than your median" / "0.4h above your median"
  function vsMedian(kind, v, med) {
    if (v == null || med == null) return "";
    var d = v - med;
    if (Math.abs(d) < 1e-9) return "right on your median";
    if (kind === "clock") {
      var mins = Math.round(Math.abs(d) * 60), h = Math.floor(mins / 60), m = mins % 60;
      var span = (h ? h + "h " : "") + (m || !h ? m + " min" : "");
      return span.trim() + (d > 0 ? " later" : " earlier") + " than your median";
    }
    var mag;
    if (kind === "h1") mag = Math.abs(d).toFixed(1) + "h";
    else if (kind === "pct0") mag = Math.abs(d).toFixed(0) + " pts";
    else if (kind === "int") mag = Math.round(Math.abs(d)).toLocaleString();
    else if (kind === "f2s") mag = Math.abs(d).toFixed(2) + "°C";
    else mag = Math.abs(d).toFixed(kind === "f0" ? 0 : 1);
    return mag + (d > 0 ? " above" : " below") + " your median";
  }

  function el(id) { return document.getElementById(id); }

  // --- freshness ------------------------------------------------------------
  // Both checks run against the VIEWER'S clock, not the build's, so they still
  // fire if the site stops rebuilding altogether. A build-time-only check
  // cannot warn you about a build that never happened.
  var DAY_MS = 86400000;
  // Before this hour, "today" means yesterday: at 1am you may not have slept
  // yet, and the strip must not claim last night is missing.
  var NEW_DAY_HOUR = 6;
  // Four runs a day: the largest healthy gap is the 11h from 11:23pm to
  // 10:23am, plus up to ~10h of queue jitter. At 30h the banner still catches a
  // genuinely dead pipeline inside a day and a half without crying wolf.
  var STALE_BUILD_HOURS = 30;

  function niceDate(iso) {
    if (!iso) return "—";
    var d = new Date(iso.length > 10 ? iso : iso + "T00:00:00");
    if (isNaN(d)) return iso;
    return d.toLocaleDateString(undefined,
      { day: "numeric", month: "short", year: "numeric" });
  }

  function niceTime(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString(undefined,
      { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
  }

  // The job is scheduled in UTC because cron has no timezone. Render those hours
  // in the reader's own clock, so it stays right either side of a DST change
  // without the build knowing anything about timezones.
  function scheduleText(fresh) {
    var hours = fresh.schedule_hours_utc;
    if (!hours || !hours.length) return fresh.schedule || "";
    var now = new Date();
    var local = hours.map(function (h) {
      return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(),
                               now.getUTCDate(), h, 0, 0));
    }).sort(function (a, b) { return a.getHours() - b.getHours(); })
      .map(function (d) {
        return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
      });
    // Sorted by LOCAL hour above: 03:00 UTC is 11pm the previous Eastern day,
    // so UTC order would list it first instead of last.
    var list = local.length < 2 ? local[0]
      : local.slice(0, -1).join(", ") + " and " + local[local.length - 1];
    return "scheduled daily around " + list;
  }

  // Local midnight of a calendar date, so day arithmetic ignores DST and
  // the viewer's offset.
  function localMidnight(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }

  // The night the viewer expects to see: the one that ended this morning.
  function expectedNight(now) {
    var d = new Date(now);
    if (d.getHours() < NEW_DAY_HOUR) d.setDate(d.getDate() - 1);
    return localMidnight(d);
  }

  // First scheduled attempt after now, in the viewer's clock, or null.
  function nextAttempt(fresh, now) {
    var hours = fresh.schedule_hours_utc || [], best = null;
    hours.forEach(function (h) {
      for (var k = 0; k < 2; k++) {
        var d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(),
                                  now.getUTCDate() + k, h, 23, 0));
        if (d > now && (!best || d < best)) best = d;
      }
    });
    return best;
  }

  function setStrip(state, html) {
    var bar = el("stalebar");
    if (!bar) return;
    bar.className = "stalebar " + state;
    bar.innerHTML = html;
    bar.hidden = false;
  }

  function renderFreshness(fresh) {
    if (!fresh) return;
    var now = new Date();
    var line = el("freshline");
    if (line) {
      line.textContent = "Sleep data through " + niceDate(fresh.data_through) +
        " · checked " + niceTime(fresh.built) + " · " + scheduleText(fresh);
    }
    if (!el("stalebar")) return;

    var checked = fresh.built ? " · checked " + niceTime(fresh.built) : "";
    var buildStale = fresh.built &&
      (now - new Date(fresh.built)) / 3600000 > STALE_BUILD_HOURS;

    if (!fresh.data_through) {
      setStrip("bad", "<b>No sleep data at all</b>" + checked);
      return;
    }
    var have = localMidnight(new Date(fresh.data_through + "T00:00:00"));
    var behind = Math.round((expectedNight(now) - have) / DAY_MS);

    var next = nextAttempt(fresh, now);
    var nextText = next ? " · next attempt around " +
      next.toLocaleTimeString(undefined, { hour: "numeric" }) : "";

    if (behind <= 0 && !buildStale) {
      setStrip("ok", "<b>Up to date</b> · last night, " +
        niceDate(fresh.data_through) + checked);
    } else if (behind === 1 && !buildStale) {
      setStrip("warn", "<b>Last night isn't in yet</b> · showing through " +
        niceDate(fresh.data_through) + checked + nextText);
    } else {
      var head = behind >= 2
        ? "<b>" + behind + " nights missing</b> · no sleep data since " +
          niceDate(fresh.data_through)
        : "<b>Up to date through " + niceDate(fresh.data_through) + "</b>";
      var tail = buildStale
        ? " · <b>the update has not run since " + niceTime(fresh.built) +
          "</b> — check the Actions tab"
        : checked + nextText;
      setStrip("bad", head + tail);
    }
  }
  function fetchJSON(path) {
    // GitHub Pages serves JSON with max-age=600 and offers no way to change
    // it. A freshness strip computed from cached JSON would confidently report
    // the wrong day, so bypass the HTTP cache for data.
    return fetch(path, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("fetch failed: " + path);
      return r.json();
    });
  }
  // A static site fails quietly: a renamed slug or a half-finished deploy just
  // leaves the page empty. Say so where the data was meant to go.
  function showLoadError(targetId, err) {
    setStrip("bad", "<b>Could not load this page's data</b> · try a reload");
    var t = el(targetId);
    if (t) t.innerHTML = "<p class='loaderr'>Could not load this page's data. " +
      "Try a reload; if it persists, the last build may have failed.</p>";
    if (window.console) console.error(err);
  }

  var BASE_LAYOUT = {
    paper_bgcolor: C.surface, plot_bgcolor: C.surface,
    font: { color: C.ink2, family: "system-ui, sans-serif", size: 12 },
    margin: { l: 64, r: 16, t: 8, b: 40 },
    hovermode: "x unified",
    hoverlabel: { bgcolor: C.page, bordercolor: C.grid, font: { color: C.ink2 } },
    dragmode: "pan",
    // Range buttons are HTML (#range-toggle), not Plotly's rangeselector: that
    // one had to be dropped on phones for space, which left them with no way
    // to zoom at all.
    xaxis: { gridcolor: C.grid, linecolor: C.grid, zeroline: false },
    yaxis: { gridcolor: C.grid, linecolor: C.grid, zeroline: false },
    legend: { orientation: "h", y: 1.06, x: 0, font: { size: 11.5 } },
    showlegend: true
  };
  // No scrollZoom: on desktop it steals the wheel from page scrolling, and on
  // phones it does nothing, because this Plotly build (3.8.2) has no
  // cartesian pinch gesture at all; its drag code reads only the first touch.
  // Pinch is implemented by hand in enablePinch below.
  var CONFIG = { displayModeBar: false, responsive: true, doubleClick: "reset" };

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

  function defaultRange(dates, days) {
    if (!dates.length || days == null) return null;
    var end = new Date(dates[dates.length - 1] + "T00:00:00Z");
    var start = new Date(end.getTime() - days * 864e5);
    end = new Date(end.getTime() + 3 * 864e5);
    return [start.toISOString().slice(0, 10), end.toISOString().slice(0, 10)];
  }

  // --- pinch to zoom --------------------------------------------------------
  // Two fingers on the plot scale the x-axis about the point between them, so
  // what you're looking at stays under your fingers. Capture phase so Plotly
  // never sees the second finger as the start of a one-finger pan. One finger
  // is left alone: Plotly pans with it, and .chart's touch-action lets a
  // vertical swipe scroll the page as usual.
  var DAY_MS = 864e5;
  function enablePinch(gd) {
    if (gd._pinchWired) return;
    gd._pinchWired = true;
    var pinch = null, pending = null;

    function dist(t) {
      var dx = t[0].clientX - t[1].clientX, dy = t[0].clientY - t[1].clientY;
      return Math.sqrt(dx * dx + dy * dy) || 1;
    }
    function midX(t) { return (t[0].clientX + t[1].clientX) / 2; }
    function xRangeMs(ax) {
      return [Date.parse(ax.range[0]), Date.parse(ax.range[1])];
    }
    function fullSpanMs() {
      var lo = Infinity, hi = -Infinity;
      (gd.data || []).forEach(function (tr) {
        (tr.x || []).forEach(function (x) {
          var t = Date.parse(x);
          if (!isNaN(t)) { lo = Math.min(lo, t); hi = Math.max(hi, t); }
        });
      });
      return isFinite(lo) ? Math.max(hi - lo + 3 * DAY_MS, 2 * DAY_MS) : null;
    }

    gd.addEventListener("touchstart", function (e) {
      if (e.touches.length !== 2) return;
      var fl = gd._fullLayout, ax = fl && fl.xaxis;
      if (!ax || !ax.range) return;
      e.preventDefault(); e.stopPropagation();
      var left = gd.getBoundingClientRect().left + fl._size.l;
      var r = xRangeMs(ax), w = fl._size.w || 1;
      var frac = (midX(e.touches) - left) / w;
      pinch = { d0: dist(e.touches), r0: r, span0: r[1] - r[0],
                anchor: r[0] + frac * (r[1] - r[0]), frac: frac,
                full: fullSpanMs() };
    }, { capture: true, passive: false });

    gd.addEventListener("touchmove", function (e) {
      if (!pinch || e.touches.length !== 2) return;
      e.preventDefault(); e.stopPropagation();
      var span = pinch.span0 * pinch.d0 / dist(e.touches);
      span = Math.max(2 * DAY_MS, Math.min(pinch.full || span, span));
      var lo = pinch.anchor - pinch.frac * span;
      pending = [new Date(lo).toISOString(), new Date(lo + span).toISOString()];
      if (!pinch.raf) {
        pinch.raf = requestAnimationFrame(function () {
          if (pinch) pinch.raf = null;
          if (pending) Plotly.relayout(gd, { "xaxis.range": pending });
          pending = null;
        });
      }
    }, { capture: true, passive: false });

    function end(e) {
      if (!pinch) return;
      if (e.touches.length >= 2) return;
      e.stopPropagation();
      pinch = null;
      // A hand-zoomed chart matches no preset, so no button should claim it.
      var seg = document.getElementById("range-toggle");
      if (seg) seg.querySelectorAll("button").forEach(function (b) { b.classList.remove("on"); });
    }
    gd.addEventListener("touchend", end, { capture: true });
    gd.addEventListener("touchcancel", end, { capture: true });
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
                    marker: { color: C.blue, size: 4, opacity: soft ? 0.3 : 0.5 },
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
    return { traces: traces, yvals: v, dates: dates };
  }

  function aggTraces(payload, view) {
    var m = payload.meta, rows = payload.agg[view] || [];
    var dates = col(rows, 0), v = col(rows, 1);
    var custom = rows.map(function (r) {
      return fmt(m.format, r[1]) + " · " + r[2] + " nights";
    });
    return {
      traces: [{ x: dates, y: v, name: view, mode: "lines+markers",
                 line: { color: C.blue, width: 2 }, marker: { size: 5, color: C.blue },
                 customdata: custom, hovertemplate: "%{customdata}<extra></extra>" }],
      yvals: v, dates: dates
    };
  }

  // days: window to open on; undefined = the view's default, null = all-time.
  function render(chartEl, payload, view, days) {
    var built = view === "daily" ? dailyTraces(payload) : aggTraces(payload, view);
    var layout = deepMerge(BASE_LAYOUT, {});
    if (payload.meta.format === "clock") layout.yaxis = deepMerge(layout.yaxis, clockAxis(built.yvals));
    if (days === undefined) days = DEFAULT_WINDOW[view];
    var r = defaultRange(built.dates, days);
    if (r) layout.xaxis.range = r; else layout.xaxis.autorange = true;
    if (window.innerWidth < 640) layout.margin.l = 48;
    chartEl.dataset.view = view;
    Plotly.react(chartEl, built.traces, layout, CONFIG);
    enablePinch(chartEl);
  }

  // The range buttons live beside the view toggle and change with it: "4m"
  // means nothing on a quarterly chart.
  function rangeButtons(seg, view) {
    if (!seg) return;
    var opts = RANGE_OPTIONS[view] || [], dflt = DEFAULT_WINDOW[view];
    seg.hidden = !opts.length;
    seg.innerHTML = opts.map(function (o) {
      var on = (o[1] === dflt) ? ' class="on"' : "";
      return "<button data-days=\"" + (o[1] == null ? "" : o[1]) + "\"" + on + ">" + o[0] + "</button>";
    }).join("");
  }
  function wireRange(seg, chartEl, payload) {
    wireSeg(seg, "days", function (d) {
      render(chartEl, payload, chartEl.dataset.view || "daily", d === "" ? null : Number(d));
    });
  }

  function statsHTML(payload) {
    var m = payload.meta, s = payload.stats || {}, d = payload.dist || {};
    function block(lbl, val, pct, extra) {
      var pctLine = pct == null ? "" : '<div class="pct"><b>' + ordinal(pct) + "</b></div>";
      return '<div class="stat"><div class="lbl">' + lbl + '</div>' +
             '<div class="val">' + fmt(m.format, val) + "</div>" + pctLine +
             (extra ? '<div class="pct">' + extra + "</div>" : "") + "</div>";
    }
    var dayLbl = "Last night · " + (s.day || "");
    if (s.carried) dayLbl += '<span class="carried"><br>carried forward, ' +
      "no sleep recorded that night</span>";
    return block(dayLbl, s.value, s.pct,
                 vsMedian(m.format, s.value, d.p50)) +
           block("7-day avg", s.avg7, s.pct7) +
           block("30-day avg", s.avg30, s.pct30);
  }

  // Range strip: min · [p25 ▮ p75] · max, with median tick and latest marker.
  function distHTML(payload) {
    var m = payload.meta, d = payload.dist, s = payload.stats || {};
    if (!d || d.min == null) return "";
    var W = 600, H = 44, padL = 8, padR = 8;
    var span = (d.max - d.min) || 1;
    function x(v) { return padL + (v - d.min) / span * (W - padL - padR); }
    var latest = s.value;
    var svg = '<svg class="strip" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">' +
      '<line x1="' + x(d.min) + '" y1="20" x2="' + x(d.max) + '" y2="20" class="rail"/>' +
      '<rect x="' + x(d.p25) + '" y="12" width="' + (x(d.p75) - x(d.p25)) + '" height="16" class="iqr"/>' +
      '<line x1="' + x(d.p50) + '" y1="9" x2="' + x(d.p50) + '" y2="31" class="med"/>' +
      (latest == null ? "" :
        '<circle cx="' + x(latest) + '" cy="20" r="6" class="now"/>') +
      "</svg>";
    var labels = '<div class="striplbl">' +
      '<span>min ' + fmt(m.format, d.min) + '</span>' +
      '<span>p25 ' + fmt(m.format, d.p25) + '</span>' +
      '<span>median ' + fmt(m.format, d.p50) + '</span>' +
      '<span>p75 ' + fmt(m.format, d.p75) + '</span>' +
      '<span>max ' + fmt(m.format, d.max) + '</span></div>';
    return '<div class="controls"><h2>Where last night sits</h2>' +
      '<span class="sub">' + d.n.toLocaleString() + " " + (d.unit || "nights") +
      ' · shaded box is the middle 50%</span></div>' +
      svg + labels;
  }

  function explainHTML(payload) {
    var ex = payload.explain;
    if (!ex) return "";
    var isScore = ex.kind === "score";
    // Confidence gets its own headed column: a bare "high" beside a bedtime
    // reads as a rating of the value, which is the misread being fixed.
    var head = isScore
      ? "<tr><th>Component</th><th>Last night</th><th class='num'>Score /100</th>" +
        "<th class='num'>Weight</th><th>Measurement confidence</th></tr>"
      : "<tr><th>Input</th><th>Value</th></tr>";
    var rows = ex.components.map(function (c) {
      var val = fmt(c.format, c.value);
      if (isScore) {
        var conf = c.confidence
          ? '<span class="badge badge-' + c.confidence + '" style="margin-left:0">' +
            c.confidence + "</span>" : "";
        var note = c.note ? ' <span class="sub">' + c.note + "</span>" : "";
        return "<tr><td>" + c.label + note + "</td><td>" + val + "</td>" +
          "<td class='num'>" + (c.score == null ? "—" : Math.round(c.score)) + "</td>" +
          "<td class='num'>" + c.weight + "</td><td>" + conf + "</td></tr>";
      }
      var n = c.note ? ' <span class="sub">' + c.note + "</span>" : "";
      return "<tr" + (c.result ? " class='result'" : "") + "><td>" + c.label + n +
        "</td><td class='num'>" + val + "</td></tr>";
    }).join("");
    var paras = Array.isArray(ex.text) ? ex.text : [ex.text];
    return "<h2>How it's calculated</h2>" +
      paras.map(function (p) { return "<p>" + p + "</p>"; }).join("") +
      "<p class='formula'>" + ex.formula + "</p>" +
      "<div class='scroll-x'><table class='comps'>" + head + rows + "</table></div>";
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
      renderFreshness(payload.fresh);
      el("stats").innerHTML = statsHTML(payload);
      var dist = el("dist");
      var distMarkup = distHTML(payload);
      if (distMarkup) dist.innerHTML = distMarkup; else dist.hidden = true;
      var contrib = el("contrib");
      if (contrib && payload.meta.latest_contribution != null) {
        contrib.textContent = "last night scored " +
          payload.meta.latest_contribution.toFixed(0) + "/100 on this component";
      }
      var exp = el("explain");
      var expMarkup = explainHTML(payload);
      if (expMarkup) {
        exp.innerHTML = expMarkup; exp.hidden = false;
        // Only if the table genuinely overflows: say so, rather than clipping
        // a column silently.
        var sx = exp.querySelector(".scroll-x");
        if (sx && sx.scrollWidth > sx.clientWidth + 2) {
          sx.insertAdjacentHTML("afterend", "<p class='sub'>Swipe the table sideways for more columns.</p>");
        }
      }
      render(el("chart"), payload, "daily");
      rangeButtons(el("range-toggle"), "daily");
      wireRange(el("range-toggle"), el("chart"), payload);
      el("tb").innerHTML = tbHTML(payload, "all");
      wireSeg(el("view-toggle"), "view", function (v) {
        render(el("chart"), payload, v);
        rangeButtons(el("range-toggle"), v);
      });
      wireSeg(el("tb-toggle"), "period", function (p) { el("tb").innerHTML = tbHTML(payload, p); });
    }).catch(function (err) { showLoadError("stats", err); });
  }

  // --- overview -------------------------------------------------------------
  function initOverview() {
    fetchJSON(P.root + "/data/overview.json").then(function (ov) {
      var since = (ov.range || "").split("→")[0].trim();
      el("ov-sub").textContent = "Sleep data through " +
        niceDate((ov.fresh || {}).data_through) + " · " +
        Number(ov.nights).toLocaleString() + " nights since " + niceDate(since);
      renderFreshness(ov.fresh);

      var partial = ov.partial;
      if (partial) {
        var miss = (partial.missing || []).indexOf("hrv") >= 0
          ? "duration, HRV and timing" : "the detail behind it";
        el("ov-sub").insertAdjacentHTML("afterend",
          '<div class="partialbox">' + niceDate(partial.day) +
          " is partial. Oura has posted a score of " + Math.round(partial.oura_score) +
          " but has not released the session yet, so " + miss +
          " are still missing. Metrics that carry forward already show " +
          niceDate(partial.day) + "; measured ones stop a day earlier.</div>");
      }
      var flag = el("flag");
      if (ov.flag && ov.flag.raised) {
        flag.innerHTML = "<div class='flagbox'><b>Something's off:</b> " +
          ov.flag.detail + "</div>";
      } else {
        flag.innerHTML = "<p class='allclear'>No health flags — temperature and " +
          "respiratory rate are within their seasonal baselines.</p>";
      }
      el("cards").innerHTML = ov.cards.map(function (c) {
        function row(lbl, v, p) {
          return "<tr><td>" + lbl + "</td><td class='num'>" + fmt(c.format, v) +
            "</td><td class='pctcell'>" + ordinal(p) + "</td></tr>";
        }
        return "<a class='cardlet' href='metrics/" + c.slug + ".html'>" +
          "<div class='lbl'>" + c.label + "</div>" +
          "<div class='big'>" + fmt(c.format, c.value) + "</div>" +
          "<table class='trio'>" +
          row("Last night", c.value, c.pct) +
          row("7-day", c.avg7, c.pct7) +
          row("30-day", c.avg30, c.pct30) +
          "</table></a>";
      }).join("");
      // The chart is the metric being optimised, which is also the lead card.
      return fetchJSON(P.root + "/data/m/opportunity-debt-h.json");
    }).then(function (payload) {
      render(el("chart"), payload, "daily");
      rangeButtons(el("range-toggle"), "daily");
      wireRange(el("range-toggle"), el("chart"), payload);
    }).catch(function (err) { showLoadError("ov-sub", err); });
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
