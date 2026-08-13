# ui_beta13.py
"""Sirgon DiskQual Beta 13 operator-interface bootstrap."""

import argparse

from .operator_ui_beta13 import OperatorDiskQualApp
from .tui import DEFAULT_STATE, LabelScreen, ReportDriveScreen, ReportScreen


def configure_focus_defaults():
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
    OperatorDiskQualApp(args.state, args.demo).run()


if __name__ == '__main__':
    main()
