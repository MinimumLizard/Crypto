"""One module per source. Every fetcher writes a source_health row and never raises.

The contract, from SPEC §2.8: a fetcher returns what it got and records what
happened. It does not decide what a gap means and it does not abort the build.
Callers read the store afterwards and render staleness from there.
"""
