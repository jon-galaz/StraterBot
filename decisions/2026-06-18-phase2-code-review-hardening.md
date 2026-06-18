# 2026-06-18 — Phase 2 code-review hardening

Full multi-agent code review of the system before paper-trading deployment.
Four parallel reviews (signal logic, safety/execution, backtest math, wiring).
Every CRITICAL finding was verified against source before fixing. Decisions and
rationale below; see `changelog.txt` for the file-by-file change list.

## Safety / live-execution (highest priority)

- **Order idempotency was fake.** `client_order_id` was a fresh `uuid4()` per
  call, so a retry / double-click / two traders produced *different* keys and
  Alpaca accepted duplicate bracket orders. **Decision:** make the key
  deterministic — `client_order_id = f"sig-{signal_id}"`. A duplicate submission
  now reuses the key and Alpaca rejects it. Backed by a DB `unique=True` on
  `TradeRecord.client_order_id`.

- **Approve→execute was not atomic.** **Decision:** claim the signal
  `pending→executing` with a guarded `UPDATE ... WHERE status='pending'` and
  check `rowcount` before submitting, plus a process-wide `threading.Lock` so
  cap/duplicate checks see a consistent view. Pre-submit failures (cap, dup,
  no-edge) release the claim back to `pending` for retry; a *broker* failure
  intentionally leaves the signal `executing` (fail closed — never auto-retry,
  because the order may have reached Alpaca).

- **Position cap / duplicate checks were blind to in-flight orders.** They
  queried only *filled* Alpaca positions, so a burst of approvals before any
  fill could exceed the cap. **Decision:** count local `TradeRecord` rows with
  `status='open'` (created at submit time) instead.

- **Kill switch could be bypassed and didn't survive restarts.** It was only
  checked in the notifier and held in memory. **Decision:** (1) re-check inside
  `AlpacaExecutor.execute()` so no caller can bypass it; (2) persist the
  triggered state to a date-keyed `kill_switch.json` so a restart on a losing
  day stays halted; (3) on breach, latch the halt and alert *before/independent
  of* liquidation, verify `close_all_positions` results, and escalate loudly if
  any close fails.

- **Reconciliation only compared presence.** **Decision:** also compare per-
  ticker quantity (local vs broker) and flag drift.

- **Telegram auth failed open.** Empty `trader_user_ids` authorised everyone.
  **Decision:** fail closed — empty list authorises no one, with a loud startup
  warning. Applied in both the callback handler and `_is_trader`.

- **Event loop was blocked** by synchronous broker/backtest calls (the source of
  the "heartbeat missed" warnings). **Decision:** run `executor.execute`,
  `/status`, and the sizing recompute via `asyncio.to_thread`. Because DB
  sessions can now be created on worker threads, SQLite engines are opened with
  `check_same_thread=False`.

- **Expire-sweep held a SQLite txn across network calls** and could clobber a
  just-approved card. **Decision:** transition status first (guarded per-record
  update), commit, then do Telegram edits outside the session.

- **Scanner cron was an hour off in winter** (fixed 21:05 UTC). **Decision:**
  schedule market-relative jobs in `America/New_York` (scan 16:05 ET, recon
  16:30 ET) so they're DST-safe.

## Backtest ↔ live consistency (the core invariant)

- **Volume filter not reproducible live:** backtest uses yfinance consolidated
  volume, live used Alpaca IEX (partial-venue) volume, so the 1.5×SMA filter
  diverges. **Decision:** make the data feed configurable (`alpaca_data_feed`,
  default `iex`); document that production should use `sip`. Full fidelity
  requires a paid SIP subscription — flagged, not silently ignored.

- **Donchian-breakdown exit existed only in the backtester.** The live monitor
  trailed the SL but never applied the shared `RuleEngine.is_exit`. **Decision:**
  the position monitor now calls `RuleEngine(ticker).is_exit(bars)` and closes
  the position on breakdown, so live exits match backtest. Exit-price lookup
  gained a fallback (most recent filled SELL order) so non-bracket closes still
  record a taxable-event price.

## Backtest math & research tooling

- **Sortino/Calmar `/1e-6` magic numbers** → return `float('inf')` for the
  undefined (no-downside / no-drawdown) cases.
- **Risk-free de-annualization** hardcoded `/252` regardless of `freq` → use
  periods-per-year (`ann_factor**2`). Latent (risk_free defaulted 0) but fixed.
- **Kelly counted scratch trades (PnL==0) as losses** → exclude them from both
  win and loss; `p` is now over decisive trades only.
- **Thread-unsafe class-attribute mutation** (`bt._strategy.ticker = …`) under
  `ThreadPoolExecutor` → pass params via `Backtest.run(ticker=…, risk_pct=…)`,
  which sets per-instance attributes (verified class attrs stay untouched).
- **`ablation.py` / `regime_sweep.py` were broken** — they called a
  `regime_series` param and `fetch_regime_series` that didn't exist, and the
  errors were swallowed, producing authoritative-looking all-zero garbage.
  **Decision:** implement real, thread-safe regime support in
  `runner.run_strategy` + `strategy.regime_series`, add `fetch_regime_series`,
  and log (don't swallow) per-ticker failures.
- **Duplication** removed: one `build_backtest`/`run_strategy` helper used
  everywhere; `BacktestResult` now carries the trades, so `portfolio.collect_all_trades`
  and `chart.py`'s second run are gone (they previously diverged on
  commission/finalize). Commission is now `COMMISSION` everywhere (was 0.0 in
  `bt.py`/`chart.py`). Standardised on `finalize_trades=True`.

## Config

- Replaced the custom `EnvSettingsSource`/`settings_customise_sources` machinery
  with `Annotated[list[str], NoDecode]` + the existing validators (simpler,
  documented pydantic-settings v2 idiom). `DEFAULT_UNIVERSE` now lives in
  `config.py` as the single source the scripts import (was copy-pasted 4×).

## Open follow-ups (not done here)

- **DB migration:** the new `unique=True` on `client_order_id` and any schema
  changes are NOT applied to an existing `trading.db` by `create_all`. Start
  Phase 2 paper trading from a fresh DB, or add Alembic before Phase 3.
- **Heartbeat is in-process** — it can't detect a fully dead loop. Add an
  external watchdog (systemd `Restart=` / docker healthcheck) for Phase 2.
- **SIP feed**: switch `ALPACA_DATA_FEED=sip` once subscribed so live volume
  matches backtest.
- Spec says 10-ticker universe (CLAUDE.md); we run 30 deliberately — reconcile
  the brief text when convenient.
