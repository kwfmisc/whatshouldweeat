# Google Sheet setup (Apps Script)

The Google Sheet is the app's only data store. The scraper pushes recipes into it through
[Code.gs](Code.gs), the website reads recipes and favorites from it through the same script, and
the heart button saves favorites back to it. Edits made in the Sheet show up on the site on the
next page load.

## One-time setup

1. **Open your Sheet.** Any existing Sheet works. The script adds four tabs of its own
   (`Recipes – adult`, `Recipes – toddler`, `Recipes – new`, `Recipes – meta`) and never
   touches your other tabs. If a tab with one of those names already exists, the push stops
   with an error rather than overwriting it.
2. **Add the script.** In the Sheet, open **Extensions → Apps Script**.
   - No code there yet (just an empty `myFunction`)? Replace it with the whole of `Code.gs`.
   - Already have code? Click **+ → Script**, name the file `Recipes`, and paste `Code.gs`
     there instead. If your existing code already has a `doGet` or `doPost` function, tell
     me first, because a project can only have one of each.
   Save.
3. **Add the script properties.** In Apps Script, open **Project Settings** (gear icon). Under
   **Script properties**, add:
   - `PUSH_TOKEN`: the `token` value from `secrets/apps-script.json` (the long random text, not
     the file name). Lets the scraper write recipes.
   - `FAVORITES_PASSCODE`: a passcode you choose and share with your family. The site asks for
     it the first time someone taps a heart, then remembers it on that device.
   Click **Save script properties**. Run `checkToken` from the editor to confirm both are set.
4. **Deploy it as a web app.** Click **Deploy → New deployment**, choose the type
   **Web app**, then set **Execute as: Me** and **Who has access: Anyone**. Click **Deploy** and
   approve the permissions. Google warns that the app is unverified because you wrote it
   yourself: choose **Advanced → Go to (project name)**.
5. **Copy the web app URL** (`https://script.google.com/macros/s/…/exec`) into both places:
   - `secrets/apps-script.json`, as the `url` value
   - `.env`, as `VITE_RECIPES_URL=…`
6. **Push the data.** Run `npm run recipes:push`. The `Recipes –` tabs appear and fill in; the
   favorites tab appears when the first heart is tapped.
7. **Rebuild the site** (`npm run build`) so it knows the URL.

## Day to day

- `npm run recipes` scrapes all the sites, updates the Sheet, and keeps a copy in `backups/recipes.json`.
- `npm run recipes:push` resends that backup without scraping again.
- Edit or delete rows in the Sheet and the site picks it up on refresh. Keep the header row
  and the `meals` column format (`Lunch, Dinner`).
- Re-scraping replaces the adult, toddler, new and meta tabs, but never touches
  `Recipes – favorites`.

## Notes

- Anyone with the web app URL can **read** the recipes and favorites (that's how the site loads
  them). Writing recipes needs the token, which lives only in `secrets/` (git-ignored) and
  Script properties. Changing favorites needs the family passcode.
- After changing `Code.gs`, use **Deploy → Manage deployments → Edit → Version: New version**
  so the URL keeps working with the new code.
- If the Sheet can't be reached, the site says so and offers a **Try again** button.
