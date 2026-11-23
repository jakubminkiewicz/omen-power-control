"""CPU power-limit profile editor for OMEN Power Control."""

from copy import deepcopy
import json

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QInputDialog, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)


LIMIT_LABELS = {'pl1': 'PL1 · sustained', 'pl2': 'PL2 · short term', 'pl4': 'PL4 · peak'}
DEFAULT_LABEL = 'Default'


class CpuProfileEditor(QWidget):
    save_requested = Signal(str)
    restore_requested = Signal()
    maximum_clocks_requested = Signal(bool)

    def __init__(self, profile_labels, frame, label):
        super().__init__()
        self.saved = self.draft = None
        self.factory = None
        self.active_mode = None
        self.config_error = None
        self.can_save = False
        self.current_profile = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        assignments, contents = frame()
        header = QHBoxLayout()
        header.addWidget(label('CPU power by EC mode', 'sectionTitle'), 1)
        self.restore_button = QPushButton('Restore to Defaults')
        self.restore_button.clicked.connect(self.restore_defaults)
        header.addWidget(self.restore_button)
        self.save_button = QPushButton('Save CPU Profiles')
        self.save_button.setObjectName('primary')
        self.save_button.clicked.connect(self.save)
        header.addWidget(self.save_button)
        contents.addLayout(header)

        selectors = QGridLayout()
        self.assignments = {}
        self.maximum_clocks = QCheckBox('Always request max clock speed\n(not recommended)')
        self.maximum_clocks.clicked.connect(lambda checked: self.maximum_clocks_requested.emit(checked))
        for column, (key, title) in enumerate(profile_labels.items()):
            choice = QComboBox()
            choice.setAccessibleName(f'{title} CPU profile')
            choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            choice.activated.connect(lambda _index, profile=key: self.set_assignment(profile))
            selectors.addWidget(label(title, 'muted'), 0, column)
            selectors.addWidget(choice, 1, column)
            if key == 'performance':
                selectors.addWidget(self.maximum_clocks, 2, column)
            selectors.setColumnStretch(column, 1)
            self.assignments[key] = choice
        contents.addLayout(selectors)
        self.save_status = label('', 'muted')
        self.save_status.setWordWrap(True)
        contents.addWidget(self.save_status)
        layout.addWidget(assignments)

        editor, contents = frame()
        header = QHBoxLayout()
        header.addWidget(label('Power limit profile', 'sectionTitle'))
        self.profile_choice = QComboBox()
        self.profile_choice.setAccessibleName('CPU profile to edit')
        self.profile_choice.activated.connect(self.select_profile)
        header.addWidget(self.profile_choice, 1)
        self.new_button = QPushButton('New Profile')
        self.new_button.clicked.connect(self.add_profile)
        self.delete_button = QPushButton('Delete Profile')
        self.delete_button.clicked.connect(self.delete_profile)
        header.addWidget(self.new_button)
        header.addWidget(self.delete_button)
        contents.addLayout(header)

        warning = label('Change CPU power limits only if you know what you’re doing. '
                        'Higher limits can increase heat, power draw, and battery drain.', 'powerWarning')
        warning.setWordWrap(True)
        contents.addWidget(warning)

        grid = QGridLayout()
        self.limits = {}
        for column, (key, title) in enumerate(LIMIT_LABELS.items()):
            field = QSpinBox()
            field.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            field.setRange(1, 1)
            field.setSuffix(' W')
            field.setAccessibleName(title)
            field.valueChanged.connect(lambda watts, limit=key: self.set_limit(limit, watts))
            grid.addWidget(label(title, 'muted'), 0, column)
            grid.addWidget(field, 1, column)
            grid.setColumnStretch(column, 1)
            self.limits[key] = field
        contents.addLayout(grid)
        self.factory_hint = label('Factory profile. Create a new profile to change these limits.', 'muted')
        self.factory_hint.setWordWrap(True)
        contents.addWidget(self.factory_hint)
        layout.addWidget(editor)
        self.show_profile()
        self.update_save()

    def load(self, config, defaults, error, max_watts, active_mode):
        self.active_mode = active_mode
        if config is None or defaults is None or (self.draft is not None and self.draft != self.saved):
            return
        if config == self.saved and defaults == self.factory and error == self.config_error:
            return
        if self.draft is None:
            self.current_profile = config['assignments'].get(active_mode)
        self.saved = deepcopy(config)
        self.draft = deepcopy(config)
        self.factory = defaults
        self.config_error = error
        for field in self.limits.values():
            with QSignalBlocker(field):
                field.setMaximum(max_watts)
        self.rebuild_choices()

    def rebuild_choices(self):
        names = sorted(self.draft['profiles'], key=str.casefold)
        if self.current_profile not in names:
            self.current_profile = names[0] if names else None
        self.profile_choice.clear()
        for name in names:
            self.profile_choice.addItem(name, name)
        self.profile_choice.setCurrentIndex(self.profile_choice.findData(self.current_profile))
        for mode, choice in self.assignments.items():
            choice.clear()
            default_name = self.factory['assignments'][mode]
            watts = self.factory['profiles'][default_name]
            choice.addItem(DEFAULT_LABEL, default_name)
            values = ' / '.join(str(watts[key]) for key in LIMIT_LABELS)
            choice.setItemData(0, f'{default_name} uses {values} W for PL1, PL2 and PL4.', Qt.ItemDataRole.ToolTipRole)
            for name in names:
                if name == default_name:
                    continue
                title = f'Profile · {name}' if name.casefold() == DEFAULT_LABEL.casefold() else name
                choice.addItem(title, name)
            selected = self.draft['assignments'][mode] or default_name
            choice.setCurrentIndex(choice.findData(selected))
        self.show_profile()
        self.update_save()

    def select_profile(self):
        self.current_profile = self.profile_choice.currentData()
        self.show_profile()

    def show_profile(self):
        watts = self.draft['profiles'].get(self.current_profile) if self.draft else None
        factory_profile = self.factory is not None and self.current_profile in self.factory['profiles']
        self.profile_choice.setEnabled(watts is not None)
        self.new_button.setEnabled(self.draft is not None)
        self.delete_button.setEnabled(watts is not None and not factory_profile)
        self.factory_hint.setVisible(factory_profile)
        for key, field in self.limits.items():
            field.setEnabled(watts is not None)
            field.setReadOnly(factory_profile)
            if watts is not None:
                with QSignalBlocker(field):
                    field.setValue(watts[key])

    def set_assignment(self, mode):
        self.draft['assignments'][mode] = self.assignments[mode].currentData()
        self.update_save()

    def show_performance_governor(self, setting, *, pending=False):
        if not pending:
            self.maximum_clocks.setChecked(setting.get('governor') == 'performance')
        self.maximum_clocks.setToolTip(setting.get('detail') or
                                      'When unchecked, Performance uses powersave and boosts as needed.')

    def set_limit(self, limit, watts):
        if self.current_profile in self.factory['profiles']:
            return
        self.draft['profiles'][self.current_profile][limit] = watts
        self.update_save()

    def add_profile(self):
        name, accepted = QInputDialog.getText(self, 'New CPU Profile', 'Profile name')
        if not accepted:
            return
        name = name.strip()
        if not name or len(name) > 48 or not name.isprintable() or name in self.draft['profiles']:
            QMessageBox.warning(self, 'Profile Name', 'Enter a unique name with 1 to 48 printable characters.')
            return
        self.draft['profiles'][name] = {key: field.value() for key, field in self.limits.items()}
        self.current_profile = name
        self.rebuild_choices()

    def delete_profile(self):
        if self.current_profile in self.factory['profiles']:
            return
        del self.draft['profiles'][self.current_profile]
        for mode, name in self.draft['assignments'].items():
            if name == self.current_profile:
                self.draft['assignments'][mode] = self.factory['assignments'][mode]
        self.rebuild_choices()

    def set_save_available(self, available):
        self.can_save = available
        self.update_save()

    def update_save(self):
        dirty = self.draft != self.saved
        self.save_button.setEnabled(self.can_save and self.draft is not None and (dirty or self.config_error is not None))
        changed_defaults = self.draft != self.factory or self.saved != self.factory or self.config_error is not None
        self.restore_button.setEnabled(self.can_save and self.factory is not None and changed_defaults)
        self.save_status.setText('Unsaved changes' if dirty else self.config_error or '')
        self.save_status.setVisible(bool(self.save_status.text()))

    def save(self):
        self.save_requested.emit(json.dumps(self.draft, separators=(',', ':')))

    def restore_defaults(self):
        answer = QMessageBox.question(self, 'Restore CPU Defaults',
                                      'Restore the factory CPU assignments? '
                                      'This removes custom CPU profiles and discards unsaved CPU edits.',
                                      defaultButton=QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.restore_requested.emit()

    def accept_save(self):
        self.saved = deepcopy(self.draft)
        self.config_error = None
        self.update_save()

    def accept_restore(self):
        self.draft = deepcopy(self.factory)
        self.current_profile = self.factory['assignments'].get(self.active_mode)
        self.accept_save()
        self.rebuild_choices()
