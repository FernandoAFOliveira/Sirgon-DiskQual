# operator_ui.py
import json
import subprocess
import threading
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Static

from .progress import format_duration, load_state, overall_for_drive, weighted_batch_progress
from .status import runtime_health
from .tui import DiskQualApp, _bar, _status_markup
from .workflow import load_registry


class OperatorHelpScreen(ModalScreen):
    BINDINGS = [Binding('escape', 'dismiss', 'Return'), Binding('backspace', 'dismiss', 'Return')]

    def compose(self) -> ComposeResult:
        text = (
            '[bold cyan]SIRGON DISKQUAL — HELP[/]\n\n'
            'I            Inventory / rescan attached drives\n'
            'SPACE        Select or deselect highlighted drive\n'
            'ENTER        Open drive details\n'
            'S            Run SMART Long on selected drives\n'
            'T            Start destructive Surface Test on selected SMART-passed drives\n'
            'F            Locate highlighted drive / stop locating it\n'
            'R            Client reports\n'
            'L            Labels\n'
            'H            Help\n'
            'Q / Ctrl+C   Exit Sirgon DiskQual display — active tests continue\n\n'
            'Surface testing is only permitted after SMART Long has passed.\n'
            'If the controller/enclosure cannot identify a physical bay, Locate reports that capability is unavailable.\n\n'
            '[bold]ESC or BACKSPACE — Return[/]'
        )
        with Container(id='dialog'):
            yield Static(text)

    def action_dismiss(self):
        self.dismiss()


class SurfaceConfirmScreen(ModalScreen):
    BINDINGS = [Binding('escape', 'cancel', 'Cancel')]

    def __init__(self, drives):
        super().__init__()
        self.drives = drives

    def compose(self) -> ComposeResult:
        lines = []
        for drive in self.drives:
            lines.append(
                f"{Path(drive.get('dev', '?')).name:<6} {float(drive.get('size_bytes') or 0) / 1e12:>4.1f} TB  "
                f"{drive.get('model', '')}  {drive.get('serial', '')}"
            )
        body = '\n'.join(lines)
        with Container(id='dialog'):
            yield Static('[bold red]DESTRUCTIVE SURFACE TEST[/]', classes='dialog-title')
            yield Static(
                '[bold]ALL DATA ON THE FOLLOWING DRIVES WILL BE DESTROYED.[/]\n\n'
                f'{body}\n\n'
                'Only drives that have passed the SMART Long phase may proceed.'
            )
            with Horizontal():
                yield Button('Cancel', id='cancel')
                yield Button('Start Surface Test', id='confirm', variant='error')

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == 'confirm')

    def action_cancel(self):
        self.dismiss(False)


class OperatorDiskQualApp(DiskQualApp):
    """Installed operator interface with phased qualification workflow."""

    BINDINGS = [
        Binding('i', 'inventory', 'Inventory'),
        Binding('space', 'toggle_drive', 'Select Drive'),
        Binding('s', 'smart_long', 'SMART Long'),
        Binding('t', 'surface', 'Surface Test'),
        Binding('f', 'locate', 'Locate Drive'),
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
        self.operator_dir = Path('/opt/diskqual/operator')
        self.selection_path = self.operator_dir / 'selection.json'
        self.locate_path = self.operator_dir / 'locate.json'
        self.selected_serials = set()
        self.inventory_mode = False
        self.locating_dev = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static('SIRGON DISKQUAL  •  Disk Qualification Station', id='title')
        yield Static(id='summary')
        with Horizontal(id='actions'):
            yield Button('Inventory', id='inventory')
            yield Button('Run SMART Long', id='smart-long', variant='primary')
            yield Button('Start Surface Test', id='surface', variant='warning')
            yield Button('Locate Drive', id='locate')
            yield Button('Clear Selection', id='clear-selection')
        yield DataTable(id='drive-table')
        yield Static(
            'SPACE Select   ENTER Details   S SMART Long   T Surface Test   F Locate   R Reports   L Labels   H Help   Q Exit',
            id='hint',
        )
        yield Static('Ctrl+Alt+F1  Switch to Linux OS — Tests Continue', id='hint2')
        yield Footer()

    def on_mount(self):
        table = self.query_one('#drive-table', DataTable)
        table.cursor_type = 'row'
        table.zebra_stripes = True
        table.add_columns('Sel', 'Dev', 'Size', 'Serial', 'Status', 'Current Test', 'Current', 'Overall', 'ETA')
        self.refresh_state()
        self.set_interval(2.0, self.refresh_state)

    def on_button_pressed(self, event: Button.Pressed):
        actions = {
            'inventory': self.action_inventory,
            'smart-long': self.action_smart_long,
            'surface': self.action_surface,
            'locate': self.action_locate,
            'clear-selection': self.action_clear_selection,
        }
        action = actions.get(event.button.id)
        if action:
            action()

    def action_help(self):
        self.push_screen(OperatorHelpScreen())

    def _inventory_state(self, drives):
        registry = load_registry()
        merged = []
        for drive in drives:
            row = dict(drive)
            workflow = registry.get(str(drive.get('serial')), {})
            if workflow:
                row['workflow_status'] = workflow.get('status')
                row['smart_long_result'] = workflow.get('smart_long_result')
                row['smart_long_detail'] = workflow.get('smart_long_detail')
            merged.append(row)
        return {
            'version': 4,
            'batch_id': 'inventory',
            'status': 'INVENTORY',
            'drives': {str(d.get('id') or d.get('serial')): d for d in merged},
        }

    def _load_inventory_file(self):
        if not self.inventory_drives_path.exists():
            return None
        try:
            data = json.loads(self.inventory_drives_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, list) else None

    def _show_inventory(self):
        inventory = self._load_inventory_file()
        if inventory is not None:
            self.state = self._inventory_state(inventory)
            self.inventory_mode = True
            return True
        return False

    def refresh_state(self):
        if self.inventory_running:
            self.query_one('#summary', Static).update(
                '[bold cyan]SCANNING FOR DISKS...[/]\n'
                'Checking attached storage and SMART information.\n'
                'OS-mounted and in-use disks are automatically excluded.'
            )
            return

        if not self.demo and not self.inventory_mode:
            state = load_state(self.state_path)
            if state:
                self.state = state
            elif self.state is None:
                self._show_inventory()

        if not self.state:
            self.query_one('#summary', Static).update(
                '[bold]No drive inventory loaded.[/]\n'
                '[cyan]Press I or click Inventory to scan for eligible test drives.[/]\n'
                'OS and mounted disks are automatically excluded.'
            )
            self._render_drive_rows([])
            return

        if self.state.get('status') == 'INVENTORY':
            drives = self.drives()
            selected = len(self.selected_serials)
            if self.inventory_message:
                message = self.inventory_message
            elif drives:
                message = f'[green]Inventory complete — {len(drives)} candidate drive(s) found.[/]  [cyan]{selected} selected[/]'
            else:
                message = '[green]Inventory complete — no eligible test drives found.[/]'
            self.query_one('#summary', Static).update(message)
            self._render_drive_rows(drives)
            return

        drives = self.drives()
        batch = weighted_batch_progress(self.state)
        health = runtime_health(self.state)
        status = str(self.state.get('status') or '').upper()
        counts = {'complete': 0, 'running': 0, 'review': 0, 'failed': 0, 'rejected': 0, 'ready': 0}
        for drive in drives:
            drive_status = str(drive.get('result') or drive.get('status') or '').upper()
            if drive_status in ('PASS', 'COMPLETE', 'QUALIFIED'):
                counts['complete'] += 1
            elif drive_status == 'RUNNING':
                counts['running'] += 1
            elif drive_status in ('REVIEW', 'SMART_LONG_PASSED', 'READY_FOR_SURFACE'):
                counts['ready'] += 1
            elif drive_status in ('FAILED', 'BAD', 'SMART_LONG_FAILED'):
                counts['failed'] += 1
            elif drive_status in ('REJECT', 'REJECTED'):
                counts['rejected'] += 1

        if health['stale']:
            age = format_duration(health['age_seconds']) if health['age_seconds'] is not None else 'unknown'
            summary = (
                f"[bold red]WORKER STOPPED[/]  Batch: {self.state.get('batch_id', 'unknown')}\n"
                f"[red]No test worker is active. Last update: {age} ago.[/]\n"
                'Use Inventory to reassess attached drives before starting another phase.'
            )
        elif status == 'SMART_REVIEW':
            summary = (
                f"[bold cyan]SMART LONG COMPLETE — REVIEW RESULTS[/]  Batch: {self.state.get('batch_id', 'unknown')}\n"
                f"[green]{counts['ready']} ready for Surface Test[/]   [red]{counts['failed'] + counts['rejected']} rejected/failed[/]\n"
                'Replace failed drives if desired, run Inventory, then SMART Long on replacements before Surface Test.'
            )
        else:
            summary = (
                f"[bold]Batch:[/] {self.state.get('batch_id', 'unknown')}    [bold cyan]{health['display_status']}[/]\n"
                f"TOTAL  [cyan]{_bar(batch, 42)}[/]  [bold]{batch * 100:5.1f}%[/]\n"
                f"{counts['running']} testing   [green]{counts['complete']} complete[/]   "
                f"[cyan]{counts['ready']} ready[/]   [red]{counts['failed']} failed   {counts['rejected']} rejected[/]"
            )
        self.query_one('#summary', Static).update(summary)
        self._render_drive_rows(drives)

    def _drive_status(self, drive):
        workflow = str(drive.get('workflow_status') or '').upper()
        if workflow:
            if workflow == 'READY_FOR_SURFACE':
                return '[bold green]READY FOR SURFACE[/]'
            if workflow == 'REJECTED':
                return '[bold red]REJECTED[/]'
            if workflow == 'QUALIFIED':
                return '[bold green]QUALIFIED[/]'
            if workflow == 'REVIEW':
                return '[bold yellow]REVIEW[/]'
        return _status_markup(drive)

    def _render_drive_rows(self, drives):
        table = self.query_one('#drive-table', DataTable)
        cursor = table.cursor_row
        table.clear()
        self.drive_keys = []
        for drive in drives:
            key = str(drive.get('id') or drive.get('serial'))
            serial = str(drive.get('serial') or key)
            self.drive_keys.append(key)
            stage_progress = float(drive.get('stage_progress') or 0)
            overall = float(drive.get('overall_progress') or overall_for_drive(drive))
            inventory = self.state and self.state.get('status') == 'INVENTORY'
            stage = str(drive.get('stage') or ('Inventory' if inventory else 'waiting')).replace('-', ' ').title()
            table.add_row(
                '[green]✓[/]' if serial in self.selected_serials else ' ',
                Path(drive.get('dev', '?')).name,
                f"{float(drive.get('size_bytes') or 0) / 1e12:.1f}T",
                serial,
                self._drive_status(drive),
                stage,
                f"{_bar(stage_progress, 10)} {stage_progress * 100:4.0f}%" if not inventory else '—',
                f"{_bar(overall, 10)} {overall * 100:4.0f}%" if not inventory else '—',
                format_duration(drive.get('stage_eta_seconds')) if not inventory else '—',
                key=key,
            )
        if table.row_count:
            table.move_cursor(row=max(0, min(cursor, table.row_count - 1)))

    def action_toggle_drive(self):
        drive = self.selected_drive()
        if not drive:
            return
        serial = str(drive.get('serial') or drive.get('id'))
        if serial in self.selected_serials:
            self.selected_serials.remove(serial)
        else:
            if str(drive.get('workflow_status') or '').upper() == 'REJECTED':
                self.notify('Rejected drives cannot be selected for further testing', severity='warning')
                return
            self.selected_serials.add(serial)
        self.refresh_state()

    def action_clear_selection(self):
        self.selected_serials.clear()
        self.refresh_state()

    def _selected_drives(self):
        return [drive for drive in self.drives() if str(drive.get('serial') or drive.get('id')) in self.selected_serials]

    def _save_selection(self):
        self.operator_dir.mkdir(parents=True, exist_ok=True)
        self.selection_path.write_text(json.dumps({'serials': sorted(self.selected_serials)}, indent=2))

    def _start_phase(self, command, label):
        if not self.selected_serials:
            self.notify('Select at least one drive first', severity='warning')
            return
        try:
            self._save_selection()
        except OSError as exc:
            self.notify(f'Unable to save drive selection: {exc}', severity='error')
            return
        self.inventory_mode = False
        self.query_one('#summary', Static).update(f'[bold cyan]STARTING {label.upper()}...[/]')
        threading.Thread(target=self._phase_worker, args=(command, label), daemon=True).start()

    def _phase_worker(self, command, label):
        process = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.call_from_thread(self._phase_started, process.returncode, process.stdout, process.stderr, label)

    def _phase_started(self, returncode, stdout, stderr, label):
        if returncode:
            detail = (stderr or stdout or 'Unknown error').strip().splitlines()[-1]
            self.notify(f'{label} could not start: {detail}', severity='error')
            self.inventory_mode = True
            self.refresh_state()
            return
        self.selected_serials.clear()
        self.notify(f'{label} started. Tests continue independently of this display.')
        self.refresh_state()

    def action_smart_long(self):
        self._start_phase(['/usr/local/bin/diskqual', 'smart-long-selected'], 'SMART Long')

    def action_surface(self):
        drives = self._selected_drives()
        if not drives:
            self.notify('Select SMART-passed drives for Surface Test', severity='warning')
            return
        not_ready = [drive for drive in drives if str(drive.get('workflow_status') or '').upper() != 'READY_FOR_SURFACE']
        if not_ready:
            self.notify('Surface Test requires every selected drive to pass SMART Long first', severity='warning')
            return
        self.push_screen(SurfaceConfirmScreen(drives), self._surface_confirmed)

    def _surface_confirmed(self, confirmed):
        if confirmed:
            self._start_phase(['/usr/local/bin/diskqual', 'surface-selected', '--yes'], 'Surface Test')

    def _write_locate_request(self, drive):
        self.operator_dir.mkdir(parents=True, exist_ok=True)
        self.locate_path.write_text(json.dumps({'dev': drive.get('dev'), 'serial': drive.get('serial')}, indent=2))

    def action_locate(self):
        drive = self.selected_drive()
        if not drive:
            self.notify('Select a drive to locate', severity='warning')
            return
        dev = str(drive.get('dev') or '')
        action = 'off' if self.locating_dev == dev else 'on'
        try:
            if self.locating_dev and self.locating_dev != dev:
                old = {'dev': self.locating_dev, 'serial': ''}
                self._write_locate_request(old)
                subprocess.run(['/usr/local/bin/diskqual', 'locate-selected', 'off'], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self._write_locate_request(drive)
        except OSError as exc:
            self.notify(f'Unable to create locate request: {exc}', severity='error')
            return
        process = subprocess.run(['/usr/local/bin/diskqual', 'locate-selected', action], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        message = (process.stdout or process.stderr or '').strip().splitlines()
        detail = message[-1] if message else 'Locate operation failed.'
        if process.returncode:
            self.locating_dev = None
            self.notify(detail, severity='warning')
        else:
            self.locating_dev = dev if action == 'on' else None
            self.notify(detail)

    def action_inventory(self):
        if self.demo:
            self.notify('Inventory is disabled in demo mode', severity='warning')
            return
        if self.inventory_running:
            self.notify('Inventory scan is already running', severity='information')
            return
        health = runtime_health(load_state(self.state_path))
        if health['worker_active']:
            self.notify('Inventory is unavailable while a test phase is running', severity='warning')
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
        process = subprocess.run(['/usr/local/bin/diskqual', 'inventory'], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.call_from_thread(self._inventory_finished, process.returncode, process.stdout, process.stderr)

    def _inventory_finished(self, returncode, stdout, stderr):
        self.inventory_running = False
        if returncode:
            detail = (stderr or stdout or 'Unknown inventory error').strip().splitlines()[-1]
            self.inventory_message = ''
            self.query_one('#summary', Static).update(
                '[bold red]Inventory failed — unable to scan disks.[/]\n'
                f'{detail}\nPress I to try again.'
            )
            self.notify('Inventory failed', severity='error')
            return

        drives = self._load_inventory_file()
        if drives is None:
            self.query_one('#summary', Static).update(
                '[bold red]Inventory failed — scan completed but the inventory file could not be read.[/]'
            )
            self.notify('Inventory output could not be read', severity='error')
            return

        present = {str(drive.get('serial') or drive.get('id')) for drive in drives}
        self.selected_serials.intersection_update(present)
        self.state = self._inventory_state(drives)
        self.inventory_mode = True
        self.inventory_message = f'[green]Inventory complete — {len(drives)} candidate drive(s) found.[/]  [cyan]{len(self.selected_serials)} selected[/]'
        self.notify(f'Inventory complete: {len(drives)} candidate drive(s) found')
        self.refresh_state()
