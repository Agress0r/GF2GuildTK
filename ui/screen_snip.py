"""
ScreenSnipOverlay — fullscreen snipping tool.

Designed to run inside its own QApplication (via snip_proc.py).
Uses QDialog + exec() so it works reliably on Windows.

Public API:
    snip = ScreenSnipOverlay(pil_screenshot)
    crop = snip.exec()   # returns PIL Image on success, None on cancel
"""

from __future__ import annotations

import io

from PIL import Image

from PyQt6.QtCore import Qt, QRect, QPoint
from PyQt6.QtGui import (
    QPixmap, QPainter, QPen, QColor, QBrush, QFont,
    QImage,
)
from PyQt6.QtWidgets import QDialog, QApplication


class ScreenSnipOverlay(QDialog):
    """
    Fullscreen semi-transparent overlay.
    User drags a rectangle — call exec() to get the cropped PIL Image.
    Returns None on ESC / cancel.
    """

    # Visual constants matching app theme
    _OVERLAY_ALPHA  = 110
    _BORDER_COLOR   = QColor(240, 192, 64)
    _BORDER_WIDTH   = 2
    _HANDLE_COLOR   = QColor(240, 192, 64, 180)
    _HINT_BG        = QColor(13, 18, 32, 200)
    _HINT_FG        = QColor(160, 192, 240)

    def __init__(self, pil_screenshot: Image.Image):
        super().__init__(None)

        self._pil_full = pil_screenshot
        self.crop: Image.Image | None = None

        # Logical screen geometry
        screen = QApplication.primaryScreen()
        geom   = screen.geometry()
        self._logical_w = geom.width()
        self._logical_h = geom.height()

        # Scale factors: logical coords → physical PIL pixels
        self._sx = pil_screenshot.width  / self._logical_w
        self._sy = pil_screenshot.height / self._logical_h

        # Downscale screenshot to logical resolution for display
        buf = io.BytesIO()
        pil_screenshot.save(buf, "PNG")
        buf.seek(0)
        qimg = QImage.fromData(buf.read())
        self._pixmap = QPixmap.fromImage(qimg).scaled(
            self._logical_w, self._logical_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        self._start:     QPoint | None = None
        self._end:       QPoint | None = None
        self._selecting: bool          = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)

    # ------------------------------------------------------------------ #
    #  Public entry point                                                  #
    # ------------------------------------------------------------------ #

    def exec(self) -> Image.Image | None:
        """
        Blocking call. Shows the overlay and waits for user action.
        Returns the cropped PIL Image on success, None on cancel.
        """
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        super().exec()
        self.hide()
        return self.crop

    # ------------------------------------------------------------------ #
    #  Painting                                                            #
    # ------------------------------------------------------------------ #

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        painter.drawPixmap(0, 0, self._pixmap)
        painter.fillRect(self.rect(), QColor(0, 0, 0, self._OVERLAY_ALPHA))

        sel = self._selection_rect()
        if sel and sel.width() > 1 and sel.height() > 1:
            painter.drawPixmap(sel, self._pixmap, sel)

            painter.setPen(QPen(self._BORDER_COLOR, self._BORDER_WIDTH))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(sel)

            self._draw_handles(painter, sel)
            self._draw_size_label(painter, sel)

        self._draw_hint(painter)

    def _draw_handles(self, painter: QPainter, rect: QRect):
        sz = 6
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._HANDLE_COLOR))
        for x in (rect.left(), rect.center().x(), rect.right()):
            for y in (rect.top(), rect.center().y(), rect.bottom()):
                painter.drawRect(x - sz // 2, y - sz // 2, sz, sz)

    def _draw_size_label(self, painter: QPainter, rect: QRect):
        pw = int(rect.width()  * self._sx)
        ph = int(rect.height() * self._sy)
        text = f" {pw} × {ph} px "

        painter.setFont(QFont("Segoe UI", 10))
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(text)
        th = fm.height()
        tx = rect.right() - tw - 2
        ty = rect.top() - th - 4
        if ty < 0:
            ty = rect.bottom() + 4

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._HINT_BG))
        painter.drawRoundedRect(tx - 2, ty, tw + 4, th + 2, 3, 3)
        painter.setPen(self._BORDER_COLOR)
        painter.drawText(tx, ty + th - 2, text)

    def _draw_hint(self, painter: QPainter):
        text = "  Нажмите и перетащите для выбора области      ESC — отмена  "
        painter.setFont(QFont("Segoe UI", 11))
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(text)
        th = fm.height()
        x  = (self._logical_w - tw) // 2
        y  = 18

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._HINT_BG))
        painter.drawRoundedRect(x - 8, y, tw + 16, th + 10, 6, 6)
        painter.setPen(self._HINT_FG)
        painter.drawText(x, y + th + 2, text)

    # ------------------------------------------------------------------ #
    #  Mouse                                                               #
    # ------------------------------------------------------------------ #

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.position().toPoint()
            self._end   = self._start
            self._selecting = True

    def mouseMoveEvent(self, event):
        if self._selecting:
            self._end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._selecting:
            self._selecting = False
            self._end = event.position().toPoint()
            sel = self._selection_rect()
            if sel and sel.width() > 5 and sel.height() > 5:
                self._do_accept(sel)
            else:
                self._start = None
                self._end   = None
                self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _selection_rect(self) -> QRect | None:
        if self._start is None or self._end is None:
            return None
        return QRect(self._start, self._end).normalized()

    def _do_accept(self, sel: QRect):
        px = int(sel.x()      * self._sx)
        py = int(sel.y()      * self._sy)
        pw = int(sel.width()  * self._sx)
        ph = int(sel.height() * self._sy)
        self.crop = self._pil_full.crop((px, py, px + pw, py + ph))
        self.accept()
