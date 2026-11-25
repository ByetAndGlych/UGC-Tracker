# UGC-Tracker

A standalone Roblox Limited UGC price-tracking and deal-detection project.

## Current milestone: public catalog search

The first working component is a read-only client for Roblox's avatar Marketplace catalog search endpoint.

- Searches collectible catalog items.
- Supports keyword search and cursor-based pagination.
- Handles HTTP 429 and temporary 5xx responses with bounded retries.
- Normalizes useful item fields while preserving the original API response.
- Uses public catalog data only; it does not ask for or store a Roblox password or session cookie.

## Run locally

```bash
python -m pip install -r requirements.txt
python app.py
python app.py --keyword "limited" --limit 10 --pages 2
```

The command prints the catalog response as JSON. Roblox may change experimental API behavior, and fields can be absent for some items.

## Next milestones

1. Verify collectible item search against live responses.
2. Validate the current public item-details and resale/RAP endpoints.
3. Store timestamped observations in SQLite.
4. Calculate 7-day and 30-day averages and sales/listing metrics where data is publicly available.
5. Add a deal score that weighs listing price, RAP, averages, and sales volume rather than treating RAP as true market value.
6. Add a dashboard and scheduled polling after the data layer is reliable.

## API reference

Roblox documents Marketplace search at:
https://create.roblox.com/docs/projects/assets/api

The catalog search endpoint used here is:
https://catalog.roblox.com/v1/search/items/details

Resale and RAP retrieval are intentionally not represented as complete yet. Those endpoints will be added only after their current response formats and access requirements are tested.
