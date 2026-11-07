// Builds web/logo.svg: the orchestrator's DiceBear Gaze avatar (CC0) in a square frame. Run: node web/vendor/build-logo.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
const here = dirname(fileURLToPath(import.meta.url));
globalThis.window = globalThis;
new Function(readFileSync(join(here, "dicebear-gaze.js"), "utf8"))();
const inner = window.uatGaze("orchestrator").replace(/<metadata[\s\S]*?<\/metadata>/, "").replace(/^<svg[^>]*?viewBox="([^"]+)"[^>]*>/, '<svg x="6" y="6" width="52" height="52" viewBox="$1">');
const c = "#6b7280";
writeFileSync(join(here, "..", "logo.svg"), `<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64"><title>Tusk</title><defs><clipPath id="c"><rect x="6" y="6" width="52" height="52"/></clipPath></defs><rect x="2" y="2" width="60" height="60" fill="#e5e7eb"/><rect x="3.5" y="3.5" width="57" height="57" fill="none" stroke="${c}" stroke-width="3"/><g clip-path="url(#c)">${inner}</g></svg>\n`);
