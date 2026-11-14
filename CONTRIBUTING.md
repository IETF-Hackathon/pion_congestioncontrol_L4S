# Contributing

The upstream project is [IETF-Hackathon/pion_congestioncontrol_L4S](https://github.com/IETF-Hackathon/pion_congestioncontrol_L4S).

## Local Git layout

- `upstream` points to the IETF-Hackathon repository.
- `origin` points to your writable fork when available.
- Create a feature branch from `upstream/main`; keep unrelated changes out of the contribution.

Inspect the actual configuration with `git remote -v` and `git status`.

If the checkout has only an `upstream` remote, create a fork using an account permitted to fork this public repository, then add it as `origin`:

```sh
git remote add origin https://github.com/YOUR-ACCOUNT/pion_congestioncontrol_L4S.git
```

Some enterprise-managed accounts cannot create a personal fork of this repository. In that case, use an account allowed to fork, or arrange direct write access with the upstream maintainers.

For a new contribution, starting with a clean working tree:

```sh
git fetch upstream
git switch -c my-contribution upstream/main
```

After editing, checking the changed files, and committing, publish only the feature branch:

```sh
git push -u origin HEAD
```

Open a pull request against upstream `main`. If your organization grants direct write access, a feature branch in the upstream repository is also an option.

## Project page

Maintain `index.html` as the single HTML project document. Keep the network diagram embedded and the results cells blank until measurements have actually been collected. Label expected outcomes as hypotheses. Check navigation anchors and the overlay controls after editing the page.

## Implementation contributions

State which Pion and controller versions were used, how congestion feedback reaches the controller, and how controller output reaches the source and pacer. A `PragueCC` wrapper and an end-to-end Pion integration are distinct milestones.

For experiment results, record the exact topology, selected ICE candidate pair, qdisc settings, traffic direction, source settings, competing load, measurement interval, and the metric definitions. Check ECN marking and feedback against captures and endpoint logs. Keep raw captures and generated logs in `results/raw/` outside Git; publish a reviewed summary and relevant evidence links.

The project currently contains documentation only. Add implementation checks alongside code when code is introduced; do not claim an experiment has run on the basis of the design alone.
