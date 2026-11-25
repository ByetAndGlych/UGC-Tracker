# UGC-Tracker

A standalone Roblox Limited UGC price-tracking and deal-detection project.

## Project goals

- Search Roblox Marketplace catalog items.
- Identify Limited and collectible UGC items.
- Collect public item details, creator information, and thumbnails.
- Retrieve current resale listings and RAP where public endpoints allow.
- Build price history and calculate 7-day and 30-day averages.
- Compare current listings with sales activity to identify potentially underpriced items.

## Development approach

The first milestone is read-only access to public Roblox Marketplace data. No Roblox password, session cookie, or other account secret should be required or stored.

Historical tracking, deal scoring, a dashboard, and scheduled monitoring will be added after the public API retrieval layer is tested.

## Status

Initial project scaffold. API endpoint behavior and data availability still need to be verified.
