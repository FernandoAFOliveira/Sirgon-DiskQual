# ui.py
"""Sirgon DiskQual operator-interface bootstrap."""

import argparse

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static

from .exports import output_locations
from .keep_awake import KeepAwake
from .operator_ui import OperatorDiskQualApp
from .tui import DEFAULT_STATE, LabelScreen, ReportDriveScreen, ReportScreen


class OutputLocationsScreen(ModalScreen):
    BINDINGS = [
        Binding('escape', 'dismiss', 'Return'),
        Binding('backspace', 'dismiss', 'Return'),
    ]

    def compose(self) -> ComposeResult:
        locations = output_locations()
        body = (
            '[bold cyan]SIRGON DISKQUAL — OUTPUT LOCATIONS[/]\n\n'
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


def _action_outputs(self):
    self.push_screen(OutputLocationsScreen())


def configure_output_locations():
    """Add a persistent, discoverable Outputs screen to operator workflows."""
    for screen_class in (OperatorDiskQualApp, ReportScreen, ReportDriveScreen, LabelScreen):
        bindings = list(screen_class.BINDINGS)
        if not any(getattr(binding, 'key', '') == 'o' for binding in bindings):
            bindings.append(Binding('o', 'outputs', 'Outputs'))
            screen_class.BINDINGS = bindings
        setattr(screen_class, 'action_outputs', _action_outputs)


def configure_focus_defaults():
    """Put keyboard focus on the safest primary control when a screen opens."""
    OperatorDiskQualApp.AUTO_FOCUS = '#drive-table'
    ReportScreen.AUTO_FOCUS = '#projects'
    ReportDriveScreen.AUTO_FOCUS = '#report-drives'
    LabelScreen.AUTO_FOCUS = '#label-drives'


def main():
    configure_focus_defaults()
    configure_output_locations()

    parser = argparse.ArgumentParser(prog='sirgon-diskqual-ui')
    parser.add_argument('--state', default=str(DEFAULT_STATE), help='Path to Sirgon DiskQual state.json')
    parser.add_argument('--demo', action='store_true', help='Run with built-in sample drive data')
    args = parser.parse_args()

    # The interactive display may inhibit screen blanking while it is open,
    # but system suspend protection belongs to the qualification worker. This
    # way drive tests remain protected even if the operator closes the UI.
    with KeepAwake.display_only():
        OperatorDiskQualApp(args.state, args.demo).run()


if __name__ == '__main__':
    main()
