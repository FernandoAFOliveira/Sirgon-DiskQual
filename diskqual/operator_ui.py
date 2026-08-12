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

from .progress import format_duration, overall_for_drive
from .station import active_serials, station_rows
from .tui import DiskQualApp, _bar, _status_markup


class OperatorHelpScreen(ModalScreen):
    BINDINGS = [Binding('escape', 'dismiss', 'Return'), Binding('backspace', 'dismiss', 'Return')]

    def compose(self) -> ComposeResult:
        text = (
            '[bold cyan]SIRGON DISKQUAL — HELP[/]\n\n'
            'I            Inventory / rescan attached drives\n'
            'SPACE        Select or deselect highlighted idle drive\n'
            'ENTER        Open drive details\n'
            'S            Run SMART Long on selected drives\n'
            'T            Start destructive Surface Test on selected SMART-passed drives\n'
            'F            Locate highlighted drive / stop locating it\n'
            'R            Client reports\n'
            'L            Labels\n'
            'H            Help\n'
            'Q / Ctrl+C   Exit display — active tests continue\n\n'
            '[bold]Station workflow[/]\n'
            'The drive list remains available while tests run. Idle drives may be selected and started independently.\n'
            'Inventory/rescan does not stop tests already running on other drives.\n'
            'A drive cannot begin another test while it already has an active job.\n'
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
                'Only drives that have passed the SMART Long phase may proceed.\n'
                'Tests already running on other drives will continue.'
            )
            with Horizontal():
                yield Button('Cancel', id='cancel')
                yield Button('Start Surface Test', id='confirm', variant='error')

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == 'confirm')

    def action_cancel(self):
        self.dismiss(False)


class OperatorDiskQualApp(DiskQualApp):
    """Station-wide operator interface with independent concurrent drive jobs."""

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
        yield Static('Main drive list remains available while tests run', id='hint2')
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

    def _load_inventory_file(self):
        if not self.inventory_drives_path.exists():
            return None
        try:
            data = json.loads(self.inventory_drives_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, list) else None

    def _station_state(self, inventory):
        rows = station_rows(inventory)
        return {
            'version': 5,
            'batch_id': 'station',
            'status': 'STATION',
            'drives': {str(d.get('id') or d.get('serial')): d for d in rows},
        }

    def refresh_state(self):
        if self.demo:
            self._render_summary(self.drives())
            self._render_drive_rows(self.drives())
            return

        inventory = self._load_inventory_file()
        if inventory is None:
            self.state = None
            self.query_one('#summary', Static).update(
                '[bold]No drive inventory loaded.[/]\n'
                '[cyan]Press I or click Inventory to scan for eligible test drives.[/]\n'
                'OS and mounted disks are automatically excluded.'
            )
            self._render_drive_rows([])
            return

        self.state = self._station_state(inventory)
        drives = self.drives()
        present = {str(drive.get('serial') or drive.get('id')) for drive in drives}
        self.selected_serials.intersection_update(present)

        if self.inventory_running:
            self.query_one('#summary', Static).update(
                '[bold cyan]SCANNING FOR DISKS...[/]\n'
                'Running tests continue independently while attached storage and SMART information are refreshed.\n'
                f'{len(drives)} previously known candidate drive(s) remain displayed.'
            )
        else:
            self._render_summary(drives)
        self._render_drive_rows(drives)

    def _render_summary(self, drives):
        counts = {'running': 0, 'ready': 0, 'rejected': 0, 'qualified': 0, 'idle': 0}
        for drive in drives:
            status = str(drive.get('status') or '').upper()
            workflow = str(drive.get('workflow_status') or '').upper()
            if status == 'RUNNING':
                counts['running'] += 1
            elif workflow == 'READY_FOR_SURFACE':
                counts['ready'] += 1
            elif workflow == 'REJECTED':
                counts['rejected'] += 1
            elif workflow in ('QUALIFIED', 'REVIEW'):
                counts['qualified'] += 1
            else:
                counts['idle'] += 1

        message = self.inventory_message
        self.inventory_message = ''
        prefix = f'{message}\n' if message else ''
        self.query_one('#summary', Static).update(
            prefix +
            f"[bold cyan]STATION READY[/]   {len(drives)} candidate drive(s)   [cyan]{len(self.selected_serials)} selected[/]\n"
            f"[cyan]{counts['running']} testing[/]   [green]{counts['ready']} ready for surface[/]   "
            f"[green]{counts['qualified']} qualified/review[/]   [red]{counts['rejected']} rejected[/]   {counts['idle']} idle\n"
            'Select any idle drive(s) to start another test; jobs already running are not interrupted.'
        )

    def _drive_status(self, drive):
        if str(drive.get('status') or '').upper() == 'RUNNING':
            return '[bold cyan]RUNNING[/]'
        workflow = str(drive.get('workflow_status') or '').upper()
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
            active = str(drive.get('status') or '').upper() == 'RUNNING'
            stage_progress = float(drive.get('stage_progress') or 0)
            overall = float(drive.get('overall_progress') or overall_for_drive(drive))
            workflow = str(drive.get('workflow_status') or '').upper()
            stage = str(drive.get('stage') or '').replace('-', ' ').title()
            if not stage:
                stage = 'Ready for Surface' if workflow == 'READY_FOR_SURFACE' else 'Idle'
            show_progress = active or bool(drive.get('active_job_id'))
            table.add_row(
                '[green]✓[/]' if serial in self.selected_serials else ' ',
                Path(drive.get('dev', '?')).name,
                f"{float(drive.get('size_bytes') or 0) / 1e12:.1f}T",
                serial,
                self._drive_status(drive),
                stage,
                f"{_bar(stage_progress, 10)} {stage_progress * 100:4.0f}%" if show_progress else '—',
                f"{_bar(overall, 10)} {overall * 100:4.0f}%" if show_progress else '—',
                format_duration(drive.get('stage_eta_seconds')) if active else '—',
                key=key,
            )
        if table.row_count:
            table.move_cursor(row=max(0, min(cursor, table.row_count - 1)))

    def _drive_is_active(self, drive):
        return str(drive.get('status') or '').upper() == 'RUNNING' or str(drive.get('serial') or '') in active_serials()

    def action_toggle_drive(self):
        drive = self.selected_drive()
        if not drive:
            return
        serial = str(drive.get('serial') or drive.get('id'))
        if serial in self.selected_serials:
            self.selected_serials.remove(serial)
        else:
            if self._drive_is_active(drive):
                self.notify('That drive already has a running test', severity='warning')
                return
            if str(drive.get('workflow_status') or '').upper() == 'REJECTED':
                self.notify('Rejected drives cannot be selected for further qualification', severity='warning')
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
        if self.demo:
            self.notify(f'{label} is disabled in demo mode', severity='warning')
            return
        if not self.selected_serials:
            self.notify('Select at least one idle drive first', severity='warning')
            return
        try:
            self._save_selection()
        except OSError as exc:
            self.notify(f'Unable to save drive selection: {exc}', severity='error')
            return
        self.query_one('#summary', Static).update(
            f'[bold cyan]STARTING {label.upper()}...[/]\nExisting drive tests continue independently.'
        )
        threading.Thread(target=self._phase_worker, args=(command, label), daemon=True).start()

    def _phase_worker(self, command, label):
        process = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.call_from_thread(self._phase_started, process.returncode, process.stdout, process.stderr, label)

    def _phase_started(self, returncode, stdout, stderr, label):
        if returncode:
            detail = (stderr or stdout or 'Unknown error').strip().splitlines()[-1]
            self.notify(f'{label} could not start: {detail}', severity='error')
            self.refresh_state()
            return
        self.selected_serials.clear()
        self.notify(f'{label} started. You may select other idle drives while it runs.')
        self.refresh_state()

    def action_smart_long(self):
        drives = self._selected_drives()
        blocked = [d for d in drives if self._drive_is_active(d) or str(d.get('workflow_status') or '').upper() in ('READY_FOR_SURFACE', 'QUALIFIED')]
        if blocked:
            self.notify('SMART Long selection contains a running or already-passed drive', severity='warning')
            return
        self._start_phase(['/usr/local/bin/diskqual', 'smart-long-selected'], 'SMART Long')

    def action_surface(self):
        drives = self._selected_drives()
        if not drives:
            self.notify('Select SMART-passed drives for Surface Test', severity='warning')
            return
        not_ready = [drive for drive in drives if self._drive_is_active(drive) or str(drive.get('workflow_status') or '').upper() != 'READY_FOR_SURFACE']
        if not_ready:
            self.notify('Surface Test requires every selected drive to be idle and READY FOR SURFACE', severity='warning')
            return
        self.push_screen(SurfaceConfirmScreen(drives), self._surface_confirmed)

    def _surface_confirmed(self, confirmed):
        if confirmed:
            self._start_phase(['/usr/local/bin/diskqual', 'surface-selected', '--yes'], 'Surface Test')

    def _write_locate_request(self, drive):
        self.operator_dir.mkdir(parents=True, exist_ok=True)
        self.locate_path.write_text(json.dumps({'dev': drive.get('dev'), 'serial': drive.get('serial')}, indent=2))

    def action_locate(self):
        if self.demo:
            self.notify('Locate is disabled in demo mode', severity='warning')
            return
        drive = self.selected_drive()
        if not drive:
            self.notify('Highlight a drive to locate', severity='warning')
            return
        dev = str(drive.get('dev') or '')
        action = 'off' if self.locating_dev == dev else 'on'
        try:
            if self.locating_dev and self.locating_dev != dev:
                self.locate_path.write_text(json.dumps({'dev': self.locating_dev, 'serial': ''}, indent=2))
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
        self.inventory_running = True
        self.inventory_message = ''
        self.refresh_state()
        threading.Thread(target=self._inventory_worker, daemon=True).start()

    def _inventory_worker(self):
        process = subprocess.run(['/usr/local/bin/diskqual', 'inventory'], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.call_from_thread(self._inventory_finished, process.returncode, process.stdout, process.stderr)

    def _inventory_finished(self, returncode, stdout, stderr):
        self.inventory_running = False
        if returncode:
            detail = (stderr or stdout or 'Unknown inventory error').strip().splitlines()[-1]
            self.query_one('#summary', Static).update(
                '[bold red]Inventory failed — unable to scan disks.[/]\n'
                f'{detail}\nPress I to try again. Running tests were not interrupted.'
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
        self.inventory_message = f'[green]Inventory complete — {len(drives)} candidate drive(s) found.[/]'
        self.notify(f'Inventory complete: {len(drives)} candidate drive(s) found')
        self.refresh_state()
