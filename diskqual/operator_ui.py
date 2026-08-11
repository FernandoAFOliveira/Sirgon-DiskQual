# operator_ui.py
import json
import subprocess
import threading
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Static

from .progress import format_duration, load_state, overall_for_drive, weighted_batch_progress
from .status import runtime_health
from .tui import DiskQualApp, _bar, _status_markup


class OperatorHelpScreen(ModalScreen):
    BINDINGS = [Binding('escape', 'dismiss', 'Return'), Binding('backspace', 'dismiss', 'Return')]

    def compose(self) -> ComposeResult:
        text = (
            '[bold cyan]SIRGON DISKQUAL — HELP[/]\n\n'
            'I            Inventory / scan for eligible test drives\n'
            '↑ / ↓        Select a drive\n'
            'ENTER        Open drive details\n'
            'R            Client reports\n'
            'L            Labels\n'
            'H            Help\n'
            'Q / Ctrl+C   Exit Sirgon DiskQual display — active tests continue\n\n'
            'Ctrl+Alt+F1  Switch to Linux OS — tests continue\n'
            'Ctrl+Alt+F2  Return to Sirgon DiskQual screen\n\n'
            '[bold]ESC or BACKSPACE — Return[/]'
        )
        with Container(id='dialog'):
            yield Static(text)

    def action_dismiss(self):
        self.dismiss()


class OperatorDiskQualApp(DiskQualApp):
    """Installed operator interface with interactive inventory support."""

    BINDINGS = [
        Binding('i', 'inventory', 'Inventory'),
        Binding('h', 'help', 'Help'),
        Binding('r', 'reports', 'Client Reports'),
        Binding('l', 'labels', 'Labels'),
        Binding('ctrl+r', 'refresh_now', 'Refresh'),
        Binding('q', 'quit_display', 'Exit Display'),
        Binding('ctrl+c', 'quit_display', 'Exit Display', show=False),
    ]

    def __init__(self, state_path, demo=False):
        super().__init__(state_path, demo)
        self.inventory_running = False
        self.inventory_message = ''
        self.inventory_drives_path = Path('/opt/diskqual/drives.json')

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static('SIRGON DISKQUAL  •  Disk Qualification Station', id='title')
        yield Static(id='summary')
        yield DataTable(id='drive-table')
        yield Static(
            'I Inventory   ↑/↓ Select Drive   ENTER Drive Details   R Client Reports   L Labels   H Help   Q Exit Display',
            id='hint',
        )
        yield Static('Ctrl+Alt+F1  Switch to Linux OS — Tests Continue', id='hint2')
        yield Footer()

    def action_help(self):
        self.push_screen(OperatorHelpScreen())

    def _inventory_state(self, drives):
        return {
            'version': 3,
            'batch_id': 'inventory',
            'status': 'INVENTORY',
            'drives': {str(d.get('id') or d.get('serial')): d for d in drives},
        }

    def _load_inventory_file(self):
        if not self.inventory_drives_path.exists():
            return None
        try:
            data = json.loads(self.inventory_drives_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, list) else None

    def refresh_state(self):
        if self.inventory_running:
            self.query_one('#summary', Static).update(
                '[bold cyan]SCANNING FOR DISKS...[/]\n'
                'Checking attached storage and SMART information.\n'
                'OS-mounted and in-use disks are automatically excluded.'
            )
            return

        if not self.demo:
            state = load_state(self.state_path)
            if state:
                self.state = state
            elif self.state is None:
                inventory = self._load_inventory_file()
                if inventory is not None:
                    self.state = self._inventory_state(inventory)

        if not self.state:
            self.query_one('#summary', Static).update(
                '[bold]No drive inventory loaded.[/]\n'
                '[cyan]Press I to scan this computer for eligible test drives.[/]\n'
                'OS and mounted disks are automatically excluded.'
            )
            self._render_drive_rows([])
            return

        if self.state.get('status') == 'INVENTORY':
            drives = self.drives()
            if self.inventory_message:
                message = self.inventory_message
            elif drives:
                message = f'[green]Inventory complete — {len(drives)} eligible drive(s) found.[/]'
            else:
                message = (
                    '[green]Inventory complete — no eligible test drives found.[/]\n'
                    'Only OS-mounted, mounted, or otherwise excluded disks were detected.'
                )
            self.query_one('#summary', Static).update(message)
            self._render_drive_rows(drives)
            return

        drives = self.drives()
        batch = weighted_batch_progress(self.state)
        health = runtime_health(self.state)
        counts = {'complete': 0, 'running': 0, 'review': 0, 'failed': 0, 'rejected': 0}
        for drive in drives:
            status = str(drive.get('result') or drive.get('status') or '').upper()
            if status in ('PASS', 'COMPLETE'):
                counts['complete'] += 1
            elif status == 'RUNNING':
                counts['running'] += 1
            elif status == 'REVIEW':
                counts['review'] += 1
            elif status in ('FAILED', 'BAD'):
                counts['failed'] += 1
            elif status in ('REJECT', 'REJECTED'):
                counts['rejected'] += 1

        if health['stale']:
            age = format_duration(health['age_seconds']) if health['age_seconds'] is not None else 'unknown'
            summary = (
                f"[bold red]WORKER STOPPED[/]  Batch: {self.state.get('batch_id', 'unknown')}\n"
                f"[red]State still says RUNNING, but no qualification worker is active. Last update: {age} ago.[/]\n"
                f"Last recorded total: [yellow]{batch * 100:5.1f}%[/] — percentages below are stale."
            )
        else:
            summary = (
                f"[bold]Batch:[/] {self.state.get('batch_id', 'unknown')}    [bold cyan]{health['display_status']}[/]\n"
                f"TOTAL  [cyan]{_bar(batch, 42)}[/]  [bold]{batch * 100:5.1f}%[/]\n"
                f"{counts['running']} testing   [green]{counts['complete']} complete[/]   "
                f"[yellow]{counts['review']} review[/]   [red]{counts['failed']} failed   {counts['rejected']} rejected[/]"
            )
        self.query_one('#summary', Static).update(summary)
        self._render_drive_rows(drives)

    def _render_drive_rows(self, drives):
        table = self.query_one('#drive-table', DataTable)
        cursor = table.cursor_row
        table.clear()
        self.drive_keys = []
        for drive in drives:
            key = str(drive.get('id') or drive.get('serial'))
            self.drive_keys.append(key)
            stage_progress = float(drive.get('stage_progress') or 0)
            overall = float(drive.get('overall_progress') or overall_for_drive(drive))
            stage = str(drive.get('stage') or ('Inventory' if self.state and self.state.get('status') == 'INVENTORY' else 'waiting')).replace('-', ' ').title()
            table.add_row(
                Path(drive.get('dev', '?')).name,
                f"{float(drive.get('size_bytes') or 0) / 1e12:.1f}T",
                str(drive.get('serial', '')),
                _status_markup(drive),
                stage,
                f"{_bar(stage_progress, 10)} {stage_progress * 100:4.0f}%" if self.state and self.state.get('status') != 'INVENTORY' else '—',
                f"{_bar(overall, 10)} {overall * 100:4.0f}%" if self.state and self.state.get('status') != 'INVENTORY' else '—',
                format_duration(drive.get('stage_eta_seconds')) if self.state and self.state.get('status') != 'INVENTORY' else '—',
                key=key,
            )
        if table.row_count:
            table.move_cursor(row=max(0, min(cursor, table.row_count - 1)))

    def action_inventory(self):
        if self.demo:
            self.notify('Inventory is disabled in demo mode', severity='warning')
            return
        if self.inventory_running:
            self.notify('Inventory scan is already running', severity='information')
            return

        self.inventory_running = True
        self.inventory_message = ''
        self.query_one('#summary', Static).update(
            '[bold cyan]SCANNING FOR DISKS...[/]\n'
            'Checking attached storage and SMART information.\n'
            'OS-mounted and in-use disks are automatically excluded.'
        )
        threading.Thread(target=self._inventory_worker, daemon=True).start()

    def _inventory_worker(self):
        process = subprocess.run(
            ['/usr/local/bin/diskqual', 'inventory'],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.call_from_thread(self._inventory_finished, process.returncode, process.stdout, process.stderr)

    def _inventory_finished(self, returncode, stdout, stderr):
        self.inventory_running = False
        if returncode:
            detail = (stderr or stdout or 'Unknown inventory error').strip().splitlines()[-1]
            self.inventory_message = ''
            self.query_one('#summary', Static).update(
                '[bold red]Inventory failed — unable to scan disks.[/]\n'
                f'{detail}\n'
                'Press I to try again.'
            )
            self.notify('Inventory failed', severity='error')
            return

        drives = self._load_inventory_file()
        if drives is None:
            self.query_one('#summary', Static).update(
                '[bold red]Inventory failed — scan completed but the inventory file could not be read.[/]\n'
                'Press I to try again.'
            )
            self.notify('Inventory output could not be read', severity='error')
            return

        self.state = self._inventory_state(drives)
        if drives:
            self.inventory_message = f'[green]Inventory complete — {len(drives)} eligible drive(s) found.[/]'
            self.notify(f'Inventory complete: {len(drives)} eligible drive(s) found')
        else:
            self.inventory_message = (
                '[green]Inventory complete — no eligible test drives found.[/]\n'
                'Only OS-mounted, mounted, or otherwise excluded disks were detected.'
            )
            self.notify('Inventory complete: no eligible test drives found')
        self.refresh_state()
