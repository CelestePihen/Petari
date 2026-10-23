#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

usage() {
    cat <<'EOF'
Usage: ./build.sh [RMGP01|RMGE01] [options]

  ./build.sh RMGP01                     Build the independent Kamek module/bootstrap
  ./build.sh RMGE01 --game-dir /.../DATA Prepare/update a private test game copy
  ./build.sh RMGP01 --diagnostic        Build Petari without Archipelago objects

The default region is RMGP01. No existing Dolphin game is installed automatically.
Use: python3 tools/build_archipelago.py --help for Kamek deployment options.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi
region="${1:-RMGP01}"
if (( $# )); then shift; fi
case "$region" in
    RMGP01|RMGE01) ;;
    *) printf 'Unsupported region: %s\n' "$region" >&2; usage >&2; exit 2 ;;
esac
python_executable="${PYTHON:-python3}"
if [[ "${1:-}" == "--diagnostic" ]]; then
    shift
    if (( $# )); then
        printf '%s\n' 'The diagnostic target does not accept deployment options.' >&2
        exit 2
    fi
    "$python_executable" configure.py --version "$region" --non-matching --map \
        --dtk build/tools/dtk --compilers build/compilers --binutils build/binutils \
        --wrapper build/tools/wibo --product-sel "orig/$region/files/ModuleData/product.sel"
    ninja "build/$region/main.dol"
    printf 'Petari diagnostic: build/%s/main.dol (no Archipelago module)\n' "$region"
else
    exec "$python_executable" tools/build_archipelago.py "$region" "$@"
fi
