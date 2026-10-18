(function () {
  var preference = "system";
  try { preference = localStorage.getItem("interviewos.theme") || "system"; } catch (_) { /* Storage is optional. */ }
  if (preference !== "light" && preference !== "dark") preference = "system";
  document.documentElement.dataset.theme = preference === "system" ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : preference;
  document.documentElement.dataset.themePreference = preference;
})();
