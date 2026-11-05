# Daylist

A task workspace with All, Today, and custom folders. Each account has its own private workspace. Select a task to see its details and manage subtasks and notes. Click the title, date, or folder in the details pane to edit it. Press Enter or tap the plus icon to add a task or subtask. Subtasks have independent checkboxes and optional due dates. Click a folder's title above the task list to edit it in place. Press Enter or click away to save, or Escape to cancel. Switching between All, Today, folders, and task details does not reload the page. Task and folder changes save in the background; unfinished edits in other forms are preserved. Deleting a folder leaves its tasks in All, without a folder.

Click a task date or calendar icon to move it to Today, Tomorrow, Next week (seven days from today), or No date. In the quick-add field, finish a title with today, tomorrow, or next week to see a date suggestion. Dismiss the suggestion to keep the words in the title. Use the flag in task details to mark a task as a priority.

Today shows tasks whose due date matches the configured time zone. The theme follows your system until you choose light or dark in the sidebar. That choice stays in the current browser.

<table>
  <tr>
    <td width="50%" valign="top"><a href="docs/screenshots/daylist-light.jpg"><img src="docs/screenshots/daylist-light.jpg" width="100%" alt="Daylist in light mode showing example tasks grouped by due date, with notes and subtasks open for a selected task"></a></td>
    <td width="50%" valign="top"><a href="docs/screenshots/daylist-dark.jpg"><img src="docs/screenshots/daylist-dark.jpg" width="100%" alt="Daylist in dark mode showing the Studio folder and the calendar for a selected task"></a></td>
  </tr>
</table>

## Install with Docker

1. Download `compose.yaml` from the [latest release](https://github.com/charya-s/daylist/releases/latest) into a new directory.
2. Create a `.env` file next to it and set `DJANGO_SECRET_KEY` to a private random value of at least 50 characters. The app is available on port 8000 at your server's IP address; no hostname configuration is needed. For public access, follow the HTTPS setup below.
3. From that directory, run:

   ```sh
   docker compose up -d
   ```

4. Open `http://YOUR_SERVER_IP:8000` (or http://localhost:8000 on the server itself) and click **Create an account**. Choose a username and password to open your own workspace. Other people can sign up from the same page; each account has its own tasks, folders, subtasks, and notes.

Registration is open to anyone who can reach the app. New usernames can contain up to 15 characters, using letters (A-Z, a-z) and numbers (0-9) only. Passwords must contain at least 14 characters and at least one number. No email address is required. Existing accounts continue to work. If you need an administrator account, you can still run `docker compose exec app python manage.py createsuperuser`.

On Linux or macOS, `openssl rand -hex 32` generates a suitable secret. In PowerShell, use:

```powershell
$bytes = New-Object byte[] 48
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
[Convert]::ToBase64String($bytes)
```

Save the generated value as `DJANGO_SECRET_KEY=your-generated-value` in `.env` and keep it private. To reset your password, run `docker compose exec app python manage.py changepassword YOUR_USERNAME`.

### Upgrade

Replace your install's `compose.yaml` with the file from the new release, keeping the same directory and `.env`. Run `docker compose pull` and then `docker compose up -d`. The app applies database migrations at startup. Back up the data volume before upgrading. If upgrading from a version with restricted hostnames, remove `APP_HOSTS` from `.env` to use the new default of accepting any hostname.

### Follow the latest release

To follow new stable releases without changing the version number each time, set the image in your install's Compose file to `ghcr.io/charya-s/daylist:latest`. Keep your existing environment settings, port mapping, and data volume. Run `docker compose pull` and `docker compose up -d` to upgrade. The running app updates when you run these commands.

Numbered image tags remain available if you prefer to stay on a specific version. The release workflow updates `latest` after publishing and testing a release; rerunning an older release does not move it backwards.

### Build from source

Use the repository's `compose.yaml` with `docker compose up -d --build`. Copy `.env.example` to `.env` and fill in the secret first.

## Serve it on your domain

Daylist accepts any hostname or IP address by default. To restrict access by hostname, optionally set `APP_HOSTS=tasks.example.com` or a comma-separated list of allowed hostnames and IP addresses. Set `APP_TIME_ZONE` if needed. Point DNS or DDNS to your server. Configure your reverse proxy to handle TLS and HTTP-to-HTTPS redirects, preserve the original host, and overwrite `X-Forwarded-Proto` with the browser-facing scheme. Daylist uses that header to mark cookies Secure for HTTPS requests.

The included Compose file publishes port 8000 on all network interfaces. When using a reverse proxy, keep the backend port private: for a proxy running on the host, change the port mapping to `"127.0.0.1:8000:8000"`. If your proxy runs in Docker on the same server, put it and Daylist on a shared Docker network, remove the `ports` mapping, then point the proxy to Daylist's network name on HTTP port 8000. In Nginx Proxy Manager, enable Force SSL for the proxy host. Keep Daylist's backend port private.

For a proxy running directly on the host, use the loopback mapping above. For example, add this to your host's Caddyfile and reload Caddy:

```caddyfile
tasks.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Allow inbound ports 80 and 443 for Caddy. Keep port 8000 private. Run `docker compose up -d` after changes to `.env`. Once HTTPS is working, sign in from a second device and confirm the same tasks appear.

The app uses Django sessions, CSRF protection, Argon2id password hashing, and separate shared limits of ten sign-in attempts and ten sign-up attempts per minute per app instance. Run a single app instance with the supplied worker configuration.

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


## Release notes

See [docs/releases](docs/releases) for the changes included in each version.
