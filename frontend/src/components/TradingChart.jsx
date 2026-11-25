import React, { useEffect, useRef, useState, useCallback } from 'react';
import { createChart } from 'lightweight-charts';
import { fetchCandles } from '../services/api';
import { useWebSocket } from '../hooks/useWebSocket';
import { RefreshCw, Clock, Zap } from 'lucide-react';

const TF_SECONDS = { '1m': 60, '5m': 300, '15m': 900 };

function getPrecision(pair) {
  if (pair.includes('JPY')) return 3;
  return 5;
}

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
  const priceLineRef      = useRef(null);
  const firstCandleOpenRef = useRef(null);

  const [loading, setLoading]         = useState(true);
  const [error, setError]             = useState(null);
  const [currentCandle, setCurrentCandle] = useState(null);
  const [candleStats, setCandleStats] = useState({ high: 0, low: 0, volume: 0 });
  const [countdown, setCountdown]     = useState(null); // seconds until next candle

  const pairRef      = useRef(pair);
  const timeframeRef = useRef(timeframe);
  useEffect(() => { pairRef.current = pair; }, [pair]);
  useEffect(() => { timeframeRef.current = timeframe; }, [timeframe]);

  const tfSecs = TF_SECONDS[timeframe] || 60;
  const candleProgressPct = countdown != null ? Math.min(100, Math.max(0, ((tfSecs - countdown) / tfSecs) * 100)) : 0;
  const isEntryZone = countdown != null && countdown <= 5;

  const fmtCountdown = (secs) => {
    if (secs == null) return '--:--';
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  };

  // ─── Load historical candles ─────────────────────────────────────────────
  const loadData = useCallback(async () => {
    if (!seriesRef.current) return;
    try {
      setLoading(true);
      setError(null);
      const data = await fetchCandles(pair, timeframe, 100);

      if (data?.candles?.length > 0) {
        const parsed = data.candles
          .map((c) => {
            let t = c.time;
            if (t == null) {
              const tsStr = String(c.timestamp || '');
              const utcStr = tsStr.endsWith('Z') || tsStr.includes('+') ? tsStr : `${tsStr}Z`;
              t = Math.floor(new Date(utcStr).getTime() / 1000);
            }
            return {
              time: Number(t),
              open:   Number(c.open),
              high:   Number(c.high),
              low:    Number(c.low),
              close:  Number(c.close),
              volume: Number(c.volume || 0),
            };
          })
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

  // ─── Apply a live tick to the candlestick series & update floating price line ───
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

      // Update Quotex-style floating price line with countdown title
      const isCandleUp = candleData.close >= candleData.open;
      const lineColor = isCandleUp ? '#10B981' : '#EF4444';

      if (priceLineRef.current) {
        seriesRef.current.removePriceLine(priceLineRef.current);
      }
      priceLineRef.current = seriesRef.current.createPriceLine({
        price: candleData.close,
        color: lineColor,
        lineWidth: 1,
        lineStyle: 2, // Dashed line across chart
        axisLabelVisible: true,
        title: `⏱ ${fmtCountdown(countdown)}`,
      });

      if (onPriceUpdate) {
        const firstOpen = firstCandleOpenRef.current;
        const chg = firstOpen ? ((candleData.close - firstOpen) / firstOpen) * 100 : 0;
        onPriceUpdate(candleData.close, chg);
      }
    } catch (e) {
      // series not ready yet
    }
  }, [countdown, onPriceUpdate]);

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
    const secs = TF_SECONDS[curTf] || 60;
    const nowSec = Math.floor(Date.now() / 1000);
    const bucketStart = nowSec - (nowSec % secs);
    const remaining = secs - (nowSec - bucketStart);
    setCountdown(remaining);
  }, [applyTick]);

  const { status: wsStatus } = useWebSocket(MARKET_WS_URL, {
    onMessage: handleMarketTick,
    pingIntervalMs: 20000,
  });

  // ─── Clock timer to update countdown every second ────────────────────────
  useEffect(() => {
    const curTf = timeframe;
    const secs = TF_SECONDS[curTf] || 60;
    const tick = () => {
      const nowSec = Math.floor(Date.now() / 1000);
      const bucketStart = nowSec - (nowSec % secs);
      const rem = secs - (nowSec - bucketStart);
      setCountdown(rem);

      // Also refresh price line title with latest countdown
      if (seriesRef.current && currentCandle && priceLineRef.current) {
        try {
          seriesRef.current.removePriceLine(priceLineRef.current);
          const isCandleUp = currentCandle.close >= currentCandle.open;
          priceLineRef.current = seriesRef.current.createPriceLine({
            price: currentCandle.close,
            color: isCandleUp ? '#10B981' : '#EF4444',
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: `⏱ ${fmtCountdown(rem)}`,
          });
        } catch {
          // ignore
        }
      }
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [timeframe, currentCandle]);

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
        priceLineRef.current = null;
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
            <span>{pair.replace('=X', '')}</span>
            <span className="text-[#10B981] text-xs font-semibold px-1.5 py-0.5 rounded bg-[#10B981]/15">
              [{timeframe} FX]
            </span>
          </div>

          {/* Quotex-style LIVE badge + countdown timer */}
          <div className="hidden sm:flex items-center gap-2">
            <div className="flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-[#0B0E14] border border-[#10B981]/40">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#10B981] opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-[#10B981]" />
              </span>
              <span className="text-[10px] font-semibold text-[#10B981] tracking-wider">LIVE 1S</span>
            </div>

            {/* Candle countdown timer badge */}
            <div className={`flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-[#0B0E14] border transition-all ${
              isEntryZone ? 'border-[#10B981] bg-[#10B981]/20 animate-pulse' : 'border-[#F59E0B]/40'
            }`}>
              <Clock className={`w-3 h-3 ${isEntryZone ? 'text-[#10B981]' : 'text-[#F59E0B]'}`} />
              <span className={`text-[11px] font-mono font-bold ${isEntryZone ? 'text-[#10B981]' : 'text-[#F59E0B]'}`}>
                {fmtCountdown(countdown)}
              </span>
            </div>

            {/* WebSocket status indicator */}
            <div
              className={`w-2 h-2 rounded-full ${wsStatus === 'open' ? 'bg-[#10B981]' : 'bg-[#EF4444] animate-pulse'}`}
              title={`Market Stream: ${wsStatus}`}
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

      {/* Chart Canvas & Quotex Floating Countdown Overlay */}
      <div className="relative flex-1 min-h-[460px] w-full bg-[#151D2A]">
        {/* Quotex-Style Floating Candle Countdown HUD (Top Right) */}
        <div className="absolute right-16 top-3 z-10 flex items-center gap-2 bg-[#0B0E14]/85 backdrop-blur-md border border-[#2A3447] px-3 py-1.5 rounded-lg shadow-2xl font-mono pointer-events-none">
          <div className="flex items-center gap-1.5">
            <span className={`relative flex h-2 w-2 ${isEntryZone ? 'animate-ping' : ''}`}>
              <span className={`inline-flex rounded-full h-2 w-2 ${isEntryZone ? 'bg-[#10B981]' : 'bg-[#F59E0B]'}`}></span>
            </span>
            <div className="flex flex-col">
              <span className="text-[9px] text-[#9CA3AF] uppercase font-semibold leading-tight">
                {isEntryZone ? 'ENTRY READY' : 'NEXT CANDLE'}
              </span>
              <span className={`text-xs font-bold leading-tight ${isEntryZone ? 'text-[#10B981]' : 'text-[#F59E0B]'}`}>
                ⏱ {fmtCountdown(countdown)}
              </span>
            </div>
          </div>

          {/* Candle elapsed progress bar */}
          <div className="w-10 h-1.5 bg-[#1F2937] rounded-full overflow-hidden ml-1">
            <div
              className={`h-full transition-all duration-1000 ${
                isEntryZone ? 'bg-[#10B981]' : 'bg-gradient-to-r from-[#10B981] to-[#F59E0B]'
              }`}
              style={{ width: `${candleProgressPct}%` }}
            />
          </div>
        </div>

        {loading && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-[#151D2A]/70 backdrop-blur-sm">
            <div className="flex flex-col items-center gap-2">
              <div className="w-7 h-7 border-2 border-[#10B981] border-t-transparent rounded-full animate-spin" />
              <span className="text-xs text-[#9CA3AF] font-mono">Loading Quotex real-time feed...</span>
            </div>
          </div>
        )}

        {error && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-[#151D2A]/90 p-4">
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
