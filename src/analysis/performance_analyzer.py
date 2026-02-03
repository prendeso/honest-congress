"""Performance analyzer - Compare congressional trading vs market benchmarks."""
import logging
from typing import List, Dict, Any, Optional
from decimal import Decimal
from datetime import datetime, timedelta
from collections import defaultdict

import yfinance as yf
from sqlalchemy.orm import Session
from sqlalchemy import func

from src.db.models import Member, Disclosure, Transaction, TransactionType

logger = logging.getLogger(__name__)

# Benchmark tickers
BENCHMARKS = {
    "sp500": "SPY",      # S&P 500 ETF
    "nasdaq": "QQQ",     # Nasdaq 100 ETF
    "buffett": "BRK-B",  # Berkshire Hathaway
    "total_market": "VTI",  # Total US Market
}


class PerformanceAnalyzer:
    """
    Analyzes congressional stock trading performance vs market benchmarks.

    Compares:
    - Individual member returns vs S&P 500
    - Member returns vs Warren Buffett (Berkshire)
    - Identifies statistically significant outperformers
    """

    def __init__(self, cache_days: int = 1):
        self.cache_days = cache_days
        self._price_cache: Dict[str, Dict[str, float]] = {}
        self._benchmark_cache: Dict[str, Any] = {}

    def get_stock_price(self, ticker: str, date: datetime) -> Optional[float]:
        """Get stock price for a specific date."""
        cache_key = f"{ticker}_{date.strftime('%Y-%m-%d')}"

        if cache_key in self._price_cache:
            return self._price_cache[cache_key]

        try:
            # Fetch data for a range around the date
            start = date - timedelta(days=7)
            end = date + timedelta(days=7)

            stock = yf.Ticker(ticker)
            hist = stock.history(start=start, end=end)

            if hist.empty:
                return None

            # Find closest date
            target_date = date.strftime('%Y-%m-%d')
            if target_date in hist.index.strftime('%Y-%m-%d').tolist():
                price = float(hist.loc[target_date]['Close'])
            else:
                # Get closest available date
                hist['date_diff'] = abs((hist.index - date).days)
                closest = hist.loc[hist['date_diff'].idxmin()]
                price = float(closest['Close'])

            self._price_cache[cache_key] = price
            return price

        except Exception as e:
            logger.warning(f"Failed to get price for {ticker} on {date}: {e}")
            return None

    def get_benchmark_returns(
        self,
        benchmark: str,
        start_date: datetime,
        end_date: datetime
    ) -> Optional[float]:
        """Get benchmark returns for a period."""
        ticker = BENCHMARKS.get(benchmark, benchmark)

        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(start=start_date, end=end_date)

            if hist.empty or len(hist) < 2:
                return None

            start_price = float(hist.iloc[0]['Close'])
            end_price = float(hist.iloc[-1]['Close'])

            return ((end_price - start_price) / start_price) * 100

        except Exception as e:
            logger.warning(f"Failed to get benchmark returns for {benchmark}: {e}")
            return None

    def calculate_member_returns(
        self,
        db: Session,
        member_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Calculate trading returns for a member.

        Uses a simplified approach:
        - Track buys and sells
        - Calculate realized gains from matched trades
        - Estimate unrealized gains for open positions
        """
        if not start_date:
            start_date = datetime(2020, 1, 1)
        if not end_date:
            end_date = datetime.now()

        # Get member's transactions
        transactions = db.query(Transaction).join(Disclosure).filter(
            Disclosure.member_id == member_id,
            Transaction.transaction_date >= start_date,
            Transaction.transaction_date <= end_date,
            Transaction.ticker.isnot(None)
        ).order_by(Transaction.transaction_date).all()

        if not transactions:
            return {
                "member_id": member_id,
                "total_trades": 0,
                "estimated_return_pct": None,
                "trades_analyzed": 0,
            }

        # Track positions
        positions: Dict[str, List[Dict]] = defaultdict(list)
        realized_gains = Decimal(0)
        total_invested = Decimal(0)
        trades_with_prices = 0

        for txn in transactions:
            ticker = txn.ticker.upper()

            # Use midpoint of amount range
            if txn.amount_min and txn.amount_max:
                amount = (txn.amount_min + txn.amount_max) / 2
            elif txn.amount_min:
                amount = txn.amount_min
            else:
                continue

            price = self.get_stock_price(ticker, txn.transaction_date)
            if not price:
                continue

            trades_with_prices += 1

            if txn.transaction_type == TransactionType.PURCHASE:
                positions[ticker].append({
                    "date": txn.transaction_date,
                    "amount": amount,
                    "price": price,
                    "shares": float(amount) / price
                })
                total_invested += amount

            elif txn.transaction_type == TransactionType.SALE:
                # Match against oldest position (FIFO)
                if positions[ticker]:
                    buy = positions[ticker].pop(0)
                    buy_value = float(buy["amount"])
                    sell_value = float(amount)
                    gain = Decimal(str(sell_value - buy_value))
                    realized_gains += gain

        # Calculate unrealized gains on remaining positions
        unrealized_gains = Decimal(0)
        for ticker, pos_list in positions.items():
            current_price = self.get_stock_price(ticker, datetime.now())
            if current_price:
                for pos in pos_list:
                    current_value = pos["shares"] * current_price
                    unrealized_gains += Decimal(str(current_value)) - pos["amount"]

        total_gains = realized_gains + unrealized_gains

        if total_invested > 0:
            return_pct = float((total_gains / total_invested) * 100)
        else:
            return_pct = None

        return {
            "member_id": member_id,
            "total_trades": len(transactions),
            "trades_analyzed": trades_with_prices,
            "total_invested": float(total_invested),
            "realized_gains": float(realized_gains),
            "unrealized_gains": float(unrealized_gains),
            "total_gains": float(total_gains),
            "estimated_return_pct": return_pct,
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
        }

    def compare_to_benchmarks(
        self,
        db: Session,
        member_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Compare member performance to market benchmarks."""
        if not start_date:
            start_date = datetime(2023, 1, 1)
        if not end_date:
            end_date = datetime.now()

        member = db.query(Member).filter(Member.id == member_id).first()
        if not member:
            return {"error": "Member not found"}

        member_returns = self.calculate_member_returns(db, member_id, start_date, end_date)

        # Get benchmark returns
        benchmarks = {}
        for name, ticker in BENCHMARKS.items():
            returns = self.get_benchmark_returns(name, start_date, end_date)
            benchmarks[name] = {
                "ticker": ticker,
                "return_pct": returns
            }

        # Calculate alpha (excess return) vs S&P 500
        member_return = member_returns.get("estimated_return_pct")
        sp500_return = benchmarks.get("sp500", {}).get("return_pct")

        alpha = None
        if member_return is not None and sp500_return is not None:
            alpha = member_return - sp500_return

        return {
            "member": {
                "id": member_id,
                "name": f"{member.first_name} {member.last_name}",
                "party": member.party.value,
                "state": member.state,
            },
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            "member_performance": member_returns,
            "benchmarks": benchmarks,
            "alpha_vs_sp500": alpha,
            "beats_sp500": alpha > 0 if alpha is not None else None,
            "beats_buffett": (
                member_return > benchmarks.get("buffett", {}).get("return_pct", float('inf'))
                if member_return is not None else None
            ),
        }

    def rank_members_by_performance(
        self,
        db: Session,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        min_trades: int = 5,
        limit: int = 20
    ) -> Dict[str, Any]:
        """Rank all members by trading performance."""
        if not start_date:
            start_date = datetime(2023, 1, 1)
        if not end_date:
            end_date = datetime.now()

        # Find members with enough trades
        members_with_trades = db.query(
            Disclosure.member_id,
            func.count(Transaction.id).label('trade_count')
        ).join(Transaction).filter(
            Transaction.ticker.isnot(None),
            Transaction.transaction_date >= start_date,
            Transaction.transaction_date <= end_date
        ).group_by(Disclosure.member_id).having(
            func.count(Transaction.id) >= min_trades
        ).all()

        performances = []

        for member_id, trade_count in members_with_trades:
            try:
                comparison = self.compare_to_benchmarks(db, member_id, start_date, end_date)
                if comparison.get("member_performance", {}).get("estimated_return_pct") is not None:
                    performances.append(comparison)
            except Exception as e:
                logger.warning(f"Error analyzing member {member_id}: {e}")
                continue

        # Sort by return
        performances.sort(
            key=lambda x: x.get("member_performance", {}).get("estimated_return_pct", float('-inf')),
            reverse=True
        )

        # Get benchmark for comparison
        sp500_return = self.get_benchmark_returns("sp500", start_date, end_date)
        buffett_return = self.get_benchmark_returns("buffett", start_date, end_date)

        return {
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            "benchmarks": {
                "sp500": sp500_return,
                "buffett": buffett_return,
            },
            "total_members_analyzed": len(performances),
            "members_beating_sp500": sum(
                1 for p in performances
                if p.get("alpha_vs_sp500") and p["alpha_vs_sp500"] > 0
            ),
            "top_performers": performances[:limit],
            "worst_performers": performances[-limit:][::-1] if len(performances) > limit else [],
        }

    def get_performance_summary(self, db: Session) -> Dict[str, Any]:
        """Get overall performance summary statistics."""
        # Count members with transactions
        members_with_txn = db.query(
            func.count(func.distinct(Disclosure.member_id))
        ).join(Transaction).filter(
            Transaction.ticker.isnot(None)
        ).scalar() or 0

        # Count total transactions with tickers
        total_txn = db.query(func.count(Transaction.id)).filter(
            Transaction.ticker.isnot(None)
        ).scalar() or 0

        # Get unique tickers traded
        unique_tickers = db.query(
            func.count(func.distinct(Transaction.ticker))
        ).filter(
            Transaction.ticker.isnot(None)
        ).scalar() or 0

        # Top traded tickers
        top_tickers = db.query(
            Transaction.ticker,
            func.count(Transaction.id).label('count')
        ).filter(
            Transaction.ticker.isnot(None)
        ).group_by(Transaction.ticker).order_by(
            func.count(Transaction.id).desc()
        ).limit(10).all()

        return {
            "members_with_transactions": members_with_txn,
            "total_transactions": total_txn,
            "unique_tickers": unique_tickers,
            "top_traded_tickers": [
                {"ticker": t, "count": c} for t, c in top_tickers
            ],
            "benchmarks_available": list(BENCHMARKS.keys()),
        }


def analyze_performance(
    db: Session,
    member_id: Optional[int] = None,
    compare_benchmarks: bool = True
) -> Dict[str, Any]:
    """Convenience function for performance analysis."""
    analyzer = PerformanceAnalyzer()

    if member_id:
        if compare_benchmarks:
            return analyzer.compare_to_benchmarks(db, member_id)
        else:
            return analyzer.calculate_member_returns(db, member_id)
    else:
        return analyzer.get_performance_summary(db)

