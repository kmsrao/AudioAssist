import { AfterViewInit, Component, ElementRef, OnDestroy, effect, input, viewChild } from '@angular/core';

import { fmtHz } from '../format';
import { TrackedPeak } from '../models';
import { AXIS_STYLE, echarts } from './echarts';

export interface SpectrumMarker { freq: number; label: string; color: string }

/** Live spectrum: smoothed level (area), instantaneous max-hold (thin line), persistent peaks and recommendation targets. */
@Component({
  selector: 'app-spectrum-chart',
  template: `<div #host class="chart"></div>`,
  styles: [`:host{display:block;position:relative} .chart{position:absolute;inset:0}`],
})
export class SpectrumChart implements AfterViewInit, OnDestroy {
  readonly freqs = input<number[]>([]);
  readonly db = input<number[]>([]);
  readonly maxhold = input<number[]>([]);
  readonly peaks = input<TrackedPeak[]>([]);
  readonly markers = input<SpectrumMarker[]>([]);

  private host = viewChild.required<ElementRef<HTMLDivElement>>('host');
  private chart: echarts.ECharts | null = null;
  private ro: ResizeObserver | null = null;

  constructor() {
    effect(() => this.render(this.freqs(), this.db(), this.maxhold(), this.peaks(), this.markers()));
  }

  ngAfterViewInit(): void {
    this.chart = echarts.init(this.host().nativeElement, undefined, { renderer: 'canvas' });
    this.chart.setOption({
      animation: false,
      grid: { left: 44, right: 12, top: 12, bottom: 26 },
      tooltip: {
        trigger: 'axis', backgroundColor: '#161c27', borderColor: '#34445e', textStyle: { color: '#e3e9f2', fontSize: 12 },
        formatter: (p: { value: [number, number]; seriesName: string }[]) => {
          if (!p.length) return '';
          const f = p[0].value[0];
          return `${fmtHz(f)}<br/>` + p.filter((x) => x.seriesName !== 'Peaks')
            .map((x) => `${x.seriesName}: ${x.value[1].toFixed(1)} dBFS`).join('<br/>');
        },
      },
      xAxis: {
        type: 'log', logBase: 10, min: 20, max: 20000, ...AXIS_STYLE,
        axisLabel: { ...AXIS_STYLE.axisLabel, formatter: (v: number) => (v >= 1000 ? `${v / 1000}k` : `${v}`) },
      },
      yAxis: { type: 'value', min: -110, max: 0, interval: 20, ...AXIS_STYLE, name: 'dBFS', nameGap: 6,
        nameTextStyle: { color: '#66748a', fontSize: 10 } },
      series: [
        { name: 'Level', type: 'line', showSymbol: false, smooth: false, data: [],
          lineStyle: { color: '#4ea8ff', width: 2 },
          areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(78,168,255,.35)' }, { offset: 1, color: 'rgba(78,168,255,.02)' }]) } },
        { name: 'Max', type: 'line', showSymbol: false, data: [], lineStyle: { color: 'rgba(255,255,255,.28)', width: 1 } },
        { name: 'Peaks', type: 'scatter', data: [], symbolSize: 9, itemStyle: { color: '#fbbf24' }, z: 5 },
      ],
    });
    this.ro = new ResizeObserver(() => this.chart?.resize());
    this.ro.observe(this.host().nativeElement);
    this.render(this.freqs(), this.db(), this.maxhold(), this.peaks(), this.markers());
  }

  ngOnDestroy(): void {
    this.ro?.disconnect();
    this.chart?.dispose();
  }

  private render(freqs: number[], db: number[], mh: number[], peaks: TrackedPeak[], markers: SpectrumMarker[]): void {
    if (!this.chart || !freqs.length) return;
    const pair = (arr: number[]) => arr.map((v, i) => [freqs[i], Math.max(-140, v)]);
    this.chart.setOption({
      series: [
        { data: pair(db),
          markLine: { silent: true, symbol: 'none', animation: false,
            data: markers.map((m) => ({ xAxis: m.freq, lineStyle: { color: m.color, type: 'dashed', width: 1.5 },
              label: { formatter: m.label, color: m.color, fontSize: 11, position: 'insideEndTop' } })) } },
        { data: pair(mh) },
        { data: peaks.map((p) => [p.freq_hz, p.level_db]) },
      ],
    });
  }
}
