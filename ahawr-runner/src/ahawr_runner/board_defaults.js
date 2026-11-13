(function () {
// GENERATED from settings_defaults.json by `python -m ahawr_runner.settings_defaults`.
// Do not edit: change the JSON and run the command again.
const DEFAULTS = {
  "runner": {
    "port": 8700,
    "url": "http://ahawr-runner:8700",
    "loopback_url": "http://127.0.0.1:8700"
  },
  "engine": {
    "max_attempts_per_task": 5,
    "retry_delays": [
      15,
      30,
      60,
      120,
      300
    ],
    "run_manager_workflow_id": "nhjwX1G7FiVTO2Ah",
    "telegram_chat_id": ""
  },
  "retrieval": {
    "enabled": false,
    "url": "http://ahawr-retrieval:8500",
    "corpora": [],
    "timeout_ms": 120000,
    "worker_max_tokens": 6000,
    "reviewer_max_tokens": 5000,
    "label": ""
  }
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = DEFAULTS;
} else if (typeof document !== "undefined") {
  globalThis.BoardDefaults = DEFAULTS;
}
})();
