# PROJECT_BRIEF.md

Semi-automated trading system. Trader-in-the-loop: scanner detects signals against a fixed rule set, sends them to Telegram for manual approval, executes approved orders via broker API.

---

## 1. Roles

- **Architect / developer:** me. Builds infrastructure, rule engine, backtester, executor.
- **Trader:** partner (real-estate professional, trades discretionarily in free time). Source of strategy intuition. Approves every signal before execution in v1. Provides capital.
- **Physicist::** partner (physics  major with masters in Modelization and Mathematical Investigation, Statistics and Computation). Will help develop better strategies and structures. To join in v2.
- **Capital ownership:** account is in trader's name. Trader bears tax liability. Written agreement required before live phase.

## 2. Regulatory posture

- Personal-use tool for trader's own account. **No CNMV authorization required.**
- No third-party signal distribution. No managed-account features. No public outputs.
- Spain (EU resident). Every executed trade is a taxable event. System must produce a complete trade log with timestamps for tax filing.
- If the project ever expands to other users or public signals, regulatory review is mandatory and triggers a full redesign.

## 3. Locked decisions

| Item | Decision |
|---|---|
| Direction | Long-only (v1) |
| Account type | Cash account, no leverage |
| Position sizing | Dynamic sizing |
| Universe | 10 pre-selected US tickers, sector-diversified. List TBD with trader. |
| Approval model | Manual per-trade approval via Telegram, with timeout auto-reject |
| Order type | Bracket orders (entry + stop + target, atomic) |
| Broker | **Alpaca** (subject to confirmation that existing EU account is operational) |
| Language | Python 3.11+ |
| Infra v1 | Single VPS, Docker Compose. Hetzner-class. |
| Database | SQLite v1, PostgreSQL v2 |

### Broker rationale

Alpaca selected over IBKR because:
- Trader chose long-only US equities → IBEX access not needed.
- Architect already has working Alpaca infrastructure → reuse.
- Cleaner REST/WebSocket API, no IB Gateway daily-restart problem.
- Free IEX market data is sufficient for ~10 liquid large-caps on daily/intraday timeframes.
- Identical API between paper and live.

Switch back to IBKR only if: (a) Alpaca EU account access is blocked, or (b) trader later adds non-US tickers.

## 4. Stack

```
Runtime:        Python 3.11+
Broker SDK:     alpaca-py
Data:           yfinance (backtest), Alpaca historical + live (paper/live)
Indicators:     pandas-ta
Backtester:     backtesting.py (v1), reconsider vectorbt for v2
Telegram:       python-telegram-bot v21+ (async)
Scheduler:      APScheduler
ORM:            SQLAlchemy
Config:         pydantic-settings + .env
Logging:        loguru
Testing:        pytest
Packaging:      pyproject.toml, uv or poetry
Container:      Docker, docker-compose
```

## 5. Architecture

Components, all sharing a common **rule engine** module:

1. **Scanner.** Periodic job (APScheduler). Pulls latest bars for the 10 tickers, computes indicators, evaluates rules, emits signal objects.
2. **Signal store.** Persists every emitted signal with full context: prices, indicator values, timeframe snapshots, score, timestamp. Append-only.
3. **Notifier.** Formats signal as Telegram message with inline keyboard (Approve / Reject / Modify size). Sends to private chat ID.
4. **Approval handler.** Receives callback from Telegram. Updates signal state. Forwards approved signals to executor. Auto-rejects after configurable timeout (default: 15 min).
5. **Executor.** Places bracket order via Alpaca. Handles partial fills, rejections, retries with idempotency keys.
6. **Position monitor.** Tracks open positions. Sends exit notifications when stop or target hits. Logs realized P&L.
7. **Backtester.** Same rule engine, fed historical bars from yfinance/Alpaca. Outputs equity curve, Sharpe, max drawdown, win rate, per-ticker breakdown.
8. **Dashboard.** Web UI for signal history, P&L, rule tuning.

**Critical invariant:** the rule engine is a single module imported by both scanner and backtester. If they diverge, backtest results lie. Enforced via shared interface and integration tests.

## 6. Strategy specification (incomplete, pending rule-extraction)

### Confirmed direction

- Long-only.
- Trade only names already in confirmed uptrend.
- Uptrend definition (working): price > 200-day SMA AND 50-day SMA > 200-day SMA. To be validated against trader's intuition on historical charts.
- Entry style: pullback within uptrend, or breakout. Specific trigger TBD.
- Exit: bracket order with stop-loss and take-profit set at entry. Levels TBD.

### Open items requiring trader input

- [ ] Final list of 10 tickers (sector-diversified)
- [ ] Exact uptrend definition (validate working definition or revise)
- [ ] Entry trigger rules (pullback depth? breakout level? RSI threshold? volume confirmation?)
- [ ] Stop-loss methodology (% below entry? ATR-based? structure-based?)
- [ ] Take-profit methodology (fixed R multiple? trailing? structure-based?)
- [ ] Timeframes used for decision (confirmed: 1M, 1W, 1D, intraday — exact intraday bar size TBD: 1h? 15m?)
- [ ] News-window filter (don't enter within X hours of earnings — confirm X)
- [ ] Time-of-day filter (avoid first/last 15 min of US session?)
- [ ] Per-ticker profile sheet: ATR(14), avg true range %, typical pullback depth in uptrend, earnings calendar, avg volume

### Rule-extraction protocol

Screen-share session with trader. She narrates 10 past trades from her own history while looking at the same chart layout she normally uses. Architect transcribes. Output: deterministic rule spec. Iterate until generated signals match her past entries on historical data.

## 7. Phases

| Phase | Goal | Exit criterion |
|---|---|---|
| 0 | Specification | Written rule spec + 10-ticker list + per-ticker profiles |
| 1 | Backtester | Rule engine + backtest pipeline runs end-to-end on historical data. Trader's known past trades are reproduced as signals (sanity check). |
| 2 | Paper trading | Full pipeline (scanner → Telegram → approval → paper execution → position tracking) running 4+ weeks on Alpaca paper. Zero unhandled errors in last 7 days. |
| 3 | Live micro | Real money, 50€ positions, hard daily-loss kill-switch. Run 2+ weeks. Reconcile every fill. |
| 4 | Live target | 200€ positions. Ongoing monitoring and rule refinement. |

No phase skipping. Each catches failures the previous did not.

## 8. Safety mechanisms (non-negotiable)

- Daily max-loss kill-switch: liquidate all positions and halt new entries when triggered.
- Max concurrent positions cap.
- Idempotency keys on every order submission. No duplicate orders ever.
- Manual per-trade approval in v1. Full auto mode requires 6+ months of proven behavior and explicit upgrade.
- Heartbeat monitor: scanner and broker connection emit liveness pings; missed ping = Telegram alert within 60 seconds.
- All entries are bracket orders. No naked entries.
- Separate Telegram bot tokens and chat IDs for paper vs live. Hard to confuse.
- Time-of-day filter on entries unless strategy explicitly designed for the avoided window.
- Reconciliation job (daily): compare local position state vs broker state, alert on mismatch.

## 9. Costs

| Item | Cost |
|---|---|
| VPS (Hetzner CX22 or equivalent) | ~4€/month |
| Alpaca paper trading | 0$ |
| Alpaca live trading commissions | 0$ on stocks |
| Alpaca live market data (IEX feed) | 0$ |
| Domain (optional) | ~10€/year |
| Everything else | 0$ |

Total operational cost: under 5€/month plus zero commissions. Upgrade to Alpaca SIP feed (99$/mo) only if IEX data proves insufficient — unlikely for 10 large-caps.

## 10. Repository layout (target)

```
trading-system/
├── pyproject.toml
├── README.md
├── PROJECT_BRIEF.md          # this file
├── .env.example
├── docker-compose.yml
├── Dockerfile
├── src/
│   └── trading_system/
│       ├── __init__.py
│       ├── config.py         # pydantic-settings
│       ├── rules/            # shared rule engine
│       │   ├── __init__.py
│       │   ├── engine.py
│       │   └── indicators.py
│       ├── data/             # data adapters (yfinance, alpaca)
│       ├── backtest/         # backtester, harness
│       ├── scanner/          # live scanner job
│       ├── notifier/         # Telegram bot
│       ├── executor/         # Alpaca order placement
│       ├── monitor/          # position monitor
│       ├── store/            # SQLAlchemy models, persistence
│       └── safety/           # kill-switch, heartbeat, reconciliation
└── tests/
    ├── unit/
    ├── integration/
    └── fixtures/
```

## 11. Immediate next actions

1. Confirm Alpaca EU account is active and funded-capable for current Spanish residency.
2. Get the 10 tickers from trader. Verify all tradable on Alpaca, sector-diversified.
3. Schedule rule-extraction session (90 min, screen share, 10 historical trades).
4. Open Alpaca paper account if not already active.
5. Scaffold repo per layout above. Phase 1 entry point: backtester pipeline running a stub rule (e.g. price > 200 SMA) on one ticker via yfinance, producing an equity curve.

## 12. Open questions log

- [ ] Alpaca EU residency status confirmed?
- [ ] All 10 tickers US-only confirmed (no IBEX)?
- [ ] Long-only locked for v1 confirmed by trader (text confirmation received: yes)?
- [ ] Leverage interpretation: trader confirmed cash-only or sizing-dynamic? (Question sent, awaiting answer.)
- [ ] Account ownership and tax liability agreement drafted between architect and trader?
- [ ] Intraday bar size for decision timeframe (1h vs 15m vs 5m)?

Update this section as items resolve. Move resolved items to section 3 (Locked decisions) or section 6 (Strategy spec).

## 13. Agent's behavior
- Every correction, assumption or decision taken must be written in a .md file so that when the user opens different conversations to keep the context of the conversation the system doesn't start over and has context from other conversations.


