"""Resume stage 4 (v1.2) after a provider thread stopped on transport failures. Execution amendment 1.2.1.

Why: the frozen executor allows 3 attempts per request (max_retries=2). Kimi stopped after three
ConnectionErrors on one request (no HTTP response was received), and the executor refuses a 4th
attempt, so the same `live` command cannot finish the frozen matrix.

What changes (engineering only, decided before any choice outcome was inspected):
  * transport retry limit 2 -> 6 (at most 7 attempts per request; earlier failed attempts count);
  * wait 30 s instead of 3 s after a ConnectionError/Timeout before retrying.
What does not change: plan, prompts, payloads, models, parameters, analysis, caps, the recorded
failed attempts, and the per-provider order. stage3_executor.py and stage4_mechanism_v12.py are
imported unchanged (their hashes are still checked by load_plan and by the resume identity).

  .\\.venv\\Scripts\\python.exe scripts\\stage4_resume_v12.py --kimi-interval 1
"""
import argparse, math, sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import stage4_mechanism_v12 as s4  # noqa: E402  (unchanged frozen module)

old = s4.old
MAX_RETRIES = 6
TRANSPORT_WAIT_S = 30.0
_configurations = old.configurations
_Transport = old.HttpTransport


def configurations(manifest, kimi_interval):
    cfg, intervals = _configurations(manifest, kimi_interval)
    for c in cfg.values():
        c['transport'] = dict(c['transport'], max_retries=MAX_RETRIES)
    return cfg, intervals


class PatientTransport(_Transport):
    def __call__(self, request, config):
        try:
            return super().__call__(request, config)
        except (requests.Timeout, requests.ConnectionError) as exc:
            return {'status': 'transport_failure', 'retryable': True, 'error_type': type(exc).__name__,
                    'possible_remote_completion': True, 'metadata': {}, 'retry_after': TRANSPORT_WAIT_S,
                    'amendment': 'stage4-1.2.1'}


def install():
    old.configurations = configurations
    old.HttpTransport = PatientTransport


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--kimi-interval', type=float, default=21)
    a = ap.parse_args()
    if not math.isfinite(a.kimi_interval) or a.kimi_interval < 0:
        raise ValueError('Invalid interval')
    batch = ROOT / 'runs/stage4-live-20261006-v12/batch'
    if not (batch / 'manifest.json').exists():
        raise SystemExit('No live batch to resume; use stage4_mechanism_v12.py live for a first run.')
    install()
    print(f'Execution amendment 1.2.1: retry limit {MAX_RETRIES}, {TRANSPORT_WAIT_S:.0f} s wait after a connection error. '
          'Received responses are not re-sent.')
    s4.run('live', a.kimi_interval)


if __name__ == '__main__':
    main()
