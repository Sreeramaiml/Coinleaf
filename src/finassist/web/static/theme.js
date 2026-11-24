// Applies the saved theme before the page paints (loaded in <head>, not deferred).
"use strict";
try {
  var saved = localStorage.getItem("finassist-theme");
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
} catch (e) { /* storage blocked: follow the system setting */ }
