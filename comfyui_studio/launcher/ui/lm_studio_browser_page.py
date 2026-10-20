"""Studio chat UI in its own WebEngine profile, separate from original LM Studio."""

import os

from ..core.constants import APP_DIR
from .browser_page import BrowserPage


class LmStudioBrowserPage(BrowserPage):
    PROFILE_NAME = "studio_lm_studio"
    PROFILE_DIR = os.path.join(APP_DIR, "webengine_profile_lm_studio")
    SETTINGS_TOOLTIP = "Вернуться к настройкам, не останавливая LM Studio"
    STOP_TOOLTIP = "Остановить приложение LM Studio"

    def __init__(self, loc=None, parent=None):
        super().__init__(loc, parent)
        self.resource_bar.hide()
