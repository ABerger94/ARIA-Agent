"""VideoPanel adapted from Mark-LVI-ARIA's in-HUD video player.

Source: https://github.com/ABerger94/Mark-LVI-ARIA/blob/61a47b0/ui.py
Original MARK LVI: Copyright FatihMakes; ARIA fork modifications: Alek Berger.
Adapted widget retains CC BY-NC 4.0 terms. See LICENSES/Mark-LVI.txt.
Changes: the source spreads playback across its MainWindow (a
QGraphicsVideoItem scene, transport wiring, mute sync); this adaptation
isolates it as one self-contained QWidget on QVideoWidget with the same
facade-shaped API — show_video / stop_video / seek / seek_percent /
set_muted — because seek is the point: it is the control the old
external-player flow never had. Sources are local files and direct
media URLs only; page-URL resolution (the source uses yt-dlp for
YouTube pages) is deliberately not ported.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSlider,
                             QVBoxLayout, QWidget)

# QtMultimedia is part of PyQt6, so it costs no new dependency — but
# its plugins can be absent on a stripped-down system, so a failure
# here disables the panel rather than stopping the app (the source's
# own guard, kept).
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    HAVE_VIDEO = True
except Exception:  # noqa: BLE001 - reported by availability(), never fatal
    QAudioOutput = QMediaPlayer = QVideoWidget = None
    HAVE_VIDEO = False


def fmt_ms(ms: int) -> str:
    """Milliseconds -> 'M:SS' or 'H:MM:SS' for the transport label."""
    s = max(0, int(ms) // 1000)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f'{h}:{m:02d}:{sec:02d}'
    return f'{m}:{sec:02d}'


class VideoPanel(QWidget):
    """In-window video player: picture, title, transport, seek, mute.

    Nothing here blocks and nothing auto-plays a source it was not
    handed: show_video() is the only entry point, called by the
    dashboard (and, through it, by any future tool wiring).
    """

    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        if not HAVE_VIDEO:
            raise RuntimeError('QtMultimedia unavailable')
        self._source = ''
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self._title_label = QLabel('')
        self._title_label.setWordWrap(True)
        lay.addWidget(self._title_label)

        self._view = QVideoWidget()
        self._player.setVideoOutput(self._view)
        lay.addWidget(self._view, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self._play_btn = QPushButton('Pause')
        self._play_btn.clicked.connect(self.toggle_play)
        row.addWidget(self._play_btn)
        self._seek = QSlider(Qt.Orientation.Horizontal)
        self._seek.setRange(0, 0)
        self._seek.sliderMoved.connect(self.seek)
        row.addWidget(self._seek, 1)
        self._time_label = QLabel('0:00 / 0:00')
        row.addWidget(self._time_label)
        self._mute_btn = QPushButton('Mute')
        self._mute_btn.clicked.connect(
            lambda: self.set_muted(not self._audio.isMuted()))
        row.addWidget(self._mute_btn)
        close_btn = QPushButton('Close')
        close_btn.clicked.connect(self.closed.emit)
        row.addWidget(close_btn)
        lay.addLayout(row)

        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.playbackStateChanged.connect(self._on_state)

    # ---- facade-shaped API (mirrors the source's JarvisUI video calls)

    def show_video(self, source: str, title: str = '',
                   muted: bool = False) -> bool:
        """Load a local file or direct media URL and start playing."""
        source = str(source or '')
        if not source:
            return False
        url = (QUrl.fromLocalFile(os.path.abspath(source))
               if os.path.exists(source) else QUrl(source))
        if not url.isValid():
            return False
        self._source = source
        self._title_label.setText(
            title or os.path.basename(source.rstrip('/')) or source)
        self.set_muted(muted)
        self._player.setSource(url)
        self._player.play()
        return True

    def stop_video(self) -> None:
        self._player.stop()
        self._player.setSource(QUrl())
        self._source = ''

    def seek(self, position_ms: int) -> int:
        """Seek, clamped to the media. Returns the target position;
        with no media loaded (duration 0) it is a no-op returning 0 —
        an honest refusal to seek what is not there."""
        duration = self._player.duration()
        if duration <= 0:
            return 0
        target = max(0, min(int(position_ms), int(duration)))
        self._player.setPosition(target)
        return target

    def seek_percent(self, pct: float) -> int:
        """Seek to a percentage of the real duration ('halfway' = 50).
        Returns the position sought, 0 when nothing is loaded."""
        duration = self._player.duration()
        if duration <= 0:
            return 0
        pct = max(0.0, min(100.0, float(pct)))
        return self.seek(int(duration * pct / 100.0))

    def position_ms(self) -> int:
        return int(self._player.position())

    def duration_ms(self) -> int:
        return int(self._player.duration())

    def is_playing(self) -> bool:
        return (self._player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState)

    def set_playing(self, playing: bool) -> None:
        if playing:
            self._player.play()
        else:
            self._player.pause()

    def toggle_play(self) -> None:
        self.set_playing(not self.is_playing())

    def set_muted(self, muted: bool) -> None:
        self._audio.setMuted(bool(muted))
        self._mute_btn.setText('Unmute' if muted else 'Mute')

    def set_volume(self, level: int) -> None:
        """0-100, the scale the rest of the window already speaks."""
        self._audio.setVolume(max(0, min(100, int(level))) / 100.0)

    # ---- player signal plumbing

    def _on_position(self, position: int) -> None:
        if not self._seek.isSliderDown():
            self._seek.setValue(int(position))
        self._time_label.setText(
            f'{fmt_ms(position)} / {fmt_ms(self._player.duration())}')

    def _on_duration(self, duration: int) -> None:
        self._seek.setRange(0, int(duration))
        self._time_label.setText(
            f'{fmt_ms(self._player.position())} / {fmt_ms(duration)}')

    def _on_state(self, _state) -> None:
        self._play_btn.setText('Pause' if self.is_playing() else 'Play')
