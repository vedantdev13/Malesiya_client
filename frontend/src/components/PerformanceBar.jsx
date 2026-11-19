import React, { useState, useEffect, useCallback } from 'react';
import { fetchPerformance } from '../services/api';
import { TrendingUp, TrendingDown, Target, Activity, Radio } from 'lucide-react';

export default function PerformanceBar({ pair, timeframe, refreshTrigger }) {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadStats = useCallback(async () => {
    try {
      const data = await fetchPerformance(pair, timeframe);
      setStats(data);
    } catch (err) {
      console.error('Failed to load performance stats:', err);
    } finally {
      setLoading(false);
    }
  }, [pair, timeframe]);

  // Reload on external trigger (new signal resolved via WebSocket)
  useEffect(() => {
    loadStats();
  }, [loadStats, refreshTrigger]);

  // Auto-refresh every 15 seconds
  useEffect(() => {
    const interval = setInterval(loadStats, 15000);
    return () => clearInterval(interval);
  }, [loadStats]);

  if (loading && !stats) {
    return (
      <div className="bg-[#151D2A] border border-[#2A3447] rounded-xl p-3 flex items-center justify-center">
        <span className="text-xs text-[#9CA3AF] font-mono animate-pulse">Loading performance metrics...</span>
      </div>
    );
  }

  if (!stats) return null;

  const winRateColor =
    stats.win_rate >= 65 ? '#10B981' :
    stats.win_rate >= 50 ? '#F59E0B' :
    '#EF4444';

  return (
    <div className="bg-[#151D2A] border border-[#2A3447] rounded-xl px-4 py-3 shadow-lg">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        {/* Performance Label */}
        <div className="flex items-center gap-2 mr-2">
          <Activity className="w-3.5 h-3.5 text-[#10B981]" />
          <span className="text-[11px] font-semibold uppercase tracking-wider text-[#9CA3AF]">
            Live Performance
          </span>
          {(pair || timeframe) && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#2A3447] text-[#9CA3AF] font-mono">
              {pair || 'ALL'} · {timeframe || 'ALL'}
            </span>
          )}
        </div>

        {/* Win Rate — primary hero metric */}
        <div className="flex items-center gap-2">
          <div
            className="text-xl font-bold font-mono"
            style={{ color: winRateColor }}
          >
            {stats.win_rate.toFixed(1)}%
          </div>
          <div className="text-[11px] text-[#9CA3AF]">Win Rate</div>
        </div>

        <div className="h-4 w-[1px] bg-[#2A3447] hidden sm:block" />

        {/* Wins */}
        <div className="flex items-center gap-1.5 text-xs font-mono">
          <TrendingUp className="w-3.5 h-3.5 text-[#10B981]" />
          <span className="text-[#10B981] font-bold">{stats.wins}</span>
          <span className="text-[#9CA3AF]">Wins</span>
        </div>

        {/* Losses */}
        <div className="flex items-center gap-1.5 text-xs font-mono">
          <TrendingDown className="w-3.5 h-3.5 text-[#EF4444]" />
          <span className="text-[#EF4444] font-bold">{stats.losses}</span>
          <span className="text-[#9CA3AF]">Losses</span>
        </div>

        {/* Draws / Pending */}
        <div className="flex items-center gap-1.5 text-xs font-mono">
          <span className="text-[#9CA3AF]">
            {stats.draws}D / {stats.pending}P
          </span>
        </div>

        <div className="h-4 w-[1px] bg-[#2A3447] hidden sm:block" />

        {/* Total Signals */}
        <div className="flex items-center gap-1.5 text-xs font-mono">
          <Radio className="w-3.5 h-3.5 text-[#3B82F6]" />
          <span className="text-white font-bold">{stats.total_signals_generated}</span>
          <span className="text-[#9CA3AF]">Signals</span>
        </div>

        {/* Tradeable / Coverage Rate */}
        <div className="flex items-center gap-1.5 text-xs font-mono">
          <Target className="w-3.5 h-3.5 text-[#F59E0B]" />
          <span className="text-[#F59E0B] font-bold">{stats.coverage_rate.toFixed(0)}%</span>
          <span className="text-[#9CA3AF]">
            Coverage ({stats.total_tradeable_signals} tradeable)
          </span>
        </div>

        {/* Win Rate Progress Bar */}
        <div className="hidden lg:flex flex-1 items-center gap-2 max-w-[160px]">
          <div className="flex-1 h-1.5 bg-[#2A3447] rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-700"
              style={{
                width: `${Math.min(stats.win_rate, 100)}%`,
                backgroundColor: winRateColor,
              }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

