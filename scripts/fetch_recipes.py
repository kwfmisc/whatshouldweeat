"""Scrape Allrecipes, Traeger, Solid Starts and GreenSpoon Kids into the Google Sheet.

The Sheet is the app's data store. A copy is also kept in backups/recipes.json (git-ignored,
not part of the website) so a failed push never wastes a scrape.

Adults:   the best-rated recipes per meal (and per protein for lunch and dinner) from
          Allrecipes and Traeger, ranked by a Bayesian average of star rating and rating count.
Toddlers: GreenSpoon Kids sample meals (in the site's own order) and Solid Starts recipes and
          serving guides (curated order below; neither site has ratings).
New:      the 3 newest recipes on Allrecipes, Traeger and GreenSpoon Kids (Solid Starts
          publishes no reliable dates).

Run with:  npm run recipes         scrape, back up, push to the Google Sheet
           npm run recipes:push    push the backup to the Sheet without re-scraping

Pushing needs secrets/apps-script.json: {"url": "<web app URL>", "token": "<PUSH_TOKEN>"}.
"""

import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'backups' / 'recipes.json'
SHEET_CONFIG = ROOT / 'secrets' / 'apps-script.json'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36'

MEALS = ['Breakfast', 'Lunch', 'Dinner', 'Snack', 'Dessert']
PROTEINS = ['Chicken', 'Beef', 'Pork', 'Seafood', 'Vegetarian']
POOL = 60        # detailed Allrecipes candidates per list, so every time filter can still fill 20
NEW_PER_SITE = 3

sys.stderr.reconfigure(encoding='utf-8')


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def fetch(url, delay=0.0, retries=4):
    for attempt in range(retries):
        if delay:
            time.sleep(delay)
        try:
            # Allrecipes answers 403 to extra headers such as Accept-Language, so send only the UA.
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=30) as res:
                return res.read().decode('utf-8', errors='ignore')
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            wait = 5 * (attempt + 1)
            log(f'  {err.code} on {url}, retrying in {wait}s')
            time.sleep(wait)
        except Exception as err:  # network hiccup
            log(f'  {err!r} on {url}')
            time.sleep(3)
    return None


def iso_minutes(value):
    m = re.match(r'P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?', value or '')
    if not m:
        return 0
    d, h, mins = (int(x or 0) for x in m.groups())
    return d * 1440 + h * 60 + mins


def recipe_ld(page):
    """Return the schema.org Recipe object embedded in a page, if any."""
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page or '', re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get('@graph', [data])
        for item in items:
            kinds = item.get('@type')
            kinds = kinds if isinstance(kinds, list) else [kinds]
            if 'Recipe' in kinds:
                return item
    return None


def first(value):
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get('url')
    return value


def clean(text, limit=150):
    text = html.unescape(html.unescape(text or '')).strip()
    text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', text))
    if len(text) > limit:
        text = text[:limit].rsplit(' ', 1)[0].rstrip(',.;:') + '…'
    return text


def servings(value):
    value = first(value) if isinstance(value, list) else value
    if not value:
        return ''
    value = str(value)
    return f'Serves {value}' if value.isdigit() else value


PROTEIN_WORDS = [
    ('Seafood', r'salmon|shrimp|prawn|tuna|\bcod\b|tilapia|halibut|fish|crab|lobster|scallop|clam|mussel|anchov|sardine|trout|mahi|swordfish|haddock|pollock|catfish|snapper|seafood|calamari|octopus'),
    ('Chicken', r'chicken|turkey'),
    ('Beef', r'beef|steak|sirloin|brisket|\bchuck\b|veal|hamburger|ground round|burger'),
    ('Pork', r'pork|bacon|\bham\b|sausage|prosciutto|pancetta|chorizo|salami|pepperoni|kielbasa|bratwurst|carnitas'),
]
BROTH = r'broth|stock|bouillon|base\b'


def classify_protein(title, ingredients):
    """Main protein from the title, else the first ingredient that names one (broth last)."""
    for name, pattern in PROTEIN_WORDS:
        if re.search(pattern, title, re.I):
            return name
    broth_hit = None
    for line in ingredients or []:
        for name, pattern in PROTEIN_WORDS:
            if re.search(pattern, line, re.I):
                if not re.search(BROTH, line, re.I):
                    return name
                broth_hit = broth_hit or name
    return broth_hit or 'Vegetarian'


def from_ld(ld, url, source, meal=None, protein=None):
    rating = ld.get('aggregateRating') or {}
    count = int(float(rating.get('ratingCount') or rating.get('reviewCount') or 0))
    total = iso_minutes(ld.get('totalTime')) or iso_minutes(ld.get('prepTime')) + iso_minutes(ld.get('cookTime'))
    image = html.unescape(first(ld.get('image')) or '')
    if source == 'traeger':
        image = re.sub(r'\?.*$', '', image) + '?fmt=auto&w=900&qlt=default'
    title = clean(ld.get('name'), 90)
    if protein is None and meal in ('Lunch', 'Dinner'):
        protein = classify_protein(title, ld.get('recipeIngredient'))
    return {
        'id': f'{source}-' + re.sub(r'[^a-z0-9]+', '-', url.lower().split('.com/')[-1]).strip('-'),
        'source': source,
        'meal': meal,
        'protein': protein,
        'title': title,
        'url': url,
        'image': image,
        'time': total,
        'servings': servings(ld.get('recipeYield')),
        'rating': round(float(rating.get('ratingValue') or 0), 2),
        'ratingCount': count,
        'description': clean(ld.get('description')),
        'published': (ld.get('datePublished') or '')[:10],
    }


COMPONENT = r'\b(sauce|dressing|vinaigrette|marinade|rub|seasoning|spice (?:mix|blend)|glaze|icing|frosting|buttercream|ganache|gravy|brine|syrup|compound butter|stock|broth|aioli|mayonnaise|mayo|crust|dough|filling|whipped cream|croutons)\s*$'


def is_component(title):
    """True for frostings, sauces, rubs and the like, which rank highly but aren't a meal."""
    title = re.sub(r'(\s+recipe)?[\s!.]*$', '', title, flags=re.I)
    head, *rest = re.split(r'\s+(?:with|w/)\s+', title, flags=re.I)  # "Pork Chops with Raspberry Sauce" is a meal
    if not re.search(COMPONENT, head, re.I):
        return False
    if re.search(r'\b(dough|crust)\s*$', head, re.I):
        return True
    if re.search(r'\s(and|&)\s', head, re.I):  # "Biscuits and Gravy"
        return False
    return not (rest and re.search(r'pasta|noodle|rice|spaghetti|linguine|penne', rest[0], re.I))


def tidy(payload):
    """Drop recipe components and breakfast dishes that slipped into dessert."""
    def keep(r):
        return not is_component(r['title']) and not ('Dessert' in r['meals'] and re.search(r'pancake|waffle|french toast', r['title'], re.I))
    before = len(payload['adult'])
    payload['adult'] = [r for r in payload['adult'] if keep(r)]
    log(f'Tidied: dropped {before - len(payload["adult"])} sauces, frostings and misfiled recipes')
    return payload


def bayesian(recipes, weight=25):
    rated = [r for r in recipes if r['ratingCount']]
    mean = sum(r['rating'] * r['ratingCount'] for r in rated) / max(1, sum(r['ratingCount'] for r in rated))
    for r in recipes:
        r['score'] = round((weight * mean + r['rating'] * r['ratingCount']) / (weight + r['ratingCount']), 4) if r['ratingCount'] else round(mean - 1, 4)
    recipes.sort(key=lambda r: r['score'], reverse=True)
    return recipes


# --------------------------------------------------------------------------- Allrecipes

AR = 'https://www.allrecipes.com'
AR_RECIPE_URL = re.compile(r'^https://www\.allrecipes\.com/(recipe/\d+/[a-z0-9-]+/|[a-z0-9-]+-recipe-\d+)$')
AR_MAX_PAGES = 12
# (meal, protein or None, start listing, keyword a sub-listing path must contain, how many to detail)
AR_LISTS = [
    ('Breakfast', None, '/recipes/78/breakfast-and-brunch/', r'breakfast|brunch', POOL),
    ('Lunch', None, '/recipes/17561/lunch/', r'lunch|sandwich|salad|wrap|soup', 120),
    ('Dinner', 'Chicken', '/recipes/201/meat-and-poultry/chicken/', r'chicken', POOL),
    ('Dinner', 'Beef', '/recipes/200/meat-and-poultry/beef/', r'beef|steak', POOL),
    ('Dinner', 'Pork', '/recipes/205/meat-and-poultry/pork/', r'pork|ham|bacon|sausage', POOL),
    ('Dinner', 'Seafood', '/recipes/93/seafood/', r'seafood|fish|salmon|shrimp|tuna|crab|scallop|tilapia|cod', POOL),
    ('Dinner', 'Vegetarian', '/recipes/265/everyday-cooking/vegetarian/main-dishes/', r'vegetarian|vegan', POOL),
    ('Snack', None, '/recipes/76/appetizers-and-snacks/', r'appetizers|snack', POOL),
    ('Dessert', None, '/recipes/79/desserts/', r'dessert|cookie|cake|pie|brownie', POOL),
]


def allrecipes_cards(page):
    cards = {}
    for m in re.finditer(r'<a[^>]+href="(https://www\.allrecipes\.com/[^"]+)"[^>]*>(.*?)</a>', page, re.S):
        url, body = m.group(1), m.group(2)
        if not AR_RECIPE_URL.match(url) or 'card__title' not in body:
            continue
        count = re.search(r'rating-count-number[^>]*>\s*([\d,]+)', body)
        cards[url] = int(count.group(1).replace(',', '')) if count else 0
    return cards


def allrecipes_list(meal, protein, start, keyword, limit, details):
    queue, seen, cards = [AR + start], set(), {}
    while queue and len(seen) < AR_MAX_PAGES:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        page = fetch(url, delay=1.2)
        if not page:
            continue
        for card, count in allrecipes_cards(page).items():
            cards[card] = max(count, cards.get(card, 0))
        for link in re.findall(r'href="(https://www\.allrecipes\.com/recipes/\d+/[a-z0-9/-]+/)"', page):
            if re.search(keyword, link.split('/recipes/')[1]) and link not in seen and link not in queue:
                queue.append(link)
    best = sorted(cards, key=cards.get, reverse=True)[:limit]
    log(f'  Allrecipes {meal} {protein or ""}: {len(seen)} listing pages, {len(cards)} recipes, detailing {len(best)}')
    recipes = []
    for url in best:
        if url not in details:  # the same recipe often sits in several lists
            page = fetch(url, delay=1.2)
            details[url] = recipe_ld(page)
        if details[url]:
            recipes.append(from_ld(details[url], url, 'allrecipes', meal, protein))
    return recipes


def allrecipes_newest():
    feed = fetch(AR + '/google-news-sitemap.xml') or ''
    entries = re.findall(r'<loc>([^<]+)</loc>.*?<news:publication_date>([^<]+)</news:publication_date>', feed, re.S)
    newest = []
    for url, published in sorted(entries, key=lambda e: e[1], reverse=True):
        if not AR_RECIPE_URL.match(url):
            continue
        ld = recipe_ld(fetch(url, delay=1.2))
        if ld:
            recipe = from_ld(ld, url, 'allrecipes')
            recipe['published'] = published[:10]
            newest.append(recipe)
        if len(newest) == NEW_PER_SITE:
            break
    return newest


# --------------------------------------------------------------------------- Traeger

TRAEGER_MEAL_WORDS = [
    ('Dessert', r'pie\b|(?<!pan)cake|cobbler|cookie|brownie|crisp\b|pudding|dessert|s-?mores|cheesecake|ice-cream|tart\b|donut|doughnut|churro|blondie|sundae|crumble|galette|fudge'),
    ('Breakfast', r'breakfast|pancake|waffle|french-toast|frittata|omelet|hash-brown|biscuits-and-gravy|cinnamon-roll|quiche|scrambled|eggs-benedict|dutch-baby'),
    ('Snack', r'dip\b|jerky|nachos|popper|nuts\b|pretzel|deviled-egg|bites\b|crostini|queso|chex|snack|appetizer|wings?\b|skewers?|sliders|candied-bacon|pigs-in-a-blanket|meatballs'),
    ('Lunch', r'sandwich|salad|wrap\b|panini|melt\b|quesadilla|soup|chili\b|tacos?\b|burrito|pizza|po-boy|hoagie|cheesesteak|burger'),
]
TRAEGER_DINNER = {
    'Chicken': r'chicken|thigh|drumstick|turkey',
    'Beef': r'beef|steak|brisket|tri-tip|ribeye|rib-eye|prime-rib|short-rib|sirloin|meatloaf|chuck|flank|skirt|tomahawk|ny-strip|filet|pastrami|picanha|pot-roast',
    'Pork': r'pork|baby-back|spare-rib|st-louis|ribs|ham(?!burger)|sausage|carnitas|porchetta|belly',
    'Seafood': r'salmon|shrimp|fish|tuna|crab|lobster|scallop|cod|halibut|trout|oyster|clam|mussel|tilapia|mahi|swordfish|seafood|prawn|snapper|octopus|calamari|steelhead',
    'Vegetarian': r'tofu|vegetable|veggie|mushroom|cauliflower|eggplant|zucchini|portobello|vegan|vegetarian|chickpea|falafel|squash|halloumi|paneer|tempeh|mac-and-cheese',
}
MEAT = r'chicken|wing|beef|steak|brisket|burger|pork|bacon|ham(?!burger)|sausage|salmon|shrimp|fish|crab|lobster|turkey|lamb|duck|rib|chorizo|pepperoni|prosciutto|meat|tuna|scallop'


def traeger_urls():
    sitemap = fetch('https://www.traeger.com/sitemap') or ''
    # The sitemap lists recipes newest first. Bare category pages (e.g. /recipes/chicken) share the prefix.
    urls = re.findall(r'<loc>(https://www\.traeger\.com/recipes/[a-z0-9-]+-[a-z0-9-]+)</loc>', sitemap)
    return list(dict.fromkeys(urls))


def traeger_meal(slug):
    """(meal, protein) for a Traeger recipe slug; protein None means work it out from ingredients."""
    for meal, pattern in TRAEGER_MEAL_WORDS:
        if re.search(pattern, slug):
            return meal, None
    for protein, pattern in TRAEGER_DINNER.items():
        if re.search(pattern, slug):
            if protein == 'Vegetarian' and re.search(MEAT, slug):
                continue
            return 'Dinner', protein
    return None, None


def traeger_detail(job):
    url, meal, protein = job
    ld = recipe_ld(fetch(url))
    return from_ld(ld, url, 'traeger', meal, protein) if ld else None


def traeger_all(urls):
    jobs = [(u, *traeger_meal(u.rsplit('/', 1)[1])) for u in urls]
    jobs = [job for job in jobs if job[1]]
    log(f'  Traeger: {len(jobs)} mealtime recipes out of {len(urls)} sitemap entries')
    with ThreadPoolExecutor(max_workers=6) as pool:
        return [r for r in pool.map(traeger_detail, jobs) if r]


def traeger_newest(urls):
    newest = []
    for url in urls:
        recipe = traeger_detail((url, None, None))
        if recipe:
            newest.append(recipe)
        if len(newest) == NEW_PER_SITE:
            break
    return newest


# --------------------------------------------------------------------------- GreenSpoon Kids

GS = 'https://www.greenspoonkids.com'


def greenspoon_detail(job):
    position, url = job
    ld = recipe_ld(fetch(url))
    if not ld:
        return None
    title = clean(ld.get('name'), 90)
    meal = ld.get('recipeCategory') or 'Snack'
    age = re.search(r'(\d+-\d+ (?:months|years))', ld.get('keywords') or '')
    stamp = re.search(r'/meal-images/(\d{13})-', first(ld.get('image')) or '')
    return {
        'id': 'greenspoon-' + url.rsplit('/', 1)[1],
        'source': 'greenspoon',
        'meals': [meal],
        'protein': classify_protein(title, ld.get('recipeIngredient')) if meal in ('Lunch', 'Dinner') else None,
        'title': title,
        'url': url,
        'image': first(ld.get('image')) or '',
        'time': iso_minutes(ld.get('totalTime')) or iso_minutes(ld.get('prepTime')),
        'kind': 'Recipe',
        'age': age.group(1).replace(' months', ' mo').replace(' years', ' yrs') if age else '',
        'description': clean(ld.get('description')),
        'published': datetime.fromtimestamp(int(stamp.group(1)) / 1000, timezone.utc).date().isoformat() if stamp else '',
        'order': position,
    }


def greenspoon_all():
    page = fetch(f'{GS}/sample-meals') or ''
    urls = []
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        data = json.loads(block)
        items = data.get('itemListElement') or (data.get('mainEntity') or {}).get('itemListElement') or []
        urls += [item['url'] for item in items if item.get('url')]
    urls = list(dict.fromkeys(urls))
    with ThreadPoolExecutor(max_workers=4) as pool:
        recipes = [r for r in pool.map(greenspoon_detail, enumerate(urls)) if r]
    log(f'  GreenSpoon Kids: {len(recipes)} of {len(urls)} sample meals')
    newest = sorted(recipes, key=lambda r: re.search(r'/(\d{13})-', r['image'] + '/0000000000000-').group(1), reverse=True)[:NEW_PER_SITE]
    return recipes, newest


# --------------------------------------------------------------------------- Solid Starts

SS = 'https://solidstarts.com'
SS_RECIPES = {  # path: (title, meals, protein)
    'articles/turkey-rice-noodle-stew-for-babies': ('Turkey rice & noodle soup', ['Lunch', 'Dinner'], 'Chicken'),
    'articles/big-batch-bolognese-recipe-for-the-whole-family': ('Big batch bolognese', ['Lunch', 'Dinner'], 'Beef'),
    'articles/ramen-noodle-recipe-for-babies-toddlers': ('Garlic-butter noodles with mushrooms', ['Lunch', 'Dinner'], 'Vegetarian'),
    'articles/buttermilk-pancakes-for-babies-perfect-sunday-recipe-for-baby-led-weaning': ('Buttermilk pancakes', ['Breakfast'], None),
    'articles/strawberry-granola-sugar-free-recipe-for-picky-eaters': ('Sugar-free strawberry granola', ['Breakfast', 'Snack'], None),
    'articles/coconut-granola-for-babies-toddlers-picky-eaters': ('Coconut granola', ['Breakfast', 'Snack'], None),
    'articles/smash-cake-recipes-for-babys-first-birthday': ('Smash cakes for a first birthday', ['Dessert'], None),
    'articles/popsicles-for-babies': ('Popsicles for babies', ['Dessert', 'Snack'], None),
}
# Serving guides, most familiar foods first (Solid Starts has no ratings to rank by).
SS_FOODS = {
    'Breakfast': ['eggs', 'oatmeal', 'yogurt', 'banana', 'pancakes', 'french-toast', 'waffle', 'avocado', 'blueberries', 'strawberry', 'bagel', 'muffin'],
    'Snack': ['avocado', 'banana', 'cheese', 'hummus', 'peanut-butter', 'apple', 'crackers', 'cucumber', 'blueberries', 'mango', 'string-cheese', 'popcorn', 'edamame', 'watermelon'],
    'Dessert': ['ice-cream', 'watermelon', 'mango', 'strawberry', 'raspberry', 'cocoa-chocolate', 'peach', 'cherry'],
    'Chicken': ['chicken', 'ground-turkey', 'turkey', 'chicken-liver', 'duck', 'chicken-stock'],
    'Beef': ['ground-beef', 'steak', 'brisket', 'meatball', 'beef-liver', 'bone-broth', 'oxtail', 'veal', 'bison', 'hot-dog'],
    'Pork': ['pork', 'ground-pork', 'pork-chop', 'pork-shoulder', 'spare-ribs', 'bacon', 'ham', 'sausage', 'chorizo', 'prosciutto', 'deli-meat'],
    'Seafood': ['salmon', 'cod', 'shrimp', 'tuna', 'sardines', 'tilapia', 'halibut', 'trout', 'scallops', 'crab-2', 'lobster', 'mussels', 'clams', 'haddock', 'pollock', 'catfish', 'anchovy'],
    'Vegetarian': ['tofu', 'black-beans', 'chickpea', 'lentil', 'eggs', 'pasta', 'quinoa', 'edamame', 'tempeh', 'kidney-beans', 'paneer', 'rice'],
}


def ss_meta(page, name):
    m = re.search(rf'<meta (?:name|property)="{name}" content="([^"]*)"', page)
    return html.unescape(m.group(1)) if m else ''


def solidstarts_page(path):
    page = fetch(f'{SS}/{path}/')
    if not page:
        return None
    age = re.search(r'(\d+) months old \+', page)
    return {
        'title': re.split(r' for Babies| - ', ss_meta(page, 'og:title'))[0],
        'image': re.sub(r'w=\d+', 'w=900', ss_meta(page, 'og:image')),
        'age': f'{age.group(1)} mo+' if age else '6 mo+',
        'description': clean(ss_meta(page, 'description')),
    }


def solidstarts_all():
    paths = list(SS_RECIPES) + sorted({f'foods/{slug}' for slugs in SS_FOODS.values() for slug in slugs})
    with ThreadPoolExecutor(max_workers=4) as pool:
        pages = dict(zip(paths, pool.map(solidstarts_page, paths)))

    def item(path, meals, protein, kind, order, title=None):
        info = pages.get(path)
        if not info:
            return None
        return {
            'id': f'solidstarts-{path.replace("/", "-")}-{"-".join(meals).lower()}-{(protein or "any").lower()}',
            'source': 'solidstarts', 'meals': meals, 'protein': protein,
            'title': title or info['title'], 'url': f'{SS}/{path}/', 'image': info['image'],
            'time': 0, 'kind': kind, 'age': info['age'], 'description': info['description'],
            'order': order,
        }

    items = [item(path, meals, protein, 'Recipe', i, title) for i, (path, (title, meals, protein)) in enumerate(SS_RECIPES.items())]
    items.append(item('articles/big-batch-bolognese-recipe-for-the-whole-family', ['Lunch', 'Dinner'], 'Pork', 'Recipe', 1, 'Big batch bolognese'))
    for group, slugs in SS_FOODS.items():
        meals, protein = ([group], None) if group in MEALS else (['Lunch', 'Dinner'], group)
        items += [item(f'foods/{slug}', meals, protein, 'Serving guide', 100 + i) for i, slug in enumerate(slugs)]
    items = [i for i in items if i]
    log(f'  Solid Starts: {len(items)} items')
    return items


# --------------------------------------------------------------------------- main

def main():
    log('GreenSpoon Kids…')
    greenspoon, gs_new = greenspoon_all()

    log('Solid Starts…')
    solidstarts = solidstarts_all()

    log('Traeger…')
    urls = traeger_urls()
    traeger = traeger_all(urls)
    traeger_new = traeger_newest(urls)

    log('Allrecipes (throttled to be polite, this takes a while)…')
    details, allrecipes = {}, []
    for meal, protein, start, keyword, limit in AR_LISTS:
        allrecipes += allrecipes_list(meal, protein, start, keyword, limit, details)
    ar_new = allrecipes_newest()

    # Rank each site's recipes within every meal (and protein for lunch/dinner) list, keep the best.
    adult = []
    for site in (allrecipes, traeger):
        groups = {}
        for r in site:
            groups.setdefault((r['meal'], r['protein']), {})[r['url']] = r
        for (meal, protein), recipes in sorted(groups.items(), key=lambda g: (MEALS.index(g[0][0]), g[0][1] or '')):
            ranked = bayesian(list(recipes.values()))[:POOL]
            log(f'  {ranked[0]["source"]:10} {meal:9} {protein or "":10} {len(ranked)}')
            adult += ranked
    for r in adult:
        r['meals'] = [r.pop('meal')]
        r['id'] += f'-{r["meals"][0].lower()}'

    payload = {
        'generatedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'adult': adult,
        'toddler': greenspoon + solidstarts,
        'newest': {'allrecipes': ar_new, 'traeger': traeger_new, 'greenspoon': gs_new},
    }
    tidy(payload)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding='utf-8')
    log(f'Wrote {OUT}: {len(adult)} adult, {len(greenspoon) + len(solidstarts)} toddler, '
        f'new {len(ar_new)}/{len(traeger_new)}/{len(gs_new)}')
    push_to_sheet(payload)


def push_to_sheet(payload):
    """Send the recipes to the Apps Script web app, which writes them into the Google Sheet."""
    if not SHEET_CONFIG.exists():
        log(f'No {SHEET_CONFIG.relative_to(ROOT)}, so the Google Sheet was not updated.')
        return
    config = json.loads(SHEET_CONFIG.read_text(encoding='utf-8'))
    if not config.get('url', '').startswith('https://script.google.com/'):
        log(f'Add the web app URL to {SHEET_CONFIG.relative_to(ROOT)} to update the Google Sheet.')
        return
    body = json.dumps({**payload, 'token': config['token']}).encode('utf-8')
    req = urllib.request.Request(config['url'], data=body, headers={'Content-Type': 'application/json'})
    # Apps Script answers with a redirect; urllib follows it as a GET, which returns the script's reply.
    with urllib.request.urlopen(req, timeout=120) as res:
        reply = res.read().decode('utf-8', errors='ignore')
    try:
        result = json.loads(reply)
    except ValueError:
        sys.exit(f'Google Sheet push failed, unexpected reply: {reply[:300]}')
    if not result.get('ok'):
        sys.exit(f'Google Sheet push failed: {result.get("error")}')
    log(f'Updated the Google Sheet: {result["adult"]} adult, {result["toddler"]} toddler, {result["newest"]} new')


if __name__ == '__main__':
    if '--tidy' in sys.argv:  # re-apply the filters to the saved JSON without scraping
        OUT.write_text(json.dumps(tidy(json.loads(OUT.read_text(encoding='utf-8'))), indent=1, ensure_ascii=False), encoding='utf-8')
    elif '--push-only' in sys.argv:
        push_to_sheet(json.loads(OUT.read_text(encoding='utf-8')))
    else:
        main()
