import React, { useEffect, useRef, useState } from 'react';
import { createChart } from 'lightweight-charts';
import { fetchCandles, fetchLiveTick } from '../services/api';
import { RefreshCw, TrendingUp, TrendingDown, Maximize2, Radio } from 'lucide-react';

export default function TradingChart({ pair, timeframe, onPriceUpdate }) {
  const chartContainerRef = useRef(null);
  const chartInstanceRef = useRef(null);
  const seriesRef = useRef(null);
  const firstCandleOpenRef = useRef(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [currentCandle, setCurrentCandle] = useState(null);
  const [candleStats, setCandleStats] = useState({ high: 0, low: 0, volume: 0 });

  const loadData = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await fetchCandles(pair, timeframe, 100);

      if (data && data.candles && data.candles.length > 0) {
        // Format for lightweight-charts: { time: unix_sec, open, high, low, close }
        const parsed = data.candles.map((c) => ({
          time: Math.floor(new Date(c.timestamp).getTime() / 1000),
          open: Number(c.open),
          high: Number(c.high),
          low: Number(c.low),
          close: Number(c.close),
          volume: Number(c.volume || 0),
        }));

        // Sort ascending and deduplicate timestamps
        parsed.sort((a, b) => a.time - b.time);
        const unique = [];
        const seen = new Set();
        for (const item of parsed) {
          if (!seen.has(item.time)) {
            seen.add(item.time);
            unique.push(item);
          }
        }

        if (seriesRef.current && unique.length > 0) {
          seriesRef.current.setData(unique);
          if (chartInstanceRef.current) {
            chartInstanceRef.current.timeScale().fitContent();
          }

          const latest = unique[unique.length - 1];
          setCurrentCandle(latest);
          firstCandleOpenRef.current = unique[0].open;

          const highs = unique.map((c) => c.high);
          const lows = unique.map((c) => c.low);
          const volTotal = unique.reduce((acc, c) => acc + c.volume, 0);

          setCandleStats({
            high: Math.max(...highs),
            low: Math.min(...lows),
            volume: volTotal,
          });

          if (onPriceUpdate) {
            const first = unique[0];
            const change = ((latest.close - first.open) / first.open) * 100;
            onPriceUpdate(latest.close, change);
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
  };

  // Initialize Lightweight Charts
  useEffect(() => {
    if (!chartContainerRef.current) return;

    // Clean previous chart if exists
    if (chartInstanceRef.current) {
      chartInstanceRef.current.remove();
      chartInstanceRef.current = null;
    }

    const container = chartContainerRef.current;

    // Local timezone formatter — auto-detects browser timezone (MYT UTC+8 for Malaysia, etc.)
    const fmtTime = (unixSec) => {
      const d = new Date(unixSec * 1000);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    };
    const fmtDate = (unixSec) => {
      const d = new Date(unixSec * 1000);
      return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
    };

    const chart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight || 480,
      layout: {
        background: { type: 'solid', color: '#151D2A' },
        textColor: '#D1D5DB',
        fontSize: 11,
        fontFamily: "'JetBrains Mono', monospace",
      },
      localization: {
        // Format crosshair & price labels in local browser timezone
        timeFormatter: (unixSec) => {
          const d = new Date(unixSec * 1000);
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
        vertLine: {
          color: '#9CA3AF',
          width: 1,
          style: 3,
          labelBackgroundColor: '#2A3447',
        },
        horzLine: {
          color: '#9CA3AF',
          width: 1,
          style: 3,
          labelBackgroundColor: '#2A3447',
        },
      },
      timeScale: {
        borderColor: '#2A3447',
        timeVisible: true,
        secondsVisible: false,
        // Tick labels on axis in local timezone
        tickMarkFormatter: (unixSec, tickMarkType) => {
          // TickMarkType: 0=Year,1=Month,2=DayOfMonth,3=Time,4=TimeWithSeconds
          if (tickMarkType <= 2) return fmtDate(unixSec);
          return fmtTime(unixSec);
        },
      },
      rightPriceScale: {
        borderColor: '#2A3447',
        scaleMargins: {
          top: 0.1,
          bottom: 0.1,
        },
      },
    });

    const getPriceDecimals = (p) => {
      if (p.includes('JPY')) return 3;
      if (p.includes('BTC') || p.includes('ETH') || p.includes('SOL')) return 2;
      return 5;
    };
    const precision = getPriceDecimals(pair);

    const series = chart.addCandlestickSeries({
      upColor: '#10B981',
      downColor: '#EF4444',
      borderVisible: false,
      wickUpColor: '#10B981',
      wickDownColor: '#EF4444',
      priceFormat: {
        type: 'price',
        precision: precision,
        minMove: 1 / Math.pow(10, precision),
      },
    });

    chartInstanceRef.current = chart;
    seriesRef.current = series;

    // Track crosshair hover for latest OHLC readouts
    chart.subscribeCrosshairMove((param) => {
      if (param && param.time && param.seriesData && seriesRef.current) {
        const dataPoint = param.seriesData.get(seriesRef.current);
        if (dataPoint) {
          setCurrentCandle(dataPoint);
        }
      }
    });

    // ResizeObserver for dynamic container dimensions
    const resizeObserver = new ResizeObserver((entries) => {
      if (!entries || entries.length === 0) return;
      const { width, height } = entries[0].contentRect;
      if (width > 0 && height > 0) {
        chart.applyOptions({ width, height });
      }
    });
    resizeObserver.observe(container);

    // Initial load
    loadData();

    // Real-Time Moving Chart Candles (zero refresh - updates active bar dynamically every 2s)
    let isCancelled = false;
    const pollTick = async () => {
      if (isCancelled || !seriesRef.current) return;
      try {
        const res = await fetchLiveTick(pair, timeframe);
        if (isCancelled || !res || !res.candle || !seriesRef.current) return;
        const candle = {
          time: Number(res.candle.time),
          open: Number(res.candle.open),
          high: Number(res.candle.high),
          low: Number(res.candle.low),
          close: Number(res.candle.close),
        };
        seriesRef.current.update(candle);
        setCurrentCandle(candle);
        setCandleStats((prev) => ({
          high: Math.max(prev.high || 0, candle.high),
          low: prev.low ? Math.min(prev.low, candle.low) : candle.low,
          volume: prev.volume,
        }));
        if (onPriceUpdate) {
          const firstOpen = firstCandleOpenRef.current;
          const chg = firstOpen ? ((candle.close - firstOpen) / firstOpen) * 100 : 0;
          onPriceUpdate(candle.close, chg);
        }
      } catch (err) {
        // Silently continue polling
      }
    };

    const tickInterval = setInterval(pollTick, 1000);

    return () => {
      isCancelled = true;
      clearInterval(tickInterval);
      resizeObserver.disconnect();
      if (chartInstanceRef.current) {
        chartInstanceRef.current.remove();
        chartInstanceRef.current = null;
      }
    };
  }, [pair, timeframe]);

  const isUp = currentCandle && currentCandle.close >= currentCandle.open;
  const priceDecimals = pair.includes('JPY') ? 3 :
    (pair.includes('BTC') || pair.includes('ETH') || pair.includes('SOL')) ? 2 : 5;

  return (
    <div className="flex flex-col h-full bg-[#151D2A] border border-[#2A3447] rounded-xl overflow-hidden shadow-xl">
      {/* Chart Top Bar / Stats */}
      <div className="flex flex-wrap items-center justify-between px-4 py-2.5 bg-[#111722] border-b border-[#2A3447] text-xs font-mono">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5 font-bold text-sm text-[#F3F4F6]">
            <span>{pair}</span>
            <span className="text-[#9CA3AF] text-xs font-normal">
              [{timeframe}]
            </span>
          </div>

          {/* Dynamic Real-Time Moving Candle Pulse */}
          <div className="hidden sm:flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-[#0B0E14] border border-[#10B981]/30">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#10B981] opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-[#10B981]"></span>
            </span>
            <span className="text-[10px] font-semibold text-[#10B981] tracking-wider">LIVE 1S</span>
          </div>

          {currentCandle && (
            <div className="flex items-center gap-3 text-[11px] text-[#9CA3AF]">
              <span>
                O: <span className="text-[#F3F4F6]">{currentCandle.open.toFixed(priceDecimals)}</span>
              </span>
              <span>
                H: <span className="text-[#10B981]">{currentCandle.high.toFixed(priceDecimals)}</span>
              </span>
              <span>
                L: <span className="text-[#EF4444]">{currentCandle.low.toFixed(priceDecimals)}</span>
              </span>
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
            <span>
              24h High: <span className="text-[#10B981]">{candleStats.high.toFixed(priceDecimals)}</span>
            </span>
            <span>
              24h Low: <span className="text-[#EF4444]">{candleStats.low.toFixed(priceDecimals)}</span>
            </span>
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

      {/* Chart Canvas Container */}
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

