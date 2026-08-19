# ui.py
"""Sirgon DiskQual operator-interface bootstrap."""

import argparse
import json
import subprocess
import threading
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from .exports import output_locations
from .keep_awake import KeepAwake
from .operator_ui import OperatorDiskQualApp
from .progress import format_duration, overall_for_drive
from .tui import DEFAULT_STATE, DriveDetails, LabelScreen, ReportDriveScreen, ReportScreen, _bar, _status_markup


def app_version():
    try:
        return version('sirgon-diskqual')
    except PackageNotFoundError:
        return 'development'


def _plain(value, default='—'):
    text = str(value if value not in (None, '') else default)
    return escape(text)


class OutputLocationsScreen(ModalScreen):
    BINDINGS = [Binding('escape', 'dismiss', 'Return'), Binding('backspace', 'dismiss', 'Return')]

    def compose(self) -> ComposeResult:
        locations = output_locations()
        body = (
            f'[bold cyan]SIRGON DISKQUAL {app_version()} — OUTPUT LOCATIONS[/]\n\n'
            f"Export root:\n{locations['root']}\n\n"
            f"Reports folder:\n{locations['reports']}\n\n"
            f"Labels folder:\n{locations['labels']}\n\n"
            '[bold]Most recent files[/]\n'
            f"Report: {locations['last_report'] or 'No report generated yet'}\n"
            f"Labels: {locations['last_labels'] or 'No labels generated yet'}\n\n"
            '[bold]ESC or BACKSPACE — Return[/]'
        )
        with Container(id='dialog'):
            yield Static(body)

    def action_dismiss(self):
        self.dismiss()


class WipeConfirmScreen(ModalScreen):
    BINDINGS = [Binding('escape', 'cancel', 'Cancel')]

    def __init__(self, drives):
        super().__init__()
        self.drives = drives

    def compose(self) -> ComposeResult:
        lines = []
        for drive in self.drives:
            lines.append(f"{Path(drive.get('dev', '?')).name:<6} {float(drive.get('size_bytes') or 0) / 1e12:>5.1f} TB  {drive.get('serial', '')}  {drive.get('model', '')}")
        with Container(id='dialog'):
            yield Static('[bold red]WIPE DISK METADATA[/]', classes='dialog-title')
            yield Static(
                '[bold]THIS REMOVES PARTITION TABLES, FILESYSTEM SIGNATURES, AND COMMON RAID METADATA.[/]\n\n'
                + '\n'.join(lines)
                + '\n\nThis is destructive metadata cleanup for reused qualification disks. It is NOT a secure data-erasure function.\n'
                  'DiskQual re-resolves and verifies each selected serial before every destructive step.'
            )
            with Horizontal():
                yield Button('Cancel', id='cancel')
                yield Button('Wipe Metadata', id='confirm', variant='error')

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == 'confirm')

    def action_cancel(self):
        self.dismiss(False)


class ResetConfirmScreen(ModalScreen):
    BINDINGS = [Binding('escape', 'cancel', 'Cancel')]

    def __init__(self, drive):
        super().__init__()
        self.drive = drive

    def compose(self) -> ComposeResult:
        drive = self.drive
        body = (
            f"Device: {Path(drive.get('dev', '?')).name}\n"
            f"Serial: {drive.get('serial', '')}\n"
            f"Model: {drive.get('model', '')}\n\n"
            'This archives the previous DiskQual qualification state and resets this drive so testing can begin again from SMART Long.\n'
            '[bold]It does not erase disk data or partition metadata.[/] Use Wipe Metadata separately when required.'
        )
        with Container(id='dialog'):
            yield Static('[bold yellow]RESET QUALIFICATION STATE[/]', classes='dialog-title')
            yield Static(body)
            with Horizontal():
                yield Button('Cancel', id='cancel')
                yield Button('Reset Qualification', id='confirm', variant='warning')

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == 'confirm')

    def action_cancel(self):
        self.dismiss(False)


def _action_outputs(self):
    self.push_screen(OutputLocationsScreen())


def configure_output_locations():
    for screen_class in (OperatorDiskQualApp, ReportScreen, ReportDriveScreen, LabelScreen):
        bindings = list(screen_class.BINDINGS)
        if not any(getattr(binding, 'key', '') == 'o' for binding in bindings):
            bindings.append(Binding('o', 'outputs', 'Outputs'))
            screen_class.BINDINGS = bindings
        setattr(screen_class, 'action_outputs', _action_outputs)


def _surface_map(drive, columns=32, rows=4):
    total = columns * rows
    size = int(drive.get('size_bytes') or 0)
    metrics = drive.get('surface_metrics') if isinstance(drive.get('surface_metrics'), dict) else {}
    verified = int(drive.get('surface_verified_bytes') or metrics.get('verified_bytes') or 0)
    workflow = str(drive.get('workflow_status') or '').upper()
    stage = str(drive.get('stage') or '').lower()
    if verified and size:
        progress = min(1.0, verified / size)
    elif workflow in ('QUALIFIED', 'REVIEW'):
        progress = 1.0
    elif stage == 'surface-test':
        progress = float(drive.get('stage_progress') or 0)
    else:
        progress = 0.0
    filled = int(progress * total)
    active = filled if stage == 'surface-test' and filled < total else -1
    cells = []
    for index in range(total):
        if index < filled:
            cells.append('[green]■[/]')
        elif index == active:
            cells.append('[cyan]■[/]')
        else:
            cells.append('[dim]□[/]')
    lines = [''.join(cells[i:i + columns]) for i in range(0, total, columns)]
    done_gib = verified / 1024**3
    total_gib = size / 1024**3 if size else 0
    return '\n'.join(lines), progress, done_gib, max(0.0, total_gib - done_gib)


def _workflow_status_markup(drive):
    status = str(drive.get('workflow_status') or '').upper()
    if status == 'REJECTED':
        return '[bold red]REJECTED[/]'
    if status == 'READY_FOR_SURFACE':
        return '[bold green]READY FOR SURFACE[/]'
    if status == 'QUALIFIED':
        return '[bold green]QUALIFIED[/]'
    if status == 'REVIEW':
        return '[bold yellow]REVIEW[/]'
    if str(drive.get('status') or '').upper() == 'RUNNING':
        return '[bold cyan]RUNNING[/]'
    return '[dim]Not yet qualified[/]'


def _enhanced_drive_details_compose(self):
    d = self.drive
    pipeline = ['baseline-smart', 'smart-short', 'smart-long', 'surface-write', 'surface-verify', 'final-smart', 'classify']
    completed = set(d.get('completed_stages', []))
    current = d.get('stage', '')
    lines = []
    for stage in pipeline:
        if stage in completed:
            marker = '[green]✓[/]'
        elif stage == current or (stage.startswith('surface-') and current == 'surface-test'):
            marker = '[cyan]▶[/]'
        else:
            marker = '[dim]○[/]'
        lines.append(f'{marker} {stage.replace("-", " ").title()}')
    current_progress = float(d.get('stage_progress') or 0)
    overall = float(d.get('overall_progress') or overall_for_drive(d))
    map_text, surface_progress, done_gib, remaining_gib = _surface_map(d)
    show_surface = str(current).lower() == 'surface-test' or bool(d.get('surface_verified_bytes')) or bool(d.get('surface_metrics')) or str(d.get('workflow_status') or '').upper() in ('QUALIFIED', 'REVIEW')
    surface_section = ''
    if show_surface:
        surface_section = (
            '\n[bold]Surface Verification Map[/]\n' + map_text + '\n'
            f'Verified: {surface_progress * 100:5.1f}%   Done: {done_gib:.1f} GiB   Remaining: {remaining_gib:.1f} GiB\n'
            f'Recoverable I/O: {d.get("surface_recoverable_errors", 0)}   Corruption mismatches: {d.get("surface_corruption_errors", 0)}\n'
        )

    evidence = []
    if d.get('smart_short_result'):
        evidence.append(f'SMART Short: [bold]{_plain(d.get("smart_short_result"))}[/]')
        if d.get('smart_short_detail'):
            evidence.append(f'  {_plain(d.get("smart_short_detail"))}')
    if d.get('smart_long_result'):
        evidence.append(f'SMART Long: [bold]{_plain(d.get("smart_long_result"))}[/]')
        if d.get('smart_long_detail'):
            evidence.append(f'  {_plain(d.get("smart_long_detail"))}')
        if d.get('smart_long_utc'):
            evidence.append(f'  Recorded: {_plain(d.get("smart_long_utc"))}')
    if d.get('surface_result'):
        evidence.append(f'Surface: [bold]{_plain(d.get("surface_result"))}[/]')
        if d.get('surface_detail'):
            evidence.append(f'  {_plain(d.get("surface_detail"))}')
    if d.get('message') and str(d.get('status') or '').upper() == 'RUNNING':
        evidence.append(f'Current message: {_plain(d.get("message"))}')
    evidence_section = '\n[bold]Qualification Evidence[/]\n' + ('\n'.join(evidence) if evidence else '[dim]No completed qualification evidence recorded yet.[/]') + '\n'

    body = (
        f'[bold]{_plain(d.get("model", "UNKNOWN"))}[/]\n'
        f'Serial: [bold]{_plain(d.get("serial", "UNKNOWN"))}[/]    Device: {_plain(d.get("dev", "?"))}\n'
        f'Capacity: {float(d.get("size_bytes") or 0)/1e12:.1f} TB    Precheck: {_status_markup({"status": d.get("precheck")})}\n'
        f'Precheck reason: {_plain(d.get("precheck_reason", ""), "None")}\n'
        f'Qualification status: {_workflow_status_markup(d)}\n' + evidence_section + '\n'
        '[bold]Qualification Pipeline[/]\n' + '\n'.join(lines) + '\n\n'
        f'Current  {_bar(current_progress, 28)}  {current_progress*100:5.1f}%\n'
        f'Overall  {_bar(overall, 28)}  {overall*100:5.1f}%\n'
        f'Elapsed: {format_duration(d.get("stage_elapsed_seconds"))}    ETA: {format_duration(d.get("stage_eta_seconds"))}\n'
        f'Throughput: {d.get("throughput_mib_s") or "—"} MiB/s\n' + surface_section +
        '\n[bold]ESC or BACKSPACE — Return to Drive List[/]'
    )
    with Container(id='dialog'):
        yield Static('[bold cyan]SIRGON DISKQUAL — DRIVE DETAILS[/]', classes='dialog-title')
        yield Static(body)


def configure_surface_map():
    DriveDetails.compose = _enhanced_drive_details_compose


def _wipe_worker(self):
    process = subprocess.run(['/usr/local/bin/diskqual', 'wipe-selected', '--yes'], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    self.call_from_thread(_wipe_finished, self, process.returncode, process.stdout, process.stderr)


def _wipe_finished(self, returncode, stdout, stderr):
    if returncode:
        detail = (stderr or stdout or 'Metadata wipe failed').strip().splitlines()[-1]
        self.notify(f'Metadata wipe failed: {detail}', severity='error')
        self.refresh_state()
        return
    self.selected_serials.clear()
    self.notify('Metadata wipe complete. Refreshing inventory.')
    self.action_inventory()


def _wipe_confirmed(self, confirmed):
    if not confirmed:
        return
    try:
        self._save_selection()
    except OSError as exc:
        self.notify(f'Unable to save drive selection: {exc}', severity='error')
        return
    threading.Thread(target=_wipe_worker, args=(self,), daemon=True).start()


def _action_wipe_metadata(self):
    if self.demo:
        self.notify('Metadata wipe is disabled in demo mode', severity='warning')
        return
    drives = self._selected_drives()
    if not drives:
        self.notify('Select one or more idle drives to wipe metadata', severity='warning')
        return
    if any(self._drive_is_active(drive) for drive in drives):
        self.notify('Metadata wipe cannot run on a drive with an active test', severity='warning')
        return
    self.push_screen(WipeConfirmScreen(drives), lambda confirmed: _wipe_confirmed(self, confirmed))


def _reset_worker(self):
    process = subprocess.run(['/usr/local/bin/diskqual', 'reset-selected', '--yes'], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    self.call_from_thread(_reset_finished, self, process.returncode, process.stdout, process.stderr)


def _reset_finished(self, returncode, stdout, stderr):
    if returncode:
        detail = (stderr or stdout or 'Qualification reset failed').strip().splitlines()[-1]
        self.notify(f'Qualification reset failed: {detail}', severity='error')
        self.refresh_state()
        return
    self.selected_serials.clear()
    self.notify('Qualification state reset. Drive is ready to start again from SMART Long.')
    self.action_inventory()


def _reset_confirmed(self, drive, confirmed):
    if not confirmed:
        return
    serial = str(drive.get('serial') or drive.get('id') or '')
    try:
        self.operator_dir.mkdir(parents=True, exist_ok=True)
        self.selection_path.write_text(json.dumps({'serials': [serial]}, indent=2))
    except OSError as exc:
        self.notify(f'Unable to save reset selection: {exc}', severity='error')
        return
    threading.Thread(target=_reset_worker, args=(self,), daemon=True).start()


def _action_reset_qualification(self):
    if self.demo:
        self.notify('Qualification reset is disabled in demo mode', severity='warning')
        return
    drive = self.selected_drive()
    if not drive:
        self.notify('Highlight the drive whose qualification state should be reset', severity='warning')
        return
    if self._drive_is_active(drive):
        self.notify('Qualification state cannot be reset while the drive has an active test', severity='warning')
        return
    self.push_screen(ResetConfirmScreen(drive), lambda confirmed: _reset_confirmed(self, drive, confirmed))


def configure_operator_actions():
    OperatorDiskQualApp.action_wipe_metadata = _action_wipe_metadata
    OperatorDiskQualApp.action_reset_qualification = _action_reset_qualification
    original_mount = OperatorDiskQualApp.on_mount

    def enhanced_mount(self):
        original_mount(self)
        self.bind('w', 'wipe_metadata', description='Wipe Metadata')
        self.bind('x', 'reset_qualification', description='Reset Qualification')
        self.query_one('#hint', Static).update(
            'SPACE Select   ENTER Details   S SMART Long   T Surface Test   F Locate   W Wipe Metadata   X Reset Qualification   R Reports   L Labels   H Help   Q Exit'
        )

    OperatorDiskQualApp.on_mount = enhanced_mount


def configure_focus_defaults():
    OperatorDiskQualApp.AUTO_FOCUS = '#drive-table'
    ReportScreen.AUTO_FOCUS = '#projects'
    ReportDriveScreen.AUTO_FOCUS = '#report-drives'
    LabelScreen.AUTO_FOCUS = '#label-drives'


def configure_version_display():
    current = app_version()
    OperatorDiskQualApp.TITLE = f'Sirgon DiskQual {current}'
    OperatorDiskQualApp.SUB_TITLE = 'Disk Qualification Station'


def main():
    configure_focus_defaults()
    configure_output_locations()
    configure_version_display()
    configure_surface_map()
    configure_operator_actions()

    parser = argparse.ArgumentParser(prog='sirgon-diskqual-ui')
    parser.add_argument('--state', default=str(DEFAULT_STATE), help='Path to Sirgon DiskQual state.json')
    parser.add_argument('--demo', action='store_true', help='Run with built-in sample drive data')
    args = parser.parse_args()
    with KeepAwake.display_only():
        OperatorDiskQualApp(args.state, args.demo).run()


if __name__ == '__main__':
    main()
