"""App URLs — Finance."""

from django.urls import path

from finance import views

app_name = "finance"

urlpatterns = [
    path("", views.wallet, name="index"),
    path("wallet/", views.wallet, name="wallet"),
    path("contracts/", views.contracts, name="contracts"),
    path("ratting/", views.ratting, name="ratting"),
    path("mining/", views.mining, name="mining"),
    path("pi/", views.pi, name="pi"),
    path("markt/", views.markt, name="markt"),
    path("mail/", views.mail, name="mail"),
    path("koppelen/", views.koppelen, name="koppelen"),
]
