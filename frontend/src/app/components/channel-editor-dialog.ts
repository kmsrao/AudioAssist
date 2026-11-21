import { Component, OnInit, inject, input, output, signal } from '@angular/core';

import { pad2 } from '../format';
import { ChannelConfig, Profile } from '../models';
import { ApiService, errorMessage } from '../services/api.service';

/** Edit channel names, profiles, groups, enabled state and the group list. */
@Component({
  selector: 'app-channel-editor-dialog',
  template: `
    <div class="overlay" (click)="close.emit()">
      <div class="dialog" (click)="$event.stopPropagation()" role="dialog" aria-modal="true">
        <div class="dialog-head"><h2>Channel mapping &amp; profiles</h2><span class="grow"></span>
          <button class="btn ghost" (click)="close.emit()" aria-label="Close">✕</button></div>
        <div class="dialog-body">
          @if (error()) { <div class="notice bad">{{ error() }}</div> }
          @if (saved()) { <div class="notice">Saved.</div> }
          <p class="dim" style="margin-top:0">Each row is one input of the audio interface. The profile gives the analyser context
            (expected tonal balance, which issues matter); it never applies EQ.</p>
          <table class="tbl">
            <thead><tr><th>Input</th><th>Name</th><th>Profile</th><th>Group</th><th>Enabled</th><th>Analyse</th></tr></thead>
            <tbody>
              @for (c of rows(); track c.index; let i = $index) {
                <tr>
                  <td class="num">CH{{ pad(c.number) }}</td>
                  <td><input class="input sm" style="width:100%" [value]="c.name" maxlength="64" (input)="patch(i, { name: $any($event.target).value })" /></td>
                  <td><select class="select sm" [value]="c.profile" (change)="patch(i, { profile: $any($event.target).value })">
                    @for (p of profiles(); track p.key) { <option [value]="p.key" [selected]="p.key === c.profile">{{ p.name }}</option> }
                  </select></td>
                  <td><select class="select sm" [value]="c.group" (change)="patch(i, { group: $any($event.target).value })">
                    @for (g of groupList(); track g) { <option [value]="g" [selected]="g === c.group">{{ g }}</option> }
                  </select></td>
                  <td><input type="checkbox" [checked]="c.enabled" (change)="patch(i, { enabled: $any($event.target).checked })" /></td>
                  <td><input type="checkbox" [checked]="c.selected" (change)="patch(i, { selected: $any($event.target).checked })" /></td>
                </tr>
              }
            </tbody>
          </table>

          <h3 style="font-size:12px;text-transform:uppercase;color:var(--text-dim);margin:18px 0 6px">Groups</h3>
          <div class="row wrap">
            @for (g of groupList(); track g; let i = $index) {
              <span class="tag">{{ g }} <button class="btn ghost sm" style="height:18px;padding:0 4px" (click)="removeGroup(i)" [disabled]="groupList().length <= 1">✕</button></span>
            }
            <input class="input sm" placeholder="New group" #ng (keydown.enter)="addGroup(ng.value); ng.value = ''" />
            <button class="btn sm" (click)="addGroup(ng.value); ng.value = ''">Add</button>
          </div>
        </div>
        <div class="dialog-foot">
          <button class="btn" (click)="close.emit()">Cancel</button>
          <button class="btn primary" (click)="save()" [disabled]="busy()">Save configuration</button>
        </div>
      </div>
    </div>
  `,
})
export class ChannelEditorDialog implements OnInit {
  private api = inject(ApiService);
  readonly channels = input<ChannelConfig[]>([]);
  readonly profiles = input<Profile[]>([]);
  readonly groups = input<string[]>([]);
  readonly close = output<void>();

  readonly rows = signal<ChannelConfig[]>([]);
  readonly groupList = signal<string[]>([]);
  readonly error = signal<string | null>(null);
  readonly saved = signal(false);
  readonly busy = signal(false);
  pad = pad2;

  ngOnInit(): void {
    this.rows.set(this.channels().map((c) => ({ ...c })));
    this.groupList.set([...this.groups()]);
  }

  patch(i: number, p: Partial<ChannelConfig>): void {
    this.saved.set(false);
    this.rows.update((r) => r.map((c, j) => (j === i ? { ...c, ...p } : c)));
  }

  addGroup(name: string): void {
    const n = name.trim();
    if (n && !this.groupList().includes(n)) this.groupList.update((g) => [...g, n]);
  }

  removeGroup(i: number): void {
    const removed = this.groupList()[i];
    const rest = this.groupList().filter((_, j) => j !== i);
    this.groupList.set(rest);
    this.rows.update((r) => r.map((c) => (c.group === removed ? { ...c, group: rest[rest.length - 1] } : c)));
  }

  async save(): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      await this.api.saveGroups(this.groupList());
      await this.api.saveChannels(this.rows().map((c) => ({ ...c, name: c.name.trim() || `Input ${c.number}` })));
      this.saved.set(true);
    } catch (e) {
      this.error.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}
