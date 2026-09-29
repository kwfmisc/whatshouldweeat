import { useEffect, useRef, useState } from 'react'
import './App.css'

const sources = {
  allrecipes: { name: 'Allrecipes', home: 'https://www.allrecipes.com/' },
  traeger: { name: 'Traeger', home: 'https://www.traeger.com/recipes' },
  greenspoon: { name: 'GreenSpoon Kids', home: 'https://www.greenspoonkids.com/sample-meals' },
  solidstarts: { name: 'Solid Starts', home: 'https://solidstarts.com/' },
}

const eaters = [
  { id: 'adult', label: 'Adults', note: 'From Allrecipes & Traeger', sites: ['allrecipes', 'traeger'] },
  { id: 'toddler', label: 'Toddler', note: 'From GreenSpoon Kids & Solid Starts', sites: ['greenspoon', 'solidstarts'] },
]
const meals = ['Breakfast', 'Lunch', 'Dinner', 'Snack', 'Dessert']
const proteinMeals = ['Lunch', 'Dinner']
const proteins = ['Chicken', 'Beef', 'Pork', 'Seafood', 'Vegetarian']
const cookTimes = [20, 30, 45, 60, Infinity]
const newSites = { adult: ['allrecipes', 'traeger', 'greenspoon'], toddler: ['greenspoon', 'allrecipes', 'traeger'] }
const TOP = 20
const PASSCODE_KEY = 'supper:passcode'
const adultSites = ['allrecipes', 'traeger']
const FAVORITE_FIELDS = ['url', 'title', 'source', 'meals', 'protein', 'time', 'servings', 'rating', 'ratingCount', 'kind', 'age', 'image', 'description', 'id']
// The Google Sheet is the only data store; this Apps Script web app reads and writes it.
// Set it in .env (see apps-script/README.md).
const SHEET_URL = import.meta.env.VITE_RECIPES_URL
const EMPTY = { generatedAt: '', adult: [], toddler: [], newest: {}, favorites: [] }

function formatTime(minutes) {
  if (!minutes) return 'Time varies'
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `${hours} hr ${rest} min` : `${hours} hr`
}

function timeLabel(time) {
  if (time === Infinity) return 'No rush'
  return time === 60 ? 'An hour' : `${time} min`
}

function formatDate(iso) {
  if (!iso) return 'Just added'
  return `Added ${new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}`
}

function ratingLabel(recipe) {
  if (!recipe.ratingCount) return 'Not yet rated'
  return `★ ${recipe.rating.toFixed(1)} · ${recipe.ratingCount.toLocaleString()} ${recipe.ratingCount === 1 ? 'rating' : 'ratings'}`
}

// The top 20 that fit the filters, alternating between the eater's two sites so both are represented.
// Adult recipes are ranked by rating; toddler sites have no ratings, so they keep each site's own order.
function topRecipes(data, { eater, meal, protein, maxTime }) {
  const { sites } = eaters.find((option) => option.id === eater)
  const pool = eater === 'adult' ? data.adult : data.toddler
  const fitsTime = (recipe) => maxTime === Infinity || (recipe.time ? recipe.time <= maxTime : eater === 'toddler')
  const fits = (recipe) =>
    recipe.meals.includes(meal) && (!proteinMeals.includes(meal) || recipe.protein === protein) && fitsTime(recipe)
  const rank = eater === 'adult' ? (a, b) => b.score - a.score : (a, b) => a.order - b.order
  const bySite = sites.map((site) => pool.filter((recipe) => recipe.source === site && fits(recipe)).sort(rank))
  const picked = new Map()
  for (let i = 0; picked.size < TOP && bySite.some((list) => i < list.length); i++) {
    for (const list of bySite) {
      if (list[i] && picked.size < TOP && !picked.has(list[i].url)) picked.set(list[i].url, list[i])
    }
  }
  return [...picked.values()]
}

// Card details depend on the site: adult recipes have ratings, toddler ones have an age range.
function cardMeta(recipe) {
  return adultSites.includes(recipe.source)
    ? <><span className="recipe-tag">{ratingLabel(recipe)}</span><span>{formatTime(recipe.time)}</span></>
    : <><span className="recipe-tag">{recipe.kind}</span><span>{recipe.age}</span></>
}

function cardFooter(recipe) {
  if (adultSites.includes(recipe.source)) return recipe.servings
  return recipe.time ? formatTime(recipe.time) : 'Baby-led friendly'
}

function HeartIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <path d="M12 20.5s-7.5-4.6-9.3-9.4C1.5 7.8 3.6 4.5 7 4.5c2 0 3.5 1.1 5 3 1.5-1.9 3-3 5-3 3.4 0 5.5 3.3 4.3 6.6-1.8 4.8-9.3 9.4-9.3 9.4z" />
    </svg>
  )
}

function RecipeCard({ recipe, index, meta, footer, isFavorite, onToggleFavorite }) {
  return (
    <article className="recipe-card" style={{ animationDelay: `${Math.min(index, 12) * 45}ms` }}>
      <img className="recipe-image" src={recipe.image} alt={recipe.title} loading="lazy" referrerPolicy="no-referrer" />
      <button
        className="favorite-button"
        type="button"
        aria-pressed={isFavorite}
        aria-label={isFavorite ? `Remove ${recipe.title} from favorites` : `Save ${recipe.title} to favorites`}
        title={isFavorite ? 'Remove from favorites' : 'Save to favorites'}
        onClick={() => onToggleFavorite(recipe)}
      >
        <HeartIcon />
      </button>
      <div className="recipe-content">
        <div className="recipe-meta">{meta}</div>
        <h3>{recipe.title}</h3>
        <p className="recipe-description">{recipe.description}</p>
        <div className="card-footer">
          <span className="servings">{footer}</span>
          <a className="recipe-link" href={recipe.url} target="_blank" rel="noopener noreferrer">
            View on {sources[recipe.source].name} <span aria-hidden="true">↗</span>
          </a>
        </div>
      </div>
    </article>
  )
}

// Everything comes from the Google Sheet on each visit, so edits there show up on refresh.
function useSheetData() {
  const [state, setState] = useState({ status: SHEET_URL ? 'loading' : 'missing', data: EMPTY })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!SHEET_URL) return
    let cancelled = false
    fetch(SHEET_URL)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(res.statusText))))
      .then((sheet) => {
        if (!Array.isArray(sheet.adult)) throw new Error('Unexpected reply')
        if (!cancelled) setState({ status: 'ready', data: { ...EMPTY, ...sheet } })
      })
      .catch(() => {
        if (!cancelled) setState({ status: 'error', data: EMPTY })
      })
    return () => { cancelled = true }
  }, [attempt])
  const retry = () => {
    setState({ status: 'loading', data: EMPTY })
    setAttempt((n) => n + 1)
  }
  const setFavorites = (update) =>
    setState((current) => ({ ...current, data: { ...current.data, favorites: update(current.data.favorites) } }))
  return { ...state, retry, setFavorites }
}

function readPasscode() {
  try { return localStorage.getItem(PASSCODE_KEY) || '' } catch { return '' }
}

function rememberPasscode(passcode) {
  try {
    if (passcode) localStorage.setItem(PASSCODE_KEY, passcode)
    else localStorage.removeItem(PASSCODE_KEY)
  } catch {
    // storage blocked: we'll just ask again next time
  }
}

// Favorites are rows in the Sheet's favorites tab, shared by every device. Saving needs the family
// passcode (FAVORITES_PASSCODE in Apps Script), asked for once and remembered on this device.
function useFavorites(favorites, setFavorites) {
  const [message, setMessage] = useState('')
  const pending = useRef(new Set())
  const saved = new Set(favorites.map((recipe) => recipe.url))

  async function toggle(recipe) {
    if (!SHEET_URL || pending.current.has(recipe.url)) return
    const passcode = readPasscode() || window.prompt('Enter the family passcode to save favorites:')?.trim()
    if (!passcode) return
    const save = !saved.has(recipe.url)
    const snapshot = Object.fromEntries(FAVORITE_FIELDS.map((key) => [key, recipe[key] ?? null]))
    const apply = (adding) => setFavorites((current) => adding
      ? [{ ...snapshot, savedAt: new Date().toISOString() }, ...current.filter((item) => item.url !== recipe.url)]
      : current.filter((item) => item.url !== recipe.url))
    pending.current.add(recipe.url)
    apply(save) // show it straight away; undone below if the Sheet refuses
    try {
      const res = await fetch(SHEET_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain;charset=utf-8' }, // a "simple" request, so no CORS preflight
        body: JSON.stringify({ action: 'favorite', passcode, saved: save, recipe: snapshot }),
      })
      const result = await res.json()
      if (!result.ok) throw Object.assign(new Error(result.error), { code: result.code })
      rememberPasscode(passcode)
      setMessage('')
    } catch (err) {
      apply(!save)
      if (err.code === 'passcode') rememberPasscode('')
      setMessage(err.code === 'passcode'
        ? 'That passcode didn’t work. Tap the heart to try again.'
        : `Couldn’t save to the Google Sheet${err.message ? ` (${err.message})` : ''}. Please try again.`)
    } finally {
      pending.current.delete(recipe.url)
    }
  }

  return { isFavorite: (recipe) => saved.has(recipe.url), toggle, message, clearMessage: () => setMessage('') }
}

function LoadState({ status, retry }) {
  if (status === 'loading') {
    return <div className="empty-state loading"><strong>Loading recipes…</strong>Fetching the latest from your Google Sheet.</div>
  }
  if (status === 'missing') {
    return <div className="empty-state"><strong>No recipe source set.</strong>Add your Apps Script web app URL as VITE_RECIPES_URL in .env and rebuild.</div>
  }
  return (
    <div className="empty-state">
      <strong>Couldn’t reach the recipes.</strong>The Google Sheet didn’t answer.
      <button className="retry-button" type="button" onClick={retry}>Try again</button>
    </div>
  )
}

function App() {
  const { status, data, retry, setFavorites } = useSheetData()
  const { favorites } = data
  const { isFavorite, toggle: toggleFavorite, message, clearMessage } = useFavorites(favorites, setFavorites)
  const favoriteProps = (recipe) => ({ isFavorite: isFavorite(recipe), onToggleFavorite: toggleFavorite })
  const [eater, setEater] = useState('adult')
  const [meal, setMeal] = useState('Dinner')
  const [selectedProtein, setSelectedProtein] = useState('Chicken')
  const [selectedTime, setSelectedTime] = useState(30)
  const isToddler = eater === 'toddler'
  const usesProtein = proteinMeals.includes(meal)
  const matches = topRecipes(data, { eater, meal, protein: selectedProtein, maxTime: selectedTime })
  const sourceNames = eaters.find((option) => option.id === eater).sites.map((site) => sources[site].name).join(' & ')
  let step = 1
  const nextStep = () => String(++step).padStart(2, '0')

  return (
    <div className="page-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="Supper home"><span className="brand-mark" aria-hidden="true" />supper</a>
        <a className="favorites-link" href="#favorites"><HeartIcon />Favorites{favorites.length > 0 && <span className="favorites-count">{favorites.length}</span>}</a>
      </header>
      <main id="top">
        <section className="intro" aria-labelledby="page-title">
          <div><p className="eyebrow">A little help for today</p><h1 id="page-title">What’s for <em>{meal.toLowerCase()}?</em></h1></div>
          <p className="intro-note">Pick a meal, tell us who’s eating and how long you’ve got. We’ll find the best-loved recipes.</p>
        </section>

        <div className="meal-tabs" role="tablist" aria-label="Choose a meal">
          {meals.map((option) => (
            <button key={option} className="meal-tab" type="button" role="tab" aria-selected={meal === option} aria-controls="results" onClick={() => setMeal(option)}>
              {option}
            </button>
          ))}
        </div>

        <section className="picker" aria-label={`${meal} preferences`}>
          <div className="picker-row">
            <div className="picker-heading"><span className="step-number">01</span><h2 className="section-label">Who’s eating?</h2></div>
            <div className="choice-row" role="group" aria-label="Choose who is eating">
              {eaters.map((option) => (
                <button key={option.id} className="choice choice-eater" type="button" aria-pressed={eater === option.id} onClick={() => setEater(option.id)}>
                  {option.label}<span className="choice-note">{option.note}</span>
                </button>
              ))}
            </div>
          </div>
          {usesProtein && (
            <div className="picker-row">
              <div className="picker-heading"><span className="step-number">{nextStep()}</span><h2 className="section-label">What are we cooking with?</h2></div>
              <div className="choice-row" role="group" aria-label="Choose a protein">
                {proteins.map((protein) => <button key={protein} className="choice" type="button" aria-pressed={selectedProtein === protein} onClick={() => setSelectedProtein(protein)}>{protein}</button>)}
              </div>
            </div>
          )}
          <div className="picker-row">
            <div className="picker-heading"><span className="step-number">{nextStep()}</span><h2 className="section-label">How much time have you got?</h2></div>
            <div className="choice-row" role="group" aria-label="Choose maximum cooking time">
              {cookTimes.map((time) => <button key={time} className="choice" type="button" aria-pressed={selectedTime === time} onClick={() => setSelectedTime(time)}>{timeLabel(time)}</button>)}
            </div>
          </div>
        </section>

        <section id="results" className="results" role="tabpanel" aria-labelledby="results-title" aria-live="polite">
          <div className="results-heading">
            <div>
              <p className="eyebrow">{isToddler ? `Little-eater ${meal.toLowerCase()}` : `Your ${meal.toLowerCase()} shortlist`}</p>
              <h2 id="results-title">{matches.length ? `Top ${matches.length} picks` : 'Let’s try another time'}</h2>
            </div>
            <span className="result-count">
              {usesProtein && `${selectedProtein.toLowerCase()} · `}
              {selectedTime === Infinity ? 'any time' : `up to ${selectedTime} min`} · {isToddler ? 'in each site’s order' : 'ranked by rating'} · from {sourceNames}
            </span>
          </div>
          {status !== 'ready' ? <LoadState status={status} retry={retry} /> : matches.length ? (
            <div className="recipe-grid">
              {matches.map((recipe, index) => (
                <RecipeCard
                  key={recipe.id}
                  recipe={recipe}
                  index={index}
                  meta={cardMeta(recipe)}
                  footer={cardFooter(recipe)}
                  {...favoriteProps(recipe)}
                />
              ))}
            </div>
          ) : <div className="empty-state"><strong>No recipes in this window.</strong>Give yourself a little more time and we’ll find something lovely.</div>}
          {isToddler && (
            <p className="safety-note">Always check age-appropriate sizes and textures, and talk to your pediatrician before introducing common allergens.</p>
          )}
        </section>

        <section id="favorites" className="results favorites" aria-labelledby="favorites-title">
          <div className="results-heading">
            <div><p className="eyebrow">Saved for later</p><h2 id="favorites-title">Your favorites</h2></div>
            <span className="result-count">{favorites.length} saved · shared through your Google Sheet</span>
          </div>
          {favorites.length ? (
            <div className="recipe-grid">
              {favorites.map((recipe, index) => (
                <RecipeCard key={recipe.url} recipe={recipe} index={index} meta={cardMeta(recipe)} footer={cardFooter(recipe)} {...favoriteProps(recipe)} />
              ))}
            </div>
          ) : status === 'ready' && (
            <div className="empty-state"><strong>Nothing saved yet.</strong>Tap the heart on any recipe to keep it here.</div>
          )}
        </section>

        <section className="results new-recipes" aria-labelledby="new-title">
          <div className="results-heading">
            <div><p className="eyebrow">Fresh off the sites</p><h2 id="new-title">New recipes</h2></div>
            <span className="result-count">The 3 newest recipes from each site</span>
          </div>
          {newSites[eater].map((site) => data.newest[site]?.length > 0 && (
            <div className="new-group" key={site}>
              <h3 className="new-source">{sources[site].name}</h3>
              <div className="recipe-grid">
                {data.newest[site].map((recipe, index) => (
                  <RecipeCard
                    key={recipe.id}
                    recipe={recipe}
                    index={index}
                    meta={<><span className="recipe-tag new-tag">New</span><span>{formatDate(recipe.published)}</span></>}
                    footer={recipe.age || formatTime(recipe.time)}
                    {...favoriteProps(recipe)}
                  />
                ))}
              </div>
            </div>
          ))}
        </section>
      </main>
      <footer>
        <span>Made for the “what should we eat?” moment.{data.generatedAt && ` Recipes updated ${new Date(data.generatedAt).toLocaleDateString()} · stored in Google Sheets.`}</span>
        <span>Recipes from {Object.values(sources).map((source, i, all) => (
          <span key={source.name}><a href={source.home} target="_blank" rel="noopener noreferrer">{source.name}</a>{i < all.length - 2 ? ', ' : i === all.length - 2 ? ' & ' : ''}</span>
        ))}.</span>
      </footer>
      {message && (
        <div className="toast" role="status">
          <span>{message}</span>
          <button type="button" aria-label="Dismiss" onClick={clearMessage}>×</button>
        </div>
      )}
    </div>
  )
}

export default App
