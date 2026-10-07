// Pick today's Worldle country with the SAME seedrandom the frontend uses.
// Usage:  node worldle_answer.js <worldleNumber> <countryCount> -> prints index
const seedrandom = require("seedrandom");

const n = parseInt(process.argv[2], 10);
const len = parseInt(process.argv[3], 10);
if (!Number.isFinite(n) || !Number.isFinite(len) || len < 1) {
  console.error("usage: node worldle_answer.js <number> <count>");
  process.exit(2);
}
const rng = seedrandom(String(n));
process.stdout.write(String(Math.floor(rng() * len)));
