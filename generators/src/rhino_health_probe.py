import json
import os
import time
from pathlib import Path


def resolve_outbox_dir():
    try:
        # Prefer centralized path conventions used by GH scripts.
        from splintcommon import splint_outbox_dir  # type: ignore
        return Path(splint_outbox_dir)
    except Exception:
        return Path('~/SplintFactoryFiles/outbox').expanduser()


def write_probe_file():
    outbox_dir = resolve_outbox_dir()
    outbox_dir.mkdir(parents=True, exist_ok=True)
    probe_path = outbox_dir / 'rhino_health_probe.json'

    payload = {
        'ok': True,
        'timestamp_unix': time.time(),
        'timestamp_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'pid': os.getpid(),
    }

    with open(probe_path, 'w', encoding='utf-8') as probe_file:
        json.dump(payload, probe_file)

    print('rhino_health_probe_written')


if __name__ == '__main__':
    write_probe_file()
