import React, { useState, useEffect, useCallback, useRef } from 'react';
import Header from './components/Header';
import TradingChart from './components/TradingChart';
import PerformanceBar from './components/PerformanceBar';
import {
  checkHealth,
  WS_BASE_URL,
  WS_MARKET_URL,
  generateSignal,
  trainModel,
  trainPrimaryFx,
  trainSecondaryCrypto,
  trainPrioritySuite,
  scanAllOpportunities,
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
  Radar,
  RefreshCw,
} from 'lucide-react';

// Robust asset pair comparison helper
const isSamePair = (p1, p2) => {
  if (!p1 || !p2) return false;
  const clean = (s) => String(s).toUpperCase().replace(/[\/=_\-\s]/g, '');
  const s1 = clean(p1);
  const s2 = clean(p2);
  if (s1 === s2) return true;
  return s1.replace(/X$/, '') === s2.replace(/X$/, '');
};

export default function App() {
  const [selectedPair, setSelectedPair]           = useState('EURUSD=X');
  const [selectedTimeframe, setSelectedTimeframe] = useState('5m');
  const [isBackendConnected, setIsBackendConnected] = useState(false);
  const [lastPrice, setLastPrice]                 = useState(null);
  const [priceChange, setPriceChange]             = useState(0);
  const [confidenceThreshold, setConfidenceThreshold] = useState(0.60);

  const [activeSignal, setActiveSignal]           = useState(null);
  const [signalHistory, setSignalHistory]         = useState([]);
  const [isGeneratingSignal, setIsGeneratingSignal] = useState(false);
  const [isTrainingFx, setIsTrainingFx]           = useState(false);
  const [isTrainingCrypto, setIsTrainingCrypto]   = useState(false);
  const [actionNotice, setActionNotice]           = useState(null);
  const [perfRefreshKey, setPerfRefreshKey]       = useState(0);

  // Scanner state
  const [opportunities, setOpportunities]         = useState([]);
  const [isScanning, setIsScanning]               = useState(false);
  const [lastScannedAt, setLastScannedAt]         = useState(null);

  // Keep a stable ref to selectedPair for WebSocket callback
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

  // ─── Resilient WebSocket hook with heartbeat & asset filtering ───────────
  const handleWsMessage = useCallback((parsed) => {
    if (parsed.event === 'NEW_SIGNAL') {
      const sig = parsed.data;
      // Append to global history (keep latest 50, deduped by id)
      setSignalHistory(prev => [sig, ...prev.filter(s => s.id !== sig.id)].slice(0, 50));

      // CRITICAL FIX: Only update activeSignal if it matches currently selected pair!
      if (isSamePair(sig.pair, selectedPairRef.current)) {
        setActiveSignal(sig);
      }
    }

    if (parsed.event === 'SIGNAL_RESOLVED') {
      const resolved = parsed.data;
      // Update active signal if it matches
      setActiveSignal(prev => (prev?.id === resolved.id ? resolved : prev));
      // Update history entry
      setSignalHistory(prev =>
        prev.map(s => (s.id === resolved.id ? resolved : s))
      );
      // Refresh performance bar stats
      setPerfRefreshKey(k => k + 1);
    }
  }, []);

  const { status: wsStatus } = useWebSocket(WS_BASE_URL, {
    onMessage: handleWsMessage,
  });

  // ─── Market tick WebSocket (for header live price sync) ───────────────────
  const handleMarketTick = useCallback((msg) => {
    if (msg.event !== 'MARKET_TICK' || !msg.ticks) return;
    const curPair = selectedPairRef.current;
    const tickData = msg.ticks[curPair];
    if (!tickData) return;
    const { price } = tickData;
    if (price) {
      setLastPrice(Number(price));
      // Rough 24h change from first open (chart provides authoritative value via onPriceUpdate)
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

  // ─── Multi-Asset Live Opportunity Scanner ────────────────────────────────
  const handleScanAll = useCallback(async (customThreshold) => {
    setIsScanning(true);
    try {
      const th = customThreshold !== undefined ? customThreshold : confidenceThreshold;
      const res = await scanAllOpportunities(selectedTimeframe, th);
      if (res?.opportunities) {
        setOpportunities(res.opportunities);
        setLastScannedAt(new Date());
      }
    } catch (err) {
      console.error('Scan all opportunities failed:', err);
    } finally {
      setIsScanning(false);
    }
  }, [selectedTimeframe, confidenceThreshold]);

  // Auto-scan on mount and every 30s
  useEffect(() => {
    handleScanAll();
    const timer = setInterval(() => {
      handleScanAll();
    }, 30000);
    return () => clearInterval(timer);
  }, [handleScanAll]);

  // Handle clicking an opportunity row in scanner
  const handleOpportunityClick = (opp) => {
    handleSelectPair(opp.pair);
    if (opp.signal === 'UP' || opp.signal === 'DOWN') {
      setActiveSignal({
        id: `scan-${opp.pair}-${Date.now()}`,
        pair: opp.pair,
        timeframe: selectedTimeframe,
        direction: opp.signal,
        confidence: opp.confidence,
        confluence_score: opp.confluence_score,
        entry_price: opp.entry_price,
        status: 'PENDING',
        gate_reason: opp.status_reason,
        timestamp: opp.timestamp || new Date().toISOString(),
      });
    }
  };

  // ─── Chart price update callback ────────────────────────────────────────
  const handlePriceUpdate = (price, change) => {
    setLastPrice(price);
    setPriceChange(change);
  };

  // ─── Signal inference ────────────────────────────────────────────────────
  const handleRequestSignal = async () => {
    setIsGeneratingSignal(true);
    setActionNotice(null);
    try {
      const res = await generateSignal(selectedPair, selectedTimeframe, confidenceThreshold);
      setActiveSignal(res);
      setSignalHistory(prev => [res, ...prev].slice(0, 20));
      setActionNotice({
        type: 'success',
        text: `Signal: ${res.direction} @ ${(res.confidence * 100).toFixed(1)}% confidence`,
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

  // ─── Primary FX Training (60,000 Bars: EUR/USD, GBP/USD, USD/JPY) ────────
  const handleTrainPrimaryFx = async () => {
    setIsTrainingFx(true);
    setActionNotice(null);
    try {
      const res = await trainPrimaryFx(selectedTimeframe);
      setActionNotice({
        type: 'success',
        text: `Primary Forex Suite (60,000 Bars) Trained! ${res.total_candles?.toLocaleString()} real bars saved in ${res.elapsed_seconds}s.`,
      });
      setPerfRefreshKey(k => k + 1);
      handleScanAll();
    } catch (err) {
      setActionNotice({
        type: 'error',
        text: 'Primary FX training error: ' + (err.response?.data?.detail || err.message),
      });
    } finally {
      setIsTrainingFx(false);
    }
  };

  // ─── Secondary Crypto Training (40,000 Bars: BTC, ETH, SOL) ──────────────
  const handleTrainSecondaryCrypto = async () => {
    setIsTrainingCrypto(true);
    setActionNotice(null);
    try {
      const res = await trainSecondaryCrypto(selectedTimeframe);
      setActionNotice({
        type: 'success',
        text: `Secondary Crypto Suite (40,000 Bars) Trained! ${res.total_candles?.toLocaleString()} real bars saved in ${res.elapsed_seconds}s.`,
      });
      setPerfRefreshKey(k => k + 1);
      handleScanAll();
    } catch (err) {
      setActionNotice({
        type: 'error',
        text: 'Crypto training error: ' + (err.response?.data?.detail || err.message),
      });
    } finally {
      setIsTrainingCrypto(false);
    }
  };

  // ─── Precision mode label ────────────────────────────────────────────────
  const thresholdPct = Math.round(confidenceThreshold * 100);
  const isHighPrecision = thresholdPct >= 75;

  // ─── Decimal precision per asset ────────────────────────────────────────
  const getDecimals = (pair) => {
    if (pair.includes('JPY')) return 3;
    if (pair.includes('BTC') || pair.includes('ETH') || pair.includes('SOL')) return 2;
    return 5;
  };
  const dec = getDecimals(selectedPair);

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
        />

        {/* Notification Toast */}
        {actionNotice && (
          <div
            className={`px-4 py-2.5 rounded-lg border text-xs font-mono flex items-center justify-between ${
              actionNotice.type === 'success'
                ? 'bg-[#10B981]/10 border-[#10B981]/30 text-[#10B981]'
                : 'bg-[#EF4444]/10 border-[#EF4444]/30 text-[#EF4444]'
            }`}
          >
            <span>{actionNotice.text}</span>
            <button onClick={() => setActionNotice(null)} className="text-[#9CA3AF] hover:text-white ml-4">✕</button>
          </div>
        )}

        {/* ── Dashboard Grid ──────────────────────────────────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-4 flex-1">

          {/* Chart (3 columns) */}
          <div className="lg:col-span-3 flex flex-col gap-4">
            <div className="h-[500px] w-full">
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
                  <span>Asset</span>
                  <BarChart2 className="w-3.5 h-3.5 text-[#3B82F6]" />
                </div>
                <div className="font-mono text-sm font-bold text-white mt-1 truncate">
                  {selectedPair.replace('=X', ' (FX)')}
                </div>
              </div>

              <div className="bg-[#151D2A] border border-[#2A3447] rounded-lg p-3">
                <div className="flex items-center justify-between text-[#9CA3AF] text-xs">
                  <span>Candle / Expiry</span>
                  <Clock className="w-3.5 h-3.5 text-[#10B981]" />
                </div>
                <div className="font-mono text-sm font-bold text-white mt-1">{selectedTimeframe}</div>
              </div>

              <div className="bg-[#151D2A] border border-[#2A3447] rounded-lg p-3">
                <div className="flex items-center justify-between text-[#9CA3AF] text-xs">
                  <span>Filter Mode</span>
                  <ShieldCheck className={`w-3.5 h-3.5 ${isHighPrecision ? 'text-[#F59E0B]' : 'text-[#10B981]'}`} />
                </div>
                <div className={`font-mono text-sm font-bold mt-1 ${isHighPrecision ? 'text-[#F59E0B]' : 'text-[#10B981]'}`}>
                  {isHighPrecision ? 'High Precision' : `Standard ${thresholdPct}%`}
                </div>
              </div>

              <div className="bg-[#151D2A] border border-[#2A3447] rounded-lg p-3">
                <div className="flex items-center justify-between text-[#9CA3AF] text-xs">
                  <span>WS Stream</span>
                  <Radio className={`w-3.5 h-3.5 ${wsStatus === 'open' ? 'text-[#10B981]' : wsStatus === 'connecting' ? 'text-[#F59E0B]' : 'text-[#EF4444]'}`} />
                </div>
                <div className={`font-mono text-sm font-bold mt-1 ${wsStatus === 'open' ? 'text-[#10B981]' : wsStatus === 'connecting' ? 'text-[#F59E0B]' : 'text-[#EF4444]'}`}>
                  {wsStatus === 'open' ? 'Active' : wsStatus === 'connecting' ? 'Connecting' : 'Offline'}
                </div>
              </div>
            </div>
          </div>

          {/* Right Panel */}
          <div className="flex flex-col gap-4">

            {/* AI Inference Card */}
            <div className="bg-[#151D2A] border border-[#2A3447] rounded-xl p-4 flex flex-col gap-3 shadow-lg">
              <div className="flex items-center justify-between pb-2 border-b border-[#2A3447]">
                <span className="text-xs font-semibold uppercase tracking-wider text-[#9CA3AF]">
                  AI Inference Engine
                </span>
                <Cpu className="w-4 h-4 text-[#10B981]" />
              </div>

              {/* Confidence Readout inside card */}
              <div className={`text-center py-1.5 rounded-lg border text-xs font-mono font-semibold ${
                isHighPrecision
                  ? 'bg-[#F59E0B]/10 border-[#F59E0B]/30 text-[#F59E0B]'
                  : 'bg-[#10B981]/10 border-[#10B981]/30 text-[#10B981]'
              }`}>
                {isHighPrecision ? '⚡ HIGH PRECISION MODE' : `Threshold: ${thresholdPct}%`}
              </div>

              <button
                onClick={handleRequestSignal}
                disabled={isGeneratingSignal}
                className="w-full flex items-center justify-center gap-2 py-2.5 px-4 bg-gradient-to-r from-[#10B981] to-[#059669] hover:from-[#059669] hover:to-[#047857] text-[#0B0E14] font-bold text-xs rounded-lg transition-all shadow-md shadow-[#10B981]/10 disabled:opacity-50"
              >
                <Zap className={`w-4 h-4 ${isGeneratingSignal ? 'animate-bounce' : ''}`} />
                <span>{isGeneratingSignal ? 'Analyzing...' : 'Run Signal Inference'}</span>
              </button>

              {/* Primary Quotex Forex Training Button (60k Bars) */}
              <button
                onClick={handleTrainPrimaryFx}
                disabled={isTrainingFx || isTrainingCrypto}
                className="w-full flex items-center justify-center gap-2 py-2 px-3 bg-[#0B0E14] hover:bg-[#121E19] border border-[#10B981]/50 hover:border-[#10B981] text-[#10B981] font-semibold text-xs rounded-lg transition-all shadow-sm shadow-[#10B981]/5 disabled:opacity-50"
              >
                <Cpu className={`w-3.5 h-3.5 ${isTrainingFx ? 'animate-spin text-[#10B981]' : ''}`} />
                <span>
                  {isTrainingFx
                    ? 'Training Primary FX (60,000 Bars)...'
                    : 'Train Primary FX (60,000 Bars)'}
                </span>
              </button>

              {/* Secondary Crypto Training Button (40k Bars) */}
              <button
                onClick={handleTrainSecondaryCrypto}
                disabled={isTrainingFx || isTrainingCrypto}
                className="w-full flex items-center justify-center gap-2 py-2 px-3 bg-[#0B0E14] hover:bg-[#1A2332] border border-[#2A3447] hover:border-[#3B82F6]/50 text-[#9CA3AF] hover:text-[#93C5FD] font-medium text-xs rounded-lg transition-all disabled:opacity-50"
              >
                <Cpu className={`w-3.5 h-3.5 ${isTrainingCrypto ? 'animate-spin text-[#3B82F6]' : ''}`} />
                <span>
                  {isTrainingCrypto
                    ? 'Training Crypto (40,000 Bars)...'
                    : 'Train Secondary Crypto (40,000 Bars)'}
                </span>
              </button>
            </div>

            {/* Active Signal Card */}
            <div className="bg-[#151D2A] border border-[#2A3447] rounded-xl p-4 flex flex-col gap-3 shadow-lg">
              <div className="flex items-center justify-between pb-2 border-b border-[#2A3447]">
                <span className="text-xs font-semibold uppercase tracking-wider text-[#9CA3AF]">
                  Latest Signal
                </span>
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#3B82F6] opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-[#3B82F6]"></span>
                </span>
              </div>

              {activeSignal ? (
                <div className="flex flex-col gap-3 font-mono text-xs">
                  {/* Direction Badge */}
                  <div
                    className={`p-3 rounded-lg border flex items-center justify-between ${
                      activeSignal.direction === 'UP'
                        ? 'bg-[#10B981]/10 border-[#10B981]/40 text-[#10B981]'
                        : activeSignal.direction === 'DOWN'
                        ? 'bg-[#EF4444]/10 border-[#EF4444]/40 text-[#EF4444]'
                        : 'bg-[#2A3447]/40 border-[#2A3447] text-[#9CA3AF]'
                    }`}
                  >
                    <div className="flex items-center gap-2 font-bold text-sm">
                      {activeSignal.direction === 'UP'   && <TrendingUp   className="w-4 h-4" />}
                      {activeSignal.direction === 'DOWN' && <TrendingDown className="w-4 h-4" />}
                      <span>{activeSignal.direction}</span>
                    </div>
                    <span className="text-xs px-2 py-0.5 rounded bg-[#0B0E14] font-semibold">
                      {(activeSignal.confidence * 100).toFixed(1)}%
                    </span>
                  </div>

                  {/* Signal Meta */}
                  <div className="space-y-2 text-[#9CA3AF] bg-[#0B0E14] p-3 rounded-lg border border-[#2A3447]">
                    <div className="flex justify-between">
                      <span>Asset:</span>
                      <span className="text-white">{activeSignal.pair?.replace('=X',' (FX)')}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Entry:</span>
                      <span className="text-white">{activeSignal.entry_price?.toFixed(dec)}</span>
                    </div>
                    {activeSignal.exit_price != null && (
                      <div className="flex justify-between">
                        <span>Exit:</span>
                        <span className="text-white">{activeSignal.exit_price?.toFixed(dec)}</span>
                      </div>
                    )}
                    {activeSignal.confluence_score != null && (
                      <div className="flex justify-between">
                        <span>Confluence:</span>
                        <span className="text-[#10B981] font-bold">{(activeSignal.confluence_score * 100).toFixed(0)}%</span>
                      </div>
                    )}
                    {activeSignal.gate_reason && (
                      <div className="text-[10px] text-[#F59E0B] bg-[#F59E0B]/10 p-1.5 rounded border border-[#F59E0B]/20">
                        {activeSignal.gate_reason}
                      </div>
                    )}
                    <div className="flex justify-between items-center">
                      <span>Outcome:</span>
                      <span className={`font-bold px-1.5 py-0.5 rounded text-[11px] ${
                        activeSignal.status === 'WIN'  ? 'bg-[#10B981]/20 text-[#10B981]' :
                        activeSignal.status === 'LOSS' ? 'bg-[#EF4444]/20 text-[#EF4444]' :
                        'bg-[#2A3447] text-[#9CA3AF]'
                      }`}>
                        {activeSignal.status}
                      </span>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="flex-1 flex flex-col items-center justify-center text-center p-6 text-[#9CA3AF]">
                  <Zap className="w-8 h-8 text-[#2A3447] mb-2" />
                  <p className="text-xs">No active signals yet.</p>
                  <p className="text-[11px] text-[#6B7280] mt-1">
                    Run inference or wait for the live stream to push a signal.
                  </p>
                </div>
              )}
            </div>

            {/* Live Opportunity Scanner (All 6 Pairs) */}
            <div className="bg-[#151D2A] border border-[#2A3447] rounded-xl p-4 flex flex-col gap-3 shadow-lg">
              <div className="flex items-center justify-between pb-2 border-b border-[#2A3447]">
                <div className="flex items-center gap-2">
                  <Radar className="w-4 h-4 text-[#10B981]" />
                  <span className="text-xs font-semibold uppercase tracking-wider text-[#9CA3AF]">
                    Opportunity Scanner
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {opportunities.some(o => o.signal === 'UP' || o.signal === 'DOWN') ? (
                    <span className="px-1.5 py-0.5 rounded-full bg-[#10B981]/20 text-[#10B981] border border-[#10B981]/40 text-[10px] font-bold">
                      {opportunities.filter(o => o.signal === 'UP' || o.signal === 'DOWN').length} ACTIVE
                    </span>
                  ) : (
                    <span className="px-1.5 py-0.5 rounded bg-[#2A3447] text-[#9CA3AF] text-[10px] font-semibold">
                      WAITING
                    </span>
                  )}
                  <button
                    onClick={() => handleScanAll()}
                    disabled={isScanning}
                    className="p-1 rounded hover:bg-[#0B0E14] text-[#9CA3AF] hover:text-white transition-colors"
                    title="Scan all 6 pairs now"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${isScanning ? 'animate-spin text-[#10B981]' : ''}`} />
                  </button>
                </div>
              </div>

              {/* Scanned pairs list */}
              <div className="space-y-1.5 max-h-[220px] overflow-y-auto pr-1">
                {opportunities.length > 0 ? (
                  opportunities.map((opp) => {
                    const isSelected = isSamePair(opp.pair, selectedPair);
                    const isActionable = opp.signal === 'UP' || opp.signal === 'DOWN';
                    return (
                      <div
                        key={opp.pair}
                        onClick={() => handleOpportunityClick(opp)}
                        className={`p-2 rounded-lg border cursor-pointer transition-all ${
                          isSelected
                            ? 'bg-[#1E293B] border-[#10B981]/60 shadow-sm'
                            : isActionable
                            ? 'bg-[#0B0E14] border-[#10B981]/30 hover:border-[#10B981] hover:bg-[#111827]'
                            : 'bg-[#0B0E14] border-[#2A3447] hover:border-[#3B4861] hover:bg-[#111827]'
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <span className={`text-xs font-bold font-mono ${isSelected ? 'text-[#10B981]' : 'text-white'}`}>
                              {opp.label}
                            </span>
                            {isSelected && (
                              <span className="text-[9px] px-1.5 py-0.2 rounded bg-[#10B981]/20 text-[#10B981] font-semibold">
                                VIEWING
                              </span>
                            )}
                          </div>

                          {/* Signal Badge */}
                          <div className="flex items-center gap-1.5 font-mono text-xs">
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-bold flex items-center gap-1 ${
                                opp.signal === 'UP'
                                    ? 'bg-[#10B981]/20 text-[#10B981] border border-[#10B981]/40'
                                  : opp.signal === 'DOWN'
                                  ? 'bg-[#EF4444]/20 text-[#EF4444] border border-[#EF4444]/40'
                                  : 'bg-[#2A3447]/60 text-[#9CA3AF]'
                              }`}
                            >
                              {opp.signal === 'UP' ? '▲ UP' : opp.signal === 'DOWN' ? '▼ DOWN' : 'CHOP'}
                              <span className="font-semibold">{(opp.confidence * 100).toFixed(0)}%</span>
                            </span>
                          </div>
                        </div>

                        {/* Sub-line: ADX & Status Reason */}
                        <div className="flex items-center justify-between mt-1 text-[10px] font-mono text-[#9CA3AF]">
                          <span className="truncate max-w-[170px]" title={opp.status_reason}>
                            {opp.status_reason}
                          </span>
                          <span className={`font-semibold shrink-0 ml-1 ${opp.adx > 20 ? 'text-[#10B981]' : opp.adx > 18 ? 'text-[#F59E0B]' : 'text-[#6B7280]'}`}>
                            ADX {opp.adx ? opp.adx.toFixed(1) : '—'}
                          </span>
                        </div>
                      </div>
                    );
                  })
                ) : (
                  <div className="py-4 text-center text-[#6B7280] text-xs">
                    {isScanning ? 'Scanning all 6 pairs...' : 'Click below to scan all 6 pairs'}
                  </div>
                )}
              </div>

              <button
                onClick={() => handleScanAll()}
                disabled={isScanning}
                className="w-full flex items-center justify-center gap-2 py-1.5 px-3 bg-[#0B0E14] hover:bg-[#1E293B] border border-[#2A3447] hover:border-[#10B981]/40 text-xs font-semibold text-[#10B981] rounded-lg transition-all disabled:opacity-50"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${isScanning ? 'animate-spin' : ''}`} />
                <span>{isScanning ? 'Scanning All 6 Pairs...' : 'Scan All 6 Pairs (Instant)'}</span>
              </button>
            </div>

            {/* Filtered Signal History for Selected Asset */}
            {(() => {
              const pairHistory = signalHistory.filter(s => isSamePair(s.pair, selectedPair));
              return (
                <div className="bg-[#151D2A] border border-[#2A3447] rounded-xl p-4 shadow-lg">
                  <div className="flex items-center justify-between pb-2 border-b border-[#2A3447] mb-2">
                    <span className="text-xs font-semibold uppercase tracking-wider text-[#9CA3AF]">
                      Signal History
                    </span>
                    <span className="text-[10px] font-mono font-semibold text-[#10B981] bg-[#10B981]/10 px-2 py-0.5 rounded border border-[#10B981]/25">
                      {selectedPair.replace('=X', ' (FX)')}
                    </span>
                  </div>
                  {pairHistory.length > 0 ? (
                    <div className="space-y-1.5 max-h-[180px] overflow-y-auto pr-1">
                      {pairHistory.slice(0, 10).map((s) => (
                        <div key={s.id} className="flex items-center justify-between text-[11px] font-mono py-1 border-b border-[#2A3447]/50 last:border-0">
                          <div className="flex items-center gap-1.5">
                            <span className={
                              s.direction === 'UP' ? 'text-[#10B981]' :
                              s.direction === 'DOWN' ? 'text-[#EF4444]' :
                              'text-[#9CA3AF]'
                            }>
                              {s.direction === 'UP' ? '▲' : s.direction === 'DOWN' ? '▼' : '·'} {s.direction}
                            </span>
                            <span className="text-white font-semibold">{(s.confidence * 100).toFixed(0)}%</span>
                          </div>
                          <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                            s.status === 'WIN'  ? 'bg-[#10B981]/15 text-[#10B981]' :
                            s.status === 'LOSS' ? 'bg-[#EF4444]/15 text-[#EF4444]' :
                            'bg-[#2A3447] text-[#9CA3AF]'
                          }`}>
                            {s.status}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="py-3 text-center text-[#6B7280] text-xs">
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
