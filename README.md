# Daylist

A single-user task workspace with All, Today, and custom lists. Select a task to see its details and manage its subtasks. Open the task menu to change its title, due date, list, or notes. Press Enter or tap the plus icon to add a task or subtask. Subtasks have independent checkboxes and optional due dates. Deleting a list leaves its tasks in All, without a list.

Click a task date or calendar icon to move it to Today, Tomorrow, Next week (seven days from today), or No date. In the quick-add field, finish a title with today, tomorrow, or next week to see a date suggestion. Dismiss the suggestion to keep the words in the title. Use the flag in task details to mark a task as a priority.

Today shows tasks whose due date matches the configured time zone. The theme follows your system until you choose light or dark in the sidebar. That choice stays in the current browser.

## Update an existing Docker Desktop install

Extract the new ZIP to a separate folder. Copy the new `daylist` source folder into your existing Daylist install, merging it with the existing `daylist` folder and replacing matching files. Keep using your original project folder and `.env`.

From the original folder containing `compose.yaml`, run:

```powershell
docker compose up -d --build
```

The container applies the database migration at startup. Existing accounts, tasks, and due dates are preserved. Refresh Daylist after the container starts.

## Run with Docker

1. Install Docker with Compose and open a terminal in this directory.
2. Copy `.env.example` to `.env`. Set `DJANGO_SECRET_KEY` to a random value of at least 50 characters. On Linux, run `openssl rand -hex 32` to generate one. Keep the key private.
3. For a test on the Docker host, set `LOCAL_HTTP=1` and leave `APP_HOSTS` at its default. For public hosting, follow the HTTPS steps below.
4. Start the app and create your account. Wait for migrations to finish before creating the account. Use a long, unique password.

       docker compose up -d --build
       docker compose logs app
       docker compose exec app python manage.py createsuperuser

5. For a local test, open http://localhost:8000. For public hosting, use your HTTPS address.

To reset your password from the server, run `docker compose exec app python manage.py changepassword YOUR_USERNAME`. Account creation and password resets happen through the server terminal.

## Serve it on your domain

Set `LOCAL_HTTP=0`, `APP_HOSTS=tasks.example.com` (replace with your domain), and `APP_TIME_ZONE` to your IANA time zone if it differs from Asia/Singapore. Point DNS to your server. The included Compose file binds the app to the host's loopback address. On the same host, configure a reverse proxy that terminates HTTPS and overwrites the forwarded protocol header. For example, add this to your host's Caddyfile and reload Caddy:

```caddyfile
tasks.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Allow inbound ports 80 and 443 for Caddy. Keep port 8000 private. Run `docker compose up -d` after changes to `.env`. Once HTTPS is working, sign in from a second device and confirm the same tasks appear.

The app uses Django sessions, CSRF protection, Argon2id password hashing, and a shared limit of ten sign-in attempts per minute per app instance. Run a single app instance with the supplied worker configuration. To refresh the base image and application dependencies, use `docker compose build --pull --no-cache` and then `docker compose up -d`.

## Storage and backups

SQLite lives in the persistent `task_data` volume. For encryption at rest, put Docker's volume storage on an encrypted disk and store backups on encrypted storage. A Docker volume alone does not encrypt its contents.

For a consistent backup on a Linux host, run these commands from the project directory, writing the archive to your encrypted storage. The app is briefly offline.

```sh
docker compose stop app
docker compose run --rm --no-deps -T app tar -C /data -cf - . > /path/on/encrypted-storage/daylist.tar
docker compose start app
```

Keep `.env` separately in secure storage. To test restoration, use a fresh Compose project with a different project name and no running Daylist instance on port 8000. From that project, restore the archive to the fresh volume before starting the app:

```sh
docker compose run --rm --no-deps -T app tar -C /data -xf - < /path/on/encrypted-storage/daylist.tar
docker compose up -d
```

Sign in and confirm that your tasks and dates match the backup.

