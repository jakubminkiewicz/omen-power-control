#!/usr/bin/python3 -I
"""Qt controls for the OMEN Transcend 14 power service."""

import json
from pathlib import Path
import runpy
import sys

from PySide6.QtCore import QProcess, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSlider,
    QTabWidget, QVBoxLayout, QWidget,
)


TITLE = "OMEN Power Control"
HELPER = Path("/usr/libexec/omen-control/omen-control-helper")
STATUS_HELPER = Path(__file__).resolve().with_name(HELPER.name)
PKEXEC = Path("/usr/bin/pkexec")
KEY_ENROLLMENT_REQUIRED = 75
PROFILE_LABELS = {"cool": "Cool", "balanced": "Balanced", "performance": "Performance"}
FAN_LABELS = {0: "Maximum", 2: "Firmware automatic"}
FanCurveEditor = runpy.run_path(str(Path(__file__).resolve().with_name("omen-fan-curves.py")))["FanCurveEditor"]
cpu_ui = runpy.run_path(str(Path(__file__).resolve().with_name("omen-cpu-profiles.py")))
CpuProfileEditor = cpu_ui["CpuProfileEditor"]
CPU_LIMIT_LABELS = cpu_ui["LIMIT_LABELS"]

STYLE = """
QMainWindow, QWidget#page { background: #12161e; }
QWidget { color: #e8edf5; font-family: "Noto Sans"; font-size: 10pt; }
QLabel#title { font-size: 22pt; font-weight: 700; }
QLabel#muted, QLabel#metricLabel { color: #a9b5c7; }
QLabel#sectionTitle { font-size: 11pt; font-weight: 700; }
QLabel#metricValue { font-size: 15pt; font-weight: 700; }
QLabel#status { color: #b5c6db; background: #263346; border-radius: 12px; padding: 5px 11px; }
QLabel#status[good="true"] { color: #a0edcf; background: #1a3a36; }
QFrame#card { border-radius: 12px; border: 1px solid #2c3647; background: #1b222e; }
QFrame#card QLabel { background: transparent; }
QTabWidget::pane { border: 0; }
QTabBar::base { border: 0; }
QTabBar::tab { color: #a9b5c7; padding: 10px 17px; margin-right: 5px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { border-color: #60d8c4; color: #f4fbff; font-weight: 700; }
QTabBar::tab:hover { color: #f4fbff; }
QScrollArea { border: 0; background: #12161e; }
QPushButton { background: #273447; border: 1px solid #394960; border-radius: 7px; padding: 7px 12px; }
QPushButton:hover { background: #33455c; }
QPushButton:focus, QComboBox:focus { border: 1px solid #60d8c4; }
QPushButton#primary { color: #102823; background: #64d9c3; border-color: #64d9c3; font-weight: 700; }
QPushButton#primary:hover { background: #83e5d4; }
QPushButton:disabled, QPushButton#primary:disabled { color: #828c9c; background: #242d3b; border-color: #343f4f; }
QComboBox { background: #253145; border: 1px solid #394960; border-radius: 7px; padding: 6px 10px; min-width: 165px; }
QComboBox#ecProfileChoice { min-width: 98px; }
QComboBox QAbstractItemView { selection-background-color: #38566d; background: #253145; }
QComboBox:disabled, QCheckBox:disabled { color: #929cab; }
QSpinBox { background: #253145; border: 1px solid #394960; border-radius: 7px; padding: 6px 8px; min-width: 57px; }
QSpinBox:focus { border-color: #60d8c4; }
QSpinBox:disabled { color: #929cab; }
QCheckBox { spacing: 8px; }
QSlider::groove:horizontal { background: #344156; height: 5px; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #64d9c3; border-radius: 2px; }
QSlider::handle:horizontal { background: #96eddc; width: 13px; margin: -5px 0; border-radius: 6px; }
QSlider::handle:horizontal:disabled { background: #6d788a; }
QSlider::sub-page:horizontal:disabled { background: #526077; }
QPlainTextEdit { background: #121820; border: 1px solid #2f3a4c; border-radius: 8px; padding: 8px; }
QScrollBar:vertical { background: #151d28; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #42516a; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QLabel#message { color: #afc5d8; }
QLabel#message[error="true"] { color: #ffb3b3; }
QLabel#powerWarning { color: #f5cb91; border-left: 2px solid #aa7947; padding: 3px 9px; }
"""


def label(text="", style=None):
    item = QLabel(text)
    item.setTextFormat(Qt.TextFormat.PlainText)
    if style:
        item.setObjectName(style)
    return item


def frame():
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(17, 14, 17, 14)
    layout.setSpacing(8)
    return card, layout


def heading(layout, text):
    layout.addWidget(label(text, "sectionTitle"))


def update_property(widget, name, value):
    if widget.property(name) != value:
        widget.setProperty(name, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


def button(text, action, *, primary=False):
    item = QPushButton(text)
    item.clicked.connect(action)
    if primary:
        item.setObjectName("primary")
    return item


def log_view():
    view = QPlainTextEdit()
    view.setReadOnly(True)
    view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
    view.setMaximumBlockCount(350)
    return view


def limit_table(layout, *columns):
    table = QGridLayout()
    table.setHorizontalSpacing(14)
    table.setVerticalSpacing(7)
    for column, name in enumerate(("Limit", *columns)):
        table.addWidget(label(name, "muted"), 0, column)
    readings = []
    for row, name in enumerate(CPU_LIMIT_LABELS.values(), start=1):
        table.addWidget(label(name), row, 0)
        values = [label("—") for _ in columns]
        readings.append(values)
        for column, value in enumerate(values, start=1):
            table.addWidget(value, row, column)
    layout.addLayout(table)
    return readings


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(TITLE)
        self.setWindowIcon(QIcon.fromTheme("preferences-system-power-management"))
        self.setMinimumSize(690, 580)
        self.resize(880, 760)
        self.state = {}
        self.profile_dirty = self.gpu_dirty = False
        self.gain_steps = ()
        self.active_action = None
        self.service_install_offered = False
        self.output = bytearray()

        self.status_process = QProcess(self)
        self.status_process.finished.connect(self.status_finished)
        self.status_process.errorOccurred.connect(self.status_error)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.collect_output)
        self.process.finished.connect(self.action_finished)
        self.process.errorOccurred.connect(self.action_error)

        main = QWidget()
        main.setObjectName("page")
        self.setCentralWidget(main)
        outer = QVBoxLayout(main)
        outer.setContentsMargins(23, 19, 23, 14)
        outer.setSpacing(12)

        top = QHBoxLayout()
        identity = QVBoxLayout()
        identity.setSpacing(3)
        identity.addWidget(label(TITLE, "title"))
        self.model = label("Reading hardware…", "muted")
        identity.addWidget(self.model)
        top.addLayout(identity, 1)
        self.service_badge = label("Checking service", "status")
        top.addWidget(self.service_badge, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.apply_button = button("Apply Fixes", lambda: self.start_action("apply-fixes"), primary=True)
        self.apply_button.setToolTip("Select Performance on rear AC and reapply the selected CPU profile, cooling and GPU boost.")
        top.addWidget(self.apply_button, alignment=Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(top)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.page(self.overview), "Overview")
        self.tabs.addTab(self.page(self.cpu_controls), "CPU Controls")
        self.tabs.addTab(self.page(self.gpu_controls), "GPU Controls")
        self.tabs.addTab(self.page(self.fan_profiles), "Fan Curves")
        self.tabs.addTab(self.page(self.fixes), "Fixes")
        self.service_page = self.page(self.services)
        self.tabs.addTab(self.service_page, "Service")
        outer.addWidget(self.tabs, 1)
        self.message = label("Reading system status…", "message")
        self.message.setWordWrap(True)
        outer.addWidget(self.message)
        self.update_buttons()
        self.refresh_timer = QTimer(self, interval=2400)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start()
        QTimer.singleShot(0, self.refresh)

    def page(self, build):
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 2, 10)
        layout.setSpacing(12)
        build(layout)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    def overview(self, layout):
        quick = QHBoxLayout()
        self.quick_values = []
        for name in ("Rear AC", "EC profile", "CPU temperature"):
            card, contents = frame()
            contents.addWidget(label(name, "metricLabel"))
            value = label("—", "metricValue")
            self.quick_values.append(value)
            contents.addWidget(value)
            if name == "EC profile":
                line = QHBoxLayout()
                self.profile_choice = QComboBox()
                self.profile_choice.setObjectName("ecProfileChoice")
                self.profile_choice.setAccessibleName("EC performance profile")
                self.profile_choice.activated.connect(self.mark_profile_dirty)
                self.set_profile_button = button("Set Profile", self.apply_profile)
                line.addWidget(self.profile_choice, 1)
                line.addWidget(self.set_profile_button)
                contents.addLayout(line)
            contents.addStretch()
            quick.addWidget(card, 1)
        layout.addLayout(quick)

        middle = QHBoxLayout()
        power_card, power = frame()
        heading(power, "CPU package limits")
        self.limit_values = limit_table(power, "MSR", "MMIO")
        power.addStretch()
        middle.addWidget(power_card, 1)

        fan_card, cooling = frame()
        heading(cooling, "Cooling")
        form = QFormLayout()
        self.fan_readback = label("—")
        self.fan1_readback = label("—")
        self.fan2_readback = label("—")
        for title, value in (("Mode", self.fan_readback), ("Fan 1", self.fan1_readback), ("Fan 2", self.fan2_readback)):
            form.addRow(title, value)
        cooling.addLayout(form)
        cooling.addStretch()
        middle.addWidget(fan_card, 1)
        layout.addLayout(middle)

        gpu_card, gpu = frame()
        line = QHBoxLayout()
        heading(line, "GPU boost")
        line.addStretch()
        self.sample_button = button("Sample GPU", lambda: self.start_action("sample-gpu", admin=False))
        line.addWidget(self.sample_button)
        gpu.addLayout(line)
        self.gpu_flags = label("—")
        gpu.addWidget(self.gpu_flags)
        self.sample_result = label("Sample once to read GPU draw and ceiling.", "muted")
        self.sample_result.setWordWrap(True)
        gpu.addWidget(self.sample_result)
        layout.addWidget(gpu_card)

        self.policy_hint = label("", "muted")
        self.policy_hint.setWordWrap(True)
        layout.addWidget(self.policy_hint)

    def gpu_controls(self, layout):
        card, settings = frame()
        heading(settings, "GPU boost")
        scope = label("These settings apply on rear AC in Performance.", "muted")
        scope.setWordWrap(True)
        settings.addWidget(scope)
        self.gpu_options = {}
        for name, text in (("ctgp", "Enable GPU cTGP"), ("gain", "Enable Smart Performance Gain")):
            item = QCheckBox(text)
            item.clicked.connect(self.mark_gpu_dirty)
            settings.addWidget(item)
            self.gpu_options[name] = item
        slider_row = QHBoxLayout()
        self.gain_min = label("—", "muted")
        self.gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.gain_slider.setAccessibleName("Smart Performance Gain wattage")
        self.gain_slider.setAccessibleDescription("Adjust the requested gain in 5 W increments")
        self.gain_slider.setRange(0, 0)
        self.gain_slider.setSingleStep(1)
        self.gain_slider.setPageStep(1)
        self.gain_slider.valueChanged.connect(self.gain_changed)
        self.gain_max = label("—", "muted")
        self.gain_value = label("—", "sectionTitle")
        self.gain_value.setMinimumWidth(56)
        slider_row.addWidget(self.gain_min)
        slider_row.addWidget(self.gain_slider, 1)
        slider_row.addWidget(self.gain_max)
        slider_row.addSpacing(12)
        slider_row.addWidget(self.gain_value, alignment=Qt.AlignmentFlag.AlignRight)
        settings.addLayout(slider_row)
        self.gain_warning = label("", "powerWarning")
        self.gain_warning.setWordWrap(True)
        settings.addWidget(self.gain_warning)
        line = QHBoxLayout()
        self.gpu_reset_button = button("Restore GPU Defaults", lambda: self.start_action("reset-gpu-settings"))
        self.gpu_apply_button = button("Apply GPU Settings", self.apply_gpu_settings, primary=True)
        line.addStretch()
        line.addWidget(self.gpu_reset_button)
        line.addWidget(self.gpu_apply_button)
        settings.addLayout(line)
        layout.addWidget(card)

    def cpu_controls(self, layout):
        self.cpu_editor = CpuProfileEditor(PROFILE_LABELS, frame, label)
        self.cpu_editor.save_requested.connect(lambda settings: self.start_action("save-cpu-profiles", settings))
        self.cpu_editor.restore_requested.connect(lambda: self.start_action("restore-cpu-defaults"))
        self.cpu_editor.maximum_clocks_requested.connect(
            lambda enabled: self.start_action("performance-governor", "performance" if enabled else "powersave")
        )
        layout.addWidget(self.cpu_editor)

        card, readings = frame()
        header = QHBoxLayout()
        heading(header, "Live CPU package limits")
        header.addStretch()
        header.addWidget(label("Power draw", "muted"))
        self.cpu_power = label("—", "metricValue")
        self.cpu_power.setAccessibleName("CPU package power draw")
        header.addWidget(self.cpu_power)
        readings.addLayout(header)
        self.cpu_state = label("Reading CPU limits…", "muted")
        self.cpu_state.setWordWrap(True)
        readings.addWidget(self.cpu_state)
        self.cpu_governor = label("CPU frequency governor · —", "muted")
        readings.addWidget(self.cpu_governor)
        self.cpu_limit_values = limit_table(readings, "Saved target", "MSR", "MMIO")
        layout.addWidget(card)

    def fan_profiles(self, layout):
        self.curves = FanCurveEditor(PROFILE_LABELS, frame, label)
        self.curves.save_requested.connect(lambda settings: self.start_action("save-fan-profiles", settings))
        layout.addWidget(self.curves)

    def fixes(self, layout):
        card, recovery = frame()
        heading(recovery, "Dynamic Boost Recovery")
        self.gpu_recovery_toggle = QCheckBox("Apply 15 W on sufficient rear AC")
        self.gpu_recovery_toggle.setToolTip("Restore NVIDIA’s short-timescale CPU TDP request on rear AC.")
        self.gpu_recovery_toggle.clicked.connect(
            lambda enabled: self.start_action("gpu-recovery", "on" if enabled else "off")
        )
        recovery.addWidget(self.gpu_recovery_toggle)
        self.gpu_recovery_state = label("Checking service…", "muted")
        self.gpu_recovery_state.setWordWrap(True)
        recovery.addWidget(self.gpu_recovery_state)
        layout.addWidget(card)

        card, patch = frame()
        heading(patch, "TuneD Power Saver")
        description = label("Map Power Saver to the firmware’s Cool profile. This updates the local TuneD profile.", "muted")
        description.setWordWrap(True)
        patch.addWidget(description)
        self.tuned_paths = label("", "muted")
        self.tuned_paths.setWordWrap(True)
        self.tuned_paths.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        patch.addWidget(self.tuned_paths)
        line = QHBoxLayout()
        self.tuned_state = label("Checking TuneD…")
        line.addWidget(self.tuned_state, 1)
        self.tuned_button = button("Apply Cool Patch", lambda: self.start_action("apply-tuned-cool"), primary=True)
        line.addWidget(self.tuned_button)
        patch.addLayout(line)
        layout.addWidget(card)

        card, recovery = frame()
        heading(recovery, "USB-C port status")
        description = label("Retry the USB-C controller after a matching boot timeout. Dock devices may briefly reconnect.", "muted")
        description.setWordWrap(True)
        recovery.addWidget(description)
        self.usb_button = button("Recover USB-C Status", lambda: self.start_action("recover-usb-c"))
        recovery.addWidget(self.usb_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(card)

    def services(self, layout):
        card, service = frame()
        heading(service, "OMEN AC power service")
        self.service_text = label("Checking service…", "muted")
        self.service_text.setWordWrap(True)
        service.addWidget(self.service_text)
        line = QHBoxLayout()
        self.install_button = button("Install Service", lambda: self.start_action("install-service"), primary=True)
        self.restart_button = button("Restart Service", lambda: self.start_action("restart-service"))
        for item in (self.install_button, self.restart_button):
            line.addWidget(item)
        line.addStretch()
        service.addLayout(line)
        layout.addWidget(card)

        activity_card, activity = frame()
        heading(activity, "Service activity")
        self.journal = log_view()
        self.journal.setAccessibleName("Recent OMEN service journal")
        self.journal.setMinimumHeight(190)
        activity.addWidget(self.journal)
        self.action_log = log_view()
        self.action_log.setAccessibleName("Service operation output")
        self.action_log.setPlaceholderText("Operation output")
        self.action_log.setMaximumHeight(110)
        activity.addWidget(self.action_log)
        layout.addWidget(activity_card)

    def mark_profile_dirty(self):
        self.profile_dirty = True

    def mark_gpu_dirty(self):
        self.gpu_dirty = True
        self.update_buttons()

    def gain_changed(self):
        watts = self.gain_steps[self.gain_slider.value()]
        self.gain_value.setText(f"+{watts} W")
        self.mark_gpu_dirty()

    def apply_profile(self):
        if profile := self.profile_choice.currentData():
            self.start_action("profile", profile)

    def apply_gpu_settings(self):
        args = []
        for name, item in self.gpu_options.items():
            args.extend(("--" + name, "on" if item.isChecked() else "off"))
        args.extend(("--gain-watts", str(self.gain_steps[self.gain_slider.value()])))
        self.start_action("gpu-settings", *args)

    def set_message(self, text, *, error=False):
        self.message.setText(text)
        update_property(self.message, "error", error)

    def refresh(self):
        if self.status_process.state() == QProcess.ProcessState.NotRunning:
            self.status_process.start("/usr/bin/python3", ["-I", str(STATUS_HELPER), "status"])

    def status_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.set_message(self.status_process.errorString(), error=True)

    def status_finished(self, code, _exit_status):
        output = bytes(self.status_process.readAllStandardOutput())
        error = bytes(self.status_process.readAllStandardError()).decode(errors="replace").strip()
        if code:
            self.set_message(error or "Could not read system status.", error=True)
            self.state = {}
            self.update_buttons()
            return
        try:
            state = json.loads(output)
        except ValueError:
            self.set_message("Could not parse system status.", error=True)
            return
        first = not self.state
        self.render(state)
        if first and not self.active_action:
            if not state["supported"]:
                self.set_message("Hardware controls require HP board 8C58, SKU 9R293EA#ABU and BIOS F.13.", error=True)
            elif not HELPER.is_file():
                self.set_message("Install the desktop app to enable administrator controls.")
            else:
                self.set_message("Ready")
        self.offer_service_install()

    def offer_service_install(self):
        if (self.service_install_offered or self.active_action or self.state.get("installed", True)
                or not self.state.get("supported") or not HELPER.is_file() or not PKEXEC.is_file()):
            return
        self.service_install_offered = True
        prompt = QMessageBox(QMessageBox.Icon.Question, "Install the OMEN service?",
                             "The service applies your CPU, fan and GPU settings. Installation may download "
                             "Fedora build tools and build kernel modules for this laptop. "
                             "Administrator authentication follows.", parent=self)
        install = prompt.addButton("Install Service", QMessageBox.ButtonRole.AcceptRole)
        later = prompt.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        prompt.setDefaultButton(later)
        prompt.exec()
        if prompt.clickedButton() == install:
            self.start_action("install-service")

    def render(self, state):
        self.state = state
        model = state["model"] or "HP OMEN"
        self.model.setText(f'{model}  ·  BIOS {state["bios"] or "—"}')
        running = state["service"].get("ActiveState") == "active"
        self.service_badge.setText("Service running" if running else "Service stopped" if state["installed"] else "Service missing")
        update_property(self.service_badge, "good", running)
        rear = state["rear"]
        temp = state["cpu_temperature"]
        for widget, value in zip(self.quick_values,
                                 ("Connected" if rear == "1" else "Disconnected" if rear == "0" else "Unavailable",
                                  PROFILE_LABELS.get(state["profile"], state["profile"] or "Unavailable"),
                                  f"{temp / 1000:.0f} °C" if temp is not None else "—"), strict=True):
            widget.setText(value)
        for row, widgets in enumerate(self.limit_values):
            for widget, values in zip(widgets, (state["msr"], state["mmio"]), strict=True):
                value = values[row]
                widget.setText(f"{value / 1e6:g} W" if value is not None else "—")
        actual = state.get("manual_fan")
        status = state.get("control_status") or {}
        self.render_cpu(state, status, running)
        fan_selection = status.get("fan_selection", "")
        if actual:
            text = f"{actual}%"
            if fan_selection.startswith("curve:"):
                text += f" · {fan_selection[6:]}"
        else:
            text = FAN_LABELS.get(state["fan_mode"], "—")
            if fan_selection == "default-performance":
                text += " · Default performance"
        self.fan_readback.setText(text)
        for widget, value in zip((self.fan1_readback, self.fan2_readback), state["fans"], strict=True):
            widget.setText(f"{value:,} RPM" if value is not None else "—")
        ctgp = "On" if state["ctgp"] == 1 else "Off" if state["ctgp"] == 0 else "Unavailable"
        gain = f'+{state["gain"]} W' if state["gain"] else "Off" if state["gain"] == 0 else "Unavailable"
        powerd = state["powerd"].title()
        self.gpu_flags.setText(f"cTGP  {ctgp}     ·     Smart Performance Gain  {gain}     ·     Dynamic Boost  {powerd}")

        if rear is None and state["ac"] == "1":
            hint = "Linux has AC power but no rear-port readback. See USB-C recovery on the Fixes tab."
        elif rear != "1":
            hint = "Connect the rear charger to use the Performance GPU boost."
        elif state["profile"] != "performance":
            hint = "GPU boost activates when the EC profile is Performance."
        else:
            hint = ""
        self.policy_hint.setText(hint)
        self.policy_hint.setVisible(bool(hint))
        selected = self.profile_choice.currentData()
        available = [name for name in PROFILE_LABELS if name in state["profiles"]]
        current_choices = [self.profile_choice.itemData(i) for i in range(self.profile_choice.count())]
        if available != current_choices:
            self.profile_choice.clear()
            for name in available:
                self.profile_choice.addItem(PROFILE_LABELS[name], name)
        selection = selected if self.profile_dirty and selected in available else state["profile"]
        index = self.profile_choice.findData(selection)
        if index >= 0:
            self.profile_choice.setCurrentIndex(index)
        if not self.gpu_dirty:
            for name, item in self.gpu_options.items():
                item.setChecked(state["controls"][name])
            steps = tuple(state.get("gain_steps", ()))
            if steps:
                default = state["default_gain_watts"]
                watts = state["controls"].get("gain_watts", default)
                self.gain_steps = steps
                with QSignalBlocker(self.gain_slider):
                    self.gain_slider.setRange(0, len(steps) - 1)
                    self.gain_slider.setValue(steps.index(watts))
                self.gain_min.setText(f"+{steps[0]} W")
                self.gain_max.setText(f"+{steps[-1]} W")
                self.gain_value.setText(f"+{watts} W")
                self.gain_warning.setText(f"Default is +{default} W. Use higher gains at your own risk.")

        self.curves.load(state.get("fan_profiles"), state.get("fan_profile_error"))
        recovery_enabled = state.get("gpu_recovery_enabled", False)
        if self.active_action != "gpu-recovery":
            self.gpu_recovery_toggle.setChecked(recovery_enabled)
        if not state["current"]:
            recovery = "Update the OMEN service to use this fix."
        elif not running:
            recovery = "Service stopped"
        else:
            recovery = status.get("gpu_recovery") or "Checking service…"
        self.gpu_recovery_state.setText(recovery)
        fix = state.get("tuned_fix", {})
        self.tuned_state.setText({"applied": "Already applied", "available": "Patch available"}.get(fix.get("state"), "Unavailable"))
        self.tuned_paths.setText(fix.get("detail", "\n".join(fix.get("paths", []))))
        if state["installed"]:
            boot = "Enabled at boot" if state["service"].get("UnitFileState") == "enabled" else "Manual start"
            text = f'{self.service_badge.text()} · {boot}'
            if not state["current"] or not state.get("fan_driver") or not state.get("gain_driver"):
                text += " · Service update available"
            elif state.get("gain_reload_pending"):
                text += " · GPU gain driver reload pending"
        else:
            text = "Install the service to manage CPU profiles, cooling and rear-AC GPU boost."
        self.service_text.setText(text)
        log = state["journal"] or "No OMEN service journal entries are visible for this boot."
        if self.journal.toPlainText() != log:
            self.journal.setPlainText(log)
            self.journal.verticalScrollBar().setValue(self.journal.verticalScrollBar().maximum())
        self.update_buttons()

    def render_cpu(self, state, status, running):
        config = state.get("cpu_profiles")
        error = state.get("cpu_profile_error")
        self.cpu_editor.load(config, state.get("cpu_defaults"), error, state.get("cpu_max_watts", 1), state["profile"])
        self.cpu_editor.show_performance_governor(state.get("performance_governor", {}),
                                                  pending=self.active_action == "performance-governor")
        governors = " / ".join(state.get("cpu_governors", [])) or "—"
        self.cpu_governor.setText(f"CPU frequency governor · {governors}")
        power_draw = status.get("cpu_power_watts")
        self.cpu_power.setText(f"{power_draw:.1f} W" if power_draw is not None else "—")
        profile = config["assignments"].get(state["profile"]) if config and not error else None
        watts = config["profiles"].get(profile, {}) if config else {}
        target = [watts[key] * 1000000 if key in watts else None for key in CPU_LIMIT_LABELS]
        for row, widgets in enumerate(self.cpu_limit_values):
            for widget, values in zip(widgets, (target, state["msr"], state["mmio"]), strict=True):
                value = values[row]
                widget.setText(f"{value / 1e6:g} W" if value is not None else "—")

        if error:
            message = "The saved CPU profiles could not be read."
        elif not running or not state["current"]:
            message = "Install or start the updated service to apply CPU profiles."
        elif status.get("cpu_error"):
            message = status["cpu_error"]
        elif profile is None:
            message = "Waiting for a supported EC mode."
        elif status.get("cpu_pending") or status.get("cpu_profile") != profile or status.get("cpu_target") != watts:
            message = f"Applying {profile}…"
        elif state["msr"] == target and state["mmio"] == target:
            message = f"{profile} · Applied"
        else:
            message = f"{profile} · Live limits differ from saved target"
        self.cpu_state.setText(message)

    def update_buttons(self):
        state = self.state
        installed = state.get("installed", False)
        current = state.get("current", False)
        running = state.get("service", {}).get("ActiveState") == "active"
        free = self.active_action is None
        admin = free and bool(state.get("supported")) and HELPER.is_file() and PKEXEC.is_file()
        ready = admin and current and running and bool(state.get("control_status"))
        update_available = not current or not state.get("fan_driver") or not state.get("gain_driver")
        self.install_button.setText("Update Service" if installed else "Install Service")
        self.install_button.setVisible(update_available)
        self.install_button.setEnabled(admin)
        self.restart_button.setText("Restart Service" if running else "Start Service")
        self.restart_button.setEnabled(admin and installed)
        self.apply_button.setEnabled(admin and current and (ready or not running)
                                     and state.get("ac") == state.get("rear") == "1")
        self.gpu_recovery_toggle.setEnabled(admin and current)
        self.usb_button.setEnabled(admin and state.get("ac") == "1" and state.get("rear") is None and state.get("front") is None)
        self.tuned_button.setEnabled(admin and state.get("tuned_fix", {}).get("state") == "available")
        self.set_profile_button.setEnabled(admin and self.profile_choice.count() > 0)
        self.profile_choice.setEnabled(self.set_profile_button.isEnabled())
        self.gpu_reset_button.setEnabled(ready)
        self.gpu_apply_button.setEnabled(ready and bool(self.gain_steps))
        self.gain_slider.setEnabled(ready and bool(self.gain_steps) and self.gpu_options["gain"].isChecked())
        fan_driver = bool(state.get("fan_driver"))
        self.cpu_editor.setEnabled(self.active_action not in ("save-cpu-profiles", "restore-cpu-defaults"))
        self.cpu_editor.set_save_available(admin and current)
        self.cpu_editor.maximum_clocks.setEnabled(admin and state.get("performance_governor", {}).get("available", False))
        self.curves.setEnabled(self.active_action != "save-fan-profiles")
        self.curves.set_save_available(admin and current and fan_driver)
        for option in self.gpu_options.values():
            option.setEnabled(ready)
        self.sample_button.setEnabled(free and bool(state) and Path("/usr/bin/nvidia-smi").is_file())

    def start_action(self, name, *args, admin=True):
        if self.active_action is not None:
            return
        self.active_action = name
        self.output.clear()
        self.action_log.clear()
        if name == "install-service":
            self.tabs.setCurrentWidget(self.service_page)
        self.update_buttons()
        if admin:
            self.set_message("Waiting for administrator authentication or service confirmation…")
            self.process.start(str(PKEXEC), [str(HELPER), name, *args])
        else:
            self.set_message("Reading GPU telemetry…")
            self.process.start("/usr/bin/python3", ["-I", str(STATUS_HELPER), name, *args])

    def collect_output(self):
        chunk = bytes(self.process.readAllStandardOutput())
        self.output.extend(chunk)
        if len(self.output) > 100_000:
            del self.output[:-100_000]
        if self.active_action != "sample-gpu" and chunk:
            self.action_log.setPlainText(self.output.decode(errors="replace").replace("\r", "\n").strip())
            self.action_log.verticalScrollBar().setValue(self.action_log.verticalScrollBar().maximum())

    def action_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.set_message(self.process.errorString(), error=True)
            self.active_action = None
            self.update_buttons()

    def action_finished(self, code, _exit_status):
        self.collect_output()
        action, self.active_action = self.active_action, None
        details = self.output.decode(errors="replace").strip()
        if action == "install-service" and code == KEY_ENROLLMENT_REQUIRED:
            self.set_message("Secure Boot key enrollment is needed before the service can be installed.")
            QMessageBox.information(self, "Enroll the local signing key",
                                    "Run this command in a terminal and choose a one-time enrollment password.\n\n"
                                    "sudo mokutil --import /etc/pki/akmods/certs/public_key.der\n\n"
                                    "Reboot, select Enroll MOK, and enter that password. Open OMEN Power Control "
                                    "again and choose Install Service.")
        elif code:
            if code == 126:
                self.set_message("Administrator authentication canceled.")
            else:
                self.set_message(details.splitlines()[-1] if details else "The operation did not complete.", error=True)
        elif action == "sample-gpu":
            self.show_gpu_sample(details)
        else:
            if action == "save-fan-profiles":
                self.curves.accept_save()
            if action == "save-cpu-profiles":
                self.cpu_editor.accept_save()
            if action == "restore-cpu-defaults":
                self.cpu_editor.accept_restore()
            if action in ("reset-gpu-settings", "apply-fixes", "gpu-settings"):
                self.gpu_dirty = False
            if action in ("profile", "apply-fixes"):
                self.profile_dirty = False
            self.set_message(details.splitlines()[-1] if details else "Settings applied.")
        self.update_buttons()
        self.refresh()

    def show_gpu_sample(self, details):
        try:
            sample = json.loads(details)
            self.sample_result.setText(
                f'{sample["draw"]} draw   ·   {sample["limit"]} ceiling   ·   {sample["utilization"]} load'
                f'   ·   {sample["pstate"]}   ·   SW Power Cap {sample["power_cap"]}'
            )
            self.set_message("GPU sample captured.")
        except (ValueError, KeyError, TypeError):
            self.set_message("Could not parse NVIDIA telemetry.", error=True)

    def closeEvent(self, event):
        if self.active_action is not None:
            self.set_message("Wait for the current operation to finish before closing.")
            event.ignore()
        else:
            super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(TITLE)
    app.setDesktopFileName("org.omenpower.Control")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = Window()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
