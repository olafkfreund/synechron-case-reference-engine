// Upload page: reload every 15 seconds while files are processing, but not while a file is chosen or focus is in a control. The page works without it.
(function () {
  setInterval(function () {
    var f = document.getElementById("up-file"), a = document.activeElement;
    if ((f && f.files && f.files.length) || (a && a !== document.body && a !== document.documentElement)) return;
    location.replace(location.pathname);  // the query (a refusal notice) is not repeated
  }, 15000);
})();
