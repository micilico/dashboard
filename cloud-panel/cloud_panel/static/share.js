/*
 * Public folder-share downloader.
 *
 * Chromium's File System Access API is used when available: the visitor picks
 * a parent directory once and the script recreates the shared tree inside a new
 * folder of the same name. No archive is built on the server and no temporary
 * file is stored there: every file is streamed from the rclone mount straight
 * to the chosen directory.
 *
 * Browsers without `showDirectoryPicker` fall back to a sequential cascade of
 * navigation downloads. That fallback is explicitly documented in the page:
 * files land flat in the browser's download folder and subfolders are lost.
 */
(function (global) {
  "use strict";

  var UPDATE_INTERVAL_MS = 200;
  var CASCADE_DELAY_MS = 350;
  var MAX_CONSECUTIVE_FAILURES = 20;

  function formatBytes(value) {
    var bytes = Number(value) || 0;
    if (bytes <= 0) return "0 o";
    var units = ["o", "Ko", "Mo", "Go", "To", "Po"];
    var index = Math.min(
      Math.floor(Math.log(bytes) / Math.log(1024)),
      units.length - 1
    );
    var amount = bytes / Math.pow(1024, index);
    return amount.toFixed(index === 0 ? 0 : 1).replace(".", ",") + " " + units[index];
  }

  function folderNameFromPath(path) {
    var parts = String(path || "").split("/").filter(Boolean);
    return parts.length ? parts[parts.length - 1] : "";
  }

  function sanitizeEntryName(name) {
    var value = String(name || "");
    if (!value || value === "." || value === "..") return "";
    if (value.indexOf("/") !== -1 || value.indexOf("\\") !== -1 || value.indexOf("\0") !== -1) {
      return "";
    }
    return value;
  }

  function buildShareUrl(baseUrl, params) {
    var url = new URL(baseUrl);
    url.search = "";
    url.hash = "";
    var search = new URLSearchParams();
    Object.keys(params || {}).forEach(function (key) {
      var value = params[key];
      if (value === undefined || value === null || value === "") return;
      search.set(key, String(value));
    });
    var query = search.toString();
    if (query) url.search = query;
    return url.toString();
  }

  function joinRelPath(prefix, name) {
    return prefix ? prefix + "/" + name : name;
  }

  function parentOf(relativePath) {
    var index = relativePath.lastIndexOf("/");
    return index === -1 ? "" : relativePath.slice(0, index);
  }

  function shareBaseUrl() {
    var url = new URL(global.location.href);
    url.search = "";
    url.hash = "";
    return url.toString();
  }

  function httpError(response) {
    if (response.status === 401 || response.status === 403) {
      var error = new Error(
        "L'accès à ce partage a expiré. Rechargez la page puis relancez le téléchargement."
      );
      error.sessionExpired = true;
      return error;
    }
    if (response.status === 404) {
      return new Error("Le contenu n'existe plus sur le partage.");
    }
    return new Error("Le serveur a refusé la demande (" + response.status + ").");
  }

  function normalizedEntryKey(name) {
    return String(name || "").normalize("NFC").toLowerCase();
  }

  async function fetchListing(base, path, first, signal) {
    var url = buildShareUrl(base, { path: path, list: 1, count: first ? 1 : 0 });
    var response = await fetch(url, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      signal: signal,
    });
    if (!response.ok) throw httpError(response);
    var type = response.headers.get("content-type") || "";
    if (type.indexOf("application/json") === -1) {
      throw new Error("Réponse inattendue du serveur. Rechargez la page.");
    }
    return response.json();
  }

  async function collectPlan(base, rootPath, signal, onProgress) {
    var files = [];
    var dirs = [];
    var skipped = [];
    var totalBytes = 0;
    var listed = 0;

    async function walk(currentPath, prefix, first) {
      var data = await fetchListing(base, currentPath, first, signal);
      listed += 1;
      var entries = Array.isArray(data.items) ? data.items : [];
      // APFS/NTFS are case-insensitive and may normalise Unicode: two distinct
      // source names can map to the same destination entry. Detect that here,
      // before writing, instead of silently overwriting one of them.
      var seenNames = new Set();
      for (var index = 0; index < entries.length; index += 1) {
        var item = entries[index];
        var rawName = String(item.name || "");
        var relative = joinRelPath(prefix, rawName);
        if (item.is_symlink) {
          skipped.push({ relative: relative, reason: "lien symbolique ignoré" });
          continue;
        }
        var name = sanitizeEntryName(rawName);
        if (!name) {
          skipped.push({ relative: relative, reason: "nom non pris en charge" });
          continue;
        }
        var key = normalizedEntryKey(name);
        if (seenNames.has(key)) {
          skipped.push({ relative: relative, reason: "conflit de casse ou d'Unicode" });
          continue;
        }
        seenNames.add(key);
        relative = joinRelPath(prefix, name);
        if (item.is_dir) {
          dirs.push(relative);
          await walk(item.path, relative, false);
        } else {
          var size = Number(item.size_bytes) || 0;
          files.push({ path: item.path, name: name, relative: relative, size: size });
          totalBytes += size;
        }
      }
      if (onProgress) {
        onProgress({
          dirs: listed,
          files: files.length,
          bytes: totalBytes,
          skipped: skipped.length,
        });
      }
    }

    await walk(rootPath, "", true);
    return {
      files: files,
      dirs: dirs,
      totalBytes: totalBytes,
      skipped: skipped,
    };
  }

  function createDirResolver(rootHandle) {
    var cache = new Map();
    return async function ensureDir(relativePath) {
      if (!relativePath) return rootHandle;
      if (cache.has(relativePath)) return cache.get(relativePath);
      var handle = rootHandle;
      var parts = relativePath.split("/");
      var walked = "";
      for (var index = 0; index < parts.length; index += 1) {
        var name = sanitizeEntryName(parts[index]);
        if (!name) throw new Error("Nom de dossier invalide.");
        walked = joinRelPath(walked, name);
        if (cache.has(walked)) {
          handle = cache.get(walked);
          continue;
        }
        handle = await handle.getDirectoryHandle(name, { create: true });
        cache.set(walked, handle);
      }
      return handle;
    };
  }

  async function pickDestination(folderName) {
    if (typeof global.showDirectoryPicker !== "function") {
      throw new Error("Ce navigateur ne permet pas d'enregistrer un dossier.");
    }
    var parent;
    try {
      parent = await global.showDirectoryPicker({
        id: "cloud-share-download",
        mode: "readwrite",
        startIn: "downloads",
      });
    } catch (error) {
      if (error && error.name === "AbortError") error.pickerCancelled = true;
      throw error;
    }

    // The picker remembers the previous parent (id). After an interruption the
    // original folder already exists: reuse it as a suffix instead of refusing,
    // otherwise the documented "reload and retry" advice could never work.
    for (var attempt = 1; attempt <= 99; attempt += 1) {
      var candidate = attempt === 1 ? folderName : folderName + " (" + attempt + ")";
      var exists = false;
      try {
        await parent.getDirectoryHandle(candidate);
        exists = true;
      } catch (error) {
        if (error && error.name === "TypeMismatchError") {
          exists = true;
        } else if (!error || error.name !== "NotFoundError") {
          throw error;
        }
      }
      if (!exists) {
        var handle = await parent.getDirectoryHandle(candidate, { create: true });
        return { handle: handle, name: candidate };
      }
    }
    throw new Error("Impossible de créer un dossier de destination (trop de doublons).");
  }

  function abortError() {
    try {
      return new DOMException("Aborted", "AbortError");
    } catch (_error) {
      var error = new Error("Aborted");
      error.name = "AbortError";
      return error;
    }
  }

  function delay(milliseconds, signal) {
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(resolve, milliseconds);
      if (!signal) return;
      if (signal.aborted) {
        clearTimeout(timer);
        reject(abortError());
        return;
      }
      signal.addEventListener(
        "abort",
        function () {
          clearTimeout(timer);
          reject(abortError());
        },
        { once: true }
      );
    });
  }

  function createUi() {
    var panel = document.getElementById("downloadPanel");
    var status = document.getElementById("downloadStatus");
    var detail = document.getElementById("downloadDetail");
    var bar = document.getElementById("downloadBar");
    var fill = document.getElementById("downloadBarFill");
    var error = document.getElementById("downloadError");
    var cancel = document.getElementById("downloadCancel");
    if (!panel || !status || !detail || !bar || !fill || !error || !cancel) return null;

    var ui = {
      panel: panel,
      cancel: cancel,
      show: function () {
        panel.hidden = false;
      },
      hide: function () {
        panel.hidden = true;
      },
      reset: function () {
        panel.hidden = false;
        error.hidden = true;
        error.replaceChildren();
        cancel.hidden = false;
        cancel.disabled = false;
        cancel.textContent = "Annuler";
        ui.setPercent(0);
        status.textContent = "Préparation…";
        detail.textContent = "";
      },
      setStatus: function (text) {
        status.textContent = text;
      },
      setDetail: function (text) {
        detail.textContent = text || "";
      },
      setPercent: function (value) {
        var percent = Math.max(0, Math.min(100, Number(value) || 0));
        fill.style.width = percent + "%";
        bar.setAttribute("aria-valuenow", String(Math.round(percent)));
        bar.setAttribute("aria-valuetext", Math.round(percent) + " %");
      },
      finish: function (title, text) {
        status.textContent = title;
        detail.textContent = text || "";
        ui.setPercent(100);
        cancel.hidden = true;
        cancel.disabled = true;
      },
      fail: function (message) {
        status.textContent = "Téléchargement impossible";
        detail.textContent = "";
        error.hidden = false;
        error.textContent = message;
        cancel.hidden = true;
      },
      showFailures: function (failures, title) {
        error.hidden = false;
        error.replaceChildren();
        var intro = document.createElement("p");
        intro.textContent =
          title || failures.length + " fichier(s) n'ont pas pu être téléchargés :";
        var list = document.createElement("ul");
        failures.slice(0, 8).forEach(function (failure) {
          var item = document.createElement("li");
          item.textContent = failure.relative + " — " + failure.message;
          list.appendChild(item);
        });
        error.append(intro, list);
        if (failures.length > 8) {
          var more = document.createElement("p");
          more.textContent = "… et " + (failures.length - 8) + " autre(s).";
          error.appendChild(more);
        }
      },
    };
    return ui;
  }

  function setTriggersDisabled(disabled) {
    document.querySelectorAll(".js-download-folder").forEach(function (button) {
      button.disabled = disabled;
    });
    var cascade = document.getElementById("downloadCascade");
    if (cascade) cascade.disabled = disabled;
  }

  function warnBeforeUnload(event) {
    event.preventDefault();
    event.returnValue = "";
    return "";
  }

  function beginRun(ui) {
    ui.reset();
    ui.show();
    setTriggersDisabled(true);
    global.addEventListener("beforeunload", warnBeforeUnload);
    try {
      ui.cancel.focus();
    } catch (_error) {
      /* focus is best effort */
    }
  }

  function endRun(state) {
    setTriggersDisabled(false);
    global.removeEventListener("beforeunload", warnBeforeUnload);
    var trigger = state && state.lastTrigger;
    if (trigger && document.contains(trigger)) {
      try {
        trigger.focus();
      } catch (_error) {
        /* focus is best effort */
      }
    }
  }

  async function runDirectoryDownload(trigger, ui, state) {
    var folderPath = trigger.getAttribute("data-folder-path") || "";
    var folderName =
      sanitizeEntryName(trigger.getAttribute("data-folder-name") || folderNameFromPath(folderPath)) ||
      "Dossier";
    var base = shareBaseUrl();
    var controller = new AbortController();
    state.controller = controller;
    state.running = true;
    state.lastTrigger = trigger;
    beginRun(ui);

    try {
      ui.setStatus("Choisissez un dossier de destination…");
      var picked = await pickDestination(folderName);
      if (controller.signal.aborted) throw abortError();
      var rootHandle = picked.handle;
      var destinationName = picked.name;

      ui.setStatus("Analyse du dossier…");
      var plan = await collectPlan(base, folderPath, controller.signal, function (progress) {
        ui.setStatus(
          "Analyse du dossier… " + progress.dirs + " dossier(s), " + progress.files +
            " fichier(s)" + (progress.skipped ? ", " + progress.skipped + " ignoré(s)" : "")
        );
      });
      if (controller.signal.aborted) throw abortError();

      var ensureDir = createDirResolver(rootHandle);
      for (var d = 0; d < plan.dirs.length; d += 1) {
        await ensureDir(plan.dirs[d]);
        if (controller.signal.aborted) throw abortError();
      }

      var skippedIssues = plan.skipped.map(function (entry) {
        return { relative: entry.relative, message: entry.reason };
      });

      if (!plan.files.length) {
        ui.finish(
          "Aucun fichier à télécharger",
          "Le dossier « " + destinationName + " » a été créé" +
            (skippedIssues.length ? ", " + skippedIssues.length + " élément(s) ignoré(s)." : ".")
        );
        if (skippedIssues.length) {
          ui.showFailures(skippedIssues, skippedIssues.length + " élément(s) ignoré(s) :");
        }
        return;
      }

      var totalFiles = plan.files.length;
      var completed = 0;
      var loadedBytes = 0;
      var currentSize = 0;
      var currentLoaded = 0;
      var lastPaint = 0;
      var failures = [];
      var sessionExpired = false;
      var abortedByFailures = false;
      var consecutiveFailures = 0;

      function paint() {
        var percent;
        if (plan.totalBytes > 0) {
          // A single 20 GB movie must move the bar during the transfer, not
          // jump from 0 to 100 once the file count flips.
          percent = (loadedBytes / plan.totalBytes) * 100;
        } else {
          var fraction = currentSize > 0 ? Math.min(1, currentLoaded / currentSize) : 0;
          percent = ((completed + fraction) / totalFiles) * 100;
        }
        ui.setPercent(Math.min(100, Math.max(0, percent)));
        ui.setDetail(
          completed + " / " + totalFiles + " fichiers · " +
            formatBytes(loadedBytes) + " / " + formatBytes(plan.totalBytes) +
            (skippedIssues.length ? " · " + skippedIssues.length + " ignoré(s)" : "")
        );
      }

      function paintThrottled() {
        var now = performance.now();
        if (now - lastPaint < UPDATE_INTERVAL_MS) return;
        lastPaint = now;
        paint();
      }

      for (var index = 0; index < plan.files.length; index += 1) {
        if (controller.signal.aborted) break;
        var file = plan.files[index];
        ui.setStatus("Téléchargement · " + completed + " / " + totalFiles + " fichiers");
        currentSize = file.size;
        currentLoaded = 0;

        var parentHandle = null;
        var fileCreated = false;
        try {
          parentHandle = await ensureDir(parentOf(file.relative));
          var response = await fetch(
            buildShareUrl(base, { path: file.path, dl: 1, count: 0 }),
            { credentials: "same-origin", signal: controller.signal }
          );
          if (!response.ok) throw httpError(response);
          if (!response.body) throw new Error("Flux de téléchargement indisponible.");

          // Create the destination entry only once the server has answered:
          // an HTTP/network failure must not leave a 0-byte file behind.
          var fileHandle = await parentHandle.getFileHandle(file.name, { create: true });
          fileCreated = true;
          var writable = await fileHandle.createWritable();
          var counter = new TransformStream({
            transform: function (chunk, control) {
              currentLoaded += chunk.byteLength;
              loadedBytes += chunk.byteLength;
              paintThrottled();
              control.enqueue(chunk);
            },
          });
          try {
            await response.body.pipeThrough(counter).pipeTo(writable);
          } catch (error) {
            try {
              await writable.abort();
            } catch (_abortError) {
              /* the writable may already be closed */
            }
            throw error;
          }
          consecutiveFailures = 0;
        } catch (error) {
          if (fileCreated && parentHandle) {
            try {
              await parentHandle.removeEntry(file.name);
            } catch (_removeError) {
              /* best effort: keep the original error */
            }
          }
          if (controller.signal.aborted) break;
          if (error && error.sessionExpired) {
            sessionExpired = true;
            controller.abort();
            break;
          }
          consecutiveFailures += 1;
          failures.push({
            relative: file.relative,
            message: error && error.message ? error.message : "Erreur inconnue",
          });
          if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
            abortedByFailures = true;
            controller.abort();
            break;
          }
        } finally {
          completed += 1;
          currentLoaded = currentSize;
          paint();
        }
      }

      if (sessionExpired) {
        ui.fail(
          "L'accès à ce partage a expiré. Rechargez la page puis relancez le téléchargement."
        );
        return;
      }

      var issues = failures.concat(skippedIssues);

      if (abortedByFailures) {
        ui.finish(
          "Téléchargement interrompu",
          "Trop d'échecs consécutifs (" + MAX_CONSECUTIVE_FAILURES + ")."
        );
        if (issues.length) {
          ui.showFailures(issues, issues.length + " élément(s) non téléchargé(s) :");
        }
        return;
      }

      if (controller.signal.aborted) {
        ui.finish(
          "Téléchargement annulé",
          completed + " fichier(s) traité(s) avant l'annulation."
        );
        if (issues.length) {
          ui.showFailures(issues, issues.length + " élément(s) non téléchargé(s) :");
        }
        return;
      }

      if (issues.length) {
        ui.finish(
          "Terminé avec " + issues.length + " élément(s) non téléchargé(s)",
          (totalFiles - failures.length) + " / " + totalFiles +
            " fichiers téléchargés · " + formatBytes(loadedBytes)
        );
        ui.showFailures(issues, issues.length + " élément(s) non téléchargé(s) :");
      } else {
        ui.finish(
          "Téléchargement terminé",
          totalFiles + " fichiers · " + formatBytes(loadedBytes) +
            " dans « " + destinationName + " »"
        );
      }
    } catch (error) {
      if (error && error.pickerCancelled) {
        ui.hide();
        return;
      }
      if (controller.signal.aborted) {
        ui.finish("Téléchargement annulé", "");
        return;
      }
      if (error && error.name === "NotAllowedError") {
        ui.fail("L'accès au dossier de destination a été refusé.");
        return;
      }
      if (error && error.name === "SecurityError") {
        ui.fail("Ce navigateur ne permet pas d'écrire dans un dossier.");
        return;
      }
      ui.fail(error && error.message ? error.message : "Le téléchargement a échoué.");
    } finally {
      state.running = false;
      state.controller = null;
      endRun(state);
    }
  }

  async function runCascade(trigger, ui, state) {
    var folderPath = trigger.getAttribute("data-folder-path") || "";
    var base = shareBaseUrl();
    var controller = new AbortController();
    state.controller = controller;
    state.running = true;
    state.lastTrigger = trigger;
    beginRun(ui);

    try {
      ui.setStatus("Analyse du dossier…");
      var plan = await collectPlan(base, folderPath, controller.signal, function (progress) {
        ui.setStatus(
          "Analyse du dossier… " + progress.files + " fichier(s)" +
            (progress.skipped ? ", " + progress.skipped + " ignoré(s)" : "")
        );
      });

      var skippedIssues = plan.skipped.map(function (entry) {
        return { relative: entry.relative, message: entry.reason };
      });

      if (!plan.files.length) {
        ui.finish(
          "Aucun fichier à télécharger",
          skippedIssues.length
            ? skippedIssues.length + " élément(s) ignoré(s)."
            : "Aucun fichier à télécharger."
        );
        if (skippedIssues.length) {
          ui.showFailures(skippedIssues, skippedIssues.length + " élément(s) ignoré(s) :");
        }
        return;
      }

      ui.setStatus("Préparation de " + plan.files.length + " téléchargements…");
      ui.setDetail("Autorisez les téléchargements multiples si le navigateur le demande.");

      for (var index = 0; index < plan.files.length; index += 1) {
        if (controller.signal.aborted) break;
        var file = plan.files[index];
        var link = document.createElement("a");
        link.href = buildShareUrl(base, { path: file.path, dl: 1, count: 0 });
        link.download = file.name;
        link.rel = "noopener";
        link.style.display = "none";
        document.body.appendChild(link);
        link.click();
        link.remove();
        ui.setStatus("Téléchargements lancés · " + (index + 1) + " / " + plan.files.length);
        ui.setPercent(((index + 1) / plan.files.length) * 100);
        await delay(CASCADE_DELAY_MS, controller.signal);
      }

      if (controller.signal.aborted) {
        ui.finish("Téléchargement annulé", "");
        return;
      }
      ui.finish(
        plan.files.length + " téléchargements lancés",
        "Les fichiers arrivent à plat dans le dossier de téléchargements du navigateur. " +
          "Si certains manquent, autorisez les téléchargements multiples pour ce site puis relancez." +
          (skippedIssues.length ? " " + skippedIssues.length + " élément(s) ignoré(s)." : "")
      );
      if (skippedIssues.length) {
        ui.showFailures(skippedIssues, skippedIssues.length + " élément(s) ignoré(s) :");
      }
    } catch (error) {
      if (controller.signal.aborted) {
        ui.finish("Téléchargement annulé", "");
        return;
      }
      ui.fail(error && error.message ? error.message : "Le téléchargement a échoué.");
    } finally {
      state.running = false;
      state.controller = null;
      endRun(state);
    }
  }

  function init() {
    var triggers = Array.prototype.slice.call(
      document.querySelectorAll(".js-download-folder")
    );
    if (!triggers.length) return;
    var ui = createUi();
    if (!ui) return;

    var state = { running: false, controller: null, pendingTrigger: null, lastTrigger: null };
    var supported = typeof global.showDirectoryPicker === "function";
    var fallback = document.getElementById("downloadFallback");
    var cascadeButton = document.getElementById("downloadCascade");

    if (fallback) fallback.hidden = false;
    if (!supported && fallback) fallback.open = true;

    triggers.forEach(function (trigger) {
      trigger.addEventListener("click", function () {
        if (state.running) return;
        state.pendingTrigger = trigger;
        state.lastTrigger = trigger;
        if (supported) {
          runDirectoryDownload(trigger, ui, state);
          return;
        }
        if (fallback) {
          fallback.open = true;
          fallback.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
        ui.reset();
        ui.show();
        ui.cancel.hidden = true;
        ui.setStatus("Ce navigateur ne peut pas enregistrer un dossier complet.");
        ui.setDetail(
          "Utilisez « Télécharger les fichiers un par un » ci-dessous. " +
            "Les fichiers seront téléchargés à plat, sans les sous-dossiers."
        );
        if (cascadeButton) {
          try {
            cascadeButton.focus();
          } catch (_error) {
            /* focus is best effort */
          }
        }
      });
    });

    if (cascadeButton) {
      cascadeButton.addEventListener("click", function () {
        if (state.running) return;
        runCascade(state.pendingTrigger || triggers[0], ui, state);
      });
    }

    ui.cancel.addEventListener("click", function () {
      if (state.controller) state.controller.abort();
    });
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", init);
    } else {
      init();
    }
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      formatBytes: formatBytes,
      folderNameFromPath: folderNameFromPath,
      sanitizeEntryName: sanitizeEntryName,
      normalizedEntryKey: normalizedEntryKey,
      buildShareUrl: buildShareUrl,
      joinRelPath: joinRelPath,
      collectPlan: collectPlan,
    };
  }
})(typeof window !== "undefined" ? window : globalThis);
