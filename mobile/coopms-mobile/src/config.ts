import * as SecureStore from 'expo-secure-store';

// The app is multi-tenant: each cooperative runs on its own backend
// (<code>.cooperativems.com). The member picks their society once; the chosen
// API base is persisted and used for every request thereafter.

const TENANT_BASE_KEY = 'coopms.tenant.base';
const TENANT_NAME_KEY = 'coopms.tenant.name';
const ROOT_DOMAIN = 'cooperativems.com';

let currentBase = '';
let currentName = '';

/** The active tenant's API base URL (empty until a cooperative is chosen). */
export function getApiBase(): string {
  return currentBase;
}

/** The active cooperative's display name. */
export function getCoopName(): string {
  return currentName;
}

/** Load the saved tenant into memory at startup. Returns true if one is set. */
export async function loadTenant(): Promise<boolean> {
  currentBase = (await SecureStore.getItemAsync(TENANT_BASE_KEY)) || '';
  currentName = (await SecureStore.getItemAsync(TENANT_NAME_KEY)) || '';
  return currentBase.length > 0;
}

/** Persist and activate the chosen cooperative. */
export async function setTenant(base: string, name: string): Promise<void> {
  currentBase = base.replace(/\/+$/, '');
  currentName = name;
  await SecureStore.setItemAsync(TENANT_BASE_KEY, currentBase);
  await SecureStore.setItemAsync(TENANT_NAME_KEY, name);
}

/** Forget the current cooperative — used by "Change cooperative". */
export async function clearTenant(): Promise<void> {
  currentBase = '';
  currentName = '';
  await SecureStore.deleteItemAsync(TENANT_BASE_KEY);
  await SecureStore.deleteItemAsync(TENANT_NAME_KEY);
}

/** Turn a society code, subdomain or full domain into an API base URL. */
export function codeToBaseUrl(input: string): string {
  const raw = (input || '').trim().toLowerCase();
  if (!raw) return '';
  if (raw.startsWith('http://') || raw.startsWith('https://')) return raw.replace(/\/+$/, '');
  if (raw.includes('.')) return `https://${raw}`.replace(/\/+$/, '');   // a full custom domain
  return `https://${raw}.${ROOT_DOMAIN}`;                                // a short code -> subdomain
}
