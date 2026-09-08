# FPL Model — FPL transfer optimiser
# The three commands that matter:
#   make update   — refresh all data from the FPL API (and historical data on first run)
#   make solve    — run the xP engine + solver + robustness check
#   make report   — write report.html
# `make all` does all three in order.

.PHONY: update solve report all test backtest

update:
	uv run python -m mudchute update

solve:
	uv run python -m mudchute solve

report:
	uv run python -m mudchute report

all: update solve report

test:
	uv run pytest -q

backtest:
	uv run python -m mudchute backtest
