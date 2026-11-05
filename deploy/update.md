# Updating this deployment when upstream releases new changes

This deployment is built so that pulling a new `openJiuwen-ai/jiuwenswarm:develop`
release never requires resolving a merge conflict: `source` is one upstream
commit plus two purely-additive commits (`docker-compose.yml`, `deploy/`,
`.env.example` - nothing upstream-tracked is modified). See
`deploy/architecture.md` §0 for why. This file is the actual step-by-step
procedure; keep it updated if the procedure ever changes.

## 1. On your machine: rebase the deploy layer onto the new upstream commit

```bash
git fetch upstream develop
git checkout source
git log --oneline source          # find the current upstream base commit -
                                   # it's the one right before "Move the
                                   # login gate entirely outside the app"
git rebase --onto upstream/develop <old-upstream-base-commit> source
```

This should apply with **zero conflicts**. If it doesn't, something upstream
started touching `docker-compose.yml`/`deploy/` (shouldn't happen) - stop and
look at the conflict instead of forcing through it with `-X ours`/`-X theirs`.

**Sanity-check the new base before deploying.** This is the step that would
have caught a real problem hit once already: the upstream tip at the time
had a broken dependency pin (`agent-gateway` and the top-level package
pointed at two different `agent-core` commits, so `uv pip install .` failed
dependency resolution outright - not a network issue, a genuine upstream
bug). Skim `git log --oneline upstream/develop -10` for anything touching
`pyproject.toml` or the `agent-*` git refs, and if in doubt, roll back to the
last upstream commit you know resolves cleanly rather than always chasing
the very latest tip.

Push the result:

```bash
git push origin source
```

If this is rejected as non-fast-forward (it will be, after a rebase) and you
know nothing else has been pushed to `source` since your last pull:

```bash
git push origin source --force-with-lease
```

`--force-with-lease` refuses the push if someone else moved `origin/source`
since you last fetched it, so it won't silently clobber work you haven't
seen - unlike a plain `--force`. Never run a plain `git merge origin/source`
here to "resolve" a rejected push: that pulls the *old* history back in and
re-conflicts every file this design keeps conflict-free. If a push is
rejected, re-run the rebase above against the latest `origin/source`, don't
merge it.

## 2. On the server: pull and rebuild

```bash
ssh -i <your-key> ubuntu@43.163.117.68
cd /home/ubuntu/projects/jiuwenswarm
git pull origin source
docker compose build app
docker compose up -d app
```

Only `app` needs rebuilding for an upstream update - `authgate`, `nginx`,
`postgres`, `certbot` are deploy-owned and don't change just because
upstream released something.

**Known build flakiness, not specific to this deployment:**
- `uv pip install .` clones a few `agent-*` repos from `gitcode.com`. That
  link has been observed to drop mid-clone (`unexpected disconnect while
  reading sideband packet` / `GnuTLS recv error`) on this VM, unrelated to
  which repo or how big it is. **Just retry the build** - a different
  dependency succeeds each time, and it's converged within 2-5 attempts
  every time this has happened so far.
- If `pip install --user uv` fails with a hash mismatch against
  `pypi.tuna.tsinghua.edu.cn`, the Dockerfile's `PIP_INDEX_URL`/
  `UV_DEFAULT_INDEX` may need pointing at `https://pypi.org/simple`
  instead - this was a one-time local edit to `Dockerfile.claw` on this
  server (not committed, to keep `app`'s build 100% unmodified upstream);
  check whether it's still in place with
  `grep PIP_INDEX_URL Dockerfile.claw` before re-applying it.

## 3. Only if `nginx` itself got rebuilt/recreated

Not needed for a plain `app` update (step 2 doesn't touch nginx). Needed any
time `nginx` is recreated - e.g. `docker compose up -d --build nginx`, or
after `docker compose down` - because Compose only restores the networks it
knows about (`frontend`), and every isolated user's dedicated network
connection is established imperatively, not tracked in `docker-compose.yml`:

```bash
bash deploy/reconnect_user_networks.sh
```

It's idempotent and safe to run any time, including when nothing needed
reconnecting.

## 4. Verify

- Log in as each provisioned account, confirm the floating "Log out" button
  appears, click it, confirm it lands back on `/login`.
- `curl -s -o /dev/null -w "%{http_code}\n" https://workswarm.app/` with no
  cookie should be `302` (redirected to `/login`), never `200`.

## Rollback

If the new `app` build is bad: `docker compose up -d --build app` again
after `git checkout <previous-good-commit> -- .` (or just `git reset --hard
<previous-good-commit>` on the server checkout) rebuilds the last known-good
version. `authgate`'s Postgres data and every workspace volume are untouched
by any of this, so rolling `app` back and forth doesn't lose logins or chat
history either way.
