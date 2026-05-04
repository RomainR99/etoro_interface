# Déploiement production

## Secrets (API eToro)

Les clés ne doivent pas être lisibles par tout le monde. En production, les mettre dans un fichier système lu par systemd :

1. `sudo install -d -m 755 /etc/etoro`
2. Copier `etoro-interface.env.example` vers `/etc/etoro/interface.env`, renseigner `ETORO_API_KEY` et `ETORO_USER_KEY`
3. `sudo chmod 600 /etc/etoro/interface.env` et propriétaire root (ou root uniquement en lecture)

Référencer ce fichier avec `EnvironmentFile=/etc/etoro/interface.env` dans l’unité Gunicorn et dans `sync-trader-posts.service`. Aucun `.env` dans le dépôt n’est nécessaire sur le serveur : Python reçoit les variables déjà injectées.

Optionnel : définir `ETORO_ENV_FILE=/etc/etoro/interface.env` si tu préfères aussi que `env_load.py` charge explicitement ce fichier (redondant avec systemd, utile pour des lancements manuels sans export).

## Sync quotidien des posts

1. Ajuster chemins et `User=` dans `sync-trader-posts.service` (l’utilisateur doit écrire dans `data/`).
2. Installer le service + timer (voir commentaires en tête de `sync-trader-posts.service`).
   - Le timer fourni déclenche le job à **06:00** chaque jour (heure serveur, généralement UTC).
3. Tester : `sudo systemctl start sync-trader-posts.service` puis `journalctl -u sync-trader-posts.service -e`

L’application recharge le JSON des posts quand le fichier sur disque change (mtime) ; pas besoin de redémarrer Gunicorn après le sync.

## Gunicorn

Voir `gunicorn-etoro.service.example` : mêmes `EnvironmentFile` et même `User` que le sync pour des permissions cohérentes sur `data/`.
