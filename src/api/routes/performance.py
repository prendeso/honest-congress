"""Performance comparison API endpoints."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.analysis.performance_analyzer import PerformanceAnalyzer
from src.db import get_db

router = APIRouter(tags=["performance"])


@router.get("/performance/summary")
async def get_performance_summary(db: Session = Depends(get_db)):
    """Get overall performance summary statistics."""
    analyzer = PerformanceAnalyzer()
    return analyzer.get_performance_summary(db)


@router.get("/performance/member/{member_id}")
async def get_member_performance(
    member_id: int,
    start_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    compare_benchmarks: bool = Query(True, description="Include benchmark comparison"),
    db: Session = Depends(get_db),
):
    """Get performance analysis for a specific member."""
    analyzer = PerformanceAnalyzer()

    start = datetime.fromisoformat(start_date) if start_date else None
    end = datetime.fromisoformat(end_date) if end_date else None

    if compare_benchmarks:
        return analyzer.compare_to_benchmarks(db, member_id, start, end)
    else:
        return analyzer.calculate_member_returns(db, member_id, start, end)


@router.get("/performance/rankings")
async def get_performance_rankings(
    start_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    min_trades: int = Query(5, description="Minimum trades to include"),
    limit: int = Query(20, description="Number of top/bottom performers"),
    db: Session = Depends(get_db),
):
    """Get ranked list of members by trading performance."""
    analyzer = PerformanceAnalyzer()

    start = datetime.fromisoformat(start_date) if start_date else None
    end = datetime.fromisoformat(end_date) if end_date else None

    return analyzer.rank_members_by_performance(db, start, end, min_trades, limit)


@router.get("/performance/benchmarks")
async def get_benchmark_returns(
    start_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
):
    """Get benchmark returns for a period."""
    analyzer = PerformanceAnalyzer()

    start = datetime.fromisoformat(start_date) if start_date else datetime(2023, 1, 1)
    end = datetime.fromisoformat(end_date) if end_date else datetime.now()

    from src.analysis.performance_analyzer import BENCHMARKS

    results = {}
    for name, ticker in BENCHMARKS.items():
        returns = analyzer.get_benchmark_returns(name, start, end)
        results[name] = {
            "ticker": ticker,
            "return_pct": returns,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
        }

    return results
