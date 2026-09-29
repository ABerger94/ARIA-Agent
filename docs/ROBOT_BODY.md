# ARIA Robot Body — Build Guide

A physical desktop body for ARIA: a pan/tilt head with a webcam (eyes),
a USB mic (ears), and a USB speaker (voice), driven by an Arduino Nano.
**No soldering required** — everything plugs into headers or screw terminals.

Phase 1 (this guide): head that looks around + face tracking. ~$55 in parts.
Phase 2 (wheels): two continuous-rotation servos turn it into a desk rover. ~$15 more.

## How it works

```
 ARIA (Python, your laptop)
   │  USB serial @115200, text protocol:  P<pan>T<tilt>   /  W<l>,<r>  /  S
   ▼
 Arduino Nano + sensor shield ──► head servos (D9 pan / D10 tilt)
                                  wheel servos (D5 / D6, phase 2)
   ▲
 USB webcam ──► eyes (vision.py, ARIA_BODY_CAMERA)
 USB mic ─────► ears (Windows default recording device)
 USB speaker ─► voice (Windows default playback device)
```

The Arduino side was the one missing piece of ARIA's existing `P90T45`
protocol — it now lives in `arduino/aria_body/aria_body.ino`. ARIA already had
`move_head_servos`, webcam capture, and face tracking; this guide gives those
bits a physical body. New tools: `drive_wheels` (phase 2) and `body_stop`.

## Parts — Phase 1 (~$55)

| Part | Why | Price found |
|---|---|---|
| Arduino Nano clone (CH340, with USB cable) | The muscle — generates servo signals, talks USB serial to ARIA | ~$4–10 (Inland Nano 3.0 is $3.99 at Micro Center) |
| Nano I/O expansion / sensor shield | Servos plug straight into its 3-pin headers — this is what makes it solder-free | ~$6–7 |
| SG90 micro servos × 2 | Pan + tilt muscles | ~$4 each |
| Mini pan-tilt bracket kit | The neck — two servos + webcam bolt together with screws | ~$5 |
| 1080p USB webcam with built-in mic | Eyes + ears in one device | ~$16–20 |
| Mini USB stereo speaker | Voice | ~$12.50 |
| 4×AA battery holder with on/off switch | Servo power — **separate from USB** (servos brown-out USB power when they move) | ~$3–6 |
| 4× AA batteries | | you probably own these |

Nothing else. The "body" itself is whatever you want: a small box, a 3D print,
a dinosaur toy with the head mounted on top. ARIA doesn't care what she looks like.

## Parts — Phase 2 (~$15, optional)

| Part | Price found |
|---|---|
| FS90R continuous-rotation micro servos × 2 | ~$7.50 each |
| Small platform (acrylic/wood/cardboard) + one caster wheel | ~$0–10 DIY |

The wheel servos *are* the motors — no motor driver needed. They plug into the
same shield (D5/D6) and ARIA drives them with the `drive_wheels` tool.

## Assembly — Phase 1 (no solder)

1. **Seat the Nano** in the sensor shield (pins line up one way; don't force it).
2. **Build the pan-tilt kit** per its instructions — bottom servo = pan, top = tilt.
   Mount the webcam on the top bracket with its tripod screw or a zip tie.
3. **Plug servos into the shield**: pan servo → D9 header, tilt servo → D10
   header. Servo plugs are keyed: brown/black wire = ground (−), red = 5V (+),
   orange/yellow = signal (S). Match the shield's labels.
4. **Servo power**: connect the 4×AA holder's leads to the shield's servo power
   terminal block (screw terminals — red to V+, black to GND). Flip the switch
   OFF for now. This powers the servos; the Nano itself is powered by USB.
5. **USB**: Nano → laptop. Webcam → laptop. Speaker → laptop. Mic is in the webcam.
6. **Windows sound settings**: set the USB speaker as default *playback* and the
   webcam mic as default *recording* device. ARIA uses system defaults — no code
   changes needed.

## Firmware (one-time, ~10 min)

1. Install Arduino IDE from arduino.cc.
2. Open `arduino/aria_body/aria_body.ino`.
3. Tools → Board → **Arduino Nano**. Tools → Processor → **ATmega328P
   (Old Bootloader)** (most CH340 clones need this).
4. Tools → Port → the COM port that appears when you plug the Nano in.
5. Upload. Open Serial Monitor @115200 — you should see `aria-body ready`.
6. Type `P0T45` → head pans full left. Type `P180T45` → full right.
   Type `S` → centers. If a servo spins the wrong way on phase 2 wheels,
   swap that servo's plug to the mirrored header or flip its sign in ARIA.

## ARIA software setup

1. `pip install -r requirements.txt` (pyserial was already a dependency).
2. Flip the battery switch ON, then start ARIA. `init_hardware()` auto-detects
   the Nano (looks for CH340 / USB Serial ports) and connects at 115200.
3. If the laptop's built-in camera should stay camera 0 and the body's webcam is
   the second device, set `ARIA_BODY_CAMERA=1` before launching ARIA.
4. Tell ARIA "look left" → `move_head_servos` fires → the head physically turns.
   "Follow my face" → `face_tracking` → the head tracks you via the webcam.

## Test procedure

- [ ] Serial Monitor: `P90T45` centers the head; servos move smoothly (eased).
- [ ] ARIA: "look left" moves the physical head.
- [ ] ARIA: "follow my face" tracks your face on the body webcam.
- [ ] Phone bridge: `/face.mjpg` now shows the body's point of view.
- [ ] Phase 2: "drive forward for 2 seconds" → `drive_wheels` → auto-stops.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Servos jitter / Nano resets when servos move | Servo power problem. Confirm the 4×AA pack is ON and wired to the shield's servo power terminals, not USB. Use fresh alkalines. |
| `Hardware: Virtual Mode` in ARIA logs | Nano not detected. Check USB cable (some cables are charge-only), correct COM port, CH340 driver installed (wch.cn). |
| Upload fails in Arduino IDE | Processor must be **ATmega328P (Old Bootloader)** for most clones. |
| Head moves opposite of command | Physically flip the servo horn 180°, or swap pan/tilt plugs. |
| Webcam shows laptop cam, not body cam | Set `ARIA_BODY_CAMERA=1` (or 2). |
| Wheels (phase 2) spin but robot turns in place | One wheel is mirrored — negate that side in the drive call, or flip the servo mount. |

## Safety

- `drive_wheels` with `seconds > 0` auto-stops (max 30 s) so ARIA can't drive off
  the desk if you walk away mid-command. `body_stop` kills everything.
- Keep the battery switch OFF when you're not using the body — SG90s hold
  position and drain AAs.
- Servos are strong enough to pinch fingers at the bracket joints. Don't stick
  fingers in the neck while it's tracking.
