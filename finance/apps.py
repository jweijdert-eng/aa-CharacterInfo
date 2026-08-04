"""App Configuration"""

from django.apps import AppConfig

from finance import __version__


class FinanceConfig(AppConfig):
    name = "finance"
    label = "finance"
    verbose_name = f"Finance v{__version__}"
