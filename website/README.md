# ALFRD public website

`public/` contains plain HTML and CSS: the home page, draft privacy policy,
and draft terms of service. No generator, JavaScript, or additional dependencies
are needed for these pages.

The assembly step in `.github/workflows/pages.yml` copies `src/alfrd/web/` into
`_site/`, renames its entry point to `studio.html` (keeping relative assets at
the same level), overlays `website/public/`, and adds the manifest schema and
`.nojekyll`. The locally served/packaged Studio entry point is unchanged.

The workflow deploys with `actions/deploy-pages`. Keep the custom domain
`alfrd.avikhagol.dev` in **Settings → Pages → Custom domain**, with the source
set to **GitHub Actions**. No `CNAME` file is needed for this deployment method.
See [GitHub's custom domain documentation](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site).

After deployment, Google Branding URLs are:

- Home: https://alfrd.avikhagol.dev/
- Privacy: https://alfrd.avikhagol.dev/privacy/
- Terms: https://alfrd.avikhagol.dev/terms/

The browser-only Studio is at https://alfrd.avikhagol.dev/studio.html.

Before OAuth verification, the maintainer must review both drafts, replace the
privacy policy's TODO email with a monitored address, confirm the effective date
and data-handling statements, and remove the draft notices when approved.

For local verification, run the workflow's parser test and assembly commands
from the repository root. Serve the resulting `_site/` directory with a static
HTTP server to preview the pages.
