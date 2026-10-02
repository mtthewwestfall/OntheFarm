# OntheFarm

Farm management tracker: crops, animals, tasks, expenses, sales, goals, feeding schedule,
build projects, supply search, and a guest view of care instructions.

Live at https://onthefarm.getveridatenow.com

## Features

- **Crops**: crop, variety, planted date, expected harvest, area, status, notes.
- **Animals**: name, species/breed, head count, location/pasture, special instructions, health notes.
- **Tasks**: due dates, priorities, done state, optional project link.
- **Expenses**: date, category, amount, description, monthly totals by category.
- **Sales**: what was sold, how many (e.g. 3 cartons, 12 per carton), amount, customer,
  paid or pending. Week, month and year totals, plus who still owes.
- **Goals**: % progress or money saved, optionally tied to a project.
- **Feeding**: per animal group: feed type, amount, times of day (or times per day), special instructions.
- **OfftheFARM**: read-only list of every animal's feeding and special instructions for whoever
  watches the farm. Print or save it as a PDF at `/schedule/print`.
- **Projects**: take or upload site photos, generate a picture of the finished build in that
  spot (Gemini image model, following the owner's plan), per-project tasks and goals. The app
  offers **one** build recommendation per project; once it's given or declined it's never offered again.
- **Shop**: searches the web (Gemini with Google Search grounding) for feed, parts, tools and
  building materials, using the farm's location to favor nearby stores.
- **Invites (free)**: the owner creates invite links.
  - *Farm member*: full access to the farm.
  - *Guest*: can only see the OfftheFARM instructions.

## Run locally

```bash
pip install -r requirements.txt
GEMINI_API_KEY=... python -m flask --app server run --debug
python -m pytest -q
```

## Configuration

| Variable | Purpose |
| --- | --- |
| `DB_PATH` | SQLite file. On Railway: `/data/onthefarm.db` on a volume mounted at `/data`. |
| `GEMINI_API_KEY` | Enables the build visualizer, project advice and Shop search. |
| `IMAGE_MODEL` | Default `gemini-2.5-flash-image`. |
| `TEXT_MODEL` | Default `gemini-flash-latest`. |
| `PUBLIC_APP_URL` | Base URL used in invite links, e.g. `https://onthefarm.getveridatenow.com`. |

## Deploy (Railway)

Railway runs `Procfile` (gunicorn). Attach a volume at `/data`, set the variables above, and
add the custom domain `onthefarm.getveridatenow.com`. DNS at Porkbun: a `CNAME` for `onthefarm`
pointing to the Railway target, plus the `_railway-verify.onthefarm` TXT record Railway shows.
