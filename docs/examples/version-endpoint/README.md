# Factory example: service version endpoint

The real NUC OpenHands worker produced `app.py` and `test_version.py` through Restate and the existing Bifrost complexity router. GET `/version` returns HTTP200 JSON `{"service":"factory-example","version":"1.0.0"}`; unknown routes return404. The existing login/logout/session/rate-limit/expiry implementation is byte-for-byte unchanged after removing the two added routing lines.

Worker commit: `d8bf750a3c8c0b03c3481b9d8c07b508fc3fc3f3`.
Original baseline: `9fca87029b3109229c8d81a1b8ee9cb8dc8a074c`.

This is a disposable test service with fixture credentials, not a production authentication application. Run its stdlib regression tests with `python3 -m unittest -v` in this directory. Full independent validation and durable acceptance evidence is in `docs/verification/version-example-2026-10-28/`.
