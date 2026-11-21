import { AfterViewInit, Component, ElementRef, OnDestroy, effect, input, viewChild } from '@angular/core';

/** Perceptual colour map (dark -> purple -> orange -> yellow), 256 entries. */
function buildColormap(): Uint8ClampedArray {
  const stops: [number, number, number, number][] = [
    [0, 8, 10, 16], [0.25, 40, 18, 84], [0.5, 140, 32, 110], [0.7, 222, 76, 64], [0.85, 251, 156, 40], [1, 252, 245, 160],
  ];
  const out = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    let k = 0;
    while (k < stops.length - 2 && t > stops[k + 1][0]) k++;
    const [t0, r0, g0, b0] = stops[k];
    const [t1, r1, g1, b1] = stops[k + 1];
    const u = (t - t0) / (t1 - t0);
    out[i * 3] = r0 + (r1 - r0) * u;
    out[i * 3 + 1] = g0 + (g1 - g0) * u;
    out[i * 3 + 2] = b0 + (b1 - b0) * u;
  }
  return out;
}

const CMAP = buildColormap();

/**
 * Scrolling spectrogram drawn on a canvas. Columns arrive pre-computed from the
 * backend (log-frequency, uint8-quantised dB); the browser only maps values to colours.
 * Rendering uses an offscreen canvas of (history x bins) scaled to the element.
 */
@Component({
  selector: 'app-spectrogram',
  template: `<canvas #cv></canvas>
    <div class="axis">@for (t of ticks(); track t.label) {<span [style.bottom.%]="t.pos">{{ t.label }}</span>}</div>`,
  styles: [`:host{display:block;position:relative;background:#080b10;border-radius:6px;overflow:hidden}
    canvas{position:absolute;inset:0;width:100%;height:100%;image-rendering:auto}
    .axis{position:absolute;left:4px;top:0;bottom:0;pointer-events:none}
    .axis span{position:absolute;left:0;transform:translateY(50%);font-size:10px;color:#cdd6e4;text-shadow:0 0 3px #000}`],
})
export class Spectrogram implements AfterViewInit, OnDestroy {
  readonly columns = input<number[][]>([]);
  readonly version = input(0);
  readonly history = input(120);
  readonly freqs = input<number[]>([]);

  private cv = viewChild<ElementRef<HTMLCanvasElement>>('cv');
  private off = document.createElement('canvas');
  private ro: ResizeObserver | null = null;

  constructor() {
    effect(() => {
      this.version();
      this.draw();
    });
  }

  ticks(): { label: string; pos: number }[] {
    const f = this.freqs();
    if (f.length < 2) return [];
    const lo = Math.log10(f[0]);
    const hi = Math.log10(f[f.length - 1]);
    return [100, 1000, 10000].filter((x) => x > f[0] && x < f[f.length - 1])
      .map((x) => ({ label: x >= 1000 ? `${x / 1000}k` : `${x}`, pos: ((Math.log10(x) - lo) / (hi - lo)) * 100 }));
  }

  ngAfterViewInit(): void {
    this.ro = new ResizeObserver(() => this.blit());
    const el = this.cv()?.nativeElement;
    if (el) this.ro.observe(el);
    this.draw();
  }

  ngOnDestroy(): void {
    this.ro?.disconnect();
  }

  private draw(): void {
    // Full repaint each update: history x bins is ~15k pixels, about 1 ms - simpler and
    // robust against coalesced signal updates.
    const cols = this.columns();
    const H = cols[0]?.length ?? 0;
    const W = this.history();
    if (!H || !this.cv()) {
      this.clear();
      return;
    }
    const ctx = this.off.getContext('2d');
    if (!ctx) return;
    if (this.off.width !== W || this.off.height !== H) {
      this.off.width = W;
      this.off.height = H;
    }
    ctx.fillStyle = '#080b10';
    ctx.fillRect(0, 0, W, H);
    this.paint(ctx, cols.slice(-W), W - Math.min(cols.length, W));
    this.blit();
  }

  private paint(ctx: CanvasRenderingContext2D, cols: number[][], x0: number): void {
    if (!cols.length) return;
    const H = cols[0].length;
    const img = ctx.createImageData(cols.length, H);
    for (let x = 0; x < cols.length; x++) {
      const c = cols[x];
      for (let y = 0; y < H; y++) {
        const v = c[H - 1 - y] | 0; // low frequencies at the bottom
        const o = (y * cols.length + x) * 4;
        img.data[o] = CMAP[v * 3];
        img.data[o + 1] = CMAP[v * 3 + 1];
        img.data[o + 2] = CMAP[v * 3 + 2];
        img.data[o + 3] = 255;
      }
    }
    ctx.putImageData(img, Math.max(0, x0), 0);
  }

  private clear(): void {
    const el = this.cv()?.nativeElement;
    if (!el) return;
    el.getContext('2d')?.clearRect(0, 0, el.width, el.height);
  }

  private blit(): void {
    const el = this.cv()?.nativeElement;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(r.width * dpr));
    const h = Math.max(1, Math.round(r.height * dpr));
    if (el.width !== w || el.height !== h) {
      el.width = w;
      el.height = h;
    }
    const ctx = el.getContext('2d');
    if (!ctx || !this.off.width) return;
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(this.off, 0, 0, w, h);
  }
}
