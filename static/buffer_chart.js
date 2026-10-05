var buffer_space = 0;
var buffer_size = 1;
var _buffer_space = new TimeSeries();
var _buffer_chart = null;

createTimeline();

// Create the chart once; later calls just reset it for a new plot
function createTimeline() {
  if (_buffer_chart === null) {
    _buffer_chart = new SmoothieChart({millisPerPixel:11,grid:{fillStyle:'transparent',strokeStyle:'transparent',borderVisible:false},labels:{fillStyle:'#fff',fontSize:15,precision:0},maxValue:buffer_size,minValue:0});
    _buffer_chart.addTimeSeries(_buffer_space, {lineWidth:2,strokeStyle:'#fff',fillStyle:'#03a9f4'});
    _buffer_chart.streamTo(document.getElementById("chart"), 500);
  }
  _buffer_chart.options.maxValue = buffer_size;
  _buffer_space.clear();
}
