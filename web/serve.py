"""Production entry point: waitress behind Caddy.

Bound to loopback only -- a reverse proxy terminates TLS on the public host
and proxies in. Nothing here should ever answer the LAN or the internet
directly.
"""
import argparse
import os

from waitress import serve

import app as dashboard


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8768)
    ap.add_argument("--threads", type=int, default=6)
    a = ap.parse_args()

    dashboard.app.config["LIVE_RELOAD"] = False      # no dev reload stream in prod
    dashboard.app.config["TEMPLATES_AUTO_RELOAD"] = False
    prefix = os.environ.get("LUMA_URL_PREFIX", "")
    print(f" * serving on http://{a.host}:{a.port}  prefix={prefix or '(root)'}", flush=True)
    serve(dashboard.app, host=a.host, port=a.port, threads=a.threads,
          ident="luma-bowling")


if __name__ == "__main__":
    main()
