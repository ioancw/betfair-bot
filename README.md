# betfair-bot

A pre-race trading bot for Betfair horse racing, built on flumine and
betfairlightweight. It bets both sides of the same horse before the race
starts and aims to profit from the price moving, not from who wins.

**Status:** everything below is tested offline (simulated races, a real
TimescaleDB). Nothing has been run against the live Betfair API yet, and the
included strategy is unproven on real data. Treat it as a framework to test
ideas with, not as something that makes money.

## How it works

```
Betfair live stream ──► stream logger ──► TimescaleDB (ticks)   ← study prices with SQL
                              │
                              └────────► recording files ──► backtester ──► profit report
                                                                  ▲
Betfair live stream ──► momentum strategy (paper or live) ────────┘ same strategy code
```

1. **Stream logger** (`scripts/run_stream_logger.py`) subscribes to upcoming
   UK horse racing WIN markets and records every price change. It places no
   bets. It writes a row per runner per change to the `ticks` table, and the
   raw stream to files under `data/streams/`.
2. **Signals** (`src/signals.py`) turn prices into numbers the strategy can
   act on: how far a price has moved over the last two minutes, how wide the
   back/lay gap is, and whether more money is queueing to back or to lay.
3. **Momentum strategy** (`src/strategies/momentum.py`) uses those signals:
   - A horse whose price has shortened by 3% or more, with more money
     queueing to back than lay, is a *steamer*: back it, then lay it at a
     lower price.
   - A horse whose price has lengthened by 3% or more, with more money
     queueing to lay, is a *drifter*: lay it, then back it at a higher price.
   - Each position has a profit target (3 price steps), a stop-loss
     (4 steps), and is closed 60 seconds before the off whatever happens.
   - The closing bet is sized so the profit or loss is the same whichever
     horse wins.
4. **Backtester** (`scripts/run_backtest.py`) replays recorded races through
   the same strategy code using flumine's simulator, which models whether
   orders would have been matched, and reports profit after commission.
5. **Paper trading** (`scripts/run_strategy.py`) runs the strategy on the live
   stream with simulated orders. Adding `--live` sends real orders.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in credentials and CERT_PATH
docker compose up -d        # TimescaleDB on localhost:5432
python scripts/init_db.py   # create tables
```

`CERT_PATH` must point at a directory containing `client-2048.crt` and
`client-2048.key`. The backtester and `init_db.py` need no Betfair credentials.

## Try it without a Betfair account

```bash
python scripts/make_sample_data.py          # 5 invented races in data/sample
python scripts/run_backtest.py data/sample --verbose
```

The sample races are synthetic: one horse is made to steam and one to drift,
and in some races the move reverses. They show the mechanics only.

## Using it for real

| Step | Command | Needs |
|---|---|---|
| Check login | `python scripts/list_markets.py` | app key, certificate |
| Record prices | `python scripts/run_stream_logger.py` | database running |
| Backtest recordings | `python scripts/run_backtest.py data/streams` | recorded or bought data |
| Paper trade | `python scripts/run_strategy.py` | live app key for real-time prices |
| Real orders | `BETFAIR_LIVE_TRADING=yes python scripts/run_strategy.py --live` | all of the above, and evidence it works |

### Backtesting on Betfair's historical data

Data bought from https://historicdata.betfair.com downloads as a `.tar` of
`.bz2` files, one per market. Extract the tar and point the backtester at the
folder; the `.bz2` files are read as they are.

```bash
mkdir -p data/historic && tar -xf ~/Downloads/data.tar -C data/historic
python scripts/run_backtest.py data/historic
```

Only GB WIN markets are traded by default, so place markets and other
countries in the download are skipped (`--countries GB IE` to widen).

Strategy settings (`--stake`, `--threshold`, `--lookback`, `--target-ticks`,
`--stop-ticks`) are the same for backtesting and trading; all of them are
listed in `MomentumConfig`.

Live trading is refused unless both `--live` and `BETFAIR_LIVE_TRADING=yes`
are given, and stakes above 5 are rejected.

## Looking at recorded prices

```sql
-- Price path of every runner in one race, one row per 10 seconds
SELECT time_bucket('10 seconds', t.time) AS bucket, r.runner_name, last(t.ltp, t.time) AS price
FROM ticks t JOIN runners r USING (market_id, selection_id)
WHERE t.market_id = '1.234567890'
GROUP BY 1, 2 ORDER BY 1, 2;

-- Biggest movers in the last 10 minutes before each race
SELECT m.venue, m.start_time, r.runner_name,
       first(t.ltp, t.time) AS price_then, last(t.ltp, t.time) AS price_at_off
FROM ticks t
JOIN markets m USING (market_id)
JOIN runners r USING (market_id, selection_id)
WHERE t.time BETWEEN m.start_time - INTERVAL '10 minutes' AND m.start_time
GROUP BY 1, 2, 3
ORDER BY abs(last(t.ltp, t.time) / first(t.ltp, t.time) - 1) DESC
LIMIT 20;
```

## Tests

```bash
python3 -m pytest tests/
```

No Betfair connection or environment variables are needed. To include the
round trip through a real database:

```bash
TEST_DATABASE_URL=postgresql://betfair:betfair@localhost:5432/betfair python3 -m pytest tests/
```
