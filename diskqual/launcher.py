# launcher.py
import os
import subprocess
import sys
from pathlib import Path

from . import __version__


def _arg_value(args, name, default=None):
    try:
        index = args.index(name)
        return args[index + 1]
    except (ValueError, IndexError):
        return default


def _start_qualification(args):
    if '--yes' not in args:
        print('Qualification is DESTRUCTIVE. Run: diskqual qualify --yes')
        return 2

    if subprocess.run(['systemctl', 'is-active', '--quiet', 'diskqual-qualify.service']).returncode == 0:
        print('A Sirgon DiskQual qualification job is already running.')
        print('Use: diskqual monitor')
        print('Or:  systemctl status diskqual-qualify.service --no-pager')
        return 1

    poll = _arg_value(args, '--poll', '10')
    home = os.environ.get('DISKQUAL_HOME', '/opt/diskqual')
    command = [
        'sudo', 'systemd-run',
        '--unit=diskqual-qualify',
        '--collect',
        '--description=Sirgon DiskQual qualification batch',
        f'--setenv=DISKQUAL_HOME={home}',
        sys.executable, '-m', 'diskqual.engine', '--yes', '--poll', str(poll),
    ]
    print('Starting persistent Sirgon DiskQual qualification service...')
    return subprocess.run(command).returncode


def main():
    args = sys.argv[1:]
    if args in (['--version'], ['-V']):
        print(f'Sirgon DiskQual {__version__}')
        return

    if args and args[0] == 'qualify':
        raise SystemExit(_start_qualification(args[1:]))

    from .cli import main as cli_main
    cli_main()


if __name__ == '__main__':
    main()
