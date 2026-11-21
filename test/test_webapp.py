#!/usr/bin/env python3
"""End-to-end test of index.html against the Thor's real service, with no Thor: asgard-library.py from an Asgard OS checkout
runs on a fake root, a stand-in Asgard Home shows the pairing code, and headless Firefox drives the page through geckodriver.
Python's standard library only.

    ASGARDOS=../asgardos python3 -I test/test_webapp.py      (needs firefox and geckodriver on PATH)

Checks: connecting by address, pairing (a wrong code, then the right one), the console list and a console's games with their
scraped names, uploading a .gba (routed by extension) and a .zip that two consoles take (routed by the CRC in its central
directory, through the scrape index), resuming an upload the Thor already has part of, renaming and deleting a game, and
unpairing."""
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ASGARDOS = os.path.abspath(os.environ.get("ASGARDOS", os.path.join(REPO, "..", "asgardos")))
SERVICE = os.path.join(ASGARDOS, "packages/asgard/sources/asgard-library.py")
if not os.path.isfile(SERVICE):
    sys.exit(f"SKIP: no Asgard OS checkout at {ASGARDOS} (set ASGARDOS)")
for tool in ("firefox", "geckodriver"):
    if not shutil.which(tool):
        sys.exit(f"SKIP: {tool} isn't installed")

# Under the repo, not /tmp: a snap-packaged Firefox has its own /tmp and couldn't read the files it's asked to upload.
RUN = os.path.join(HERE, ".run")
shutil.rmtree(RUN, ignore_errors=True)
ROOT, SHARE, FILES = (os.path.join(RUN, d) for d in ("root", "share", "files"))


def write(path, data=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --- a fake Thor ---------------------------------------------------------------------------------------------------------
write(f"{ROOT}/usr/config/emulationstation/es_systems.cfg", """<systemList>
<system><name>gba</name><fullname>Game Boy Advance</fullname><path>/storage/roms/gba</path><extension>.gba .zip</extension></system>
<system><name>snes</name><fullname>Super Nintendo</fullname><path>/storage/roms/snes</path><extension>.sfc .smc .zip</extension></system>
<system><name>psx</name><fullname>PlayStation</fullname><path>/storage/roms/psx</path><extension>.cue .chd</extension></system>
</systemList>""")
write(f"{ROOT}/storage/roms/gba/Zelda.gba", b"z" * 2048)
os.makedirs(f"{ROOT}/storage/roms/snes", exist_ok=True)
write(f"{ROOT}/storage/ES-DE/gamelists/gba/gamelist.xml", "<gameList><game><path>./Zelda.gba</path>"
      "<name>The Legend of Zelda: The Minish Cap</name><developer>Capcom</developer><releasedate>20041104T000000</releasedate>"
      "<desc>Link shrinks.</desc></game></gameList>")
write(f"{ROOT}/storage/ES-DE/downloaded_media/gba/covers/Zelda.png",
      bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"))
write(f"{ROOT}/storage/.config/system/configs/system.cfg", "system.hostname=Test Thor\n")
if os.environ.get("SHOTS"):   # screenshots for the wiki: a fuller library, with coloured covers (all made up)
    def png(rgb, w=60, h=80):
        raw = b"".join(b"\0" + bytes(rgb) * w for _ in range(h))
        chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) +
                chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    demo = [("Star Pilot", "A shooter in space.", (90, 110, 220)), ("Forest Quest", "", (60, 160, 110)),
            ("Puzzle Tower", "Stack blocks to the sky.", (220, 140, 60)), ("Racing Days", "", (200, 70, 110))]
    entries = ""
    for name, desc, rgb in demo:
        write(f"{ROOT}/storage/roms/gba/{name}.gba", b"x" * 4194304)
        write(f"{ROOT}/storage/ES-DE/downloaded_media/gba/covers/{name}.png", png(rgb))
        entries += f"<game><path>./{name}.gba</path><name>{name}</name><desc>{desc}</desc><developer>Homebrew</developer></game>"
    write(f"{ROOT}/storage/roms/gba/Untitled.gba", b"x" * 1048576)
    write(f"{ROOT}/storage/ES-DE/gamelists/gba/gamelist.xml", "<gameList><game><path>./Zelda.gba</path>"
          "<name>The Legend of Zelda: The Minish Cap</name><developer>Capcom</developer><releasedate>20041104T000000</releasedate>"
          "<desc>Link shrinks.</desc></game>" + entries + "</gameList>")
os.makedirs(f"{ROOT}/run", exist_ok=True)
SMW = b"super mario world rom" * 100
write(f"{SHARE}/asgard-scrape.py", f"INDEX = {SHARE!r} + '/index'\nSYSTEMS = {{'snes': ('Nintendo - SNES', 4), "
                                   f"'gba': ('Nintendo - GBA', 12)}}\n")
write(f"{SHARE}/index/Nintendo - SNES.tsv", f"Super Mario World (USA)\t{zlib.crc32(SMW):08X}\tNintendo\n")
write(f"{SHARE}/asgard-transfers.py", f"import json,sys\nopen({SHARE!r}+'/transfers.log','a').write(' '.join(sys.argv[1:])+'\\n')\n"
                                      "print(json.dumps({'added':[1]} if sys.argv[1]=='add' else {'busy':False,'queue':[],"
                                      "'current':None,'paused':'','scrape':{'done':0,'found':0,'total':0}}))\n")

# Asgard Home, as far as pairing goes: shows each code by writing it back
fifo = f"{ROOT}/run/asgard-home.cmd"
os.mkfifo(fifo)
codes = []


def home():
    while True:
        with open(fifo) as f:
            for line in f:
                if line.startswith("pair "):
                    codes.append(line.split()[1])
                    write(f"{ROOT}/run/asgard-library-pin", line.split()[1])


threading.Thread(target=home, daemon=True).start()

PORT = free_port()
env = dict(os.environ, ASGARD_ROOT=ROOT, ASGARD_SHARE=SHARE, ASGARD_LIBRARY_PORT=str(PORT), ASGARD_LIBRARY_WEBAPP=REPO)
service = subprocess.Popen([sys.executable, "-I", SERVICE, "serve"], env=env)
WD_PORT = free_port()
driver = subprocess.Popen(["geckodriver", "--port", str(WD_PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait(cond, what, secs=15):
    end = time.monotonic() + secs
    while True:
        try:
            got = cond()
            if got:
                return got
        except Exception:
            pass
        if time.monotonic() > end:
            raise AssertionError(f"timed out: {what() if callable(what) else what}")
        time.sleep(0.1)


# --- WebDriver, the few calls this needs -----------------------------------------------------------------------------------
def wd(method, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{WD_PORT}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["value"]


wait(lambda: urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/hello", timeout=1), "the service")
wait(lambda: socket.create_connection(("127.0.0.1", WD_PORT), timeout=1), "geckodriver")
sid = wd("POST", "/session", {"capabilities": {"alwaysMatch": {"moz:firefoxOptions": {"args": ["-headless"]}}}})["sessionId"]
S = f"/session/{sid}"
wd("POST", f"{S}/window/rect", {"width": 1280, "height": 800})


def js(script, *args):
    return wd("POST", f"{S}/execute/sync", {"script": script, "args": list(args)})


def find(css):
    v = wd("POST", f"{S}/element", {"using": "css selector", "value": css})
    assert "element-6066-11e4-a52e-4f735466cecf" in v, (css, v)   # WebDriver's element reference key
    return v["element-6066-11e4-a52e-4f735466cecf"]


def click(css):
    wd("POST", f"{S}/element/{find(css)}/click", {})


def typed(css, text):
    e = find(css)
    wd("POST", f"{S}/element/{e}/clear", {})
    wd("POST", f"{S}/element/{e}/value", {"text": text})


def text(css):
    return js("const e = document.querySelector(arguments[0]); return e ? e.textContent : null", css)


def visible(css):
    return js("const e = document.querySelector(arguments[0]); return !!e && !e.closest('.hidden') && e.offsetParent !== null", css)


SHOTS = os.environ.get("SHOTS")   # a folder: screenshots of each step, for the wiki


def shot(name):
    if SHOTS:
        import base64
        os.makedirs(SHOTS, exist_ok=True)
        with open(f"{SHOTS}/{name}.png", "wb") as f:
            f.write(base64.b64decode(wd("GET", f"{S}/screenshot")))


def button(label, within="body"):
    """Clicks the button whose text is label."""
    ok = js("const b = [...document.querySelectorAll(arguments[1] + ' button')].find(b => b.textContent.trim() === arguments[0]);"
            "if (b) b.click(); return !!b", label, within)
    assert ok, f"no button {label!r}"


try:
    # connect: the page asks for the Thor's address (opened on another port than the Thor's own, as from disk)
    wd("POST", f"{S}/url", {"url": f"http://127.0.0.1:{PORT}/"})
    wait(lambda: visible("#host"), "the connect form")
    if SHOTS:
        typed("#host", "192.168.1.30")   # what a user would type
        shot("library-manager-connect")
    typed("#host", f"127.0.0.1:{PORT}")
    click("#connectBtn")
    wait(lambda: visible("#code") and codes, "the pairing page and a code on the Thor")

    # a wrong code: two tries left; the right one: in
    wrong = "000000" if codes[-1] != "000000" else "111111"
    typed("#code", wrong)
    click("#pairBtn")
    wait(lambda: "2 tries left" in text("#pairMsg"), "the wrong-code message")
    shot("library-manager-pair")
    typed("#code", codes[-1])
    click("#pairBtn")
    wait(lambda: visible("#nav") and text("#thorName") == "Test Thor", "the library")
    token = json.loads(js("return localStorage.getItem('asgard.thors')"))[f"127.0.0.1:{PORT}"]["token"]
    assert len(token) > 30

    # consoles with games, the first opened, its game with its scraped name and art
    wait(lambda: "The Legend of Zelda: The Minish Cap" in text("#games"), "Zelda's card")
    assert "Game Boy Advance" in text("#nav") and "Capcom" in text("#games"), text("#nav")
    wait(lambda: js("const i = document.querySelector('.game img'); return !!i && i.complete && i.naturalWidth > 0"), "the cover")
    shot("library-manager-console")

    # upload anything: a .gba by its extension, a .zip that two consoles take by the CRC inside it
    write(f"{FILES}/Metroid Fusion.gba", os.urandom(20000))
    import zipfile
    with zipfile.ZipFile(f"{FILES}/smw.zip", "w") as z:
        z.writestr("Super Mario World (USA).sfc", SMW)
    click("#nav button.sys")   # Upload anything
    wait(lambda: "Upload anything" in text("#content"), "the upload page")
    js("localStorage.setItem('asgard.scrapeAfter', '1')")
    wd("POST", f"{S}/element/{find('#filePick')}/value", {"text": f"{FILES}/Metroid Fusion.gba\n{FILES}/smw.zip"})
    wait(lambda: os.path.isfile(f"{ROOT}/storage/roms/gba/Metroid Fusion.gba") and os.path.isfile(f"{ROOT}/storage/roms/snes/smw.zip"),
         lambda: "both uploads to land in their consoles: " + str(text("#upList")) + str(os.listdir(f"{ROOT}/storage/roms")))
    assert open(f"{ROOT}/storage/roms/gba/Metroid Fusion.gba", "rb").read() == open(f"{FILES}/Metroid Fusion.gba", "rb").read()
    wait(lambda: "Super Mario World (USA)" in text("#upList"), "the zip recognised by name")
    shot("library-manager-upload")
    log = open(f"{SHARE}/transfers.log").read()
    assert "add scrape gba /storage/roms/gba/Metroid Fusion.gba Metroid Fusion" in log, log

    # resume: the Thor already has the first half of a file; only the rest is sent
    big = os.urandom(300000)
    write(f"{FILES}/Advance Wars.gba", big)
    write(f"{ROOT}/storage/roms/gba/Advance Wars.gba.asgard-part", big[:150000])
    wd("POST", f"{S}/element/{find('#filePick')}/value", {"text": f"{FILES}/Advance Wars.gba"})
    wait(lambda: os.path.isfile(f"{ROOT}/storage/roms/gba/Advance Wars.gba"), "the resumed upload")
    assert open(f"{ROOT}/storage/roms/gba/Advance Wars.gba", "rb").read() == big

    # rename and delete, from a console's page
    js("document.querySelector('#nav [data-sys=gba]').click()")
    wait(lambda: "Metroid Fusion" in text("#games"), "the new game in the list")
    js("[...document.querySelectorAll('.game')].find(g => g.dataset.path === 'Metroid Fusion.gba')"
       ".querySelectorAll('button')[1].click()")   # Rename
    wait(lambda: visible("#dlgInput"), "the rename dialog")
    typed("#dlgInput", "Metroid Fusion (USA).gba")
    click("#dlgYes")
    wait(lambda: os.path.isfile(f"{ROOT}/storage/roms/gba/Metroid Fusion (USA).gba"), "the rename")
    wait(lambda: js("return !!document.querySelector('.game[data-path=\"Zelda.gba\"]')"), "the list again")
    js("document.querySelector('.game[data-path=\"Zelda.gba\"]').querySelectorAll('button')[2].click()")   # Delete
    wait(lambda: visible("#dlgYes"), "the delete dialog")
    click("#dlgYes")
    wait(lambda: not os.path.exists(f"{ROOT}/storage/roms/gba/Zelda.gba"), "the delete")

    # unpair: the token stops working
    click("#logoutBtn")
    wait(lambda: visible("#dlgYes"), "the unpair dialog")
    click("#dlgYes")
    wait(lambda: visible("#host"), "back to connect")
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/systems", headers={"Authorization": "Bearer " + token})
    try:
        urllib.request.urlopen(req, timeout=5)
        raise AssertionError("the token still works after unpairing")
    except urllib.error.HTTPError as e:
        assert e.code == 401
    print("ok")
finally:
    try:
        wd("DELETE", S)
    except Exception:
        pass
    driver.terminate()
    service.terminate()
    shutil.rmtree(RUN, ignore_errors=True)
