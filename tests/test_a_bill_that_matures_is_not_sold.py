"""A Treasury bill "sold" on the maturity date in its own name was redeemed.

David Trone's T-bill ladder was published as a run of $1m+ sales; the ones
dated on the bill's own maturity are the Treasury paying the bill off, which
nobody decides to do on the day.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from src.analysis.asset_class import redeemed_at_maturity
from src.analysis.trade_analyzer import TradeAnalyzer
from src.db.models import TransactionType


class _Txn:
    def __init__(self, description, kind, when, low="1000001", high="5000000"):
        self.id = 1
        self.disclosure_id = 1
        self.description = description
        self.transaction_type = kind
        self.transaction_date = when
        self.amount_min = Decimal(low)
        self.amount_max = Decimal(high)
        self.ticker = None
        self.owner = "Joint"


BILL = "US Treasury Bill 02/22/2024 [GS]"


@pytest.mark.parametrize(
    ("description", "kind", "when", "redeemed"),
    [
        (BILL, TransactionType.SALE, datetime(2024, 2, 22), True),
        ("US Treasury Note due 3/15/25 [GS]", TransactionType.SALE, datetime(2025, 3, 15), True),
        (BILL, TransactionType.SALE, datetime(2024, 2, 19), False),  # sold early: a sale
        (BILL, TransactionType.PURCHASE, datetime(2024, 2, 22), False),
        (
            "Acme Corp 02/22/2024 Common Stock [ST]",
            TransactionType.SALE,
            datetime(2024, 2, 22),
            False,
        ),
    ],
)
def test_only_a_debt_sale_on_its_own_maturity_is_a_redemption(description, kind, when, redeemed):
    assert redeemed_at_maturity(_Txn(description, kind, when)) is redeemed


def test_a_redemption_is_not_a_large_trade():
    analyzer = TradeAnalyzer()
    rows = [
        _Txn(BILL, TransactionType.SALE, datetime(2024, 2, 22)),
        _Txn(BILL, TransactionType.SALE, datetime(2024, 2, 19)),
    ]

    found = analyzer._check_large_trades(rows, 1, None)

    assert len(found) == 1, "the early sale stays; the redemption does not"
