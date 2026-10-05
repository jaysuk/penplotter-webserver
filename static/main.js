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

        for (var content of response.data.content) {
          jQuery("#fileList").append(
            `<li> ${renderFileListElement(content.name)} </li>`
          );
        }
      }
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    })
    .then(function () {});
}

// Handle file selection
function selectFile(element) {
  const filename = jQuery(element).attr("data-filename");

  // Update form
  jQuery("#fileName").val(filename);

  // Update list
  jQuery("#fileList li").removeClass("uk-alert-primary");
  const li = jQuery(element).parents("li")[0];
  if (li) jQuery(li).addClass("uk-alert-primary");

  // Update sidebar
  jQuery(".selectedFilename").text(filename);

  loadFileInfo(filename);
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
  showPreview(filename, "/uploads/" + encodeURIComponent(filename), null);
}

// Show a drawing in the preview dialog. `conversion` is {name, summary} for a conversion that has
// not been saved yet (it can be saved or abandoned from the dialog), null for a file in the list.
var previewedConversion = null;

function showPreview(title, url, conversion) {
  previewedConversion = conversion;
  jQuery("#previewFileName").text(title);
  jQuery("#previewInfo").text("Loading...");
  jQuery("#previewSummary").text(conversion ? describeSummary(conversion.summary) : "");
  jQuery("#previewActions").toggleClass("uk-hidden", !conversion);

  const open = function () {
    // The canvas is sized from its container, which only has a width once the modal is visible
    UIkit.util.once("#modal-previewFile", "shown", function () {
      loadPreview(url);
    });
    UIkit.modal("#modal-previewFile").show();
  };
  // A dialog that is still closing would swallow the new one
  const convertModal = UIkit.modal("#modal-convertFile");
  if (convertModal.isToggled()) {
    UIkit.util.once("#modal-convertFile", "hidden", open);
    convertModal.hide();
  } else {
    open();
  }
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

function loadPreview(url) {
  axios
    .get(url, {
      responseType: "text",
      transformResponse: [(data) => data], // keep the raw text, don't try to parse JSON
      params: { _: Date.now() }, // always show the latest version of a re-converted file
    })
    .then(function (response) {
      if (response.data.length > PREVIEW_MAX_BYTES) {
        jQuery("#previewInfo").text("File is too large to preview");
        return;
      }

      if (!hpglViewer) {
        hpglViewer = new HPGLViewer(document.getElementById("hpglCanvas"));
      }
      const stats = hpglViewer.loadHPGL(response.data);

      if (stats.paths == 0) {
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
    })
    .catch(function (error) {
      jQuery("#previewInfo").text("Preview failed: " + errorMessage(error));
      console.error(error);
    });
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
      showPreview(name, "/preview_files/" + encodeURIComponent(name), response.data);
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
      updateFiles();
      UIkit.modal("#modal-previewFile").hide();
    })
    .catch(function (error) {
      notify(errorMessage(error), "danger");
      console.error(error);
    });
}

// Back from the preview to the conversion options
function backToConvert() {
  const preview = UIkit.modal("#modal-previewFile");
  UIkit.util.once("#modal-previewFile", "hidden", function () {
    UIkit.modal("#modal-convertFile").show();
  });
  preview.hide();
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

// Display card
function closeCard(element) {
  const card = jQuery(element).data("card");

  jQuery(element).addClass("uk-hidden");
  jQuery("#" + card).addClass("uk-hidden");
  jQuery(".showCard[data-card='" + card + "']").removeClass("uk-hidden");
}

function showCard(element) {
  const card = jQuery(element).data("card");

  jQuery(element).addClass("uk-hidden");
  jQuery("#" + card).removeClass("uk-hidden");
  jQuery(".closeCard[data-card='" + card + "']").removeClass("uk-hidden");
}

// Clear Logs
function clearLog() {
  // Remove old content from log
  jQuery("#statusLog").html("");
  jQuery("#bytes_written").html("");
}

// Start plotting
function startPlot() {
  const plotterData = jQuery("#plotterData").serializeArray();
  console.log("plotterData", plotterData);

  // Validation
  if (jQuery("#fileName").val() == "") {
    notify("No *.hpgl file selected", "danger");
    return false;
  }
  if (jQuery("#portList").val() == null) {
    notify("No COM port selected", "danger");
    updatePorts();
    return false;
  }
  if (jQuery(".penChoice").length > 0 && jQuery(".penChoice:checked").length === 0) {
    notify("Select at least one pen to plot", "danger");
    return false;
  }
  updatePenSelection();

  axios
    .post("/start_plot", jQuery("#plotterData").serialize())
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
function applyPlotState(state) {
  jQuery(".pausePlot").toggleClass("uk-hidden", !!state.paused);
  jQuery(".resumePlot").toggleClass("uk-hidden", !state.paused);

  // Held back for a manual pen change: ask for the pen, and close the question when it is over
  if (state.running && state.paused && state.wait_reason === "pen_change") {
    showPenChange(state.pen);
  } else {
    hidePenChange();
  }
  setEta(state.running ? state.eta : null);

  if (state.running && state.file) {
    jQuery(".selectedFilename").text(state.file);
  }
  jQuery(".printProgress").val(state.progress || 0);
  if (state.bytes_written !== undefined) {
    jQuery("#bytes_written").text(state.bytes_written);
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

// Time left
function setEta(eta) {
  const text = eta ? "Time left: about " + formatDuration(eta.remaining) + " (of " + formatDuration(eta.total) + ")" : "";
  jQuery(".etaText").text(text);
}

// Pen change dialog
function showPenChange(pen) {
  jQuery("#penChangeNumber").text(pen);
  if (!UIkit.modal("#modal-penChange").isToggled()) {
    UIkit.modal("#modal-penChange").show();
  }
}

function hidePenChange() {
  const modal = UIkit.modal("#modal-penChange");
  if (modal.isToggled()) modal.hide();
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
            `<td>${escapeHtml(done)}</td></tr>`
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
