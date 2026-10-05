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
      { selected_port: jQuery("#portList").val() },
      function (response) {
        console.log(response);
        if (response != "None") {
          // change to new baudrate value
          $(".baudRate").val(response);
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
        disk + "Files " + formatBytes(s.uploads) + ", cache " + formatBytes(s.cache) + ", history " + formatBytes(s.history) + "."
      );
      // Running low is worth a warning colour
      jQuery("#storageInfo").toggleClass("uk-text-danger", s.total !== null && s.free < 200 * 1024 * 1024);
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
  if (!/\.hpgl$/i.test(filename)) return;

  const request = ++fileInfoRequest;
  axios
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
      const stats = hpglViewer.loadHPGL(response.data);
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
  const canChoose = pens.length > 1 && /\.hpgl$/i.test(previewedFile || "") && !previewedConversion && !watchingPlot &&
    jQuery("#fileName").val() === previewedFile && jQuery(".penChoice").length > 0;
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
  jQuery(".penChoice").each(function () {
    this.checked = shown.has(this.value);
  });
  updatePenSelection();
  notify("Plotting only the pens shown", "success");
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
function updatePageSize(element) {
  // TODO add pagesize filter for the machines
  ////////////////////////////////////////////////
  ////////////////////////////////////////////////
}

// Handle file conversion
function convertFileModal(element) {
  const filename = jQuery(element).attr("data-filename");
  jQuery("#convertFile").val(filename);
  updatePresets();
  UIkit.modal("#modal-convertFile").show();
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
    notify("No *.hpgl file selected", "danger");
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
  form.set("pause_after", jQuery("#queuePause").is(":checked") ? "1" : "");
  axios
    .post("/queue/add", form)
    .then(function () {
      notify("Added to the queue", "success");
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
  jQuery("#queueEmpty").toggleClass("uk-hidden", queue.items.length > 0);
  jQuery("#queueMessage").text(queue.message || "");
  jQuery(".startQueue").prop("disabled", queue.active || queue.items.length === 0);
  queue.items.forEach(function (item, index) {
    const id = Number(item.id);
    const running = item.status === "running";
    const row = jQuery("<li>").toggleClass("is-running", running).toggleClass("uk-text-muted", !running && queue.active);
    row.append(jQuery("<span class='ftype num'>").text(index + 1));
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
      `data-buffered="${buffered ? 1 : 0}" title="Carry on from where this plot got to">Resume</a>`;
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
function askResume(id, buffered) {
  jQuery("#resumeJobId").val(id);
  jQuery("#resumeRewind").val(buffered ? "0" : "1024");
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

function updateHistory() {
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

// Fetch config.ini data and update UI
function updateConfiguration() {
  return axios
    .get("/save_configfile")
    .then(function (response) {
      // handle success
      if (response.status == 200) {
        jQuery("#telegram_token").val(response.data.telegram_token);
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
    });
}

// The notification settings that are plain fields in the config modal
const NOTIFICATION_FIELDS = [
  "notify_start", "notify_finish", "notify_error", "notify_pen_change", "notify_progress_every",
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
        notify("Restored " + r.config + " settings" + (r.history ? ", the history" : "") + ", " + r.uploads + " files", "success");
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
        jQuery("#telegram_token").val(response.data.telegram_token);
        jQuery("#telegram_chatid").val(response.data.telegram_chatid);
        jQuery("#tasmota_enable").val(response.data.tasmota_enable);
        jQuery("#tasmota_ip").val(response.data.tasmota_ip);
        jQuery("#tasmota_on_delay").val(response.data.tasmota_on_delay);
        jQuery("#tasmota_off_delay").val(response.data.tasmota_off_delay);
        jQuery("#timelapse_enable").val(response.data.timelapse_enable);
        jQuery("#timelapse_auto_start").val(response.data.timelapse_auto_start);
        jQuery("#timelapse_preview").val(response.data.timelapse_preview);
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
  const configData = jQuery("#configData").serializeArray();
  console.log("configData", configData);

  axios
    .post("/save_configfile", jQuery("#configData").serialize())
    .then(function (response) {
      console.log(response);
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
