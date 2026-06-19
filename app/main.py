"""VAbk Studio — application entry point."""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QMainWindow, QTabWidget

from . import APP_NAME, __version__
from .paths import app_root
from .settings import derive_workspace, load_config, save_config
from .ui.abogen_tab import AbogenTab
from .ui.pipeline_tab import PipelineTab
from .ui.settings_tab import SettingsTab
from .ui.video_tab import VideoTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1040, 680)
        self.cfg = load_config()

        self.tabs = QTabWidget()
        self.pipeline_tab = PipelineTab(self.cfg, save_config)
        self.tabs.addTab(self.pipeline_tab, "Full Pipeline")
        self.abogen_tab = AbogenTab(self.cfg, save_config)
        self.tabs.addTab(self.abogen_tab, "Abogen")
        self.video_tab = VideoTab(self.cfg, save_config)
        self.tabs.addTab(self.video_tab, "FFMPEG")
        self.settings_tab = SettingsTab(self.cfg, save_config, self.video_tab.apply_settings)
        self.tabs.addTab(self.settings_tab, "Settings")
        self.setCentralWidget(self.tabs)


def _maybe_first_run(smoke: bool) -> None:
    """On first launch, choose a workspace base and create the media folders.

    Defaults to inside the app folder. Skipped (headless default) under --smoke.
    """
    cfg = load_config()
    if cfg.get("workspace_configured"):
        return
    base = None
    if not smoke:
        from .ui.first_run_dialog import FirstRunDialog
        dlg = FirstRunDialog(default_base=str(app_root()))
        if dlg.exec():
            base = dlg.chosen_base()
    base = base or str(app_root())
    cfg.update(derive_workspace(base))
    cfg["workspace_configured"] = True
    for key in ("input_folder", "audio_folder", "output_folder"):
        try:
            Path(cfg[key]).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    save_config(cfg)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    smoke = "--smoke" in sys.argv
    _maybe_first_run(smoke)
    win = MainWindow()
    win.show()
    if smoke:  # build verification: construct, process events, exit
        for _ in range(5):
            app.processEvents()
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
