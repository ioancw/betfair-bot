# betfair-bot

Betfair pre-race trading bot (price signal layer) built on flumine and betfairlightweight.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in credentials and CERT_PATH
```

`CERT_PATH` must point at a directory containing `client-2048.crt` and `client-2048.key`.

## Usage

```bash
python scripts/list_markets.py   # next 20 UK horse racing WIN markets
```

## Tests

All tests are mocked; no Betfair connection is needed. The four required
variables only need dummy values:

```bash
BETFAIR_USERNAME=x BETFAIR_PASSWORD=x BETFAIR_APP_KEY=x CERT_PATH=/tmp python3 -m pytest tests/
```
