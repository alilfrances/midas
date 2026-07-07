# Public Repo Setup Spec

## Goal

Publish `midas` as a public GitHub repository with standard public-project hygiene.

## Scope

- Add GitHub Actions CI for Python unittest coverage.
- Add CodeQL scanning for Python.
- Add Dependabot updates for GitHub Actions.
- Add public contribution and security reporting docs.
- Create the public GitHub repo, push `main`, and protect `main`.

## GitHub Settings

- Visibility: public.
- Default branch: `main`.
- Branch protection: pull requests required, one approving review, stale approval dismissal, conversation resolution, linear history, no force pushes, no deletions.
- Required status check: `tests`.
- Admin bypass: enabled by leaving admin enforcement off.
- Security: vulnerability alerts, Dependabot security updates where available, secret scanning and push protection where available.
