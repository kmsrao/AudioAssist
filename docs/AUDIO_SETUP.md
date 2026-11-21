# Audio setup, channel mapping and selection

## Choosing the input

Open **Audio & Settings → Audio input**.

1. Pick a device row. Columns show host API, input channel count, probed sample rates and
   the driver's default low latency. **Test** opens a 1-second stream and reports RMS/peak
   for each channel — use it to confirm signal arrives on the expected inputs.
3. Choose **sample rate** (48 kHz recommended), **buffer (block) size** (512 default; 256 or 1024
   are fine) and the **number of input channels** to capture (up to the device maximum).
4. **Use this input & start capture.** The previous stream is closed first; diagnostics and
   recommendations from the previous device are expired.

**Rescan devices** re-initialises PortAudio to see newly connected hardware; capture must be
stopped first. Saved devices are matched by **name + host API** on start because device indices
change when hardware is replugged or the OS reorders devices.

### Which host API?

| OS | Prefer | Notes |
|---|---|---|
| Windows | ASIO (if the interface ships a driver) | Full channel count, low latency; often single-client. |
| Windows | WASAPI | Good default for class-compliant/USB devices; shared mode at the Windows mix rate (auto-converted). |
| Windows | MME / DirectSound | Compatibility only; frequently limited to 2 channels; higher latency. |
| Windows | WDM-KS | Low latency, but some drivers expose odd endpoints or refuse rates. |
| macOS | Core Audio | Only option; set the device rate in Audio MIDI Setup. |

Live Audio Assist **only reads** inputs. Typical live setups feed it from the console's USB/Dante/
MADI multitrack outputs (direct outs or post-preamp taps) into the computer.

## Channel mapping

Each channel row corresponds to one input of the capture device (`index` 0 → **CH01**). Open
**Channels** in the top bar to edit:

- **Name** (e.g. "Pastor Mic"), **Profile** (analysis context), **Group** (Vocals, Speech,
  Instruments, Drums, Other — editable list), **Enabled** (disabled inputs can't be selected).
- **Analyse** – the selection flag.

All of this is stored in SQLite and restored on the next start. If you change the channel
count, existing channel configurations are kept for the inputs that still exist.

## Selecting channels

In the channel panel:

- Tick/untick individual channels.
- **All / None**.
- Group buttons select **only** that group; **Shift-click** adds the group to the selection.
- Search by name, number or group; filter by profile.
- Click a selected channel to focus it in the analysis panel.

Only selected (and enabled) channels are analysed. Deselecting a channel immediately clears its
diagnostics and expires its recommendations. All captured channels still show live level/clip
activity so you can see signal on unselected inputs.

## Profiles

| Profile | Focus | HPF range |
|---|---|---|
| Speech | intelligibility, rumble, noise, mud, harshness, feedback | 80–120 Hz |
| Lead Vocal | tonal balance, harshness, sibilance, presence, feedback | 80–110 Hz |
| Backing Vocal | as lead, slightly more tolerant | 100–150 Hz |
| Choir | feedback, low-mid build-up, noise (high-gain distant mics) | 100–150 Hz |
| Acoustic Guitar | boom, mud, boxiness, feedback | 70–100 Hz |
| Electric Guitar | mud, boxiness, harshness, hum | 70–100 Hz |
| Bass | balance, low-mid, hum, level (no rumble/boom/feedback rules) | 25–40 Hz |
| Keyboard | broad & conservative (no narrow mud/boxy rules, no feedback) | 30–60 Hz |
| Kick | boxiness, overload (no hum: the drum's own 50–60 Hz body) | 25–40 Hz |
| Snare | boxiness, rumble, overload | 80–120 Hz |
| Cymbals | rumble/bleed, harshness | 150–300 Hz |
| General | most conservative | 40–80 Hz |

Profiles change thresholds and which rules apply; they never apply EQ. See
[DIAGNOSTIC_RULES](DIAGNOSTIC_RULES.md).

## Levels and gain staging

Meters show dBFS at the capture device. Diagnostics such as *weak signal* or *clipping* refer to
the level **arriving at the computer**: if you feed the analyser from console direct outs that
are post-fader or post-processing, level advice may not map 1:1 to the preamp. Prefer pre-fader,
pre-processing taps for meaningful gain-staging advice.
