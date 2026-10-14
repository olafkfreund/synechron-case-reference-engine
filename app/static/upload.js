// Upload page: reload every 15 seconds while files are processing, but never while a file is chosen. The page works without it.
(function () {
  setInterval(function () {
    var f = document.getElementById("up-file");
    if (!(f && f.files && f.files.length)) location.reload();
  }, 15000);
})();
