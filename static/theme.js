// Light, dark or follow the device: the choice is kept in this browser (localStorage) and applied as
// data-theme on <html>, which static/theme.css reads. Loaded in <head> so there is no flash.
(function (global) {
  const KEY = "webplotter-theme";
  const CHOICES = ["light", "dark", "auto"];

  // The theme to show: "light" or "dark"
  function resolve(choice, prefersDark) {
    if (choice === "dark") return "dark";
    if (choice === "auto") return prefersDark ? "dark" : "light";
    return "light";
  }

  function stored() {
    try {
      const value = global.localStorage.getItem(KEY);
      return CHOICES.indexOf(value) >= 0 ? value : "light";
    } catch (e) {
      return "light";             // storage can be blocked (private windows)
    }
  }

  function prefersDark() {
    return !!(global.matchMedia && global.matchMedia("(prefers-color-scheme: dark)").matches);
  }

  function apply(choice) {
    if (global.document) global.document.documentElement.setAttribute("data-theme", resolve(choice, prefersDark()));
  }

  function save(choice) {
    if (CHOICES.indexOf(choice) < 0) return;
    try {
      global.localStorage.setItem(KEY, choice);
    } catch (e) { /* the choice then only lasts until the page is reloaded */ }
    apply(choice);
  }

  const theme = { resolve: resolve, stored: stored, apply: apply, save: save, CHOICES: CHOICES };
  global.WebPlotterTheme = theme;
  if (typeof module !== "undefined" && module.exports) module.exports = theme;

  apply(stored());
  if (global.matchMedia) {
    const query = global.matchMedia("(prefers-color-scheme: dark)");
    const follow = function () { if (stored() === "auto") apply("auto"); };
    if (query.addEventListener) query.addEventListener("change", follow);
  }
})(typeof window !== "undefined" ? window : globalThis);
