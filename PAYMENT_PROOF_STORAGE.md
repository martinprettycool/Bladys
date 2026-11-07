# Payment proof storage

Local development defaults to `media/payment_proofs` under the project root,
independent of the working directory. Existing files there remain accessible.
`PAYMENT_PROOFS_DIR` overrides the physical directory; relative values are also
resolved against the project root.

Production: set `APP_ENV=production` and an absolute `PAYMENT_PROOFS_DIR`
(for example `/data/payment_proofs`). Provision that directory on persistent
storage with access restricted to the application account before startup.
Startup fails if the directory is absent, not a directory, or cannot be written.
No temporary fallback is used. New POSIX files use mode 0600.
Windows access must be restricted through filesystem ACLs.

Database keys remain `media/payment_proofs/<filename>` regardless of storage
location. Changing configuration does not move files: ensure existing files are
available in the configured directory before switching it. No migration is needed.
Do not publish this directory through a web server or static mount. Retrieval
continues through the RECEPTION-authorized payment proof endpoint.
Back up the proof directory together with PostgreSQL; this configuration alone
does not provide persistence, backups, retention, or crash recovery.
