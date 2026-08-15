# ui.py
"""Sirgon DiskQual operator-interface bootstrap."""

import argparse

from .keep_awake import KeepAwake
from .operator_ui import OperatorDiskQualApp
from .tui import DEFAULT_STATE, LabelScreen, ReportDriveScreen, ReportScreen


def configure_focus_defaults():
    """Put keyboard focus on the safest primary control when a screen opens."""
    OperatorDiskQualApp.AUTO_FOCUS = '#drive-table'
    ReportScreen.AUTO_FOCUS = '#projects'
    ReportDriveScreen.AUTO_FOCUS = '#report-drives'
    LabelScreen.AUTO_FOCUS = '#label-drives'


def main():
    configure_focus_defaults()

    parser = argparse.ArgumentParser(prog='sirgon-diskqual-ui')
    parser.add_argument('--state', default=str(DEFAULT_STATE), help='Path to Sirgon DiskQual state.json')
    parser.add_argument('--demo', action='store_true', help='Run with built-in sample drive data')
    args = parser.parse_args()

    # Keep-awake is intentionally temporary. DiskQual requests system and
    # desktop idle inhibitors only while the operator UI is open; closing the
    # UI releases them and leaves the user's normal power settings untouched.
    with KeepAwake():
        OperatorDiskQualApp(args.state, args.demo).run()


if __name__ == '__main__':
    main()
