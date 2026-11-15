# Contributing

The upstream project is [IETF-Hackathon/pion_congestioncontrol_L4S](https://github.com/IETF-Hackathon/pion_congestioncontrol_L4S).

## Local Git layout

- `origin` points to `https://github.com/IETF-Hackathon/pion_congestioncontrol_L4S.git`, the main application repository.
- GitHub work in this session uses the personal `altanai` account authorized through Safari. Verify `gh api user --jq .login` before an authenticated write.
- Create a feature branch from `origin/main`; keep unrelated changes out of the contribution.
- `altanai/interceptor` and `altanai/ice` are library forks selected by versioned module replacements. They are not alternate remotes for this application repo.

Inspect the actual configuration with `git remote -v` and `git status`.

For a new contribution, starting with a clean working tree:

```sh
git fetch origin
git switch -c my-contribution origin/main
```

After editing, checking the changed files, and committing, publish only the feature branch:

```sh
git push -u origin HEAD
```

Open a pull request against `IETF-Hackathon/pion_congestioncontrol_L4S:main`. Do not replace newer remote README changes when reconciling an older local checkout.

## Project page

Maintain `index.html` as the single HTML project document. Keep the network diagram embedded and the results cells blank until measurements have actually been collected. Label expected outcomes as hypotheses. Check navigation anchors and the overlay controls after editing the page.

## Implementation contributions

For Prague changes use `bash scripts/check.sh` and `bash scripts/build.sh`;
they configure cgo and permit the reference core's `-fwrapv` compiler flag.
Run `gofmt -w cmd/pion-l4s internal/prague` after Go edits. See
`docs/pion-l4s-interop/prague.md` for the controller pin and local media check.

State which Pion and controller versions were used, how congestion feedback reaches the controller, and how controller output reaches the source and pacer. A `PragueCC` wrapper and an end-to-end Pion integration are distinct milestones.

For experiment results, record the exact topology, selected ICE candidate pair, qdisc settings, traffic direction, source settings, competing load, measurement interval, and the metric definitions. Check ECN marking and feedback against captures and endpoint logs. Keep raw captures and generated logs in `results/raw/` outside Git; publish a reviewed summary and relevant evidence links.

The starter includes a Go module and sender/receiver code. Run `gofmt -w cmd/pion-l4s`, `GOWORK=off go test ./...`, `GOWORK=off go vet ./...`, `GOWORK=off go test -race ./...`, and `python3 -m unittest discover -s scripts -p 'test_*.py'` for code changes, plus the loopback media check described in the build guide when feedback or lifecycle behavior changes. Check shell syntax with `bash -n scripts/build.sh scripts/make-media.sh` and build with `bash scripts/build.sh`. Local transport checks do not establish L4S or lab performance results.

Add a file overview and a purpose comment/docstring for every function, including helpers and tests. Explain boundaries such as fixed source pacing, endpoint checks versus actual path proof, and unavailable metrics where they matter.

The [testbed guide](docs/pion-l4s-interop/testbed.md) describes the current `lld-black4` branch. Preserve its distinction from monitor9/monitor10. Keep the config template free of real host assignments, and keep raw run output out of Git.
