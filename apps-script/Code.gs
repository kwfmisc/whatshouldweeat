/**
 * Supper recipes: Google Apps Script bound to the recipes Sheet. The Sheet is the app's only
 * data store: recipes and favorites both live here.
 *
 * POST {token, adult, toddler, newest, generatedAt}   (from scripts/fetch_recipes.py)
 *       replaces the adult, toddler, new and meta tabs. Requires the PUSH_TOKEN script property.
 * POST {action: 'favorite', passcode, saved, recipe}  (from the website's heart button)
 *       adds or removes one row in the favorites tab. Requires the FAVORITES_PASSCODE property.
 * GET   (from the website) returns recipes and favorites as JSON, so edits made in the Sheet
 *       show up on the site's next page load.
 *
 * Deploy as a web app: Execute as "Me", Who has access "Anyone".
 *
 * Works from your own Sheet (Extensions → Apps Script) or as a standalone script with a
 * SHEET_ID script property. Only the tabs named in TABS are ever written; a tab with one of
 * those names is only replaced if this script created it, so none of your own tabs get wiped.
 * Re-scraping never touches the favorites tab.
 */

const TABS = {
  adult: 'Recipes – adult',
  toddler: 'Recipes – toddler',
  newest: 'Recipes – new',
  meta: 'Recipes – meta',
  favorites: 'Recipes – favorites',
};
const MARKER = 'supper-recipes';
const NUMBER_FIELDS = ['time', 'rating', 'ratingCount', 'score', 'order'];
const LIST_FIELDS = ['meals'];
const FAVORITE_FIELDS = ['url', 'savedAt', 'title', 'source', 'meals', 'protein', 'time', 'servings',
  'rating', 'ratingCount', 'kind', 'age', 'image', 'description', 'id'];

function doPost(e) {
  const body = JSON.parse(e.postData.contents);
  if (body.action === 'favorite') return json_(saveFavorite_(body));

  const token = PropertiesService.getScriptProperties().getProperty('PUSH_TOKEN');
  if (!token || body.token !== token) return json_({ ok: false, error: 'Invalid token' });

  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const ss = spreadsheet_();
    const newest = Object.keys(body.newest || {}).flatMap((site) =>
      body.newest[site].map((recipe) => Object.assign({ site: site }, recipe)));
    writeTab_(ss, TABS.adult, body.adult || []);
    writeTab_(ss, TABS.toddler, body.toddler || []);
    writeTab_(ss, TABS.newest, newest);
    writeTab_(ss, TABS.meta, [{ generatedAt: body.generatedAt }]);
    return json_({ ok: true, adult: body.adult.length, toddler: body.toddler.length, newest: newest.length });
  } catch (err) {
    return json_({ ok: false, error: String(err.message || err) });
  } finally {
    lock.releaseLock();
  }
}

function doGet() {
  const ss = spreadsheet_();
  const newest = {};
  readTab_(ss, TABS.newest).forEach((recipe) => {
    const site = recipe.site;
    delete recipe.site;
    (newest[site] = newest[site] || []).push(recipe);
  });
  const meta = readTab_(ss, TABS.meta)[0] || {};
  const favorites = readTab_(ss, TABS.favorites).sort((a, b) => String(b.savedAt).localeCompare(String(a.savedAt)));
  return json_({
    generatedAt: meta.generatedAt || '',
    adult: readTab_(ss, TABS.adult),
    toddler: readTab_(ss, TABS.toddler),
    newest: newest,
    favorites: favorites,
  });
}

function saveFavorite_(body) {
  const passcode = PropertiesService.getScriptProperties().getProperty('FAVORITES_PASSCODE');
  if (!passcode) return { ok: false, error: 'FAVORITES_PASSCODE is not set in Script properties.' };
  if (String(body.passcode) !== passcode) return { ok: false, code: 'passcode', error: 'Wrong passcode' };
  const recipe = body.recipe || {};
  if (!/^https:\/\//.test(recipe.url || '')) return { ok: false, error: 'Missing recipe link' };

  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const sheet = favoritesSheet_(spreadsheet_());
    const count = sheet.getLastRow() - 1;
    const urls = count > 0 ? sheet.getRange(2, 1, count, 1).getDisplayValues().map((row) => row[0]) : [];
    const index = urls.indexOf(recipe.url);
    if (body.saved && index === -1) {
      const row = FAVORITE_FIELDS.map((key) => {
        const value = key === 'savedAt' ? new Date().toISOString() : recipe[key];
        if (value === null || value === undefined) return '';
        return (Array.isArray(value) ? value.join(', ') : String(value)).slice(0, 1000);
      });
      sheet.getRange(sheet.getLastRow() + 1, 1, 1, row.length).setNumberFormat('@').setValues([row]);
    } else if (!body.saved && index !== -1) {
      sheet.deleteRow(index + 2);
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String(err.message || err) };
  } finally {
    lock.releaseLock();
  }
}

function spreadsheet_() {
  const id = PropertiesService.getScriptProperties().getProperty('SHEET_ID');
  return id ? SpreadsheetApp.openById(id) : SpreadsheetApp.getActiveSpreadsheet();
}

/** Returns a tab this script owns, creating it if needed; refuses to touch tabs it didn't create. */
function ownTab_(ss, name) {
  let sheet = ss.getSheetByName(name);
  if (!sheet) {
    sheet = ss.insertSheet(name);
    sheet.addDeveloperMetadata(MARKER);
  } else if (!sheet.getDeveloperMetadata().some((m) => m.getKey() === MARKER)) {
    throw new Error(`A tab named "${name}" already exists and wasn't created by this script. Rename it, or change TABS in Code.gs.`);
  }
  return sheet;
}

function favoritesSheet_(ss) {
  const sheet = ownTab_(ss, TABS.favorites);
  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, FAVORITE_FIELDS.length).setValues([FAVORITE_FIELDS]).setFontWeight('bold');
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function writeTab_(ss, name, rows) {
  const sheet = ownTab_(ss, name);
  sheet.clear();
  if (!rows.length) return;
  const headers = [];
  rows.forEach((row) => Object.keys(row).forEach((key) => {
    if (headers.indexOf(key) === -1) headers.push(key);
  }));
  const values = rows.map((row) => headers.map((key) => {
    const value = row[key];
    if (value === null || value === undefined) return '';
    return Array.isArray(value) ? value.join(', ') : String(value);
  }));
  const range = sheet.getRange(1, 1, values.length + 1, headers.length);
  range.setNumberFormat('@'); // plain text, so "8-10 mo" or "2026-03-04" aren't turned into dates
  range.setValues([headers].concat(values));
  sheet.setFrozenRows(1);
  sheet.getRange(1, 1, 1, headers.length).setFontWeight('bold');
}

function readTab_(ss, name) {
  const sheet = ss.getSheetByName(name);
  if (!sheet || sheet.getLastRow() < 2) return [];
  const [headers, ...rows] = sheet.getDataRange().getDisplayValues();
  return rows
    .filter((row) => row.some((cell) => cell !== ''))
    .map((row) => {
      const item = {};
      headers.forEach((key, i) => {
        const cell = row[i];
        if (NUMBER_FIELDS.indexOf(key) !== -1) item[key] = Number(cell) || 0;
        else if (LIST_FIELDS.indexOf(key) !== -1) item[key] = cell ? cell.split(/\s*,\s*/) : [];
        else item[key] = cell === '' ? null : cell;
      });
      return item;
    });
}

/** Run from the Apps Script editor to check the push token without revealing it. */
function checkToken() {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty('PUSH_TOKEN');
  Logger.log(token
    ? `PUSH_TOKEN is set: ${token.length} characters, starts "${token.slice(0, 4)}", ends "${token.slice(-4)}"`
    : 'PUSH_TOKEN is not set in this project. Add it under Project Settings → Script properties.');
  Logger.log(props.getProperty('FAVORITES_PASSCODE')
    ? 'FAVORITES_PASSCODE is set.'
    : 'FAVORITES_PASSCODE is not set, so favorites cannot be saved yet.');
}

function json_(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}
