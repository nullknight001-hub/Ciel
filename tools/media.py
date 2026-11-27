import os
import socket
import subprocess

MPV_SOCKET = "/tmp/ciel_mpv.sock"


def play_youtube_music(query: str = "liked") -> str:
    """Plays shuffled music in the background from your YouTube account ('liked' or a playlist URL) or a 20-track search."""
    subprocess.run(["pkill", "-f", "mpv --no-video"], stderr=subprocess.DEVNULL)
    if os.path.exists(MPV_SOCKET):
        try:
            os.remove(MPV_SOCKET)
        except OSError:
            pass

    browser = os.getenv("YT_BROWSER", "firefox").strip()
    default_playlist = os.getenv(
        "YT_DEFAULT_PLAYLIST", "https://www.youtube.com/playlist?list=LM"
    ).strip()

    cleaned = (query or "").strip()
    lowered = cleaned.lower()

    if not cleaned or lowered in ("liked", "my music", "my songs", "my playlist", "account", "favorites"):
        target = default_playlist
        label = "your YouTube Liked Music"
    elif cleaned.startswith(("http://", "https://")):
        target = cleaned
        label = "your YouTube playlist"
    else:
        target = f"ytdl://ytsearch20:{cleaned}"
        label = f"'{cleaned}' (20-track mix)"

    ytdl_opts = "yes-playlist="
    if browser:
        ytdl_opts = f"cookies-from-browser={browser},{ytdl_opts}"

    subprocess.Popen(
        [
            "mpv",
            "--no-video",
            "--shuffle",
            "--loop-playlist=inf",
            f"--input-ipc-server={MPV_SOCKET}",
            f"--ytdl-raw-options={ytdl_opts}",
            target,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return f"Playing {label} shuffled in the background."


def skip_music() -> str:
    """Skips to the next song in the background music player."""
    if not os.path.exists(MPV_SOCKET):
        return "No background music player is currently running."
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2.0)
            client.connect(MPV_SOCKET)
            client.sendall(b'{"command": ["playlist-next"]}\n')
        return "Skipped to the next track."
    except Exception as e:
        return f"Could not skip track: {e}"


def stop_music() -> str:
    """Stops any background YouTube music currently playing."""
    result = subprocess.run(["pkill", "-f", "mpv --no-video"])
    if os.path.exists(MPV_SOCKET):
        try:
            os.remove(MPV_SOCKET)
        except OSError:
            pass
    return (
        "Stopped background music."
        if result.returncode == 0
        else "No background music was playing."
    )

def open_youtube_browser(query: str = "") -> str:
    """Opens YouTube in your default signed-in browser and loads a video or playlist to start playing there."""
    import shutil
    import urllib.parse

    browser = (os.getenv("YT_BROWSER") or os.getenv("BROWSER") or "").strip()
    default_playlist = os.getenv(
        "YT_DEFAULT_PLAYLIST", "https://www.youtube.com/playlist?list=LL"
    ).strip()
    if default_playlist.endswith("/feed/liked"):
        default_playlist = "https://www.youtube.com/playlist?list=LL"

    cleaned = (query or "").strip()
    lowered = cleaned.lower()

    url = None
    label = ""

    if not cleaned or lowered in ("liked", "my music", "my songs", "my playlist", "account", "favorites", "music", "track"):
        label = "your YouTube Liked track in your browser"
        if shutil.which("yt-dlp"):
            cmd = ["yt-dlp", "--get-id", "--playlist-start", "1", "--playlist-end", "1", "--no-warnings"]
            if browser:
                cmd += ["--cookies-from-browser", browser]
            cmd.append(default_playlist)
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
                vids = [line.strip() for line in (res.stdout or "").splitlines() if line.strip()]
                if vids:
                    url = f"https://www.youtube.com/watch?v={vids[0]}&list=LL"
            except Exception:
                url = None
        if not url:
            url = default_playlist
    elif cleaned.startswith(("http://", "https://")):
        url = cleaned
        label = f"YouTube URL ({url}) in your browser"
    else:
        label = f"'{cleaned}' on YouTube in your browser"
        if shutil.which("yt-dlp"):
            try:
                res = subprocess.run(
                    ["yt-dlp", "--get-id", "--no-warnings", f"ytsearch1:{cleaned}"],
                    capture_output=True,
                    text=True,
                    timeout=12,
                )
                vids = [line.strip() for line in (res.stdout or "").splitlines() if line.strip()]
                if vids:
                    url = f"https://www.youtube.com/watch?v={vids[0]}"
            except Exception:
                url = None
        if not url:
            url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(cleaned)

    opener = shutil.which("xdg-open") or shutil.which(browser) or shutil.which("firefox") or shutil.which("chromium")
    if not opener:
        return "Error: No browser opener found on system."

    subprocess.Popen(
        [opener, url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return f"Opened {label}: {url}"

