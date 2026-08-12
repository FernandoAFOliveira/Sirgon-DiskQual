# launcher.py
import os
import subprocess
import sys

from . import __version__


def _arg_value(args, name, default=None):
    try:
        index = args.index(name)
        return args[index + 1]
    except (ValueError, IndexError):
        return default


def _unit_active(*units):
    return any(subprocess.run(['systemctl', 'is-active', '--quiet', unit]).returncode == 0 for unit in units)


def _start_qualification(args):
    if '--yes' not in args:
        print('Qualification is DESTRUCTIVE. Run: diskqual qualify --yes')
        return 2

    if _unit_active('diskqual-qualify.service', 'diskqual-smart-long.service', 'diskqual-surface.service'):
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


def _start_phase_root(phase):
    if os.geteuid() != 0:
        print('Internal phase launcher must run as root.', file=sys.stderr)
        return 1
    if _unit_active('diskqual-qualify.service', 'diskqual-smart-long.service', 'diskqual-surface.service'):
        print('Another Sirgon DiskQual test job is already running.', file=sys.stderr)
        return 1
    unit = 'diskqual-smart-long' if phase == 'smart-long' else 'diskqual-surface'
    description = 'Sirgon DiskQual SMART Long phase' if phase == 'smart-long' else 'Sirgon DiskQual destructive surface phase'
    home = os.environ.get('DISKQUAL_HOME', '/opt/diskqual')
    command = [
        'systemd-run', f'--unit={unit}', '--collect', f'--description={description}',
        f'--setenv=DISKQUAL_HOME={home}', sys.executable, '-m', 'diskqual.workflow', phase,
    ]
    return subprocess.run(command).returncode


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
    if os.geteuid() == 0 and action.startswith('_'):
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
    # for the local DiskQual operator. They intentionally accept no device path
    # or serial number on the command line; selections are revalidated by the
    # privileged worker against the current inventory.
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
