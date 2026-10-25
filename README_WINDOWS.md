# Build A Boat Speaker Cue Mapper — Windows Edition

A Windows port of Speaker Cue Mapper for Build A Boat for Treasure. It sends ordinary Windows keyboard input to the currently focused game so the game's Music Note blocks produce the sound.

## Includes

- Choose MP3/MP4 and other FFmpeg-supported audio/video files (audio track only).
- Choose MIDI for direct note-event timing; MIDI does not go through pitch guessing.
- Automatic analysis after selecting a file.
- Notes-at-once selector from 1 to 25, plus an “All notes” option.
- Main Pitch Only and Piano Mode switches; changing settings re-analyzes the current file.
- Three-second countdown; the mapper minimizes afterward and verifies the foreground window is Roblox before sending anything. If another app is focused, playback is cancelled and no keys are sent.
- Batched chords using Windows `SendInput` with the complete INPUT union layout and keyboard scan codes. A foreground-window guard prevents accidental key presses in other apps; the mapper minimizes after countdown.
- CSV export and Stop button.

## Install

1. Install 64-bit Python 3.11 or 3.12 from https://www.python.org/downloads/windows/ . In the installer, include Tcl/Tk and (optionally) add Python to PATH. The Windows `py` launcher is used by the included scripts.
2. Extract this ZIP to a normal folder.
3. Double-click `setup_windows.bat` and wait for dependency installation to finish.
4. Double-click `run_windows.bat`.

The setup creates a local .venv environment. It explicitly checks for yt-dlp and installs it automatically if missing, then installs NumPy, Mido, and imageio-ffmpeg. The last package supplies FFmpeg for decoding audio, so you usually do not need a separate FFmpeg installation.

## Play

1. Open Roblox and build/load your 25 Music Note blocks with the existing Q–N mappings.
2. In the app, choose a MIDI file, or select an MP3/MP4 and let it be analyzed.
3. Set the note count / Main Pitch Only / Piano Mode as desired. Changes trigger a new analysis.
4. Click Play, then click the Roblox game window during the three-second countdown. Keep Roblox focused during playback.
5. The app sends key taps, and the in-game blocks should make the notes. It does not play local tones.

## Limitations

- MIDI playback uses MIDI note timing directly, but out-of-range pitches are folded by octaves into the 25 available keys.
- MP3/MP4 transcription is approximate. It can mistake harmonics for notes, especially with vocals, percussion, or dense multi-instrument recordings. Piano Mode changes the harmonic scoring method but is not a professional transcription model.
- Windows `SendInput` sends synthetic keyboard events. The app uses the full INPUT union definition required by 64-bit Windows. If input still fails, first run both the app and Roblox normally (not as Administrator), and make sure the game window is focused. Windows can block input across privilege levels; only if Roblox is explicitly elevated should you test matching privilege levels.
- The three-second timer stays inside the main app. When it ends, the mapper minimizes and checks that a Roblox window is actually foreground; if not, it cancels without sending any keys.


## Fixed input build

This build initializes the Windows `user32` API before mapping scan codes, uses the full `INPUT` union, and checks the foreground process before each chord. If Roblox is not foreground, playback stops instead of typing into another app. If Windows accepts only part of a batch, the app makes a best-effort key-up cleanup. Run the app and Roblox at the same privilege level.

Roblox disconnects are a separate network/session issue; this app cannot repair Wi-Fi or Roblox server connectivity.

## Decode media from a URL

The app has a **Sound URL** field and **Decode URL sound** button. Paste a direct `http://` or `https://` media-file URL, a YouTube video URL, or another page supported by yt-dlp and click the button. The app uses yt-dlp for webpage URLs and a direct downloader for media-file URLs, with a 100 MB limit, then uses the same FFmpeg audio decode and analysis pipeline as local MP3/MP4 files. Changing Notes at once, Main Pitch Only, or Piano Mode re-runs decoding/analysis from that cached download without downloading it again. Clicking Decode URL sound again fetches a fresh copy.

The setup script installs yt-dlp automatically into the local app environment if it is missing. Site support can change as websites change. Login-gated and DRM-protected streams are not bypassed; use only media you have permission to download. Audio and video need to be readable by the bundled FFmpeg decoder.
