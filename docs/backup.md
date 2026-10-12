# Full backup and restore

`./local.sh backup` briefly stops the annotation app and worker while it snapshots PostgreSQL and the companion's edit database. It restarts them afterwards. This saves original image bytes (embedded in PostgreSQL records), Argilla responses/users/settings, per-user SQLite edits, and the generated credentials. Elasticsearch is rebuildable and is not backed up. Redis queues are not backed up; finish imports and wait for worker jobs before taking a backup.

## Restore to a fresh installation

Use the same release and an empty stack. Do not use these commands against an installation containing work you need to keep. Let `backup` point to the directory created by the backup command:

```bash
cd ~/persianvlm-local
backup=/absolute/path/to/backup
cp "$backup/credentials.env" .env
chmod 600 .env
docker compose up -d --wait postgres elasticsearch redis
docker compose exec -T postgres pg_restore -U postgres -d argilla --clean --if-exists < "$backup/postgres.dump"
docker compose run --rm --no-deps -T --entrypoint tar local xzf - -C /data < "$backup/local-data.tar.gz"
docker compose run --rm --no-deps -T --entrypoint sh argilla -c 'python -m argilla_server database migrate && python -m argilla_server search-engine reindex'
./local.sh start
```

Keep backups private: they contain passwords, API keys, account data, and annotations. A dataset export is portable annotation data, but does not replace a full backup of users, credentials and the local database.
