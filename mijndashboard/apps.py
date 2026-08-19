"""App Configuration"""

from django.apps import AppConfig

from mijndashboard import __version__


class FinanceConfig(AppConfig):
    name = "mijndashboard"
    label = "mijndashboard"
    verbose_name = f"Mijn Dashboard v{__version__}"
