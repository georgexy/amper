#!/bin/bash
# =====================================================================
#  Prima pornire a imaginii SD Amper (rulat o singură dată, ca root, de amper-firstboot.service).
#  Imaginea e la fel pentru toți, deci tot ce trebuie să fie unic pe fiecare Pi se face aici:
#  topicul ntfy, cheile SSH, machine-id. Cheile VAPID, cheia cookie-ului și baza de date le face
#  serviciul amper când pornește (lipsesc din imagine). Starea Tailscale apare la logarea din aplicație.
# =====================================================================
set -u
ETC=/etc/amper

if grep -q "REPLACE-TOPIC" "$ETC/config.yaml" 2>/dev/null; then
  TOPIC="baterii-$(python3 -c 'import secrets; print(secrets.token_hex(6))')"
  sed -i "s/REPLACE-TOPIC/$TOPIC/" "$ETC/config.yaml"
fi
chown root:amper "$ETC/config.yaml" && chmod 640 "$ETC/config.yaml"

# machine-id: imaginea îl are gol, systemd îl generează la pornire; aici doar îl fixăm pe card
[ -s /etc/machine-id ] || systemd-machine-id-setup
# cheile SSH (SSH e oprit; dacă e pornit mai târziu, cheile sunt ale acestui Pi)
ls /etc/ssh/ssh_host_*_key >/dev/null 2>&1 || ssh-keygen -A
chown -R amper:amper /var/lib/amper

rm -f "$ETC/.firstboot"
echo "amper: prima pornire gata"
