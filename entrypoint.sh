#!/bin/sh
set -e

# Valeur par défaut si CRON_SCHEDULE n'est pas fournie
CRON_SCHEDULE="${CRON_SCHEDULE:-0 4 * * *}"
OUTPUT_DIR="${OUTPUT_DIR:-/media/streaming}"
EXTRA_ARGS="${EXTRA_ARGS:-}"

echo "=== Flemmix scraper - configuration cron ==="
echo "Planification : $CRON_SCHEDULE"
echo "Répertoire de sortie : $OUTPUT_DIR"

# Génère dynamiquement le fichier crontab à partir de la variable d'env
echo "$CRON_SCHEDULE python /app/flemmix-scraper-v16.py --output-dir $OUTPUT_DIR $EXTRA_ARGS >> /var/log/flemmix/cron.log 2>&1" > /etc/crontabs/root

mkdir -p /var/log/flemmix
touch /var/log/flemmix/cron.log

echo "=== Exécution immédiate au démarrage du conteneur (optionnel) ==="
python /app/flemmix-scraper-v16.py --output-dir "$OUTPUT_DIR" $EXTRA_ARGS || echo "Premier run échoué, cron prendra le relais."

echo "=== Démarrage du démon cron ==="
crond -f -l 2 &

# Affiche les logs en continu pour que 'docker logs' fonctionne
tail -f /var/log/flemmix/cron.log
