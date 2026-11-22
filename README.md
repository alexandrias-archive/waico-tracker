# WAICO tracker · 世界人工智能合作组织动态

Collects English and Chinese news about the World AI Cooperation Organization (WAICO, 世界人工智能合作组织), sorts it into categories, and publishes it as a small website and RSS feed. It runs by itself on GitHub every 6 hours.

**What it searches**
- Google News: US, UK, Kenya and Nigeria English editions; mainland China, Taiwan and Hong Kong Chinese editions
- Bing News and GDELT are built in but switched off, because neither answers requests from GitHub's servers. See `config.yml` to turn them back on.

**What you get**
- A dashboard with filters for language, date, category and keyword, plus a CSV download
- An RSS feed you can follow in any feed reader
- A growing archive in `docs/items.json`

## Set up (about 10 minutes)

1. **Create a repository.** On GitHub, click **New repository**. Name it `waico-tracker` and make it **Public** (free GitHub Pages needs a public repo). Don't add a README.
2. **Upload the files.** On the empty repo page, click **uploading an existing file** and drag in everything from this folder, including the hidden `.github` folder. On a Mac, press `Cmd + Shift + .` in Finder to show hidden folders. Click **Commit changes**.
   - If the `.github` folder didn't upload: click **Add file → Create new file**, type `.github/workflows/crawl.yml` as the name, paste in the contents of that file, and commit.
3. **Run it the first time.** Go to the **Actions** tab (click the green button to enable workflows if asked). Choose **Crawl WAICO news → Run workflow**, tick **backfill**, and run. It takes a minute or two. The first run collects everything Google News still has (up to 100 articles per search).
4. **Turn on the website.** Go to **Settings → Pages**. Under *Build and deployment*, pick **Deploy from a branch**, then branch **main** and folder **/docs**. Save. After a minute your dashboard is at `https://YOUR-USERNAME.github.io/waico-tracker/`.
5. **Optional: follow the feed.** Add `https://YOUR-USERNAME.github.io/waico-tracker/feed.xml` to a feed reader (Feedly, Inoreader, NetNewsWire, or Slack's RSS app).

After that it runs every 6 hours on its own.

## Change what it looks for

Everything is in **`config.yml`**. Open it on GitHub, click the pencil icon, edit, and commit. Changes take effect on the next run, and category changes are applied to the whole archive.

- **Categories:** add, remove or rename categories and their keywords (English and Chinese). Hover over a tag on the dashboard to see which keyword put an article there.
- **Searches:** add queries or Google News editions (for example `en-ZA` for South Africa or `en-GH` for Ghana).
- **Noise:** add words to `exclude_terms` to drop unrelated articles.

To change how often it runs, edit the `cron` line in `.github/workflows/crawl.yml`.

## Good to know

- **"mention" tag:** WAICO isn't named in the headline. The search engine found it in the article text. Use *Only headlines that name WAICO* to hide these.
- **"+2 more" next to an outlet:** the same headline ran in several outlets (common with Xinhua copy). Hover to see which.
- **Google News links** open through a Google redirect, which lands on the original article.
- **If a source breaks**, the others keep running and the run log (Actions tab) says which one failed. If every source fails, the run is marked as failed and GitHub emails you.
- **Timing:** GitHub sometimes delays scheduled runs by a few minutes when it is busy.
- **Run it on your own computer:** `pip install -r requirements.txt`, then `python crawler.py`. Open `docs/index.html` in a browser to see the results.
