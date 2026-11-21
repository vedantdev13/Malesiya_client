import axios from 'axios';

// ─── Dynamic base URL resolution ─────────────────────────────────────────────
// Works on any machine / any host (localhost, LAN IP, Eshank's device, etc.)
// Priority:
//  1. VITE_API_URL env variable (set in .env.local for custom deployments)
//  2. Same hostname as the browser, port 8000 (auto-detects LAN IP)
//  3. Falls back to relative /api path (served by Vite proxy in dev)
function resolveApiBase() {
  if (import.meta.env.VITE_API_URL) {
    return import.meta.env.VITE_API_URL;
  }
  // In dev the Vite proxy handles /api → localhost:8000 (works on any host)
  if (import.meta.env.DEV) {
    return '/api/v1';
  }
  // Production: same host, port 8000
  const { protocol, hostname } = window.location;
  return `${protocol}//${hostname}:8000/api/v1`;
}

function resolveWsBase() {
  if (import.meta.env.VITE_WS_URL) {
    return import.meta.env.VITE_WS_URL;
  }
  if (import.meta.env.DEV) {
    return null; // Vite proxy will handle /ws
  }
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.hostname}:8000`;
}

export const API_BASE_URL = resolveApiBase();

const _wsBase = resolveWsBase();
export const WS_BASE_URL = _wsBase
  ? `${_wsBase}/ws/live-signals`
  : `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/live-signals`;

export const WS_MARKET_URL = _wsBase
  ? `${_wsBase}/ws/market`
  : `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/market`;

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 90000,
  headers: { 'Content-Type': 'application/json' },
});

export const checkHealth = async () => {
  const response = await api.get('/health');
  return response.data;
};

export const fetchCandles = async (pair = 'BTCUSDT', timeframe = '1m', limit = 100) => {
  const response = await api.get('/data/fetch', { params: { pair, timeframe, limit } });
  return response.data;
};

export const fetchFeatures = async (pair = 'BTCUSDT', timeframe = '1m') => {
  const response = await api.get('/data/features', { params: { pair, timeframe } });
  return response.data;
};

export const trainModel = async (pair = 'EURUSD=X', timeframe = '5m', limit = 20000) => {
  const response = await api.post('/predict/train', null, { params: { pair, timeframe, limit } });
  return response.data;
};

export const trainPrimaryFx = async (timeframe = '5m') => {
  const response = await api.post('/predict/train-primary-fx', null, { params: { timeframe } });
  return response.data;
};

export const trainSecondaryCrypto = async (timeframe = '5m') => {
  const response = await api.post('/predict/train-secondary-crypto', null, { params: { timeframe } });
  return response.data;
};

export const trainPrioritySuite = async (timeframe = '5m') => {
  const response = await api.post('/predict/train-priority-suite', null, { params: { timeframe } });
  return response.data;
};

export const generateSignal = async (pair = 'EURUSD=X', timeframe = '5m', confidenceThreshold = 0.60) => {
  const response = await api.get('/predict/signal', {
    params: { pair, timeframe, confidence_threshold: confidenceThreshold },
  });
  return response.data;
};

export const fetchPerformance = async (pair, timeframe) => {
  const response = await api.get('/analytics/performance', { params: { pair, timeframe } });
  return response.data;
};

export const scanAllOpportunities = async (timeframe = '5m', threshold = 0.60) => {
  const response = await api.get('/predict/scan-all', { params: { timeframe, threshold } });
  return response.data;
};

export const fetchLiveTick = async (pair = 'BTCUSDT', timeframe = '5m') => {
  const response = await api.get('/data/live-tick', { params: { pair, timeframe } });
  return response.data;
};

export default api;
