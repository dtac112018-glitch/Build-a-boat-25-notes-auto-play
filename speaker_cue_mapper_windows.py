"""Build A Boat Speaker Cue Mapper — Windows edition.

Audio files are transcribed approximately; MIDI files use their note events directly.
Playback sends ordinary Windows keyboard events to the currently focused application.
The sound should come from the game's Music Note blocks, not from this app.
"""
from __future__ import annotations

import csv
import math
import os
import hashlib
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import defaultdict, deque
from dataclasses import dataclass
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

try:
    import numpy as np
except ImportError:
    np = None

KEYS = list("QWERTYUIOPASDFGHJKLZXCVBN")
MIDI_MIN = 54  # F#3
MIDI_MAX = 78  # F#5, inclusive: 25 semitones
SAMPLE_RATE = 22050
FRAME_SIZE = 4096
FFT_SIZE = 16384
HOP_SIZE = 512
MAX_SECONDS = 180
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_to_note(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


@dataclass
class NoteEvent:
    start: float
    duration: float
    midi: int
    velocity: int = 100

    @property
    def key(self) -> str:
        return KEYS[self.midi - MIDI_MIN]

    @property
    def note(self) -> str:
        return midi_to_note(self.midi)

    @property
    def hz(self) -> float:
        return 440.0 * (2.0 ** ((self.midi - 69) / 12.0))


def get_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError(
            "FFmpeg helper is missing. Install dependencies by running setup_windows.bat."
        ) from exc


def decode_audio(path: str) -> tuple[object, int, bool]:
    ffmpeg = get_ffmpeg()
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", path, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-t", str(MAX_SECONDS), "-f", "f32le", "pipe:1",
    ]
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=240, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Audio decoding timed out. Try a shorter file.") from exc
    if proc.returncode != 0 or not proc.stdout:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            "Could not read an audio track from this file. Choose an MP3, MP4 with audio, "
            "WAV, M4A, FLAC, OGG, or another FFmpeg-supported format.\n\n" + detail[-1200:]
        )
    audio = np.frombuffer(proc.stdout, dtype="<f4").copy()
    audio = np.nan_to_num(audio, copy=False)
    capped = len(audio) >= SAMPLE_RATE * MAX_SECONDS
    if len(audio) < int(SAMPLE_RATE * 0.25):
        raise RuntimeError("The audio is too short or contains no readable audio.")
    return audio, SAMPLE_RATE, capped



URL_MAX_BYTES = 100 * 1024 * 1024
URL_TIMEOUT_SECONDS = 25
URL_DOWNLOAD_DEADLINE_SECONDS = 180
URL_MEDIA_SUFFIXES = {
    ".mp3", ".mp4", ".m4a", ".wav", ".aac", ".flac", ".ogg",
    ".webm", ".mov", ".avi", ".mkv", ".opus", ".aiff", ".wma",
}
URL_MIME_SUFFIX = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "video/mp4": ".mp4",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "video/webm": ".webm",
}


def _download_direct_media_url(url: str) -> str:
    """Download a direct HTTP(S) media file to a bounded local cache file."""
    url = (url or "").strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("Paste a direct http:// or https:// MP3/MP4/audio URL.")
    if parsed.username is not None or parsed.password is not None:
        raise RuntimeError("URLs containing embedded usernames or passwords are not supported.")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SpeakerCueMapper/1.0 (direct media downloader)",
            "Accept": "audio/*, video/*, application/octet-stream, */*;q=0.5",
        },
        method="GET",
    )
    cache_dir = Path(tempfile.gettempdir()) / "SpeakerCueMapperURLCache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    partial_path = None

    try:
        with urllib.request.urlopen(request, timeout=URL_TIMEOUT_SECONDS) as response:
            final = urllib.parse.urlsplit(response.geturl())
            if final.scheme.lower() not in {"http", "https"}:
                raise RuntimeError("The URL redirected to an unsupported protocol.")

            status = getattr(response, "status", 200)
            if status < 200 or status >= 300:
                raise RuntimeError(f"URL download returned HTTP status {status}.")

            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type in {"text/html", "application/xhtml+xml", "application/json"}:
                raise RuntimeError(
                    "This URL points to a webpage/API response, not a direct media file. "
                    "Use a direct MP3/MP4/audio-file URL instead."
                )

            length_header = response.headers.get("Content-Length")
            if length_header:
                try:
                    announced_size = int(length_header)
                except ValueError:
                    announced_size = 0
                if announced_size > URL_MAX_BYTES:
                    raise RuntimeError("The media URL is larger than the 100 MB download limit.")

            url_suffix = Path(urllib.parse.unquote(final.path)).suffix.lower()
            suffix = url_suffix if url_suffix in URL_MEDIA_SUFFIXES else URL_MIME_SUFFIX.get(content_type, ".media")
            digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
            output_path = cache_dir / f"{digest}-{time.time_ns()}{suffix}"
            partial_path = output_path.with_suffix(output_path.suffix + ".part")

            total = 0
            first_chunk = response.read(65536)
            if not first_chunk:
                raise RuntimeError("The URL returned an empty response.")
            probe = first_chunk.lstrip().lower()
            if probe.startswith(b"<!doctype html") or probe.startswith(b"<html"):
                raise RuntimeError(
                    "The URL returned an HTML page, not a direct media file. "
                    "Paste a direct MP3/MP4/audio-file URL."
                )

            with open(partial_path, "wb") as out:
                out.write(first_chunk)
                total += len(first_chunk)
                while True:
                    if time.monotonic() - started > URL_DOWNLOAD_DEADLINE_SECONDS:
                        raise RuntimeError("URL download timed out after 3 minutes.")
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > URL_MAX_BYTES:
                        raise RuntimeError("The media URL exceeded the 100 MB download limit.")
                    out.write(chunk)

            if total < 256:
                raise RuntimeError("The URL response is too small to be a usable media file.")
            partial_path.replace(output_path)
            return str(output_path)

    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"The media server returned HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not download the media URL: {exc.reason}") from exc
    finally:
        if partial_path is not None:
            try:
                partial_path.unlink(missing_ok=True)
            except OSError:
                pass



def download_with_ytdlp(url: str) -> str:
    """Download audio from a supported media webpage with yt-dlp.

    This is intended for public or otherwise authorized media URLs. It does not
    bypass DRM, paywalls, or login restrictions.
    """
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError(
            "yt-dlp is not installed in this app environment. Run the setup script "
            "included with this ZIP, then try again."
        ) from exc

    cache_dir = Path(tempfile.gettempdir()) / "SpeakerCueMapperURLCache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    prefix = f"{digest}-ytdlp-"

    # Remove only old results from this exact URL, not the entire cache.
    for old in cache_dir.glob(prefix + "*"):
        try:
            if old.is_file():
                old.unlink()
        except OSError:
            pass

    template = str(cache_dir / (prefix + "%(id)s.%(ext)s"))

    def enforce_size_limit(info):
        if info.get("status") != "downloading":
            return
        current = int(info.get("downloaded_bytes") or 0)
        if current > URL_MAX_BYTES:
            raise yt_dlp.utils.DownloadError(
                "Download exceeded the 100 MB URL limit; stopping."
            )

    options = {
        "format": "bestaudio/best",
        "outtmpl": template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": URL_TIMEOUT_SECONDS,
        "retries": 2,
        "fragment_retries": 2,
        "extractor_retries": 1,
        "max_filesize": URL_MAX_BYTES,
        "progress_hooks": [enforce_size_limit],
        "cachedir": str(cache_dir / "yt-dlp-cache"),
        "overwrites": True,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise RuntimeError("The website did not return a downloadable media item.")
            if info.get("_type") == "playlist" or info.get("entries"):
                raise RuntimeError(
                    "Playlist URLs are not supported. Paste a link to one video or track."
                )
            expected = Path(ydl.prepare_filename(info))

        if expected.is_file():
            result = expected
        else:
            candidates = [
                p for p in cache_dir.glob(prefix + "*")
                if p.is_file() and not p.name.endswith((".part", ".ytdl", ".json"))
            ]
            if not candidates:
                raise RuntimeError("yt-dlp finished without producing a media file.")
            result = max(candidates, key=lambda p: p.stat().st_mtime)

        if result.stat().st_size <= 0:
            result.unlink(missing_ok=True)
            raise RuntimeError("The downloaded media file is empty.")
        if result.stat().st_size > URL_MAX_BYTES:
            result.unlink(missing_ok=True)
            raise RuntimeError("The downloaded media exceeds the 100 MB limit.")
        return str(result)
    except Exception as exc:
        raise RuntimeError(
            "yt-dlp could not extract audio from this URL. The site may be unsupported, "
            "the video may be private/login-gated, or the site may require a newer yt-dlp. "
            "Only use media you have permission to download; DRM/access restrictions are not bypassed.\n\n"
            f"Details: {exc}"
        ) from exc


def download_media_url(url: str) -> str:
    """Accept direct media-file URLs and supported webpage URLs via yt-dlp."""
    url = (url or "").strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("Paste an http:// or https:// media URL or supported video-page URL.")
    if parsed.username is not None or parsed.password is not None:
        raise RuntimeError("URLs containing embedded usernames or passwords are not supported.")

    suffix = Path(urllib.parse.unquote(parsed.path)).suffix.lower()
    # Direct media-file URLs are faster and don't need a website extractor.
    if suffix in URL_MEDIA_SUFFIXES:
        try:
            return _download_direct_media_url(url)
        except Exception as direct_error:
            # Some sites use a media-looking route which requires an extractor.
            try:
                return download_with_ytdlp(url)
            except Exception as extractor_error:
                raise RuntimeError(
                    "Could not download this direct media URL or extract it with yt-dlp.\n\n"
                    f"Direct download: {direct_error}\n\nWebsite extractor: {extractor_error}"
                ) from extractor_error

    # Webpage URLs (for example YouTube watch URLs) need yt-dlp.
    try:
        return download_with_ytdlp(url)
    except Exception as extractor_error:
        # If yt-dlp does not recognize it, a no-extension direct media endpoint
        # may still be downloadable through ordinary HTTP.
        try:
            return _download_direct_media_url(url)
        except Exception as direct_error:
            raise RuntimeError(
                "Could not extract this webpage or download it as a direct media file.\n\n"
                f"Website extractor: {extractor_error}\n\nDirect download: {direct_error}"
            ) from extractor_error

def nearby_peak(spec, index: int, radius: int = 2) -> float:
    lo = max(1, index - radius)
    hi = min(len(spec), index + radius + 1)
    return float(np.max(spec[lo:hi])) if hi > lo else 0.0


def analyze_audio(
    audio, sr: int, max_notes: int = 3,
    main_pitch_only: bool = False, piano_mode: bool = True,
) -> list[NoteEvent]:
    """Approximate polyphonic note detection, outputting notes in F#3–F#5."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return []
    peak = float(np.max(np.abs(audio)))
    if peak > 1.0:
        audio = audio / peak

    window = np.hanning(FRAME_SIZE).astype(np.float32)
    freqs = np.array([
        440.0 * (2.0 ** ((m - 69) / 12.0))
        for m in range(MIDI_MIN, MIDI_MAX + 1)
    ])
    starts = list(range(0, len(audio), HOP_SIZE))
    hop_seconds = HOP_SIZE / sr
    frame_rms = []
    for start in starts:
        frame = audio[start:start + FRAME_SIZE]
        if len(frame) < FRAME_SIZE:
            frame = np.pad(frame, (0, FRAME_SIZE - len(frame)))
        frame_rms.append(float(np.sqrt(np.mean(frame * frame))))
    max_rms = max(frame_rms, default=0.0)
    gate = max(0.0012, max_rms * 0.035)
    bin_hz = sr / FFT_SIZE
    presence = np.zeros((len(starts), len(freqs)), dtype=bool)
    limit = 1 if main_pitch_only else max(1, min(25, int(max_notes)))

    for row, start in enumerate(starts):
        if frame_rms[row] < gate:
            continue
        frame = audio[start:start + FRAME_SIZE]
        if len(frame) < FRAME_SIZE:
            frame = np.pad(frame, (0, FRAME_SIZE - len(frame)))
        spectrum = np.abs(np.fft.rfft(frame * window, n=FFT_SIZE)).astype(np.float32)
        spectrum_peak = float(np.max(spectrum))
        if spectrum_peak <= 1e-8:
            continue

        fundamentals = np.zeros(len(freqs), dtype=np.float32)
        scores = np.zeros(len(freqs), dtype=np.float32)

        if piano_mode:
            residual = spectrum.copy()
            for _ in range(limit):
                fundamentals[:] = 0
                scores[:] = 0
                residual_peak = float(np.max(residual))
                for idx, frequency in enumerate(freqs):
                    k1 = int(round(frequency / bin_hz))
                    fundamental = nearby_peak(residual, k1, 1)
                    fundamentals[idx] = fundamental
                    if fundamental < spectrum_peak * 0.0015:
                        continue
                    score = 1.65 * fundamental
                    for harmonic in range(2, 8):
                        k = int(round(frequency * harmonic / bin_hz))
                        if k >= len(residual):
                            break
                        score += nearby_peak(residual, k, 1) / (harmonic ** 1.25)
                    scores[idx] = score
                best_idx = int(np.argmax(scores))
                best_score = float(scores[best_idx])
                if best_score < max(spectrum_peak * 0.018, residual_peak * 0.035):
                    break
                if presence[row, best_idx]:
                    break
                presence[row, best_idx] = True
                frequency = freqs[best_idx]
                # Reduce that pitch's partials so the following pass can find
                # other notes in a chord instead of repeatedly selecting the same note.
                for harmonic in range(1, 8):
                    k = int(round(frequency * harmonic / bin_hz))
                    if k >= len(residual):
                        break
                    lo, hi = max(1, k - 1), min(len(residual), k + 2)
                    residual[lo:hi] *= 0.28
        else:
            for idx, frequency in enumerate(freqs):
                k1 = int(round(frequency / bin_hz))
                k2 = int(round(2.0 * frequency / bin_hz))
                k3 = int(round(3.0 * frequency / bin_hz))
                fundamentals[idx] = nearby_peak(spectrum, k1, 2)
                a2 = nearby_peak(spectrum, k2, 2) if k2 < len(spectrum) else 0.0
                a3 = nearby_peak(spectrum, k3, 2) if k3 < len(spectrum) else 0.0
                scores[idx] = fundamentals[idx] + 0.22 * a2 + 0.08 * a3
            best = float(np.max(scores))
            best_fundamental = float(np.max(fundamentals))
            if best <= 0 or best_fundamental <= 0:
                continue
            candidates = (
                (scores >= best * 0.20)
                & (fundamentals >= best_fundamental * 0.08)
                & (fundamentals >= spectrum_peak * 0.006)
            )
            indices = np.flatnonzero(candidates)
            if indices.size > limit:
                selected = indices[np.argsort(scores[indices])[-limit:]]
                candidates[:] = False
                candidates[selected] = True
            presence[row] = candidates

    # Smooth isolated detections and convert continuous pitch regions to events.
    smooth = presence.copy()
    for pitch_idx in range(len(freqs)):
        raw = presence[:, pitch_idx]
        for i in range(1, len(starts) - 1):
            if raw[i - 1] and raw[i + 1]:
                smooth[i, pitch_idx] = True
            elif raw[i] and not raw[i - 1] and not raw[i + 1]:
                smooth[i, pitch_idx] = False

    events: list[NoteEvent] = []
    duration_audio = len(audio) / sr
    for pitch_idx in range(len(freqs)):
        i = 0
        while i < len(starts):
            if not smooth[i, pitch_idx]:
                i += 1
                continue
            first = i
            while i < len(starts) and smooth[i, pitch_idx]:
                i += 1
            st = first * hop_seconds
            end = min(i * hop_seconds, duration_audio)
            if end - st >= 0.075:
                events.append(NoteEvent(st, end - st, MIDI_MIN + pitch_idx))
    events.sort(key=lambda e: (e.start, e.midi))
    return events


def fold_to_range(note: int) -> int:
    while note < MIDI_MIN:
        note += 12
    while note > MIDI_MAX:
        note -= 12
    return note


def load_midi_events(path: str, max_notes: int = 3, main_pitch_only: bool = False) -> list[NoteEvent]:
    try:
        import mido
    except ImportError as exc:
        raise RuntimeError("MIDI support requires mido. Run setup_windows.bat again.") from exc

    midi = mido.MidiFile(path)
    tempo = 500000
    elapsed = 0.0
    active = defaultdict(deque)
    raw: list[tuple[float, float, int, int]] = []

    for msg in midi.merged_track:
        elapsed += msg.time * tempo / (midi.ticks_per_beat * 1_000_000.0)
        if msg.type == "set_tempo":
            tempo = msg.tempo
        elif msg.type == "note_on" and msg.velocity > 0:
            active[(msg.channel, msg.note)].append((elapsed, int(msg.velocity)))
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            queue = active.get((msg.channel, msg.note))
            if queue:
                start, velocity = queue.popleft()
                raw.append((start, max(0.025, elapsed - start), fold_to_range(int(msg.note)), velocity))

    for (_, note), queue in list(active.items()):
        for start, velocity in queue:
            raw.append((start, max(0.10, elapsed - start), fold_to_range(int(note)), velocity))
    if not raw:
        raise RuntimeError("No note events were found in this MIDI file.")

    raw.sort(key=lambda item: item[0])
    limit = 1 if main_pitch_only else max(1, min(25, int(max_notes)))
    groups: list[list[tuple[float, float, int, int]]] = []
    for item in raw:
        if not groups or item[0] - groups[-1][0][0] > 0.025:
            groups.append([item])
        else:
            groups[-1].append(item)
    events: list[NoteEvent] = []
    for group in groups:
        chosen = sorted(group, key=lambda item: item[3], reverse=True)[:limit]
        seen = set()
        for start, duration, pitch, velocity in chosen:
            ident = (round(start, 4), pitch)
            if ident not in seen:
                events.append(NoteEvent(start, duration, pitch, velocity))
                seen.add(ident)
    events.sort(key=lambda e: (e.start, e.midi))
    return events


# Windows keyboard input through user32.SendInput (no external key sender needed).
if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    # INPUT is a tagged union of MOUSEINPUT, KEYBDINPUT and HARDWAREINPUT.
    # Defining only KEYBDINPUT makes sizeof(INPUT) too small on 64-bit Windows,
    # causing SendInput to reject the whole batch because cbSize is incorrect.
    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT),
        ]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("union",)
        _fields_ = [
            ("type", wintypes.DWORD),
            ("union", INPUT_UNION),
        ]

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008
    MAPVK_VK_TO_VSC = 0


def foreground_window_info() -> tuple[str, str]:
    """Return (foreground title, process executable name) on Windows."""
    if os.name != "nt":
        return "", ""

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", ""

    title_buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW.argtypes = (
        wintypes.HWND, wintypes.LPWSTR, ctypes.c_int
    )
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextW(hwnd, title_buf, len(title_buf))

    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId.argtypes = (
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
    )
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    process_name = ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32.OpenProcess.argtypes = (
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD
    )
    kernel32.OpenProcess.restype = wintypes.HANDLE
    hprocess = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
    )
    if hprocess:
        try:
            image_buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(image_buf))
            kernel32.QueryFullProcessImageNameW.argtypes = (
                wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD)
            )
            kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
            if kernel32.QueryFullProcessImageNameW(
                hprocess, 0, image_buf, ctypes.byref(size)
            ):
                process_name = os.path.basename(image_buf.value).lower()
        finally:
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(hprocess)

    return title_buf.value, process_name


def is_roblox_foreground() -> tuple[bool, str, str]:
    title, process_name = foreground_window_info()
    is_game = (
        "roblox" in title.casefold()
        or process_name in {"robloxplayerbeta.exe", "roblox.exe"}
    )
    return is_game, title, process_name

def send_game_keys(keys: list[str]) -> None:
    """Send a short chord only while Roblox is the actual foreground process."""
    if os.name != "nt":
        raise RuntimeError("This Windows edition must be run on Windows.")

    unique = list(dict.fromkeys(str(k).upper() for k in keys))
    if not unique:
        return

    for key in unique:
        if len(key) != 1 or not ("A" <= key <= "Z"):
            raise RuntimeError(f"Unsupported key: {key}")

    is_game, title, process_name = is_roblox_foreground()
    if not is_game:
        raise RuntimeError(
            "Playback stopped to protect other apps: Roblox is not the foreground window.\n\n"
            f"Foreground title: {title or '(no title)'}\n"
            f"Foreground process: {process_name or '(unknown)'}\n\n"
            "Click the Roblox game window before playback and keep it focused."
        )

    # IMPORTANT: Initialize user32 and set function signatures BEFORE calling
    # MapVirtualKeyW. This avoids the prior UnboundLocalError.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (
        wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int
    )
    user32.SendInput.restype = wintypes.UINT
    user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
    user32.MapVirtualKeyW.restype = wintypes.UINT

    scans = []
    for key in unique:
        scan = int(user32.MapVirtualKeyW(ord(key), MAPVK_VK_TO_VSC))
        if scan == 0:
            raise RuntimeError(f"Windows could not map {key} to a keyboard scan code.")
        scans.append(scan)

    # One batch: all keys down first, then all keys up to approximate a chord.
    events = []
    for scan in scans:
        ki = KEYBDINPUT(0, scan, KEYEVENTF_SCANCODE, 0, 0)
        events.append((INPUT_KEYBOARD, ki))
    for scan in scans:
        ki = KEYBDINPUT(0, scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0, 0)
        events.append((INPUT_KEYBOARD, ki))

    inputs = (INPUT * len(events))()
    for i, (kind, keyboard_input) in enumerate(events):
        inputs[i].type = kind
        inputs[i].ki = keyboard_input

    expected = len(inputs)
    ctypes.set_last_error(0)
    sent = int(user32.SendInput(expected, inputs, ctypes.sizeof(INPUT)))
    if sent != expected:
        error_code = ctypes.get_last_error()
        # If Windows accepted some key-downs but not all events, best-effort
        # release every involved key to reduce the chance of a stuck key.
        release = (INPUT * len(scans))()
        for i, scan in enumerate(scans):
            release[i].type = INPUT_KEYBOARD
            release[i].ki = KEYBDINPUT(
                0, scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0, 0
            )
        user32.SendInput(len(release), release, ctypes.sizeof(INPUT))
        raise RuntimeError(
            f"Windows accepted {sent} of {expected} keyboard events. "
            f"INPUT size={ctypes.sizeof(INPUT)} bytes; GetLastError={error_code}.\n\n"
            "SendInput can be blocked when the target app has a higher integrity level. "
            "Run both Roblox and Speaker Cue Mapper normally (neither as Administrator)."
        )


class SpeakerCueMapper:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Build A Boat Speaker Cue Mapper — Windows")
        self.root.geometry("970x730")
        self.root.minsize(820, 620)
        self.root.configure(bg="#10131b")

        self.source_path: str | None = None
        self.source_kind: str | None = None
        self.source_url: str | None = None
        self.url_var = tk.StringVar()
        self.url_downloading = False
        self.pending_reanalysis_after_url = False
        self.url_generation = 0
        self.events: list[NoteEvent] = []
        self.capped = False
        self.busy = False
        self.pending_analysis = False
        self.analysis_generation = 0
        self.reanalyze_job = None
        self.cue_running = False
        self.cue_start_time = 0.0
        self.cue_index = 0
        self.cue_speed = tk.DoubleVar(value=1.0)
        self.note_count_var = tk.StringVar(value="3 notes")
        self.main_pitch_var = tk.BooleanVar(value=False)
        self.piano_mode_var = tk.BooleanVar(value=True)
        self._build_ui()

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", padding=(9, 6), font=("Segoe UI", 10))
        style.configure("TLabel", background="#10131b", foreground="#e8edf5", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 19, "bold"), foreground="#ffffff")
        style.configure("Sub.TLabel", font=("Segoe UI", 10), foreground="#aab5c8")
        style.configure("Treeview", rowheight=24, font=("Consolas", 10), background="#1b2130", foreground="#e8edf5", fieldbackground="#1b2130")
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

        outer = tk.Frame(self.root, bg="#10131b", padx=16, pady=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Build A Boat Speaker Cue Mapper", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Windows edition · Q = F♯3 · N = F♯5 · in-game sound only", style="Sub.TLabel").pack(anchor="w", pady=(3, 11))

        top = tk.Frame(outer, bg="#1a2030", padx=10, pady=10)
        top.pack(fill="x")
        self.file_label = tk.Label(top, text="Choose an audio or MIDI file", bg="#1a2030", fg="#dce5f5", anchor="w", font=("Segoe UI", 10), wraplength=470)
        self.file_label.grid(row=0, column=0, sticky="ew", padx=(0, 8), columnspan=2)
        self.audio_btn = ttk.Button(top, text="Choose MP3 / MP4…", command=self.choose_audio)
        self.audio_btn.grid(row=0, column=2, sticky="e", padx=(4, 0))
        self.midi_btn = ttk.Button(top, text="Choose MIDI…", command=self.choose_midi)
        self.midi_btn.grid(row=0, column=3, sticky="e", padx=(4, 0))
        self.analyze_btn = ttk.Button(top, text="Analyze again", command=self.request_analysis)
        self.analyze_btn.grid(row=1, column=3, sticky="e", pady=(8, 0), padx=(4, 0))
        self.status = tk.Label(top, text="Choose an MP3/MP4 or MIDI file. MP4 video is ignored.", bg="#1a2030", fg="#aab5c8", anchor="w", justify="left", wraplength=570)
        self.status.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0), padx=(0, 8))
        top.grid_columnconfigure(0, weight=1)

        url_row = tk.Frame(outer, bg="#1a2030", padx=10, pady=8)
        url_row.pack(fill="x", pady=(8, 0))
        tk.Label(
            url_row, text="Sound URL:", bg="#1a2030", fg="#e8edf5",
            font=("Segoe UI", 10, "bold")
        ).pack(side="left", padx=(0, 8))
        self.url_entry = ttk.Entry(url_row, textvariable=self.url_var)
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.url_btn = ttk.Button(
            url_row, text="Decode URL sound", command=self.decode_url
        )
        self.url_btn.pack(side="right")

        settings = tk.Frame(outer, bg="#1a2030", padx=10, pady=9)
        settings.pack(fill="x", pady=(9, 7))
        tk.Label(settings, text="Notes at once:", bg="#1a2030", fg="#e8edf5").pack(side="left", padx=(0, 7))
        opts = ["1 note"] + [f"{n} notes" for n in range(2, 26)] + ["All notes (not recommended)"]
        self.note_combo = ttk.Combobox(settings, textvariable=self.note_count_var, values=opts, state="readonly", width=26)
        self.note_combo.pack(side="left", padx=(0, 10))
        self.note_combo.bind("<<ComboboxSelected>>", self.settings_changed)
        self.main_switch = tk.Checkbutton(settings, text="Main pitch only: OFF", variable=self.main_pitch_var, indicatoron=False, command=self.settings_changed, bg="#374151", fg="white", selectcolor="#16803d", activebackground="#374151", activeforeground="white", relief="flat", padx=8, pady=5)
        self.main_switch.pack(side="left", padx=(0, 7))
        self.piano_switch = tk.Checkbutton(settings, text="Piano mode: ON", variable=self.piano_mode_var, indicatoron=False, command=self.settings_changed, bg="#374151", fg="white", selectcolor="#16803d", activebackground="#374151", activeforeground="white", relief="flat", padx=8, pady=5)
        self.piano_switch.pack(side="left")

        legend = tk.Frame(outer, bg="#10131b")
        legend.pack(fill="x", pady=(0, 7))
        ttk.Label(legend, text="25-note mapping: QWERTYUIOP · ASDFGHJKL · ZXCVBN").pack(side="left")
        ttk.Label(legend, text="MIDI: direct note timing  |  Audio: approximate transcription").pack(side="right")

        table_frame = tk.Frame(outer, bg="#10131b")
        table_frame.pack(fill="both", expand=True)
        cols = ("idx", "time", "duration", "key", "note", "hz")
        self.table = ttk.Treeview(table_frame, columns=cols, show="headings", height=12)
        for col, label, width in [("idx", "#", 50), ("time", "Start (s)", 105), ("duration", "Duration (s)", 110), ("key", "Key", 85), ("note", "Pitch", 95), ("hz", "Frequency", 110)]:
            self.table.heading(col, text=label)
            self.table.column(col, width=width, anchor="center")
        bar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=bar.set)
        self.table.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

        controls = tk.Frame(outer, bg="#10131b")
        controls.pack(fill="x", pady=(10, 0))
        ttk.Label(controls, text="Playback speed:").pack(side="left")
        self.speed_label = tk.Label(controls, text="1.00×", bg="#10131b", fg="#ffffff", width=6)
        self.speed_label.pack(side="left", padx=(5, 2))
        scale = ttk.Scale(controls, from_=0.2, to=1.5, variable=self.cue_speed, orient="horizontal", length=150, command=lambda _v: self.speed_label.configure(text=f"{self.cue_speed.get():.2f}×"))
        scale.pack(side="left", padx=(2, 10))
        self.save_btn = ttk.Button(controls, text="Save CSV…", command=self.save_csv, state="disabled")
        self.save_btn.pack(side="right", padx=(7, 0))
        self.stop_btn = ttk.Button(controls, text="Stop", command=self.stop_playback, state="disabled")
        self.stop_btn.pack(side="right", padx=(7, 0))
        self.play_btn = ttk.Button(controls, text="Play (3-second focus countdown)", command=self.start_countdown, state="disabled")
        self.play_btn.pack(side="right")

        tk.Label(outer, text="Focus Roblox during the 3-second countdown. Playback waits for Roblox to become foreground and stops if focus is lost. Run both apps at the same privilege level (normally neither as Administrator).", bg="#10131b", fg="#aab5c8", justify="left", wraplength=900, font=("Segoe UI", 9)).pack(fill="x", pady=(8, 0))

    def set_status(self, text: str, color: str = "#aab5c8"):
        self.status.configure(text=text, fg=color)

    def choose_audio(self):
        path = filedialog.askopenfilename(
            title="Choose an audio or video file",
            filetypes=[("Audio/video", "*.mp3 *.mp4 *.m4a *.wav *.aac *.flac *.ogg *.webm *.mov *.avi"), ("All files", "*.*")],
        )
        if path:
            self.source_path = path
            self.source_kind = "audio"
            self.source_url = None
            self.file_label.configure(text=path)
            self.schedule_analysis(100)

    def choose_midi(self):
        path = filedialog.askopenfilename(
            title="Choose a MIDI file",
            filetypes=[("MIDI files", "*.mid *.midi"), ("All files", "*.*")],
        )
        if path:
            self.source_path = path
            self.source_kind = "midi"
            self.source_url = None
            self.file_label.configure(text=path)
            self.schedule_analysis(100)

    def decode_url(self):
        url = self.url_var.get().strip()
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            messagebox.showinfo(
                "Paste a media or webpage URL",
                "Paste an http:// or https:// direct media URL, YouTube video URL, "
                "or another webpage supported by yt-dlp. Login-gated/DRM media "
                "is not supported."
            )
            return
        if self.url_downloading:
            return

        self.url_generation += 1
        generation = self.url_generation
        self.url_downloading = True
        self.pending_reanalysis_after_url = False
        self.url_btn.configure(state="disabled")
        self.set_status("Resolving URL / downloading audio (limit 100 MB)…", "#ffcb67")
        threading.Thread(
            target=self._url_download_worker,
            args=(url, generation),
            daemon=True,
        ).start()

    def _url_download_worker(self, url: str, generation: int):
        try:
            path = download_media_url(url)
            error = None
        except Exception as exc:
            path = None
            error = str(exc)
        self.root.after(
            0, lambda: self._url_download_done(url, generation, path, error)
        )

    def _url_download_done(self, url, generation, path, error):
        if generation != self.url_generation:
            return
        self.url_downloading = False
        self.url_btn.configure(state="normal")
        if error:
            self.set_status(f"URL download failed: {error}", "#ff7777")
            messagebox.showerror("Could not decode URL sound", error)
            if self.pending_reanalysis_after_url and self.source_path:
                self.pending_reanalysis_after_url = False
                self.schedule_analysis(100)
            return

        self.source_path = path
        self.source_kind = "audio"
        self.source_url = url
        self.file_label.configure(text=f"URL media: {url}")
        self.set_status(
            "URL media retrieved. Decoding audio and analyzing notes…",
            "#ffcb67",
        )
        self.pending_reanalysis_after_url = False
        self.schedule_analysis(100)

    def settings_changed(self, _event=None):
        self.main_switch.configure(text=f"Main pitch only: {'ON' if self.main_pitch_var.get() else 'OFF'}")
        self.piano_switch.configure(text=f"Piano mode: {'ON' if self.piano_mode_var.get() else 'OFF'}")
        self.schedule_analysis(400)

    def schedule_analysis(self, delay=350):
        if self.url_downloading:
            self.pending_reanalysis_after_url = True
            return
        if not self.source_path:
            return
        if self.reanalyze_job is not None:
            try:
                self.root.after_cancel(self.reanalyze_job)
            except tk.TclError:
                pass
        self.reanalyze_job = self.root.after(delay, self.request_analysis)

    def request_analysis(self):
        self.reanalyze_job = None
        if self.url_downloading:
            self.pending_reanalysis_after_url = True
            return
        if not self.source_path:
            self.set_status("Choose an MP3/MP4 or MIDI file first.")
            return
        if self.busy:
            self.pending_analysis = True
            return

        self.pending_analysis = False
        self.busy = True
        self.analysis_generation += 1
        generation = self.analysis_generation
        source_path = self.source_path
        source_kind = self.source_kind
        selected = self.note_count_var.get()
        max_notes = 25 if selected.startswith("All") else int(selected.split()[0])
        main_only = bool(self.main_pitch_var.get())
        piano_mode = bool(self.piano_mode_var.get())
        self.play_btn.configure(state="disabled")
        self.analyze_btn.configure(state="disabled")
        self.save_btn.configure(state="disabled")
        self.set_status("Reading MIDI events…" if source_kind == "midi" else "Decoding audio and analyzing pitches…", "#ffcb67")
        worker = threading.Thread(
            target=self._analysis_worker,
            args=(generation, source_path, source_kind, max_notes, main_only, piano_mode),
            daemon=True,
        )
        worker.start()

    def _analysis_worker(self, generation, source_path, source_kind, max_notes, main_only, piano_mode):
        try:
            if source_kind == "midi":
                events = load_midi_events(source_path, max_notes, main_only)
                capped = False
            else:
                if np is None:
                    raise RuntimeError("NumPy is missing. Run setup_windows.bat.")
                audio, sr, capped = decode_audio(source_path)
                events = analyze_audio(audio, sr, max_notes, main_only, piano_mode)
            error = None
        except Exception as exc:
            events, capped, error = [], False, str(exc)
        self.root.after(0, lambda: self._analysis_done(generation, events, capped, error))

    def _analysis_done(self, generation, events, capped, error):
        self.busy = False
        self.analyze_btn.configure(state="normal")
        # Ignore a stale worker if the user selected another file while it ran.
        if generation == self.analysis_generation:
            if error:
                self.events = []
                self.play_btn.configure(state="disabled")
                self.save_btn.configure(state="disabled")
                self.set_status(error, "#ff7777")
                messagebox.showerror("Could not analyze file", error)
            else:
                self.events = events
                self.capped = capped
                for item in self.table.get_children():
                    self.table.delete(item)
                for idx, ev in enumerate(events[:5000], start=1):
                    self.table.insert("", "end", values=(idx, f"{ev.start:.3f}", f"{ev.duration:.3f}", ev.key, ev.note, f"{ev.hz:.1f} Hz"))
                if not events:
                    self.set_status("No note events found. Try a clearer or shorter clip.", "#ff7777")
                    self.play_btn.configure(state="disabled")
                    self.save_btn.configure(state="disabled")
                else:
                    mode = "MIDI" if self.source_kind == "midi" else "approximate audio transcription"
                    suffix = f" Only the first {MAX_SECONDS} seconds were analyzed." if capped else ""
                    self.set_status(f"Loaded {len(events)} note events using {mode}.{suffix}", "#8be0a2")
                    self.play_btn.configure(state="normal")
                    self.save_btn.configure(state="normal")
        if self.pending_analysis:
            self.pending_analysis = False
            self.schedule_analysis(100)

    def save_csv(self):
        if not self.events:
            return
        path = filedialog.asksaveasfilename(
            title="Save note events", defaultextension=".csv",
            filetypes=[("CSV file", "*.csv")], initialfile="speaker_cues.csv",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["start_seconds", "duration_seconds", "key", "note", "frequency_hz", "midi_note"])
                for ev in self.events:
                    writer.writerow([f"{ev.start:.4f}", f"{ev.duration:.4f}", ev.key, ev.note, f"{ev.hz:.2f}", ev.midi])
            messagebox.showinfo("Saved", f"Saved {len(self.events)} events to:\n{path}")
        except OSError as exc:
            messagebox.showerror("Save failed", str(exc))

    def start_countdown(self):
        if not self.events or self.cue_running:
            return
        self.play_btn.configure(state="disabled")
        self.set_status("Click the Roblox game window now — playback starts in 3…", "#ffcb67")
        def tick(n):
            if not self.root.winfo_exists():
                return
            if n > 0:
                self.set_status(f"Focus Roblox now — playback starts in {n}…", "#ffcb67")
                self.root.after(1000, lambda: tick(n - 1))
            else:
                # Hide the mapper after the countdown so it cannot remain above
                # the game. After Windows switches back, verify the foreground
                # app before sending the first note.
                self.set_status("Checking Roblox focus…", "#ffcb67")
                self.root.iconify()
                self.root.after(250, self._begin_if_roblox_focused)
        tick(3)

    def _begin_if_roblox_focused(self, deadline=None):
        """Wait briefly for Roblox to become foreground after the app minimizes."""
        if deadline is None:
            deadline = time.monotonic() + 8.0

        is_game, title, process_name = is_roblox_foreground()
        if is_game:
            self._start_playback()
            return

        # Keep polling while minimized. No input is sent during this period.
        if time.monotonic() < deadline:
            self.root.after(150, lambda: self._begin_if_roblox_focused(deadline))
            return

        self.root.deiconify()
        self.play_btn.configure(state="normal" if self.events else "disabled")
        shown_title = title or "(no foreground title)"
        shown_process = process_name or "(unknown process)"
        message = (
            "Playback was not started; no keys were sent.\n\n"
            f"Foreground title: {shown_title}\n"
            f"Foreground process: {shown_process}\n\n"
            "Click the actual Roblox game window during the countdown. "
            "The app waited for focus but Roblox did not become the foreground window."
        )
        self.set_status("Playback cancelled: Roblox was not focused.", "#ff7777")
        messagebox.showerror("Roblox is not focused", message)

    def _start_playback(self):
        self.cue_running = True
        self.cue_index = 0
        self.cue_start_time = time.perf_counter()
        self.stop_btn.configure(state="normal")
        self.set_status("PLAYING IN-GAME — keep Roblox focused. Stop ends playback early.", "#8be0a2")
        self.root.after(1, self._playback_tick)

    def _playback_tick(self):
        if not self.cue_running:
            return
        if self.cue_index >= len(self.events):
            total = len(self.events)
            self.stop_playback(completed=True)
            self.set_status(f"Playback complete — sent {total} note events.", "#8be0a2")
            return

        now = time.perf_counter() - self.cue_start_time
        speed = max(0.20, min(1.5, float(self.cue_speed.get())))
        first = self.events[self.cue_index]
        due = first.start / speed
        if due > now:
            self.root.after(max(1, min(8, int((due - now) * 1000))), self._playback_tick)
            return

        # Send near-simultaneous notes as one chord, with all key-downs preceding key-ups.
        keys = []
        group_start = first.start
        while self.cue_index < len(self.events):
            ev = self.events[self.cue_index]
            if ev.start > group_start + 0.030:
                break
            if ev.key not in keys:
                keys.append(ev.key)
            self.cue_index += 1
        try:
            send_game_keys(keys)
        except Exception as exc:
            self.stop_playback()
            messagebox.showerror("Keyboard input failed", str(exc))
            return

        self.set_status(f"PLAYING IN-GAME · {' + '.join(keys)} · {self.cue_index}/{len(self.events)} events", "#8be0a2")
        self.root.after(1, self._playback_tick)

    def stop_playback(self, completed=False):
        was_running = self.cue_running
        self.cue_running = False
        self.stop_btn.configure(state="disabled")
        self.play_btn.configure(state="normal" if self.events else "disabled")
        if self.root.state() == "iconic":
            self.root.deiconify()
        if was_running and not completed:
            self.set_status("Playback stopped.")


def main():
    root = tk.Tk()
    app = SpeakerCueMapper(root)
    if np is None:
        app.set_status("NumPy is missing. Run setup_windows.bat to install dependencies.", "#ff7777")
    root.mainloop()


if __name__ == "__main__":
    main()
