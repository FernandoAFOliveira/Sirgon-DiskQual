# ui.py
"""Sirgon DiskQual operator-interface bootstrap.

This module owns application-level UI defaults that should be consistent across
Linux qualification stations, independently of the individual screen classes.
"""

import argparse

from .tui import (
    DEFAULT_STATE,
    DiskQualApp,
    LabelScreen,
    ReportDriveScreen,
    ReportScreen,
)


def configure_focus_defaults():
    """Put keyboard focus on the safest primary control when a screen opens."""
    DiskQualApp.AUTO_FOCUS = '#drive-table'
    ReportScreen.AUTO_FOCUS = '#projects'
    ReportDriveScreen.AUTO_FOCUS = '#report-drives'
    LabelScreen.AUTO_FOCUS = '#label-drives'


def main():
    configure_focus_defaults()

    parser = argparse.ArgumentParser(prog='sirgon-diskqual-ui')
    parser.add_argument('--state', default=str(DEFAULT_STATE), help='Path to Sirgon DiskQual state.json')
    parser.add_argument('--demo', action='store_true', help='Run with built-in sample drive data')
    args = parser.parse_args()

    DiskQualApp(args.state, args.demo).run()


if __name__ == '__main__':
    main()
