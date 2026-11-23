"""Interactive CPU-temperature fan curves for OMEN Power Control."""

from copy import deepcopy
import json

from PySide6.QtCore import QPointF, QRectF, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QInputDialog, QMessageBox, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)


MIN_TEMP, MAX_TEMP = 20, 100
MIN_SPEED, MAX_SPEED = 35, 100
MAX_POINTS = 20
DEFAULT_POINTS = [[35, 35], [55, 50], [70, 75], [82, 100]]
FAN_MODES = {'automatic': 'Firmware automatic', 'default-performance': 'Default performance', 'maximum': 'Maximum'}


class CurveGraph(QWidget):
    point_added = Signal(int, int)
    point_selected = Signal(int)
    point_moved = Signal(int, int)

    def __init__(self):
        super().__init__()
        self.points, self.selected = [], -1
        self.dragging = False
        self.setMinimumHeight(242)
        self.setAccessibleName('Fan curve graph, CPU temperature against fan speed')

    def show_points(self, points, selected):
        self.points, self.selected = points, selected
        self.update()

    def plot_rect(self):
        return QRectF(self.rect()).adjusted(74, 17, -28, -51)

    def coord(self, plot, temp, speed):
        x = plot.left() + (temp - MIN_TEMP) * plot.width() / (MAX_TEMP - MIN_TEMP)
        y = plot.bottom() - (speed - MIN_SPEED) * plot.height() / (MAX_SPEED - MIN_SPEED)
        return QPointF(x, y)

    def values(self, plot, position):
        temp = MIN_TEMP + (position.x() - plot.left()) * (MAX_TEMP - MIN_TEMP) / plot.width()
        speed = MIN_SPEED + (plot.bottom() - position.y()) * (MAX_SPEED - MIN_SPEED) / plot.height()
        return min(MAX_TEMP, max(MIN_TEMP, round(temp))), min(MAX_SPEED, max(MIN_SPEED, round(speed)))

    def paintEvent(self, _event):
        plot = self.plot_rect()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#151c27'))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0, 0, -1, -1), 8, 8)
        grid, text = QColor('#334056'), QColor('#afbdd0')
        for temp in range(MIN_TEMP, MAX_TEMP + 1, 10):
            x = self.coord(plot, temp, MIN_SPEED).x()
            painter.setPen(grid)
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.setPen(text)
            painter.drawText(QRectF(x - 20, plot.bottom() + 4, 40, 20), Qt.AlignmentFlag.AlignCenter, str(temp))
        for speed in (35, 50, 60, 70, 80, 90, 100):
            y = self.coord(plot, MIN_TEMP, speed).y()
            painter.setPen(grid)
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(text)
            painter.drawText(QRectF(24, y - 9, 42, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(speed))

        painter.drawText(QRectF(plot.left(), plot.bottom() + 25, plot.width(), 20),
                         Qt.AlignmentFlag.AlignCenter, 'CPU temperature (°C)')
        painter.save()
        painter.translate(17, plot.center().y())
        painter.rotate(-90)
        painter.drawText(QRectF(-plot.height() / 2, -10, plot.height(), 20),
                         Qt.AlignmentFlag.AlignCenter, 'Fan speed (%)')
        painter.restore()

        if not self.points:
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, 'Add a profile to start a curve')
            return
        nodes = [self.coord(plot, *point) for point in self.points]
        teal = QColor('#64d9c3')
        painter.setPen(QPen(QColor('#4a978e'), 1.5, Qt.PenStyle.DashLine))
        painter.drawLine(self.coord(plot, MIN_TEMP, self.points[0][1]), nodes[0])
        painter.drawLine(nodes[-1], self.coord(plot, MAX_TEMP, self.points[-1][1]))
        painter.setPen(QPen(teal, 2.5))
        painter.drawPolyline(QPolygonF(nodes))
        for index, node in enumerate(nodes):
            selected = index == self.selected
            painter.setPen(QPen(QColor('#f2f8ff') if selected else QColor('#151c27'), 1.5))
            painter.setBrush(QColor('#f2f8ff') if selected else teal)
            size = 5.5 if selected else 4.5
            painter.drawEllipse(node, size, size)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.points:
            return
        plot = self.plot_rect()
        position = event.position()
        if not plot.adjusted(-8, -8, 8, 8).contains(position):
            return
        distance = [(self.coord(plot, *point) - position).manhattanLength() for point in self.points]
        nearest = distance.index(min(distance))
        if distance[nearest] <= 11:
            self.point_selected.emit(nearest)
        elif len(self.points) < MAX_POINTS:
            self.point_added.emit(*self.values(plot, position))
        else:
            return
        self.dragging = True

    def mouseMoveEvent(self, event):
        if self.dragging and event.buttons() & Qt.MouseButton.LeftButton:
            self.point_moved.emit(*self.values(self.plot_rect(), event.position()))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False


class FanCurveEditor(QWidget):
    save_requested = Signal(str)

    def __init__(self, profile_labels, frame, label):
        super().__init__()
        self.saved = self.draft = None
        self.config_error = None
        self.can_save = False
        self.current_curve = None
        self.selected = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        assignments, contents = frame()
        header = QHBoxLayout()
        header.addWidget(label('Cooling by EC profile', 'sectionTitle'), 1)
        self.save_button = QPushButton('Save Fan Settings')
        self.save_button.setObjectName('primary')
        self.save_button.clicked.connect(self.save)
        header.addWidget(self.save_button)
        contents.addLayout(header)
        selectors = QGridLayout()
        self.assignments = {}
        for column, (key, title) in enumerate(profile_labels.items()):
            choice = QComboBox()
            choice.setAccessibleName(f'{title} fan mode')
            choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            choice.activated.connect(lambda _index, profile=key: self.set_assignment(profile))
            selectors.addWidget(label(title, 'muted'), 0, column)
            selectors.addWidget(choice, 1, column)
            selectors.setColumnStretch(column, 1)
            self.assignments[key] = choice
        contents.addLayout(selectors)
        refresh_row = QHBoxLayout()
        self.interval = QSpinBox()
        self.interval.setRange(1, 60)
        self.interval.setValue(3)
        self.interval.setSuffix(' s')
        self.interval.setAccessibleName('Curve refresh interval')
        self.interval.setToolTip('How often the service recalculates the fan target from the active curve.')
        self.interval.valueChanged.connect(self.set_interval)
        refresh_row.addWidget(label('Curve refresh interval'))
        refresh_row.addWidget(self.interval)
        refresh_row.addStretch()
        self.save_status = label('', 'muted')
        refresh_row.addWidget(self.save_status)
        contents.addLayout(refresh_row)
        layout.addWidget(assignments)

        editor, contents = frame()
        header = QHBoxLayout()
        heading = label('Fan curve', 'sectionTitle')
        self.curve_choice = QComboBox()
        self.curve_choice.setAccessibleName('Fan profile')
        self.curve_choice.activated.connect(self.select_curve)
        self.add_curve_button = QPushButton('New Profile')
        self.add_curve_button.clicked.connect(self.add_curve)
        self.delete_curve_button = QPushButton('Delete Profile')
        self.delete_curve_button.clicked.connect(self.delete_curve)
        header.addWidget(heading)
        header.addWidget(self.curve_choice, 1)
        header.addWidget(self.add_curve_button)
        header.addWidget(self.delete_curve_button)
        contents.addLayout(header)

        self.builtin_details = label(
            'Firmware automatic cooling switches to maximum after the CPU package stays above 82°C '
            'for 8 seconds. It returns to automatic after 25 seconds below 70°C.',
            'muted',
        )
        self.builtin_details.setWordWrap(True)
        contents.addWidget(self.builtin_details)

        self.graph = CurveGraph()
        self.graph.point_added.connect(self.add_point)
        self.graph.point_selected.connect(self.select_point)
        self.graph.point_moved.connect(self.move_point)
        contents.addWidget(self.graph)

        row = QHBoxLayout()
        self.point_number = QSpinBox()
        self.point_number.setPrefix('Point ')
        self.point_number.setAccessibleName('Selected fan curve point')
        self.point_number.valueChanged.connect(lambda number: self.select_point(number - 1))
        self.point_temp, self.point_speed = QSpinBox(), QSpinBox()
        self.point_temp.setSuffix(' °C')
        self.point_speed.setSuffix(' %')
        self.point_temp.setAccessibleName('Point CPU temperature')
        self.point_speed.setAccessibleName('Point fan speed')
        self.point_speed.setRange(MIN_SPEED, MAX_SPEED)
        self.point_temp.valueChanged.connect(lambda temp: self.move_point(temp, self.point_speed.value()))
        self.point_speed.valueChanged.connect(lambda speed: self.move_point(self.point_temp.value(), speed))
        self.add_point_button = QPushButton('Add Point')
        self.add_point_button.clicked.connect(self.add_point_between)
        self.remove_point_button = QPushButton('Remove Point')
        self.remove_point_button.clicked.connect(self.remove_point)
        self.interval.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        for item in (self.point_number, self.point_temp, self.point_speed):
            item.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            row.addWidget(item)
        row.addStretch()
        row.addWidget(self.add_point_button)
        row.addWidget(self.remove_point_button)
        contents.addLayout(row)

        self.point_hint = label('', 'muted')
        contents.addWidget(self.point_hint)
        layout.addWidget(editor)
        self.show_point()
        self.update_save()

    def load(self, config, error):
        if config is None or (self.draft is not None and self.draft != self.saved):
            return
        if config == self.saved and error == self.config_error:
            return
        if self.draft is None:
            self.current_curve = min(config['curves'], key=str.casefold, default=None)
        self.saved = deepcopy(config)
        self.draft = deepcopy(config)
        self.config_error = error
        with QSignalBlocker(self.interval):
            self.interval.setValue(config['interval'])
        self.rebuild_choices()

    def rebuild_choices(self):
        curves = sorted(self.draft['curves'], key=str.casefold)
        self.curve_choice.clear()
        builtin = FAN_MODES['default-performance']
        self.curve_choice.addItem(builtin)
        for name in curves:
            self.curve_choice.addItem(f'Curve · {name}' if name.casefold() == builtin.casefold() else name, name)
        if self.current_curve is not None and self.current_curve not in curves:
            self.current_curve = curves[0] if curves else None
            self.selected = 0
        if self.current_curve:
            self.curve_choice.setCurrentIndex(self.curve_choice.findData(self.current_curve))
        for key, choice in self.assignments.items():
            choice.clear()
            for mode, title in FAN_MODES.items():
                choice.addItem(title, mode)
            for name in curves:
                choice.addItem(f'Curve · {name}', f'curve:{name}')
            choice.setCurrentIndex(choice.findData(self.draft['assignments'][key]))
        self.show_point()
        self.update_save()

    def set_assignment(self, profile):
        self.draft['assignments'][profile] = self.assignments[profile].currentData()
        self.update_save()

    def set_interval(self, interval):
        if self.draft is not None:
            self.draft['interval'] = interval
            self.update_save()

    def select_curve(self):
        self.current_curve = self.curve_choice.currentData()
        self.selected = 0
        self.show_point()

    def add_curve(self):
        name, accepted = QInputDialog.getText(self, 'New Fan Profile', 'Profile name')
        if not accepted:
            return
        name = name.strip()
        if not name or len(name) > 48 or not name.isprintable() or name in self.draft['curves']:
            QMessageBox.warning(self, 'Profile Name', 'Enter a unique name with 1 to 48 printable characters.')
            return
        self.draft['curves'][name] = deepcopy(DEFAULT_POINTS)
        self.current_curve, self.selected = name, 0
        self.rebuild_choices()

    def delete_curve(self):
        name = self.current_curve
        del self.draft['curves'][name]
        for profile, choice in self.draft['assignments'].items():
            if choice == f'curve:{name}':
                self.draft['assignments'][profile] = 'automatic'
        self.rebuild_choices()

    def points(self):
        return self.draft['curves'][self.current_curve] if self.current_curve else []

    def temperature_range(self, points):
        low = points[self.selected - 1][0] + 1 if self.selected > 0 else MIN_TEMP
        high = points[self.selected + 1][0] - 1 if self.selected + 1 < len(points) else MAX_TEMP
        return low, high

    def show_point(self):
        points = self.points()
        if points:
            self.selected = min(self.selected, len(points) - 1)
            temp, speed = points[self.selected]
            with QSignalBlocker(self.point_number), QSignalBlocker(self.point_temp), QSignalBlocker(self.point_speed):
                self.point_number.setRange(1, len(points))
                self.point_number.setValue(self.selected + 1)
                self.point_temp.setRange(*self.temperature_range(points))
                self.point_temp.setValue(temp)
                self.point_speed.setValue(speed)
        self.graph.show_points(points, self.selected if points else -1)
        self.graph.setVisible(bool(points))
        self.builtin_details.setVisible(self.draft is not None and not points)
        self.add_curve_button.setEnabled(self.draft is not None)
        self.delete_curve_button.setEnabled(bool(points))
        self.curve_choice.setEnabled(self.draft is not None)
        for item in (self.point_number, self.point_temp, self.point_speed, self.add_point_button,
                     self.remove_point_button, self.point_hint):
            item.setVisible(bool(points))
        self.add_point_button.setEnabled(0 < len(points) < MAX_POINTS)
        self.remove_point_button.setEnabled(len(points) > 2)
        self.point_hint.setText(f'{len(points)} / {MAX_POINTS} points · Click to add, drag to move.' if points else '')

    def select_point(self, index):
        self.selected = index
        self.show_point()

    def add_point(self, temp, speed):
        points = self.points()
        if len(points) >= MAX_POINTS:
            return
        index = next((i for i, point in enumerate(points) if point[0] >= temp), len(points))
        if index == len(points) or points[index][0] != temp:
            points.insert(index, [temp, speed])
        self.select_point(index)
        self.update_save()

    def add_point_between(self):
        points = self.points()
        edges = [[MIN_TEMP - 1, points[0][1]], *points, [MAX_TEMP + 1, points[-1][1]]]
        left, right = max(zip(edges, edges[1:]), key=lambda pair: pair[1][0] - pair[0][0])
        self.add_point((left[0] + right[0]) // 2, round((left[1] + right[1]) / 2))

    def move_point(self, temp, speed):
        points = self.points()
        if points:
            low, high = self.temperature_range(points)
            points[self.selected] = [max(low, min(high, temp)), speed]
            self.show_point()
            self.update_save()

    def remove_point(self):
        del self.points()[self.selected]
        self.show_point()
        self.update_save()

    def set_save_available(self, available):
        self.can_save = available
        self.update_save()

    def update_save(self):
        dirty = self.draft != self.saved
        self.save_button.setEnabled(self.can_save and (dirty or self.config_error is not None))
        self.save_status.setText('Unsaved changes' if dirty else 'Saved settings could not be read' if self.config_error else '')

    def save(self):
        self.save_requested.emit(json.dumps(self.draft, separators=(',', ':')))

    def accept_save(self):
        self.saved = deepcopy(self.draft)
        self.config_error = None
        self.update_save()
