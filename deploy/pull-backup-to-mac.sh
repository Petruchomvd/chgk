#!/bin/zsh

set -euo pipefail

server_alias="${CHGK_SERVER_ALIAS:-myosserver}"
backup_root="${CHGK_BACKUP_DIR:-${HOME}/Backups/myos/chgk}"
keep_backups="${CHGK_KEEP_BACKUPS:-4}"
timestamp="$(date '+%Y-%m-%d_%H-%M-%S')"
final_dir="${backup_root}/${timestamp}"
remote_dir="/home/matvey/.chgk-backup-${timestamp}"

mkdir -p "${backup_root}"
incoming_dir="$(mktemp -d "${backup_root}/.incoming-${timestamp}.XXXXXX")"

cleanup() {
  rm -rf "${incoming_dir}"
  ssh "${server_alias}" "rm -rf '${remote_dir}'" >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM

ssh "${server_alias}" "set -eu
mkdir -m 700 '${remote_dir}'
sudo sqlite3 /var/lib/chgk/chgk_analysis.db \".backup '${remote_dir}/chgk_analysis.db'\"
sudo sqlite3 /var/lib/chgk/training.db \".backup '${remote_dir}/training.db'\"
sudo chown matvey:matvey '${remote_dir}/chgk_analysis.db' '${remote_dir}/training.db'
chmod 600 '${remote_dir}/chgk_analysis.db' '${remote_dir}/training.db'
"

rsync -ah "${server_alias}:${remote_dir}/chgk_analysis.db" "${incoming_dir}/"
rsync -ah "${server_alias}:${remote_dir}/training.db" "${incoming_dir}/"

main_check="$(sqlite3 "file:${incoming_dir}/chgk_analysis.db?immutable=1" 'PRAGMA quick_check;')"
training_check="$(sqlite3 "file:${incoming_dir}/training.db?immutable=1" 'PRAGMA quick_check;')"

if [[ "${main_check}" != "ok" || "${training_check}" != "ok" ]]; then
  print -u2 "Проверка резервной копии не пройдена."
  exit 1
fi

(
  cd "${incoming_dir}"
  shasum -a 256 chgk_analysis.db training.db > SHA256SUMS
)

mv "${incoming_dir}" "${final_dir}"
trap - EXIT INT TERM
ssh "${server_alias}" "rm -rf '${remote_dir}'"

if [[ "${keep_backups}" == <-> && "${keep_backups}" -gt 0 ]]; then
  backup_dirs=("${(@f)$(find "${backup_root}" -mindepth 1 -maxdepth 1 -type d -name '20??-??-??_??-??-??' | sort)}")
  while (( ${#backup_dirs[@]} > keep_backups )); do
    oldest="${backup_dirs[1]}"
    rm -rf "${oldest}"
    backup_dirs[1]=()
  done
fi

print "Резервная копия создана и проверена: ${final_dir}"
