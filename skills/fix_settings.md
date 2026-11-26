cat << 'EOF' > ~/Projects/Ciel_V3/skills/fix_settings.md
# Linux Settings & Hardware Troubleshooting

1. **Snapshot before changing anything:** Query the current setting first and print it so you can restore it if needed.
2. **Audio / Microphone (PipeWire / PulseAudio):**
   - Inspect sinks/sources: `wpctl status` (or `pactl list short sinks && pactl list short sources`).
   - Unmute & set sane volume: `wpctl set-mute @DEFAULT_AUDIO_SINK@ 0 && wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.7` (use `@DEFAULT_AUDIO_SOURCE@` for mic).
   - Switch default device: `wpctl set-default <ID>`.
3. **Wi-Fi & Network:**
   - Check radio & interfaces: `nmcli radio wifi && nmcli device status`.
   - Re-enable Wi-Fi or reconnect: `nmcli radio wifi on` or `nmcli device connect <iface>`.
   - Flush DNS cache if websites fail to resolve: `resolvectl flush-caches`.
4. **Bluetooth:**
   - Check adapter & unblock: `rfkill unblock bluetooth && bluetoothctl power on`.
5. **GUI / App-Specific Settings:**
   - If the issue is inside an open app window (browser permissions, Discord, antivirus), call `see_screen` to locate the exact setting and guide or toggle it.
EOF