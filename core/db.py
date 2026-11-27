"""Database schema and access.

One schema definition (SQLAlchemy Core) works on both Supabase Postgres in the
cloud and SQLite for local development/tests. Tables are created on first run;
on Postgres, Row Level Security is switched on with no policies, so Supabase's
public REST API (anon key) can read nothing. The app connects directly as the
database owner, which RLS does not restrict.

Model
-----
accounts        one row per bank account (IBKR U123..., Saxo client id, ...)
imports         one row per confirmed statement upload (sha256 prevents re-import)
positions       holdings snapshot per import; "current" = latest import per account
cash_balances   cash per currency per import
transactions    deduplicated by fingerprint across overlapping statements
ticker_map      instrument_key (ISIN or BANK:symbol) -> Yahoo ticker
overrides       manual corrections, e.g. cost basis of zero-cost option structures
notes           free-text notes + tags per instrument
manual_items    "Other" cash and illiquid holdings with manual valuations
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, MetaData,
    String, Table, Text, UniqueConstraint, create_engine, select, text, func,
    insert, update, delete, and_,
)
from sqlalchemy.engine import Engine

from core.models import ParsedStatement

metadata = MetaData()


def _now():
    return datetime.now(timezone.utc)


accounts = Table(
    "accounts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("bank", String(20), nullable=False),
    Column("account_ref", String(64), nullable=False),
    Column("base_currency", String(3)),
    Column("label", String(100)),
    UniqueConstraint("bank", "account_ref", name="uq_account"),
)

imports = Table(
    "imports", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("account_id", Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
    Column("period_start", Date),
    Column("period_end", Date, nullable=False),
    Column("file_name", String(255)),
    Column("file_sha256", String(64), nullable=False, unique=True),
    Column("parser", String(40)),
    Column("totals_json", Text),          # statement-reported totals + check results
    Column("storage_path", String(255)),  # optional copy in a private Supabase bucket
    Column("imported_at", DateTime(timezone=True), default=_now),
)

positions = Table(
    "positions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("import_id", Integer, ForeignKey("imports.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("account_id", Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("as_of", Date, nullable=False),
    Column("instrument_key", String(80), nullable=False, index=True),
    Column("symbol", String(80)),
    Column("isin", String(12)),
    Column("description", String(255)),
    Column("asset_class", String(40)),
    Column("currency", String(3), nullable=False),
    Column("quantity", Float, nullable=False),
    Column("multiplier", Float, default=1.0),
    Column("cost_basis", Float),
    Column("close_price", Float),
    Column("market_value", Float),
)

cash_balances = Table(
    "cash_balances", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("import_id", Integer, ForeignKey("imports.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("account_id", Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
    Column("as_of", Date, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("amount", Float, nullable=False),
)

transactions = Table(
    "transactions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("account_id", Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("import_id", Integer, ForeignKey("imports.id", ondelete="SET NULL")),
    Column("fingerprint", String(32), nullable=False),
    Column("trade_date", Date, nullable=False, index=True),
    Column("settle_date", Date),
    Column("tx_type", String(20), nullable=False),
    Column("instrument_key", String(80), index=True),
    Column("symbol", String(80)),
    Column("description", String(255)),
    Column("currency", String(3), nullable=False),
    Column("quantity", Float),
    Column("price", Float),
    Column("amount", Float, nullable=False),
    Column("fees", Float, default=0.0),
    Column("realized_pnl", Float),
    UniqueConstraint("account_id", "fingerprint", name="uq_tx_fingerprint"),
)

ticker_map = Table(
    "ticker_map", metadata,
    Column("instrument_key", String(80), primary_key=True),
    Column("yahoo_ticker", String(40)),
    Column("display_name", String(255)),
    # Yahoo quotes some London lines in pence (GBp); divide by this to get GBP.
    Column("price_divisor", Float, default=1.0),
    Column("source", String(40)),          # "manual", "excel-seed", ...
    Column("updated_at", DateTime(timezone=True), default=_now, onupdate=_now),
)

overrides = Table(
    "overrides", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("account_id", Integer, ForeignKey("accounts.id", ondelete="CASCADE")),  # NULL = all accounts
    Column("instrument_key", String(80), nullable=False, index=True),
    Column("field", String(40), nullable=False),     # "cost_basis" for now
    Column("value", Float, nullable=False),
    Column("currency", String(3)),
    Column("reason", Text),
    Column("active", Boolean, default=True, nullable=False),
    Column("created_at", DateTime(timezone=True), default=_now),
)

notes = Table(
    "notes", metadata,
    Column("instrument_key", String(80), primary_key=True),
    Column("note", Text),
    Column("tags", String(255)),            # comma-separated
    Column("updated_at", DateTime(timezone=True), default=_now, onupdate=_now),
)

manual_items = Table(
    "manual_items", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", String(20), nullable=False),      # "cash" | "illiquid"
    Column("label", String(255), nullable=False),
    Column("holder", String(100)),                   # where it's held
    Column("currency", String(3), nullable=False),
    Column("value", Float, nullable=False),
    Column("cost_basis", Float),
    Column("valued_on", Date),
    Column("notes", Text),
    Column("active", Boolean, default=True, nullable=False),
)

ALL_TABLES = [t.name for t in metadata.sorted_tables]


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #
def make_engine(url: str) -> Engine:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    kwargs = {"pool_pre_ping": True}
    if url.startswith("postgresql"):
        kwargs["connect_args"] = {"sslmode": "require"}
    engine = create_engine(url, **kwargs)
    init_schema(engine)
    return engine


def init_schema(engine: Engine) -> None:
    metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            for name in ALL_TABLES:
                conn.execute(text(f'alter table public."{name}" enable row level security'))


# --------------------------------------------------------------------------- #
# Import a confirmed statement
# --------------------------------------------------------------------------- #
class DuplicateImport(Exception):
    pass


def get_or_create_account(conn, bank: str, account_ref: str, base_currency: Optional[str]) -> int:
    row = conn.execute(
        select(accounts.c.id).where(and_(accounts.c.bank == bank, accounts.c.account_ref == account_ref))
    ).first()
    if row:
        return row.id
    return conn.execute(
        insert(accounts).values(bank=bank, account_ref=account_ref, base_currency=base_currency)
        .returning(accounts.c.id)
    ).scalar_one()


def save_statement(engine: Engine, st: ParsedStatement, file_name: str, file_sha256: str,
                   parser: str, storage_path: Optional[str] = None) -> dict:
    """Write a parsed + confirmed statement in one transaction.

    Returns counts. Raises DuplicateImport if this exact file was saved before.
    Transactions already present (overlapping statement periods) are skipped.
    """
    with engine.begin() as conn:
        if conn.execute(select(imports.c.id).where(imports.c.file_sha256 == file_sha256)).first():
            raise DuplicateImport(file_name)

        account_id = get_or_create_account(conn, st.bank, st.account_ref, st.base_currency)
        totals = {
            "statement": st.totals,
            "fx_to_base": st.fx_to_base,
            "checks": [
                {"name": c.name, "expected": c.expected, "actual": c.actual, "status": c.status}
                for c in st.checks
            ],
            "warnings": st.warnings,
        }
        import_id = conn.execute(
            insert(imports).values(
                account_id=account_id, period_start=st.period_start, period_end=st.period_end,
                file_name=file_name, file_sha256=file_sha256, parser=parser,
                totals_json=json.dumps(totals, default=str), storage_path=storage_path,
            ).returning(imports.c.id)
        ).scalar_one()

        if st.positions:
            conn.execute(insert(positions), [
                dict(import_id=import_id, account_id=account_id, as_of=st.period_end,
                     instrument_key=p.instrument_key, symbol=p.symbol, isin=p.isin,
                     description=p.description, asset_class=p.asset_class, currency=p.currency,
                     quantity=p.quantity, multiplier=p.multiplier, cost_basis=p.cost_basis,
                     close_price=p.close_price, market_value=p.market_value)
                for p in st.positions
            ])
        if st.cash:
            conn.execute(insert(cash_balances), [
                dict(import_id=import_id, account_id=account_id, as_of=st.period_end,
                     currency=c.currency, amount=c.amount)
                for c in st.cash
            ])

        existing = {
            r.fingerprint for r in conn.execute(
                select(transactions.c.fingerprint).where(transactions.c.account_id == account_id)
            )
        }
        new_tx = []
        for t in st.transactions:
            fp = t.fingerprint(st.account_ref)
            if fp in existing:
                continue
            existing.add(fp)
            new_tx.append(dict(
                account_id=account_id, import_id=import_id, fingerprint=fp,
                trade_date=t.trade_date, settle_date=t.settle_date, tx_type=t.tx_type,
                instrument_key=t.instrument_key, symbol=t.symbol, description=t.description,
                currency=t.currency, quantity=t.quantity, price=t.price, amount=t.amount,
                fees=t.fees, realized_pnl=t.realized_pnl,
            ))
        if new_tx:
            conn.execute(insert(transactions), new_tx)

    return {
        "import_id": import_id,
        "positions": len(st.positions),
        "cash": len(st.cash),
        "transactions_new": len(new_tx),
        "transactions_skipped": len(st.transactions) - len(new_tx),
    }


def delete_import(engine: Engine, import_id: int) -> None:
    """Undo an import: its positions/cash go (cascade); its transactions too."""
    with engine.begin() as conn:
        conn.execute(delete(transactions).where(transactions.c.import_id == import_id))
        conn.execute(delete(positions).where(positions.c.import_id == import_id))
        conn.execute(delete(cash_balances).where(cash_balances.c.import_id == import_id))
        conn.execute(delete(imports).where(imports.c.id == import_id))


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
def _latest_import_ids(conn):
    """Latest import (by statement end date, then id) for each account."""
    sub = (
        select(imports.c.account_id, func.max(imports.c.period_end).label("pe"))
        .group_by(imports.c.account_id).subquery()
    )
    rows = conn.execute(
        select(imports.c.account_id, func.max(imports.c.id).label("iid"))
        .join(sub, and_(imports.c.account_id == sub.c.account_id, imports.c.period_end == sub.c.pe))
        .group_by(imports.c.account_id)
    ).all()
    return [r.iid for r in rows]


def current_positions(engine: Engine) -> pd.DataFrame:
    with engine.connect() as conn:
        ids = _latest_import_ids(conn)
        if not ids:
            return pd.DataFrame()
        q = (
            select(positions, accounts.c.bank, accounts.c.account_ref)
            .join(accounts, positions.c.account_id == accounts.c.id)
            .where(positions.c.import_id.in_(ids))
        )
        return pd.read_sql(q, conn)


def current_cash(engine: Engine) -> pd.DataFrame:
    with engine.connect() as conn:
        ids = _latest_import_ids(conn)
        if not ids:
            return pd.DataFrame()
        q = (
            select(cash_balances, accounts.c.bank, accounts.c.account_ref)
            .join(accounts, cash_balances.c.account_id == accounts.c.id)
            .where(cash_balances.c.import_id.in_(ids))
        )
        return pd.read_sql(q, conn)


def read_table(engine: Engine, table: Table) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(select(table), conn)


def list_imports(engine: Engine) -> pd.DataFrame:
    with engine.connect() as conn:
        q = (
            select(imports.c.id, accounts.c.bank, accounts.c.account_ref, imports.c.period_start,
                   imports.c.period_end, imports.c.file_name, imports.c.imported_at)
            .join(accounts, imports.c.account_id == accounts.c.id)
            .order_by(imports.c.imported_at.desc())
        )
        return pd.read_sql(q, conn)


def all_transactions(engine: Engine) -> pd.DataFrame:
    with engine.connect() as conn:
        q = (
            select(transactions, accounts.c.bank, accounts.c.account_ref)
            .join(accounts, transactions.c.account_id == accounts.c.id)
            .order_by(transactions.c.trade_date)
        )
        return pd.read_sql(q, conn)


# --------------------------------------------------------------------------- #
# Writes for user-maintained tables (mapping, overrides, notes, manual items)
# --------------------------------------------------------------------------- #
def upsert_ticker(engine: Engine, instrument_key: str, yahoo_ticker: Optional[str],
                  display_name: Optional[str] = None, price_divisor: float = 1.0,
                  source: str = "manual") -> None:
    values = dict(yahoo_ticker=yahoo_ticker or None, display_name=display_name,
                  price_divisor=price_divisor or 1.0, source=source, updated_at=_now())
    with engine.begin() as conn:
        exists = conn.execute(
            select(ticker_map.c.instrument_key).where(ticker_map.c.instrument_key == instrument_key)
        ).first()
        if exists:
            conn.execute(update(ticker_map).where(ticker_map.c.instrument_key == instrument_key).values(**values))
        else:
            conn.execute(insert(ticker_map).values(instrument_key=instrument_key, **values))


def upsert_note(engine: Engine, instrument_key: str, note: str, tags: str) -> None:
    with engine.begin() as conn:
        exists = conn.execute(select(notes.c.instrument_key).where(notes.c.instrument_key == instrument_key)).first()
        values = dict(note=note or None, tags=tags or None, updated_at=_now())
        if exists:
            conn.execute(update(notes).where(notes.c.instrument_key == instrument_key).values(**values))
        else:
            conn.execute(insert(notes).values(instrument_key=instrument_key, **values))


def replace_rows(engine: Engine, table: Table, df: pd.DataFrame, key: str = "id") -> None:
    """Persist an edited data_editor frame for a simple table: rows with an id are
    updated, rows without are inserted, ids no longer present are deleted."""
    cols = [c.name for c in table.columns]
    with engine.begin() as conn:
        existing = {r[0] for r in conn.execute(select(table.c[key]))}
        keep = set()
        for rec in df.to_dict("records"):
            rec = {k: (None if pd.isna(v) else v) for k, v in rec.items() if k in cols}
            rid = rec.pop(key, None)
            if rid is not None and int(rid) in existing:
                keep.add(int(rid))
                conn.execute(update(table).where(table.c[key] == int(rid)).values(**rec))
            else:
                conn.execute(insert(table).values(**rec))
        gone = existing - keep
        if gone:
            conn.execute(delete(table).where(table.c[key].in_(gone)))
