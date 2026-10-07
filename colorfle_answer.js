// Exact Colorfle answer via the real seedrandom package (same as frontend).
// Usage: node colorfle_answer.js YYYY-MM-DD [mode]
const path = require('path');
const fs = require('fs');

// `seedrandom` must resolve, and the frontend checkout has no node_modules
// installed by default, so try every sensible location in order:
//   1. an explicit SEEDRANDOM_PATH override,
//   2. a copy installed next to this script (npm install seedrandom),
//   3. the frontend's own node_modules,
//   4. a bare require() so Node's normal resolution still gets a chance.
// Failing loudly here beats silently returning a WRONG daily answer, which is
// exactly what the old hard-coded path caused.
function loadSeedrandom() {
  const tried = [];
  const explicit = process.env.SEEDRANDOM_PATH;
  if (explicit && fs.existsSync(explicit)) return require(explicit);
  const local = path.join(__dirname, 'node_modules', 'seedrandom');
  if (fs.existsSync(local)) return require(local);
  const root = path.resolve(__dirname, '..', '..');
  for (const fe of [process.env.WORDJI_PATH, process.env.ZAI_PATH,
                    path.join(root, 'wordsJi'),
                    path.join(root, 'wordsolverx-z-ai')].filter(Boolean)) {
    const p = path.join(fe, 'node_modules', 'seedrandom');
    tried.push(p);
    if (fs.existsSync(p)) return require(p);
  }
  try {
    return require('seedrandom');
  } catch (e) {
    throw new Error(
      'seedrandom not found. Run `npm install seedrandom` in '
      + __dirname + ', or set SEEDRANDOM_PATH. Also tried: '
      + (tried.join(', ') || '(none)'));
  }
}
const seedrandom = loadSeedrandom();

const COLORS = [
  '#FFFFFF', '#FFFAC8', '#FABEBE', '#AAFFC3', '#E6BEFF',
  '#46F0F0', '#FFE119', '#BCF60C', '#F58231', '#3CB44B',
  '#F032E6', '#808000', '#008080', '#9A6324', '#E6194B',
  '#4363D8', '#911EB4', '#800000', '#000075', '#000000'
];
const COLOR_NAMES = [
  'White', 'Light Yellow', 'Pink', 'Light Green', 'Lavender',
  'Cyan', 'Yellow', 'Lime', 'Orange', 'Green',
  'Magenta', 'Olive', 'Teal', 'Brown', 'Red',
  'Blue', 'Purple', 'Maroon', 'Navy', 'Black'
];
const NUM_BLOCKS = [3, 4];
const LAUNCH = Date.UTC(2022, 3, 25, 12, 0, 0);

function seedStr(dateKey, mode) {
  const [y, m, d] = dateKey.split('-').map(Number);
  // reset = next 15:00 UTC (midnight JST); seed uses reset's Y/M/D
  let reset = Date.UTC(y, m - 1, d, 15);
  const dayMs = Date.UTC(y, m - 1, d);
  if (dayMs >= reset) reset += 86400000;
  const r = new Date(reset);
  return `${mode} ${r.getUTCDate()} ${r.getUTCMonth()} ${r.getUTCFullYear()}`;
}

const dateKey = process.argv[2];
const mode = parseInt(process.argv[3] || '0', 10);
const [y, m, d] = dateKey.split('-').map(Number);
const puzzleNumber = Math.floor((Date.UTC(y, m - 1, d) - LAUNCH) / 86400000);
const rng = seedrandom(seedStr(dateKey, mode));
const n = NUM_BLOCKS[mode];
const pool = Array.from({ length: 20 }, (_, i) => i);
const chosen = [];
for (let i = 0; i < n; i++) {
  const k = Math.floor(rng() * pool.length);
  chosen.push(pool[k]);
  pool.splice(k, 1);
}
console.log(JSON.stringify({
  date: dateKey, mode, puzzleNumber,
  colors: chosen,
  colorNames: chosen.map((i) => COLOR_NAMES[i]),
  colorHexes: chosen.map((i) => COLORS[i]),
}));
