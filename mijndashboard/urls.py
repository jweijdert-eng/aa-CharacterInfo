"""App URLs — Finance."""

from django.urls import path

from mijndashboard import views

app_name = "mijndashboard"

urlpatterns = [
    # Het dashboard is de voorpagina; de wallet blijft op z'n eigen pad staan,
    # zodat oude links en bladwijzers naar /wallet/ blijven werken.
    path("", views.dashboard, name="index"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("local/", views.local, name="local"),
    path("local/standings/", views.local_standings, name="local_standings"),
    path("wallet/", views.wallet, name="wallet"),
    path("contracts/", views.contracts, name="contracts"),
    path("ratting/", views.ratting, name="ratting"),
    path("mining/", views.mining, name="mining"),
    path("pi/", views.pi, name="pi"),
    path("markt/", views.markt, name="markt"),
    path("mail/", views.mail, name="mail"),
    path("koppelen/", views.koppelen, name="koppelen"),
]
