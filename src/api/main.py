"""FastAPI application for Honest Congress."""
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from src.db import init_db, SessionLocal
from src.api.routes import members, disclosures, anomalies, health, assets
from src.api.routes import dashboard_v2, performance
from src.config import get_settings
from src.analysis.trade_analyzer import TradeAnalyzer
from src.ingestion.orchestrator import IngestionOrchestrator

settings = get_settings()

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Get the data directory path
DATA_DIR = Path(__file__).parent.parent.parent / "data"
DISCLOSURES_DIR = DATA_DIR / "disclosures"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info("Starting Honest Congress API...")
    init_db()
    logger.info("Database initialized")

    db = SessionLocal()
    try:
        # Lightweight sync: ensure large-trade anomalies match transactions
        TradeAnalyzer()._sync_large_trade_anomalies(db)
        logger.info("Large-trade anomalies synced")

        # Fix any future-dated disclosures (data quality)
        fixed_dates = IngestionOrchestrator().fix_future_dates(db)
        if fixed_dates:
            logger.info("Fixed %s disclosures with future dates", fixed_dates)
    except Exception:
        logger.exception("Failed during startup maintenance")
        db.rollback()
    finally:
        db.close()

    yield
    # Shutdown
    logger.info("Shutting down Honest Congress API...")


app = FastAPI(
    title="Honest Congress API",
    description=(
        "API for analyzing congressional financial disclosures "
        "and detecting potential anomalies in reported wealth and investments."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files for local documents
if DISCLOSURES_DIR.exists():
    app.mount("/documents", StaticFiles(directory=str(DISCLOSURES_DIR)), name="documents")


@app.get("/api/documents/check/{doc_id}")
async def check_document_exists(doc_id: str):
    """Check if a document exists locally."""
    for year_dir in (DISCLOSURES_DIR / "fd").glob("*"):
        if year_dir.is_dir():
            pdf_path = year_dir / f"{doc_id}.pdf"
            if pdf_path.exists():
                return {"exists": True, "path": f"/api/documents/{year_dir.name}/{doc_id}", "type": "fd"}

    for year_dir in (DISCLOSURES_DIR / "ptr").glob("*"):
        if year_dir.is_dir():
            pdf_path = year_dir / f"{doc_id}.pdf"
            if pdf_path.exists():
                return {"exists": True, "path": f"/api/documents/{year_dir.name}/{doc_id}", "type": "ptr"}

    return {"exists": False, "document_id": doc_id}


@app.get("/api/documents/{year}/{doc_id}")
async def get_document(year: int, doc_id: str):
    """Serve a local document PDF if available."""
    # Check for FD documents
    fd_path = DISCLOSURES_DIR / "fd" / str(year) / f"{doc_id}.pdf"
    if fd_path.exists():
        return FileResponse(fd_path, media_type="application/pdf", filename=f"{doc_id}.pdf")

    # Check for PTR documents
    ptr_path = DISCLOSURES_DIR / "ptr" / str(year) / f"{doc_id}.pdf"
    if ptr_path.exists():
        return FileResponse(ptr_path, media_type="application/pdf", filename=f"{doc_id}.pdf")

    # Check if this looks like API data (non-numeric doc_id)
    if not doc_id.isdigit():
        return JSONResponse(
            status_code=404,
            content={
                "error": "This disclosure is from API data and does not have a downloadable PDF",
                "document_id": doc_id,
                "hint": "Check the 'Original Source' link for the official filing"
            }
        )

    return JSONResponse(
        status_code=404,
        content={
            "error": "Document not found locally",
            "document_id": doc_id,
            "hint": "The PDF may not have been downloaded yet"
        }
    )

# Include routers
app.include_router(dashboard_v2.router, tags=["Dashboard"])  # Enhanced dashboard
app.include_router(health.router, tags=["Health"])
app.include_router(members.router, prefix="/api/members", tags=["Members"])
app.include_router(disclosures.router, prefix="/api/disclosures", tags=["Disclosures"])
app.include_router(anomalies.router, prefix="/api/anomalies", tags=["Anomalies"])
app.include_router(assets.router, prefix="/api", tags=["Assets"])
app.include_router(performance.router, prefix="/api", tags=["Performance"])

