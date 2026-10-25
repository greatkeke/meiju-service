import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { LanService } from '../../core/lan.service';

@Component({
  selector: 'mj-login',
  imports: [FormsModule],
  templateUrl: './login.component.html',
  styleUrl: './login.component.scss',
})
export class LoginComponent {
  private readonly lan = inject(LanService);
  private readonly router = inject(Router);

  readonly account = signal('');
  readonly password = signal('');
  readonly busy = signal(false);
  readonly error = signal('');
  readonly notice = signal('');
  readonly showPassword = signal(false);
  readonly cacheInfo = signal<{ count: number; account: string | null; age: number | null } | null>(null);

  readonly ready = computed(() => this.account().trim().length > 0 && this.password().length > 0);

  async ngOnInit(): Promise<void> {
    try {
      const summary = await this.lan.keysSummary();
      if (summary.count > 0) {
        this.cacheInfo.set({
          count: summary.count,
          account: summary.account,
          age: summary.age_seconds,
        });
        this.account.set(summary.account ?? '');
        if (summary.valid) {
          this.notice.set(
            `已有 ${summary.count} 台设备的密钥缓存（${this.humanAge(summary.age_seconds)}），可直接进入。`,
          );
        } else {
          this.error.set(summary.invalid_reason || '缓存的密钥已失效，请重新登录获取。');
        }
      }
    } catch {
      this.error.set('连不上后端（8080）。请确认后端服务已启动。');
    }
  }

  togglePassword(): void {
    this.showPassword.update((v) => !v);
  }

  humanAge(seconds: number | null): string {
    if (seconds === null || seconds === undefined) {
      return '未知时间';
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

  async skip(): Promise<void> {
    await this.router.navigate(['/lan']);
  }

  async submit(): Promise<void> {
    if (this.busy() || !this.ready()) {
      return;
    }
    this.busy.set(true);
    this.error.set('');
    this.notice.set('');
    try {
      const result = await this.lan.loginAndFetchKeys(this.account().trim(), this.password());
      if (!result.count) {
        this.error.set('登录成功，但这个账号下没有取到设备密钥。');
        return;
      }
      this.password.set('');
      await this.router.navigate(['/lan']);
    } catch (err) {
      this.error.set(this.describe(err));
    } finally {
      this.busy.set(false);
    }
  }

  private describe(err: unknown): string {
    const detail = (err as { error?: { detail?: string } })?.error?.detail;
    if (detail) {
      return detail;
    }
    return (err as { message?: string })?.message ?? '登录失败，请稍后重试';
  }
}
