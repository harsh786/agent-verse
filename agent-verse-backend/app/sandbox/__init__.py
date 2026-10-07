"""Remote code sandbox: the runner service and the client the platform calls it with.

* :mod:`app.sandbox.runner` — the standalone HTTP runner (stdlib only, never imports
  the rest of the app) that executes untrusted code in locked-down subprocesses.
  Deployed as its own container (``Dockerfile.sandbox``; compose ``code-sandbox``;
  the Helm charts' ``code-sandbox`` Deployment).
* :mod:`app.sandbox.client` — the async client
  :class:`app.tools.code_interpreter.CodeInterpreter` uses when ``CODE_SANDBOX_URL``
  is configured.

Keep this package import-free: ``python -m app.sandbox.runner`` imports it.
"""
