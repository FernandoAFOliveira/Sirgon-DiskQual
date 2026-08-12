# Sirgon DiskQual installer operator configuration
# This file is appended to the public release installer by GitHub Actions.

configure_inventory_operator() {
    local operator="${SUDO_USER:-}"
    local sudoers_file="/etc/sudoers.d/sirgon-diskqual-operator"
    local operator_dir="/opt/diskqual/operator"

    if [ -z "$operator" ] || [ "$operator" = "root" ]; then
        mapfile -t operator_candidates < <(
            getent passwd | awk -F: '$3 >= 1000 && $3 < 65534 && $7 !~ /(nologin|false)$/ {print $1}'
        )
        if [ "${#operator_candidates[@]}" -eq 1 ]; then
            operator="${operator_candidates[0]}"
        fi
    fi

    if [ -z "$operator" ] || [ "$operator" = "root" ] || ! id "$operator" >/dev/null 2>&1; then
        warn "Could not determine a non-root Sirgon DiskQual operator."
        warn "UI inventory/test actions will require normal sudo authentication until an operator is configured."
        return 0
    fi

    case "$operator" in
        *[!A-Za-z0-9._-]*)
            warn "Operator name '$operator' contains unsupported characters; operator sudo rule was not created."
            return 0
            ;;
    esac

    info "Configuring Sirgon DiskQual operator privileges for: $operator"
    mkdir -p /etc/sudoers.d "$operator_dir"
    chown "$operator":"$(id -gn "$operator")" "$operator_dir"
    chmod 700 "$operator_dir"

    cat >"$sudoers_file" <<EOF
$operator ALL=(root) NOPASSWD: /usr/local/bin/diskqual inventory
$operator ALL=(root) NOPASSWD: /usr/local/bin/diskqual _smart-long-root
$operator ALL=(root) NOPASSWD: /usr/local/bin/diskqual _surface-root --yes
$operator ALL=(root) NOPASSWD: /usr/local/bin/diskqual _locate-root on
$operator ALL=(root) NOPASSWD: /usr/local/bin/diskqual _locate-root off
$operator ALL=(root) NOPASSWD: /usr/local/bin/diskqual _locate-root check
EOF
    chmod 440 "$sudoers_file"
    rm -f /etc/sudoers.d/sirgon-diskqual-inventory

    if command -v visudo >/dev/null 2>&1; then
        if ! visudo -cf "$sudoers_file" >/dev/null; then
            rm -f "$sudoers_file"
            fail "Could not validate the Sirgon DiskQual operator sudo rule."
        fi
    fi

    ok "UI inventory, phased testing, and locate privileges configured for $operator"
}

configure_inventory_operator
