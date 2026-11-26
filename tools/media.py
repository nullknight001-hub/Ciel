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
