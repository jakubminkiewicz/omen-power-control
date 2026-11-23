# Build and publish OMEN Power Control

## Build the RPM locally

From the repository root on Fedora, install the packaging tools and build a versioned package.

```bash
sudo dnf install rpm-build desktop-file-utils python3
./packaging/build-rpm.sh 0.1.0
```

The RPM and `SHA256SUMS` appear in `dist/0.1.0/`. The app RPM carries the userspace app and service sources. Kernel modules are built on the user's Fedora system so they match its kernel and signing key.

## Create the GitHub repository

Review the files and license terms in this directory before publishing. Install the [GitHub CLI](https://cli.github.com/) or create an empty public repository in the GitHub web interface. From this directory, commit and push the source.

```bash
git add .
git commit -m "Package OMEN Power Control for Fedora"
gh auth login
gh repo create omen-power-control --public --source=. --remote=origin --push
```

If you create the repository in the web interface, use the remote URL from the repository's Quick Setup page with `git remote add origin` and push `main`. [GitHub import instructions](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github) · [GitHub CLI repository creation](https://cli.github.com/manual/gh_repo_create)

## Publish a release

Push a version tag after the source is on GitHub.

```bash
git tag -a v0.1.0 -m "OMEN Power Control 0.1.0"
git push origin v0.1.0
```

The tag triggers **Build and publish release** in GitHub Actions. It builds the RPM in a Fedora container, checks the package build, and attaches the RPM and `SHA256SUMS` to the matching GitHub Release. The publish job uses the repository's built-in Actions token. Once the run completes, the RPM appears under **Releases → Assets**. [Workflow](../.github/workflows/release.yml) · [GitHub release CLI](https://cli.github.com/manual/gh_release_create)

For a manual release, create a release for the tag on GitHub and upload the RPM and checksum file from `dist/<version>/`. The source archive GitHub includes is for building from source. Users should choose the RPM to install the app.
