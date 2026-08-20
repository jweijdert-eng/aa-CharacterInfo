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
    # PI hoort onder Industry; het oude pad blijft werken voor bladwijzers.
    path("industry/pi/", views.pi, name="industry_pi"),
    path("pi/", views.pi, name="pi"),
    # Industry heeft z'n eigen tabbalk; de sub-pagina staat in de URL zodat
    # elke sub-tab te delen en te bookmarken is.
    path("industry/", views.industry, name="industry"),
    path("industry/blueprints/", views.industry, {"sub": "blueprints"}, name="industry_blueprints"),
    path("industry/bouwproject/", views.industry, {"sub": "bouwproject"}, name="industry_bouwproject"),
    path("industry/bouwwinst/", views.industry, {"sub": "bouwwinst"}, name="industry_bouwwinst"),
    path("industry/bouwen-of-kopen/", views.industry, {"sub": "bouwenkopen"}, name="industry_bouwenkopen"),
    # Fleetsessies staan los van Mining en Ratting: het gaat hier om de
    # verdeling over meerdere mensen, niet om je eigen opbrengst.
    path("fleet/", views.fleet, name="fleet"),
    path("fleet/roam/", views.fleet_roam, name="fleet_roam"),
    path("fleet/kaart.json", views.fleet_kaart, name="fleet_kaart"),
    path("fleet/fc/", views.fleet_koppelen, name="fleet_koppelen"),
    path("fleet/<int:sessie_id>/", views.fleet_sessie, name="fleet_sessie"),
    path("markt/", views.markt, name="markt"),
    path("mail/", views.mail, name="mail"),
    path("koppelen/", views.koppelen, name="koppelen"),
]
