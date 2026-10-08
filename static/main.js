// Update port list
function updatePorts() {
  return axios
    .get("/update_ports")
    .then(function (response) {
      // handle success
      if (response.status == 200) {
        jQuery(".portList").each(function () {
          const previous = jQuery(this).val();
          // Remove old content from list
          jQuery(this).html("");
          for (var content of response.data.content) {
            const value = escapeHtml(content);
            jQuery(this).append(`<option value="${value}">${value}</option>`);
          }
          // Keep the previous selection if it is still available
          if (previous) jQuery(this).val(previous);
        });
      }
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    })
    .then(function () {});
}

// Auto detect Baudrate
var baudDetectRunning = false;

function updateBaud() {
  if (baudDetectRunning) return;
  baudDetectRunning = true;

  // load spinner icon while the pi check for baudrate
  jQuery(".updateBaud").html('<span uk-spinner></span>');

  const restoreIcon = function () {
    baudDetectRunning = false;
    jQuery(".updateBaud").html('<span data-uk-icon="icon: search"></span>');
  };

  // send port info over first
  jQuery
    .post(
      "/update_baud",
      Object.assign({ selected_port: jQuery("#portList").val() }, lineValues()),
      function (response) {
        console.log(response);
        if (response != "None") {
          // change to new baudrate value
          $(".baudRate").val(response);
          scheduleUiSave();
        } else {
          notify("Plotter not detected", "warning");
        }
        restoreIcon();
      }
    )
    .fail(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
      restoreIcon();
    });
}

// Update file list
function updateFiles() {
  axios
    .get("/update_files")
    .then(function (response) {
      // handle success
      if (response.status == 200) {
        // Remove old content from list
        jQuery("#fileList").html("");

        const now = Date.now() / 1000;
        for (var content of response.data.content) {
          const info = content.size === undefined ? "" : formatBytes(content.size) + ", " + formatAge(now - content.mtime);
          jQuery("#fileList").append(
            `<li> ${renderFileListElement(content.name, info)} </li>`
          );
        }
        const current = jQuery("#fileName").val();
        jQuery("#fileList .selectFile").each(function () {
          if (jQuery(this).attr("data-filename") === current) jQuery(this).closest("li").addClass("is-selected");
        });
        updateStorage(response.data.content);
      }
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    })
    .then(function () {});
}

// Room left on the disk, and what the web plotter uses
var uploadedFiles = [];

function updateStorage(files) {
  if (files) uploadedFiles = files;
  return axios
    .get("/storage")
    .then(function (response) {
      const s = response.data;
      const disk = s.total === null ? "" : formatBytes(s.free) + " free of " + formatBytes(s.total) + ". ";
      jQuery("#storageInfo").text(
        disk + "Files " + formatBytes(s.uploads) + ", cache " + formatBytes(s.cache) + ", history " + formatBytes(s.history) +
          ", timelapse " + formatBytes(s.timelapse) + "."
      );
      // Running low is worth a warning colour
      jQuery("#storageInfo").toggleClass("uk-text-danger", s.total !== null && s.free < 200 * 1024 * 1024);
      // A backup with the files is restored in one upload, which has a size limit
      const size = jQuery("#backupUploadsSize");
      const tooBig = s.max_upload && s.uploads > s.max_upload * 0.8;
      size.text("(" + formatBytes(s.uploads) + (tooBig ? ", too big to restore here: back the files up another way" : "") + ")");
      size.toggleClass("uk-text-danger", !!tooBig);
    })
    .catch(function (error) {
      console.error(error);
    });
}

function deleteOldFiles() {
  const days = parseInt(jQuery("#oldDays").val(), 10);
  if (!(days >= 1)) {
    notify("Enter a number of days (1 or more)", "danger");
    return;
  }
  const cutoff = Date.now() / 1000 - days * 86400;
  const old = uploadedFiles.filter((file) => file.mtime !== undefined && file.mtime < cutoff);
  if (old.length === 0) {
    notify("No files are older than " + days + " days", "primary");
    return;
  }
  UIkit.modal.confirm("Delete " + old.length + " file" + (old.length === 1 ? "" : "s") + " older than " + days + " days? Files waiting in the queue are kept.").then(function () {
    axios
      .post("/delete_old_files", new URLSearchParams({ days: days }))
      .then(function (response) {
        notify("Deleted " + response.data.length + " file" + (response.data.length === 1 ? "" : "s"), "success");
        updateFiles();
      })
      .catch(function (error) {
        notify(errorMessage(error), "danger");
      });
  }, function () {});
}

function clearCache() {
  axios
    .post("/clear_cache")
    .then(function (response) {
      notify("Freed " + formatBytes(response.data.freed), "success");
      updateStorage();
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
    });
}

// Handle file selection
function selectFile(element) {
  const filename = jQuery(element).attr("data-filename");

  // Update form
  jQuery("#fileName").val(filename);

  // Update list
  jQuery("#fileList li").removeClass("is-selected");
  const li = jQuery(element).parents("li")[0];
  if (li) jQuery(li).addClass("is-selected");

  // Update sidebar
  jQuery(".selectedFilename").text(filename);

  loadFileInfo(filename);
  previewSelected(filename);
}

// Make a file the selected one without loading its preview again (the pens shown there must stay as they are).
// Returns a promise that is done when its details (and so its pens) are loaded.
function chooseFileQuietly(filename) {
  jQuery("#fileName").val(filename);
  jQuery("#fileList li").removeClass("is-selected");
  jQuery("#fileList [data-filename]").filter(function () {
    return jQuery(this).attr("data-filename") === filename;
  }).first().parents("li").first().addClass("is-selected");
  jQuery(".selectedFilename").text(filename);
  return loadFileInfo(filename);
}

// What the selected file draws: size, time estimate and pens
var fileInfoRequest = 0;

function clearFileInfo() {
  jQuery("#fileInfo").addClass("uk-hidden");
  jQuery("#penChoices").addClass("uk-hidden");
  jQuery("#penChoiceList").empty();
  jQuery("#plotPens").val("");
}

function loadFileInfo(filename) {
  clearFileInfo();
  if (!/\.hpgl$/i.test(filename)) return Promise.resolve();

  const request = ++fileInfoRequest;
  return axios
    .get("/analyze", { params: { file: filename } })
    .then(function (response) {
      // A newer selection has been made meanwhile
      if (request !== fileInfoRequest) return;
      showFileInfo(response.data);
    })
    .catch(function (error) {
      if (request === fileInfoRequest) console.error(error);
    });
}

function showFileInfo(data) {
  const summary = data.summary;
  if (!summary) {
    // Busy plotting (nothing cached) or too large to analyse: the plot still works
    jQuery("#fileInfoText").text(
      data.busy ? "Details are not available while plotting." : "This file is too large to estimate."
    );
    jQuery("#fileInfo").removeClass("uk-hidden");
    return;
  }

  let text =
    "About " + formatDuration(summary.seconds) + " to plot, " +
    summary.width_mm.toFixed(0) + " x " + summary.height_mm.toFixed(0) + " mm, " +
    summary.paths + " lines, " + (summary.draw_mm / 1000).toFixed(1) + " m drawn.";
  if (summary.unsupported.length > 0) {
    text += " Not counted: " + summary.unsupported.join(", ") + ".";
  }
  jQuery("#fileInfoText").text(text);

  const list = jQuery("#penChoiceList").empty();
  if (summary.pens.length > 1) {
    for (const pen of summary.pens) {
      const label = jQuery("<label/>");
      jQuery("<input/>", { type: "checkbox", class: "uk-checkbox penChoice", value: pen.pen, checked: true }).appendTo(label);
      label.append(document.createTextNode(" Pen " + pen.pen + " (" + formatDuration(pen.seconds) + ")"));
      jQuery("<div/>").append(label).appendTo(list);
    }
    jQuery("#penChoices").removeClass("uk-hidden");
  }
  jQuery("#fileInfo").removeClass("uk-hidden");
}

// The pens to plot go to the server only when some of them are left out
function updatePenSelection() {
  const all = jQuery(".penChoice");
  const chosen = all.filter(":checked").map(function () { return this.value; }).get();
  jQuery("#plotPens").val(chosen.length === all.length ? "" : chosen.join(","));
}

// Handle file deletion
function deleteFile(element) {
  const filename = jQuery(element).attr("data-filename");

  axios
    .post("/delete_file", { filename: filename })
    .then(function (response) {
      // handle success
      if (response.status == 200) {
        // Remove old content from list
        notify(response.data, "warning");
        updateFiles();
      }
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    })
    .then(function () {});
}

// Handle HPGL preview
var hpglViewer = null;

function previewFile(element) {
  const filename = jQuery(element).attr("data-filename");
  showPreview(filename, "/uploads/" + encodeURIComponent(filename), null, false);
}

// The file shown in the preview, and whether the cursor follows the plot that is running
var previewedFile = null;
var watchingPlot = false;

// The conversion shown in the preview: {name, summary} for one that has not been saved yet (it can be
// saved, or abandoned by looking at something else), null for a file in the list.
var previewedConversion = null;

// The preview is a panel, so it can be hidden, folded up or parked where nothing has a size
function previewVisible() {
  return jQuery("#previewStage").is(":visible");
}

// Show a drawing in the preview panel. `quiet` is for a file that was only selected: it does not bring a
// hidden panel back or move the page, and does not close the conversion options.
function showPreview(title, url, conversion, watch, quiet) {
  previewedConversion = conversion;
  previewedFile = title;
  watchingPlot = !!watch;
  jQuery("#previewFileName").text(title);
  jQuery("#previewUnsaved").toggleClass("uk-hidden", !conversion);
  jQuery("#previewInfo").text("Loading...");
  jQuery("#previewSummary").text(conversion ? describeSummary(conversion.summary) : "");
  jQuery("#previewActions").toggleClass("uk-hidden", !conversion);
  updateWatchButton();

  if (!quiet) {
    const convertModal = UIkit.modal("#modal-convertFile");
    if (convertModal.isToggled()) convertModal.hide();
    if (window.WebPlotterLayoutUI) WebPlotterLayoutUI.reveal("preview");
  }
  loadPreview(url);
}

// A file was selected in the list: show it, unless that would replace a conversion that is not saved yet
function previewSelected(filename) {
  if (previewedConversion || !previewVisible()) return;
  const watch = watchingPlot && filename === currentPlotFile;
  showPreview(filename, "/uploads/" + encodeURIComponent(filename), null, watch, true);
}

// The panels were changed: show the selected file if the preview has just come back, and stop a replay
// that nobody can see
function onPanelsChanged() {
  if (!previewVisible()) {
    if (hpglViewer && hpglViewer.isReplaying()) {
      hpglViewer.stopReplay();
      jQuery("#replayToggle").text("Replay");
    }
    return;
  }
  const file = jQuery("#fileName").val();
  if (file && !previewedConversion && previewedFile !== file) previewSelected(file);
}

function describeSummary(summary) {
  if (!summary) return "";
  const drawn = summary.draw_mm / 1000;
  const travel = summary.travel_mm / 1000;
  const share = drawn > 0 ? Math.round((100 * travel) / drawn) : 0;
  return (
    "About " + formatDuration(summary.seconds) + " to plot. Drawing " + drawn.toFixed(1) +
    " m, pen-up travel " + travel.toFixed(1) + " m (" + share + "% of the drawing)."
  );
}

// Preview files bigger than this would freeze a phone while parsing
const PREVIEW_MAX_BYTES = 30 * 1024 * 1024;

// Show the canvas, or say there is nothing to show
function setPreviewDrawing(has) {
  jQuery("#previewStage").toggleClass("has-drawing", has);
  if (!has) jQuery("#previewReplayBar, #previewUsePens").addClass("uk-hidden");
  if (!has) jQuery("#previewLegend").empty();
}

// The canvas is as wide as its stage: fit the drawing again when the panel changes width (a column was
// resized, the panel was moved, or it was folded up and opened again)
function watchPreviewSize(stage) {
  if (!window.ResizeObserver) return;
  let width = stage.clientWidth;
  new ResizeObserver(function () {
    const now = stage.clientWidth;
    if (now && now !== width && hpglViewer && hpglViewer.bounds) {
      hpglViewer.resizeCanvas();
      hpglViewer.fit();
    }
    if (now) width = now;
  }).observe(stage);
}

function loadPreview(url) {
  const requested = previewedFile;
  axios
    .get(url, {
      responseType: "text",
      transformResponse: [(data) => data], // keep the raw text, don't try to parse JSON
      params: { _: Date.now() }, // always show the latest version of a re-converted file
    })
    .then(function (response) {
      // Something else was chosen while this was loading
      if (requested !== previewedFile) return;
      if (response.data.length > PREVIEW_MAX_BYTES) {
        setPreviewDrawing(false);
        jQuery("#previewInfo").text("File is too large to preview");
        return;
      }

      if (!hpglViewer) {
        hpglViewer = new HPGLViewer(document.getElementById("hpglCanvas"), {
          onHover: showPreviewCoords,
          onReplay: showReplayProgress,
        });
        watchPreviewSize(document.getElementById("previewStage"));
      }
      // The canvas takes its size from the stage, so the stage has to show it first
      setPreviewDrawing(true);
      const stats = hpglViewer.loadHPGL(response.data, /\.cal$/i.test(requested || "") ? "cal" : "hpgl");
      resetPreviewTools(stats);

      if (stats.paths == 0) {
        setPreviewDrawing(false);
        jQuery("#previewInfo").text("Nothing to preview: the file contains no pen-down movements");
        return;
      }

      let info =
        stats.paths + " paths, " + stats.points + " points, approx. " +
        stats.widthMm.toFixed(0) + " x " + stats.heightMm.toFixed(0) + " mm";
      if (stats.unsupported.length > 0) {
        info += " (ignored commands: " + stats.unsupported.join(", ") + ")";
      }
      jQuery("#previewInfo").text(info);
      if (watchingPlot) followPlot(jQuery("#bytes_written").text());
    })
    .catch(function (error) {
      if (requested !== previewedFile) return;
      setPreviewDrawing(false);
      jQuery("#previewInfo").text("Preview failed: " + errorMessage(error));
      console.error(error);
    });
}

// The tools under the preview: legend, travel, paper, replay
function resetPreviewTools(stats) {
  jQuery("#previewTravel").prop("checked", false);
  jQuery("#previewCoords").text(PREVIEW_HINT);
  jQuery("#previewReplayBar").toggleClass("uk-hidden", stats.paths === 0 || watchingPlot);
  jQuery("#replayToggle").text("Replay");
  jQuery("#replaySeek").val(0);
  jQuery("#replayTime").text("");

  // The paper is only known for a conversion that has not been saved yet
  if (previewedConversion && previewedConversion.paper) {
    const paper = HPGLViewer.paperSizeMm(previewedConversion.paper.size, previewedConversion.paper.orientation, previewedConversion.paper.rotate);
    hpglViewer.setPaper(paper);
  } else {
    hpglViewer.setPaper(null);
  }
  buildLegend();
}

const PREVIEW_HINT = "Scroll to zoom, drag to move, double-click to fit.";

function buildLegend() {
  const legend = jQuery("#previewLegend").empty();
  const pens = hpglViewer.penList();
  const canChoose = pens.length > 1 && /\.hpgl$/i.test(previewedFile || "") && !previewedConversion && !watchingPlot;
  jQuery("#previewUsePens").toggleClass("uk-hidden", !canChoose);
  if (pens.length === 0) return;
  for (const pen of pens) {
    const label = jQuery("<label class='uk-margin-small-right uk-text-nowrap'/>");
    jQuery("<input/>", { type: "checkbox", class: "uk-checkbox legendPen", value: pen.pen, checked: pen.visible }).appendTo(label);
    jQuery("<span class='legend-swatch'/>").css("background", pen.color).appendTo(label);
    label.append(document.createTextNode(" Pen " + pen.pen + " (" + (pen.lengthMm / 1000).toFixed(1) + " m)"));
    legend.append(label);
  }
}

function showPreviewCoords(info) {
  jQuery("#previewCoords").text(info ? info.xMm.toFixed(1) + ", " + info.yMm.toFixed(1) + " mm from the lower left corner" : PREVIEW_HINT);
}

// Plot only the pens that are shown in the preview
function usePreviewPens() {
  const shown = new Set(hpglViewer.penList().filter((pen) => pen.visible).map((pen) => String(pen.pen)));
  if (shown.size === 0) {
    notify("Show at least one pen", "danger");
    return;
  }
  // The file being looked at becomes the file to plot, when it is not already
  const apply = function () {
    if (jQuery(".penChoice").length === 0) {
      notify("The pens of this file are not known yet", "danger");
      return;
    }
    jQuery(".penChoice").each(function () {
      this.checked = shown.has(this.value);
    });
    updatePenSelection();
    notify("Plotting " + previewedFile + " with only the pens shown", "success");
  };
  if (jQuery("#fileName").val() === previewedFile && jQuery(".penChoice").length > 0) apply();
  else chooseFileQuietly(previewedFile).then(apply);
}

// Replay
function toggleReplay() {
  if (!hpglViewer) return;
  if (hpglViewer.isReplaying()) {
    hpglViewer.stopReplay();
    jQuery("#replayToggle").text("Replay");
  } else {
    hpglViewer.startReplay(Number(jQuery("#replaySpeed").val()));
    jQuery("#replayToggle").text("Pause");
  }
}

function showReplayProgress(fraction, seconds) {
  jQuery("#replaySeek").val(Math.round(fraction * 1000));
  jQuery("#replayTime").text(formatDuration(seconds));
  if (fraction >= 1) jQuery("#replayToggle").text("Replay");
}

function seekReplay(value) {
  hpglViewer.setCursor(Number(value) / 1000);
  jQuery("#replayTime").text(formatDuration((Number(value) / 1000) * hpglViewer.replaySeconds()));
}

// Watch the plot that is running: the cursor follows the bytes sent to the plotter. Pressed again, it stops.
function watchPlot() {
  if (watchingPlot) {
    stopWatching();
    return;
  }
  if (!currentPlotFile) return;
  showPreview(currentPlotFile, "/uploads/" + encodeURIComponent(currentPlotFile), null, true);
}

function stopWatching() {
  watchingPlot = false;
  if (hpglViewer) {
    hpglViewer.setCursor(null);
    if (hpglViewer.paths.length > 0) jQuery("#previewReplayBar").removeClass("uk-hidden");
  }
  updateWatchButton();
}

function updateWatchButton() {
  jQuery(".watchPlot").text(watchingPlot ? "Stop watching" : "Watch the plot");
}

function followPlot(text) {
  const match = /([0-9]+) bytes (?:written|sent)/.exec(text || "");
  if (watchingPlot && hpglViewer && match) hpglViewer.setCursorOffset(Number(match[1]));
}

// Update page size options
// Paper sizes a device of your own does not have cannot be chosen (the ones that come with vpype are not checked here)
function updatePageSize() {
  const device = vpypeDevices[jQuery("#convertData [name='device']").val()];
  const size = jQuery("#convertData [name='outputsize']");
  size.find("option").each(function () {
    jQuery(this).prop("disabled", !!device && device.papers.indexOf(this.value) < 0);
  });
  if (size.find("option:selected").prop("disabled")) {
    const first = size.find("option:not(:disabled)").first();
    if (first.length) size.val(first.val());
  }
}

// Handle file conversion
function convertFileModal(element) {
  const filename = jQuery(element).attr("data-filename");
  jQuery("#convertFile").val(filename);
  updatePageSize();
  updatePresets();
  updatePlugins();
  UIkit.modal("#modal-convertFile").show();
}

// vpype plugins: what is installed, and a click puts a command into the custom command box
function pluginCommandHtml(command) {
  const params = command.params.map(function (param) {
    let text = escapeHtml(param.flags);
    if (param.type && param.type !== "flag") text += " <em>" + escapeHtml(param.type) + "</em>";
    if (param.default !== null && param.default !== "" && param.default !== false) {
      text += " (default " + escapeHtml(param.default) + ")";
    }
    if (param.help) text += ": " + escapeHtml(param.help);
    return "<li>" + text + "</li>";
  });
  const name = escapeHtml(command.name);
  const button = command.file
    ? '<span class="plugin-cmd is-disabled" title="Takes a file, so it cannot be used here">' + name + "</span>"
    : '<a href="#" class="plugin-cmd" data-command="' + name + '" title="Add to the custom command">' + name + "</a>";
  return (
    '<div class="plugin-command">' + button +
    (command.help ? " <span class=\"uk-text-muted\">" + escapeHtml(command.help) + "</span>" : "") +
    (command.file ? ' <span class="uk-text-warning">takes a file: not available</span>' : "") +
    (params.length ? '<ul class="plugin-params">' + params.join("") + "</ul>" : "") +
    "</div>"
  );
}

function showPlugins(data) {
  const list = jQuery("#pluginList").empty();
  jQuery("#pluginCount").text(data.plugins.length ? "(" + data.plugins.length + ")" : "(none installed)");
  for (const plugin of data.plugins) {
    const box = jQuery("<div/>", { class: "plugin" }).appendTo(list);
    jQuery("<strong/>", { text: plugin.name + (plugin.version ? " " + plugin.version : "") }).appendTo(box);
    if (plugin.error) jQuery("<div/>", { class: "uk-text-danger", text: plugin.error }).appendTo(box);
    box.append(plugin.commands.map(pluginCommandHtml).join(""));
  }
  const hint = jQuery("<p/>", { class: "uk-text-muted uk-margin-small-top" }).appendTo(list);
  hint.append(
    document.createTextNode(
      "Plugins are installed on the plotter's computer, then the server is restarted. In a terminal, run: "
    )
  );
  jQuery("<code/>", { text: data.install + "vpype-<name>" }).appendTo(hint);
  hint.append(document.createTextNode(". Commands that take a file cannot be used here."));
}

function updatePlugins() {
  return axios
    .get("/vpype_plugins")
    .then(function (response) {
      showPlugins(response.data);
    })
    .catch(function (error) {
      console.error(error);
    });
}

function addPluginCommand(name) {
  const custom = jQuery("#use_custom_command");
  if (!custom.prop("checked")) custom.prop("checked", true).trigger("change");
  const input = jQuery("#command_input");
  input.val((input.val().trim() + " " + name).trim()).trigger("focus");
}

// Make an svg from typed text
function createText() {
  axios
    .post("/create_text", jQuery("#textData").serialize())
    .then(function (response) {
      notify(response.data, "success");
      updateFiles();
      UIkit.modal("#modal-createText").hide();
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    });
}

// Plotters: named sets of the settings in Plotter settings (device, baud rate, flow control, pen
// changes and the serial line). The port is not part of one: it belongs to this computer.
const LINE_FIELDS = ["bytesize", "parity", "stopbits", "xonxoff", "rtscts", "dsrdtr", "dtr", "rts", "timeout", "open_delay"];
const LINE_DEFAULTS = { bytesize: "8", parity: "N", stopbits: "1", xonxoff: "auto", rtscts: "auto", dsrdtr: "auto", dtr: "auto", rts: "auto", timeout: "", open_delay: "0" };
var plotterProfiles = {};

// The serial line settings as they are in the form
function lineValues() {
  const values = {};
  for (const key of LINE_FIELDS) values[key] = jQuery("#plotterData [name='" + key + "']").val() || "";
  return values;
}

// Choose an option, adding it when the list does not have it (a baud rate from a shared plotter)
function setSelect(select, value) {
  select = jQuery(select);
  if (value && !select.find("option").filter(function () { return this.value == value; }).length) {
    jQuery("<option/>", { value: value, text: value }).appendTo(select);
  }
  select.val(value);
}

function updatePlotterProfiles(selected) {
  return axios
    .get("/plotters")
    .then(function (response) {
      const panel = jQuery("#plotterProfile");
      const previous = selected !== undefined ? selected : panel.val();
      plotterProfiles = {};
      const lists = [
        [panel, "Custom settings"],
        [jQuery("#plotter_profile"), "None"],
      ];
      for (const [list, first] of lists) {
        const kept = list.is(panel) ? previous : list.val();
        list.empty();
        jQuery("<option/>", { value: "", text: first }).appendTo(list);
        const groups = {
          builtin: jQuery("<optgroup/>", { label: "Built in" }),
          custom: jQuery("<optgroup/>", { label: "My plotters" }),
        };
        for (const profile of response.data) {
          plotterProfiles[profile.id] = profile;
          jQuery("<option/>", { value: profile.id, text: profile.name }).appendTo(groups[profile.source]);
        }
        for (const source of ["builtin", "custom"]) {
          if (groups[source].children().length) groups[source].appendTo(list);
        }
        list.val(plotterProfiles[kept] ? kept : "");
      }
      showPlotterNotes(panel.val());
    })
    .catch(function (error) {
      console.error(error);
    });
}

function showPlotterNotes(id) {
  const profile = plotterProfiles[id];
  const notes = jQuery("#plotterNotes");
  if (profile && profile.notes) notes.text(profile.notes).prop("hidden", false);
  else notes.text("").prop("hidden", true);
}

function applyPlotterProfile(id) {
  showPlotterNotes(id);
  const profile = plotterProfiles[id];
  if (!profile) return;
  const s = profile.settings;
  jQuery("#device").val(s.device).change();
  jQuery("#convertData [name='device']").val(s.device);
  setSelect("#baudRate", s.baudrate);
  jQuery("#flowControl").val(s.flowControl).change();
  jQuery("#penChange").val(s.pen_change);
  let changed = false;
  for (const key of LINE_FIELDS) {
    jQuery("#plotterData [name='" + key + "']").val(s[key]);
    if (s[key] !== LINE_DEFAULTS[key]) changed = true;
  }
  // Show the serial line when the plotter does something unusual with it
  if (changed) jQuery("#serialLine").prop("open", true);
  scheduleUiSave();
}

function savePlotter() {
  const chosen = plotterProfiles[jQuery("#plotterProfile").val()];
  UIkit.modal.prompt("Name for this plotter:", chosen ? chosen.name : "").then(function (name) {
    name = (name || "").trim();
    if (!name) return;
    let body = jQuery("#plotterData").serialize() + "&name=" + encodeURIComponent(name);
    if (chosen && chosen.name === name) body += "&notes=" + encodeURIComponent(chosen.notes || "");
    axios
      .post("/plotters", body)
      .then(function (response) {
        notify(response.data, "success");
        return axios.get("/plotters");
      })
      .then(function (response) {
        const saved = response.data.find(function (profile) { return profile.name === name; });
        return updatePlotterProfiles(saved ? saved.id : "");
      })
      .catch(function (error) {
        notify(errorMessage(error), "danger");
        console.error(error);
      });
  });
}

function deletePlotter() {
  const profile = plotterProfiles[jQuery("#plotterProfile").val()];
  if (!profile || profile.source !== "custom") {
    notify("Choose one of your own plotters to delete", "warning");
    return;
  }
  UIkit.modal.confirm("Delete the plotter " + profile.name + "?").then(function () {
    axios
      .post("/plotters/delete", new URLSearchParams({ id: profile.id }).toString())
      .then(function (response) {
        notify(response.data, "warning");
        return updatePlotterProfiles("");
      })
      .catch(function (error) {
        notify(errorMessage(error), "danger");
        console.error(error);
      });
  }, function () {});
}

function importPlotters() {
  const file = jQuery("#importPlottersFile")[0].files[0];
  if (!file) {
    notify("Choose a plotter file first", "danger");
    return;
  }
  const form = new FormData();
  form.append("plotters", file);
  axios
    .post("/plotters/import", form)
    .then(function (response) {
      notify("Imported " + response.data.imported + " plotters: " + response.data.names.join(", "), "success");
      jQuery("#importPlottersFile").val("");
      return updatePlotterProfiles();
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
    });
}

// vpype devices of your own: plotters vpype does not know. A device is text in vpype's own format (TOML); the
// server checks it, keeps it in userdata/ and registers it with vpype before each conversion.
var vpypeDevices = {};
var vpypeBuiltin = [];
var editingDevice = null;

// The device lists on the page: the devices of your own go in a group of their own
function updateVpypeDevices() {
  return axios
    .get("/vpype_devices")
    .then(function (response) {
      vpypeDevices = {};
      for (const device of response.data.devices) vpypeDevices[device.id] = device;
      vpypeBuiltin = response.data.builtin;
      const lists = [jQuery("#device"), jQuery("#convertData [name='device']"), jQuery("#plotter_device")];
      for (const list of lists) {
        const kept = list.val();
        list.find("optgroup.my-devices").remove();
        const mine = Object.values(vpypeDevices);
        if (mine.length) {
          const group = jQuery("<optgroup/>", { label: "My devices", class: "my-devices" });
          for (const device of mine) {
            // A device of your own with the name of one the page lists (vpype may not have it) takes its place
            list.children("option[value='" + device.id + "']").remove();
            jQuery("<option/>", { value: device.id, text: device.name }).appendTo(group);
          }
          group.appendTo(list);
        }
        if (kept && list.find("option").filter(function () { return this.value === kept; }).length) list.val(kept);
      }
      showDeviceList();
      updatePageSize();
    })
    .catch(function (error) {
      console.error(error);
    });
}

function showDeviceList() {
  const list = jQuery("#deviceList").empty();
  const devices = Object.values(vpypeDevices);
  if (!devices.length) {
    jQuery("<li/>", { class: "cs-note", text: "None yet. Add one below." }).appendTo(list);
  }
  for (const device of devices) {
    const row = jQuery("<li/>").append(
      jQuery("<div/>", { class: "device-row" })
        .append(jQuery("<span/>", { class: "device-name", text: device.name }))
        .append(jQuery("<span/>", { class: "device-meta", text: device.pens + (device.pens === 1 ? " pen" : " pens") + ", " + device.papers.join(", ") }))
        .append(jQuery("<a/>", { href: "#", class: "editDevice uk-text-small", "data-id": device.id, text: "Edit" }))
        .append(jQuery("<a/>", { href: "#", class: "deleteDevice uk-text-small", "data-id": device.id, text: "Delete" }))
    );
    row.appendTo(list);
  }
  const base = jQuery("#deviceBase");
  base.find("option:not(:first)").remove();
  for (const id of vpypeBuiltin) jQuery("<option/>", { value: id, text: id }).appendTo(base);
}

function deviceError(message) {
  jQuery("#deviceError").text(message || "").prop("hidden", !message);
}

function startDeviceEditor(id, text) {
  editingDevice = id;
  jQuery("#deviceEditorTitle").text(id ? "Change " + vpypeDevices[id].name : "Add a device");
  jQuery("#deviceText").val(text);
  jQuery("#deviceBase").val("");
  deviceError("");
}

function openDeviceManager() {
  updateVpypeDevices().then(function () {
    startDeviceEditor(null, "");
    UIkit.modal("#modal-vpypeDevices").show();
  });
}

function editDevice(id) {
  if (!vpypeDevices[id]) return;
  startDeviceEditor(id, vpypeDevices[id].text);
  jQuery("#deviceText").trigger("focus");
}

function generateDevice() {
  deviceError("");
  axios
    .post("/vpype_devices/generate", jQuery("#deviceQuickForm").serialize())
    .then(function (response) {
      jQuery("#deviceText").val(response.data.text);
    })
    .catch(function (error) {
      deviceError(errorMessage(error));
    });
}

function deviceFromVpype(id) {
  if (!id) return;
  deviceError("");
  axios
    .get("/vpype_devices/template", { params: { base: id } })
    .then(function (response) {
      editingDevice = null;
      jQuery("#deviceEditorTitle").text("Add a device");
      jQuery("#deviceText").val(response.data.text);
    })
    .catch(function (error) {
      deviceError(errorMessage(error));
    });
}

function saveDevice() {
  deviceError("");
  const body = new URLSearchParams({ text: jQuery("#deviceText").val() });
  if (editingDevice) body.set("replace", editingDevice);
  axios
    .post("/vpype_devices", body.toString())
    .then(function (response) {
      notify("Saved " + response.data.saved.join(", "), "success");
      return updateVpypeDevices().then(function () {
        startDeviceEditor(null, "");
      });
    })
    .catch(function (error) {
      deviceError(errorMessage(error));
    });
}

function deleteDevice(id) {
  const device = vpypeDevices[id];
  if (!device) return;
  UIkit.modal.confirm("Delete the device " + device.name + "? Plotters that use it cannot convert until it is added again.").then(function () {
    axios
      .post("/vpype_devices/delete", new URLSearchParams({ id: id }).toString())
      .then(function (response) {
        notify(response.data, "warning");
        if (editingDevice === id) startDeviceEditor(null, "");
        return updateVpypeDevices();
      })
      .catch(function (error) {
        notify(errorMessage(error), "danger");
      });
  }, function () {});
}

function loadDeviceFile() {
  const file = jQuery("#deviceFile")[0].files[0];
  if (!file) {
    deviceError("Choose a file first");
    return;
  }
  if (file.size > 128 * 1024) {
    deviceError("That file is too large to be a device");
    return;
  }
  const reader = new FileReader();
  reader.onload = function () {
    editingDevice = null;
    jQuery("#deviceEditorTitle").text("Add a device");
    jQuery("#deviceText").val(reader.result);
    jQuery("#deviceFile").val("");
    deviceError("");
  };
  reader.onerror = function () {
    deviceError("That file could not be read");
  };
  reader.readAsText(file);
}

// Conversion presets: named sets of the options in the convert dialog
var presetOptions = {};

function updatePresets(selected) {
  return axios
    .get("/presets")
    .then(function (response) {
      const list = jQuery("#presetList").empty();
      jQuery("<option/>", { value: "", text: response.data.length ? "Choose a preset..." : "No presets saved" }).appendTo(list);
      presetOptions = {};
      for (const preset of response.data) {
        presetOptions[preset.name] = preset.options;
        jQuery("<option/>", { value: preset.name, text: preset.name }).appendTo(list);
      }
      list.val(selected && presetOptions[selected] ? selected : "");
    })
    .catch(function (error) {
      console.error(error);
    });
}

function applyPreset(name) {
  const options = presetOptions[name];
  if (!options) return;

  const custom = jQuery("#use_custom_command");
  // Leave custom command mode first, so the optimisation boxes can be set
  if (custom.prop("checked") && !options.command_input) {
    custom.prop("checked", false).trigger("change");
  }
  for (const key in options) {
    const field = jQuery("#convertData [name='" + key + "']");
    if (field.is(":checkbox")) field.prop("checked", !!options[key]);
    else field.val(options[key]);
  }
  if (options.command_input && !custom.prop("checked")) {
    custom.prop("checked", true).trigger("change");
    jQuery("#command_input").val(options.command_input);
  }
  scheduleUiSave();
}

function savePreset() {
  UIkit.modal.prompt("Name for this set of options:", jQuery("#presetList").val() || "").then(function (name) {
    name = (name || "").trim();
    if (!name) return;
    axios
      .post("/presets", jQuery("#convertData").serialize() + "&name=" + encodeURIComponent(name))
      .then(function (response) {
        notify(response.data, "success");
        return updatePresets(name);
      })
      .catch(function (error) {
        notify(errorMessage(error), "danger");
        console.error(error);
      });
  });
}

function deletePreset() {
  const name = jQuery("#presetList").val();
  if (!name) {
    notify("Choose a preset to delete", "warning");
    return;
  }
  UIkit.modal.confirm("Delete the preset " + name + "?").then(function () {
    axios
      .post("/presets/delete", new URLSearchParams({ name: name }).toString())
      .then(function (response) {
        notify(response.data, "warning");
        return updatePresets();
      })
      .catch(function (error) {
        notify(errorMessage(error), "danger");
        console.error(error);
      });
  }, function () {});
}

// Convert without keeping the result, and show it
function previewConversion() {
  if (jQuery("#convertFile").val() == "") {
    notify("No *.svg file selected", "danger");
    return false;
  }

  jQuery("#loader").removeClass("uk-hidden");

  axios
    .post("/preview_conversion", jQuery("#convertData").serialize())
    .then(function (response) {
      const name = response.data.name;
      const conversion = response.data;
      conversion.paper = {
        size: jQuery("#convertData [name=outputsize]").val(),
        orientation: jQuery("#convertData [name=pageorientation]").val(),
        rotate: jQuery("#rotate").val(),
      };
      showPreview(name, "/preview_files/" + encodeURIComponent(name), conversion);
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    })
    .then(function () {
      jQuery("#loader").addClass("uk-hidden");
    });
}

// Keep the previewed conversion
function savePreview() {
  if (!previewedConversion) return;
  axios
    .post("/save_preview", new URLSearchParams({ name: previewedConversion.name }).toString())
    .then(function (response) {
      notify(response.data, "success");
      previewedConversion = null;
      jQuery("#previewActions, #previewUnsaved").addClass("uk-hidden");
      updateFiles();
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    });
}

// Back from the preview to the conversion options
function backToConvert() {
  UIkit.modal("#modal-convertFile").show();
}

// Start conversion
function convertFile() {
  const convertData = jQuery("#convertData").serializeArray();
  console.log("convertData", convertData);

  // Validation
  if (jQuery("#convertFile").val() == "") {
    notify("No *.svg file selected", "danger");
    return false;
  }

  jQuery("#loader").removeClass("uk-hidden");

  axios
    .post("/start_conversion", jQuery("#convertData").serialize())
    .then(function (response) {
      // handle success
      if (response.status == 200) {
        if (response.data == "File not converted.") {
          console.error(response);
          notify(response.data, "danger");
        } else {
          console.log(response);
          notify(response.data, "success");
        }

        updateFiles();
        UIkit.modal("#modal-convertFile").hide();
      }
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    })
    .then(function () {
      jQuery("#loader").addClass("uk-hidden");
    });
}

// Clear Logs
function clearLog() {
  // Remove old content from log
  jQuery("#statusLog").html("");
  jQuery("#bytes_written").html("");
}

// The plot form as a query string, or null (after telling the user why) when it is not complete
function plotFormData() {
  if (jQuery("#fileName").val() == "") {
    notify("No *.hpgl or *.cal file selected", "danger");
    return null;
  }
  if (jQuery("#portList").val() == null) {
    notify("No COM port selected", "danger");
    updatePorts();
    return null;
  }
  if (jQuery(".penChoice").length > 0 && jQuery(".penChoice:checked").length === 0) {
    notify("Select at least one pen to plot", "danger");
    return null;
  }
  updatePenSelection();
  return jQuery("#plotterData").serialize();
}

// Start plotting
function startPlot() {
  const data = plotFormData();
  if (data === null) return false;

  axios
    .post("/start_plot", data)
    .then(function (response) {
      // handle success
      if (response.status == 200) {
        console.log(response);
      }
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    });
}

function stopPlot() {
  axios
    .post("/stop_plot")
    .then(function (response) {
      // handle success
      if (response.status == 200) {
        console.log(response);
        notify("Stopped Print", "danger");

        // Update sidebar
        jQuery(".selectedFilename").html("");
        jQuery("#statusLog").html("Plot canceled" + "<br>");
      }
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    });
}

function pausePlot() {
  axios.post("/pause_plot").catch(function (error) {
    notify(errorMessage(error), "danger");
    console.error(error);
  });
}

function resumePlot() {
  axios.post("/resume_plot").catch(function (error) {
    notify(errorMessage(error), "danger");
    console.error(error);
  });
}

// Log lines, shared by live events and the log replayed to a freshly opened page
function appendStatusLog(text) {
  jQuery("#statusLog").append(escapeHtml(text) + "<br>");
  scrollLog();
}

function appendErrorLog(text) {
  jQuery("#statusLog").append("<br>" + jQuery('<div class="error"/>').text(text).html());
  scrollLog();
}

// Bring the plot view in line with the server: used when the page is opened or refreshed, or
// connects from another device, while a plot is running, and when the plot is paused or resumed.
var currentPlotFile = null;

function applyPlotState(state) {
  updateTransport(state);
  jQuery("#convertPlotWarning").toggleClass("uk-hidden", !state.running);
  currentPlotFile = state.running && state.cursor_ok ? state.file : null;
  jQuery(".watchPlot").toggleClass("uk-hidden", !currentPlotFile);
  jQuery(".pausePlot").toggleClass("uk-hidden", !!state.paused);
  jQuery(".resumePlot").toggleClass("uk-hidden", !state.paused);

  // Held back (pen change, paper change, lost connection): say why, and close the notice when it is over
  if (state.running && state.paused && WAIT_NOTICES[state.wait_reason]) {
    showWaitNotice(state);
  } else {
    hideWaitNotice();
  }
  setEta(state.running ? state.eta : null);
  if (state.running && state.timelapse) showTimelapseFrame(state.timelapse);
  else hideTimelapseFrame();
  if (!state.running && watchingPlot) stopWatching();

  if (state.running && state.file) {
    jQuery(".selectedFilename").text(state.file);
  }
  setProgress(state.progress || 0);
  if (state.bytes_written !== undefined) {
    setBytesWritten(state.bytes_written);
    followPlot(state.bytes_written);
  }

  if (state.buffer_size) {
    // Only restart the chart when it is not already showing this plotter's buffer
    if (Number(state.buffer_size) !== Number(buffer_size)) {
      buffer_size = state.buffer_size;
      createTimeline();
    }
    jQuery("#chartContents").removeClass("hidden");
  } else if (!state.running) {
    jQuery("#chartContents").addClass("hidden");
  }

  // The log is only sent when connecting
  if (state.log) {
    jQuery("#statusLog").empty();
    for (const entry of state.log) {
      if (entry.type == "error") appendErrorLog(entry.text);
      else appendStatusLog(entry.text);
    }
  }
}

// The transport bar at the top of the page: what the plot is doing, and why it is held back
const STATE_LABELS = {
  idle: "Idle",
  plotting: "Plotting",
  paused: "Paused",
  pen_change: "Pen change",
  paper_change: "Paper change",
  disconnected: "Disconnected",
  reconnect: "Plotter is back",
  frame_check: "Check the paper",
};

function plotStateName(state) {
  if (!state.running) return "idle";
  if (!state.paused) return "plotting";
  return state.wait_reason in STATE_LABELS ? state.wait_reason : "paused";
}

function updateTransport(state) {
  const name = plotStateName(state);
  jQuery("#statePill").attr("data-s", name).text(STATE_LABELS[name]);
  jQuery("#transport").toggleClass("is-plotting", name === "plotting").toggleClass("is-held", !!(state.running && state.paused));
  jQuery(".penText").text(state.running && state.pen ? state.pen : "-");
  if (!state.running) jQuery(".bytesText").html("&nbsp;");
  const notice = state.running && state.paused ? WAIT_NOTICES[state.wait_reason] : null;
  jQuery("#transportMessage").prop("hidden", !notice).text(notice ? notice.title + ". " + notice.text(state) : "");
}

function setProgress(value) {
  jQuery(".printProgress").val(value);
  jQuery(".pctText").text(Math.round(Number(value) || 0) + "%");
}

function setBytesWritten(value) {
  jQuery("#bytes_written").text(value);
  const match = /([0-9]+) bytes (?:written|sent)/.exec(value || "");
  if (match && Number(match[1]) > 0) jQuery(".bytesText").text(formatBytes(Number(match[1])) + " sent");
}

// 754 -> "12:34", 3700 -> "1:01:40"
function formatClock(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h > 0 ? h + ":" + pad(m) + ":" + pad(s) : m + ":" + pad(s);
}

// Time left
function setEta(eta) {
  jQuery(".etaClock").text(eta ? formatClock(eta.remaining) : "--:--");
  jQuery(".etaClock").parent().attr("title", eta ? "About " + formatDuration(eta.remaining) + " left, of " + formatDuration(eta.total) : "");
}

// The dialog shown while the plot is held back, by wait_reason
const WAIT_NOTICES = {
  pen_change: {
    title: "Change pen",
    text: (state) => "Wait until the plotter has stopped moving, then load pen " + state.pen + " and press Resume.",
    resume: "Resume",
    stop: "Stop plot",
  },
  disconnected: {
    title: "Lost the connection to the plotter",
    text: () => "The plot is held. The server keeps trying to connect again (check the cable, the adapter and that the plotter is on). Stop gives the plot up.",
    resume: "",
    stop: "Stop plot",
  },
  reconnect: {
    title: "The plotter is back",
    text: () => "Check that the paper and the pen carriage have not moved, and that the right pen is loaded. Resume carries on from a little before where the connection dropped, so the last strokes may be drawn twice.",
    resume: "Resume",
    stop: "Stop plot",
  },
  frame_check: {
    title: "Check the paper",
    text: () => "The pen has gone round the area the drawing will cover. Look at where it went (the pen is up, nothing is drawn yet), move the paper if needed, then press Resume to start drawing.",
    resume: "Start drawing",
    stop: "Cancel plot",
  },
  paper_change: {
    title: "Change the paper",
    text: () => "Take out the finished sheet and load the next one, then press Resume to plot the next file in the queue.",
    resume: "Resume",
    stop: "Stop queue",
  },
};

function showWaitNotice(state) {
  const notice = WAIT_NOTICES[state.wait_reason];
  jQuery("#waitTitle").text(notice.title);
  jQuery("#waitText").text(notice.text(state));
  jQuery("#waitResume").text(notice.resume).toggleClass("uk-hidden", !notice.resume);
  jQuery("#waitStop").text(notice.stop);
  if (!UIkit.modal("#modal-wait").isToggled()) {
    UIkit.modal("#modal-wait").show();
  }
}

function hideWaitNotice() {
  const modal = UIkit.modal("#modal-wait");
  if (modal.isToggled()) modal.hide();
}

// Plot queue
function addToQueue() {
  const data = plotFormData();
  if (data === null) return;
  const form = new URLSearchParams(data);
  const paper = jQuery("#queuePause").is(":checked") ? "1" : "";
  form.set("pause_after", paper);
  form.set("paper_between", paper);
  const copies = Math.max(1, Math.min(20, parseInt(jQuery("#queueCopies").val(), 10) || 1));
  form.set("copies", String(copies));
  axios
    .post("/queue/add", form)
    .then(function (response) {
      notify(response.data, "success");
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    });
}

function queueAction(route, fields) {
  return axios.post("/queue/" + route, new URLSearchParams(fields || {})).catch(function (error) {
    notify(errorMessage(error), "danger");
    console.error(error);
  });
}

function startQueue() {
  queueAction("start");
}

function clearQueue() {
  UIkit.modal.confirm("Remove every waiting file from the queue?").then(function () {
    queueAction("clear");
  }, function () {});
}

// Draw the queue (sent by the server whenever it changes, and when a page connects)
function renderQueue(queue) {
  const list = jQuery("#queueList").empty();
  queueOrderSent = queue.items.filter(function (item) { return item.status !== "running"; }).map(function (item) { return String(item.id); }).join(",");
  jQuery("#queueEmpty").toggleClass("uk-hidden", queue.items.length > 0);
  jQuery("#queueMessage").text(queue.message || "");
  jQuery(".startQueue").prop("disabled", queue.active || queue.items.length === 0);
  queue.items.forEach(function (item, index) {
    const id = Number(item.id);
    const running = item.status === "running";
    const row = jQuery("<li>").toggleClass("is-running", running).toggleClass("uk-text-muted", !running && queue.active);
    row.toggleClass("is-waiting", !running).attr("data-id", id);
    const number = jQuery("<span class='ftype num'>").text(index + 1);
    if (!running) number.addClass("q-grip").attr("title", "Drag to change the order");
    row.append(number);
    const name = jQuery("<div class='q-name'>").append(jQuery("<span>").text(item.file));
    if (item.pens) name.append(jQuery("<span class='uk-text-small uk-text-muted'>").text(" (pens " + item.pens + ")"));
    row.append(name);
    if (running) {
      row.append(jQuery("<span class='uk-label'>").text("plotting"));
    } else {
      row.append(
        `<div class="q-actions">` +
          `<label><input class="uk-checkbox queuePauseAfter" type="checkbox" data-id="${id}"` +
          `${item.pause_after ? " checked" : ""}> Paper change after</label>` +
          `<a href="#" class="uk-icon-link queueMove" data-id="${id}" data-direction="up" title="Move up" data-uk-icon="icon: arrow-up"></a>` +
          `<a href="#" class="uk-icon-link queueMove" data-id="${id}" data-direction="down" title="Move down" data-uk-icon="icon: arrow-down"></a>` +
          `<a href="#" class="uk-icon-link queueRemove" data-id="${id}" title="Remove" data-uk-icon="icon: close"></a>` +
          `</div>`
      );
    }
    list.append(row);
  });
}

// Dragging the waiting files into a new order (UIkit's sortable; the arrows stay for touch screens and keyboards)
function waitingQueueIds() {
  return jQuery("#queueList li.is-waiting").map(function () { return jQuery(this).attr("data-id"); }).get();
}

var queueOrderSent = "";

function setupQueueSort() {
  const list = document.getElementById("queueList");
  if (!list || !window.UIkit || !UIkit.sortable) return;
  UIkit.sortable(list, { handle: ".q-grip", animation: 150, threshold: 6 });
  UIkit.util.on(list, "stop", function () {
    const ids = waitingQueueIds().join(",");
    if (!ids || ids === queueOrderSent) return;
    queueAction("order", { ids: ids }).then(function (response) {
      if (!response) updateQueue(); // refused (the queue changed meanwhile): show what it is now
    });
  });
}

function updateQueue() {
  return axios
    .get("/queue")
    .then(function (response) {
      renderQueue(response.data);
    })
    .catch(function (error) {
      console.error(error);
    });
}

// Plotter control: move the pen by hand
function plotterAction(action, extra) {
  const port = jQuery("#portList").val();
  if (port == null) {
    notify("No COM port selected", "danger");
    updatePorts();
    return Promise.resolve(null);
  }
  const form = new URLSearchParams({
    port: port,
    baudrate: jQuery("#baudRate").val(),
    flowControl: jQuery("#flowControl").val(),
  });
  const line = lineValues();
  for (const key in line) form.append(key, line[key]);
  for (const key in extra || {}) form.append(key, extra[key]);

  return axios
    .post("/plotter/" + action, form.toString())
    .then(function (response) {
      return response.data;
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
      return null;
    });
}

function jog(button) {
  const step = Number(jQuery("#jogStep").val());
  const dx = Number(jQuery(button).attr("data-dx")) * step;
  const dy = Number(jQuery(button).attr("data-dy")) * step;
  plotterAction("jog", { dx: dx, dy: dy });
}

function showPenPosition() {
  plotterAction("position").then(function (position) {
    if (!position) return;
    jQuery("#penPosition").text(
      "x " + position.x_mm + " mm, y " + position.y_mm + " mm, pen " + (position.pen_down ? "down" : "up")
    );
  });
}

function traceBounds() {
  const file = jQuery("#fileName").val();
  if (!/\.hpgl$/i.test(file)) {
    notify("Select an *.hpgl file to trace", "danger");
    return;
  }
  const draw = jQuery("#traceDraw").prop("checked");
  const run = function () {
    plotterAction("bounds", { file: file, draw: draw ? "1" : "0" });
  };
  if (draw) {
    UIkit.modal.confirm("The pen will be lowered and draw a frame round the plot area. Continue?").then(run, function () {});
  } else {
    run();
  }
}

// Plot history
const HISTORY_LABELS = {
  completed: "uk-label-success",
  stopped: "uk-label-warning",
  failed: "uk-label-danger",
  interrupted: "uk-label-danger",
  running: "",
};

function formatDuration(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return h + " h " + m + " min";
  if (m > 0) return m + " min " + s + " s";
  return s + " s";
}

// Buttons of a history row: the ids are numbers from the server, the labels are fixed text
function historyActions(job) {
  if (job.status == "running") return "";
  const id = Number(job.id);
  let buttons = "";
  if (job.can_resume) {
    // Buffer flow control knows what the plotter had not drawn yet, the others do not
    const buffered = job.flow_control == "CTS/RTS" || job.flow_control == "Software";
    buttons +=
      `<a href="#" class="uk-button uk-button-default uk-button-small resumeJob" data-job="${id}" ` +
      `data-buffered="${buffered ? 1 : 0}" data-interrupted="${job.status == "interrupted" ? 1 : 0}" ` +
      `title="Carry on from where this plot got to">Resume</a>`;
  }
  if (job.can_replot) {
    buttons +=
      `<a href="#" class="uk-button uk-button-default uk-button-small replotJob" data-job="${id}" ` +
      `title="Plot this file again with the same settings">Plot again</a>`;
  }
  return buttons ? `<div class="history-actions">${buttons}</div>` : "";
}

// Resume a stopped plot: the pen carriage and paper must not have moved, and the plotter may
// have a few commands in its buffer that never got drawn, so offer to go back a little
function askResume(id, buffered, interrupted) {
  jQuery("#resumeJobId").val(id);
  // After a power cut or a restart the position is the last one noted (about every 30 s) and the
  // plotter lost what was in its buffer: go back further
  jQuery("#resumeRewind").val(interrupted ? "4096" : buffered ? "0" : "1024");
  jQuery("#resumeInterrupted").toggleClass("uk-hidden", !interrupted);
  UIkit.modal("#modal-resume").show();
}

function resumeJob() {
  const id = jQuery("#resumeJobId").val();
  const rewind = jQuery("#resumeRewind").val();
  UIkit.modal("#modal-resume").hide();
  axios
    .post("/resume_job", new URLSearchParams({ job: id, rewind: rewind }))
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    });
}

function replotJob(id) {
  axios
    .post("/replot", new URLSearchParams({ job: id }))
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    });
}

// How far each pen has drawn since it was last replaced
function updatePenUsage() {
  return axios
    .get("/pen_usage")
    .then(function (response) {
      const rows = jQuery("#penUsageList").empty();
      const pens = response.data.pens;
      jQuery("#penUsageEmpty").toggleClass("uk-hidden", pens.length > 0);
      for (const pen of pens) {
        const row = jQuery("<tr>");
        row.append(jQuery("<td>").text("Pen " + Number(pen.pen)));
        row.append(jQuery("<td class='num'>").text((Number(pen.mm) / 1000).toFixed(1) + " m"));
        row.append(jQuery("<td>").text(Number(pen.plots) + (Number(pen.plots) == 1 ? " plot" : " plots") + " since " + new Date(pen.since * 1000).toLocaleDateString()));
        row.append(
          jQuery("<td>").append(
            jQuery("<a href='#' class='uk-button uk-button-default uk-button-small resetPen' title='A new pen was loaded: count again from zero'>New pen</a>").attr("data-pen", Number(pen.pen))
          )
        );
        rows.append(row);
      }
    })
    .catch(function (error) {
      console.error(error);
    });
}

function resetPen(pen) {
  axios
    .post("/pen_usage/reset", new URLSearchParams({ pen: pen }))
    .then(updatePenUsage)
    .catch(function (error) {
      notify(errorMessage(error), "danger");
    });
}

function updateHistory() {
  updatePenUsage();
  return axios
    .get("/job_history")
    .then(function (response) {
      const rows = jQuery("#historyList").empty();
      jQuery("#historyEmpty").toggleClass("uk-hidden", response.data.length > 0);
      for (const job of response.data) {
        const status = escapeHtml(job.status);
        const label = HISTORY_LABELS[job.status] || "";
        const title = job.error ? ` title="${escapeHtml(job.error)}"` : "";
        const done = job.finished_at ? formatDuration(job.finished_at - job.started_at) : "";
        const percent = job.status == "completed" || job.status == "running" ? "" : ` (${job.progress}%)`;
        rows.append(
          `<tr><td>${escapeHtml(new Date(job.started_at * 1000).toLocaleString())}</td>` +
            `<td>${escapeHtml(job.file)}</td>` +
            `<td><span class="uk-label ${label}"${title}>${status}</span>${escapeHtml(percent)}</td>` +
            `<td>${escapeHtml(done)}</td>` +
            `<td>${historyActions(job)}</td></tr>`
        );
      }
    })
    .catch(function (error) {
      console.error(error);
    });
}

function clearHistory() {
  axios
    .post("/clear_history")
    .then(updateHistory)
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    });
}

// Reboot Pi
function actionReboot() {
  axios
    .post("/action_reboot")
    .then(function (response) {
      // handle success
      if (response.status == 200) {
        UIkit.modal("#modal-reboot").hide();
        notify("Rebooting now", "warning");
      }
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    });
}

// Poweroff Pi
function actionPoweroff() {
  axios
    .post("/action_poweroff")
    .then(function (response) {
      // handle success
      if (response.status == 200) {
        UIkit.modal("#modal-poweroff").hide();
        notify("Poweroff now", "danger");
      }
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    });
}

// Timelapse: pictures of the plot, and the video made from them
var timelapseSettings = { enabled: false, preview: false };
const BUTTON_FIELDS = ["buttons_enable", "button_start_action", "button_stop_action"];
const TIMELAPSE_FIELDS = [
  "timelapse_enable",
  "timelapse_auto_start",
  "timelapse_preview",
  "timelapse_source",
  "timelapse_url",
  "timelapse_interval",
  "timelapse_fps",
  "timelapse_tail",
  "timelapse_keep_frames",
];
const TIMELAPSE_STATES = {
  recording: "Recording",
  rendering: "Making the video",
  video: "Video",
  frames: "Pictures only",
  interrupted: "Interrupted, pictures only",
};

// The "record this plot" box is offered when timelapse is on, and starts ticked when every plot is recorded
function applyTimelapseSettings(data) {
  timelapseSettings.enabled = String(data.timelapse_enable).toLowerCase() == "true";
  timelapseSettings.preview = String(data.timelapse_preview).toLowerCase() == "true";
  jQuery("#timelapse_control").toggleClass("uk-hidden", !timelapseSettings.enabled);
  jQuery("#timelapse").prop("checked", String(data.timelapse_auto_start).toLowerCase() == "true");
}

function showTimelapseFrame(id) {
  if (!timelapseSettings.preview || !id) return;
  jQuery("#timelapseLive").attr("src", "/timelapse/" + encodeURIComponent(id) + "/latest.jpg?t=" + Date.now());
  jQuery("#timelapseLiveBox").removeClass("uk-hidden");
}

function hideTimelapseFrame() {
  jQuery("#timelapseLiveBox").addClass("uk-hidden");
  jQuery("#timelapseLive").removeAttr("src");
}

function timelapseRow(item) {
  const id = escapeHtml(item.id);
  const url = "/timelapse/" + encodeURIComponent(item.id) + "/";
  const when = item.started ? new Date(item.started * 1000).toLocaleString() : item.id.slice(0, 15);
  const details = [when, item.frames + (item.frames == 1 ? " picture" : " pictures"), formatBytes(item.size)];
  const idle = item.state != "recording" && item.state != "rendering";
  let actions = "";
  if (item.video) {
    actions += '<a href="#" class="uk-icon-link timelapsePlay" data-id="' + id + '" title="Play" data-uk-tooltip data-uk-icon="icon: play-circle"></a> ';
    actions += '<a href="' + url + 'timelapse.mp4?download=1" class="uk-icon-link" title="Download the video" data-uk-tooltip data-uk-icon="icon: download"></a> ';
  }
  if (item.zip) {
    actions += '<a href="' + url + 'frames.zip" class="uk-icon-link" title="Download the pictures (zip)" data-uk-tooltip data-uk-icon="icon: camera"></a> ';
    if (item.frames >= 2 && idle) {
      actions += '<a href="#" class="uk-icon-link timelapseRender" data-id="' + id + '" title="Make the video" data-uk-tooltip data-uk-icon="icon: refresh"></a> ';
    }
  }
  if (idle) {
    actions += '<a href="#" class="uk-icon-link timelapseDelete" data-id="' + id + '" title="Delete" data-uk-tooltip data-uk-icon="icon: trash"></a>';
  }
  const picture = item.poster
    ? '<img src="' + url + 'latest.jpg" alt="" loading="lazy" height="40" style="height: 40px; width: 70px; object-fit: cover; margin-right: 10px">'
    : "";
  return (
    '<li class="uk-flex uk-flex-middle">' + picture +
    '<div class="uk-width-expand"><div>' + escapeHtml(item.file || item.id) + "</div>" +
    '<div class="uk-text-small uk-text-muted">' + escapeHtml(details.join(", ")) + ". " + escapeHtml(TIMELAPSE_STATES[item.state] || item.state) + "</div></div>" +
    '<div class="uk-flex-none">' + actions + "</div></li>"
  );
}

function updateTimelapses() {
  return axios
    .get("/timelapses")
    .then(function (response) {
      const data = response.data;
      jQuery("#timelapseList").html(data.items.map(timelapseRow).join(""));
      jQuery("#timelapseEmpty").toggleClass("uk-hidden", data.items.length > 0);
      const notes = [];
      if (!data.enabled) notes.push("Timelapse is switched off in the settings.");
      else if (data.problem) notes.push(data.problem + ".");
      if (!data.ffmpeg) notes.push("ffmpeg is not installed (sudo apt install ffmpeg), so only the pictures are kept.");
      notes.push("Using " + formatBytes(data.usage) + ".");
      jQuery("#timelapseNote").text(notes.join(" "));
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
    });
}

// ---- Plot log ----------------------------------------------------------------------------------

function loadPlotLog() {
  return axios
    .get("/plot_log?lines=400", { responseType: "text", transformResponse: [(d) => d] })
    .then(function (response) {
      const box = jQuery("#plotLogText");
      box.text(response.data || "The log is empty.");
      box.scrollTop(box[0].scrollHeight);
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
    });
}

function openPlotLog() {
  loadPlotLog().then(function () {
    UIkit.modal("#modal-plotlog").show();
    const box = jQuery("#plotLogText");
    box.scrollTop(box[0].scrollHeight);
  });
}

function clearPlotLog() {
  UIkit.modal.confirm("Delete the plot log?").then(function () {
    axios
      .post("/plot_log/clear")
      .then(loadPlotLog)
      .catch(function (error) {
        notify(errorMessage(error), "danger");
      });
  }, function () {});
}

function openTimelapses() {
  jQuery("#timelapsePlayer").addClass("uk-hidden").removeAttr("src");
  updateTimelapses().then(function () {
    UIkit.modal("#modal-timelapse").show();
  });
}

function playTimelapse(id) {
  const player = jQuery("#timelapsePlayer");
  player.attr("src", "/timelapse/" + encodeURIComponent(id) + "/timelapse.mp4").removeClass("uk-hidden");
  const started = player[0].play();
  if (started) started.catch(function () {});
}

function renderTimelapse(id) {
  axios
    .post("/timelapse/render", new URLSearchParams({ id: id }).toString())
    .then(function (response) {
      notify(response.data, "success");
      updateTimelapses();
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
    });
}

function deleteTimelapse(id) {
  UIkit.modal.confirm("Delete this timelapse?").then(function () {
    axios
      .post("/timelapse/delete", new URLSearchParams({ id: id }).toString())
      .then(function () {
        updateTimelapses();
        updateStorage();
      })
      .catch(function (error) {
        notify(errorMessage(error), "danger");
      });
  }, function () {});
}

function testTimelapse() {
  axios
    .post("/timelapse/test", "", { responseType: "blob" })
    .then(function (response) {
      jQuery("#timelapseTest").attr("src", URL.createObjectURL(response.data)).removeClass("uk-hidden");
    })
    .catch(function (error) {
      const data = error.response && error.response.data;
      if (data instanceof Blob) {
        data.text().then(function (text) {
          notify(text || errorMessage(error), "danger");
        });
      } else {
        notify(errorMessage(error), "danger");
      }
    });
}

function actionTasmota() {
  axios
    .post("/action_tasmota")
    .then(function (response) {
      // handle success
      if (response.status == 200) {
        notify("Tasmota Toggled", "success");
      }
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    });
}

// What the forms were set to is kept on the server (userdata/ui_state.json), so a reload or another device shows the
// same values. The file, the chosen pens and the typed text are not part of it.
const UI_STATE_FORMS = { plotter: "#plotterData", convert: "#convertData", text: "#textData" };
// The plot adjustments are for one plot: a speed or an offset that came back by itself would be a surprise
const UI_STATE_SKIP = { plotter: ["file", "pens", "plot_speed", "plot_force", "plot_accel", "offset_x", "offset_y", "frame_check"], convert: ["file"], text: ["text"] };
var uiStateReady = false; // nothing is saved before the saved values have been put back
var uiStateRestoring = false;
var uiStateTimer = null;

function uiStateFields(group) {
  const form = jQuery(UI_STATE_FORMS[group])[0];
  if (!form) return [];
  return Array.from(form.elements).filter(function (el) {
    return el.name && UI_STATE_SKIP[group].indexOf(el.name) < 0 && ["hidden", "button", "submit", "file", "password"].indexOf(el.type) < 0;
  });
}

function collectUiState() {
  const state = {};
  for (const group in UI_STATE_FORMS) {
    const values = {};
    for (const el of uiStateFields(group)) {
      if (el.type === "checkbox") {
        // The optimisation boxes are cleared and disabled by a custom command: what they were is kept aside
        const original = el.getAttribute("data-original");
        values[el.name] = el.disabled && original !== null ? original === "true" : el.checked;
      } else {
        values[el.name] = el.value;
      }
    }
    state[group] = values;
  }
  state.plotter._profile = jQuery("#plotterProfile").val() || "";
  state.plotter._queuePause = jQuery("#queuePause").is(":checked");
  return state;
}

function scheduleUiSave() {
  if (!uiStateReady || uiStateRestoring) return;
  clearTimeout(uiStateTimer);
  uiStateTimer = setTimeout(function () {
    axios.post("/ui_state", collectUiState()).catch(function (error) {
      console.error(error);
    });
  }, 400);
}

// Put the saved values back into the forms, on top of the defaults from the settings
function applyUiState(state) {
  uiStateRestoring = true;
  try {
    const plotter = state.plotter || {};
    const convert = state.convert || {};
    const put = function (group, values) {
      // The device first: it decides which paper sizes there are
      const names = Object.keys(values).sort(function (a, b) { return (b === "device") - (a === "device"); });
      for (const name of names) {
        const field = jQuery(UI_STATE_FORMS[group]).find("[name='" + name + "']");
        if (!field.length) continue;
        if (field.is(":checkbox")) {
          field.prop("checked", !!values[name]);
        } else if (field.is("select")) {
          // A baud rate may not be in the list; anything else must be (a port that is gone stays as it was)
          if (name === "baudrate") setSelect(field, values[name]);
          else if (field.find("option").filter(function () { return this.value === values[name]; }).length) field.val(values[name]);
        } else {
          field.val(values[name]);
        }
        if (name === "device" || name === "flowControl") field.trigger("change");
      }
    };
    put("plotter", plotter);
    jQuery("[form='plotterData'][name='timelapse'], [form='plotterData'][name='tasmota']").each(function () {
      if (this.name in plotter) this.checked = !!plotter[this.name];
    });
    // The chosen plotter is shown, but not applied again: the values above are what the form was set to
    if (plotterProfiles[plotter._profile]) {
      jQuery("#plotterProfile").val(plotter._profile);
      showPlotterNotes(plotter._profile);
    }
    if ("_queuePause" in plotter) jQuery("#queuePause").prop("checked", !!plotter._queuePause);
    if (plotter.timeout || LINE_FIELDS.some(function (key) { return key in plotter && plotter[key] !== LINE_DEFAULTS[key]; })) {
      jQuery("#serialLine").prop("open", true);
    }

    // The optimisation boxes first, then the custom command (which clears and locks them)
    const custom = jQuery("#use_custom_command");
    if (custom.prop("checked")) custom.prop("checked", false).trigger("change");
    put("convert", convert);
    if (custom.prop("checked")) custom.trigger("change");
    updatePageSize();
    put("text", state.text || {});
  } finally {
    uiStateRestoring = false;
  }
}

function loadUiState() {
  return axios
    .get("/ui_state")
    .then(function (response) {
      applyUiState(response.data || {});
    })
    .catch(function (error) {
      console.error(error);
    })
    .then(function () {
      uiStateReady = true;
    });
}

// Fetch config.ini data and update UI
function updateConfiguration() {
  return updateVpypeDevices()
    .then(updatePlotterProfiles)
    .then(function () {
      return axios.get("/save_configfile");
    })
    .then(function (response) {
      // handle success
      if (response.status == 200) {
        jQuery("#telegram_chatid").val(response.data.telegram_chatid);
        jQuery("#tasmota_enable").val(response.data.tasmota_enable);
        jQuery("#tasmota_ip").val(response.data.tasmota_ip);
        jQuery(".plotter_name").html(escapeHtml(response.data.plotter_name));
        jQuery(".portList").val(response.data.plotter_port).change();
        jQuery("#device").val(response.data.plotter_device).change();
        jQuery("#baudRate").val(response.data.plotter_baudrate).change();
        jQuery("#flowControl").val(response.data.plotter_flowControl).change();
        if (response.data.plotter_pen_change) {
          jQuery("#penChange").val(response.data.plotter_pen_change);
        }
        // The default plotter fills in its own settings on top of those
        const profile = response.data.plotter_profile;
        if (profile && plotterProfiles[profile]) {
          jQuery("#plotterProfile").val(profile);
          applyPlotterProfile(profile);
        }

        applyTimelapseSettings(response.data);
        if (String(response.data.tasmota_enable).toLowerCase() == "true") {
          jQuery("#tasmota_control").removeClass("uk-hidden");
        } else {
          jQuery("#tasmota_control").addClass("uk-hidden");
        }
      }
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    })
    .then(scheduleUiSave); // the form now shows the defaults again
}

// The notification settings that are plain fields in the config modal
const NOTIFICATION_FIELDS = [
  "notify_start", "notify_finish", "notify_error", "notify_pen_change", "notify_progress_every",
  "notify_update", "update_check", "convert_separate",
  "webhook_url", "mqtt_host", "mqtt_port", "mqtt_topic", "mqtt_username",
];

// Put a backup zip back
function restoreBackup() {
  const file = jQuery("#restoreFile")[0].files[0];
  if (!file) {
    notify("Choose a backup file first", "danger");
    return;
  }
  UIkit.modal.confirm("Restore " + file.name + "? Its settings, history and files replace the current ones.").then(function () {
    const form = new FormData();
    form.append("backup", file);
    axios
      .post("/restore", form)
      .then(function (response) {
        const r = response.data;
        notify("Restored " + r.config + " settings" + (r.history ? ", the history" : "") + ", " + r.plotters + " plotters, " + r.uploads + " files", "success");
        jQuery("#restoreFile").val("");
        updateFiles();
        updateHistory();
        updateQueue();
        updatePresets();
        updateConfiguration();
      })
      .catch(function (error) {
        notify(errorMessage(error), "danger");
      });
  }, function () {});
}

function testNotification() {
  axios
    .post("/action_test_notification")
    .then(function (response) {
      notify(response.data, "success");
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
    });
}

// Fetch config.ini data and display modal
function actionOpenConfig() {
  axios
    .get("/save_configfile")
    .then(function (response) {
      // handle success
      if (response.status == 200) {
        jQuery("#telegram_chatid").val(response.data.telegram_chatid);
        // The token is never sent to the browser; leaving the field empty keeps it
        jQuery("#telegram_token")
          .val("")
          .attr("placeholder", response.data.telegram_token_set ? "Leave empty to keep the current token" : "");
        jQuery("#telegram_token_remove").prop("checked", false);
        jQuery("#tasmota_enable").val(response.data.tasmota_enable);
        jQuery("#tasmota_ip").val(response.data.tasmota_ip);
        jQuery("#tasmota_on_delay").val(response.data.tasmota_on_delay);
        jQuery("#tasmota_off_delay").val(response.data.tasmota_off_delay);
        for (const field of TIMELAPSE_FIELDS.concat(BUTTON_FIELDS)) {
          jQuery("#" + field).val(response.data[field]);
        }
        for (const field of NOTIFICATION_FIELDS) {
          jQuery("#" + field).val(response.data[field]);
        }
        jQuery("#mqtt_password")
          .val("")
          .attr("placeholder", response.data.mqtt_password_set ? "Leave empty to keep the current password" : "");
        jQuery("#auth_username").val(response.data.auth_username);
        // The password is never sent to the browser; leaving the field empty keeps it
        jQuery("#auth_password")
          .val("")
          .attr("placeholder", response.data.auth_password_set ? "Leave empty to keep the current password" : "");
        jQuery("#plotter_name").val(response.data.plotter_name);
        jQuery("#plotter_port").val(response.data.plotter_port).change();
        jQuery("#plotter_device").val(response.data.plotter_device).change();
        jQuery("#plotter_baudrate")
          .val(response.data.plotter_baudrate)
          .change();
        jQuery("#plotter_flowControl")
          .val(response.data.plotter_flowControl)
          .change();
        jQuery("#plotter_pen_change").val(response.data.plotter_pen_change || "auto");
        jQuery("#plotter_chunk_size").val(response.data.plotter_chunk_size || "0");
        jQuery("#plotter_profile").val(plotterProfiles[response.data.plotter_profile] ? response.data.plotter_profile : "");

        UIkit.modal("#modal-configFile").show();
      }
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    });
}

// Save new values in config.ini
function saveConfig() {
  axios
    .post("/save_configfile", jQuery("#configData").serialize())
    .then(function (response) {
      // handle success
      if (response.status == 200) {
        notify(response.data, "success");
        updateConfiguration();
      }
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    });
}


// ---- Updates ----------------------------------------------------------------------------------

let updateTimer = null;
let updateVersionBefore = null;

// What the server says about versions: the chip in the header, the System panel and the dialog
function showUpdate(info) {
  jQuery(".versionText").text(info.current || "-");
  jQuery("#updateLatest").text(info.latest || "-");
  jQuery("#updateChecked").text(info.checked_at ? "(checked " + new Date(info.checked_at * 1000).toLocaleString() + ")" : "");
  jQuery("#updateChip").toggleClass("uk-hidden", !info.available);

  const state = info.progress.state;
  let message = "";
  if (state === "running") {
    message = "Updating. This page reconnects by itself when the web plotter is back.";
  } else if (state === "failed") {
    message = "The update did not finish. The old version is still in place; the log is below.";
  } else if (info.available) {
    message = "Version " + info.latest + " is available.";
  } else if (info.latest) {
    message = "You have the latest version.";
  } else if (info.error) {
    message = "Could not look for a new version (" + info.error + ").";
  } else {
    message = "Not checked yet.";
  }
  jQuery("#updateMessage").text(message);
  jQuery("#updateWhy").text(info.why_not || "").toggleClass("uk-hidden", info.can_update);
  const changes = info.available ? info.changes || [] : [];
  renderChanges(jQuery("#updateChanges").empty().toggleClass("uk-hidden", changes.length === 0), changes);
  const log = info.progress.log || [];
  jQuery("#updateLog").text(log.join("\n")).toggleClass("uk-hidden", state === "idle" || log.length === 0);
  jQuery(".startUpdate")
    .text(info.available ? "Update to " + info.latest : "Reinstall this version")
    .prop("disabled", !info.can_update || state === "running");
}

function loadUpdateStatus() {
  return axios
    .get("/update/status")
    .then(function (response) {
      showUpdate(response.data);
      if (response.data.progress.state === "running") watchUpdate(response.data.current);
    })
    .catch(function (error) {
      console.error(error); // a missing answer is not worth a message: the page works without it
    });
}

function checkUpdate() {
  jQuery(".checkUpdate").prop("disabled", true);
  return axios
    .post("/update/check")
    .then(function (response) {
      showUpdate(response.data);
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
    })
    .then(function () {
      jQuery(".checkUpdate").prop("disabled", false);
    });
}

function startUpdate() {
  const before = jQuery(".versionText").first().text();
  jQuery(".startUpdate").prop("disabled", true);
  return axios
    .post("/update/start")
    .then(function () {
      notify("Update started", "warning");
      watchUpdate(before);
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      return loadUpdateStatus();
    });
}

// Ask every few seconds until the installer is done: the server goes away while it restarts, then
// comes back with the new version, and the page is loaded again to get the new files.
function watchUpdate(versionBefore) {
  if (updateTimer) return;
  updateVersionBefore = versionBefore;
  jQuery("#updateMessage").text("Updating. This page reconnects by itself when the web plotter is back.");
  updateTimer = setInterval(function () {
    axios
      .get("/update/status", { timeout: 5000 })
      .then(function (response) {
        const info = response.data;
        showUpdate(info);
        const state = info.progress.state;
        if (state === "done" || (info.current && info.current !== updateVersionBefore)) {
          clearInterval(updateTimer);
          location.reload();
        } else if (state === "failed") {
          clearInterval(updateTimer);
          updateTimer = null;
          UIkit.modal("#modal-update").show();
        }
      })
      .catch(function () {
        jQuery("#updateMessage").text("The web plotter is restarting. Waiting for it to come back...");
      });
  }, 3000);
}

// ---- Changelog --------------------------------------------------------------------------------

const SEEN_VERSION_KEY = "webplotter-seen-version";

// Entries as the server parsed them ({version, date, groups: [{title, items}]}), as text only
function renderChanges(container, entries) {
  for (const entry of entries) {
    const box = jQuery("<div>").addClass("uk-margin-small-bottom");
    box.append(jQuery("<h4>").addClass("uk-margin-remove").text("Version " + entry.version + (entry.date ? " (" + entry.date + ")" : "")));
    for (const group of entry.groups) {
      if (group.title) box.append(jQuery("<h5>").addClass("uk-margin-small-top uk-margin-remove-bottom").text(group.title));
      const list = jQuery("<ul>").addClass("uk-list uk-list-bullet uk-margin-small uk-text-small");
      for (const item of group.items) list.append(jQuery("<li>").text(item));
      box.append(list);
    }
    container.append(box);
  }
}

function showChangelog(since) {
  const query = since === "" ? "" : "?since=" + encodeURIComponent(since);
  return axios
    .get("/changelog" + query)
    .then(function (response) {
      const entries = response.data.entries;
      if (!entries.length) {
        notify("There is nothing new to show", "primary");
        return;
      }
      jQuery("#changelogTitle").text(since === "" ? "Changelog" : "What's new");
      renderChanges(jQuery("#changelogBody").empty(), entries);
      UIkit.modal("#modal-changelog").show();
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
    });
}

// After an update: show what changed since this browser last looked. A browser that has never looked
// is shown the current version's notes once. Without somewhere to remember it (blocked storage) it stays
// quiet rather than asking at every load.
function maybeShowChangelog() {
  let seen = null;
  try {
    seen = window.localStorage.getItem(SEEN_VERSION_KEY) || "none";
  } catch (e) {
    return;
  }
  axios
    .get("/changelog?since=" + encodeURIComponent(seen))
    .then(function (response) {
      try {
        window.localStorage.setItem(SEEN_VERSION_KEY, response.data.current || "");
      } catch (e) {
        return;
      }
      if (response.data.entries.length) {
        jQuery("#changelogTitle").text("What's new");
        renderChanges(jQuery("#changelogBody").empty(), response.data.entries);
        UIkit.modal("#modal-changelog").show();
      }
    })
    .catch(function (error) {
      console.error(error); // not worth a message
    });
}
