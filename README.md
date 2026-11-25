# UGC-Tracker

A standalone Roblox Limited UGC price-tracking and deal-detection project.

## Current milestone: continuous catalog scanner and live gallery

The project now includes a phone-friendly web gallery that searches Roblox collectible items and loads their real thumbnails through Roblox's public Thumbnails API.

- Continuously scans Roblox's public collectible catalog in 120-item pages, without requiring a search keyword.
- Scans continuously with no scheduled sleep window; pagination progress and collected items are stored in SQLite while the service filesystem persists.
- Retrieves RAP in parallel for each scanned page and ranks cached items by best deal versus RAP.
- Search by item name.
- Choose 10, 30, 60, or 120 results.
- View compact item cards with thumbnail, name, lowest available price, percent below RAP, and a direct Roblox link.
- Tap an item thumbnail or name to open detailed public information, including RAP, creator price, resale listings, sales/stock figures, and available price history.
- Missing public data is displayed as NA rather than estimated.
- Uses public read-only endpoints; no Roblox password or session cookie is needed.
- The gallery is a small Python web server and does not require a paid framework.

## Run locally

```bash
python -m pip install -r requirements.txt
python web_app.py
```

Open http://127.0.0.1:8000 in a browser on the same computer.

To test the live catalog and thumbnail endpoints:

```bash
python web_app.py --check
python -m unittest -v
```

## Current limitations

- Roblox's public RAP endpoint can return no RAP for newer CollectibleItem-system limiteds; those items display NA.
- The scanner has no scheduled sleep window, but Render's free web service can still spin down when idle. Truly uninterrupted scanning requires an always-on hosting plan; durable progress across restarts also requires persistent storage.
- Public resale history and reseller listings may be unavailable for some items or temporarily rate-limited.
- Some catalog items may not have a thumbnail or public creator field.
- To open the gallery from a phone when the server is running elsewhere, it must be deployed to a public host. Hosting/deployment is a separate next step.

## Next milestones

1. Validate live thumbnail retrieval in GitHub Actions.
2. Verify public resale and RAP endpoints.
3. Store timestamped observations in SQLite.
4. Calculate 7-day and 30-day averages and sales/listing metrics where public data permits.
5. Add deal scoring based on price history and sales volume.
6. Deploy the gallery so it can be opened from a phone and run continuously.

## Roblox API references

- Catalog search: https://catalog.roblox.com/v2/search/items/details
- Asset thumbnails: https://thumbnails.roblox.com/v1/assets
- Roblox Creator Hub: https://create.roblox.com/docs/projects/assets/api
