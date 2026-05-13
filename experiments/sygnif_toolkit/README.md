# SYGNIF Toolkit

This directory contains the SYGNIF toolkit, which includes:
1.  **Phase 1:** A Bitcoin price simulator with a Golden Cross trading strategy and PnL decomposition harness.
2.  **Phase 2:** A cross-venue lead-lag signal generator and backtesting framework.

## Installation

This project is managed by `poetry`. Run `poetry install` to install the dependencies.

## Usage

See the documentation in each phase for more details on usage.

## Backtest Docker Image

To ensure consistent environments across all CI backtest jobs, we maintain a minimal base Docker image containing explicitly pinned dependencies.

**Included Packages:**
- `pandas==2.2.*`
- `numpy==1.26.*`
- `requests==2.32.*`
- `ccxt==4.4.*`
- `pytest==8.3.*`

**Pulling Locally:**
You can pull the latest image locally with the following command:
```bash
docker pull registry.gitlab.com/giansn1/sygnif/backtest-deps:latest
```
