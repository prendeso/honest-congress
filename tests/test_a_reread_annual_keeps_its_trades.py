"""Re-reading a Senate annual report deleted its trades and put none back.

`parse_senate_annual` was written for Part 3 and Part 7 -- assets and debts --
and returned an empty transaction list. The orchestrator clears every row a
filing holds before storing a new read of it, transactions included. So the
first re-parse of a Senate annual report deleted the Part 4b trades the older
reader had stored, and nothing restored them.

Found by an audit of live findings: the annual reports behind committee-bill
and cross-member cluster findings for eleven senators showed zero transactions,
while the findings built on those trades were still published.
"""

from __future__ import annotations

from datetime import datetime

from src.db.models import Chamber, Disclosure, Member, Party, Transaction, TransactionType
from src.parsing.senate_html_parser import SenateHtmlParser

ANNUAL = """
<html><body>
<h3 class="h4">Part 3. Assets</h3>
<table>
  <thead><tr>
    <th></th><th>Asset</th><th>Asset Type</th><th>Owner</th>
    <th>Value</th><th>Income Type</th><th>Income</th>
  </tr></thead>
  <tbody>
    <tr>
      <td>1</td><td><strong>SPDR Gold Trust</strong></td>
      <td>Investment Fund</td><td>Self</td><td>$15,001 - $50,000</td>
      <td>None</td><td>None (or less than $201)</td>
    </tr>
  </tbody>
</table>
<h3 class="h4">Part 4b. Transactions</h3>
<table>
  <tr>
    <th></th><th>#</th><th>Owner</th><th>Ticker</th><th>Asset Name</th>
    <th>Transaction Type</th><th>Transaction Date</th><th>Amount</th><th>Comments</th>
  </tr>
  <tr>
    <td></td><td>1</td><td>Self</td><td>GLD</td><td>SPDR Gold Trust</td>
    <td>Purchase</td><td>03/15/2023</td><td>$15,001 - $50,000</td><td>--</td>
  </tr>
  <tr>
    <td></td><td>2</td><td>Spouse</td><td>MSFT</td><td>Microsoft Corp</td>
    <td>Sale (Full)</td><td>04/02/2023</td><td>$1,001 - $15,000</td><td>--</td>
  </tr>
</table>
</body></html>
"""


def test_the_annual_reader_returns_the_trades(tmp_path):
    path = tmp_path / "annual.html"
    path.write_text(ANNUAL, encoding="utf-8")

    result = SenateHtmlParser().parse_senate_annual(str(path))

    assert len(result["assets"]) == 1
    tickers = sorted(t["ticker"] for t in result["transactions"])
    assert tickers == ["GLD", "MSFT"]


def _senate_annual(db):
    member = Member(
        bioguide_id="SA00001",
        first_name="Ann",
        last_name="Ual",
        chamber=Chamber.SENATE,
        party=Party.DEMOCRAT,
        state="RI",
    )
    db.add(member)
    db.commit()
    disclosure = Disclosure(
        member_id=member.id,
        filing_year=2023,
        filing_type="Annual Report for CY 2023",
        filing_date=datetime(2024, 5, 15),
        document_id="Sabc",
        document_url="https://efdsearch.senate.gov/search/view/annual/abc/",
        is_ptr=False,
        parsed=True,
    )
    db.add(disclosure)
    db.commit()
    db.refresh(disclosure)
    return disclosure


def test_a_reread_keeps_the_trades_the_filing_prints(db_session, tmp_path):
    from src.ingestion.orchestrator import IngestionOrchestrator

    saved = tmp_path / "Sabc.html"
    saved.write_text(ANNUAL, encoding="utf-8")
    disclosure = _senate_annual(db_session)

    # What the older reader left behind: the trades, and no assets.
    for ticker in ("GLD", "MSFT"):
        db_session.add(
            Transaction(
                disclosure_id=disclosure.id,
                transaction_date=datetime(2023, 3, 15),
                transaction_type=TransactionType.PURCHASE,
                description=ticker,
                ticker=ticker,
            )
        )
    db_session.commit()

    orch = IngestionOrchestrator(data_dir=tmp_path)
    orch.download_disclosure_pdf = lambda d, force=False: saved  # type: ignore[method-assign]
    assert orch.parse_disclosure(db_session, disclosure) is True

    stored = db_session.query(Transaction).filter_by(disclosure_id=disclosure.id).all()
    assert sorted(t.ticker for t in stored) == ["GLD", "MSFT"], (
        "re-reading the annual report cleared its trades and stored none"
    )
    assert {t.owner for t in stored} == {"Self", "Spouse"}
