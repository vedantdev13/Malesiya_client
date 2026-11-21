import json
import logging
from typing import Any, Dict, List
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["WebSockets"])


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(
            f"WebSocket client connected. Total active connections: {len(self.active_connections)}"
        )

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(
                f"WebSocket client disconnected. Total active connections: {len(self.active_connections)}"
            )

    async def send_personal_message(
        self, message: Dict[str, Any], websocket: WebSocket
    ) -> None:
        await websocket.send_text(json.dumps(message))

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """
        Broadcast JSON payload to all active clients.
        Cleanly removes any closed or disconnected connections.
        """
        if not self.active_connections:
            return

        payload = json.dumps(message)
        dead_connections: List[WebSocket] = []

        for connection in list(self.active_connections):
            try:
                await connection.send_text(payload)
            except Exception as e:
                logger.warning(f"Error broadcasting to WebSocket client: {e}")
                dead_connections.append(connection)

        for dead_conn in dead_connections:
            self.disconnect(dead_conn)


# Signal stream manager (for paper-trader + resolver signal events)
ws_manager = ConnectionManager()

# Market tick stream manager (for /ws/market — real-time price ticks, all 6 pairs)
market_ws_manager = ConnectionManager()


@router.websocket("/ws/live-signals")
async def websocket_live_signals_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        await ws_manager.send_personal_message(
            {
                "event": "CONNECTED",
                "message": "Connected to AI Binary Trading live signals stream",
            },
            websocket,
        )
        while True:
            data = await websocket.receive_text()
            try:
                parsed = json.loads(data)
                if parsed.get("type") == "ping":
                    await ws_manager.send_personal_message({"type": "pong"}, websocket)
            except Exception:
                if data.strip().lower() == "ping":
                    await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket (live-signals) error: {e}")
        ws_manager.disconnect(websocket)


@router.websocket("/ws/market")
async def websocket_market_endpoint(websocket: WebSocket):
    """
    Real-Time Market Tick WebSocket (/ws/market).
    Pushes live OHLC candle updates for all 6 pairs every 1 second.
    Message shape:
      { event: "MARKET_TICK", timestamp: "...", ticks: { PAIR: { price, candles: { 1m, 5m, 15m } } } }
    """
    await market_ws_manager.connect(websocket)
    try:
        await market_ws_manager.send_personal_message(
            {
                "event": "CONNECTED",
                "message": "Connected to Quantum AI real-time market tick stream (all 6 pairs, 1s)",
            },
            websocket,
        )
        while True:
            data = await websocket.receive_text()
            try:
                parsed = json.loads(data)
                if parsed.get("type") == "ping":
                    await market_ws_manager.send_personal_message({"type": "pong"}, websocket)
            except Exception:
                if data.strip().lower() == "ping":
                    await websocket.send_text("pong")
    except WebSocketDisconnect:
        market_ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket (market) error: {e}")
        market_ws_manager.disconnect(websocket)

