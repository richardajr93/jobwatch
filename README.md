# Job Watch: setup in about 15 minutes

What it does: every morning it pulls open roles from employers' public job-board feeds, screens each one
with your rules (GO / REVIEW / SKIP, with the reason quoted), and updates a web page you bookmark.

## One-time setup
1. Create a free GitHub account if needed, then click **New repository** and name it `jobwatch` (Private is fine).
2. Click **Add file > Upload files** and drag in everything from this folder (keep the `.github/workflows` folder).
   If the hidden `.github` folder will not upload, use **Add file > Create new file**, type
   `.github/workflows/jobwatch.yml` as the name, and paste in the file contents.
3. **Settings > Pages**: Source = "Deploy from a branch", Branch = `main`, Folder = `/docs`. Save.
   Your page will live at `https://YOUR-USERNAME.github.io/jobwatch/` (private repos need a paid plan for Pages;
   if you want it private, open `docs/index.html` after each run instead).
4. **Actions** tab > "Job Watch" > **Run workflow**. Wait about 2 minutes, then open the page.
5. Look at **Source health** at the bottom of the page. Any row marked FAILED means that company's feed
   address in `config.json` is wrong. Fix the name there and run again.

## Optional: broad search (recommended)
Create a free key at developer.adzuna.com, then in the repo go to **Settings > Secrets and variables > Actions**
and add two secrets: `ADZUNA_APP_ID` and `ADZUNA_APP_KEY`. This searches across many employers using your keyword
list in `config.json`.

## Adding companies
Open `config.json` and add a line under the right system:
- Greenhouse: the company name in `boards.greenhouse.io/NAME`
- Lever: the name in `jobs.lever.co/NAME`
- Ashby: the name in `jobs.ashbyhq.com/NAME`
- Workday: the host, tenant, and site from the careers URL, e.g. `humana.wd5.myworkdayjobs.com/Humana_External_Career_Site`

## Your rules
They live near the top of `jobwatch.py` and in `config.json` (pay floors, accepted metros, travel limit,
target titles). Named platforms (SAP, Adaptive, and so on) are notes only and never cause a skip.

## Limits
LinkedIn, Indeed, and Glassdoor are not used: they block automated access. The starter company list is a
best guess. Some names will fail until you correct them, and the page tells you which.
