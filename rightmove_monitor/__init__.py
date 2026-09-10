"""Longitudinal monitoring of the Edinburgh housing market.

Modules
-------
rightmove : scrape a complete snapshot of current for-sale listings
ukhpi     : download the UK House Price Index series for City of Edinburgh
snapshot  : orchestrate a snapshot run and persist it
history   : maintain a listing-level history (price changes, time on market)
analyse   : build tidy time series from accumulated snapshots
forecast  : baseline house-price forecast from the UK HPI series
"""

__version__ = "0.1.0"
