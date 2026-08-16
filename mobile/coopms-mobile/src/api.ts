import * as SecureStore from 'expo-secure-store';
import { HQ_API_BASE, codeToBaseUrl, getApiBase } from './config';
import type { DashboardPayload, Loan, MobileNotification, SavingRow } from './types';

const TOKEN_KEY = 'coopms.mobile.token';

type ApiOptions = {
  method?: 'GET' | 'POST' | 'PATCH';
  body?: Record<string, unknown>;
  token?: string | null;
};

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function parseResponse(response: Response) {
  return response.json().catch(() => ({} as Record<string, unknown>));
}

async function request<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const headers: Record<string, string> = {
    Accept: 'application/json'
  };
  if (options.body) headers['Content-Type'] = 'application/json';
  if (options.token) headers.Authorization = `Bearer ${options.token}`;

  const base = getApiBase();
  if (!base) throw new ApiError('No cooperative selected.', 0);

  const response = await fetch(`${base}${path}`, {
    method: options.method || 'GET',
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined
  });

  const payload = await parseResponse(response);
  if (!response.ok || payload.success === false) {
    throw new ApiError((payload.error as string) || `Request failed (${response.status})`, response.status);
  }
  return payload as T;
}

/** Look up a cooperative by code/domain via its public tenant endpoint.
 *  Returns the resolved API base + display name so the app can target the right
 *  backend and brand its login screen. Throws ApiError with a friendly message. */
export async function resolveTenant(code: string): Promise<{ base: string; coopName: string; logo: string }> {
  const cleanCode = (code || '').trim().toLowerCase();
  if (!cleanCode) throw new ApiError('Enter your cooperative code.', 0);
  try {
    const response = await fetch(
      `${HQ_API_BASE}/api/mobile/v1/tenants/resolve?code=${encodeURIComponent(cleanCode)}`,
      { headers: { Accept: 'application/json' } }
    );
    const payload = await parseResponse(response);
    const tenant = payload.tenant as Record<string, unknown> | undefined;
    if (response.ok && payload.success === true && tenant?.base_url) {
      return {
        base: String(tenant.base_url).replace(/\/+$/, ''),
        coopName: String(tenant.coop_name || tenant.name || 'Cooperative'),
        logo: String(tenant.logo || '')
      };
    }
  } catch {
    // Fall back to direct tenant probing below so local/dev testing still works.
  }

  const base = codeToBaseUrl(cleanCode);
  let response: Response;
  try {
    response = await fetch(`${base}/api/mobile/v1/tenant`, { headers: { Accept: 'application/json' } });
  } catch {
    throw new ApiError('Could not reach that cooperative. Check the code and your connection.', 0);
  }
  const payload = await parseResponse(response);
  if (!response.ok || payload.success !== true) {
    throw new ApiError('Cooperative not found — check the code with your society.', response.status);
  }
  return {
    base,
    coopName: (payload.coop_name as string) || 'Cooperative',
    logo: (payload.logo as string) || ''
  };
}

export async function saveToken(token: string) {
  await SecureStore.setItemAsync(TOKEN_KEY, token);
}

export async function loadToken() {
  return SecureStore.getItemAsync(TOKEN_KEY);
}

export async function clearToken() {
  await SecureStore.deleteItemAsync(TOKEN_KEY);
}

export async function login(username: string, password: string) {
  const payload = await request<{ success: boolean; token: string; user: unknown }>('/api/mobile/login', {
    method: 'POST',
    body: { username, password }
  });
  await saveToken(payload.token);
  return payload;
}

export async function requestPasswordReset(identifier: string) {
  return request<{ success: boolean; message: string }>('/api/mobile/v1/auth/forgot-password', {
    method: 'POST',
    body: { identifier }
  });
}

export async function changePassword(token: string, currentPassword: string, newPassword: string, confirmPassword: string) {
  return request<{ success: boolean; message: string }>('/api/mobile/v1/auth/change-password', {
    method: 'POST',
    token,
    body: {
      current_password: currentPassword,
      new_password: newPassword,
      confirm_password: confirmPassword
    }
  });
}

export async function getDashboard(token: string) {
  return request<DashboardPayload>('/api/mobile/v1/dashboard', { token });
}

export async function getProfile(token: string) {
  return request<{ success: boolean; profile: Record<string, string>; profile_completion: unknown }>(
    '/api/mobile/v1/profile',
    { token }
  );
}

export async function updateProfile(token: string, profile: Record<string, string>) {
  return request<{ success: boolean; member: DashboardPayload['member'] }>('/api/mobile/v1/profile', {
    method: 'PATCH',
    token,
    body: profile
  });
}

export async function getSavings(token: string) {
  return request<{ success: boolean; balance: number; rows: SavingRow[] }>('/api/mobile/v1/savings', { token });
}

export async function getLoans(token: string) {
  return request<{ success: boolean; loans: Loan[] }>('/api/mobile/v1/loans', { token });
}

export async function previewLoanSchedule(token: string, input: { amount: number; purpose: string; tenure: number }) {
  return request<{
    success: boolean;
    amount: number;
    purpose: string;
    tenure: number;
    interest_rate: number;
    interest_method: string;
    monthly_payment: number;
    total_repayment: number;
    total_interest: number;
    schedule: Loan['schedule'];
  }>('/api/mobile/v1/loans/schedule-preview', {
    method: 'POST',
    token,
    body: input
  });
}

export async function applyForLoan(token: string, input: Record<string, unknown>) {
  return request<{ success: boolean; loan: Loan }>('/api/mobile/v1/loans/apply', {
    method: 'POST',
    token,
    body: input
  });
}

export async function getLoanDetail(token: string, loanId: number) {
  return request<{ success: boolean; loan: Loan }>(`/api/mobile/v1/loans/${loanId}`, { token });
}

export async function withdrawLoan(token: string, loanId: number, reason: string) {
  return request<{ success: boolean; loan: Loan }>(`/api/mobile/v1/loans/${loanId}/withdraw`, {
    method: 'POST',
    token,
    body: { reason }
  });
}

export async function getNotifications(token: string) {
  return request<{ success: boolean; notifications: MobileNotification[] }>('/api/mobile/v1/notifications', { token });
}

export async function markAllNotificationsRead(token: string) {
  return request<{ success: boolean }>('/api/mobile/v1/notifications/mark-all-read', {
    method: 'POST',
    token
  });
}

export async function registerDevice(token: string, pushToken: string, platform: string, deviceName: string) {
  return request<{ success: boolean; device_id: number }>('/api/mobile/v1/devices', {
    method: 'POST',
    token,
    body: {
      push_token: pushToken,
      platform,
      device_name: deviceName
    }
  });
}
