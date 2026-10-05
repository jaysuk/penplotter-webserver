// Puts the panels where the saved layout says (static/layout.js) and lets the user change it: drag a panel by
// its handle into any column, move it with the arrow keys, fold it up, hide it (the chips bar brings it back),
// and drag the dividers to change the width of the side columns.
// It runs right after the columns in index.html so the layout is in place before the page is first drawn;
// dragging is switched on once UIkit has loaded.
(function () {
  const Layout = window.WebPlotterLayout;
  const container = document.getElementById("cols");
  if (!Layout || !container) return;

  const columns = {};
  Layout.COLUMNS.forEach(function (name) { columns[name] = container.querySelector('[data-col="' + name + '"]'); });
  const panels = {};
  container.querySelectorAll("[data-panel]").forEach(function (el) { panels[el.dataset.panel] = el; });
  const shelf = document.getElementById("panelShelf");
  // A hidden panel stays in the page (main.js keeps updating it) but out of its column, so that a column with
  // nothing to see is really empty and a panel can be dropped into it
  const park = document.createElement("div");
  park.hidden = true;
  container.insertAdjacentElement("afterend", park);
  const narrow = window.matchMedia("(max-width: 860px)");

  let layout = Layout.load();

  const icon = function (name, size) {
    return '<svg class="ico" width="' + size + '" height="' + size + '" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><use href="#' + name + '"/></svg>';
  };
  const TOOL_ICONS = {
    grip: '<svg class="ico" width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/></svg>',
    fold: '<svg class="ico" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 15 6-6 6 6"/></svg>',
    unfold: '<svg class="ico" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>',
    hide: '<svg class="ico" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  };
  const title = function (id) { return panels[id].querySelector(".cs-phead h2").textContent.trim(); };

  // A handle, a fold button and a hide button in each panel's title bar
  Object.keys(panels).forEach(function (id) {
    const head = panels[id].querySelector(".cs-phead");
    const name = title(id);
    head.insertAdjacentHTML("afterbegin", '<button type="button" class="cs-grip" aria-label="Move ' + name + '. Arrow keys move it, and left and right change column." title="Drag to move">' + TOOL_ICONS.grip + "</button>");
    head.insertAdjacentHTML("beforeend",
      '<button type="button" class="cs-tool cs-fold" data-fold title="Fold up" aria-label="Fold up ' + name + '">' + TOOL_ICONS.fold + "</button>" +
      '<button type="button" class="cs-tool cs-hide" data-hide title="Hide" aria-label="Hide ' + name + '">' + TOOL_ICONS.hide + "</button>");
  });

  function commit(next) {
    layout = next;
    Layout.save(layout);
    apply();
    // Let the page know, so a panel that has just come back can fill itself in
    window.dispatchEvent(new Event("panelschanged"));
  }

  // Bring a panel back if it is hidden or folded up, and make sure it is on the screen
  window.WebPlotterLayoutUI = {
    reveal: function (id) {
      if (!panels[id]) return;
      if (layout.hidden.indexOf(id) >= 0 || layout.folded[id] === true) {
        commit(Layout.setFolded(Layout.setHidden(layout, id, false), id, false));
      }
      panels[id].scrollIntoView({ behavior: "smooth", block: "nearest" });
    },
  };

  // The order in the page right now
  function fromPage() {
    const cols = {};
    Layout.COLUMNS.forEach(function (name) {
      cols[name] = Array.prototype.map.call(columns[name].children, function (el) { return el.dataset.panel; }).filter(Boolean);
    });
    return Layout.withVisible(layout, cols);
  }

  function apply() {
    layout.hidden.forEach(function (id) { park.appendChild(panels[id]); });
    // Only move what is not already in place: moving a panel in the page resets its scroll position
    Layout.COLUMNS.forEach(function (name) {
      Layout.visible(layout, name).forEach(function (id, i) {
        if (columns[name].children[i] !== panels[id]) columns[name].insertBefore(panels[id], columns[name].children[i] || null);
      });
    });
    Object.keys(panels).forEach(function (id) {
      const el = panels[id];
      const folded = layout.folded[id] === true;
      el.classList.toggle("is-folded", folded);
      const button = el.querySelector("[data-fold]");
      button.innerHTML = folded ? TOOL_ICONS.unfold : TOOL_ICONS.fold;
      button.title = folded ? "Unfold" : "Fold up";
      button.setAttribute("aria-label", (folded ? "Unfold " : "Fold up ") + title(id));
      button.setAttribute("aria-expanded", String(!folded));
    });
    container.style.setProperty("--lw", layout.lw + "px");
    container.style.setProperty("--rw", layout.rw + "px");
    renderShelf();
  }

  function renderShelf() {
    if (!shelf) return;
    // A fixed order, so the chips do not move about as the panels do
    shelf.innerHTML = '<span class="cs-small">Panels</span>' + Layout.PANELS.map(function (id) {
      const shown = layout.hidden.indexOf(id) < 0;
      return '<button type="button" class="cs-chip" data-chip="' + id + '" aria-pressed="' + shown + '">' + icon(panels[id].querySelector("use").getAttribute("href").slice(1), 13) + title(id) + "</button>";
    }).join("") + '<span class="cs-sp"></span><button type="button" class="uk-button uk-button-default uk-button-small resetLayout" title="Put the panels back where they started">Reset layout</button>';
  }

  container.addEventListener("click", function (e) {
    const panel = e.target.closest("[data-panel]");
    if (!panel) return;
    const id = panel.dataset.panel;
    if (e.target.closest("[data-hide]")) commit(Layout.setHidden(layout, id, true));
    else if (e.target.closest("[data-fold]")) commit(Layout.setFolded(layout, id, layout.folded[id] !== true));
  });

  if (shelf) {
    shelf.addEventListener("click", function (e) {
      const chip = e.target.closest("[data-chip]");
      if (!chip) return;
      const id = chip.dataset.chip;
      const nowHidden = layout.hidden.indexOf(id) < 0;
      // A panel that comes back is unfolded, so something happens when its chip is pressed
      commit(Layout.setFolded(Layout.setHidden(layout, id, nowHidden), id, nowHidden ? layout.folded[id] === true : false));
    });
  }

  // "Reset layout", in the chips bar and in the settings dialog
  document.addEventListener("click", function (e) {
    if (!e.target.closest(".resetLayout")) return;
    e.preventDefault();
    Layout.forget();
    layout = Layout.defaults();
    apply();
  });

  // Keyboard: the handle takes the arrow keys
  container.addEventListener("keydown", function (e) {
    const grip = e.target.closest(".cs-grip");
    const direction = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right" }[e.key];
    if (!grip || !direction || narrow.matches) return;
    e.preventDefault();
    const id = grip.closest("[data-panel]").dataset.panel;
    commit(Layout.shift(layout, id, direction));
    panels[id].querySelector(".cs-grip").focus();
  });

  // Dividers: drag, or arrow keys when one has the focus
  container.querySelectorAll("[data-split]").forEach(function (bar) {
    const side = bar.dataset.split;
    const sign = side === "lw" ? 1 : -1;
    let start = null;
    bar.addEventListener("pointerdown", function (e) {
      start = { x: e.clientX, width: layout[side], id: e.pointerId };
      bar.setPointerCapture(e.pointerId);
      bar.classList.add("is-dragging");
      e.preventDefault();
    });
    bar.addEventListener("pointermove", function (e) {
      if (!start || e.pointerId !== start.id) return;
      layout = Layout.setWidth(layout, side, start.width + sign * (e.clientX - start.x));
      container.style.setProperty("--" + side, layout[side] + "px");
    });
    const stop = function () {
      if (!start) return;
      start = null;
      bar.classList.remove("is-dragging");
      Layout.save(layout);
    };
    bar.addEventListener("pointerup", stop);
    bar.addEventListener("pointercancel", stop);
    bar.addEventListener("keydown", function (e) {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      commit(Layout.setWidth(layout, side, layout[side] + sign * (e.key === "ArrowRight" ? 20 : -20)));
    });
  });

  // Dragging a panel between columns: UIkit's sortable, one list per column, one group
  function enableDragging() {
    if (!window.UIkit || !UIkit.sortable) return;
    Layout.COLUMNS.forEach(function (name) {
      UIkit.sortable(columns[name], { group: "panels", handle: ".cs-grip", animation: 150, threshold: 6 });
      UIkit.util.on(columns[name], "stop", function () {
        // UIkit puts the panel in its place just after the event
        setTimeout(function () { commit(fromPage()); }, 0);
      });
    });
  }

  apply();
  document.addEventListener("DOMContentLoaded", enableDragging);
})();
