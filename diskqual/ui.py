# ui.py
"""Sirgon DiskQual operator-interface bootstrap.

This module owns application-level UI defaults and operator actions that should
be consistent across Linux qualification stations, independently of the
individual screen classes.
"""

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from textual.binding import Binding

from .tui import (
    DEFAULT_STATE,
    DiskQualApp,
    LabelScreen,
    ReportDriveScreen,
    ReportScreen,
)


INVENTORY_FILE = Path('/opt/diskqual/drives.json')


def configure_focus_defaults():
    """Put keyboard focus on the safest primary control when a screen opens."""
    DiskQualApp.AUTO_FOCUS = '#drive-table'
    ReportScreen.AUTO_FOCUS = '#projects'
    ReportDriveScreen.AUTO_FOCUS = '#report-drives'
    LabelScreen.AUTO_FOCUS = '#label-drives'


def _inventory_state(drives):
    state = {
        'version': 3,
        'batch_id': 'inventory_' + datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S'),
        'status': 'INVENTORY',
        'drives': {},
    }
    for drive in drives:
        key = str(drive.get('id') or drive.get('serial') or drive.get('dev'))
        item = dict(drive)
        item.setdefault('id', key)
        item['status'] = item.get('precheck') or item.get('health') or 'INVENTORY'
        item['stage'] = 'inventory'
        item['stage_progress'] = 1.0
        item['overall_progress'] = 0.0
        item['stage_eta_seconds'] = None
        item['message'] = 'Inventory complete'
        state['drives'][key] = item
    return state


def configure_inventory_action():
    """Add a safe inventory action to the installed operator interface."""
    if not any(getattr(binding, 'key', None) == 'i' for binding in DiskQualApp.BINDINGS):
        DiskQualApp.BINDINGS = [
            *DiskQualApp.BINDINGS,
            Binding('i', 'inventory', 'Inventory'),
        ]

    original_refresh_state = DiskQualApp.refresh_state

    def refresh_state(self):
        if getattr(self, 'inventory_mode', False):
            original_demo = self.demo
            self.demo = True
            try:
                return original_refresh_state(self)
            finally:
                self.demo = original_demo
        return original_refresh_state(self)

    def action_inventory(self):
        if self.demo:
            self.notify('Inventory is disabled in demo mode', severity='warning')
            return

        self.notify('Running Sirgon DiskQual inventory...')
        process = subprocess.run(
            ['sudo', '-n', '/usr/local/bin/diskqual', 'inventory'],
            text=True,
            capture_output=True,
        )
        if process.returncode != 0:
            detail = (process.stderr or process.stdout or '').strip()
            if 'password' in detail.lower() or 'sudo' in detail.lower():
                detail = 'Inventory authorization is not configured for this operator.'
            self.notify(detail or 'Inventory failed', severity='error', timeout=8)
            return

        try:
            drives = json.loads(INVENTORY_FILE.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            self.notify(f'Inventory completed but drives.json could not be read: {exc}', severity='error', timeout=8)
            return

        self.inventory_mode = True
        self.state = _inventory_state(drives)
        self.refresh_state()
        self.query_one('#drive-table').focus()
        self.notify(f'Inventory complete: {len(drives)} eligible drive(s) found')

    DiskQualApp.refresh_state = refresh_state
    DiskQualApp.action_inventory = action_inventory


def main():
    configure_focus_defaults()
    configure_inventory_action()

    parser = argparse.ArgumentParser(prog='sirgon-diskqual-ui')
    parser.add_argument('--state', default=str(DEFAULT_STATE), help='Path to Sirgon DiskQual state.json')
    parser.add_argument('--demo', action='store_true', help='Run with built-in sample drive data')
    args = parser.parse_args()

    DiskQualApp(args.state, args.demo).run()


if __name__ == '__main__':
    main()
