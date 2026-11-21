# Asgard Library Manager

Manage the games on an [AYN Thor](https://github.com/AsgardOperationSystem/asgardos) running Asgard OS from a browser on
the same network: upload games, rename and delete them, and get their art and details, without opening the Thor or copying
files by hand.

- **See your library** console by console, with the names, covers, years, developers and descriptions scraping found.
- **Upload games**, one by one, many at once, or a whole folder. Drop them on a console, or on **Upload anything**, which
  puts each file in its console by its type. When several consoles take a type (`.zip`, `.chd`, `.iso`), the Thor's game
  database recognises the game by its checksum. If that doesn't settle it, you pick.
- **Big files are fine.** Uploads go in 8 MB pieces, and a dropped connection carries on where it stopped. The Thor stays
  awake while a game arrives, even with its lid closed.
- **Scrape** a game, or every game missing its art, from here. The Thor's own scraping queue does it, and it finishes
  before the Thor sleeps.

How to use it, step by step: **[the wiki's Library Manager page](https://github.com/AsgardOperationSystem/asgardos/wiki/Library-Manager)**.

## How it works

The web app is one file, `index.html`: no build step, no framework, and nothing loaded from the internet. It talks to
`asgard-library.py`, a small service on the Thor (port **8467**), which serves this page too. Open
`http://<the Thor's address>:8467` in a browser. The Thor shows its address under **Settings → Hardware → Network →
Library Manager**.

You can also open `index.html` from your own computer and type the Thor's address. A copy on a website can't work:
browsers block an `https://` page from talking to a device on your network over plain `http://`.

### Pairing

The first time a browser connects, the Thor shows a six-digit code on its screen, and you type it in the browser. You get
three tries. After the third wrong code, the Thor shows a new one, and each failed round makes the next wait longer
(10 s, then 20 s, 40 s and so on, up to 15 minutes). A right code gives the browser a token that it keeps (in
`localStorage`, per Thor), so it doesn't ask again. The Thor keeps only a hash of the token.

- **Unpair** in the web app forgets this browser.
- **Settings → Hardware → Network → Library Manager → Forget paired browsers** on the Thor forgets every browser.
- The service can be turned off on the same page.

Without a token, the service answers only `/api/hello` and the pairing requests. Everything it changes stays inside the
consoles' game folders: it checks each name, refuses anything that would land outside them, and writes an upload under a
temporary name until it's complete.

The rules the Thor's side follows are in Asgard OS's
[`SPEC.md`](https://github.com/AsgardOperationSystem/asgardos/blob/main/SPEC.md) (LIB-1 to LIB-5, and XFER-1 to XFER-5 for the
transfers queue).

## API

JSON over HTTP on port 8467. Send `Authorization: Bearer <token>` with every request except the first three.

| Request | What it does |
|---|---|
| `GET /api/hello` | `{app, name, version, paired}` |
| `POST /api/pair` | Shows a code on the Thor: `{pairing, tries, expires_in}` |
| `POST /api/pair/confirm` `{pairing, code, name}` | `{token}`, or the error with `tries` left, `new_code` and `retry_in` |
| `POST /api/pair/cancel` `{pairing}` | Ends a pairing |
| `POST /api/logout` | Forgets this token |
| `GET /api/systems` | Every console: `name`, `fullname`, `extensions`, `count`, `size`; and `free` bytes |
| `GET /api/games?system=` | The console's games: `path`, `stem`, `size`, `mtime`, `media`, and the scraped `name`, `desc`, `developer`… |
| `GET /api/media?system=&stem=&kind=covers` | A game's cover (or `screenshots`, `marquees`) |
| `GET /api/route?name=[&crc=]` | Which consoles take this file, and which the scrape database knows its CRC32 for |
| `GET /api/upload?system=&path=` | How much of an upload already arrived: `{received, exists}` |
| `PUT /api/upload?system=&path=&offset=&total=[&scrape=1][&replace=1]` | One piece, at most 64 MB |
| `DELETE /api/upload?system=&path=` | Drops a partial upload |
| `POST /api/rename` `{system, path, to}` | Renames a game in its folder |
| `POST /api/delete` `{system, path}` | Deletes a game (its saves stay) |
| `POST /api/scrape` `{system, paths}` | Queues scrapes |
| `GET /api/transfers` | The Thor's scraping and download queue |

## Testing

`test/test_webapp.py` runs the page against the Thor's real service on a fake root, with no Thor, in headless Firefox
through geckodriver. It uses Python's standard library only.

```sh
ASGARDOS=../asgardos python3 -I test/test_webapp.py        # needs firefox and geckodriver
SHOTS=test/shots ASGARDOS=../asgardos python3 -I test/test_webapp.py   # also saves a screenshot of each step
```

## License

GPL-2.0, as Asgard OS's own packages are. See `LICENSE`.
