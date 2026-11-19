/**
 * useWebSocket.js
 *
 * Custom React hook for a resilient WebSocket connection with:
 *  - Automatic exponential-backoff reconnection (max 30s between attempts)
 *  - Heartbeat ping/pong to detect silent connection drops
 *  - Connection state tracking: 'connecting' | 'open' | 'closed' | 'error'
 *  - Clean teardown on component unmount (no memory leaks)
 *  - Callback refs so callers never need to wrap handlers in useCallback
 *
 * Usage:
 *   const { status, send, connectionAttempts } = useWebSocket(url, {
 *     onMessage: (parsedData) => { ... },
 *     onOpen:    ()           => { ... },
 *     onClose:   ()           => { ... },
 *     pingIntervalMs: 20000,
 *   });
 */

import { useCallback, useEffect, useRef, useState } from 'react';

const DEFAULT_PING_INTERVAL_MS  = 20_000;  // send ping every 20s
const DEFAULT_PONG_TIMEOUT_MS   = 8_000;   // if no pong within 8s, reconnect
const BASE_RECONNECT_DELAY_MS   = 1_000;   // first retry after 1s
const MAX_RECONNECT_DELAY_MS    = 30_000;  // cap at 30s

/**
 * @param {string} url  WebSocket URL (ws:// or wss://)
 * @param {object} opts
 * @param {(data: any) => void}  [opts.onMessage]       Called with parsed JSON (or raw string)
 * @param {() => void}           [opts.onOpen]          Called when connection opens
 * @param {() => void}           [opts.onClose]         Called when connection closes
 * @param {number}               [opts.pingIntervalMs]  Heartbeat interval (default 20s)
 * @param {boolean}              [opts.enabled]         Set false to disable connection (default true)
 */
export function useWebSocket(url, opts = {}) {
  const {
    onMessage,
    onOpen,
    onClose,
    pingIntervalMs = DEFAULT_PING_INTERVAL_MS,
    enabled = true,
  } = opts;

  const [status, setStatus]               = useState('closed');
  const [connectionAttempts, setAttempts] = useState(0);

  // Stable refs — updated on every render so callbacks always see fresh closures
  const onMessageRef  = useRef(onMessage);
  const onOpenRef     = useRef(onOpen);
  const onCloseRef    = useRef(onClose);
  useEffect(() => { onMessageRef.current  = onMessage;  }, [onMessage]);
  useEffect(() => { onOpenRef.current     = onOpen;     }, [onOpen]);
  useEffect(() => { onCloseRef.current    = onClose;    }, [onClose]);

  // Mutable refs that survive renders without causing re-renders
  const wsRef              = useRef(null);
  const reconnectTimerRef  = useRef(null);
  const pingTimerRef       = useRef(null);
  const pongTimerRef       = useRef(null);
  const attemptRef         = useRef(0);
  const unmountedRef       = useRef(false);

  // ------------------------------------------------------------------
  // Heartbeat helpers
  // ------------------------------------------------------------------

  const clearTimers = useCallback(() => {
    clearInterval(pingTimerRef.current);
    clearTimeout(pongTimerRef.current);
    clearTimeout(reconnectTimerRef.current);
  }, []);

  const startHeartbeat = useCallback((ws) => {
    clearInterval(pingTimerRef.current);

    pingTimerRef.current = setInterval(() => {
      if (ws.readyState !== WebSocket.OPEN) return;

      // Send ping
      try {
        ws.send(JSON.stringify({ type: 'ping' }));
      } catch {
        return;
      }

      // Expect a pong-like reply within pong timeout
      pongTimerRef.current = setTimeout(() => {
        console.warn('[WS] Pong timeout — forcing reconnect');
        ws.close(4000, 'pong_timeout');
      }, DEFAULT_PONG_TIMEOUT_MS);
    }, pingIntervalMs);
  }, [pingIntervalMs]);

  // ------------------------------------------------------------------
  // Core connect
  // ------------------------------------------------------------------

  const connect = useCallback(() => {
    if (unmountedRef.current || !enabled) return;
    if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) return; // already connecting/open

    setStatus('connecting');

    let ws;
    try {
      ws = new WebSocket(url);
    } catch (err) {
      console.error('[WS] Failed to construct WebSocket:', err);
      scheduleReconnect();
      return;
    }

    wsRef.current = ws;

    ws.onopen = () => {
      if (unmountedRef.current) { ws.close(); return; }
      attemptRef.current = 0;
      setAttempts(0);
      setStatus('open');
      startHeartbeat(ws);
      onOpenRef.current?.();
      console.log('[WS] Connected to', url);
    };

    ws.onmessage = (event) => {
      // Clear pong timer on ANY incoming message (server replied = alive)
      clearTimeout(pongTimerRef.current);

      let parsed;
      try {
        parsed = JSON.parse(event.data);
      } catch {
        parsed = event.data;
      }

      // Silently absorb pure pong frames
      if (parsed?.type === 'pong' || parsed?.event === 'PONG') return;

      onMessageRef.current?.(parsed);
    };

    ws.onclose = (event) => {
      clearTimers();
      if (unmountedRef.current) return;
      setStatus('closed');
      onCloseRef.current?.();
      // Don't reconnect on clean intentional close (code 1000 from our own teardown)
      if (event.code !== 1000 || event.reason === 'pong_timeout') {
        scheduleReconnect();
      }
    };

    ws.onerror = () => {
      setStatus('error');
      ws.close();
    };
  }, [url, enabled, startHeartbeat, clearTimers]); // eslint-disable-line react-hooks/exhaustive-deps

  // ------------------------------------------------------------------
  // Reconnect with exponential backoff
  // ------------------------------------------------------------------

  const scheduleReconnect = useCallback(() => {
    if (unmountedRef.current || !enabled) return;

    attemptRef.current += 1;
    setAttempts(attemptRef.current);

    const delay = Math.min(
      BASE_RECONNECT_DELAY_MS * 2 ** (attemptRef.current - 1),
      MAX_RECONNECT_DELAY_MS
    );

    console.log(`[WS] Reconnect attempt #${attemptRef.current} in ${delay / 1000}s`);
    reconnectTimerRef.current = setTimeout(connect, delay);
  }, [enabled, connect]);

  // ------------------------------------------------------------------
  // Lifecycle
  // ------------------------------------------------------------------

  useEffect(() => {
    unmountedRef.current = false;

    if (enabled && url) {
      connect();
    }

    return () => {
      unmountedRef.current = true;
      clearTimers();
      if (wsRef.current) {
        wsRef.current.close(1000, 'component_unmount');
        wsRef.current = null;
      }
    };
  }, [url, enabled]); // only re-run if url or enabled changes

  // ------------------------------------------------------------------
  // Public send helper
  // ------------------------------------------------------------------

  const send = useCallback((data) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.warn('[WS] send() called but socket is not open');
      return false;
    }
    ws.send(typeof data === 'string' ? data : JSON.stringify(data));
    return true;
  }, []);

  return { status, send, connectionAttempts };
}

