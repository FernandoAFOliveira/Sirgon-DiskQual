# launcher.py
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

BASE = Path(os.environ.get('DISKQUAL_HOME', '/opt/diskqual'))
JOBS = BASE / 'jobs'
OPERATOR_SELECTION = BASE / 'operator' / 'selection.json'


def _arg_value(args, name, default=None):
    try:
        index = args.index(name)
        return args[index + 1]
    except (ValueError, IndexError):
        return default


def _dynamic_worker_active():
    result = subprocess.run(
        ['systemctl', 'list-units', '--type=service', '--state=running', '--no-legend', 'diskqual-*.service'],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return bool(result.stdout.strip())


def _start_qualification(args):
    if '--yes' not in args:
        print('Qualification is DESTRUCTIVE. Run: diskqual qualify --yes')
        return 2

    if _dynamic_worker_active():
        print('A Sirgon DiskQual test job is already running.')
        print('Use: diskqual status')
        return 1

    poll = _arg_value(args, '--poll', '10')
    home = os.environ.get('DISKQUAL_HOME', '/opt/diskqual')
    worker_args = ['--yes', '--poll', str(poll)]
    if '--allow-existing-data' in args:
        worker_args.append('--allow-existing-data')

    command = [
        'sudo', 'systemd-run',
        '--unit=diskqual-qualify',
        '--collect',
        '--description=Sirgon DiskQual qualification batch',
        f'--setenv=DISKQUAL_HOME={home}',
        sys.executable, '-m', 'diskqual.engine', *worker_args,
    ]
    print('Starting persistent Sirgon DiskQual qualification service...')
    if '--allow-existing-data' in args:
        print('WARNING: protected disks with existing partitions/filesystems are explicitly allowed for destructive testing.')
    return subprocess.run(command).returncode


def _run_inventory():
    if os.geteuid() == 0:
        from .cli import main as cli_main
        cli_main()
        return 0
    return subprocess.run(['sudo', '/usr/local/bin/diskqual', 'inventory']).returncode


def _job_id(phase):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return f'{phase}-{stamp}-{os.getpid()}'


def _snapshot_selection(job_id):
    if not OPERATOR_SELECTION.exists():
        raise RuntimeError('No operator drive selection exists.')
    try:
        data = json.loads(OPERATOR_SELECTION.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'Could not read operator drive selection: {exc}') from exc
    serials = data.get('serials', []) if isinstance(data, dict) else []
    if not serials:
        raise RuntimeError('No drives are selected.')
    JOBS.mkdir(parents=True, exist_ok=True)
    path = JOBS / f'{job_id}.selection.json'
    path.write_text(json.dumps({'serials': [str(serial) for serial in serials]}, indent=2))
    os.chmod(path, 0o600)
    return path


def _start_phase_root(phase):
    if os.geteuid() != 0:
        print('Internal phase launcher must run as root.', file=sys.stderr)
        return 1

    job_id = _job_id(phase)
    try:
        selection_path = _snapshot_selection(job_id)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    state_path = JOBS / f'{job_id}.json'
    unit = f'diskqual-{job_id}'
    description = 'Sirgon DiskQual SMART Long job' if phase == 'smart-long' else 'Sirgon DiskQual destructive surface job'
    home = os.environ.get('DISKQUAL_HOME', '/opt/diskqual')
    command = [
        'systemd-run', f'--unit={unit}', '--collect', f'--description={description}',
        f'--setenv=DISKQUAL_HOME={home}', f'--setenv=DISKQUAL_STATE={state_path}',
        sys.executable, '-m', 'diskqual.workflow', phase,
        '--selection-path', str(selection_path), '--state-path', str(state_path), '--job-id', job_id,
    ]
    result = subprocess.run(command)
    if result.returncode:
        selection_path.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)
        return result.returncode
    print(f'Started {phase} job: {job_id}')
    return 0


def _run_operator_phase(phase, destructive_confirmed=False):
    launcher = '/usr/local/bin/diskqual'
    if phase == 'surface' and not destructive_confirmed:
        print('Surface qualification is DESTRUCTIVE. Run only after explicit confirmation.', file=sys.stderr)
        return 2
    internal = '_smart-long-root' if phase == 'smart-long' else '_surface-root'
    command = ['sudo', launcher, internal]
    if phase == 'surface':
        command.append('--yes')
    return subprocess.run(command).returncode


def _run_locate(action):
    if action not in ('on', 'off', 'check'):
        print('Locate action must be on, off, or check.', file=sys.stderr)
        return 2
    return subprocess.run(['sudo', '/usr/local/bin/diskqual', '_locate-root', action]).returncode


def main():
    args = sys.argv[1:]
    if args in (['--version'], ['-V']):
        print(f'Sirgon DiskQual {__version__}')
        return

    if args == ['status']:
        from .status import main as status_main
        status_main()
        return

    if args == ['inventory']:
        raise SystemExit(_run_inventory())

    if args == ['smart-long-selected']:
        raise SystemExit(_run_operator_phase('smart-long'))

    if args == ['surface-selected', '--yes']:
        raise SystemExit(_run_operator_phase('surface', destructive_confirmed=True))

    if len(args) == 2 and args[0] == 'locate-selected' and args[1] in ('on', 'off', 'check'):
        raise SystemExit(_run_locate(args[1]))

    # Root-only fixed commands used by the tightly scoped sudo policy installed
    # for the local DiskQual operator. Device paths are never accepted here;
    # selections are snapshotted and then revalidated by the privileged worker.
    if args == ['_smart-long-root']:
        raise SystemExit(_start_phase_root('smart-long'))

    if args == ['_surface-root', '--yes']:
        raise SystemExit(_start_phase_root('surface'))

    if len(args) == 2 and args[0] == '_locate-root' and args[1] in ('on', 'off', 'check'):
        if os.geteuid() != 0:
            raise SystemExit('Locate helper must run as root.')
        from .locate import main as locate_main
        sys.argv = ['diskqual locate', args[1]]
        locate_main()
        return

    if args and args[0] == 'qualify':
        raise SystemExit(_start_qualification(args[1:]))

    from .cli import main as cli_main
    cli_main()


if __name__ == '__main__':
    main()
