# JAV StreamIO — Debrid Resolver (Vercel)

Dynamic Stremio addon + debrid resolver. Serves the Stremio addon protocol
(`/manifest.json`, `/catalog/...`, `/meta/...`) by proxying from the `borepig/javstreamio`
GitHub raw, and makes `/stream/...` **dynamic**: it resolves a torrent `infoHash` into a
direct download URL through the user's chosen debrid service.

## What it does

- **Frontend** (`/`): pick a debrid service (Torbox, Real-Debrid, AllDebrid, Premiumize,
  Debrid-Link) and paste **your own** API key (browser **localStorage** only — no key is
  shipped with the site). Copy the addon URL into Stremio.
- **Dynamic addon `/stream`**: when Stremio requests `/stream/movie/<id>.json?service=&token=`,
  the route fetches the static stream (for `infoHash`), looks up the magnet from the scraped
  JSONL, and resolves it through the debrid. Without `service`/`token` it returns the
  `infoHash` + magnet so Stremio's built-in BitTorrent engine can play.

## Run locally

```
npm install
npm run dev        # http://localhost:3000
```

## Deploy to Vercel

```
npx vercel          # link + deploy (or `vercel --prod`)
```

Point Stremio at the deployed URL as a new addon. To use your debrid, add the addon with
`?service=<service>&token=<key>` in the base URL so the dynamic `/stream` resolves to a
direct URL. Otherwise the addon falls back to the magnet link (built-in engine).

## Notes

- Torbox, Real-Debrid, AllDebrid, Premiumize, and Debrid-Link all add the magnet then
  `/play` 302s to a CDN file URL. Torrentio/Pretorx were not debrid APIs and were removed.
- The debrid key never leaves your browser (localStorage) and is sent per-request to the
  serverless function; it is not stored server-side.
- Static addon data is sourced from `borepig/javstreamio` (GitHub raw), so rebuilding/committing
  the addon (v0.3.0) updates what the app serves.
