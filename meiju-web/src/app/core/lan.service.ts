import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

/** 后端与前端同源不同端口：统一在 8080（LAN 接口已并入主服务）。 */
function resolveApiBase(): string {
  if (typeof window === 'undefined') {
    return 'http://127.0.0.1:8080';
  }
  const override = new URLSearchParams(window.location.search).get('api');
  if (override) {
    return override.replace(/\/+$/, '');
  }
  const host = window.location.hostname || '127.0.0.1';
  return `http://${host}:8080`;
}

export const LAN_API_BASE = resolveApiBase();

export interface LanDevice {
  device_id: string;
  name: string | null;
  type: string;
  sn: string | null;
  model?: string | null;
  methods?: string[];
  has_keys: boolean;
}

export interface LanStatus {
  device_id: number;
  class?: string;
  type?: string;
  available?: boolean | null;
  /** 该机型可下发的属性；null 表示未收录（可能全部可下发，也可能只读） */
  settable?: string[] | null;
  attributes?: Record<string, unknown>;
  raw?: string;
}

export interface LanDiscovered {
  device_id: number;
  name: string | null;
  cloud_id: string | null;
  type: string;
  ip: string;
  port: number;
  protocol: number;
  sn: string;
  model: string;
  has_keys: boolean;
}

@Injectable({ providedIn: 'root' })
export class LanService {
  private readonly http = inject(HttpClient);

  async health(): Promise<{
    ok: boolean;
    cached_devices: number;
    cache_valid: boolean;
    fetched_at: string | null;
    cache_age_seconds: number | null;
    account: string | null;
  }> {
    return firstValueFrom(
      this.http.get<{
        ok: boolean;
        cached_devices: number;
        cache_valid: boolean;
        fetched_at: string | null;
        cache_age_seconds: number | null;
        account: string | null;
      }>(`${LAN_API_BASE}/health`),
    );
  }

  /** 密钥缓存摘要：决定是否需要重新登录。 */
  async keysSummary(): Promise<{
    ok: boolean;
    valid: boolean;
    count: number;
    account: string | null;
    fetched_at: string | null;
    age_seconds: number | null;
    invalid_reason?: string | null;
    devices: Record<string, { name: string | null; type: string; sn: string; methods: string[] }>;
  }> {
    return firstValueFrom(
      this.http.get<{
        ok: boolean;
        valid: boolean;
        count: number;
        account: string | null;
        fetched_at: string | null;
        age_seconds: number | null;
        invalid_reason?: string | null;
        devices: Record<string, { name: string | null; type: string; sn: string; methods: string[] }>;
      }>(`${LAN_API_BASE}/lan/keys`),
    );
  }

  /** 用美居账号密码登录取密钥（密码只在服务端内存中用于本次登录）。 */
  async loginAndFetchKeys(
    account: string,
    password: string,
  ): Promise<{ ok: boolean; account: string | null; count: number; fetched_at: string | null }> {
    return firstValueFrom(
      this.http.post<{ ok: boolean; account: string | null; count: number; fetched_at: string | null }>(
        `${LAN_API_BASE}/lan/keys/login`,
        { account, password },
      ),
    );
  }

  async cachedDevices(): Promise<LanDevice[]> {
    const result = await firstValueFrom(
      this.http.get<{ count: number; devices: LanDevice[] }>(`${LAN_API_BASE}/lan/devices`),
    );
    return result.devices;
  }

  async discover(): Promise<LanDiscovered[]> {
    const result = await firstValueFrom(
      this.http.get<{ count: number; devices: LanDiscovered[] }>(`${LAN_API_BASE}/lan/discover`),
    );
    return result.devices;
  }

  async status(deviceId: string): Promise<LanStatus> {
    return firstValueFrom(this.http.get<LanStatus>(`${LAN_API_BASE}/lan/devices/${deviceId}/status`));
  }

  async control(deviceId: string, attribute: string, value: unknown): Promise<unknown> {
    return firstValueFrom(
      this.http.post(`${LAN_API_BASE}/lan/devices/${deviceId}/control`, { attribute, value }),
    );
  }
}

