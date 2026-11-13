"""
Sonos as a plain Windows speaker.

Whatever Windows plays to the VB-Audio Virtual Cable output device is sent to
the chosen Sonos speakers. Pick that device in the Windows sound menu and the
Sonos plays; pick anything else and it is released after an hour of silence.
The tray icon's menu ticks which Sonos speakers to use and sets their volume.
The Windows volume (and mute) of that output device is the Sonos volume.

How it works:
  1. Read the cable's audio by WASAPI loopback.
  2. Serve it as an endless WAV file over HTTP on this PC.
  3. When sound starts, tell the Sonos to play that URL.

Setup: run install.bat (see README.md).
"""

import json
import os
import queue
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def leave_console():
    """Started with python.exe (double-clicked, or from a command prompt)?
    Then start again with pythonw.exe, which has no console window, and let
    this copy end. Otherwise the window stays open and closing it would quit
    Speaker Bridge. Pass --console to stay in the console."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if exe.name.lower() != "python.exe" or "--console" in sys.argv or not pythonw.exists():
        return
    import subprocess
    subprocess.Popen(
        [str(pythonw), os.path.abspath(__file__), *sys.argv[1:]],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sys.exit(0)


if __name__ == "__main__":
    leave_console()

import soco

# ---- settings ---------------------------------------------------------------
CABLE_NAME = "VB-Audio Virtual Cable"   # part of the output device's name
PORT = 5757                  # the Sonos fetches the audio from this port
IDLE_STOP_SECONDS = 60 * 60  # release the Sonos after this much silence
# -----------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
SETTINGS_FILE = HERE / "settings.json"   # which speaker to use; written by the tray menu
CHUNK = 1024                    # frames per read (~21ms)
SILENCE_PEAK = 16               # samples at or below this count as silence
RETRY_SECONDS = 10              # min gap between "play" commands / discovery passes

soco.config.REQUEST_TIMEOUT = 4.0

log_file = open(HERE / "speaker_bridge.log", "w", buffering=1, encoding="utf-8")


def log(msg):
    log_file.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def load_settings():
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if "speaker" in saved:   # written before more than one speaker could be chosen
        name, ip = saved.pop("speaker"), saved.pop("ip", None)
        saved["speakers"] = [name]
        saved["ips"] = {name: ip} if ip else {}
    return saved


# ---- shared state -----------------------------------------------------------
settings = load_settings()   # {"speakers": [room names], "ips": {room name: last address}}
speakers = {}          # room name -> soco.SoCo, from the last discovery pass
zones = []             # the chosen speakers that are online, in name order
volume = None          # the first one's volume (0-100) as last read or set
muted = False          # ... and whether it is muted
volume_lock = threading.Lock()   # held while reading or setting the Sonos volume
holding_sonos = False  # we started playback and haven't released it yet
last_play = -1e9       # monotonic time of the last "play" command
last_volume_check = -1e9
tray = None            # the pystray icon, once it exists

clients = set()        # one queue per connected Sonos HTTP request
clients_lock = threading.Lock()
audio_format = None    # (sample rate, channels) of the open capture stream
generation = 0         # bumped when the capture stream reopens
last_sound = -1e9      # monotonic time of the last non-silent audio


# ---- 1. capture -------------------------------------------------------------
def is_sound(data):
    samples = memoryview(data).cast("h")
    return max(samples) > SILENCE_PEAK or min(samples) < -SILENCE_PEAK


def publish(data):
    with clients_lock:
        targets = list(clients)
    for q in targets:
        try:
            q.put_nowait(data)
        except queue.Full:      # listener is behind: drop the oldest chunk
            try:
                q.get_nowait()
                q.put_nowait(data)
            except Exception:
                pass


def capture_loop():
    """Windows only delivers loopback audio while something is playing, so a
    read blocks during silence. The HTTP side fills those gaps with silence."""
    global audio_format, generation, last_sound
    import pyaudiowpatch as pyaudio
    warned = False
    while True:
        pa = pyaudio.PyAudio()
        try:
            dev = next((d for d in pa.get_loopback_device_info_generator()
                        if CABLE_NAME.lower() in d["name"].lower()), None)
            if dev is None:
                if not warned:
                    warned = True
                    log(f"no output device containing '{CABLE_NAME}' found; waiting for it "
                        f"(install VB-CABLE from https://vb-audio.com/Cable/)")
                time.sleep(5)
                continue
            warned = False
            rate, channels = int(dev["defaultSampleRate"]), int(dev["maxInputChannels"])
            if channels > 2:
                log(f"'{dev['name']}' is set to {channels} channels; set it to stereo "
                    f"in the Sound control panel")
                time.sleep(30)
                continue
            stream = pa.open(format=pyaudio.paInt16, channels=channels, rate=rate,
                             input=True, input_device_index=dev["index"],
                             frames_per_buffer=CHUNK)
            audio_format = (rate, channels)
            generation += 1
            log(f"capturing '{dev['name']}' at {rate}Hz, {channels}ch")
            errors = 0
            while errors < 5:
                try:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                except Exception as e:
                    errors += 1
                    log(f"capture error: {e}")
                    time.sleep(0.2)
                    continue
                errors = 0
                if is_sound(data):
                    last_sound = time.monotonic()
                publish(data)
            log("capture stream died; reopening")
        except Exception as e:
            log(f"capture setup failed: {e}")
        finally:
            try:
                pa.terminate()
            except Exception:
                pass
        time.sleep(2)


# ---- 2. HTTP stream ---------------------------------------------------------
def wav_header(rate, channels):
    size = 0x7FFFFFFF   # "endless" so the Sonos treats it as a live stream
    return (b"RIFF" + struct.pack("<I", size) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * channels * 2,
                          channels * 2, 16)
            + b"data" + struct.pack("<I", size))


class StreamHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.end_headers()

    def do_HEAD(self):
        self._headers()

    def do_GET(self):
        if audio_format is None:
            self.send_error(503)
            return
        rate, channels = audio_format
        my_generation = generation
        silence = bytes(int(rate * 0.1) * channels * 2)   # 100ms
        q = queue.Queue(maxsize=25)                       # ~0.5s
        with clients_lock:
            clients.add(q)
        log(f"Sonos connected ({self.client_address[0]})")
        try:
            self._headers()
            self.wfile.write(wav_header(rate, channels))
            while my_generation == generation:
                try:
                    data = q.get(timeout=0.1)
                except queue.Empty:
                    data = silence
                self.wfile.write(data)
        except OSError:
            pass
        finally:
            with clients_lock:
                clients.discard(q)
            log("Sonos disconnected")


# ---- 3. Sonos control -------------------------------------------------------
def refresh_menu():
    if tray is not None:
        try:
            tray.update_menu()
        except Exception:
            pass


def discover():
    """Find the Sonos speakers on the network (for the tray's Speakers menu).
    If the network search finds nothing, fall back to the chosen speakers'
    last known addresses."""
    global speakers
    found = {}
    try:
        found = {z.player_name: z for z in (soco.discover(timeout=5) or []) if z.is_visible}
    except Exception as e:
        log(f"speaker search failed: {e}")
    if not found:
        for ip in settings.get("ips", {}).values():
            try:
                z = soco.SoCo(ip)
                found[z.player_name] = z
            except Exception:
                pass
    if sorted(found) != sorted(speakers):
        log(f"speakers found: {', '.join(sorted(found)) or 'none'}")
    speakers = found
    update_zones()
    refresh_menu()
    return found


def update_zones():
    global zones
    zones = [speakers[n] for n in sorted(settings.get("speakers") or []) if n in speakers]


def save_choice(names):
    """Save which speakers are ticked, with their addresses where known."""
    ips = settings.get("ips", {})
    settings["speakers"] = sorted(names)
    settings["ips"] = {n: speakers[n].ip_address if n in speakers else ips.get(n)
                       for n in settings["speakers"]}
    settings["ips"] = {n: ip for n, ip in settings["ips"].items() if ip}
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def find_speakers():
    """Look for the chosen speakers. On first run the first speaker found
    becomes the chosen one. A chosen speaker that is offline is waited for,
    never swapped for a different room."""
    found = discover()
    if settings.get("speakers") is None and found:
        first = sorted(found)[0]
        save_choice([first])
        update_zones()
        log(f"using {first} (change it from the tray icon's Speakers menu)")
        refresh_menu()


def toggle_speaker(name):
    """Tray menu: tick or untick a speaker."""
    global volume, holding_sonos, last_play, last_volume_check
    chosen = set(settings.get("speakers") or [])
    if name in chosen:
        chosen.discard(name)
        speaker = speakers.get(name)
        others_online = any(n in speakers for n in chosen)
        try:
            if speaker is not None and clients:
                if others_online and len(speaker.group.members) > 1:
                    speaker.unjoin()    # the rest of the group keeps playing
                else:
                    coordinator(speaker).stop()
        except Exception as e:
            log(f"couldn't stop {name}: {e}")
        log(f"unticked {name}")
    else:
        chosen.add(name)
        log(f"ticked {name}")
    save_choice(chosen)
    update_zones()
    volume = None
    holding_sonos = holding_sonos and bool(zones)
    last_play = last_volume_check = -1e9   # act on the change right away
    refresh_menu()


def read_volume(speaker):
    """Note the speaker's volume, in case it was changed elsewhere (such as
    the Sonos app). The lock keeps a slow read from undoing a newer change."""
    global volume, muted
    with volume_lock:
        new = (speaker.volume, speaker.mute)
        changed = new != (volume, muted)
        volume, muted = new
    if changed:
        refresh_menu()


def set_volume(new_volume, new_muted=None):
    """Set every chosen speaker's volume, and its mute if given."""
    global volume, muted
    with volume_lock:
        if not zones:
            log("volume not changed: no chosen Sonos speaker is online")
            return
        for speaker in zones:
            try:
                speaker.volume = new_volume
                if new_muted is not None:
                    speaker.mute = new_muted
            except Exception as e:
                log(f"volume change failed on {speaker.player_name}: {e}")
        volume = new_volume
        if new_muted is not None:
            muted = new_muted
    refresh_menu()


def cable_endpoint():
    """The Windows volume control of the cable's output device, or None."""
    from pycaw.pycaw import AudioUtilities
    for dev in AudioUtilities.GetAllDevices():
        if (dev.FriendlyName and CABLE_NAME.lower() in dev.FriendlyName.lower()
                and dev.id.startswith("{0.0.0.")   # an output, not a recording device
                and dev.state.name == "Active"):
            return dev.EndpointVolume
    return None


def windows_volume_loop():
    """Keep the Windows volume of the cable device and the Sonos volume the
    same. Windows' own volume has no effect on the audio we capture, so the
    Sonos does the actual turning down. A change made in Windows (slider,
    volume keys, mute) goes to the Sonos; a change made anywhere else (tray
    menu, Sonos app) moves the Windows slider. On startup the Sonos volume
    wins, so a slider left at 100% can't make the Sonos blast."""
    import comtypes
    comtypes.CoInitialize()
    endpoint = None
    seen = None   # the Windows (volume, muted) as last read or set here
    while True:
        time.sleep(0.2)
        try:
            if endpoint is None:
                endpoint = cable_endpoint()
                if endpoint is None:
                    time.sleep(5)
                    continue
            windows = (round(endpoint.GetMasterVolumeLevelScalar() * 100),
                       bool(endpoint.GetMute()))
            sonos = (volume, muted)
            if seen is not None and windows != seen:
                set_volume(*windows)
            elif zones and volume is not None and sonos != windows:
                endpoint.SetMasterVolumeLevelScalar(volume / 100, None)
                endpoint.SetMute(muted, None)
                windows = sonos
            seen = windows
        except Exception as e:
            log(f"Windows volume sync error: {e}")
            endpoint = seen = None
            time.sleep(5)


def coordinator(speaker):
    """Play/stop must go to the group coordinator if the speaker is grouped."""
    try:
        return speaker.group.coordinator or speaker
    except Exception:
        return speaker


def group_up(active):
    """Put the other chosen speakers in the first one's Sonos group, so they
    all play the same stream in sync."""
    lead = coordinator(active[0])
    for speaker in active[1:]:
        try:
            if coordinator(speaker).uid != lead.uid:
                speaker.join(lead)
                log(f"grouped {speaker.player_name} with {active[0].player_name}")
        except Exception as e:
            log(f"couldn't group {speaker.player_name}: {e}")


def lan_ip_towards(target_ip):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target_ip, 80))
        return s.getsockname()[0]
    finally:
        s.close()


METADATA = (
    '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
    'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
    '<item id="1" parentID="0" restricted="1">'
    "<dc:title>PC Audio</dc:title>"
    "<upnp:class>object.item.audioItem.musicTrack</upnp:class>"
    "</item></DIDL-Lite>"
)


def control_loop():
    """Start the Sonos when sound starts and it isn't already pulling our
    stream (also covers dropouts); stop it after IDLE_STOP_SECONDS of silence.
    Also keeps the tray's volume in step with changes made elsewhere."""
    global zones, holding_sonos, last_play, last_volume_check
    last_find = -1e9
    while True:
        time.sleep(0.25)
        now = time.monotonic()
        try:
            wanted = settings.get("speakers")
            if wanted is None or len(zones) < len(wanted):
                # search often while nothing is online, rarely for a straggler
                if now - last_find > (60 if zones else RETRY_SECONDS):
                    last_find = now
                    find_speakers()
            active = zones
            if not active:
                continue
            speaker = active[0]
            if now - last_volume_check > 5:
                last_volume_check = now
                read_volume(speaker)
                if holding_sonos and clients:   # e.g. a speaker was just ticked
                    group_up(active)
            if now - last_sound < 2 and not clients and now - last_play > RETRY_SECONDS:
                last_play = now
                group_up(active)
                url = f"http://{lan_ip_towards(speaker.ip_address)}:{PORT}/pc.wav"
                coordinator(speaker).play_uri(url, meta=METADATA)
                holding_sonos = True
                log(f"playing on {', '.join(s.player_name for s in active)}")
            elif holding_sonos and now - last_sound > IDLE_STOP_SECONDS:
                holding_sonos = False
                if clients:   # still on our stream (not switched to another source)
                    coordinator(speaker).stop()
                    log("silent for a while; released the Sonos")
        except Exception as e:
            log(f"Sonos control error: {e}")
            zones = []


# ---- tray icon --------------------------------------------------------------
def quit_app(icon=None, item=None):
    try:
        if zones and clients:
            coordinator(zones[0]).stop()
    except Exception:
        pass
    os._exit(0)


START_MENU_LINK = (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows"
                   / "Start Menu" / "Programs" / "Speaker Bridge.lnk")


def toggle_start_menu(icon=None, item=None):
    """Add Speaker Bridge to the Start menu's app list, or take it out again."""
    try:
        if START_MENU_LINK.exists():
            START_MENU_LINK.unlink()
            log("removed from the Start menu")
            return
        import subprocess
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        env = dict(os.environ,
                   SB_LINK=str(START_MENU_LINK),
                   SB_TARGET=str(pythonw if pythonw.exists() else sys.executable),
                   SB_SCRIPT=str(Path(__file__).resolve()),
                   SB_DIR=str(HERE),
                   SB_ICON=str(HERE / "speaker_bridge.ico"))
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:SB_LINK);"
             "$s.TargetPath = $env:SB_TARGET;"
             "$s.Arguments = '\"' + $env:SB_SCRIPT + '\"';"
             "$s.WorkingDirectory = $env:SB_DIR;"
             "if (Test-Path $env:SB_ICON) { $s.IconLocation = $env:SB_ICON };"
             "$s.Save()"],
            env=env, check=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        log("added to the Start menu")
        if icon:
            icon.notify("Open Start, type Speaker Bridge, then right-click it "
                        "and choose Pin to Start.", "Added to the Start menu")
    except Exception as e:
        log(f"Start menu shortcut error: {e}")


def run_tray():
    global tray
    import pystray
    from pystray import Menu, MenuItem
    from PIL import Image, ImageDraw
    try:
        # the same icon the Start menu shortcut uses
        img = Image.open(HERE / "speaker_bridge.ico")
        img.load()
    except Exception:
        # icon file missing or unreadable: draw a plain speaker instead
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((14, 4, 50, 60), radius=8, fill=(30, 30, 30, 255))
        draw.ellipse((22, 26, 42, 46), fill=(240, 240, 240, 255))
        draw.ellipse((28, 11, 36, 19), fill=(240, 240, 240, 255))

    def level_item(level):
        return MenuItem(
            f"{level}%",
            lambda icon, item: set_volume(level),
            checked=lambda item: volume is not None and round(volume / 10) * 10 == level,
            radio=True)

    def speaker_item(name):
        return MenuItem(
            name if name in speakers else f"{name} (not found)",
            lambda icon, item: toggle_speaker(name),
            checked=lambda item: name in (settings.get("speakers") or []))

    def speakers_label(item):
        chosen = settings.get("speakers")
        if chosen is None:
            return "Speakers: none yet"
        return f"Speakers: {', '.join(chosen) or 'none'}"

    def speaker_items():
        names = sorted(set(speakers) | set(settings.get("speakers") or []))
        items = [speaker_item(name) for name in names]
        if not items:
            items = [MenuItem("No speakers found", None, enabled=False)]
        return items + [
            Menu.SEPARATOR,
            MenuItem("Search again",
                     lambda icon, item: threading.Thread(target=discover, daemon=True).start())]

    tray = pystray.Icon("SpeakerBridge", img, "Speaker Bridge", Menu(
        MenuItem("Show in Start menu", toggle_start_menu,
                 checked=lambda item: START_MENU_LINK.exists()),
        MenuItem(speakers_label, Menu(speaker_items)),
        MenuItem(lambda item: "Volume" if volume is None
                 else f"Volume: {volume}%{' (muted)' if muted else ''}",
                 Menu(*[level_item(level) for level in range(100, -1, -10)])),
        Menu.SEPARATOR,
        MenuItem("Quit", quit_app)))
    tray.run()


def main():
    try:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), StreamHandler)
    except OSError as e:
        log(f"port {PORT} is in use (is Speaker Bridge already running?): {e}")
        sys.exit(1)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=capture_loop, daemon=True).start()
    threading.Thread(target=control_loop, daemon=True).start()
    threading.Thread(target=windows_volume_loop, daemon=True).start()
    log("started")
    try:
        run_tray()
    except Exception as e:
        log(f"no tray icon ({e}); running without one")
        threading.Event().wait()


if __name__ == "__main__":
    main()
