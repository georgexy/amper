#!/usr/bin/env bash
# Împachetează scena 3D a temei „Casă” (scene.js + three.js) într-un singur modul: web/casa3d.js.
# Se rulează după orice schimbare în scene.js:  bash tools/casa3d/build.sh
set -euo pipefail
cd "$(dirname "$0")"
[ -d node_modules ] || npm install --no-audit --no-fund --silent
npx esbuild scene.js --bundle --format=esm --minify --target=es2020 --legal-comments=eof \
  --outfile=../../web/casa3d.js
ls -l ../../web/casa3d.js
