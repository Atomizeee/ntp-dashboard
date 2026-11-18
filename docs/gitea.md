# Testing in Gitea, then moving to GitHub

This repository has CI for both. `.gitea/workflows/ci.yml` runs on Gitea (tests, JavaScript check, image build). `.github/workflows/ci.yml` runs on GitHub and also publishes a multi-architecture image to GitHub Container Registry.

## 1. Create the repository in Gitea

Create an empty repository (no README, no license) named `ntp-dashboard`, then from the project folder:

```bash
git init -b main
git add .
git commit -m "Initial commit"
git remote add origin https://<your-gitea>/<user>/ntp-dashboard.git
git push -u origin main
```

Gitea asks for your username and a password or access token (Settings > Applications).

## 2. Turn on Actions

- Server: `app.ini` needs `[actions] ENABLED = true` (the default in recent Gitea versions).
- Repository: Settings > enable **Actions** (Repository > Settings > Advanced settings).

## 3. Run a runner

The runner is `act_runner`. On a Docker host:

1. In Gitea open Repository (or Site Administration) > Settings > Actions > Runners > **Create new runner** and copy the registration token.
2. Start the runner, with access to the Docker socket so the image job can build:

```yaml
# docker-compose.yml for the runner (a separate folder, not this project)
services:
  runner:
    image: gitea/act_runner:latest
    restart: unless-stopped
    environment:
      GITEA_INSTANCE_URL: https://<your-gitea>
      GITEA_RUNNER_REGISTRATION_TOKEN: <token>
      GITEA_RUNNER_NAME: runner1
      GITEA_RUNNER_LABELS: ubuntu-latest:docker://catthehacker/ubuntu:act-latest
    volumes:
      - ./data:/data
      - /var/run/docker.sock:/var/run/docker.sock
```

The label `ubuntu-latest` must match `runs-on` in the workflow. The runner needs internet access to download `actions/checkout` and `actions/setup-python` (Gitea fetches them from github.com by default).

Mounting the Docker socket gives workflows control of that Docker host. Use a dedicated runner machine or VM for projects you don't fully trust.

## 4. Check it ran

Push a commit and open the repository's **Actions** tab. Expected: `test` passes with 37 tests, then `image` builds and prints `ok`.

| Problem | Fix |
|---|---|
| Job stays "waiting" | No runner has the label `ubuntu-latest`, or the runner is offline |
| `docker: command not found` or cannot connect | The runner container needs `/var/run/docker.sock`, and the job image needs the Docker CLI (`catthehacker/ubuntu:act-latest` has it) |
| `actions/checkout` fails to download | The runner can't reach github.com, or set `DEFAULT_ACTIONS_URL` in Gitea's `app.ini` to a mirror |

## 5. Test the dashboard itself

Besides CI, deploy it the normal way on any Docker host:

```bash
git clone https://<your-gitea>/<user>/ntp-dashboard.git
cd ntp-dashboard && cp .env.example .env     # set SERVER_HOST
docker compose up -d --build
```

## 6. Move to GitHub

1. Create an empty repository on GitHub (no README or license).
2. Push everything, including tags:

```bash
git remote add github https://github.com/<you>/ntp-dashboard.git
git push github main --tags
```

3. On GitHub the workflow in `.github/workflows/` runs by itself. Under Settings > Actions > General, set **Workflow permissions** to *Read and write* so the image can be published to `ghcr.io/<you>/ntp-dashboard`.
4. Replace `<you>` in the README's `git clone` line.
5. Optional: keep Gitea as the main copy and let it mirror to GitHub: Repository > Settings > Mirror settings > **Push mirror**, with a GitHub personal access token.

Issues, pull requests and wiki pages don't move with `git push`. Gitea's migration tool can import from GitHub, but not export to it, so if you have any, recreate them by hand.
