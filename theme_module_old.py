"""
theme_module.py — Svjetla i tamna tema za aplikaciju fiskalizacije.

Korištenje u BillingApp.__init__:
    from theme_module import ThemeManager
    self.theme_manager = ThemeManager(self, checkmark_path=self._checkmark_path)
    # ... dodaj toggle gumb u toolbar ...
    self.theme_manager.toggle_btn  # QPushButton koji se može dodati u layout
"""

from PySide6.QtWidgets import QPushButton, QApplication
from PySide6.QtCore import QSettings


def _light_stylesheet(checkmark_path: str) -> str:
    return f"""
        QWidget {{
            font-family: "Segoe UI", Arial, sans-serif;
            font-size: 11px;
            color: #2c2c2c;
            background-color: #f0f2f5;
        }}
        QComboBox QAbstractItemView {{
            background-color: #ffffff;
            color: #2c2c2c;
            border: 1px solid #cdd1d9;
            border-radius: 4px;
            selection-background-color: #e8f0fe;
            selection-color: #2c2c2c;
            outline: none;
        }}
        QGroupBox {{
            font-weight: bold;
            font-size: 11px;
            color: #444;
            border: 1px solid #d0d4db;
            border-radius: 8px;
            margin-top: 10px;
            padding: 10px 8px 8px 8px;
            background-color: #ffffff;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 6px;
            left: 12px;
            color: #555;
            background-color: #ffffff;
        }}
        QGroupBox QWidget {{ background-color: transparent; }}
        QGroupBox QLabel  {{ background-color: transparent; }}
        QLineEdit, QTextEdit, QDateEdit, QComboBox {{
            border: 1px solid #cdd1d9;
            border-radius: 5px;
            padding: 5px 8px;
            background-color: #ffffff;
            color: #2c2c2c;
            selection-background-color: #4a90d9;
        }}
        QLineEdit:focus, QTextEdit:focus,
        QDateEdit:focus, QComboBox:focus {{
            border: 1px solid #4a90d9;
            background-color: #f7faff;
        }}
        QLineEdit:disabled, QDateEdit:disabled {{
            background-color: #f4f4f4;
            color: #aaa;
        }}
        QComboBox::drop-down {{ border: none; width: 24px; }}
        QComboBox::down-arrow {{
            image: none;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 6px solid #888;
            margin-right: 6px;
        }}
        QPushButton {{
            border: none;
            border-radius: 6px;
            padding: 7px 16px;
            background-color: #4a90d9;
            color: white;
            font-weight: bold;
        }}
        QPushButton:hover   {{ background-color: #357abd; }}
        QPushButton:pressed {{ background-color: #2a6099; }}
        QPushButton:checked {{
            background-color: #e8f0fe;
            color: #3367d6;
            border: 1px solid #4a90d9;
        }}
        QPushButton#saveBtn {{
            background-color: #2e7d32;
            font-size: 13px;
            padding: 12px;
            color: white;
        }}
        QPushButton#saveBtn:hover {{ background-color: #1b5e20; }}
        QPushButton#addItemBtn {{
            background-color: #ffebee;
            font-size: 12px;
            color: #333;
            padding: 5px;
        }}
        QPushButton#addItemBtn:hover {{
            background-color: #4a90d9;
            color: white;
        }}
        QPushButton#kupacBtn {{
            background-color: #f5f5f5;
            color: #555;
            border: 1px solid #ccc;
        }}
        QPushButton#kupacBtn:hover {{
            background-color: #ffebee;
            color: #c62828;
            border-color: #ef9a9a;
        }}
        QPushButton#removeItemBtn {{
            background-color: #ffebee;
            font-size: 12px;
            color: #333;
            padding: 5px;
        }}
        QPushButton#removeItemBtn:hover {{
            background-color: #4a90d9;
            color: white;
        }}
        QPushButton#dangerBtn {{
            background-color: #f5f5f5;
            color: #555;
            border: 1px solid #ccc;
        }}
        QPushButton#dangerBtn:hover {{
            background-color: #ffebee;
            color: #c62828;
            border-color: #ef9a9a;
        }}
        QCheckBox {{ spacing: 6px; background-color: transparent; }}
        QCheckBox::indicator {{
            width: 16px; height: 16px;
            border-radius: 4px;
            border: 2px solid #cdd1d9;
            background-color: #ffffff;
        }}
        QCheckBox::indicator:hover  {{ border-color: #4a90d9; }}
        QCheckBox::indicator:checked {{
            background-color: #4a90d9;
            border: 2px solid #4a90d9;
            border-radius: 4px;
            image: url({checkmark_path});
        }}
        QTableWidget {{
            border: 1px solid #d0d4db;
            border-radius: 6px;
            gridline-color: #eaecef;
            background-color: #ffffff;
            alternate-background-color: #f8f9fb;
            color: #2c2c2c;
        }}
        QTableWidget::item {{ padding: 4px 6px; color: #2c2c2c; }}
        QTableWidget::item:selected {{
            background-color: #e8f0fe;
            color: #2c2c2c;
        }}
        QHeaderView::section {{
            background-color: #2c3e50;
            color: white;
            font-weight: bold;
            padding: 6px 8px;
            border: none;
            font-size: 10px;
        }}
        QScrollArea {{ border: none; background-color: #f0f2f5; }}
        QScrollBar:vertical {{
            border: none; background: #f0f2f5;
            width: 8px; border-radius: 4px;
        }}
        QScrollBar::handle:vertical {{
            background: #c0c4cc; border-radius: 4px; min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{ background: #9aa0ad; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        QListView {{ background-color: #ffffff; color: #2c2c2c; }}
        QListView::item {{ padding: 6px 8px; color: #2c2c2c; background-color: #ffffff; }}
        QListView::item:hover {{ background-color: #e8f0fe; color: #2c2c2c; }}
        QListView::item:selected {{ background-color: #4a90d9; color: #ffffff; }}
        QToolTip {{
            background-color: #ffffff; color: #2c2c2c;
            border: 1px solid #cdd1d9; border-radius: 4px;
            padding: 5px 8px; font-size: 11px;
        }}
        QCalendarWidget {{ background-color: #ffffff; color: #2c2c2c; }}
        QCalendarWidget QAbstractItemView {{
            background-color: #ffffff; color: #2c2c2c;
            selection-background-color: #4a90d9; selection-color: #ffffff;
        }}
        QCalendarWidget QAbstractItemView:disabled {{ color: #aaaaaa; }}
        QCalendarWidget QWidget {{ background-color: #ffffff; color: #2c2c2c; }}
        QCalendarWidget QToolButton {{
            background-color: #ffffff; color: #2c2c2c;
            border: none; border-radius: 4px; padding: 4px 8px; font-weight: bold;
        }}
        QCalendarWidget QToolButton:hover {{ background-color: #e8f0fe; color: #3367d6; }}
        QCalendarWidget QSpinBox {{
            background-color: #ffffff; color: #2c2c2c;
            border: 1px solid #cdd1d9; border-radius: 4px;
        }}
        QCalendarWidget QMenu {{ background-color: #ffffff; color: #2c2c2c; }}
        #qt_calendar_navigationbar {{
            background-color: #f0f2f5;
            border-bottom: 1px solid #d0d4db; padding: 4px;
        }}
        QTabWidget::pane {{
            border: 1px solid #d0d4db; border-radius: 8px;
            background-color: #f0f2f5;
        }}
        QTabBar::tab {{
            background-color: #e0e4ea; color: #555;
            padding: 8px 20px;
            border-top-left-radius: 6px; border-top-right-radius: 6px;
            margin-right: 2px; font-weight: bold;
        }}
        QTabBar::tab:selected {{ background-color: #4a90d9; color: white; }}
        QTabBar::tab:hover:!selected {{ background-color: #c8d0dc; }}
        QLabel#themeToggleLabel {{
            color: #555; font-size: 10px; background-color: transparent;
        }}
        QDialog {{
            background-color: #f0f2f5;
        }}
        QDialogButtonBox QPushButton {{
            min-width: 100px;
        }}
    """


def _dark_stylesheet(checkmark_path: str) -> str:
    return f"""
        QWidget {{
            font-family: "Segoe UI", Arial, sans-serif;
            font-size: 11px;
            color: #e0e0e0;
            background-color: #1e1e2e;
        }}
        QComboBox QAbstractItemView {{
            background-color: #2a2a3e;
            color: #e0e0e0;
            border: 1px solid #3a3a52;
            border-radius: 4px;
            selection-background-color: #3a6bc4;
            selection-color: #ffffff;
            outline: none;
        }}
        QGroupBox {{
            font-weight: bold;
            font-size: 11px;
            color: #b0b8cc;
            border: 1px solid #3a3a52;
            border-radius: 8px;
            margin-top: 10px;
            padding: 10px 8px 8px 8px;
            background-color: #252538;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 6px;
            left: 12px;
            color: #8892a8;
            background-color: #252538;
        }}
        QGroupBox QWidget {{ background-color: transparent; }}
        QGroupBox QLabel  {{ background-color: transparent; }}
        QLineEdit, QTextEdit, QDateEdit, QComboBox {{
            border: 1px solid #3a3a52;
            border-radius: 5px;
            padding: 5px 8px;
            background-color: #2a2a3e;
            color: #e0e0e0;
            selection-background-color: #3a6bc4;
        }}
        QLineEdit:focus, QTextEdit:focus,
        QDateEdit:focus, QComboBox:focus {{
            border: 1px solid #5a8fd8;
            background-color: #2e2e48;
        }}
        QLineEdit:disabled, QDateEdit:disabled {{
            background-color: #222232;
            color: #555570;
        }}
        QComboBox::drop-down {{ border: none; width: 24px; }}
        QComboBox::down-arrow {{
            image: none;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 6px solid #8892a8;
            margin-right: 6px;
        }}
        QPushButton {{
            border: none;
            border-radius: 6px;
            padding: 7px 16px;
            background-color: #3a6bc4;
            color: #ffffff;
            font-weight: bold;
        }}
        QPushButton:hover   {{ background-color: #4a7dd4; }}
        QPushButton:pressed {{ background-color: #2a5aa4; }}
        QPushButton:checked {{
            background-color: #1e2e4a;
            color: #7aabf0;
            border: 1px solid #3a6bc4;
        }}
        QPushButton#saveBtn {{
            background-color: #2e6b32;
            font-size: 13px;
            padding: 12px;
            color: white;
        }}
        QPushButton#saveBtn:hover {{ background-color: #1e5022; }}
        QPushButton#addItemBtn {{
            background-color: #2a2a3e;
            font-size: 12px;
            color: #b0b8cc;
            padding: 5px;
            border: 1px solid #3a3a52;
        }}
        QPushButton#addItemBtn:hover {{
            background-color: #3a6bc4;
            color: white;
            border: none;
        }}
        QPushButton#kupacBtn {{
            background-color: #2a2a3e;
            color: #8892a8;
            border: 1px solid #3a3a52;
        }}
        QPushButton#kupacBtn:hover {{
            background-color: #4a1e2a;
            color: #f08080;
            border-color: #a04040;
        }}
        QPushButton#removeItemBtn {{
            background-color: #2a2a3e;
            font-size: 12px;
            color: #b0b8cc;
            padding: 5px;
            border: 1px solid #3a3a52;
        }}
        QPushButton#removeItemBtn:hover {{
            background-color: #3a6bc4;
            color: white;
            border: none;
        }}
        QPushButton#dangerBtn {{
            background-color: #2a2a3e;
            color: #8892a8;
            border: 1px solid #3a3a52;
        }}
        QPushButton#dangerBtn:hover {{
            background-color: #4a1e2a;
            color: #f08080;
            border-color: #a04040;
        }}
        QCheckBox {{ spacing: 6px; background-color: transparent; }}
        QCheckBox::indicator {{
            width: 16px; height: 16px;
            border-radius: 4px;
            border: 2px solid #3a3a52;
            background-color: #2a2a3e;
        }}
        QCheckBox::indicator:hover  {{ border-color: #5a8fd8; }}
        QCheckBox::indicator:checked {{
            background-color: #3a6bc4;
            border: 2px solid #3a6bc4;
            border-radius: 4px;
            image: url({checkmark_path});
        }}
        QTableWidget {{
            border: 1px solid #3a3a52;
            border-radius: 6px;
            gridline-color: #2e2e48;
            background-color: #252538;
            alternate-background-color: #2a2a3e;
            color: #e0e0e0;
        }}
        QTableWidget::item {{ padding: 4px 6px; color: #e0e0e0; }}
        QTableWidget::item:selected {{
            background-color: #3a4e7a;
            color: #ffffff;
        }}
        QHeaderView::section {{
            background-color: #1a1a2e;
            color: #a0b0cc;
            font-weight: bold;
            padding: 6px 8px;
            border: none;
            border-right: 1px solid #2e2e48;
            font-size: 10px;
        }}
        QScrollArea {{ border: none; background-color: #1e1e2e; }}
        QScrollBar:vertical {{
            border: none; background: #1e1e2e;
            width: 8px; border-radius: 4px;
        }}
        QScrollBar::handle:vertical {{
            background: #3a3a52; border-radius: 4px; min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{ background: #4a4a6a; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        QListView {{ background-color: #2a2a3e; color: #e0e0e0; }}
        QListView::item {{ padding: 6px 8px; color: #e0e0e0; background-color: #2a2a3e; }}
        QListView::item:hover {{ background-color: #2e3e5e; color: #e0e0e0; }}
        QListView::item:selected {{ background-color: #3a6bc4; color: #ffffff; }}
        QToolTip {{
            background-color: #2a2a3e; color: #e0e0e0;
            border: 1px solid #3a3a52; border-radius: 4px;
            padding: 5px 8px; font-size: 11px;
        }}
        QCalendarWidget {{ background-color: #252538; color: #e0e0e0; }}
        QCalendarWidget QAbstractItemView {{
            background-color: #252538; color: #e0e0e0;
            selection-background-color: #3a6bc4; selection-color: #ffffff;
        }}
        QCalendarWidget QAbstractItemView:disabled {{ color: #444460; }}
        QCalendarWidget QWidget {{ background-color: #252538; color: #e0e0e0; }}
        QCalendarWidget QToolButton {{
            background-color: #252538; color: #e0e0e0;
            border: none; border-radius: 4px; padding: 4px 8px; font-weight: bold;
        }}
        QCalendarWidget QToolButton:hover {{ background-color: #2e3e5e; color: #7aabf0; }}
        QCalendarWidget QSpinBox {{
            background-color: #2a2a3e; color: #e0e0e0;
            border: 1px solid #3a3a52; border-radius: 4px;
        }}
        QCalendarWidget QMenu {{ background-color: #2a2a3e; color: #e0e0e0; }}
        #qt_calendar_navigationbar {{
            background-color: #1a1a2e;
            border-bottom: 1px solid #3a3a52; padding: 4px;
        }}
        QTabWidget::pane {{
            border: 1px solid #3a3a52; border-radius: 8px;
            background-color: #1e1e2e;
        }}
        QTabBar::tab {{
            background-color: #252538; color: #8892a8;
            padding: 8px 20px;
            border-top-left-radius: 6px; border-top-right-radius: 6px;
            margin-right: 2px; font-weight: bold;
        }}
        QTabBar::tab:selected {{ background-color: #3a6bc4; color: white; }}
        QTabBar::tab:hover:!selected {{ background-color: #2e2e48; color: #b0b8cc; }}
        QLabel#themeToggleLabel {{
            color: #8892a8; font-size: 10px; background-color: transparent;
        }}
        QDialog {{
            background-color: #1e1e2e;
        }}
        QDialogButtonBox QPushButton {{
            min-width: 100px;
        }}
    """


class ThemeManager:
    """
    Upravlja svjetlom i tamnom temom aplikacije.
    Pamti odabir u QSettings (HKCU/Software/Fiskalizacija/theme).
    """
    LIGHT = "light"
    DARK  = "dark"

    def __init__(self, app_widget, checkmark_path: str):
        self._widget         = app_widget
        self._checkmark_path = checkmark_path.replace("\\", "/")
        self._settings       = QSettings("Fiskalizacija", "App")
        self._current        = self._settings.value("theme", self.LIGHT)

        # Toggle gumb koji se može umetnuti u bilo koji layout
        self.toggle_btn = QPushButton()
        self.toggle_btn.setCheckable(False)
        self.toggle_btn.setFixedWidth(110)
        self.toggle_btn.setToolTip("Prebaci između svjetle i tamne teme")
        self.toggle_btn.clicked.connect(self.toggle)

        # Primijeni pohranjenu temu
        self.apply(self._current)

    # ── Javno sučelje ───────────────────────────────────────────────────────

    @property
    def is_dark(self) -> bool:
        return self._current == self.DARK

    def toggle(self):
        self.apply(self.DARK if self._current == self.LIGHT else self.LIGHT)

    def apply(self, mode: str):
        self._current = mode
        self._settings.setValue("theme", mode)

        cp = self._checkmark_path
        ss = _dark_stylesheet(cp) if mode == self.DARK else _light_stylesheet(cp)

        # Primijeni na cijelu aplikaciju
        app = QApplication.instance()
        if app:
            app.setStyleSheet(ss)

        self._update_btn_label()

    def _update_btn_label(self):
        if self._current == self.DARK:
            self.toggle_btn.setText("☀️  Svijetla tema")
            self.toggle_btn.setToolTip("Prebaci na svjetlu temu")
        else:
            self.toggle_btn.setText("🌙  Tamna tema")
            self.toggle_btn.setToolTip("Prebaci na tamnu temu")
