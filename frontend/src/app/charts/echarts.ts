// Tree-shaken ECharts build: only what the dashboard uses.
import { BarChart, LineChart, ScatterChart } from 'echarts/charts';
import { GridComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent } from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';

echarts.use([LineChart, BarChart, ScatterChart, GridComponent, TooltipComponent, MarkLineComponent,
  MarkAreaComponent, CanvasRenderer]);

export { echarts };

export const AXIS_STYLE = {
  axisLine: { lineStyle: { color: '#34445e' } },
  axisTick: { lineStyle: { color: '#34445e' } },
  axisLabel: { color: '#8b98ab', fontSize: 11 },
  splitLine: { lineStyle: { color: '#1b2433' } },
};
