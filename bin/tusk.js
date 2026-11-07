#!/usr/bin/env node
// Thin launcher so `npm i -g @dexterhere/tusk` gives a `tusk` command. All logic is in ./tusk (Python, standard library only).
const { spawnSync } = require("node:child_process");
const path = require("node:path");
for (const py of ["python3", "python"]) {
  const r = spawnSync(py, [path.join(__dirname, "tusk"), ...process.argv.slice(2)], { stdio: "inherit" });
  if (!r.error) process.exit(r.status ?? 1);
}
console.error("Tusk needs python3 (3.10+). Install it, then run this again.");
process.exit(1);
