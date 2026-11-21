import React, { useEffect, useRef, useState, useCallback } from 'react';
import { createChart } from 'lightweight-charts';
import { fetchCandles } from '../services/api';
import { useWebSocket } from '../hooks/useWebSocket';
import { RefreshCw } from 'lucide-react';

// ─── Candle bucket duration per timeframe ──────────────────────────────────
const TF_SECONDS = { '1m': 60, '5m': 300, '15m': 900 };

/** Decimal precision per asset */
function getPrecision(pair) {
  if (pair.includes('JPY')) return 3;
  if (pair.includes('BTC') || pair.includes('ETH') || pair.includes('SOL')) return 2;
  return 5;
}

/** Resolve the market WebSocket URL dynamically (Vite proxy in dev, explicit host in prod) */
function resolveMarketWsUrl() {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  if (import.meta.env.DEV) {
    return `${proto}//${window.location.host}/ws/market`;
  }
  if (import.meta.env.VITE_WS_URL) {
    return `${import.meta.env.VITE_WS_URL}/ws/market`;
  }
  return `${proto}//${window.location.hostname}:8000/ws/market`;
}

const MARKET_WS_URL = resolveMarketWsUrl();

export default function TradingChart({ pair, timeframe, onPriceUpdate }) {
  const chartContainerRef = useRef(null);
  const chartInstanceRef  = useRef(null);
  const seriesRef         = useRef(null);
  const firstCandleOpenRef = useRef(null);

  const [loading, setLoading]         = useState(true);
  const [error, setError]             = useState(null);
  const [currentCandle, setCurrentCandle] = useState(null);
  const [candleStats, setCandleStats] = useState({ high: 0, low: 0, volume: 0 });
  const [countdown, setCountdown]     = useState(null); // seconds until next candle

  const pairRef      = useRef(pair);
  const timeframeRef = useRef(timeframe);
  useEffect(() => { pairRef.current = pair;      }, [pair]);
  useEffect(() => { timeframeRef.current = timeframe; }, [timeframe]);

  // ─── Load historical candles ─────────────────────────────────────────────
  const loadData = useCallback(async () => {
    if (!seriesRef.current) return;
    try {
      setLoading(true);
      setError(null);
      const data = await fetchCandles(pair, timeframe, 100);

      if (data?.candles?.length > 0) {
        const parsed = data.candles
          .map((c) => ({
            time: Math.floor(new Date(c.timestamp).getTime() / 1000),
            open:   Number(c.open),
            high:   Number(c.high),
            low:    Number(c.low),
            close:  Number(c.close),
            volume: Number(c.volume || 0),
          }))
          .sort((a, b) => a.time - b.time);

        // Deduplicate
        const unique = [];
        const seen = new Set();
        for (const item of parsed) {
          if (!seen.has(item.time)) { seen.add(item.time); unique.push(item); }
        }

        if (seriesRef.current && unique.length > 0) {
          seriesRef.current.setData(unique);
          chartInstanceRef.current?.timeScale().fitContent();

          const latest = unique[unique.length - 1];
          setCurrentCandle(latest);
          firstCandleOpenRef.current = unique[0].open;

          setCandleStats({
            high:   Math.max(...unique.map((c) => c.high)),
            low:    Math.min(...unique.map((c) => c.low)),
            volume: unique.reduce((acc, c) => acc + c.volume, 0),
          });

          if (onPriceUpdate) {
            const chg = ((latest.close - unique[0].open) / unique[0].open) * 100;
            onPriceUpdate(latest.close, chg);
          }
        }
      } else {
        setError('No candle data returned for ' + pair);
      }
    } catch (err) {
      console.error('Failed to fetch candles:', err);
      setError(err.message || 'Error loading market candles');
    } finally {
      setLoading(false);
    }
  }, [pair, timeframe, onPriceUpdate]);

  // ─── Apply a live tick to the candlestick series ─────────────────────────
  const applyTick = useCallback((candleData) => {
    if (!seriesRef.current) return;
    try {
      seriesRef.current.update(candleData);
      setCurrentCandle(candleData);
      setCandleStats((prev) => ({
        high:   Math.max(prev.high || 0, candleData.high),
        low:    prev.low ? Math.min(prev.low, candleData.low) : candleData.low,
        volume: prev.volume,
      }));
      if (onPriceUpdate) {
        const firstOpen = firstCandleOpenRef.current;
        const chg = firstOpen ? ((candleData.close - firstOpen) / firstOpen) * 100 : 0;
        onPriceUpdate(candleData.close, chg);
      }
    } catch (e) {
      // series not ready yet — silently skip
    }
  }, [onPriceUpdate]);

  // ─── WebSocket market tick handler ───────────────────────────────────────
  const handleMarketTick = useCallback((msg) => {
    if (msg.event !== 'MARKET_TICK' || !msg.ticks) return;
    const curPair = pairRef.current;
    const curTf   = timeframeRef.current;

    const tickData = msg.ticks[curPair];
    if (!tickData) return;

    const candleRaw = tickData.candles?.[curTf];
    if (!candleRaw) return;

    const candle = {
      time:  Number(candleRaw.time),
      open:  Number(candleRaw.open),
      high:  Number(candleRaw.high),
      low:   Number(candleRaw.low),
      close: Number(candleRaw.close),
    };

    applyTick(candle);

    // Live candle countdown
    const tfSecs = TF_SECONDS[curTf] || 300;
    const nowSec = Math.floor(Date.now() / 1000);
    const bucketStart = nowSec - (nowSec % tfSecs);
    const remaining = tfSecs - (nowSec - bucketStart);
    setCountdown(remaining);
  }, [applyTick]);

  const { status: wsStatus } = useWebSocket(MARKET_WS_URL, {
    onMessage: handleMarketTick,
    pingIntervalMs: 20000,
  });

  // ─── Countdown label formatter ────────────────────────────────────────────
  // Also tick the countdown every second from local clock
  useEffect(() => {
    const curTf = timeframe;
    const tfSecs = TF_SECONDS[curTf] || 300;
    const tick = () => {
      const nowSec = Math.floor(Date.now() / 1000);
      const bucketStart = nowSec - (nowSec % tfSecs);
      setCountdown(tfSecs - (nowSec - bucketStart));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [timeframe]);

  const fmtCountdown = (secs) => {
    if (secs == null) return '--:--';
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  };

  // ─── Init Lightweight Charts ──────────────────────────────────────────────
  useEffect(() => {
    if (!chartContainerRef.current) return;

    if (chartInstanceRef.current) {
      chartInstanceRef.current.remove();
      chartInstanceRef.current = null;
    }

    const container = chartContainerRef.current;

    const fmtTime = (unix) => {
      const d = new Date(unix * 1000);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    };
    const fmtDate = (unix) => {
      const d = new Date(unix * 1000);
      return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
    };

    const chart = createChart(container, {
      width:  container.clientWidth,
      height: container.clientHeight || 480,
      layout: {
        background: { type: 'solid', color: '#151D2A' },
        textColor: '#D1D5DB',
        fontSize: 11,
        fontFamily: "'JetBrains Mono', monospace",
      },
      localization: {
        timeFormatter: (unix) => {
          const d = new Date(unix * 1000);
          return d.toLocaleString([], {
            month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit', hour12: false,
          });
        },
      },
      grid: {
        vertLines: { color: '#2A3447', style: 1 },
        horzLines: { color: '#2A3447', style: 1 },
      },
      crosshair: {
        mode: 0,
        vertLine: { color: '#9CA3AF', width: 1, style: 3, labelBackgroundColor: '#2A3447' },
        horzLine: { color: '#9CA3AF', width: 1, style: 3, labelBackgroundColor: '#2A3447' },
      },
      timeScale: {
        borderColor: '#2A3447',
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (unix, type) => type <= 2 ? fmtDate(unix) : fmtTime(unix),
      },
      rightPriceScale: {
        borderColor: '#2A3447',
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
    });

    const precision = getPrecision(pair);
    const series = chart.addCandlestickSeries({
      upColor:     '#10B981',
      downColor:   '#EF4444',
      borderVisible: false,
      wickUpColor:   '#10B981',
      wickDownColor: '#EF4444',
      priceFormat: {
        type: 'price',
        precision,
        minMove: 1 / Math.pow(10, precision),
      },
    });

    chartInstanceRef.current = chart;
    seriesRef.current = series;

    chart.subscribeCrosshairMove((param) => {
      if (param?.time && param.seriesData && seriesRef.current) {
        const dp = param.seriesData.get(seriesRef.current);
        if (dp) setCurrentCandle(dp);
      }
    });

    const resizeObserver = new ResizeObserver((entries) => {
      if (!entries || entries.length === 0) return;
      const { width, height } = entries[0].contentRect;
      if (width > 0 && height > 0) chart.applyOptions({ width, height });
    });
    resizeObserver.observe(container);

    loadData();

    return () => {
      resizeObserver.disconnect();
      if (chartInstanceRef.current) {
        chartInstanceRef.current.remove();
        chartInstanceRef.current = null;
        seriesRef.current = null;
      }
    };
  }, [pair, timeframe]); // eslint-disable-line react-hooks/exhaustive-deps

  const isUp = currentCandle && currentCandle.close >= currentCandle.open;
  const priceDecimals = getPrecision(pair);

  return (
    <div className="flex flex-col h-full bg-[#151D2A] border border-[#2A3447] rounded-xl overflow-hidden shadow-xl">
      {/* Chart Top Bar */}
      <div className="flex flex-wrap items-center justify-between px-4 py-2.5 bg-[#111722] border-b border-[#2A3447] text-xs font-mono">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5 font-bold text-sm text-[#F3F4F6]">
            <span>{pair}</span>
            <span className="text-[#9CA3AF] text-xs font-normal">[{timeframe}]</span>
          </div>

          {/* LIVE badge + countdown timer */}
          <div className="hidden sm:flex items-center gap-2">
            <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-[#0B0E14] border border-[#10B981]/30">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#10B981] opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-[#10B981]" />
              </span>
              <span className="text-[10px] font-semibold text-[#10B981] tracking-wider">LIVE 1S</span>
            </div>

            {/* Candle countdown — just like Quotex */}
            <div className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-[#0B0E14] border border-[#F59E0B]/30">
              <span className="text-[10px] text-[#F59E0B] font-mono font-semibold">
                ⏱ {fmtCountdown(countdown)}
              </span>
            </div>

            {/* WebSocket status dot */}
            <div
              className={`w-1.5 h-1.5 rounded-full ${wsStatus === 'open' ? 'bg-[#10B981]' : 'bg-[#EF4444] animate-pulse'}`}
              title={`WS: ${wsStatus}`}
            />
          </div>

          {currentCandle && (
            <div className="flex items-center gap-3 text-[11px] text-[#9CA3AF]">
              <span>O: <span className="text-[#F3F4F6]">{currentCandle.open.toFixed(priceDecimals)}</span></span>
              <span>H: <span className="text-[#10B981]">{currentCandle.high.toFixed(priceDecimals)}</span></span>
              <span>L: <span className="text-[#EF4444]">{currentCandle.low.toFixed(priceDecimals)}</span></span>
              <span>
                C:{' '}
                <span className={isUp ? 'text-[#10B981]' : 'text-[#EF4444]'}>
                  {currentCandle.close.toFixed(priceDecimals)}
                </span>
              </span>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3">
          <div className="hidden sm:flex items-center gap-3 text-[11px] text-[#9CA3AF]">
            <span>24h High: <span className="text-[#10B981]">{candleStats.high.toFixed(priceDecimals)}</span></span>
            <span>24h Low: <span className="text-[#EF4444]">{candleStats.low.toFixed(priceDecimals)}</span></span>
          </div>
          <button
            onClick={loadData}
            disabled={loading}
            title="Refresh Candles"
            className="p-1.5 rounded-lg bg-[#0B0E14] border border-[#2A3447] text-[#9CA3AF] hover:text-white hover:border-[#3B4861] transition-all"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin text-[#10B981]' : ''}`} />
          </button>
        </div>
      </div>

      {/* Chart Canvas */}
      <div className="relative flex-1 min-h-[460px] w-full bg-[#151D2A]">
        {loading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-[#151D2A]/70 backdrop-blur-sm">
            <div className="flex flex-col items-center gap-2">
              <div className="w-7 h-7 border-2 border-[#10B981] border-t-transparent rounded-full animate-spin" />
              <span className="text-xs text-[#9CA3AF] font-mono">Loading market stream...</span>
            </div>
          </div>
        )}
        {error && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-[#151D2A]/90 p-4">
            <div className="text-center max-w-sm">
              <p className="text-sm text-[#EF4444] font-medium mb-2">{error}</p>
              <button
                onClick={loadData}
                className="px-3 py-1.5 text-xs bg-[#2A3447] hover:bg-[#3B4861] text-white rounded-md font-mono"
              >
                Retry
              </button>
            </div>
          </div>
        )}
        <div ref={chartContainerRef} className="w-full h-full" />
      </div>
    </div>
  );
}
