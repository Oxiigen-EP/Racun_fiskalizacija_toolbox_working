"""
theme_module.py — Svjetla i tamna tema za aplikaciju fiskalizacije.

Lokalni setStyleSheet() pozivi nadjačavaju QApplication stylesheet.
Rješenje: ThemeManager drži COLORS rječnik i emitira signal theme_changed
kojeg slušaju moduli za ažuriranje svojih widgeta.
"""

from PySide6.QtWidgets import QPushButton, QApplication
from PySide6.QtCore import QSettings, QObject, Signal


# ── Palete boja ─────────────────────────────────────────────────────────────

LIGHT_COLORS = {
    "bg":              "#f0f2f5",
    "bg_widget":       "#ffffff",
    "bg_input":        "#ffffff",
    "bg_input_focus":  "#f7faff",
    "bg_disabled":     "#f4f4f4",
    "bg_panel":        "#f8f9fb",
    "bg_subtle":       "transparent",
    "border":          "#cdd1d9",
    "border_focus":    "#4a90d9",
    "text":            "#2c2c2c",
    "text_muted":      "#555555",
    "text_dim":        "#888888",
    "text_disabled":   "#aaaaaa",
    "accent":          "#4a90d9",
    "accent_hover":    "#357abd",
    "accent_text":     "#ffffff",
    "success":         "#2e7d32",
    "success_hover":   "#1b5e20",
    "danger_bg":       "#ffebee",
    "danger_text":     "#c62828",
    "danger_border":   "#ef9a9a",
    "warning":         "#e67e22",
    "info":            "#1565c0",
    "table_alt":       "#f8f9fb",
    "table_header":    "#2c3e50",
    "table_selected":  "#e8f0fe",
    "table_sel_text":  "#2c2c2c",
    "scrollbar":       "#f0f2f5",
    "scrollbar_handle":"#c0c4cc",
    "tab_inactive":    "#e0e4ea",
    "tab_text":        "#555555",
}

DARK_COLORS = {
    "bg":              "#1e1e2e",
    "bg_widget":       "#252538",
    "bg_input":        "#2a2a3e",
    "bg_input_focus":  "#2e2e48",
    "bg_disabled":     "#222232",
    "bg_panel":        "#2a2a3e",
    "bg_subtle":       "transparent",
    "border":          "#3a3a52",
    "border_focus":    "#5a8fd8",
    "text":            "#e0e0e0",
    "text_muted":      "#b0b8cc",
    "text_dim":        "#8892a8",
    "text_disabled":   "#555570",
    "accent":          "#3a6bc4",
    "accent_hover":    "#4a7dd4",
    "accent_text":     "#ffffff",
    "success":         "#2e6b32",
    "success_hover":   "#1e5022",
    "danger_bg":       "#4a1e2a",
    "danger_text":     "#f08080",
    "danger_border":   "#a04040",
    "warning":         "#f0a040",
    "info":            "#7aabf0",
    "table_alt":       "#2a2a3e",
    "table_header":    "#1a1a2e",
    "table_selected":  "#3a4e7a",
    "table_sel_text":  "#ffffff",
    "scrollbar":       "#1e1e2e",
    "scrollbar_handle":"#3a3a52",
    "tab_inactive":    "#252538",
    "tab_text":        "#8892a8",
}


def _build_stylesheet(c: dict, checkmark_path: str) -> str:
    return f"""
        QWidget {{
            font-family: "Segoe UI", Arial, sans-serif;
            font-size: 11px;
            color: {c['text']};
            background-color: {c['bg']};
        }}
        QComboBox QAbstractItemView {{
            background-color: {c['bg_input']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 4px;
            selection-background-color: {c['accent']};
            selection-color: {c['accent_text']};
            outline: none;
        }}
        QGroupBox {{
            font-weight: bold; font-size: 11px;
            color: {c['text_muted']};
            border: 1px solid {c['border']};
            border-radius: 8px;
            margin-top: 10px;
            padding: 10px 8px 8px 8px;
            background-color: {c['bg_widget']};
        }}
        QGroupBox::title {{
            subcontrol-origin: margin; subcontrol-position: top left;
            padding: 0 6px; left: 12px;
            color: {c['text_dim']};
            background-color: {c['bg_widget']};
        }}
        QGroupBox QWidget {{ background-color: transparent; }}
        QGroupBox QLabel  {{ background-color: transparent; }}
        QLineEdit, QTextEdit, QDateEdit, QComboBox {{
            border: 1px solid {c['border']};
            border-radius: 5px; padding: 5px 8px;
            background-color: {c['bg_input']};
            color: {c['text']};
            selection-background-color: {c['accent']};
        }}
        QLineEdit:focus, QTextEdit:focus, QDateEdit:focus, QComboBox:focus {{
            border: 1px solid {c['border_focus']};
            background-color: {c['bg_input_focus']};
        }}
        QLineEdit:disabled, QDateEdit:disabled {{
            background-color: {c['bg_disabled']};
            color: {c['text_disabled']};
        }}
        QComboBox::drop-down {{ border: none; width: 24px; }}
        QComboBox::down-arrow {{
            image: none;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 6px solid {c['text_dim']};
            margin-right: 6px;
        }}
        QPushButton {{
            border: 1px solid; border-radius: 6px; padding: 7px 16px;
            background-color: {c['accent']}; color: {c['accent_text']}; font-weight: bold;
        }}
        QPushButton:hover   {{ background-color: {c['accent_hover']}; }}
        QPushButton:pressed {{ background-color: {c['accent']}; opacity: 0.8; }}
        QPushButton:checked {{
            background-color: {c['bg_input']}; color: {c['info']};
            border: 1px solid {c['accent']};
        }}
        QPushButton#saveBtn {{
            background-color: {c['success']}; font-size: 13px; padding: 12px; color: white;
        }}
        QPushButton#saveBtn:hover {{ background-color: {c['success_hover']}; }}
        QPushButton#addItemBtn {{
            background-color: {c['danger_bg']}; font-size: 12px;
            color: {c['text']}; padding: 5px;
        }}
        QPushButton#addItemBtn:hover {{ background-color: {c['accent']}; color: white; }}
        QPushButton#kupacBtn {{
            background-color: {c['bg_input']}; color: {c['text_dim']};
            border: 1px solid {c['border']};
        }}
        QPushButton#kupacBtn:hover {{
            background-color: {c['danger_bg']}; color: {c['danger_text']};
            border-color: {c['danger_border']};
        }}
        QPushButton#removeItemBtn {{
            background-color: {c['danger_bg']}; font-size: 12px;
            color: {c['text']}; padding: 5px;
        }}
        QPushButton#removeItemBtn:hover {{ background-color: {c['accent']}; color: white; }}
        QPushButton#dangerBtn {{
            background-color: {c['bg_input']}; color: {c['text_dim']};
            border: 1px solid {c['border']};
        }}
        QPushButton#dangerBtn:hover {{
            background-color: {c['danger_bg']}; color: {c['danger_text']};
            border-color: {c['danger_border']};
        }}
        QCheckBox {{ spacing: 6px; background-color: transparent; }}
        QCheckBox::indicator {{
            width: 16px; height: 16px; border-radius: 4px;
            border: 2px solid {c['border']};
            background-color: {c['bg_input']};
        }}
        QCheckBox::indicator:hover  {{ border-color: {c['border_focus']}; }}
        QCheckBox::indicator:checked {{
            background-color: {c['accent']}; border: 2px solid {c['accent']};
            border-radius: 4px; image: url({checkmark_path});
        }}
        QTableWidget {{
            border: 1px solid {c['border']}; border-radius: 6px;
            gridline-color: {c['border']};
            background-color: {c['bg_widget']};
            alternate-background-color: {c['table_alt']};
            color: {c['text']};
        }}
        QTableWidget::item {{ padding: 4px 6px; color: {c['text']}; }}
        QTableWidget::item:selected {{
            background-color: {c['table_selected']}; color: {c['table_sel_text']};
        }}
        QHeaderView::section {{
            background-color: {c['table_header']}; color: white;
            font-weight: bold; padding: 6px 8px; border: none; font-size: 10px;
        }}
        QScrollArea {{ border: none; background-color: {c['bg']}; }}
        QScrollBar:vertical {{
            border: none; background: {c['scrollbar']}; width: 8px; border-radius: 4px;
        }}
        QScrollBar::handle:vertical {{
            background: {c['scrollbar_handle']}; border-radius: 4px; min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {c['text_dim']}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        QListView {{ background-color: {c['bg_input']}; color: {c['text']}; }}
        QListView::item {{ padding: 6px 8px; color: {c['text']}; background-color: {c['bg_input']}; }}
        QListView::item:hover {{ background-color: {c['table_selected']}; color: {c['text']}; }}
        QListView::item:selected {{ background-color: {c['accent']}; color: {c['accent_text']}; }}
        QToolTip {{
            background-color: {c['bg_widget']}; color: {c['text']};
            border: 1px solid {c['border']}; border-radius: 4px;
            padding: 5px 8px; font-size: 11px;
        }}
        QCalendarWidget {{
            background-color: {c['bg_widget']}; color: {c['text']};
        }}
        QCalendarWidget QAbstractItemView {{
            background-color: {c['bg_widget']}; color: {c['text']};
            selection-background-color: {c['accent']}; selection-color: {c['accent_text']};
            outline: none;
        }}
        QCalendarWidget QAbstractItemView:enabled {{
            color: {c['text']};
        }}
        QCalendarWidget QAbstractItemView:disabled {{
            color: {c['text_disabled']};
        }}
        QCalendarWidget QWidget {{
            background-color: {c['bg_widget']}; color: {c['text']};
        }}
        QCalendarWidget QToolButton {{
            background-color: {c['table_header']}; color: #ffffff;
            border: none; border-radius: 4px; padding: 4px 8px; font-weight: bold;
            font-size: 11px;
        }}
        QCalendarWidget QToolButton:hover {{
            background-color: {c['accent']}; color: #ffffff;
        }}
        QCalendarWidget QToolButton#qt_calendar_prevmonth,
        QCalendarWidget QToolButton#qt_calendar_nextmonth {{
            color: #ffffff; font-size: 14px; font-weight: bold;
            background-color: {c['table_header']};
        }}
        QCalendarWidget QToolButton#qt_calendar_prevmonth:hover,
        QCalendarWidget QToolButton#qt_calendar_nextmonth:hover {{
            background-color: {c['accent']};
        }}
        QCalendarWidget QSpinBox {{
            background-color: {c['table_header']}; color: #ffffff;
            border: 1px solid {c['border']}; border-radius: 4px;
            selection-background-color: {c['accent']}; selection-color: #ffffff;
        }}
        QCalendarWidget QSpinBox::up-button,
        QCalendarWidget QSpinBox::down-button {{
            background-color: {c['table_header']};
        }}
        QCalendarWidget QMenu {{
            background-color: {c['bg_input']}; color: {c['text']};
        }}
        #qt_calendar_navigationbar {{
            background-color: {c['table_header']};
            border-bottom: 1px solid {c['border']};
            padding: 4px;
        }}
        #qt_calendar_calendarview {{
            background-color: {c['bg_widget']}; color: {c['text']};
        }}
        QTabWidget::pane {{
            border: 1px solid {c['border']}; border-radius: 8px; background-color: {c['bg']};
        }}
        QTabBar::tab {{
            background-color: {c['tab_inactive']}; color: {c['tab_text']};
            padding: 8px 20px;
            border-top-left-radius: 6px; border-top-right-radius: 6px;
            margin-right: 2px; font-weight: bold;
        }}
        QTabBar::tab:selected {{ background-color: {c['accent']}; color: white; }}
        QTabBar::tab:hover:!selected {{ background-color: {c['bg_input']}; color: {c['text_muted']}; }}
        QDialog {{ background-color: {c['bg']}; }}
        QDialogButtonBox QPushButton {{ min-width: 100px; }}
        QFrame {{ background-color: transparent; }}
        QSplitter::handle {{ background-color: {c['border']}; }}
        QLabel {{ background-color: transparent; color: {c['text']}; }}
        QToolButton#qt_calendar_prevmonth,
        QToolButton#qt_calendar_nextmonth {{
            color: #ffffff;
            background-color: transparent;
            border: none;
            font-size: 20px;
            font-weight: bold;
        }}
    """


# ── ThemeManager ─────────────────────────────────────────────────────────────

class ThemeManager(QObject):
    """
    Upravlja svjetlom i tamnom temom aplikacije.
    Emitira signal theme_changed(colors_dict) koji slušaju moduli
    za ažuriranje svojih lokalnih setStyleSheet() poziva.
    Pamti odabir u QSettings.
    """
    theme_changed = Signal(dict)   # emitira COLORS rječnik aktivne teme

    LIGHT = "light"
    DARK  = "dark"

    def __init__(self, app_widget, checkmark_path: str):
        super().__init__(app_widget)
        self._widget         = app_widget
        self._checkmark_path = checkmark_path.replace("\\", "/")
        self._settings       = QSettings("Fiskalizacija", "App")
        self._current        = self._settings.value("theme", self.LIGHT)

        self.toggle_btn = QPushButton()
        self.toggle_btn.setCheckable(False)
        self.toggle_btn.setFixedWidth(115)
        self.toggle_btn.setToolTip("Prebaci između svjetle i tamne teme")
        self.toggle_btn.clicked.connect(self.toggle)

        self.apply(self._current)

    # ── Javno sučelje ────────────────────────────────────────────────────────

    @property
    def is_dark(self) -> bool:
        return self._current == self.DARK

    @property
    def colors(self) -> dict:
        return DARK_COLORS if self.is_dark else LIGHT_COLORS

    def toggle(self):
        self.apply(self.DARK if self._current == self.LIGHT else self.LIGHT)

    def apply(self, mode: str):
        self._current = mode
        self._settings.setValue("theme", mode)

        c  = DARK_COLORS if mode == self.DARK else LIGHT_COLORS
        ss = _build_stylesheet(c, self._checkmark_path)

        app = QApplication.instance()
        if app:
            app.setStyleSheet(ss)

        self.theme_changed.emit(c)
        self._update_btn_label()

    def _update_btn_label(self):
        if self._current == self.DARK:
            self.toggle_btn.setText("☀️  Svijetla tema")
            self.toggle_btn.setObjectName("dangerBtn")
            self.toggle_btn.setToolTip("Prebaci na svjetlu temu")
        else:
            self.toggle_btn.setText("🌙  Tamna tema")
            self.toggle_btn.setObjectName("dangerBtn")
            self.toggle_btn.setToolTip("Prebaci na tamnu temu")
