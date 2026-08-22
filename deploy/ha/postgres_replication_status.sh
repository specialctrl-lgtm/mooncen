#!/usr/bin/env bash
set -euo pipefail

cat <<'INFO'
== Local PostgreSQL role ==
INFO
sudo -u postgres psql -At -F $'\t' -c "SELECT CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END AS role;"

if sudo -u postgres psql -At -c "SELECT pg_is_in_recovery();" | grep -qx t; then
  cat <<'INFO'
== Standby replay status ==
INFO
  sudo -u postgres psql -x -c "
    SELECT
      pg_is_in_recovery() AS in_recovery,
      pg_last_wal_receive_lsn() AS receive_lsn,
      pg_last_wal_replay_lsn() AS replay_lsn,
      now() - pg_last_xact_replay_timestamp() AS replay_delay;
  "
else
  cat <<'INFO'
== Primary replication clients ==
INFO
  sudo -u postgres psql -x -c "
    SELECT
      application_name,
      client_addr,
      state,
      sync_state,
      sent_lsn,
      write_lsn,
      flush_lsn,
      replay_lsn,
      write_lag,
      flush_lag,
      replay_lag
    FROM pg_stat_replication
    ORDER BY client_addr;
  "
  cat <<'INFO'
== Primary replication slots ==
INFO
  sudo -u postgres psql -x -c "
    SELECT slot_name, slot_type, active, restart_lsn, confirmed_flush_lsn
    FROM pg_replication_slots
    ORDER BY slot_name;
  "
fi
