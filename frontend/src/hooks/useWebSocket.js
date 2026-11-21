/**
 * useWebSocket.js
 *
 * Custom React hook for a resilient WebSocket connection with:
 *  - Automatic exponential-backoff reconnection (max 30s between attempts)
 *  - Heartbeat ping/pong to detect silent connection drops
 *  - Connection state tracking: 'connecting' | 'open' | 'closed' | 'error'
 *  - Clean teardown on component unmount (no memory leaks)
 *  - Callback refs so callers never need to wrap handlers in useCallback
 *  - Dynamic URL resolution (works on any host / Vite proxy / LAN)
 */

import { useCallback, useEffect, useRef, useState } from 'react';

const DEFAULT_PING_INTERVAL_MS = 20_000;  // send ping every 20s
const DEFAULT_PONG_TIMEOUT_MS  = 8_000;   // if no pong within 8s, reconnect
const BASE_RECONNECT_DELAY_MS  = 1_000;   // first retry after 1s
const MAX_RECONNECT_DELAY_MS   = 30_000;  // cap at 30s

/**
 * Resolve a WebSocket URL dynamically so it works from any device:
 *  - In dev (Vite): pass a path like '/ws/live-signals' and Vite proxy routes it
 *  - Explicit full URL (ws:// or wss://): used as-is
 */
function resolveWsUrl(urlOrPath) {
  if (!urlOrPath) return null;
  if (urlOrPath.startsWith('ws://') || urlOrPath.startsWith('wss://')) {
    return urlOrPath;
  }
  // Relative path — build from current browser origin
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}${urlOrPath}`;
}

/**
 * @param {string} url  WebSocket URL or path (e.g. '/ws/market' or 'ws://host:8000/ws/live-signals')
 * @param {object} opts
 * @param {(data: any) => void}  [opts.onMessage]       Called with parsed JSON (or raw string)
 * @param {() => void}           [opts.onOpen]          Called when connection opens
 * @param {() => void}           [opts.onClose]         Called when connection closes
 * @param {number}               [opts.pingIntervalMs]  Heartbeat interval (default 20s)
 * @param {boolean}              [opts.enabled]         Set false to disable (default true)
 */
export function useWebSocket(url, opts = {}) {
  const {
    onMessage,
    onOpen,
    onClose,
    pingIntervalMs = DEFAULT_PING_INTERVAL_MS,
    enabled = true,
  } = opts;

  const resolvedUrl = resolveWsUrl(url);

  const [status, setStatus]               = useState('closed');
  const [connectionAttempts, setAttempts] = useState(0);

  const onMessageRef = useRef(onMessage);
  const onOpenRef    = useRef(onOpen);
  const onCloseRef   = useRef(onClose);
  useEffect(() => { onMessageRef.current = onMessage; }, [onMessage]);
  useEffect(() => { onOpenRef.current    = onOpen;    }, [onOpen]);
  useEffect(() => { onCloseRef.current   = onClose;   }, [onClose]);

  const wsRef             = useRef(null);
  const reconnectTimerRef = useRef(null);
  const pingTimerRef      = useRef(null);
  const pongTimerRef      = useRef(null);
  const attemptRef        = useRef(0);
  const unmountedRef      = useRef(false);

  const clearTimers = useCallback(() => {
    clearInterval(pingTimerRef.current);
    clearTimeout(pongTimerRef.current);
    clearTimeout(reconnectTimerRef.current);
  }, []);

  const startHeartbeat = useCallback((ws) => {
    clearInterval(pingTimerRef.current);
    pingTimerRef.current = setInterval(() => {
      if (ws.readyState !== WebSocket.OPEN) return;
      try { ws.send(JSON.stringify({ type: 'ping' })); } catch { return; }
      pongTimerRef.current = setTimeout(() => {
        console.warn('[WS] Pong timeout — forcing reconnect');
        ws.close(4000, 'pong_timeout');
      }, DEFAULT_PONG_TIMEOUT_MS);
    }, pingIntervalMs);
  }, [pingIntervalMs]);

  const scheduleReconnect = useCallback(() => {
    if (unmountedRef.current || !enabled) return;
    attemptRef.current += 1;
    setAttempts(attemptRef.current);
    const delay = Math.min(
      BASE_RECONNECT_DELAY_MS * 2 ** (attemptRef.current - 1),
      MAX_RECONNECT_DELAY_MS
    );
    console.log(`[WS] Reconnect attempt #${attemptRef.current} in ${delay / 1000}s`);
    reconnectTimerRef.current = setTimeout(() => connect(), delay); // eslint-disable-line
  }, [enabled]); // eslint-disable-line react-hooks/exhaustive-deps

  const connect = useCallback(() => {
    if (unmountedRef.current || !enabled || !resolvedUrl) return;
    if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) return;

    setStatus('connecting');
    let ws;
    try {
      ws = new WebSocket(resolvedUrl);
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
      console.log('[WS] Connected to', resolvedUrl);
    };

    ws.onmessage = (event) => {
      clearTimeout(pongTimerRef.current);
      let parsed;
      try { parsed = JSON.parse(event.data); } catch { parsed = event.data; }
      if (parsed?.type === 'pong' || parsed?.event === 'PONG') return;
      onMessageRef.current?.(parsed);
    };

    ws.onclose = (event) => {
      clearTimers();
      if (unmountedRef.current) return;
      setStatus('closed');
      onCloseRef.current?.();
      if (event.code !== 1000 || event.reason === 'pong_timeout') {
        scheduleReconnect();
      }
    };

    ws.onerror = () => { setStatus('error'); ws.close(); };
  }, [resolvedUrl, enabled, startHeartbeat, clearTimers, scheduleReconnect]);

  useEffect(() => {
    unmountedRef.current = false;
    if (enabled && resolvedUrl) connect();
    return () => {
      unmountedRef.current = true;
      clearTimers();
      if (wsRef.current) {
        wsRef.current.close(1000, 'component_unmount');
        wsRef.current = null;
      }
    };
  }, [resolvedUrl, enabled]); // eslint-disable-line react-hooks/exhaustive-deps

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
