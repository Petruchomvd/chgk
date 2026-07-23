#!/usr/bin/env bash

set -euo pipefail
umask 077

backup_root="/var/backups/chgk"
keep_backups=7
timestamp="$(date --utc '+%Y-%m-%d_%H-%M-%S')"
incoming_dir="$(mktemp -d "${backup_root}/.incoming-${timestamp}.XXXXXX")"
final_dir="${backup_root}/${timestamp}"

cleanup() {
  if [[ -d "${incoming_dir}" ]]; then
    rm -rf -- "${incoming_dir}"
  fi
}
trap cleanup EXIT INT TERM

sqlite3 "file:/var/lib/chgk/chgk_analysis.db?mode=ro" \
  ".backup '${incoming_dir}/chgk_analysis.db'"
sqlite3 "file:/var/lib/chgk/training.db?mode=ro" \
  ".backup '${incoming_dir}/training.db'"

main_check="$(sqlite3 "file:${incoming_dir}/chgk_analysis.db?immutable=1" 'PRAGMA quick_check;')"
training_check="$(sqlite3 "file:${incoming_dir}/training.db?immutable=1" 'PRAGMA quick_check;')"

if [[ "${main_check}" != "ok" || "${training_check}" != "ok" ]]; then
  echo "Database integrity check failed." >&2
  exit 1
fi

(
  cd "${incoming_dir}"
  sha256sum chgk_analysis.db training.db > SHA256SUMS
)

mv -- "${incoming_dir}" "${final_dir}"
trap - EXIT INT TERM

mapfile -t backup_dirs < <(
  find "${backup_root}" -mindepth 1 -maxdepth 1 -type d \
    -name '20??-??-??_??-??-??' -print | sort
)

while (( ${#backup_dirs[@]} > keep_backups )); do
  oldest="${backup_dirs[0]}"
  case "${oldest}" in
    "${backup_root}"/20??-??-??_??-??-??) rm -rf -- "${oldest}" ;;
    *) echo "Refusing to remove unexpected path: ${oldest}" >&2; exit 1 ;;
  esac
  backup_dirs=("${backup_dirs[@]:1}")
done

echo "Backup created and verified: ${final_dir}"
