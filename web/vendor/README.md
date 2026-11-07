# Vendored front-end assets

The dashboard loads nothing from the internet. These files are built once and served from `/vendor/`.

| File | What | Source | License |
|---|---|---|---|
| `dicebear-gaze.js` | Agent avatars: DiceBear core with the "Gaze" style, exposes `window.uatGaze(seed)` | `@dicebear/core@10.7.0`, `@dicebear/styles@10.6.0` (https://www.dicebear.com/styles/gaze/) | core MIT (`LICENSE-dicebear-core.txt`); the Gaze style is CC0 1.0 by DiceBear |
| `iconoir.js` | The icons the dashboard uses, exposes `window.UAT_ICONS` | `iconoir@7.12.2` (https://iconoir.com) | MIT (`LICENSE-iconoir.txt`) |

Rebuild: `npm i @dicebear/core@10.7.0 @dicebear/styles@10.6.0 iconoir@7.12.2 esbuild`, then
`npx esbuild build-avatar-entry.js --bundle --minify --format=iife --platform=browser --loader:.json=json --outfile=dicebear-gaze.js` and `node build-icons.mjs` (edit the name map inside to change icons).
