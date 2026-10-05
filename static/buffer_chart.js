var buffer_space = 0;
var buffer_size = 1;
var _buffer_space = new TimeSeries();
var _buffer_chart = null;

createTimeline();

// A colour from the page's theme (theme.css), "#rrggbb" turned into rgba() when an alpha is given
function chartColour(name, fallback, alpha) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  const hex = /^#([0-9a-f]{6})$/i.exec(value);
  if (!hex || alpha === undefined) return value;
  const n = parseInt(hex[1], 16);
  return "rgba(" + (n >> 16) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + alpha + ")";
}

// Create the chart once; later calls just reset it for a new plot
function createTimeline() {
  const line = chartColour("--accent", "#03a9f4");
  const labels = chartColour("--muted", "#888888");
  if (_buffer_chart === null) {
    _buffer_chart = new SmoothieChart({millisPerPixel:11,grid:{fillStyle:'transparent',strokeStyle:'transparent',borderVisible:false},labels:{fillStyle:labels,fontSize:12,precision:0},maxValue:buffer_size,minValue:0});
    _buffer_chart.addTimeSeries(_buffer_space, {lineWidth:2,strokeStyle:line,fillStyle:chartColour("--accent", "#03a9f4", 0.18)});
    _buffer_chart.streamTo(document.getElementById("chart"), 500);
  }
  // The theme may have changed since the chart was made
  _buffer_chart.options.labels.fillStyle = labels;
  _buffer_chart.seriesSet[0].options.strokeStyle = line;
  _buffer_chart.seriesSet[0].options.fillStyle = chartColour("--accent", "#03a9f4", 0.18);
  _buffer_chart.options.maxValue = buffer_size;
  _buffer_space.clear();
}
