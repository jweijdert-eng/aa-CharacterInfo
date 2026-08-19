"""Mijn Dashboard — Alliance Auth-plugin met je eigen wallet, contracten,
ratting, mining, PI, markt en mail op één plek.

De plugin heette intern `finance` (en zichtbaar Finance → Character Info). Sinds
v3.0.0 heet ook de binnenkant `mijndashboard`: package, app-label, URL-namespace
en pip-naam. Dat is geen cosmetische wijziging — het app-label staat in
`django_migrations` en `django_content_type`, en daar hangt de permissie aan.
Zie de README voor wat een bestaande installatie moet doen.
"""

__version__ = "3.1.0"
__title__ = "Mijn Dashboard"
