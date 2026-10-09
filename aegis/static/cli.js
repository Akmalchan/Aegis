/* CLI section: copy button + replay the terminal when it scrolls into view. */
(function () {
  var copy = document.getElementById("cliCopy"), cmd = document.getElementById("cliCmd");
  if (copy && cmd) copy.addEventListener("click", function () {
    var done = function () { copy.textContent = "Copied"; setTimeout(function () { copy.textContent = "Copy"; }, 1400); };
    if (navigator.clipboard) navigator.clipboard.writeText(cmd.textContent).then(done, done); else done();
  });
  var sec = document.getElementById("cli");
  if (!sec || !("IntersectionObserver" in window) || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  sec.classList.add("anim");
  var lines = sec.querySelectorAll(".t-l");
  lines.forEach(function (l, i) { l.style.animationDelay = (i === 0 ? 0 : 0.5 + i * 0.12) + "s"; });
  new IntersectionObserver(function (es, obs) {
    es.forEach(function (e) { if (e.isIntersecting) { sec.classList.add("play"); obs.disconnect(); } });
  }, { threshold: 0.35 }).observe(sec);
})();
