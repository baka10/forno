# deploy/pgadmin

Auto-provisions one pgAdmin server connection to the `forno` Postgres instance, fully passwordless
— open http://localhost:5050, log into pgAdmin itself (`admin@forno.dev` / `admin`, both
overridable via `PGADMIN_DEFAULT_EMAIL`/`PGADMIN_DEFAULT_PASSWORD`), and the `forno` server in the
tree is already connected. No second password prompt for Postgres itself.

## Why the entrypoint override exists

pgAdmin only accepts a `PassFile` (a `.pgpass`-format file), not a plaintext password, in
`servers.json` — that restriction is deliberate on pgAdmin's part, not something to work around
with a different config key.

`.pgpass` has a hard requirement: the file must be mode `600`, or pgAdmin refuses to use it (same
rule `libpq` enforces for `~/.pgpass`). A bind-mounted file keeps its *host* ownership inside the
container, and pgAdmin's container runs as its own non-root user — so a host-owned, `600`-mode file
is usually unreadable by the container process, and git doesn't reliably preserve exact permission
bits across a clone either way.

The fix (`docker-compose.yml`'s `pgadmin` service `entrypoint`): mount `pgpass` read-only, copy it
to `/tmp/pgpass` inside the container at startup, `chmod 600` it there — now correctly owned by the
container's own user — then hand off to the image's real entrypoint. `servers.json`'s `PassFile`
points at that same `/tmp/pgpass`.

## Changing the credentials

Edit `pgpass` (format: `hostname:port:database:username:password`, `*` wildcards allowed) to match
whatever `POSTGRES_USER`/`POSTGRES_PASSWORD` the `postgres` service in `docker-compose.yml` is
actually running — the two need to stay in sync since nothing cross-checks them.
