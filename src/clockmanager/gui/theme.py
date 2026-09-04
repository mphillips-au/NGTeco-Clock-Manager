"""Application theme: light/dark palettes and a shared stylesheet.

PHASE 12: give the desktop application one consistent business-product look
instead of per-view ad-hoc styling. Only ``clockmanager.gui`` imports this
module (``ARCHITECTURE.md``). The choice persists in ``QSettings`` so it
survives restarts without touching the application database or config file.

The stylesheet is generated from one :class:`Palette` per theme, so a colour
is defined once and every rule that needs it reads the same token. Views must
not hardcode colours; they set an ``objectName`` (see ``common.py``) and let
the sheet decide, which is what keeps dark mode legible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

__all__ = [
    "Palette",
    "ThemeName",
    "current_palette",
    "current_theme",
    "set_theme",
    "toggle_theme",
]

ThemeName = Literal["light", "dark"]

_SETTINGS_KEY = "appearance/theme"


@dataclass(frozen=True, slots=True)
class Palette:
    """Every colour one theme uses. Views read semantic entries only."""

    window: str  #: application/page background
    surface: str  #: cards, tables, inputs
    surface_alt: str  #: alternating rows, headers, hover fills
    sidebar: str
    border: str
    text: str
    text_muted: str
    accent: str  #: primary action / selection
    accent_text: str  #: text drawn on the accent
    accent_soft: str  #: hover tint of the accent
    success: str
    warning: str
    error: str
    info: str
    badge_in: str  #: attendance IN
    badge_out: str  #: attendance OUT


_LIGHT_PALETTE = Palette(
    window="#f4f6fa",
    surface="#ffffff",
    surface_alt="#f1f4f9",
    sidebar="#eaeff6",
    border="#dbe2ec",
    text="#1b2536",
    text_muted="#5d6b80",
    accent="#1971c2",
    accent_text="#ffffff",
    accent_soft="#e3f0fb",
    success="#2b8a3e",
    warning="#9c6d00",
    error="#c92a2a",
    info="#334155",
    badge_in="#2b8a3e",
    badge_out="#9a5b00",
)

_DARK_PALETTE = Palette(
    window="#111827",
    surface="#1b2534",
    surface_alt="#222e40",
    sidebar="#161f2c",
    border="#33415a",
    text="#e6ebf3",
    text_muted="#9fb0c6",
    accent="#2b7fd4",
    accent_text="#ffffff",
    accent_soft="#22364e",
    success="#69db7c",
    warning="#ffd43b",
    error="#ff8787",
    info="#dbe3ee",
    badge_in="#69db7c",
    badge_out="#ffc078",
)

_PALETTES: dict[str, Palette] = {"light": _LIGHT_PALETTE, "dark": _DARK_PALETTE}


def current_theme() -> ThemeName:
    """Return the stored theme choice, defaulting to light."""
    stored = QSettings().value(_SETTINGS_KEY, "light")
    return "dark" if str(stored).lower() == "dark" else "light"


def current_palette() -> Palette:
    """Return the palette for the theme in use.

    Views that must colour a single cell (a table item has no stylesheet of
    its own) read the token from here instead of hardcoding hex.
    """
    return _PALETTES[current_theme()]


def colour(token: str) -> QColor:
    """Return one palette entry as a ``QColor`` for item-level painting."""
    return QColor(getattr(current_palette(), token))


def set_theme(app: QApplication, name: ThemeName) -> None:
    """Apply ``name`` to the whole application and remember the choice."""
    QSettings().setValue(_SETTINGS_KEY, name)
    app.setStyleSheet(stylesheet(name))


def toggle_theme(app: QApplication) -> ThemeName:
    """Flip between light and dark. Returns the theme now in use."""
    next_theme: ThemeName = "light" if current_theme() == "dark" else "dark"
    set_theme(app, next_theme)
    return next_theme


def stylesheet(name: ThemeName) -> str:
    """Return the full application stylesheet for one theme."""
    return _build(_PALETTES[name])


def _build(p: Palette) -> str:
    """Render the stylesheet for ``p``.

    Rules are grouped by widget family. Every rule that paints a background
    also sets a colour: a background without a matching foreground is what
    made the first dark theme unreadable.
    """
    return f"""
/* -- base ------------------------------------------------------------- */
QWidget {{
    background: {p.window};
    color: {p.text};
    font-family: "Segoe UI", "Noto Sans", sans-serif;
    font-size: 10pt;
}}
QMainWindow, QDialog, QWidget#MainContent {{ background: {p.window}; color: {p.text}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: {p.window}; border: none; }}
QAbstractItemView, QListWidget, QTableWidget, QTableView {{ outline: none; }}
QToolTip {{
    background: {p.surface}; color: {p.text}; border: 1px solid {p.border};
    padding: 5px 7px; border-radius: 5px;
}}

/* -- menu bar --------------------------------------------------------- */
QMenuBar {{ background: {p.sidebar}; color: {p.text}; border-bottom: 1px solid {p.border}; }}
QMenuBar::item {{ background: transparent; padding: 6px 11px; border-radius: 5px; }}
QMenuBar::item:selected {{ background: {p.accent_soft}; color: {p.text}; }}
QMenu {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 20px; border-radius: 5px; }}
QMenu::item:selected {{ background: {p.accent}; color: {p.accent_text}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 6px; }}

/* -- sidebar / navigation --------------------------------------------- */
QWidget#Sidebar {{ background: {p.sidebar}; border-right: 1px solid {p.border}; }}
QWidget#Sidebar QLabel {{ background: transparent; }}
QLabel#SidebarBrand {{ color: {p.text}; font-size: 14pt; font-weight: 700; }}
QLabel#SidebarRole {{ color: {p.text_muted}; font-size: 8.5pt; padding-bottom: 6px; }}
QListWidget#Navigation {{ background: {p.sidebar}; color: {p.text}; border: none; padding: 2px; }}
QListWidget#Navigation::item {{
    padding: 8px 10px; border-radius: 7px; margin: 1px 0; min-height: 18px; max-height: 22px;
}}
QListWidget#Navigation::item:selected {{ background: {p.accent}; color: {p.accent_text}; }}
QListWidget#Navigation::item:hover:!selected {{ background: {p.accent_soft}; }}

/* -- page structure --------------------------------------------------- */
QLabel#PageTitle {{ font-size: 15pt; font-weight: 700; color: {p.text}; }}
QLabel#PageSubtitle {{ color: {p.text_muted}; font-size: 9.5pt; }}
QFrame#PageDivider {{ background: {p.border}; max-height: 1px; border: none; }}
QLabel#SectionLabel {{ font-weight: 700; color: {p.text}; }}
QLabel#Muted {{ color: {p.text_muted}; }}

/* -- cards / group boxes ---------------------------------------------- */
QGroupBox {{
    background: {p.surface}; color: {p.text}; border: 1px solid {p.border};
    border-radius: 9px; font-weight: 600; margin-top: 13px; padding: 10px 8px 8px 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left; left: 11px;
    padding: 0 4px; color: {p.text_muted}; background: transparent;
}}
QGroupBox QLabel, QGroupBox QCheckBox {{ background: transparent; }}
QGroupBox#MetricCard {{ border-top: 3px solid {p.accent}; }}
QLabel#CardTitle {{ color: {p.text_muted}; font-size: 9pt; font-weight: 600; }}
QLabel#CardValue {{ color: {p.text}; font-size: 17pt; font-weight: 700; }}
QLabel#CardHint {{ color: {p.text_muted}; font-size: 8.5pt; }}

/* -- empty / placeholder states --------------------------------------- */
QWidget#EmptyState {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 9px; }}
QWidget#EmptyState QLabel {{ background: transparent; }}
QLabel#EmptyTitle {{ color: {p.text}; font-size: 11pt; font-weight: 600; }}
QLabel#EmptyBody {{ color: {p.text_muted}; }}

/* -- tables ----------------------------------------------------------- */
QTableWidget, QTableView {{
    background: {p.surface}; alternate-background-color: {p.surface_alt};
    color: {p.text}; border: 1px solid {p.border}; border-radius: 9px;
    gridline-color: {p.border};
    selection-background-color: {p.accent}; selection-color: {p.accent_text};
}}
QTableWidget::item {{ padding: 4px 8px; border: none; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {p.surface_alt}; color: {p.text_muted}; padding: 8px;
    border: none; border-bottom: 1px solid {p.border}; font-weight: 600;
}}
QTableCornerButton::section {{ background: {p.surface_alt}; border: none; }}

/* -- buttons ---------------------------------------------------------- */
QPushButton {{
    background: {p.surface}; border: 1px solid {p.border}; color: {p.text};
    padding: 7px 14px; border-radius: 6px; font-weight: 600;
}}
QPushButton:hover {{ background: {p.accent_soft}; border-color: {p.accent}; }}
QPushButton:pressed {{ background: {p.accent_soft}; padding-top: 8px; padding-bottom: 6px; }}
QPushButton[focusVisible="true"]:focus {{ border: 2px solid {p.accent}; }}
QPushButton:disabled {{ background: {p.surface_alt}; border-color: {p.border}; color: {p.text_muted}; }}
QPushButton#Primary {{ background: {p.accent}; border-color: {p.accent}; color: {p.accent_text}; }}
QPushButton#Primary:hover {{ background: {p.accent}; border-color: {p.text}; }}
QPushButton#Primary:disabled {{
    background: {p.surface_alt}; border-color: {p.border}; color: {p.text_muted};
}}
QPushButton#Danger {{ color: {p.error}; border-color: {p.border}; }}
QPushButton#Danger:hover {{ border-color: {p.error}; background: {p.surface_alt}; }}

/* -- inputs ----------------------------------------------------------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QPlainTextEdit, QTextEdit {{
    background: {p.surface}; color: {p.text}; border: 1px solid {p.border};
    padding: 6px 8px; border-radius: 6px; min-height: 17px;
    selection-background-color: {p.accent}; selection-color: {p.accent_text};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QDateEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border: 2px solid {p.accent}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDateEdit:disabled {{
    background: {p.surface_alt}; color: {p.text_muted};
}}
QComboBox QAbstractItemView {{
    background: {p.surface}; color: {p.text}; border: 1px solid {p.border};
    selection-background-color: {p.accent}; selection-color: {p.accent_text};
}}
QCheckBox, QRadioButton {{ background: transparent; color: {p.text}; spacing: 7px; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {p.text_muted}; }}
QCheckBox[focusVisible="true"]::indicator:focus,
QRadioButton[focusVisible="true"]::indicator:focus {{ border: 2px solid {p.accent}; }}

/* -- tabs ------------------------------------------------------------- */
QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: 9px; background: {p.surface}; top: -1px; }}
QTabBar::tab {{
    background: {p.surface_alt}; color: {p.text_muted}; padding: 8px 15px; margin-right: 2px;
    border: 1px solid {p.border}; border-bottom: none;
    border-top-left-radius: 7px; border-top-right-radius: 7px;
}}
QTabBar::tab:selected {{ background: {p.surface}; color: {p.text}; font-weight: 600; }}
QTabBar::tab:hover:!selected {{ color: {p.text}; }}
QTabBar::tab:focus {{ border: 1px solid {p.border}; border-bottom: none; }}
QTabBar[focusVisible="true"]::tab:focus {{ border: 2px solid {p.accent}; }}

/* -- scrollbars ------------------------------------------------------- */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle {{ background: {p.border}; border-radius: 5px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {p.text_muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* -- status ----------------------------------------------------------- */
QStatusBar {{ background: {p.sidebar}; color: {p.text_muted}; border-top: 1px solid {p.border}; }}
QStatusBar::item {{ border: none; }}
QProgressBar {{
    background: {p.surface_alt}; border: 1px solid {p.border}; border-radius: 5px;
    height: 6px; text-align: center; color: {p.text_muted};
}}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 5px; }}
QLabel#StatusSuccess {{ color: {p.success}; }}
QLabel#StatusWarning {{ color: {p.warning}; }}
QLabel#StatusError {{ color: {p.error}; }}
QLabel#StatusInfo {{ color: {p.info}; }}
QLabel#StatusLoading {{ color: {p.accent}; }}

/* -- toast notification ----------------------------------------------- */
QFrame#Toast {{
    background: {p.surface}; border: 1px solid {p.border};
    border-left: 4px solid {p.success}; border-radius: 8px;
}}
QFrame#Toast QLabel {{ background: transparent; color: {p.text}; }}
QFrame#ToastError {{
    background: {p.surface}; border: 1px solid {p.border};
    border-left: 4px solid {p.error}; border-radius: 8px;
}}
QFrame#ToastError QLabel {{ background: transparent; color: {p.text}; }}
"""
