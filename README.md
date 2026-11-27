# Portfolio from statements

Streamlit app that reads raw bank statements (IBKR, Saxo, HL, SQ), builds
positions, cash, cost basis and transactions, stores them in Supabase Postgres,
and values everything live with Yahoo Finance prices. Reporting currency is EUR,
USD or GBP. This app is separate from `portfolio-dashboard` and does not touch it.

## Data rules

- No statements, holdings or credentials in git. `.gitignore` blocks
  `*.csv/*.pdf/*.xlsx` and similar, plus `secrets.toml`. The one exception is
  `tests/fixtures/`, which holds **synthetic** files only.
- Statements are parsed in memory by local code (`core/parsers/`). Nothing is
  sent to Claude or any other service. Only tickers and FX pairs go to Yahoo.
- Nothing is saved until you press **Confirm and save** after reviewing the
  checks against statement totals. Raw files are not stored.

## Layout

```
app.py                     navigation, password gate, currency picker
core/models.py             bank-agnostic Position / Transaction / Cash / Check
core/parsers/ibkr.py       IBKR Activity Statement CSV (section-based)
core/parsers/__init__.py   parser registry + format detection
core/db.py                 schema (SQLAlchemy Core) + reads/writes
core/portfolio.py          valuation: live/statement price, FX, overrides, day/MTD
core/prices.py             Yahoo Finance closes + FX (cached 15 min)
views/                     Dashboard, Import, Mapping & overrides, Other holdings
tests/                     pytest, synthetic fixtures only
```

### Tables

| table | what |
|---|---|
| `accounts` | one per bank account |
| `imports` | one per confirmed statement (sha256 blocks re-importing the same file) |
| `positions` | holdings snapshot per import. The current view is the latest import per account |
| `cash_balances` | cash per currency per import |
| `transactions` | trades, dividends, tax, fees, deposits, FX, deduplicated across overlapping statements |
| `ticker_map` | instrument key (ISIN, or `BANK:symbol`) → Yahoo ticker |
| `overrides` | e.g. corrected cost basis for zero-cost option structures |
| `notes` | note + tags per instrument |
| `manual_items` | "Other" cash and illiquid holdings with manual valuations |

Tables are created automatically on first run. On Postgres, Row Level Security
is enabled with no policies, so Supabase's public REST API can't read anything.
The app connects directly with the database password.

## Local development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # set a password; sqlite url is fine locally
.venv/bin/python -m pytest -q
.venv/bin/streamlit run app.py
```

## Cloud setup (you do these steps; the app never creates accounts)

1. **Supabase**: create a project in an EU region on the free tier. Copy the
   *Session pooler* connection string from Project Settings → Database (Streamlit
   Cloud needs IPv4). Free projects pause after about a week of inactivity, so
   un-pause from the Supabase dashboard if the app can't connect.
2. **GitHub**: push this repo as a new **private** repo.
3. **Streamlit Community Cloud**: New app → this repo → `app.py`. Under
   Settings → Secrets, paste the contents of `secrets.toml.example` with real
   values. Under Settings → Sharing, restrict viewers to your email if your plan
   allows another private app. If it doesn't, the app is public and the app
   password is the only gate, so use a long random one.

## Adding a bank

Write `core/parsers/<bank>.py` exposing `BANK`, `NAME`, `looks_like(text)` and
`parse(raw, file_name) -> ParsedStatement`. Include `Check`s against the
statement's own totals. Register it in `core/parsers/__init__.py` and add a test
with a synthetic fixture.

## Roadmap

- [x] Repo skeleton, schema, IBKR Activity CSV parser, upload → check → confirm
- [x] Dashboard (net worth, day/MTD, unrealised P&L, movers), mapping, overrides, notes, manual items
- [ ] Validate IBKR parser against a real statement (column names/total rows may differ)
- [ ] Saxo parser (CSV/Excel), HL parser (PDF), SQ parser
- [ ] One-off import of the existing Excel position list → ticker map + cost basis
- [ ] Rebuild holdings from transactions and reconcile with statement positions
- [ ] Optional: keep originals in a private Supabase storage bucket
