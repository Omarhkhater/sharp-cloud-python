# Sharp Air Control (Egypt)

Home Assistant custom integration for Sharp room air conditioners on the **Sharp Egypt** cloud used by the SHARP Air Control app (`com.elarabygroup.elshaircond`, El Araby Group).

It was checked against three units on one account:

- 2× AY-XP12YHE
- 1× AY-XP18YHE

The integration talks to `eg-hms.cloudlabs.sharp.co.jp`. It does not use a password, and it does not control the units over your LAN. Each indoor unit becomes a climate entity, plus sensors and switches.

## Install with HACS

1. In Home Assistant, open **HACS → Integrations**.
2. Open the menu (top right) and choose **Custom repositories**.
3. Add this repository:
   - Repository: `https://github.com/Omarhkhater/sharp-cloud-python`
   - Category: **Integration**
4. Click **Add**, then find **Sharp Air Control** in the HACS integration list and download it.
5. Restart Home Assistant.

Home Assistant 2025.1.0 or newer is required.

### Without HACS

Copy `custom_components/sharp_cocoro_air` into your Home Assistant `config/custom_components/` folder and restart.

## Sign in

The app signs in with Azure AD B2C in a browser. Home Assistant never sees the account password.

1. Go to **Settings → Devices & services → Add integration** and choose **Sharp Air Control**.
2. The setup form shows an authorize link. Open that link in a browser (phone or computer) and sign in with the same account you use in SHARP Air Control.
3. After a successful sign-in the browser tries to open `https://sharp-cocoroair-egypt/?code=...`. That page does not load. That is expected.
4. Copy the **entire address** from the address bar. It is fine if the browser says it cannot open the page.
5. Paste that address into the Home Assistant form. Pasting only the `code` value also works.
6. Home Assistant exchanges the code, registers a terminal, and pairs it to each air conditioner. The three units should show up under one device hub.

The code in that address works once. If setup fails after you paste it, open the authorize link again and paste the new address.

What is stored in Home Assistant:

- the terminal id
- the session cookies (`JSESSIONID` and the load-balancer cookies)

The B2C code and the one-time login nonce are not stored. No password is stored.

## What you can control

Each air conditioner:

| Entity | What it does |
|---|---|
| Climate | On/off, mode (auto, cool, heat, dry, fan), whole-degree target temperature, room temperature, humidity, fan speed |
| Outdoor temperature | EPC `BE` |
| Power | Instantaneous watts, EPC `84` |
| Energy | Cumulative kWh from EPC `85`, using the ECHONET scale of 0.001 kWh per count |
| Plasmacluster | On/off, EPC `CF` |
| Fault | On when EPC `88` reports a fault |

Only the browser sign-in, terminal pairing, and a whole-degree setpoint change were confirmed on a live unit (21 °C → 22 °C → 21 °C, EPC `B3`). Power, mode, fan, and Plasmacluster are the same kind of single-EPC write and need one supervised test each before you rely on them.

The temperature slider runs from 16 °C to 30 °C. F0 names a per-model range table, but that table was not in the capture, so the slider uses this span. The cloud rejects a value the unit cannot use.

Fan speeds are Auto, Quiet, Very weak, Weak, Strong, and Rapid.

The integration polls about every 60 seconds. After a command is accepted, the entity updates immediately instead of waiting for the next poll.

### Experimental controls

Half-degree setpoints, vertical and horizontal vanes, and the Sleep switch send the Sharp FA frame. Those writes were not confirmed live. They are off by default.

To turn them on: **Settings → Devices & services → Sharp Air Control → Configure**, then enable **Enable experimental controls**. Soft, Speed, and Cocoro Air appear only after that, and only when that unit's capability data says the feature exists. On the three units above, those bits are off, so those three switches stay hidden.

Vane positions, when enabled: vertical Auto, Up, Horizontal, Down 1, Down 2, Down, and Swing; horizontal Auto, Straight, Left, Right, Wide, Enclose, and Swing. The captured status frames put those values at FA indexes 49 and 50 (section 4.4). Section 4.2's table lists 48 and 49; the frames do not match that numbering.

## If Home Assistant asks you to sign in again

A 401 first retries login with the stored terminal id and cookies. If that fails, Home Assistant starts a re-authentication flow. Repeat the browser sign-in and paste the new address. The same terminal is kept, so the air conditioners stay paired.

## What to try first

Use one unit, then put it back. Leave experimental controls off for this pass.

1. Confirm all three units appear, with the right on/off state, mode, room temperature, and setpoint.
2. Change the setpoint by 1 °C, wait until the app shows the same number, then change it back. This path was confirmed live.
3. Change the mode, then the fan speed, then Plasmacluster. Put each one back. These still need one supervised test.
4. Turn the unit off and on, and put it back. Power on/off also needs that supervised test.
5. Compare **Power** (W) with the unit while it is cooling, and compare **Energy** (kWh) with the app's energy graph. Energy uses the standard ECHONET scaling and has not been checked against the app's graph.
6. Restart Home Assistant and confirm the units come back without another sign-in.

## Not included

These are left out because the capture does not pin them down:

- Super Jet
- Eco, filter-cleaning, and dust-filter commands
- Patrol / auto-guard
- The separate `A1` vane code (swing uses the FA vane tables)
- Special operation modes other than auto, cool, heat, dry, and fan
- Timers, the cboard API, and the daily energy-history endpoint
- LAN or SoftAP control

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Tests use the redacted `boxInfo` fixture and a local fake cloud. They do not sign in to a real account.
