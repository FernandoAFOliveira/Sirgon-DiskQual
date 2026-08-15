# keep_awake.py
"""Temporary Linux display and system-sleep inhibition for DiskQual."""

import os
import re
import shutil
import subprocess


class KeepAwake:
    """Hold best-effort temporary power-management inhibitors.

    ``display=True`` requests desktop screensaver/display inhibition. This is
    appropriate for the interactive UI and is released when the UI exits.

    ``system=True`` requests a logind sleep inhibitor. This is intended for
    active qualification workers so closing the UI cannot allow the host to
    suspend while a drive test is still running.

    No persistent power settings are changed. Unsupported mechanisms are
    skipped so DiskQual remains portable across Linux desktops and servers.
    """

    def __init__(self, *, display=True, system=True, reason='Drive qualification is active'):
        self.display = bool(display)
        self.system = bool(system)
        self.reason = str(reason)
        self._logind_fd = None
        self._screensaver_cookie = None
        self._power_cookie = None

    @classmethod
    def display_only(cls):
        return cls(display=True, system=False, reason='DiskQual operator display is active')

    @classmethod
    def testing_only(cls):
        return cls(display=False, system=True, reason='DiskQual drive testing is active')

    @staticmethod
    def _gdbus_call(destination, object_path, method, *args):
        if not shutil.which('gdbus'):
            return ''
        process = subprocess.run(
            [
                'gdbus', 'call', '--session',
                '--dest', destination,
                '--object-path', object_path,
                '--method', method,
                *args,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return process.stdout.strip() if process.returncode == 0 else ''

    @staticmethod
    def _cookie(output):
        match = re.search(r'uint32\s+(\d+)', output)
        if not match:
            match = re.search(r'\((\d+),?\)', output)
        return int(match.group(1)) if match else None

    def _start_logind_inhibitor(self):
        if not self.system:
            return
        try:
            from dbus_next.aio import MessageBus
            from dbus_next.constants import BusType
        except ImportError:
            return

        # dbus-next's async API is awkward to hold from our synchronous worker,
        # so use the low-level UNIX FD helper exposed by busctl when available.
        # busctl's --fd-capability keeps the returned inhibitor FD open in the
        # caller, which is exactly how logind inhibition is designed to work.
        if not shutil.which('busctl'):
            return

        try:
            process = subprocess.run(
                [
                    'busctl', '--system', '--fd-capability', 'call',
                    'org.freedesktop.login1',
                    '/org/freedesktop/login1',
                    'org.freedesktop.login1.Manager',
                    'Inhibit',
                    'ssss',
                    'sleep',
                    'Sirgon DiskQual',
                    self.reason,
                    'block',
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                pass_fds=(),
                timeout=5,
            )
            if process.returncode != 0:
                return
        except (OSError, subprocess.SubprocessError):
            return

    def _start_systemd_inhibitor(self):
        # Prefer a plain systemd-inhibit child because the inhibitor lifetime is
        # tied to that child's process. Use `tail -f /dev/null` rather than
        # `sleep infinity`; the latter can exit immediately on some systems.
        if not self.system or not shutil.which('systemd-inhibit'):
            return
        try:
            self._systemd_process = subprocess.Popen(
                [
                    'systemd-inhibit',
                    '--what=sleep',
                    '--who=Sirgon DiskQual',
                    f'--why={self.reason}',
                    '--mode=block',
                    'tail', '-f', '/dev/null',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self._systemd_process = None

    def _start_desktop_inhibitors(self):
        if not self.display:
            return
        try:
            output = self._gdbus_call(
                'org.freedesktop.ScreenSaver',
                '/org/freedesktop/ScreenSaver',
                'org.freedesktop.ScreenSaver.Inhibit',
                'Sirgon DiskQual',
                self.reason,
            )
            self._screensaver_cookie = self._cookie(output)
        except (OSError, subprocess.SubprocessError):
            self._screensaver_cookie = None

        try:
            output = self._gdbus_call(
                'org.freedesktop.PowerManagement',
                '/org/freedesktop/PowerManagement/Inhibit',
                'org.freedesktop.PowerManagement.Inhibit.Inhibit',
                'Sirgon DiskQual',
                self.reason,
            )
            self._power_cookie = self._cookie(output)
        except (OSError, subprocess.SubprocessError):
            self._power_cookie = None

    @property
    def system_active(self):
        return getattr(self, '_systemd_process', None) is not None and self._systemd_process.poll() is None

    def start(self):
        self._systemd_process = None
        self._start_systemd_inhibitor()
        self._start_desktop_inhibitors()
        return self

    def stop(self):
        if self._screensaver_cookie is not None:
            try:
                self._gdbus_call(
                    'org.freedesktop.ScreenSaver',
                    '/org/freedesktop/ScreenSaver',
                    'org.freedesktop.ScreenSaver.UnInhibit',
                    str(self._screensaver_cookie),
                )
            except (OSError, subprocess.SubprocessError):
                pass
            self._screensaver_cookie = None

        if self._power_cookie is not None:
            try:
                self._gdbus_call(
                    'org.freedesktop.PowerManagement',
                    '/org/freedesktop/PowerManagement/Inhibit',
                    'org.freedesktop.PowerManagement.Inhibit.UnInhibit',
                    str(self._power_cookie),
                )
            except (OSError, subprocess.SubprocessError):
                pass
            self._power_cookie = None

        if getattr(self, '_systemd_process', None) is not None:
            try:
                self._systemd_process.terminate()
                self._systemd_process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self._systemd_process.kill()
                except OSError:
                    pass
            self._systemd_process = None

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
        return False
