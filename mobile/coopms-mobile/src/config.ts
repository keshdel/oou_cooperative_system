import Constants from 'expo-constants';

type ExtraConfig = {
  apiBaseUrl?: string;
};

const extra = (Constants.expoConfig?.extra || {}) as ExtraConfig;

export const API_BASE_URL = (extra.apiBaseUrl || 'https://ooucoop.cooperativems.com').replace(/\/$/, '');
