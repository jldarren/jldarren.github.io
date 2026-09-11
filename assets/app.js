// TechPulse：纯前端渲染 data/news.json（由 scripts/fetch_news.py 每日生成）
(function () {
  "use strict";

  var DATA_URL = "data/news.json";
  var SUMMARY_URL = "data/summaries.json";
  var ARCHIVE_INDEX_URL = "data/archive/index.json";

  var state = {
    items: [],
    summaries: {},
    categories: [],
    meta: null,
    hidden: loadHidden(),
    query: "",
    todayOnly: localStorage.getItem("tp-today") === "1",
    day: "" // 空字符串 = 最新
  };

  var el = {
    feed: document.getElementById("feed"),
    chips: document.getElementById("categoryChips"),
    search: document.getElementById("searchInput"),
    todayOnly: document.getElementById("todayOnly"),
    archive: document.getElementById("archiveSelect"),
    count: document.getElementById("resultCount"),
    updated: document.getElementById("updated"),
    empty: document.getElementById("emptyState"),
    footer: document.getElementById("footerInfo"),
    themeToggle: document.getElementById("themeToggle")
  };

  // ---------------- 工具 ----------------
  function loadHidden() {
    try {
      return JSON.parse(localStorage.getItem("tp-cats") || "[]");
    } catch (e) {
      return [];
    }
  }

  function saveHidden() {
    localStorage.setItem("tp-cats", JSON.stringify(state.hidden));
  }

  function escapeHtml(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function toDate(iso) {
    var d = new Date(iso);
    return isNaN(d.getTime()) ? null : d;
  }

  function dayKey(iso) {
    var d = toDate(iso);
    if (!d) return "未知日期";
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }

  function pad(n) {
    return n < 10 ? "0" + n : "" + n;
  }

  function dayLabel(key) {
    var today = dayKey(new Date().toISOString());
    var yesterday = dayKey(new Date(Date.now() - 86400000).toISOString());
    if (key === today) return "今天";
    if (key === yesterday) return "昨天";
    return key;
  }

  function relativeTime(iso) {
    var d = toDate(iso);
    if (!d) return "";
    var diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 3600) return Math.max(1, Math.floor(diff / 60)) + " 分钟前";
    if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
    if (diff < 86400 * 7) return Math.floor(diff / 86400) + " 天前";
    return dayKey(iso);
  }

  // ---------------- 数据加载 ----------------
  function fetchJson(url) {
    return fetch(url, { cache: "no-store" }).then(function (res) {
      if (!res.ok) throw new Error(url + " -> HTTP " + res.status);
      return res.json();
    });
  }

  function load() {
    return fetchJson(DATA_URL)
      .then(function (data) {
        state.meta = data;
        state.categories = data.categories || [];
        state.items = data.items || [];
        el.updated.textContent =
          "更新于 " + (data.generated_at || "").replace("T", " ").slice(0, 16) + " UTC";
        el.footer.textContent =
          "共 " + state.items.length + " 条 · " + (data.sources || []).length +
          " 个资讯源 · " + (data.categories || []).length + " 个分类 · 由 GitHub Actions 每日自动更新";
        return fetchJson(SUMMARY_URL).catch(function () {
          return null;
        });
      })
      .then(function (summaries) {
        state.summaries = (summaries && summaries.summaries) || {};
        return fetchJson(ARCHIVE_INDEX_URL).catch(function () {
          return null;
        });
      })
      .then(function (index) {
        renderArchive(index);
        render();
      })
      .catch(function (err) {
        showError(err);
      });
  }

  function showError(err) {
    el.feed.innerHTML =
      '<div class="error-box">数据加载失败：' + escapeHtml(err.message) +
      "<p>如果是通过 <code>file://</code> 直接打开的，请在仓库根目录执行 " +
      "<code>python3 -m http.server 8000</code> 后访问 " +
      "<code>http://localhost:8000</code>。</p>" +
      "<p>若还没有数据，先执行一次 <code>python3 scripts/fetch_news.py</code>。</p></div>";
  }

  function renderArchive(index) {
    var days = (index && index.days) || [];
    var options = ['<option value="">最新</option>'];
    days.forEach(function (d) {
      options.push('<option value="' + d + '">' + d + "</option>");
    });
    el.archive.innerHTML = options.join("");
  }

  // ---------------- 渲染 ----------------
  function currentItems() {
    var source = state.day ? state.dayItems || [] : state.items;
    var query = state.query.trim().toLowerCase();
    var todayKey = dayKey(new Date().toISOString());

    return source.filter(function (item) {
      if (state.hidden.indexOf(item.category) !== -1) return false;
      if (state.todayOnly && dayKey(item.published) !== todayKey) return false;
      if (!query) return true;
      var blob = (
        item.title + " " + item.source + " " + (item.summary || "") + " " +
        (state.summaries[item.id] || "")
      ).toLowerCase();
      return query.split(/\s+/).every(function (word) {
        return blob.indexOf(word) !== -1;
      });
    });
  }

  function renderChips() {
    var counts = {};
    var pool = state.day ? state.dayItems || [] : state.items;
    pool.forEach(function (item) {
      counts[item.category] = (counts[item.category] || 0) + 1;
    });

    var html = ['<button class="chip" type="button" data-cat="__all" aria-pressed="' +
      (state.hidden.length === 0 ? "true" : "false") + '">全部<span class="count">' +
      pool.length + "</span></button>"];

    state.categories.forEach(function (cat) {
      var active = state.hidden.indexOf(cat.id) === -1;
      html.push(
        '<button class="chip" type="button" data-cat="' + escapeHtml(cat.id) +
          '" aria-pressed="' + (active ? "true" : "false") + '"' +
          ' style="--chip-color:' + escapeHtml(cat.color || "#6366f1") + '">' +
          '<span class="dot"></span>' + escapeHtml(cat.name) +
          '<span class="count">' + (counts[cat.id] || 0) + "</span></button>"
      );
    });
    el.chips.innerHTML = html.join("");
  }

  function cardHtml(item) {
    var color = "#6366f1";
    state.categories.forEach(function (c) {
      if (c.id === item.category) color = c.color || color;
    });
    var ai = state.summaries[item.id];
    var summary = item.summary && item.summary.length > 2
      ? '<p class="card-summary">' + escapeHtml(item.summary) + "</p>"
      : "";
    var aiBlock = ai
      ? '<p class="card-ai"><span class="ai-tag">AI 摘要</span>' + escapeHtml(ai) + "</p>"
      : "";
    return (
      '<article class="card">' +
      '<h2 class="card-title"><a href="' + escapeHtml(item.url) +
      '" target="_blank" rel="noopener noreferrer">' + escapeHtml(item.title) + "</a></h2>" +
      '<div class="card-meta">' +
      '<span class="badge" style="--badge-color:' + color + '">' +
      escapeHtml(item.category_name || item.category) + "</span>" +
      '<span class="badge-outline">' + escapeHtml(item.source) + "</span>" +
      "<span>" + relativeTime(item.published) + "</span>" +
      "</div>" + aiBlock + summary +
      "</article>"
    );
  }

  function render() {
    renderChips();
    var items = currentItems();

    el.count.textContent = "共 " + items.length + " 条" +
      (items.length ? "（按发布时间倒序）" : "");
    el.empty.hidden = items.length !== 0;

    if (!items.length) {
      el.feed.innerHTML = "";
      return;
    }

    var groups = [];
    var map = {};
    items.forEach(function (item) {
      var key = dayKey(item.published);
      if (!map[key]) {
        map[key] = { key: key, items: [] };
        groups.push(map[key]);
      }
      map[key].items.push(item);
    });

    el.feed.innerHTML = groups
      .map(function (group) {
        return (
          '<section class="day-group"><h2 class="day-label">' +
          escapeHtml(dayLabel(group.key)) + "</h2>" +
          '<div class="cards">' + group.items.map(cardHtml).join("") + "</div></section>"
        );
      })
      .join("");
  }

  // ---------------- 事件 ----------------
  function bind() {
    el.chips.addEventListener("click", function (event) {
      var chip = event.target.closest(".chip");
      if (!chip) return;
      var cat = chip.getAttribute("data-cat");
      if (cat === "__all") {
        state.hidden = state.hidden.length ? [] : state.categories.map(function (c) { return c.id; });
      } else {
        var index = state.hidden.indexOf(cat);
        if (index === -1) state.hidden.push(cat);
        else state.hidden.splice(index, 1);
      }
      saveHidden();
      render();
    });

    var timer = null;
    el.search.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        state.query = el.search.value;
        render();
      }, 150);
    });

    el.todayOnly.checked = state.todayOnly;
    el.todayOnly.addEventListener("change", function () {
      state.todayOnly = el.todayOnly.checked;
      localStorage.setItem("tp-today", state.todayOnly ? "1" : "0");
      render();
    });

    el.archive.addEventListener("change", function () {
      var day = el.archive.value;
      if (!day) {
        state.day = "";
        state.dayItems = null;
        render();
        return;
      }
      fetchJson("data/archive/" + day + ".json")
        .then(function (data) {
          state.day = day;
          state.dayItems = data.items || [];
          render();
        })
        .catch(function () {
          state.day = "";
          render();
        });
    });

    el.themeToggle.addEventListener("click", function () {
      var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("tp-theme", next);
    });
  }

  // ---------------- 启动 ----------------
  var saved = localStorage.getItem("tp-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);

  bind();
  load();
})();
