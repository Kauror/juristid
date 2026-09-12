# Operating the real-data host — access, identity and safe execution

How to reach the machine that serves the Chamber's register, how to prove it is
that machine, and how to run more than one command on it without losing half of
them. Everything here is about the *channel*; nothing here is a deployment step.

| What | Where |
| --- | --- |
| The release procedure, in order | [`README.md`](README.md) §"Deploying a release" |
| Backup, restore, rollback, disaster recovery | [`RECOVERY.md`](RECOVERY.md) |
| The gates a real-data step passes, as a checklist | [`../../docs/production-readiness.md`](../../docs/production-readiness.md) |
| Access, host identity, safe remote execution, transcript safety | **this file** |

This file is the canonical one for the four things in its last row, and it does
not restate the other three. Where a rule below has a longer explanation
somewhere else it links rather than copies: two procedures that disagree are
worse than one that is incomplete.

Two of the rules here were learned during the September 2026 releases, from the
two things that actually went wrong. They are written as rules because both
failures were silent — one dropped commands and still exited zero, the other
looked like a dead host and was not.

## Reaching the host

Two access paths are acceptable, and both are the same key to the same machine.

**Normally, Tailscale.** The address the tailnet publishes for this host, from
the workstation that holds the authorised key.

**When the tailnet is unavailable, the host's established LAN address.** During
a September 2026 release Tailscale was unavailable on the workstation. The host
was reachable on the LAN with the same key, and the release completed. So:

> Tailscale being unavailable is not evidence that the production host is
> unreachable, and it is not on its own a reason to postpone a release that is
> otherwise ready.

**Neither address is written here, and this file is not where either belongs.**
The repository is public. The address you use is whichever one your own SSH
configuration or the operator inventory already records for this host — a value
you had before you opened this file. The fallback is *the known LAN address of
the known host*; it is not "whatever on the network answers on port 22". Looking
for a host that responds and deploying to it is the failure the next section
exists to prevent, and it would find this host's neighbours as readily as this
host.

**Operator keys live in the Unraid GUI key store, not in `authorized_keys`.**
The WebGUI regenerates `authorized_keys` from `/boot/config/ssh/root.pubkeys`
by replacement, so a hand-appended key is erased the next time anybody opens
that screen — which presents as `Permission denied (publickey)` on a host where
every service is healthy. That is DR1-B, and
[`RECOVERY.md`](RECOVERY.md) carries it in full under *What is not yet fixed*,
including how to tell it from a failed flash drive.

## Proving you reached production

`ssh` succeeding proves that *a* host accepted the key. It does not prove which
one, and there is more than one wrong answer available: the synthetic rehearsal
`juristid-test` lives on this same host, and any other machine that accepts the
same key is one typo away.

So before any command that changes anything, ask the host what it is. All four
of these are read-only and none of them prints a secret.

```bash
uname -n
```

```bash
git -C /mnt/user/appdata/juristid-main/repo rev-parse HEAD
```

```bash
docker ps --filter label=com.docker.compose.project=juristid-main --format '{{.Names}} {{.Image}} {{.Status}}'
```

```bash
curl -s https://juristid.orgusaar.ee/healthz
```

What each one settles:

| | It proves |
| --- | --- |
| `uname -n` | the machine's own name, which is the cheapest thing to compare against the one you meant |
| the `rev-parse` | that `/mnt/user/appdata/juristid-main/repo` exists and is a checkout — that path is the production layout, and a host without it is not this deployment |
| the `docker ps` | that this project's containers are here, which image tag each is running, and whether they are healthy. The tag is the release identity, so this is also part of the "what is running now" a rollback needs |
| `healthz` | the running revision, through the public name — the only one of the four that also proves the tunnel is up |

The `docker ps` line filters on the Compose project label rather than on a name,
so it cannot report `juristid-test`'s containers as this stack's, and it prints
nothing at all on a host where this project does not exist — which is exactly
the answer you want when you are on the wrong machine.

The deployment preflight settles the same question more thoroughly and is where
it belongs before a release: it verifies the checkout, the target commit, the
Compose resolution, the environment file's mode and the free disk, and it names
the host it ran on in its header. Run it rather than inventing checks;
[`README.md`](README.md) has the invocation under *2. Preflight*.

### When the SSH host key has changed

**Stop.** A changed host key on a machine whose key nobody rotated is either a
rebuilt host or a different host answering, and both mean the next command would
run somewhere you did not choose.

Never work around it:

    ssh -o StrictHostKeyChecking=no …
    ssh -o UserKnownHostsFile=/dev/null …

Both replace the one check that noticed with a check that cannot notice again,
and they do it permanently, in a shell history somebody else will copy.
Accepting a changed fingerprint at the prompt is the same thing by hand.

The response is to establish what the host is through a channel that is not the
one in doubt — the Unraid administration interface, or the console — and to
update `known_hosts` only once the fingerprint is confirmed there. Until then
nothing runs.

## Running more than one command on the host

**Do not feed a multi-step script to the host over stdin.** Not in any of these
shapes:

    ssh host 'bash -s' < deploy.sh
    cat deploy.sh | ssh host bash
    ssh host bash <<SCRIPT ... SCRIPT

**Why, concretely.** The remote shell is reading the script from its own
standard input. Docker and Compose inherit that same standard input, and a
command that reads it consumes bytes out of the *script* — so the shell resumes
part-way down the file, or at the end of it, and the commands in between never
run at all. The shell then exits zero, because everything it did execute
succeeded.

That is the dangerous shape: the first half of a release lands, the verification
steps at the bottom are silently skipped, and the transcript reads like a clean
run. It is not hypothetical — it happened during a September 2026 release, and
what worked was to stop piping and execute from a file.

### What to do instead

**A — run the reviewed script that already exists.** Everything a release needs
is checked in: the preflight, the backup, the verification, the restore. Move
the checkout to the reviewed commit ([`README.md`](README.md), *3. Move the
checkout to the reviewed commit*) and run the file from disk:

```bash
/mnt/user/appdata/juristid-main/repo/scripts/deploy/juristid-deploy-preflight.sh --help
```

A handwritten substitute for a script that already exists is a second,
unreviewed procedure, and it is the one nobody will read again.

**B — when a genuine one-off sequence is needed, copy it and run the file.**
Write it on the workstation, transfer it, run the remote *file*:

```bash
scp /tmp/juristid-op.sh <host>:/tmp/juristid-op.sh
```

```bash
ssh <host> 'bash /tmp/juristid-op.sh'
```

The script is now on disk, so the remote shell reads it from a descriptor
nothing else is competing for, and stdin belongs to whatever the script says it
belongs to. Put no secret in it: it is a readable file on a shared host until
you remove it, and removing it once the run is finished and its output has been
read is worth doing.

**C — for two or three commands, just run them.** Separate invocations, each
result read before the next is typed. Most host work is this, and assembling a
pipeline for it is how the habit the prohibition above forbids gets formed.

### Stdin inside a script that runs on the host

Once the script is executed from a file, a command *may* legitimately read
stdin, and the reviewed procedure does:

```bash
docker load < juristid-main-web-${JURISTID_IMAGE_TAG}.tar.gz
```

That redirect hands `docker load` the archive, not the rest of the script, and
it is correct precisely because the script is not itself arriving on stdin. The
same distinction is why every `docker compose exec` in the real-data
runbooks carries `-T`: without it Compose allocates a TTY and reads from the terminal, which in a
non-interactive run is not what anybody wants.

The rule is therefore about the *transport*, not about Docker. Do not walk the
scripts adding `</dev/null` to every Docker line — it would break `docker load`,
and it would treat a consequence of piping scripts into `ssh` as though it were
a property of Docker.

## One shell, one identity

The release variables are exported once, and every command after them resolves
the same image:

```bash
export JURISTID_GIT_SHA=<full-40-char-sha>
```

```bash
export JURISTID_IMAGE_TAG=${JURISTID_GIT_SHA:0:12}
```

A second SSH invocation is a second shell, and a second shell has neither. A
Compose command without them falls back to the tag `juristid-main-web:local` —
which exists, is whatever was last loaded or built under that name, and will
migrate the production database without complaining. That is the operational
reason part B of the release runs in **one** shell: not tidiness, but a fallback
that is silent and plausible.

It is also why a release is a poor fit for option B above. If a sequence is
worth scripting, the exports belong inside the same script as the commands that
read them.

## What a release replaces, and what it leaves alone

The services that run the release image move together: `web`,
`intake-reader` and `searchindex`. The replacement command names none of
them — it is
unqualified, so Compose starts what the file defines and a fourth application
service added later moves with the other three rather than being forgotten.

`db` and `tunnel` are not part of a code release. They run pinned upstream
images, no `build:` stanza can touch them, and Compose leaves a container whose
definition has not changed exactly where it is. So the `docker ps` check above,
run again afterwards, should show the three with a new image tag and a fresh
uptime, and those two with the uptime they had before. A `db` that restarted
during a code deployment is a question, not a detail — it is in the stop list
below.

The commands themselves, and why each one is shaped the way it is, are
[`README.md`](README.md) under *9. Migrate, then replace*.

## What must not reach the transcript

Deployments are recorded — in a terminal buffer, in an agent transcript, in a
report pasted into a pull request. Treat everything printed on the host as
published.

**Do not run bare `docker compose config`.** Compose resolves `env_file` and
prints the values inline, so one command puts the database password, the shared
gate password and the tunnel credential into the record. The preflight resolves
the Compose file too, and captures the result into a variable instead of
printing it: that is the pattern to copy. If you need to know what a service
resolves to, read `compose.yml` and `.env.example` in the checkout, or let the
preflight answer.

**Do not print the environment.** Not the environment file, not `env` inside a
container, and not a filtered dump either — a filter still read the secret into
a process whose output somebody may widen later. Print identities, digests,
paths, container names and health, which is what the checks above are made of.

**Do not `set -x` a script that touches secrets.** Every expansion is echoed,
including the ones nobody thought about. If a run needs explaining, print what it
decided, not how each variable expanded.

Above all of these stands the rule in [`README.md`](README.md) under *What must
not happen here*: no real data leaves this host — not into Git, CI, a pull
request comment, a screenshot, or a log uploaded anywhere. The repository is
public.

## Handling the release artifact

The release artifact is three files, and their digests are not interchangeable.

GitHub shows its own digest for the **ZIP** it wraps an artifact in on download.
That is a digest of the wrapper. The one that matters is the SHA-256 of
`juristid-main-web-<sha12>.tar.gz`, written beside it by the build as
`juristid-main-web-<sha12>.tar.gz.sha256` and repeated as `archive_sha256` in
`release-manifest-<sha12>.txt`. Check that one, against those files, before
loading anything; [`README.md`](README.md) has the step under *5. Verify and
load the release image*.

Where the three files are staged on the host is the operator's choice and
nothing in the procedure depends on it. After a successful deployment:

- **keep the previous release's image.** It is what a code-only rollback loads,
  under its own `juristid-main-web:<sha12>` tag, and re-downloading it is the
  slow path at the worst possible moment.
- **an archive that was never deployed is housekeeping, not release work.** Tidy
  it later, deliberately, by name — never with a prune, for the reason
  [`README.md`](README.md) gives under *What must not happen here*.

## Release notes are part of the release

A payload the Chamber's lawyers will notice does not deploy without an entry in
`docs/release-notes/uuendused.toml`, dated the day the change actually becomes
available to them rather than the day it was written. The build refuses a
release without one, and the deployment is not finished until `/uuendused/` has
been opened on the deployed instance and the entry is there with the right date.
The rule and its waiver live in
[`../../docs/release-notes/README.md`](../../docs/release-notes/README.md).

A deployment report that does not say the notes were verified has not verified
them.

## STOP — do not improvise

Each of these stops the deployment where it is. Preserve what you can see — the
output, the revision, the container states — and report it. None of them is an
invitation to re-run the command until it turns green, and none of them becomes
safer by being passed quickly.

- host identity is uncertain, or the SSH host key changed
- the production revision is not the one recorded before starting
- `origin/main` moved to something nobody reviewed
- the preflight reports the checkout is dirty, or reports any `FAIL`
- the archive's SHA-256 does not match its `.sha256` or the manifest
- `docker load` restores a tag other than the one the exports name
- `/app/GIT_SHA` in the loaded image is not `$JURISTID_GIT_SHA`
- the migration plan holds a migration the review and the release note did not
  mention
- the backup fails, or the newest set is not fresh
- `deployment_readiness` fails, or names an authenticator nobody intended
- `healthz` reports a revision other than the one deployed
- `db` or `tunnel` restarted when only the application services should have moved
- a search-integrity or evidence-integrity check reports a finding
- `/uuendused/` does not show the release's entry, on the date it went live

Rolling back is a decision rather than a reflex: code-only rollback is the same
sequence with the previous reviewed commit, and rolling back *across* a
migration is [`RECOVERY.md`](RECOVERY.md)'s decision tree, not a command.

## What this file deliberately does not decide

**Who may have access.** The developer and support access policy is an open
decision with a named owner (`../../docs/secure-pilot-gate.md` row 8,
`../../docs/open-decisions.md`). This file says how somebody who already holds a
key uses it safely. It does not say who should hold one, and reading a procedure
as an authorisation is how an access policy ends up never being written.

**Where the secrets and the tunnel credential are kept.** That is DR1-C, still
open, in [`RECOVERY.md`](RECOVERY.md) under *What is not yet fixed*.
