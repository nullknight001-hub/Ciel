#!/bin/bash
# Launch Ciel Voice Chat in a terminal window (konsole or alacritty or foot)
# Let's check available terminals
if command -v konsole &> /dev/null; then
    konsole -e bash -c "/home/ilham/Projects/Ciel_V3/venv/bin/python /home/ilham/Projects/Ciel_V3/live_voice.py; exec bash"
elif command -v alacritty &> /dev/null; then
    alacritty -e bash -c "/home/ilham/Projects/Ciel_V3/venv/bin/python /home/ilham/Projects/Ciel_V3/live_voice.py; exec bash"
elif command -v kitty &> /dev/null; then
    kitty bash -c "/home/ilham/Projects/Ciel_V3/venv/bin/python /home/ilham/Projects/Ciel_V3/live_voice.py; exec bash"
else
    xterm -e bash -c "/home/ilham/Projects/Ciel_V3/venv/bin/python /home/ilham/Projects/Ciel_V3/live_voice.py; exec bash"
fi
