import React from 'react';
import { Activity, ChevronDown, Wifi, WifiOff, SlidersHorizontal, Zap } from 'lucide-react';

export default function Header({
  selectedPair,
  onSelectPair,
  selectedTimeframe,
  onSelectTimeframe,
  isBackendConnected,
  lastPrice,
  priceChange,
  confidenceThreshold,
  onConfidenceChange,
}) {
  // Only the 3 Core Quotex Forex pairs
  const pairs = [
    { label: 'EUR / USD (Quotex FX)', value: 'EURUSD=X', group: 'Quotex Forex' },
    { label: 'GBP / USD (Quotex FX)', value: 'GBPUSD=X', group: 'Quotex Forex' },
    { label: 'USD / JPY (Quotex FX)', value: 'USDJPY=X', group: 'Quotex Forex' },
  ];

  const timeframes = ['1m', '5m', '15m'];

  const thresholdPct = Math.round(confidenceThreshold * 100);
  const isHighPrecision = thresholdPct >= 80;
  const isBalanced = thresholdPct < 80;

  return (
    <header className="bg-[#151D2A] border-b border-[#2A3447] px-4 py-3 sm:px-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        {/* Brand & Live Indicator */}
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[#10B981] to-[#047857] flex items-center justify-center shadow-lg shadow-[#10B981]/20">
              <Activity className="w-5 h-5 text-white" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-bold text-lg tracking-wider text-[#F3F4F6]">
                  QUANTUM <span className="text-[#10B981]">AI</span>
                </span>
                <span className="text-[10px] font-semibold uppercase tracking-widest px-1.5 py-0.5 rounded bg-[#10B981]/20 text-[#10B981] border border-[#10B981]/30">
                  QUOTEX FX
                </span>
              </div>
            </div>
          </div>

          <div className="h-4 w-[1px] bg-[#2A3447] hidden sm:block" />

          {/* Live Feed pulse */}
          <div className="flex items-center gap-2 px-2.5 py-1 rounded-full bg-[#0B0E14] border border-[#2A3447]">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#10B981] opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-[#10B981]"></span>
            </span>
            <span className="text-[11px] font-medium text-[#9CA3AF] tracking-wide">LIVE FX FEED</span>
          </div>

          {/* Live Price */}
          {lastPrice && (
            <div className="hidden md:flex items-center gap-2 font-mono">
              <span className="text-sm font-semibold text-white">
                {lastPrice.toLocaleString(undefined, {
                  minimumFractionDigits: selectedPair.includes('JPY') ? 3 : 5,
                })}
              </span>
              {priceChange !== undefined && (
                <span className={`text-xs ${priceChange >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}`}>
                  {priceChange >= 0 ? '+' : ''}{priceChange.toFixed(2)}%
                </span>
              )}
            </div>
          )}
        </div>

        {/* Right Controls */}
        <div className="flex flex-wrap items-center gap-3">
          {/* Pair Selector */}
          <div className="relative">
            <select
              value={selectedPair}
              onChange={(e) => onSelectPair(e.target.value)}
              className="appearance-none bg-[#0B0E14] border border-[#10B981]/40 text-[#F3F4F6] text-xs font-semibold rounded-lg pl-3 pr-8 py-1.5 cursor-pointer hover:border-[#10B981] focus:outline-none focus:border-[#10B981] transition-colors"
            >
              {pairs.map((p) => (
                <option key={p.value} value={p.value} className="bg-[#151D2A]">
                  {p.label}
                </option>
              ))}
            </select>
            <ChevronDown className="w-3.5 h-3.5 text-[#9CA3AF] absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
          </div>

          {/* Timeframe Pills */}
          <div className="flex items-center p-0.5 rounded-lg bg-[#0B0E14] border border-[#2A3447]">
            {timeframes.map((tf) => {
              const active = selectedTimeframe === tf;
              return (
                <button
                  key={tf}
                  onClick={() => onSelectTimeframe(tf)}
                  className={`px-3 py-1 text-xs font-medium rounded-md transition-all ${active
                    ? 'bg-[#10B981] text-[#0B0E14] font-semibold shadow-sm'
                    : 'text-[#9CA3AF] hover:text-[#F3F4F6] hover:bg-[#151D2A]'
                    }`}
                >
                  {tf}
                </button>
              );
            })}
          </div>

          {/* Confidence Slider Control */}
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-[#0B0E14] border border-[#2A3447]">
            <SlidersHorizontal className="w-3.5 h-3.5 text-[#9CA3AF] shrink-0" />
            <div className="flex flex-col gap-0.5">
              <div className="flex items-center gap-2">
                <span className="text-[11px] text-[#9CA3AF]">Confidence:</span>
                <span
                  className={`text-[11px] font-bold font-mono ${
                    isHighPrecision ? 'text-[#10B981]' : 'text-[#F59E0B]'
                  }`}
                >
                  {thresholdPct}%
                </span>
                {isHighPrecision && (
                  <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-[#10B981]/20 text-[#10B981] border border-[#10B981]/30 font-semibold uppercase tracking-wide">
                    85% High Precision
                  </span>
                )}
                {isBalanced && (
                  <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-[#F59E0B]/20 text-[#F59E0B] border border-[#F59E0B]/30 font-semibold uppercase tracking-wide">
                    Custom ({thresholdPct}%)
                  </span>
                )}
              </div>
              <input
                type="range"
                min="50"
                max="95"
                step="1"
                value={thresholdPct}
                onChange={(e) => onConfidenceChange(Number(e.target.value) / 100)}
                className="w-28 h-1 accent-[#10B981] cursor-pointer"
                title={`Filter threshold: ${thresholdPct}%`}
              />
            </div>
          </div>

          {/* Backend Status Badge */}
          <div
            className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium border ${isBackendConnected
              ? 'bg-[#10B981]/10 text-[#10B981] border-[#10B981]/30'
              : 'bg-[#EF4444]/10 text-[#EF4444] border-[#EF4444]/30'
              }`}
          >
            {isBackendConnected ? (
              <><Wifi className="w-3.5 h-3.5" /><span>Quotex Engine Active</span></>
            ) : (
              <><WifiOff className="w-3.5 h-3.5" /><span>Connecting...</span></>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}
