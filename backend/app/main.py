# run ---    python -m uvicorn app.main:app --reload --port 8000     === in backend terminal to start backend 
''' this is main file  of the project for running the server

"Tune Model (1,000 Real Bars)" dabane par kya hota hai?

Ye Model Training / Study Button hai (har baar nahi dabana hota, din mein 1–2 baar ya naya coin select karne par dabate hain).

Isko dabane par backend Binance / Yahoo Finance se pichhli 1,000 live candles uthata hai, aur tumhare AI (XGBoost + RandomForest) ko sikhata hai ki is market mein pichhle kuch ghanton/dinon mein kaunsa pattern kaam kar raha hai.

"Run Signal Inference" dabane par kya hota hai?

Ye Live Prediction Button hai (jab bhi client trade lena chahe, isko dabata hai).

Ye abhi ki latest live candle ko check karta hai, aur 1 second ke andar "LATEST SIGNAL" box mein result nikal kar deta hai.
''' 

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes.analytics import router as analytics_router
from app.api.routes.data import router as data_router
from app.api.routes.health import router as health_router
from app.api.routes.predict import router as predict_router
from app.api.websockets import router as ws_router
from app.core.config import settings
from app.core.database import init_db
from app.services.paper_trader import start_paper_trading_loop
from app.services.resolver import start_resolver_loop
from app.services.live_streamer import live_streamer


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables on startup
    await init_db()

    # Start the background signal outcome resolver loop
    resolver_task = asyncio.create_task(start_resolver_loop())

    # Start the autonomous paper trading loop
    paper_trader_task = asyncio.create_task(start_paper_trading_loop())

    # Start the Quotex-Grade Real-Time Live Streamer (1s tick, all 6 pairs)
    live_streamer_task = asyncio.create_task(live_streamer.start())

    yield

    # Clean shutdown of background tasks
    live_streamer.is_running = False
    resolver_task.cancel()
    paper_trader_task.cancel()
    live_streamer_task.cancel()
    try:
        await asyncio.gather(resolver_task, paper_trader_task, live_streamer_task, return_exceptions=True)
    except asyncio.CancelledError:
        pass


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers
app.include_router(health_router, prefix=settings.API_V1_STR)
app.include_router(data_router, prefix=settings.API_V1_STR)
app.include_router(predict_router, prefix=settings.API_V1_STR)
app.include_router(analytics_router, prefix=settings.API_V1_STR)

# Register WebSocket router
app.include_router(ws_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
