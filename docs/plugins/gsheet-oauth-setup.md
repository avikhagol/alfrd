# Maintainer guide: Google Cloud OAuth client and consent screen for alfrd-gsheet

Audience: ALFRD maintainers (or an organisation that brings its own Cloud project) who set up the Google Cloud side of **Sign in with Google** and **Google Picker** for the `alfrd-gsheet` plugin. End users never do any of this; they click **Sign in with Google**.

**End users of official ALFRD releases need no OAuth credentials or client setup.** Release CI fills the bundled client from secrets. The options below are for maintainers working from source or organisations using their own project.

Design background: `docs/briefs/gsheet-oauth-picker.md` in the `alfrd_brainstorm` repo. This guide uses its names exactly.

The Google Cloud console is renamed and rearranged from time to time. Menu names below follow **Google Auth Platform** (Branding, Audience, Data Access, Clients). If your console differs, look for the equivalent page.

## What you will end up with

| Value | Env var (exact) | Where it comes from |
|---|---|---|
| OAuth client ID | `ALFRD_GSHEET_OAUTH_CLIENT_ID` | Desktop app OAuth client (ends with `.apps.googleusercontent.com`) |
| OAuth client secret | `ALFRD_GSHEET_OAUTH_CLIENT_SECRET` | Same client |
| Picker API key | `ALFRD_GSHEET_PICKER_API_KEY` | API key restricted to the Picker API |
| Cloud project number | `ALFRD_GSHEET_GCP_PROJECT_NUMBER` | Project dashboard (digits only, not the project ID) |

## Where the values go

The plugin ships a defaults file, `alfrd_gsheet/oauth_client.json` (package data under `examples/plugins/alfrd-gsheet/`):

```json
{"client_id": "", "client_secret": "", "picker_api_key": "", "project_number": ""}
```

- It stays **empty in the repository**. The release build fills it (decision D1 in the brief: CI secret vs committed public values).
- Set `ALFRD_GSHEET_OAUTH_CLIENT_FILE` to the path of Google's downloaded **Desktop app** client JSON (`{"installed": {...}}`), or a JSON file with the flat keys shown above. Web client JSON (`{"web": {...}}`) is rejected. The packaged file also accepts the Desktop format.
- For automatic discovery, save the file at **`~/.config/alfrd/gsheet_oauth_client.json`**, or **`$XDG_CONFIG_HOME/alfrd/gsheet_oauth_client.json`** when `XDG_CONFIG_HOME` is set. No file env var is needed. It accepts the same Desktop or flat JSON formats, including optional `picker_api_key` and `project_number` in the object containing the client keys.
- Precedence is **per key**: a non-empty individual env var above wins over a non-empty value in `ALFRD_GSHEET_OAUTH_CLIENT_FILE`, then the per-user file, then the packaged file. Google's download has no Picker API key or project number; add those keys or use the matching env vars. `project_id` is not the numeric project number.
- Keep the downloaded client file **outside the repository** and `chmod 600` it. Group- or world-readable client files produce a server warning but still load. An absent per-user file is ignored; malformed or unreadable files produce a clear error without their contents. Do not rely on gitignore to protect credentials.
- Example for a local run or an organisation's own project:

  ```bash
  # Save the downloaded Desktop JSON here first (use $XDG_CONFIG_HOME if set).
  chmod 600 ~/.config/alfrd/gsheet_oauth_client.json
  # Optional explicit override instead of automatic discovery:
  # export ALFRD_GSHEET_OAUTH_CLIENT_FILE="/private/path/desktop-client.json"
  export ALFRD_GSHEET_PICKER_API_KEY="<picker api key>"
  export ALFRD_GSHEET_GCP_PROJECT_NUMBER="<digits-only project number>"
  # Alternative to the file: set both individually (these win over the file, per key)
  # export ALFRD_GSHEET_OAUTH_CLIENT_ID="<id>.apps.googleusercontent.com"
  # export ALFRD_GSHEET_OAUTH_CLIENT_SECRET="<secret>"
  ```

- Set the env vars where `alfrd serve` **and** the plan runner run (the runner inherits from serve). The CLI reads its own environment.
- Organisations using their own Cloud project set all four individual env vars, or use `ALFRD_GSHEET_OAUTH_CLIENT_FILE` plus `ALFRD_GSHEET_PICKER_API_KEY` and `ALFRD_GSHEET_GCP_PROJECT_NUMBER`.
- Google documents that a Desktop client's secret is not confidential (desktop apps are public clients and use PKCE), so shipping it in the package is expected. It is still not something to paste into issues or chat.
- Validation: client ID must end with `.apps.googleusercontent.com`; project number is 6–20 digits; an invalid value produces an error naming the variable, never the value.
- Without client ID + secret Studio and the API say: "Google sign-in isn't available in this build. Install the official ALFRD release to sign in with Google, or use Advanced → service account in Settings." Maintainer hints naming the env vars and per-user path appear in the server log at INFO level, not in this UI message. Without the API key or project number, sign-in and **Create new spreadsheet** work but **Choose from Google Drive** is disabled.

The release workflow builds and publishes the host and `alfrd-gsheet` distributions. Before building the plugin it fills the packaged defaults from Environment secrets in **`Plugin oauth secret for google`** (referenced by the `deploy` job in `.github/workflows/publish-pypi.yml`): `GSHEET_OAUTH_CLIENT_ID`, `GSHEET_OAUTH_CLIENT_SECRET`, `GSHEET_PICKER_API_KEY`, and `GSHEET_GCP_PROJECT_NUMBER` using JSON serialization without logging values. Repository secrets also work. Missing or invalid client ID/secret fails release preparation; missing Picker credentials only disables Picker. After building, CI validates the OAuth defaults in every plugin wheel and fails if required defaults are absent or invalid. Configure the PyPI token to allow publishing both projects.

## 1. Create the Google Cloud project

1. Open <https://console.cloud.google.com/> and sign in with the account that will own the project. Prefer a shared maintainer/organisation account over a personal one.
2. Project picker → **New project**. Name it (for example `ALFRD`), choose the organisation if any, **Create**.
3. Make sure the new project is selected in the top bar.
4. Open the project **Dashboard** (or **IAM & Admin → Settings**) and note the **Project number**. This is `ALFRD_GSHEET_GCP_PROJECT_NUMBER`. The **Project ID** is a different string and is not used.

## 2. Enable the APIs

**APIs & Services → Library**, search for each and click **Enable**:

- **Google Sheets API**
- **Google Drive API**
- **Google Picker API**

(Direct links of the form `https://console.cloud.google.com/apis/library/sheets.googleapis.com` also work.) A disabled API shows up later as a `SERVICE_DISABLED` 403 error.

## 3. Configure Google Auth Platform

Open **APIs & Services → OAuth consent screen** (this leads to **Google Auth Platform**). Click **Get started** on a new project.

### 3.1 Branding

- App name: what users see on the consent screen (for example `ALFRD`).
- User support email: a monitored address.
- Developer contact email.
- Optional but needed for brand verification when you publish: app logo, application home page, **privacy policy** link, terms of service link, and **authorised domains** that cover those links.

### 3.2 Audience

- User type: **External** (unless every user is in your own Google Workspace organisation, in which case Internal is possible and skips most of this section).
- Publishing status starts as **Testing**.
- Under **Test users**, click **Add users** and add the Google accounts that should be able to sign in while in Testing (up to 100). Include your own account.
- Testing mode is for development only; see "Testing mode and the 7-day expiry" below.

### 3.3 Data Access (scopes)

Click **Add or remove scopes** and select **only** these three:

- `openid`
- `https://www.googleapis.com/auth/userinfo.email`
- `https://www.googleapis.com/auth/drive.file`

Save. Do not add `spreadsheets`, `drive`, or `drive.readonly`. All three scopes are non-sensitive, which keeps verification light. ALFRD can only see spreadsheets the user picks or creates with it.

## 4. Create the Desktop app OAuth client

1. **Google Auth Platform → Clients → Create client** (or **APIs & Services → Credentials → Create credentials → OAuth client ID**).
2. Application type: **Desktop app**. Name: for example `ALFRD desktop`.
3. **Create**. Copy the **Client ID** and **Client secret** (you can view them again later from the client's page).
   - `ALFRD_GSHEET_OAUTH_CLIENT_ID`
   - `ALFRD_GSHEET_OAUTH_CLIENT_SECRET`

No redirect URI needs registering: Desktop clients accept loopback redirects (`http://127.0.0.1:<random port>/`) automatically.

## 5. Create and restrict the Picker API key

1. **APIs & Services → Credentials → Create credentials → API key**. Copy the key. This is `ALFRD_GSHEET_PICKER_API_KEY`.
2. Click the key (or **Edit API key**) and set:
   - **API restrictions → Restrict key → Google Picker API** only.
   - **Application restrictions: None** (recommended, decision D2). The key ships in the package so it is public anyway, and website restrictions with random loopback ports are unverified.
3. Save. Restriction changes can take a few minutes to apply.

If the spike (S0 in the brief) proves loopback referrers work you may add a website restriction, but then include the Picker host `https://docs.google.com/*` as Google requires, because Picker renders in an iframe hosted there.

## 6. Smoke test before release

1. Put the four values in the environment (client ID and secret may come from `ALFRD_GSHEET_OAUTH_CLIENT_FILE`, see the export example above; or from `oauth_client.json`) and start `alfrd serve`.
2. In the Studio's Google Sheet dialog (or `alfrd gsheet auth login`) sign in with a **test user**. Google shows an "unverified app" warning while in Testing; continue past it.
3. Choose a spreadsheet in the Picker; create a new spreadsheet; sync one step.
4. Revoke access at <https://myaccount.google.com/connections> and confirm ALFRD shows "Google sign-in expired or was revoked".

## After the smoke test: release and production

Do these in order once the smoke test passes. Console wording that could not be confirmed against the live console is marked **(unconfirmed)**.

1. **Add the four GitHub Environment secrets.** In the ALFRD repository: **Settings → Environments → Plugin oauth secret for google → Environment secrets → Add secret**. The `deploy` job in `.github/workflows/publish-pypi.yml` references this exact environment. Repository secrets also work. Names must match exactly:
   - `GSHEET_OAUTH_CLIENT_ID`
   - `GSHEET_OAUTH_CLIENT_SECRET`
   - `GSHEET_PICKER_API_KEY`
   - `GSHEET_GCP_PROJECT_NUMBER`

   Missing or invalid client ID/secret fails the release before publishing; CI also checks the built wheel. Picker API key and project number are optional for basic sign-in but required for Picker. `PYPI_API_TOKEN` can remain a repository secret.
2. **Verify the domain `avikhagol.dev` in Google Search Console.** Open <https://search.google.com/search-console>, signed in with the **same Google account that owns the Cloud project** (brand verification checks ownership against the project owners/editors). **Add property → Domain** (not URL prefix), enter `avikhagol.dev`, and add the **TXT** record Google shows at your DNS provider for the root domain (host `@`). Wait for DNS to propagate, then click **Verify**. Keep the TXT record in place afterwards. Do not remove it.
3. **Fill in Branding** (**Google Auth Platform → Branding**):
   - Application home page: `https://alfrd.avikhagol.dev/`
   - Privacy policy link: `https://alfrd.avikhagol.dev/privacy/`
   - Terms of service link: `https://alfrd.avikhagol.dev/terms/`
   - Authorised domain: `avikhagol.dev` (the subdomain `alfrd.avikhagol.dev` is covered by it)
   - App logo: **120×120 px** square image (PNG/JPG, small file size; the exact size limit is **(unconfirmed)**).

   Check that the three URLs load publicly before saving. The privacy and terms pages are currently drafts; review their text before submitting.
4. **Publish app**: **Audience → Publish app**, confirm; status must read **In production** (see "Publishing to production").
5. **Submit brand verification** (**Verification Center** / the prompt that appears after publishing; exact entry point **(unconfirmed)**). Respond to any Google e-mail. See "Verification expectations".
6. **Remember**: tokens issued while the app was in Testing keep the 7-day limit. Everyone who signed in before publishing (including you) must sign in again once.

## Testing mode and the 7-day expiry

While the publishing status is **Testing** and the user type is External, Google issues refresh tokens that **expire after 7 days**. The only exemption is apps that request just a subset of `openid`, `userinfo.email`, `userinfo.profile`. `drive.file` is outside that subset, so in Testing **every user is signed out after 7 days** (ALFRD then shows "Google sign-in expired or was revoked" and pauses sync). Testing also limits sign-in to the listed test users and shows an unverified-app warning.

Testing is therefore fine for development, but **the consent screen must be In production before release**. People who use their own Cloud project (env vars) need to publish it too, or accept weekly re-sign-in.

Other reasons a refresh token stops working: the user revoked access, it was unused for six months, more than 100 live refresh tokens exist for the same Google account and client (the oldest is silently invalidated), or a Workspace admin policy blocks the app.

## Publishing to production

1. **Google Auth Platform → Audience → Publish app** (button under Publishing status) and confirm.
2. Status becomes **In production**. Anyone with a Google account can now sign in; the test-user list no longer applies.
3. Complete **brand verification** (see below). Until it is done, users see an "unverified app" screen and a user cap may apply (the console shows the current limit).

Check the console afterwards: the status must read **In production**. Refresh tokens issued while the app was in Testing keep their 7-day limit, so users who signed in earlier must sign in once more.

## Verification expectations

Verification page: <https://support.google.com/cloud/answer/13464321>.

- All apps need **brand verification** (name, logo, home page, privacy policy, authorised domains, domain ownership verified in Google Search Console). It is usually a few days, mostly e-mail back-and-forth.
- The extra requirements for **sensitive or restricted scopes** (scope justification, demo video, security assessment) apply to apps requesting them. Our three scopes (`openid`, `userinfo.email`, `drive.file`) are non-sensitive, so we expect brand verification only. This is assumption A5 in the brief; confirm it in the console when you publish and update this guide if Google asks for more.
- Requesting any other scope later (for example `spreadsheets`) would change this and is a non-goal.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `redirect_uri_mismatch` (Error 400) | The client is not a **Desktop app** type (a Web client requires registered redirect URIs and rejects random loopback ports), or the code was changed to a non-loopback redirect | Create a **Desktop app** client (section 4) and put its ID/secret in the env vars or `oauth_client.json`. Do not register redirect URIs on a Desktop client |
| `ALFRD_GSHEET_OAUTH_CLIENT_FILE` rejected with a message about a web client (file has a top-level `"web"` key) | The file was downloaded from a **Web application** client; only Desktop (`"installed"`) or flat-key JSON is accepted | Create a **Desktop app** client (section 4), download its JSON, and point the variable at it. The error names the variable, never the file contents |
| `access_denied` / "app has not completed the Google verification process" for a user | App in Testing and the user is not on **Test users** | Add them under **Audience → Test users**, or publish to production |
| `access_denied` after the user clicked Cancel, or a Workspace message "blocked by admin" | User cancelled, or admin policy (`admin_policy_enforced`) blocks third-party apps | Try again; for admin blocks ask the Workspace admin to allow the client, or use a service account |
| Sign-in works but ALFRD says drive.file not granted | The user unticked the Drive permission on the granular-consent screen | Sign in again and tick "See, edit, create, and delete only the specific Google Drive files you use with this app" |
| Picker does not open / blank iframe / "The API developer key is invalid" | Wrong key, key restricted to a different API, Picker API not enabled, or key recently edited | Enable **Google Picker API**; set the key's API restriction to Picker API; wait a few minutes; recheck `ALFRD_GSHEET_PICKER_API_KEY` |
| Picker error about origin / referrer, or key "not authorized for this website" | A website (HTTP referrer) restriction on the key that does not include the loopback origin or `https://docs.google.com/*` | Remove the application restriction (recommended), or add the required referrers |
| Picker opens but shows no files or cannot access picked file | Wrong `ALFRD_GSHEET_GCP_PROJECT_NUMBER` (using project ID instead of the number), or the token is from a different project's client | Use the digits-only project number of the same project as the OAuth client |
| `invalid_grant` on refresh (ALFRD: "Google sign-in expired or was revoked") | App in Testing (7-day expiry), user revoked access, six months unused, password change on some accounts, or the client was deleted/rotated | Publish to production; the user signs in again via the Google Sheet dialog or `alfrd gsheet auth login` |
| `invalid_grant` immediately on code exchange | Wrong client secret, or clock skew | Recheck the secret; check the machine's time |
| 403 `SERVICE_DISABLED` | Sheets/Drive/Picker API not enabled in this project | Enable it (section 2) and retry after a minute |
| 429 / quota errors for all users | Shared per-minute Sheets quota on the shipped project | Request a quota increase in the console, or have heavy organisations use their own project |
| Sign-in page never completes | Browser and ALFRD are not on the same machine (SSH, remote desktop) | Use a service account |

## Advanced: service-account headless mode

For servers and CI there is no browser, and Google tells apps not to use user credentials for server-to-server deployments. Use a service account instead; it needs no consent screen, OAuth client or Picker key.

1. In the same (or any) Cloud project, enable the Sheets API and Drive API.
2. **IAM & Admin → Service Accounts → Create service account**, then **Keys → Add key → Create new key → JSON** and download it.
3. In ALFRD **Settings → Plugins → Google Sheet** set **Sign-in method** to `service_account` (setting `auth_mode`) and paste the JSON into `credentials_json`.
4. Share each target spreadsheet with the key's `client_email` as **Editor**. Forgetting this gives a 403 "share the sheet with the service account's client_email".

Existing configs that only have `credentials_json` and no `auth_mode` keep working as service-account mode with no migration. Treat the key like a password and rotate it if it leaks.
