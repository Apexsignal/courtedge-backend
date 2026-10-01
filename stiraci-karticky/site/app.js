/* ==========================================================================
   Digitální stírací kartičky
   Veškerý obsah se načítá z config.json – pro jiného obdarovaného stačí
   vyměnit config.json a fotky ve složce img/.
   ========================================================================== */
(function () {
  "use strict";

  var REVEAL_THRESHOLD = 0.5;   // podíl setřené plochy, po kterém se políčko odkryje
  var BRUSH_CSS_PX = 26;        // poloměr „mince“ v CSS pixelech

  var reducedMotion = false;
  try {
    reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch (e) { /* ignore */ }

  var config = null;
  var state = null;
  var cards = [];               // { cfg, el, index, scratcher }
  var currentIndex = 0;

  /* ------------------------------------------------------------------------
     Pomocné funkce
     ------------------------------------------------------------------------ */
  function $(sel, root) { return (root || document).querySelector(sel); }

  function stem(file) {
    return String(file || "").replace(/\.[a-z0-9]+$/i, "");
  }

  function photoSources(file) {
    var base = "img/" + stem(file);
    return { webp: base + ".webp", jpg: base + ".jpg" };
  }

  function fillTemplate(tpl, data) {
    return String(tpl || "").replace(/\{(\w+)\}/g, function (m, key) {
      return data[key] != null ? data[key] : "";
    });
  }

  function formatDate(ts) {
    try {
      return new Date(ts).toLocaleDateString("cs-CZ", { day: "numeric", month: "numeric", year: "numeric" });
    } catch (e) {
      var d = new Date(ts);
      return d.getDate() + ". " + (d.getMonth() + 1) + ". " + d.getFullYear();
    }
  }

  function vibrate(pattern) {
    try {
      if (navigator.vibrate) navigator.vibrate(pattern);
    } catch (e) { /* ignore */ }
  }

  var toastTimer = null;
  function toast(msg, ms) {
    var el = $("#toast");
    if (!el) return;
    el.textContent = msg;
    el.classList.add("is-visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove("is-visible"); }, ms || 2600);
  }

  /* ------------------------------------------------------------------------
     Stav (localStorage)
     ------------------------------------------------------------------------ */
  function storageKey() {
    return (config && config.storageKey) || "stiraci-karticky";
  }

  function emptyState() {
    return { v: 1, opened: false, scratched: {}, claimed: {}, wishes: {}, finaleShown: false, lastIndex: 0 };
  }

  function loadState() {
    var s = emptyState();
    try {
      var raw = window.localStorage.getItem(storageKey());
      if (raw) {
        var parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") {
          s.opened = !!parsed.opened;
          s.scratched = parsed.scratched || {};
          s.claimed = parsed.claimed || {};
          s.wishes = parsed.wishes || {};
          s.finaleShown = !!parsed.finaleShown;
          s.lastIndex = parseInt(parsed.lastIndex, 10) || 0;
        }
      }
    } catch (e) { /* prázdný stav */ }
    return s;
  }

  function saveState() {
    try {
      window.localStorage.setItem(storageKey(), JSON.stringify(state));
    } catch (e) { /* např. anonymní režim – nevadí, jen se stav neuloží */ }
  }

  function handleResetParam() {
    try {
      var params = new URLSearchParams(window.location.search);
      if (params.get("reset") === "1") {
        try { window.localStorage.removeItem(storageKey()); } catch (e) { /* ignore */ }
        params.delete("reset");
        var qs = params.toString();
        var clean = window.location.pathname + (qs ? "?" + qs : "") + window.location.hash;
        try { window.history.replaceState(null, "", clean); } catch (e) { /* ignore */ }
        return true;
      }
    } catch (e) { /* ignore */ }
    return false;
  }

  /* ------------------------------------------------------------------------
     Obrazovky
     ------------------------------------------------------------------------ */
  var screens = ["intro", "greeting", "cards", "finale"];

  function showScreen(name) {
    screens.forEach(function (s) {
      var el = $("#screen-" + s);
      if (el) el.classList.toggle("is-active", s === name);
    });
    if (name === "cards") {
      // Po zobrazení přepočítej velikosti plátna a skoč na aktuální kartičku.
      requestAnimationFrame(function () {
        cards.forEach(function (c) { if (c.scratcher) c.scratcher.resize(); });
        goTo(currentIndex, true);
      });
    }
    if (name === "finale") renderSummary();
  }

  /* ------------------------------------------------------------------------
     Úvod a přání
     ------------------------------------------------------------------------ */
  function setupIntro() {
    var cover = config.cover || {};
    var src = photoSources(cover.photo);
    $("#cover-webp").srcset = src.webp;
    var img = $("#cover-img");
    img.src = src.jpg;
    img.alt = config.title || "";
    if (cover.position) img.style.objectPosition = cover.position;

    $("#intro-age").textContent = config.age != null ? String(config.age) : "";
    $("#intro-title").textContent = config.title || "";
    $("#intro-subtitle").textContent = config.subtitle || "";

    var btn = $("#btn-open");
    if (state.opened) btn.textContent = "Pokračovat ke kartičkám";
    btn.addEventListener("click", function () {
      if (state.opened) {
        showScreen("cards");
      } else {
        state.opened = true;
        saveState();
        showScreen("greeting");
        sparkles();
        vibrate(30);
      }
    });

    // Greeting
    $("#greeting-age").textContent = (config.recipient || "") + (config.age ? " · " + config.age : "");
    var title = $("#greeting-title");
    title.textContent = "";
    String(config.greeting || "Všechno nejlepší k narozeninám").split(/\s+/).forEach(function (word, i) {
      var span = document.createElement("span");
      span.className = "w";
      span.style.animationDelay = (0.15 + i * 0.16) + "s";
      span.textContent = word;
      title.appendChild(span);
      title.appendChild(document.createTextNode(" "));
    });
    var msg = config.message || "";
    if (config.validity) msg += " " + config.validity + ".";
    $("#greeting-msg").textContent = msg.trim();
    $("#greeting-from").textContent = config.from || "";
    $("#btn-to-cards").addEventListener("click", function () { showScreen("cards"); });

    document.title = config.ogTitle || config.title || document.title;
  }

  function sparkles() {
    if (reducedMotion) return;
    var box = $(".greeting__sparkles");
    if (!box || box.childElementCount) return;
    var icons = ["💛", "✨", "🎉", "💗", "✨", "🎈"];
    for (var i = 0; i < 16; i++) {
      var s = document.createElement("i");
      s.textContent = icons[i % icons.length];
      s.style.left = (Math.random() * 92) + "%";
      s.style.animationDelay = (Math.random() * 5) + "s";
      s.style.animationDuration = (4 + Math.random() * 3) + "s";
      s.style.fontSize = (14 + Math.random() * 12) + "px";
      box.appendChild(s);
    }
  }

  /* ------------------------------------------------------------------------
     Stírací vrstva
     ------------------------------------------------------------------------ */
  function Scratcher(canvas, opts) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d", { willReadFrequently: true });
    this.opts = opts;
    this.done = false;
    this.drawing = false;
    this.last = null;
    this.w = 0;
    this.h = 0;
    this.lastCheck = 0;
    this.scratchedSinceDraw = false;
    this.bind();
  }

  Scratcher.prototype.resize = function (force) {
    if (this.done) return;
    var rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    var w = Math.round(rect.width), h = Math.round(rect.height);
    if (!force && w === this.w && h === this.h) return;
    this.w = w;
    this.h = h;
    var dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    this.dpr = dpr;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.paint();
  };

  Scratcher.prototype.paint = function () {
    var ctx = this.ctx, w = this.w, h = this.h, main = this.opts.main;
    ctx.globalCompositeOperation = "source-over";
    ctx.clearRect(0, 0, w, h);

    // zaoblené rohy
    var r = 18;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(r, 0);
    ctx.arcTo(w, 0, w, h, r);
    ctx.arcTo(w, h, 0, h, r);
    ctx.arcTo(0, h, 0, 0, r);
    ctx.arcTo(0, 0, w, 0, r);
    ctx.closePath();
    ctx.clip();

    // základní metalický gradient
    var g = ctx.createLinearGradient(0, 0, w, h);
    if (main) {
      g.addColorStop(0, "#8F6E2A");
      g.addColorStop(0.18, "#E9CF86");
      g.addColorStop(0.36, "#B8923F");
      g.addColorStop(0.52, "#FFF1C1");
      g.addColorStop(0.68, "#C9A04A");
      g.addColorStop(0.85, "#E9CF86");
      g.addColorStop(1, "#9A7631");
    } else {
      g.addColorStop(0, "#A9843A");
      g.addColorStop(0.25, "#D4AF5A");
      g.addColorStop(0.5, "#EED793");
      g.addColorStop(0.72, "#C9A04A");
      g.addColorStop(1, "#B8923F");
    }
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);

    // lesklý pruh
    var sheen = ctx.createLinearGradient(0, h, w, 0);
    sheen.addColorStop(0.3, "rgba(255,255,255,0)");
    sheen.addColorStop(0.45, "rgba(255,255,255,.35)");
    sheen.addColorStop(0.6, "rgba(255,255,255,0)");
    ctx.fillStyle = sheen;
    ctx.fillRect(0, 0, w, h);

    // zrnitá textura
    var count = Math.round((w * h) / (main ? 22 : 28));
    for (var i = 0; i < count; i++) {
      var x = Math.random() * w, y = Math.random() * h;
      var light = Math.random() > 0.5;
      ctx.fillStyle = light
        ? "rgba(255,248,220," + (0.15 + Math.random() * 0.35) + ")"
        : "rgba(110,80,20," + (0.08 + Math.random() * 0.22) + ")";
      var s = Math.random() < 0.85 ? 1 : 1.6;
      ctx.fillRect(x, y, s, s);
    }

    // jiskry pro hlavní výhru
    if (main) {
      for (var k = 0; k < 14; k++) {
        drawStar(ctx, Math.random() * w, Math.random() * h, 2 + Math.random() * 4, "rgba(255,255,240,.85)");
      }
    }

    // jemný vnitřní rámeček
    ctx.strokeStyle = "rgba(255,245,210,.55)";
    ctx.lineWidth = 2;
    ctx.setLineDash([7, 6]);
    ctx.strokeRect(9, 9, w - 18, h - 18);
    ctx.setLineDash([]);

    // nápis
    var label = this.opts.label || "Setři";
    var size = Math.min(h * 0.32, main ? 46 : 42);
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = size + 'px "Yeseva One", Georgia, serif';
    ctx.fillStyle = "rgba(255,250,230,.55)";
    ctx.fillText(label, w / 2 + 1, h / 2 - (main ? 8 : 4) + 1.5);
    ctx.fillStyle = "rgba(95,70,20,.82)";
    ctx.fillText(label, w / 2, h / 2 - (main ? 8 : 4));
    if (main || this.opts.sublabel) {
      ctx.font = '600 ' + Math.max(11, size * 0.32) + 'px "Poppins", system-ui, sans-serif';
      ctx.fillStyle = "rgba(95,70,20,.75)";
      ctx.fillText(this.opts.sublabel || "", w / 2, h / 2 + size * 0.62);
    }
    // ikona mince
    drawCoinHint(ctx, w - 34, h - 30);

    ctx.restore();
    this.scratchedSinceDraw = false;
  };

  function drawStar(ctx, x, y, r, color) {
    ctx.save();
    ctx.fillStyle = color;
    ctx.beginPath();
    for (var i = 0; i < 8; i++) {
      var a = (Math.PI / 4) * i;
      var rr = i % 2 === 0 ? r : r * 0.3;
      ctx.lineTo(x + Math.cos(a) * rr, y + Math.sin(a) * rr);
    }
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  function drawCoinHint(ctx, x, y) {
    ctx.save();
    ctx.globalAlpha = 0.7;
    ctx.fillStyle = "rgba(95,70,20,.55)";
    ctx.beginPath();
    ctx.arc(x, y, 11, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "rgba(255,240,200,.9)";
    ctx.beginPath();
    ctx.arc(x - 1, y - 1, 8, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  Scratcher.prototype.bind = function () {
    var self = this, c = this.canvas;

    function pos(e) {
      var rect = c.getBoundingClientRect();
      return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    }

    c.addEventListener("pointerdown", function (e) {
      if (self.done) return;
      if (e.pointerType === "mouse" && e.button !== 0) return;
      e.preventDefault();
      self.drawing = true;
      try { c.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      self.last = pos(e);
      self.scratchAt(self.last, self.last);
      if (self.opts.onStart) self.opts.onStart();
    });

    c.addEventListener("pointermove", function (e) {
      if (!self.drawing || self.done) return;
      e.preventDefault();
      var events = (e.getCoalescedEvents && e.getCoalescedEvents()) || [e];
      if (!events.length) events = [e];
      for (var i = 0; i < events.length; i++) {
        var p = pos(events[i]);
        self.scratchAt(self.last, p);
        self.last = p;
      }
      var now = Date.now();
      if (now - self.lastCheck > 180) {
        self.lastCheck = now;
        self.check();
      }
    });

    function end(e) {
      if (!self.drawing) return;
      self.drawing = false;
      self.last = null;
      try { c.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      self.check();
    }
    c.addEventListener("pointerup", end);
    c.addEventListener("pointercancel", end);
    c.addEventListener("lostpointercapture", function () { self.drawing = false; self.last = null; });

    // klávesnice / přístupnost: Enter nebo mezerník odkryje celé políčko
    c.addEventListener("keydown", function (e) {
      if (self.done) return;
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        self.reveal();
      }
    });

    // zabrání kontextovému menu / lupě při dlouhém podržení
    c.addEventListener("contextmenu", function (e) { e.preventDefault(); });
  };

  Scratcher.prototype.scratchAt = function (a, b) {
    var ctx = this.ctx;
    ctx.globalCompositeOperation = "destination-out";
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.lineWidth = BRUSH_CSS_PX * 2;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x + 0.01, b.y + 0.01);
    ctx.stroke();
    // mírně nepravidelný okraj – působí jako stírání mincí
    ctx.beginPath();
    ctx.arc(b.x + (Math.random() - 0.5) * 6, b.y + (Math.random() - 0.5) * 6, BRUSH_CSS_PX * 0.9, 0, Math.PI * 2);
    ctx.fill();
    this.scratchedSinceDraw = true;
  };

  Scratcher.prototype.clearedRatio = function () {
    var cw = this.canvas.width, ch = this.canvas.height;
    if (!cw || !ch) return 0;
    var data;
    try {
      data = this.ctx.getImageData(0, 0, cw, ch).data;
    } catch (e) {
      return 0;
    }
    var step = Math.max(4, Math.round(Math.min(cw, ch) / 60)); // vzorkování
    var total = 0, cleared = 0;
    for (var y = 0; y < ch; y += step) {
      for (var x = 0; x < cw; x += step) {
        total++;
        if (data[(y * cw + x) * 4 + 3] < 128) cleared++;
      }
    }
    // rohy jsou díky zaoblení průhledné odjakživa – zanedbatelné
    return total ? cleared / total : 0;
  };

  Scratcher.prototype.check = function () {
    if (this.done || !this.scratchedSinceDraw) return;
    if (this.clearedRatio() >= REVEAL_THRESHOLD) this.reveal();
  };

  Scratcher.prototype.reveal = function () {
    if (this.done) return;
    this.done = true;
    this.drawing = false;
    if (this.opts.onReveal) this.opts.onReveal();
  };

  /* ------------------------------------------------------------------------
     Kartičky
     ------------------------------------------------------------------------ */
  function cardLabel(cfg) {
    if (cfg.main) return "Hlavní výhra";
    if (cfg.type === "joker") return "Žolík";
    return "Stírací los";
  }

  function buildCards() {
    var tpl = $("#tpl-card");
    var track = $("#track");
    var dots = $("#dots");
    var total = config.cards.length;

    config.cards.forEach(function (cfg, i) {
      var frag = tpl.content.cloneNode(true);
      var slide = frag.querySelector(".slide");
      var card = frag.querySelector(".card");
      var id = cfg.id || ("karticka-" + (i + 1));
      cfg.id = id;

      slide.setAttribute("aria-label", (i + 1) + " ze " + total);
      slide.dataset.index = String(i);

      if (cfg.main) card.classList.add("is-main");
      if (cfg.type === "joker") card.classList.add("is-joker");

      var src = photoSources(cfg.photo);
      card.querySelector("source").srcset = src.webp;
      var img = card.querySelector("img");
      img.src = src.jpg;
      img.alt = cfg.photoAlt || "";
      img.loading = i < 2 ? "eager" : "lazy";
      if (cfg.photoPosition) img.style.objectPosition = cfg.photoPosition;

      card.querySelector(".card__badge").textContent = cfg.main ? "★ Hlavní výhra" : "";
      card.querySelector(".card__label").textContent = cardLabel(cfg);
      card.querySelector(".card__num").textContent = (i + 1) + " ze " + total;
      card.querySelector(".prize__title").textContent = cfg.title || "";
      card.querySelector(".prize__sub").textContent = cfg.subtitle || "";

      var note = card.querySelector(".card__note");
      note.textContent = "Setři zlaté políčko prstem." + (config.validity ? " " + config.validity + "." : "");

      var entry = { cfg: cfg, el: card, slide: slide, index: i, scratcher: null };
      cards.push(entry);

      // akce
      var claim = card.querySelector(".btn--claim");
      var sms = card.querySelector(".sms-link");
      var wish = card.querySelector(".wish__input");
      if (cfg.type === "joker") {
        wish.value = state.wishes[id] || "";
        wish.addEventListener("input", function () {
          state.wishes[id] = wish.value;
          saveState();
          updateLinks(entry);
        });
      }
      claim.addEventListener("click", function (e) { onClaim(entry, e, "whatsapp"); });
      sms.addEventListener("click", function (e) { onClaim(entry, e, "sms"); });

      track.appendChild(frag);

      var dot = document.createElement("span");
      dots.appendChild(dot);

      // stírací vrstva
      var canvas = card.querySelector(".scratch__foil");
      if (state.scratched[id]) {
        card.classList.add("is-revealed", "is-preset");
      } else {
        entry.scratcher = new Scratcher(canvas, {
          main: !!cfg.main,
          label: config.scratchLabel || "Setři",
          sublabel: cfg.main ? "hlavní výhru" : "",
          onStart: hideHint,
          onReveal: function () { onReveal(entry); }
        });
      }
      updateCardState(entry);
    });

    // nastavení stavu tlačítek
    cards.forEach(updateLinks);
    updateDots();
  }

  function updateCardState(entry) {
    var id = entry.cfg.id;
    var claimedAt = state.claimed[id];
    entry.el.classList.toggle("is-claimed", !!claimedAt);
    var date = entry.el.querySelector(".stamp__date");
    date.textContent = claimedAt ? formatDate(claimedAt) : "";
    var txt = entry.el.querySelector(".btn--claim .btn__text");
    txt.textContent = claimedAt ? "Poslat znovu" : "Uplatnit";
  }

  function giverDigits() {
    var p = String(config.giverPhone || "");
    if (/x/i.test(p)) return ""; // placeholder ještě nebyl vyplněn
    return p.replace(/[^\d]/g, "");
  }

  function isIOS() {
    var ua = navigator.userAgent || "";
    return /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  }

  function claimText(entry) {
    var cfg = entry.cfg;
    if (cfg.type === "joker") {
      var wish = (state.wishes[cfg.id] || "").trim();
      return fillTemplate(config.jokerClaimMessage || config.claimMessage, { title: cfg.title, wish: wish, recipient: config.recipient });
    }
    return fillTemplate(config.claimMessage, { title: cfg.title, subtitle: cfg.subtitle, recipient: config.recipient });
  }

  function updateLinks(entry) {
    var text = claimText(entry);
    var digits = giverDigits();
    var wa = "https://wa.me/" + digits + "?text=" + encodeURIComponent(text);
    var phone = digits ? "+" + digits : "";
    var sms = "sms:" + phone + (isIOS() ? "&" : "?") + "body=" + encodeURIComponent(text);
    entry.el.querySelector(".btn--claim").href = wa;
    entry.el.querySelector(".sms-link").href = sms;
  }

  function onClaim(entry, e, channel) {
    var cfg = entry.cfg;
    if (cfg.type === "joker" && !(state.wishes[cfg.id] || "").trim()) {
      e.preventDefault();
      toast("Nejdřív napiš své přání 💛");
      var input = entry.el.querySelector(".wish__input");
      try { input.focus(); } catch (err) { /* ignore */ }
      return;
    }
    updateLinks(entry);
    if (!giverDigits()) {
      toast(channel === "sms" ? "Vyber, komu SMS poslat 💛" : "Vyber ve WhatsAppu, komu zprávu poslat 💛", 3200);
    }
    var first = !state.claimed[cfg.id];
    if (first) {
      state.claimed[cfg.id] = Date.now();
      saveState();
      vibrate([20, 40, 20]);
    }
    // Odkaz se otevře nativně (bez preventDefault); razítko přidáme hned.
    setTimeout(function () {
      updateCardState(entry);
      updateDots();
    }, 60);
  }

  function onReveal(entry) {
    var id = entry.cfg.id;
    state.scratched[id] = Date.now();
    saveState();
    entry.el.classList.add("is-revealed");
    vibrate(entry.cfg.main ? [60, 50, 120] : 60);
    burst(entry);
    updateDots();
    updateLinks(entry);

    if (entry.cfg.main && !reducedMotion) confetti(1800);

    if (allScratched() && !state.finaleShown) {
      state.finaleShown = true;
      saveState();
      setTimeout(function () {
        confetti(reducedMotion ? 0 : 4200);
        showScreen("finale");
      }, entry.cfg.main ? 1900 : 1300);
    }
  }

  function burst(entry) {
    if (reducedMotion) return;
    var box = entry.el.querySelector(".scratch");
    var colors = ["#D4AF5A", "#B8923F", "#E7A9B5", "#FFF1C1", "#1F2547"];
    var n = entry.cfg.main ? 28 : 16;
    for (var i = 0; i < n; i++) {
      var s = document.createElement("span");
      s.className = "burst";
      var a = Math.random() * Math.PI * 2;
      var d = 60 + Math.random() * (entry.cfg.main ? 140 : 90);
      s.style.setProperty("--dx", Math.cos(a) * d + "px");
      s.style.setProperty("--dy", Math.sin(a) * d * 0.6 + "px");
      s.style.background = colors[i % colors.length];
      s.style.animationDelay = (Math.random() * 0.12) + "s";
      box.appendChild(s);
      setTimeout(function (el) { return function () { el.remove(); }; }(s), 1200);
    }
  }

  function allScratched() {
    return cards.every(function (c) { return !!state.scratched[c.cfg.id]; });
  }

  function updateDots() {
    var dots = $("#dots").children;
    cards.forEach(function (c, i) {
      var d = dots[i];
      if (!d) return;
      d.classList.toggle("is-scratched", !!state.scratched[c.cfg.id]);
      d.classList.toggle("is-claimed", !!state.claimed[c.cfg.id]);
      d.classList.toggle("is-current", i === currentIndex);
    });
  }

  /* ------------------------------------------------------------------------
     Listování
     ------------------------------------------------------------------------ */
  function goTo(i, instant) {
    var track = $("#track");
    i = Math.max(0, Math.min(cards.length - 1, i));
    var left = i * track.clientWidth;
    if (instant) {
      var prev = track.style.scrollBehavior;
      track.style.scrollBehavior = "auto";
      track.scrollLeft = left;
      track.style.scrollBehavior = prev;
    } else {
      try {
        track.scrollTo({ left: left, behavior: reducedMotion ? "auto" : "smooth" });
      } catch (e) {
        track.scrollLeft = left;
      }
    }
    setCurrent(i);
  }

  function setCurrent(i) {
    if (i === currentIndex && $("#card-count").textContent) {
      updateNav();
      return;
    }
    currentIndex = i;
    state.lastIndex = i;
    saveState();
    updateNav();
  }

  function updateNav() {
    $("#card-count").textContent = (currentIndex + 1) + " ze " + cards.length;
    $("#btn-prev").disabled = currentIndex === 0;
    $("#btn-next").disabled = currentIndex === cards.length - 1;
    updateDots();
  }

  var hintHidden = false;
  function hideHint() {
    if (hintHidden) return;
    hintHidden = true;
    var h = $("#swipe-hint");
    if (h) h.classList.add("is-hidden");
  }

  function setupNavigation() {
    var track = $("#track");
    var ticking = false;
    track.addEventListener("scroll", function () {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () {
        ticking = false;
        var w = track.clientWidth || 1;
        var i = Math.round(track.scrollLeft / w);
        i = Math.max(0, Math.min(cards.length - 1, i));
        if (i !== currentIndex) {
          setCurrent(i);
          if (i > 0) hideHint();
        }
      });
    }, { passive: true });

    $("#btn-prev").addEventListener("click", function () { goTo(currentIndex - 1); });
    $("#btn-next").addEventListener("click", function () { goTo(currentIndex + 1); });
    $("#btn-home").addEventListener("click", function () {
      $("#btn-open").textContent = "Pokračovat ke kartičkám";
      showScreen("intro");
    });
    $("#btn-summary").addEventListener("click", function () { showScreen("finale"); });
    $("#btn-back").addEventListener("click", function () { showScreen("cards"); });

    document.addEventListener("keydown", function (e) {
      if (!$("#screen-cards").classList.contains("is-active")) return;
      var tag = (e.target && e.target.tagName) || "";
      if (tag === "TEXTAREA" || tag === "INPUT") return;
      if (e.key === "ArrowRight") { e.preventDefault(); goTo(currentIndex + 1); }
      if (e.key === "ArrowLeft") { e.preventDefault(); goTo(currentIndex - 1); }
    });

    var resizeTimer = null;
    window.addEventListener("resize", function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        if (!$("#screen-cards").classList.contains("is-active")) return;
        cards.forEach(function (c) { if (c.scratcher) c.scratcher.resize(); });
        goTo(currentIndex, true);
      }, 150);
    });
  }

  /* ------------------------------------------------------------------------
     Přehled / závěr
     ------------------------------------------------------------------------ */
  function renderSummary() {
    var all = allScratched();
    var scratchedCount = cards.filter(function (c) { return state.scratched[c.cfg.id]; }).length;
    var claimedCount = cards.filter(function (c) { return state.claimed[c.cfg.id]; }).length;

    $("#finale-kicker").textContent = all ? "Všechno setřeno! 🎉" : "Přehled kartiček";
    $("#finale-title").textContent = all
      ? "Všechno nejlepší, " + (config.recipient || "") + "!"
      : (config.title || "Tvoje kartičky");
    $("#finale-lead").textContent = "Setřeno " + scratchedCount + " z " + cards.length +
      ", uplatněno " + claimedCount + "." + (config.validity ? " " + config.validity + "." : "");
    $("#finale-from").textContent = config.from || "";

    var list = $("#summary");
    list.textContent = "";
    cards.forEach(function (c, i) {
      var cfg = c.cfg;
      var li = document.createElement("li");
      if (cfg.main) li.className = "is-main";
      var scratched = !!state.scratched[cfg.id];
      var claimedAt = state.claimed[cfg.id];

      var pic = document.createElement("picture");
      var srcs = photoSources(cfg.photo);
      var so = document.createElement("source");
      so.type = "image/webp";
      so.srcset = srcs.webp;
      var im = document.createElement("img");
      im.src = srcs.jpg;
      im.alt = "";
      im.loading = "lazy";
      pic.appendChild(so);
      pic.appendChild(im);

      var text = document.createElement("div");
      var t = document.createElement("p");
      t.className = "summary__title";
      t.textContent = scratched ? cfg.title : (cfg.main ? "Hlavní výhra" : "Kartička " + (i + 1));
      var s = document.createElement("p");
      s.className = "summary__sub";
      if (scratched) {
        var sub = cfg.subtitle || "";
        if (cfg.type === "joker" && (state.wishes[cfg.id] || "").trim()) sub = "Přání: " + state.wishes[cfg.id].trim();
        s.textContent = sub;
      } else {
        s.textContent = "Ještě nesetřeno";
      }
      text.appendChild(t);
      text.appendChild(s);

      var st = document.createElement("span");
      st.className = "summary__status " + (claimedAt ? "st-claimed" : scratched ? "st-scratched" : "st-hidden");
      st.textContent = claimedAt ? "Uplatněno " + formatDate(claimedAt) : scratched ? "Setřeno" : "Čeká";

      text.appendChild(st);
      li.appendChild(pic);
      li.appendChild(text);
      li.addEventListener("click", function () {
        currentIndex = i;
        showScreen("cards");
      });
      list.appendChild(li);
    });
  }

  /* ------------------------------------------------------------------------
     Konfety
     ------------------------------------------------------------------------ */
  var confettiRunning = false;
  function confetti(duration) {
    if (reducedMotion || !duration) return;
    var canvas = $("#confetti");
    var ctx = canvas.getContext("2d");
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var W = window.innerWidth, H = window.innerHeight;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    var colors = ["#D4AF5A", "#B8923F", "#E7A9B5", "#F3ECF3", "#1F2547", "#FFF1C1"];
    var parts = [];
    var n = Math.min(220, Math.round(W / 2.2));
    for (var i = 0; i < n; i++) {
      parts.push({
        x: Math.random() * W,
        y: -20 - Math.random() * H * 0.6,
        w: 6 + Math.random() * 6,
        h: 8 + Math.random() * 10,
        vy: 2 + Math.random() * 3.5,
        vx: (Math.random() - 0.5) * 2,
        rot: Math.random() * Math.PI,
        vr: (Math.random() - 0.5) * 0.2,
        c: colors[i % colors.length],
        heart: Math.random() < 0.12
      });
    }
    var start = performance.now();
    if (confettiRunning) return;
    confettiRunning = true;

    function frame(now) {
      var t = now - start;
      ctx.clearRect(0, 0, W, H);
      var alive = false;
      parts.forEach(function (p) {
        p.x += p.vx + Math.sin((t + p.y) / 400) * 0.6;
        p.y += p.vy;
        p.rot += p.vr;
        if (p.y > H + 30) {
          if (t < duration) { p.y = -20; p.x = Math.random() * W; } else return;
        }
        alive = true;
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rot);
        ctx.fillStyle = p.c;
        if (p.heart) {
          ctx.font = "16px sans-serif";
          ctx.fillText("💛", -8, 8);
        } else {
          ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h * Math.abs(Math.cos(p.rot * 2)) + 2);
        }
        ctx.restore();
      });
      if (alive) {
        requestAnimationFrame(frame);
      } else {
        ctx.clearRect(0, 0, W, H);
        confettiRunning = false;
      }
    }
    requestAnimationFrame(frame);
  }

  /* ------------------------------------------------------------------------
     Start
     ------------------------------------------------------------------------ */
  function fail(msg) {
    var app = $("#app");
    app.innerHTML = "";
    var p = document.createElement("p");
    p.className = "noscript";
    p.textContent = msg;
    app.appendChild(p);
  }

  function start(cfg) {
    config = cfg;
    if (!config || !Array.isArray(config.cards) || !config.cards.length) {
      fail("V konfiguraci chybí kartičky.");
      return;
    }
    var wasReset = handleResetParam();
    state = loadState();

    setupIntro();
    buildCards();
    setupNavigation();

    currentIndex = Math.max(0, Math.min(cards.length - 1, state.lastIndex || 0));
    updateNav();
    showScreen("intro");

    // Text „Setři“ na plátně potřebuje načtené písmo – po načtení překresli.
    try {
      if (document.fonts && document.fonts.load) {
        Promise.all([
          document.fonts.load('40px "Yeseva One"'),
          document.fonts.load('600 14px "Poppins"')
        ]).then(function () {
          cards.forEach(function (c) { if (c.scratcher && c.scratcher.w) c.scratcher.resize(true); });
        }).catch(function () { /* ignore */ });
      }
    } catch (e) { /* ignore */ }

    if (wasReset) toast("Stav byl vymazán – vše je jako nové ✨", 3000);
  }

  function boot() {
    try {
      fetch("config.json", { cache: "no-cache" })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (cfg) {
          try { start(cfg); } catch (e) {
            console.error(e);
            fail("Něco se pokazilo při zobrazení dárku. Zkus stránku načíst znovu.");
          }
        })
        .catch(function (e) {
          console.error(e);
          fail("Nepodařilo se načíst dárek (config.json). Zkus stránku načíst znovu.");
        });
    } catch (e) {
      fail("Tento prohlížeč je bohužel příliš starý.");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
