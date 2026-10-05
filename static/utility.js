// Escape text for safe use in HTML content and attributes
function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Readable message from an axios / jQuery / generic error
function errorMessage(error) {
  if (error && error.response) {
    // axios: prefer the message sent by the server
    if (typeof error.response.data === 'string' && error.response.data) {
      return error.response.data;
    }
    return error.response.status + ' ' + error.response.statusText;
  }
  if (error && error.responseText) return error.responseText; // jQuery
  if (error && error.message) return error.message;
  return String(error);
}

// 1536 -> "1.5 KB"
function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return '?';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return (unit === 0 ? value.toFixed(0) : value.toFixed(value < 10 ? 1 : 0)) + ' ' + units[unit];
}

// Seconds since a file was last written, as "today", "3 days"
function formatAge(seconds) {
  const days = Math.floor(seconds / 86400);
  if (days < 1) return 'today';
  if (days < 60) return days + (days === 1 ? ' day' : ' days');
  if (days < 730) return Math.floor(days / 30) + ' months';
  return Math.floor(days / 365) + ' years';
}

// Nicer format for file list
function renderFileListElement(name, info) {

  // Get the file extension
  const re = /(?:\.([^.]+))?$/;
  const ext = re.exec(name)[1];
  const label = escapeHtml(name);
  const details = info ? ` <span class="uk-text-small uk-text-muted">${escapeHtml(info)}</span>` : '';
  let html = ''

  switch (ext) {
    case 'hpgl':
      html = `<div class="uk-grid uk-grid-small">
                <div class="uk-width-expand">
                  <a href="#" class="selectFile" data-filename="${label}">
                    <span>${label}</span>
                  </a>${details}
                </div>
                <div class="uk-width-auto uk-text-right panel-icons">
                  <a href="#" class="uk-icon-link previewFile" data-filename="${label}" title="Preview" data-uk-tooltip data-uk-icon="icon: image"></a>
                  <a href="#" class="uk-icon-link deleteFile lock-edit" data-filename="${label}" title="Delete" data-uk-tooltip data-uk-icon="icon: close"></a>
                </div>
              </div>`;
        break;
    case 'svg':
      html = `<div class="uk-grid uk-grid-small">
                <div class="uk-width-expand">
                  <a href="#" class="no-selectFile" data-filename="${label}">
                    <span>${label}</span>
                  </a>${details}
                </div>
                <div class="uk-width-auto uk-text-right panel-icons">
                  <a href="#" class="uk-icon-link convertFile lock-edit" data-filename="${label}" title="Convert to HPGL" data-uk-tooltip data-uk-icon="icon: bolt"></a>
                  <a href="#" class="uk-icon-link deleteFile lock-edit" data-filename="${label}" title="Delete" data-uk-tooltip data-uk-icon="icon: close"></a>
                </div>
              </div>`;
        break;
    default:
      html = `<div class="uk-grid uk-grid-small">
                <div class="uk-width-expand">
                  <a href="#" class="no-selectFile" data-filename="${label}">
                    <span>${label}</span>
                  </a>${details}
                </div>
                <div class="uk-width-auto uk-text-right panel-icons">
                  <a href="#" class="uk-icon-link deleteFile lock-edit" data-filename="${label}" title="Delete" data-uk-tooltip data-uk-icon="icon: close"></a>
                </div>
              </div>`;
  }

  return html;
}

// Simplify notification handling
function notify(message, status) {
    UIkit.notification({
      message: escapeHtml(message),
      status: status,
      pos: 'top-right',
      timeout: 5000
  });
}

function scrollLog() {
  jQuery('.auto-scroll').each(function( index ) {
    jQuery(this).animate({
      scrollTop: jQuery(this)[0].scrollHeight
    }, 10);
  });
}
