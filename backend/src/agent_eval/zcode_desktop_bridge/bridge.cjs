#!/usr/bin/env node
import("./bridge.mjs").catch((error) => {
  process.stderr.write(`${error?.stack ?? error}\n`);
  process.exitCode = 1;
});
