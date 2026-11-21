import { AfterViewInit, Component, ElementRef, OnDestroy, effect, input, viewChild } from '@angular/core';

import { Band } from '../models';
import { AXIS_STYLE, echarts } from './echarts';

/** Frequency-band energy (analytical bands, not EQ suggestions). */
@Component({
  selector: 'app-band-chart',
  template: `<div #host class="chart"></div>`,
  styles: [`:host{display:block;position:relative} .chart{position:absolute;inset:0}`],
})
export class BandChart implements AfterViewInit, OnDestroy {
  readonly bands = input<Band[]>([]);
  readonly highlight = input<number[]>([]); // target frequencies to highlight

  private host = viewChild.required<ElementRef<HTMLDivElement>>('host');
  private chart: echarts.ECharts | null = null;
  private ro: ResizeObserver | null = null;

  constructor() {
    effect(() => this.render(this.bands(), this.highlight()));
  }

  ngAfterViewInit(): void {
    this.chart = echarts.init(this.host().nativeElement);
    this.chart.setOption({
      animation: false,
      grid: { left: 40, right: 8, top: 10, bottom: 36 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: '#161c27', borderColor: '#34445e',
        textStyle: { color: '#e3e9f2', fontSize: 12 }, valueFormatter: (v: number) => `${v.toFixed(1)} dBFS` },
      xAxis: { type: 'category', data: [], ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, fontSize: 10, interval: 0, rotate: 35 } },
      yAxis: { type: 'value', min: -100, max: 0, interval: 25, ...AXIS_STYLE },
      series: [{ type: 'bar', data: [], barWidth: '62%', itemStyle: { borderRadius: [3, 3, 0, 0] } }],
    });
    this.ro = new ResizeObserver(() => this.chart?.resize());
    this.ro.observe(this.host().nativeElement);
    this.render(this.bands(), this.highlight());
  }

  ngOnDestroy(): void {
    this.ro?.disconnect();
    this.chart?.dispose();
  }

  private render(bands: Band[], hl: number[]): void {
    if (!this.chart || !bands.length) return;
    this.chart.setOption({
      xAxis: { data: bands.map((b) => b.label) },
      series: [{
        data: bands.map((b) => ({
          value: Math.max(-100, b.level_db),
          itemStyle: { color: hl.some((f) => f >= b.lo && f < b.hi) ? '#fb923c' : '#3d7fc4' },
        })),
      }],
    });
  }
}
