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

  jQuery("#previewFileName").text(filename);
  jQuery("#previewInfo").text("Loading...");

  // The canvas is sized from its container, which only has a width once the modal is visible
  UIkit.util.once("#modal-previewFile", "shown", function () {
    loadPreview(filename);
  });
  UIkit.modal("#modal-previewFile").show();
}

// Preview files bigger than this would freeze a phone while parsing
const PREVIEW_MAX_BYTES = 30 * 1024 * 1024;

function loadPreview(filename) {
  axios
    .get("/uploads/" + encodeURIComponent(filename), {
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
