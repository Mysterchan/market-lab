# Documentation website

The website introduces Market Lab with one real Desktop tutorial, then offers two
reading paths: App usage and Agent/SDK development. API, architecture and source
reference pages remain available from the same navigation and search.

The existing Markdown files are the documentation source. Do not maintain a second
copy of guides in HTML. `home.html` owns homepage copy; `assets/` owns website styles
and interaction. Shared colors and type/spacing roles live in `sim_platform/design.py`.
The builder writes `assets/design.css` from that source; do not maintain a second
palette in the website stylesheet. Homepage and documentation share these roles,
with a simpler homepage navigation and a reading-focused guide layout.

## Build and preview

```bash
python -m pip install -r requirements-site.txt
python tools/build_pages.py
python -m http.server 8000 --directory build/pages
```

Open the localhost address printed by the server. The builder renders maintained
Markdown, rewrites local links, creates readable views for linked source files,
builds the local search index and checks all generated local URLs and anchors.
Output is disposable and must remain under `build/`. No App dependencies or native
build are needed for website generation when the recorded media already exists.

## GitHub Pages

`.github/workflows/pages.yml` builds and checks the site on pull requests and relevant
pushes. Pushes to the default branch and manual runs on that branch also deploy the
generated site. Pull requests never deploy. Before the first deployment, enable
**Settings → Pages → Build and deployment → Source → GitHub Actions** in each target
repository. The build reads `github.repository` for its source link; relative URLs
support either repository's project prefix without changing the content.

For `Mysterchan/market-lab`, the standard project URL is
[mysterchan.github.io/market-lab](https://mysterchan.github.io/market-lab/).
The README Website button uses this address. The site becomes available after a
successful deployment; editing the link alone does not publish it. If a source export
uses another owner, repository name or custom domain, update the README Website URL.

The same files can be included in the internal source repository and its public
source export. Public HTML includes no internal reports, credentials or databases.
Adding the workflow does not itself confirm that Pages is enabled or deployed.
The workflow follows [GitHub's custom Pages workflow documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

## Record the tutorial

```bash
python -m pip install -r requirements-demo.txt
make all
QT_QPA_PLATFORM=cocoa python tools/record_desktop_demo.py
```

The macOS recorder drives actual Desktop controls in a temporary workspace. It
captures only the App and its owned dialogs, includes teaching captions, checks
an actual manual fill and verifies a Live rule run through recorded-operation
Replay. It uses synthetic market data and makes no model/provider calls. It does
not show a model-backed Agent trading; the Agent chapter introduces the editor.

The recorder writes `media/demo.mp4`, a poster, English WebVTT captions and an
evidence file with chapter timings. The website uses these timings for chapter
links. Keep these files together. Record again when the illustrated workflow changes.
Review the video and transcript before publishing new media.

[App guide](../user-guide/README.md) · [Documentation policy](../development/documentation.md)

## Website styles

[Visual direction](DESIGN.md) records the homepage, reading and Desktop roles.
The website now uses Tailwind CSS for responsive homepage layout, compiled into a
small static asset. No runtime CDN or Node dependency is added to Desktop. The
checked-in CSS lets the Python build above work without installing Node.

After editing homepage utility classes, regenerate the CSS with Node 24:

```bash
cd docs/site
npm ci --ignore-scripts
npm run build:css
```

Keep `assets/utilities.css` with the source change. `assets/presentation.css` owns
website presentation; `assets/site.css` owns shared navigation and reading styles.
Manrope is self-hosted under `assets/fonts/` with its SIL Open Font License; the App
capture is real Desktop with explicitly synthetic data. No third-party font request
or new model/network call is needed to view the page.
