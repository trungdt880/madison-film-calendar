# Madison campus film calendars

Subscribable `.ics` feeds built from:

- **UW Cinematheque**: https://cinema.wisc.edu/calendar/
- **WUD Film at the Marquee**: https://union.wisc.edu/visit/union-south/the-marquee

A GitHub Actions cron job (`.github/workflows/update.yml`) runs `build_calendars.py` every other day (or on demand via *Actions → Run workflow*) and commits any changes to `docs/`, which GitHub Pages serves.

| Feed | File |
| --- | --- |
| Cinematheque only | `docs/cinematheque.ics` |
| Marquee (WUD Film) only | `docs/marquee.ics` |
| Both combined | `docs/madison-film.ics` |

## Subscribing

Use the GitHub Pages URL, e.g. `https://<user>.github.io/madison-film-calendar/madison-film.ics`.

- **Google Calendar**: Other calendars → **+** → *From URL*. Google refreshes subscriptions on its own schedule (typically every 12–24 h).
- **Apple Calendar**: File → *New Calendar Subscription…* (or open the `webcal://` form of the URL). Set *Auto-refresh* to *Every day*.
- **Outlook**: Add calendar → *Subscribe from web*.

## How it scrapes

- **Cinematheque** runs WordPress + EventON. The script reads the calendar's shortcode config and nonce from the page, then calls EventON's `eventon_init_load` endpoint with a widened date range (30 days back to 1 year ahead). Descriptions come from `/wp-json/wp/v2/ajde_events`. The site hides end times, so the end time is the start plus the runtime from the description (`| 101 mins.`), defaulting to 2 h.
- **Marquee** uses the Wisconsin Union's XML feed (`/events-and-activities/event-calendar/feed`), keeping `category=Film` events titled "WUD Film Presents…" (committee meetings are skipped).

If one site fails or changes its layout, its previous `.ics` is kept (and the combined feed isn't rewritten), and the workflow run fails so GitHub emails you.

## Run locally

```sh
python3 build_calendars.py   # writes docs/*.ics; stdlib only, Python 3.9+
```
