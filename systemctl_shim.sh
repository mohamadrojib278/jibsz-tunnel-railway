#!/bin/bash
# Shim systemctl untuk lingkungan Docker / Railway (tanpa systemd)
ACTION="$1"
shift || true

ARGS=()
for arg in "$@"; do
    if [[ "$arg" != --* && "$arg" != "-q" ]]; then
        ARGS+=("$arg")
    fi
done

SVC="${ARGS[0]:-}"
SVC="${SVC%.service}"

map_svc() {
    case "$1" in
        xray) echo "xray" ;;
        nginx) echo "nginx" ;;
        dropbear) echo "dropbear" ;;
        ssh|sshd) echo "sshd" ;;
        ssh-ws|ssh_ws) echo "ssh-ws" ;;
        cloudflared) echo "cloudflared" ;;
        cron) echo "cron" ;;
        webpanel|jibsz-webpanel) echo "webpanel" ;;
        *) echo "$1" ;;
    esac
}

TARGET="$(map_svc "$SVC")"

case "$ACTION" in
    daemon-reload)
        exit 0
        ;;
    is-active)
        if supervisorctl status "$TARGET" 2>/dev/null | grep -q "RUNNING"; then
            exit 0
        else
            exit 3
        fi
        ;;
    restart|reload)
        if [[ "$TARGET" == "nginx" ]]; then
            nginx -t >/dev/null 2>&1 && nginx -s reload >/dev/null 2>&1 || supervisorctl restart nginx >/dev/null 2>&1
        else
            supervisorctl restart "$TARGET" >/dev/null 2>&1 || true
        fi
        exit 0
        ;;
    start)
        supervisorctl start "$TARGET" >/dev/null 2>&1 || true
        exit 0
        ;;
    stop)
        supervisorctl stop "$TARGET" >/dev/null 2>&1 || true
        exit 0
        ;;
    *)
        exit 0
        ;;
esac
