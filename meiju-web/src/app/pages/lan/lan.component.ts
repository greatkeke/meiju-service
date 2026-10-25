import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { LanDevice, LanDiscovered, LanService, LanStatus } from '../../core/lan.service';

@Component({
  selector: 'mj-lan',
  imports: [FormsModule],
  templateUrl: './lan.component.html',
  styleUrl: './lan.component.scss',
})
export class LanComponent {
  private readonly lan = inject(LanService);
  private readonly router = inject(Router);

  readonly cached = signal<LanDevice[]>([]);
  readonly discovered = signal<LanDiscovered[]>([]);
  readonly status = signal<LanStatus | null>(null);
  readonly selected = signal<string>('');
  readonly busy = signal('');
  readonly error = signal('');
  readonly note = signal('');
  readonly cacheState = signal<{ valid: boolean; count: number; age: number | null; account: string | null } | null>(
    null,
  );

  readonly attrName = signal('');
  readonly attrValue = signal('');
  readonly lastResult = signal('');

  readonly attributes = computed(() => {
    const attrs = this.status()?.attributes ?? {};
    return Object.entries(attrs).sort(([a], [b]) => a.localeCompare(b));
  });

  /** 只列出该机型真正能下发的属性，避免选到只读项后“点了没反应”。 */
  readonly attrKeys = computed(() => {
    const settable = this.status()?.settable;
    if (settable && settable.length) {
      return [...settable].sort();
    }
    return this.attributes().map(([k]) => k);
  });

  readonly hasSettableInfo = computed(() => {
    const settable = this.status()?.settable;
    return Boolean(settable && settable.length);
  });

  readonly selectedName = computed(() => {
    const id = this.selected();
    return this.cached().find((d) => d.device_id === id)?.name ?? id;
  });

  async ngOnInit(): Promise<void> {
    await this.loadCacheState();
    await Promise.all([this.refreshCache(), this.checkHealth()]);
  }

  /** 缓存无效（无密钥或已失效）时跳回登录页。 */
  private async loadCacheState(): Promise<void> {
    try {
      const summary = await this.lan.keysSummary();
      this.cacheState.set({
        valid: summary.valid,
        count: summary.count,
        age: summary.age_seconds,
        account: summary.account,
      });
      if (!summary.valid || summary.count === 0) {
        await this.router.navigate(['/login']);
      }
    } catch {
      this.error.set('连不上后端（8080）。');
    }
  }

  async goLogin(): Promise<void> {
    await this.router.navigate(['/login']);
  }

  humanAge(seconds: number | null): string {
    if (seconds === null || seconds === undefined) {
      return '未知';
    }
    if (seconds < 60) {
      return `${seconds} 秒前`;
    }
    if (seconds < 3600) {
      return `${Math.floor(seconds / 60)} 分钟前`;
    }
    if (seconds < 86400) {
      return `${Math.floor(seconds / 3600)} 小时前`;
    }
    return `${Math.floor(seconds / 86400)} 天前`;
  }

  private async checkHealth(): Promise<void> {
    try {
      const h = await this.lan.health();
      this.note.set(`LAN 服务在线，已缓存 ${h.cached_devices} 台设备的密钥`);
    } catch {
      this.error.set('连不上后端（8080）。请确认后端服务已启动。');
    }
  }

  async refreshCache(): Promise<void> {
    this.busy.set('cache');
    this.error.set('');
    try {
      this.cached.set(await this.lan.cachedDevices());
    } catch (err) {
      this.error.set(this.describe(err));
    } finally {
      this.busy.set('');
    }
  }

  async doDiscover(): Promise<void> {
    this.busy.set('discover');
    this.error.set('');
    this.discovered.set([]);
    try {
      const found = await this.lan.discover();
      this.discovered.set(found);
      if (found.length === 0) {
        this.error.set(
          '局域网内没发现设备。通常是本机与设备不在同一网段（或设备离线）。局域网发现必须在同一网络下才能工作。',
        );
      }
    } catch (err) {
      this.error.set(this.describe(err));
    } finally {
      this.busy.set('');
    }
  }

  select(id: string): void {
    this.selected.set(id);
    this.status.set(null);
    this.attrName.set('');
    this.attrValue.set('');
    this.lastResult.set('');
  }

  /** 模板里不能直接调全局 String()，用这个转。 */
  idStr(id: number | string): string {
    return String(id);
  }

  async loadStatus(): Promise<void> {
    const id = this.selected();
    if (!id) {
      return;
    }
    this.busy.set('status');
    this.error.set('');
    try {
      const result = await this.lan.status(id);
      this.status.set(result);
      const keys = Object.keys(result.attributes ?? {});
      if (keys.length && !this.attrName()) {
        this.attrName.set(keys[0]);
      }
    } catch (err) {
      this.error.set(this.describe(err));
    } finally {
      this.busy.set('');
    }
  }

  async sendControl(): Promise<void> {
    const id = this.selected();
    const attr = this.attrName().trim();
    if (!id || !attr) {
      this.error.set('先选设备、再选属性');
      return;
    }
    const raw = this.attrValue().trim();
    let value: unknown = raw;
    if (raw.toLowerCase() === 'true' || raw.toLowerCase() === 'on') {
      value = true;
    } else if (raw.toLowerCase() === 'false' || raw.toLowerCase() === 'off') {
      value = false;
    } else if (raw !== '' && !Number.isNaN(Number(raw))) {
      value = Number(raw);
    }

    if (!window.confirm(`向「${this.selectedName()}」下发\n\n属性：${attr}\n值：${String(value)}\n\n确认执行？`)) {
      return;
    }

    this.busy.set('control');
    this.error.set('');
    this.lastResult.set('');
    try {
      const result = await this.lan.control(id, attr, value);
      this.lastResult.set(JSON.stringify(result, null, 2));
      await this.loadStatus();
    } catch (err) {
      this.error.set(this.describe(err));
    } finally {
      this.busy.set('');
    }
  }

  formatValue(value: unknown): string {
    if (value === null || value === undefined) {
      return '—';
    }
    if (typeof value === 'object') {
      return JSON.stringify(value);
    }
    return String(value);
  }

  private describe(err: unknown): string {
    const detail = (err as { error?: { detail?: string } })?.error?.detail;
    if (detail) {
      return detail;
    }
    const message = (err as { message?: string })?.message;
    return message ?? '请求失败';
  }
}
