# Sharp Air Egypt

![Sharp Air Egypt](custom_components/sharp_cocoro_air/brand/logo.png)

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
4. Click **Add**, then find **Sharp Air Egypt** in the HACS integration list and download it.
5. Restart Home Assistant.

Home Assistant 2025.1.0 or newer is required.

### Without HACS

Copy `custom_components/sharp_cocoro_air` into your Home Assistant `config/custom_components/` folder and restart.

## Sign in

The app signs in with Azure AD B2C in a browser. Home Assistant never sees the account password.

1. Go to **Settings → Devices & services → Add integration** and choose **Sharp Air Egypt**.
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
| Super Jet | Read-only, confirmed. On when FA byte 50 is `01` |

Confirmed live on the Living Room AY-XP12YHE: browser sign-in, terminal pairing, a whole-degree setpoint (21 °C → 22 °C → 21 °C, EPC `B3`), power off (`80` = `31`, `valueSingle`), power on (`80` = `30`, confirmed by a status read rather than a `controlResult`), fan Auto (`A0` = `41`, `valueSingle`), mode (`B0` = `45` fan only and `B0` = `42` cool, `valueSingle`), and Plasmacluster (`CF` = `42` off and `CF` = `41` on, `valueSingle`).

Fan Rapid (`A0` = `38`) was rejected. `controlResult` came back `unmatch`, and the unit briefly reported `37` (Strong). Rapid is hidden unless experimental controls are on, and the label says it may be unsupported. Do not expect it to stick.

Super Jet is read from FA byte 50 (`01` on, `00` off). Super Jet set from the remote is reflected in the cloud status (FA byte 50 = `01`), with some lag; the sensor updates on the next poll. Both write attempts were rejected with `unmatch`: index 51 only, and a vane write plus index 51. Super Jet is read-only and needs an IR blaster to toggle. There is a binary sensor and no switch.

The temperature slider runs from 16 °C to 30 °C. F0 names a per-model range table, but that table was not in the capture, so the slider uses this span. The cloud rejects a value the unit cannot use.

Fan speeds are Auto, Quiet, Very weak, Weak, and Strong.

The integration polls about every 60 seconds. After a command is accepted, the entity updates immediately instead of waiting for the next poll.

### Experimental controls

Half-degree setpoints, vertical and horizontal vanes, the Rapid fan speed, and the Sleep switch are off by default. Rapid was rejected on a live unit. The other writes in this group were not confirmed live.

Experimental controls can report a failure and still change the unit. On a live AY-XP12YHE, FA-frame writes came back `unmatch` (shown in Home Assistant as "The air conditioner rejected the command"), yet the vane position still changed. Rapid also came back `unmatch`, and the unit then briefly reported Strong. Home Assistant does not retry these commands. It refreshes status shortly after any write; trust what it shows, not the error. If the unit is not where you want it, set it again yourself.

To turn them on: **Settings → Devices & services → Sharp Air Egypt → Configure**, then enable **Enable experimental controls**. Soft, Speed, and Cocoro Air appear only after that, and only when that unit's capability data says the feature exists. On the three units above, those bits are off, so those three switches stay hidden.

Vane positions, when enabled: vertical Auto, Up, Horizontal, Down 1, Down 2, Down, and Swing; horizontal Auto, Straight, Left, Right, Wide, Enclose, and Swing. The captured status frames put those values at FA indexes 49 and 50 (section 4.4). Section 4.2's table lists 48 and 49; the frames do not match that numbering.

## Removing the integration

Deleting Sharp Air Egypt in Home Assistant does not delete it from the Sharp cloud. Setup registers a terminal named **Home Assistant** (`spremote_a_eg`) and pairs it to each air conditioner. That terminal stays paired after you remove the integration.

The cloud has an unpair request (`PUT setting/pairing/` with the terminal id and box id). The live capture never sent it, and the `houseFlag` value for that request is not confirmed, so this integration does not call it.

In the SHARP Air Control app, open the list of signed-in terminals or smartphones if the app shows one, and delete **Home Assistant**. The live account listed the phone as `spremote_i_eg` and this integration as `spremote_a_eg`. There is no confirmed button path beyond that list.

## If Home Assistant asks you to sign in again

A 401 first retries login with the stored terminal id and cookies. If that fails, Home Assistant starts a re-authentication flow. Repeat the browser sign-in and paste the new address. The same terminal is kept, so the air conditioners stay paired.

## What to try first

Use one unit, then put it back. Leave experimental controls off for this pass.

1. Confirm all three units appear, with the right on/off state, mode, room temperature, and setpoint. Super Jet set from the remote is reflected in the cloud status (FA byte 50 = `01`), with some lag; the sensor updates on the next poll.
2. Change the setpoint by 1 °C, wait until the app shows the same number, then change it back. This path was confirmed live.
3. Cool, Fan only, fan Auto, and Plasmacluster were confirmed live. Quiet, Very weak, Weak, and Strong have not been written yet. Leave Rapid off.
4. Power off was confirmed live (`80` = `31`). Power on (`80` = `30`) was confirmed by a status read rather than a `controlResult`. You can still turn the unit off and on once and put it back.
5. Compare **Power** (W) with the unit while it is cooling, and compare **Energy** (kWh) with the app's energy graph. Energy uses the standard ECHONET scaling and has not been checked against the app's graph.
6. Restart Home Assistant and confirm the units come back without another sign-in.

## Not included

These are left out because the capture does not pin them down:

- Super Jet writes (the on/off state is read from FA byte 50)
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
