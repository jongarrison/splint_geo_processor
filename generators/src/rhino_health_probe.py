import json
import os
import time


def resolve_outbox_dir():
    try:
        # Prefer centralized path conventions used by GH scripts.
        from splintcommon import splint_outbox_dir  # type: ignore
        return str(splint_outbox_dir)
    except Exception:
        return os.path.expanduser('~/SplintFactoryFiles/outbox')


def ensure_directory(path):
    if os.path.isdir(path):
        return
    try:
        os.makedirs(path)
    except OSError:
        if not os.path.isdir(path):
            raise


def write_probe_file():
    try:
        outbox_dir = resolve_outbox_dir()
        ensure_directory(outbox_dir)
        probe_path = os.path.join(outbox_dir, 'rhino_health_probe.json')

        payload = {
            'ok': True,
            'timestamp_unix': time.time(),
            'timestamp_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'pid': os.getpid(),
        }

        with open(probe_path, 'w') as probe_file:
            json.dump(payload, probe_file)

        print('rhino_health_probe_written')
    except Exception as err:
        # Never let probe exceptions bubble to Rhino UI modal dialogs.
        print('rhino_health_probe_error: {0}'.format(err))


if __name__ == '__main__':
    write_probe_file()
