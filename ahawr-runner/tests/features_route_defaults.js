// The fields the /api/board/features route adds to its answer (features_config.py serves the same JSON file),
// for the stubs of tests whose code reads that route. board_features.js keeps no copy of the defaults.
"use strict";
const fs = require("fs");
const path = require("path");

const shipped = JSON.parse(
  fs.readFileSync(path.join(__dirname, "..", "src", "ahawr_runner", "features_defaults.json"), "utf8"),
);

module.exports = {
  FEATURE_ROUTE_DEFAULTS: { feature_defaults: shipped.features, param_minimums: shipped.param_minimums },
};
