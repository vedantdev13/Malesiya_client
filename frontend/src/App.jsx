import React, { useState, useEffect, useCallback, useRef } from 'react';
import Header from './components/Header';
import TradingChart from './components/TradingChart';
import PerformanceBar from './components/PerformanceBar';
import {
  checkHealth,
  WS_BASE_URL,
  WS_MARKET_URL,
  generateSignal,
  trainPrimaryFx,
} from './services/api';
import { useWebSocket } from './hooks/useWebSocket';
import {
  Zap,
  TrendingUp,
  TrendingDown,
  Cpu,
  BarChart2,
  Clock,
  ShieldCheck,
  Radio,
  Timer,
  AlertCircle,
  CheckCircle2,
} from 'lucide-react';

const isSamePair = (p1, p2) => {
  if (!p1 || !p2) return false;
  const clean = (s) => String(s).toUpperCase().replace(/[\/=_\-\s]/g, '');
  const s1 = clean(p1);
  const s2 = clean(p2);
  if (s1 === s2) return true;
  return s1.replace(/X$/, '') === s2.replace(/X$/, '');
};

const TF_SECONDS = { '1m': 60, '5m': 300, '15m': 900 };

export default function App() {
  // 100% Focused on Quotex Forex (EUR/USD, GBP/USD, USD/JPY) on 1m
  const [selectedPair, setSelectedPair]               = useState('EURUSD=X');
  const [selectedTimeframe, setSelectedTimeframe]     = useState('1m');
  const [isBackendConnected, setIsBackendConnected]   = useState(false);
  const [lastPrice, setLastPrice]                     = useState(null);
  const [priceChange, setPriceChange]                 = useState(0);
  const [confidenceThreshold, setConfidenceThreshold] = useState(0.85);

  const [activeSignal, setActiveSignal]               = useState(null);
  const [signalHistory, setSignalHistory]             = useState([]);
  const [isGeneratingSignal, setIsGeneratingSignal]   = useState(false);
  const [isTrainingFx, setIsTrainingFx]               = useState(false);
  const [actionNotice, setActionNotice]               = useState(null);
  const [perfRefreshKey, setPerfRefreshKey]           = useState(0);

  // Live candle countdown tracker
  const [candleSecondsRemaining, setCandleSecondsRemaining] = useState(60);

  const selectedPairRef = useRef(selectedPair);
  useEffect(() => {
    selectedPairRef.current = selectedPair;
  }, [selectedPair]);

  // ─── Backend health polling ──────────────────────────────────────────────
  useEffect(() => {
    let isMounted = true;
    const verifyHealth = async () => {
      try {
        const res = await checkHealth();
        if (isMounted) setIsBackendConnected(res?.status === 'online');
      } catch {
        if (isMounted) setIsBackendConnected(false);
      }
    };
    verifyHealth();
    const id = setInterval(verifyHealth, 8000);
    return () => { isMounted = false; clearInterval(id); };
  }, []);

  // ─── Live Clock Timer for Candle Countdown ───────────────────────────────
  useEffect(() => {
    const secs = TF_SECONDS[selectedTimeframe] || 60;
    const updateCountdown = () => {
      const nowSec = Math.floor(Date.now() / 1000);
      const rem = secs - (nowSec % secs);
      setCandleSecondsRemaining(rem === 0 ? secs : rem);
    };
    updateCountdown();
    const timer = setInterval(updateCountdown, 250);
    return () => clearInterval(timer);
  }, [selectedTimeframe]);

  // ─── Resilient WebSocket hook for Signal Outcomes ─────────────────────────
  const handleWsMessage = useCallback((parsed) => {
    if (parsed.event === 'NEW_SIGNAL') {
      const sig = parsed.data;
      setSignalHistory(prev => [sig, ...prev.filter(s => s.id !== sig.id)].slice(0, 50));
      if (isSamePair(sig.pair, selectedPairRef.current)) {
        setActiveSignal(sig);
      }
    }

    if (parsed.event === 'SIGNAL_RESOLVED') {
      const resolved = parsed.data;
      setActiveSignal(prev => (prev?.id === resolved.id ? resolved : prev));
      setSignalHistory(prev =>
        prev.map(s => (s.id === resolved.id ? resolved : s))
      );
      setPerfRefreshKey(k => k + 1);
    }
  }, []);

  const { status: wsStatus } = useWebSocket(WS_BASE_URL, {
    onMessage: handleWsMessage,
  });

  // ─── Real-Time Auto-Settlement of PENDING Signals ────────────────────────
  useEffect(() => {
    if (!lastPrice) return;

    const interval = setInterval(() => {
      const now = Date.now();
      let updatedAny = false;

      setSignalHistory(prevHistory => {
        const newHistory = prevHistory.map(sig => {
          if (sig.status !== 'PENDING' || !sig.entry_price) return sig;

          // Determine expiry timestamp in milliseconds
          let expiryMs = null;
          if (sig.expiry_timestamp) {
            expiryMs = new Date(sig.expiry_timestamp).getTime();
          } else if (sig.timestamp) {
            const tfSec = TF_SECONDS[sig.timeframe] || 60;
            expiryMs = new Date(sig.timestamp).getTime() + (tfSec * 1000);
          }

          // If expired, evaluate binary outcome against live price
          if (expiryMs && now >= expiryMs) {
            let outcome = 'DRAW';
            if (sig.direction === 'UP') {
              outcome = lastPrice > sig.entry_price ? 'WIN' : (lastPrice < sig.entry_price ? 'LOSS' : 'DRAW');
            } else if (sig.direction === 'DOWN') {
              outcome = lastPrice < sig.entry_price ? 'WIN' : (lastPrice > sig.entry_price ? 'LOSS' : 'DRAW');
            }

            if (outcome !== 'PENDING') {
              updatedAny = true;
              return {
                ...sig,
                status: outcome,
                exit_price: lastPrice,
                resolved_at: new Date().toISOString(),
              };
            }
          }
          return sig;
        });

        if (updatedAny) {
          // Sync activeSignal if it matches the resolved signal
          setActiveSignal(curActive => {
            if (!curActive || curActive.status !== 'PENDING') return curActive;
            const match = newHistory.find(s => s.id === curActive.id);
            return match || curActive;
          });
          setPerfRefreshKey(k => k + 1);
          return newHistory;
        }
        return prevHistory;
      });
    }, 1000);

    return () => clearInterval(interval);
  }, [lastPrice]);

  // ─── Live Market WebSocket for Price Sync ─────────────────────────────────
  const handleMarketTick = useCallback((msg) => {
    if (msg.event !== 'MARKET_TICK' || !msg.ticks) return;
    const curPair = selectedPairRef.current;
    const tickData = msg.ticks[curPair];
    if (tickData?.price) {
      setLastPrice(Number(tickData.price));
    }
  }, []);

  useWebSocket(WS_MARKET_URL, {
    onMessage: handleMarketTick,
    pingIntervalMs: 20000,
  });

  // Switch pair and automatically surface latest signal for newly selected asset
  const handleSelectPair = (newPair) => {
    setSelectedPair(newPair);
    setSignalHistory(prev => {
      const latestForPair = prev.find(s => isSamePair(s.pair, newPair)) || null;
      setActiveSignal(latestForPair);
      return prev;
    });
  };

  // ─── Chart price update callback ──────────────────────────────────────────
  const handlePriceUpdate = (price, change) => {
    setLastPrice(price);
    setPriceChange(change);
  };

  // ─── Instant 1-Click Signal Inference (< 1-2 Seconds) ─────────────────────
  const handleRequestSignal = async () => {
    setIsGeneratingSignal(true);
    setActionNotice(null);
    try {
      const res = await generateSignal(selectedPair, selectedTimeframe, confidenceThreshold);
      setActiveSignal(res);
      setSignalHistory(prev => [res, ...prev.filter(s => s.id !== res.id)].slice(0, 20));

      const isCall = res.direction === 'UP';
      const isPut = res.direction === 'DOWN';
      const isWait = !isCall && !isPut;
      const label = isCall ? 'UP (CALL)' : isPut ? 'DOWN (PUT)' : 'WAIT — Low Edge';
      setActionNotice({
        type: isWait ? 'warning' : 'success',
        text: isWait
          ? 'Signal Generated: WAIT — Low Edge (Skip This Candle)'
          : `Signal Generated: ${label} @ ${(res.confidence * 100).toFixed(1)}% confidence`,
      });
    } catch (err) {
      setActionNotice({
        type: 'error',
        text: 'Signal error: ' + (err.response?.data?.detail || err.message),
      });
    } finally {
      setIsGeneratingSignal(false);
    }
  };

  // ─── Primary Quotex Forex Training (60,000 Bars) ──────────────────────────
  const handleTrainPrimaryFx = async () => {
    setIsTrainingFx(true);
    setActionNotice(null);
    try {
      const res = await trainPrimaryFx(selectedTimeframe);
      setActionNotice({
        type: 'success',
        text: `Quotex Forex Suite Trained! ${res.total_candles?.toLocaleString()} real bars saved in ${res.elapsed_seconds}s.`,
      });
      setPerfRefreshKey(k => k + 1);
    } catch (err) {
      setActionNotice({
        type: 'error',
        text: 'Forex training error: ' + (err.response?.data?.detail || err.message),
      });
    } finally {
      setIsTrainingFx(false);
    }
  };

  const thresholdPct = Math.round(confidenceThreshold * 100);
  const isHighPrecision = thresholdPct >= 80;
  const dec = selectedPair.includes('JPY') ? 3 : 5;

  const fmtTime = (secs) => {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  };

  return (
    <div className="min-h-screen bg-[#0B0E14] text-[#F3F4F6] flex flex-col font-sans">
      {/* ── Top Nav ──────────────────────────────────────────────────────── */}
      <Header
        selectedPair={selectedPair}
        onSelectPair={handleSelectPair}
        selectedTimeframe={selectedTimeframe}
        onSelectTimeframe={setSelectedTimeframe}
        isBackendConnected={isBackendConnected}
        lastPrice={lastPrice}
        priceChange={priceChange}
        confidenceThreshold={confidenceThreshold}
        onConfidenceChange={setConfidenceThreshold}
      />

      {/* ── Main workspace ───────────────────────────────────────────────── */}
      <main className="flex-1 p-4 sm:p-6 max-w-[1600px] w-full mx-auto flex flex-col gap-4">

        {/* Performance Bar */}
        <PerformanceBar
          pair={selectedPair}
          timeframe={selectedTimeframe}
          refreshTrigger={perfRefreshKey}
          onReset={() => {
            setSignalHistory([]);
            setActiveSignal(null);
            setActionNotice({
              type: 'success',
              text: 'Performance stats and signal history have been reset to zero baseline.',
            });
          }}
        />

        {/* Notification Toast */}
        {actionNotice && (
          <div
            className={`px-4 py-2.5 rounded-lg border text-xs font-mono flex items-center justify-between ${
              actionNotice.type === 'success'
                ? 'bg-[#10B981]/15 border-[#10B981]/40 text-[#10B981]'
                : actionNotice.type === 'warning'
                ? 'bg-[#F59E0B]/15 border-[#F59E0B]/40 text-[#F59E0B]'
                : 'bg-[#EF4444]/15 border-[#EF4444]/40 text-[#EF4444]'
            }`}
          >
            <div className="flex items-center gap-2">
              {actionNotice.type === 'success' && <CheckCircle2 className="w-4 h-4" />}
              {actionNotice.type === 'warning' && <AlertCircle className="w-4 h-4" />}
              <span>{actionNotice.text}</span>
            </div>
            <button onClick={() => setActionNotice(null)} className="text-[#9CA3AF] hover:text-white ml-4">✕</button>
          </div>
        )}

        {/* ── Dashboard Grid: Chart (3 cols) + Single-Market Focus Tracker (1 col) ── */}
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-4 flex-1">

          {/* Left: Trading Chart (3 columns) */}
          <div className="lg:col-span-3 flex flex-col gap-4">
            <div className="h-[520px] w-full">
              <TradingChart
                pair={selectedPair}
                timeframe={selectedTimeframe}
                onPriceUpdate={handlePriceUpdate}
              />
            </div>

            {/* Metric Strip */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div className="bg-[#151D2A] border border-[#2A3447] rounded-lg p-3">
                <div className="flex items-center justify-between text-[#9CA3AF] text-xs">
                  <span>Selected Asset</span>
                  <BarChart2 className="w-3.5 h-3.5 text-[#3B82F6]" />
                </div>
                <div className="font-mono text-sm font-bold text-white mt-1 truncate">
                  {selectedPair.replace('=X', ' (Quotex FX)')}
                </div>
              </div>

              <div className="bg-[#151D2A] border border-[#2A3447] rounded-lg p-3">
                <div className="flex items-center justify-between text-[#9CA3AF] text-xs">
                  <span>Candle / Expiry</span>
                  <Clock className="w-3.5 h-3.5 text-[#10B981]" />
                </div>
                <div className="font-mono text-sm font-bold text-white mt-1">{selectedTimeframe} Binary Expiry</div>
              </div>

              <div className="bg-[#151D2A] border border-[#2A3447] rounded-lg p-3">
                <div className="flex items-center justify-between text-[#9CA3AF] text-xs">
                  <span>Sensitivity Mode</span>
                  <ShieldCheck className={`w-3.5 h-3.5 ${isHighPrecision ? 'text-[#F59E0B]' : 'text-[#10B981]'}`} />
                </div>
                <div className={`font-mono text-sm font-bold mt-1 ${isHighPrecision ? 'text-[#10B981]' : 'text-[#F59E0B]'}`}>
                  {isHighPrecision ? 'High Precision (85%)' : `Balanced (${thresholdPct}%)`}
                </div>
              </div>

              <div className="bg-[#151D2A] border border-[#2A3447] rounded-lg p-3">
                <div className="flex items-center justify-between text-[#9CA3AF] text-xs">
                  <span>Quotex Stream</span>
                  <Radio className={`w-3.5 h-3.5 ${wsStatus === 'open' ? 'text-[#10B981]' : wsStatus === 'connecting' ? 'text-[#F59E0B]' : 'text-[#EF4444]'}`} />
                </div>
                <div className={`font-mono text-sm font-bold mt-1 ${wsStatus === 'open' ? 'text-[#10B981]' : wsStatus === 'connecting' ? 'text-[#F59E0B]' : 'text-[#EF4444]'}`}>
                  {wsStatus === 'open' ? '1s Active' : wsStatus === 'connecting' ? 'Connecting' : 'Offline'}
                </div>
              </div>
            </div>
          </div>

          {/* Right: Single-Market Live Signal & Candle Tracker (1 column) */}
          <div className="flex flex-col gap-4">

            {/* Single-Market Header & Action Card */}
            <div className="bg-[#151D2A] border border-[#2A3447] rounded-xl p-4 flex flex-col gap-3 shadow-lg">
              <div className="flex items-center justify-between pb-2 border-b border-[#2A3447]">
                <div className="flex items-center gap-1.5 font-bold text-xs text-[#F3F4F6]">
                  <Timer className="w-4 h-4 text-[#10B981]" />
                  <span>SINGLE-MARKET TRACKER</span>
                </div>
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[#10B981]/15 text-[#10B981] font-bold border border-[#10B981]/30">
                  {selectedPair.replace('=X', '')} [{selectedTimeframe}]
                </span>
              </div>

              {/* Next Candle Countdown & Timing Box */}
              <div className="p-3 rounded-lg bg-[#0B0E14] border border-[#2A3447] flex items-center justify-between font-mono">
                <div>
                  <div className="text-[10px] text-[#9CA3AF] uppercase font-semibold">Candle Close In</div>
                  <div className="text-xl font-bold text-[#F59E0B] tracking-wider mt-0.5">
                    ⏱ {fmtTime(candleSecondsRemaining)}
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-[10px] text-[#9CA3AF] uppercase font-semibold">Execution Timing</div>
                  <div className={`text-xs font-bold mt-0.5 ${candleSecondsRemaining <= 5 ? 'text-[#10B981] animate-pulse' : 'text-[#D1D5DB]'}`}>
                    {candleSecondsRemaining <= 5 ? '⚡ ENTRY READY' : 'Next Candle Open'}
                  </div>
                </div>
              </div>

              {/* Primary Instant Signal Button */}
              <button
                onClick={handleRequestSignal}
                disabled={isGeneratingSignal}
                className="w-full flex items-center justify-center gap-2 py-3 px-4 bg-gradient-to-r from-[#10B981] to-[#059669] hover:from-[#059669] hover:to-[#047857] text-[#0B0E14] font-black text-xs uppercase tracking-wider rounded-lg transition-all shadow-lg shadow-[#10B981]/15 disabled:opacity-50"
              >
                <Zap className={`w-4 h-4 ${isGeneratingSignal ? 'animate-bounce' : ''}`} />
                <span>{isGeneratingSignal ? 'Analyzing Confluence...' : 'Get Signal Now (Instant)'}</span>
              </button>

              {/* Train All Quotex FX Button */}
              <button
                onClick={handleTrainPrimaryFx}
                disabled={isTrainingFx}
                className="w-full flex items-center justify-center gap-2 py-2 px-3 bg-[#0B0E14] hover:bg-[#121E19] border border-[#10B981]/40 hover:border-[#10B981] text-[#10B981] font-semibold text-xs rounded-lg transition-all shadow-sm disabled:opacity-50"
              >
                <Cpu className={`w-3.5 h-3.5 ${isTrainingFx ? 'animate-spin text-[#10B981]' : ''}`} />
                <span>
                  {isTrainingFx
                    ? 'Training All Quotex FX (60,000 Bars)...'
                    : 'Train All Quotex FX (60,000 Bars)'}
                </span>
              </button>
            </div>

            {/* Active Signal Details & Entry Instruction */}
            <div className="bg-[#151D2A] border border-[#2A3447] rounded-xl p-4 flex flex-col gap-3 shadow-lg">
              <div className="flex items-center justify-between pb-2 border-b border-[#2A3447]">
                <span className="text-xs font-semibold uppercase tracking-wider text-[#9CA3AF]">
                  Active Signal & Entry Setup
                </span>
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#10B981] opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-[#10B981]"></span>
                </span>
              </div>

              {activeSignal ? (
                <div className="flex flex-col gap-3 font-mono text-xs">
                  {/* Direction Badge */}
                  <div
                    className={`p-3 rounded-lg border flex items-center justify-between shadow-md ${
                      activeSignal.direction === 'UP'
                        ? 'bg-[#10B981]/15 border-[#10B981]/50 text-[#10B981]'
                        : activeSignal.direction === 'DOWN'
                        ? 'bg-[#EF4444]/15 border-[#EF4444]/50 text-[#EF4444]'
                        : 'bg-[#F59E0B]/15 border-[#F59E0B]/50 text-[#F59E0B]'
                    }`}
                  >
                    <div className="flex items-center gap-2 font-black text-base">
                      {activeSignal.direction === 'UP'   && <TrendingUp   className="w-5 h-5 text-[#10B981]" />}
                      {activeSignal.direction === 'DOWN' && <TrendingDown className="w-5 h-5 text-[#EF4444]" />}
                      {(activeSignal.direction === 'NO_SIGNAL' || activeSignal.direction === 'WAIT') && <AlertCircle className="w-5 h-5 text-[#F59E0B]" />}
                      <span>
                        {activeSignal.direction === 'UP' ? 'UP (CALL)' : activeSignal.direction === 'DOWN' ? 'DOWN (PUT)' : 'WAIT — Low Edge'}
                      </span>
                    </div>
                    <span className={`text-xs px-2.5 py-1 rounded bg-[#0B0E14] font-bold border ${
                      activeSignal.direction === 'NO_SIGNAL' || activeSignal.direction === 'WAIT'
                        ? 'text-[#F59E0B] border-[#F59E0B]/30'
                        : 'text-white border-[#2A3447]'
                    }`}>
                      {activeSignal.direction === 'NO_SIGNAL' || activeSignal.direction === 'WAIT'
                        ? 'SKIP CANDLE'
                        : `${(activeSignal.confidence * 100).toFixed(1)}% CONF`}
                    </span>
                  </div>

                  {/* Prominent Next-Candle Entry Instruction Banner */}
                  <div className={`p-2.5 rounded-lg border font-mono text-xs leading-relaxed ${
                    activeSignal.direction === 'UP'
                      ? 'bg-[#10B981]/10 border-[#10B981]/30 text-[#10B981]'
                      : activeSignal.direction === 'DOWN'
                      ? 'bg-[#EF4444]/10 border-[#EF4444]/30 text-[#EF4444]'
                      : 'bg-[#F59E0B]/10 border-[#F59E0B]/30 text-[#F59E0B]'
                  }`}>
                    <div className="text-[10px] font-bold uppercase tracking-wider text-[#9CA3AF] mb-0.5">
                      Execution Instruction:
                    </div>
                    <div className="font-bold text-[11px]">
                      {activeSignal.entry_instruction || (
                        activeSignal.direction === 'UP'
                          ? `ENTER UP (CALL) AT NEXT CANDLE OPEN (in ${candleSecondsRemaining}s)`
                          : activeSignal.direction === 'DOWN'
                          ? `ENTER DOWN (PUT) AT NEXT CANDLE OPEN (in ${candleSecondsRemaining}s)`
                          : `WAIT — Low Edge (Skip This Candle) (in ${candleSecondsRemaining}s)`
                      )}
                    </div>
                  </div>

                  {/* Signal Meta */}
                  <div className="space-y-2 text-[#9CA3AF] bg-[#0B0E14] p-3 rounded-lg border border-[#2A3447]">
                    <div className="flex justify-between">
                      <span>Asset:</span>
                      <span className="text-white font-bold">{selectedPair.replace('=X',' (Quotex FX)')}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Entry Level:</span>
                      <span className="text-white font-bold">{activeSignal.entry_price?.toFixed(dec)}</span>
                    </div>
                    {activeSignal.confluence_score != null && (
                      <div className="flex justify-between">
                        <span>Binary Confluence:</span>
                        <span className="text-[#10B981] font-bold">{(activeSignal.confluence_score * 100).toFixed(0)}%</span>
                      </div>
                    )}
                    {activeSignal.status_reason && (
                      <div className="text-[10px] text-[#D1D5DB] bg-[#1F2937]/50 p-1.5 rounded border border-[#374151]">
                        {activeSignal.status_reason}
                      </div>
                    )}
                    <div className="flex justify-between items-center">
                      <span>Outcome:</span>
                      <span className={`font-bold px-2 py-0.5 rounded text-[11px] ${
                        activeSignal.status === 'WIN'  ? 'bg-[#10B981]/20 text-[#10B981]' :
                        activeSignal.status === 'LOSS' ? 'bg-[#EF4444]/20 text-[#EF4444]' :
                        activeSignal.direction === 'NO_SIGNAL' || activeSignal.direction === 'WAIT' ? 'bg-[#F59E0B]/20 text-[#F59E0B]' :
                        'bg-[#2A3447] text-[#9CA3AF]'
                      }`}>
                        {activeSignal.direction === 'NO_SIGNAL' || activeSignal.direction === 'WAIT' ? 'SKIPPED' : activeSignal.status}
                      </span>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="flex-1 flex flex-col items-center justify-center text-center p-6 text-[#9CA3AF]">
                  <Zap className="w-8 h-8 text-[#2A3447] mb-2" />
                  <p className="text-xs font-semibold text-white">No active signal yet.</p>
                  <p className="text-[11px] text-[#6B7280] mt-1">
                    Click "Get Signal Now (Instant)" above to generate an immediate prediction for {selectedPair.replace('=X', '')}.
                  </p>
                </div>
              )}
            </div>

            {/* Filtered Signal History for Selected Asset */}
            {(() => {
              const pairHistory = signalHistory.filter(s => isSamePair(s.pair, selectedPair));
              return (
                <div className="bg-[#151D2A] border border-[#2A3447] rounded-xl p-4 shadow-lg">
                  <div className="flex items-center justify-between pb-2 border-b border-[#2A3447] mb-2">
                    <span className="text-xs font-semibold uppercase tracking-wider text-[#9CA3AF]">
                      Recent Signals Log
                    </span>
                    <span className="text-[10px] font-mono font-semibold text-[#10B981] bg-[#10B981]/10 px-2 py-0.5 rounded border border-[#10B981]/25">
                      {selectedPair.replace('=X', '')}
                    </span>
                  </div>
                  {pairHistory.length > 0 ? (
                    <div className="space-y-1.5 max-h-[200px] overflow-y-auto pr-1">
                      {pairHistory.slice(0, 10).map((s) => (
                        <div key={s.id} className="flex items-center justify-between text-[11px] font-mono py-1 border-b border-[#2A3447]/50 last:border-0">
                          <div className="flex items-center gap-1.5">
                            <span className={
                              s.direction === 'UP' ? 'text-[#10B981] font-bold' :
                              s.direction === 'DOWN' ? 'text-[#EF4444] font-bold' :
                              'text-[#9CA3AF]'
                            }>
                              {s.direction === 'UP' ? '▲ CALL' : s.direction === 'DOWN' ? '▼ PUT' : '· WAIT'}
                            </span>
                            <span className="text-white font-semibold">{(s.confidence * 100).toFixed(0)}%</span>
                          </div>
                          <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                            s.status === 'WIN'  ? 'bg-[#10B981]/15 text-[#10B981]' :
                            s.status === 'LOSS' ? 'bg-[#EF4444]/15 text-[#EF4444]' :
                            s.status === 'PENDING' ? 'bg-[#3B82F6]/15 text-[#3B82F6] animate-pulse' :
                            s.direction === 'NO_SIGNAL' || s.direction === 'WAIT' ? 'bg-[#F59E0B]/15 text-[#F59E0B]' :
                            'bg-[#2A3447] text-[#9CA3AF]'
                          }`}>
                            {s.status === 'PENDING' ? 'LIVE' : (s.direction === 'NO_SIGNAL' || s.direction === 'WAIT' ? 'SKIP' : s.status)}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="py-4 text-center text-[#6B7280] text-xs">
                      No recorded signals for this asset yet.
                    </div>
                  )}
                </div>
              );
            })()}

          </div>
        </div>
      </main>
    </div>
  );
}
