Vendored front-end libraries (served from static/, never from a CDN at runtime).

lightweight-charts-4.1.3.standalone.production.js
  TradingView Lightweight Charts(TM) v4.1.3, Copyright (c) 2023-2024 TradingView, Inc.
  Licence: Apache License 2.0 (lightweight-charts-LICENSE.txt, copied unmodified from the package).
  Source: the npm registry tarball https://registry.npmjs.org/lightweight-charts/-/lightweight-charts-4.1.3.tgz
    tarball sha1 467fb88a5efc3665bea64c193591cfc082d84300 (the registry's published shasum)
    file    dist/lightweight-charts.standalone.production.js, unmodified
    sha256  0c146b9bfbc6e5f19da1a0de876dae43023c332d5157d82f7575f9b7653ee519 (pinned by tests/guards_auto_crypto.py)
  Why vendored: the crypto tab's chart needs zoom, pan, a crosshair, candles, lines, a volume histogram,
  markers and seconds-resolution time; hand-written SVG had none of the first three. Loaded only when the
  Crypto tab opens (static/crypto.js inserts the script), so no other page pays its 160 KB.
  Attribution: the project's README asks for its NOTICE ("TradingView Lightweight Charts(TM) Copyright (c)
  2023 TradingView, Inc. https://www.tradingview.com/") and a link to https://www.tradingview.com/ on the
  page that uses it -- the Crypto tab prints both under the chart.
