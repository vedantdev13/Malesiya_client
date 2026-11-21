import axios from 'axios';

export const API_BASE_URL = 'http://127.0.0.1:8000/api/v1';
export const WS_BASE_URL = 'ws://127.0.0.1:8000/ws/live-signals';

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 90000,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const checkHealth = async () => {
  const response = await api.get('/health');
  return response.data;
};

export const fetchCandles = async (pair = 'BTCUSDT', timeframe = '1m', limit = 100) => {
  const response = await api.get('/data/fetch', {
    params: { pair, timeframe, limit },
  });
  return response.data;
};

export const fetchFeatures = async (pair = 'BTCUSDT', timeframe = '1m') => {
  const response = await api.get('/data/features', {
    params: { pair, timeframe },
  });
  return response.data;
};

export const trainModel = async (pair = 'EURUSD=X', timeframe = '5m', limit = 20000) => {
  const response = await api.post('/predict/train', null, {
    params: { pair, timeframe, limit },
  });
  return response.data;
};

export const trainPrimaryFx = async (timeframe = '5m') => {
  const response = await api.post('/predict/train-primary-fx', null, {
    params: { timeframe },
  });
  return response.data;
};

export const trainSecondaryCrypto = async (timeframe = '5m') => {
  const response = await api.post('/predict/train-secondary-crypto', null, {
    params: { timeframe },
  });
  return response.data;
};

export const trainPrioritySuite = async (timeframe = '5m') => {
  const response = await api.post('/predict/train-priority-suite', null, {
    params: { timeframe },
  });
  return response.data;
};

export const generateSignal = async (
  pair = 'EURUSD=X',
  timeframe = '5m',
  confidenceThreshold = 0.60
) => {
  const response = await api.get('/predict/signal', {
    params: { pair, timeframe, confidence_threshold: confidenceThreshold },
  });
  return response.data;
};

export const fetchPerformance = async (pair, timeframe) => {
  const response = await api.get('/analytics/performance', {
    params: { pair, timeframe },
  });
  return response.data;
};

export const scanAllOpportunities = async (timeframe = '5m', threshold = 0.60) => {
  const response = await api.get('/predict/scan-all', {
    params: { timeframe, threshold },
  });
  return response.data;
};

export const fetchLiveTick = async (pair = 'BTCUSDT', timeframe = '5m') => {
  const response = await api.get('/data/live-tick', {
    params: { pair, timeframe },
  });
  return response.data;
};

export default api;

