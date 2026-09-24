# The history store

This branch is **not** the project. It holds the normalised history the
pipeline accumulates, and it exists so that `main` stays code-only.

Why a separate branch (docs/DECISIONS.md, D008): the pipeline snapshots
sources every two hours. Parquet does not delta-compress in git, so on `main`
every clone of the repository would carry the entire growth of that history
forever. Here the data can be squashed on a schedule without touching code
history.

    ohlcv/{symbol}/{venue}/{year}.parquet   daily and hourly bars, per venue
    onchain/{asset}/{metric}.parquet        CoinMetrics and derived metrics
    snapshots/{table}/{yyyy-mm}.parquet     things no free API serves historically
    macro/{series}.parquet                  macro series, each row with its vintage
    source_health.parquet                   one row per fetch attempt

Bars are stored per VENUE and never merged. Binance is what MiniLizard's parity
is defined on; the longest series is what the charts use. Where they differ the
site says so.

Nothing here is hand-edited. Rebuild any of it with `uv run terminal fetch`.
