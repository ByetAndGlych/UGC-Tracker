# UGC-Tracker

A standalone Roblox Limited UGC price-tracking and deal-detection project.

## Current milestone: live visual catalog gallery

The project now includes a phone-friendly web gallery that searches Roblox collectible items and loads their real thumbnails through Roblox's public Thumbnails API.

- Search by item name.
- Choose 10, 30, 60, or 120 results.
- View item thumbnails, names, creators, and available price fields.
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

- The gallery displays catalog price fields when Roblox supplies them. It does not yet calculate RAP, verified resale comparisons, or price history.
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
